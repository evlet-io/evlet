"""A small durable queue for short single-node SQLite tasks, and long tasks run in committed steps."""

from dataclasses import dataclass, field, replace
import json
import hashlib
import logging
import re
import secrets
import threading
import time

from . import qb
from .adapter import Column, OperationalError, TableDefinition
from .auth import AccessError, User
from .identity import RecordId, text as text_id
from .database import NotFoundError
from .models import ValidationError
from .permissions import Policy, trusted
from .writes import WRITE_LABEL, job_writes

logger = logging.getLogger(__name__)
# Seconds a claim lasts by default, and how far each committed step of a resumable task extends it.
LEASE = 300


def timestamp(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError('Use nonnegative integer UTC seconds')
    return value


def name(value):
    if not isinstance(value, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', value):
        raise ValueError('Use a lowercase task or schedule name')
    return value


@dataclass(frozen=True)
class Task:
    name: str
    handler: object
    max_attempts: int = 3
    retry_delay: int = 30
    external: bool = False
    # Called once per step, each in its own transaction, until it returns None (see docs/jobs-realtime.md).
    resumable: bool = False


@dataclass(frozen=True)
class JobReceipt:
    id: int
    state: str

    def public(self):
        return {'id': self.id, 'state': self.state}


@dataclass(frozen=True)
class JobContext:
    id: int
    tenant: str
    attempt: int
    user: User | None
    idempotency_key: str
    # The value the last committed step of a resumable task returned; None before the first step.
    checkpoint: object = None
    _report: object = field(default=None, init=False, repr=False, compare=False)
    _business: object = field(default=None, init=False, repr=False, compare=False)
    _queue: object = field(default=None, init=False, repr=False, compare=False)

    def follow_up(self, tx, payload=None, *, run_at, key=None):
        """Queue this job's task again to run at run_at (UTC seconds), in the job's own transaction: it is queued only
        if this run (or this step of a resumable task) commits. payload defaults to this job's payload.

        The next run has this job's identity: the same account, API key or AI assistant, or the same system role.
        It never runs as anyone else, such as the Administrator. key defaults to one per job and run_at, so a retried
        run does not queue the same follow-up twice. Returns its JobReceipt."""
        return self._queue._follow_up(tx, self, payload, run_at, key)

    def business(self, tx, *, user=None, system=None, role=None):
        """A document context (evlet.documents.Context) in this job's transaction, for moves, actions and
        document writes with every rule, permission check and Journal entry they have over HTTP.

        * `job.business(tx)` acts as the job's own user: the account that queued it, read again when the
          job started (or the role a system job was queued with).
        * `job.business(tx, user=account_id)` acts as that account, read again now: it must be active,
          and its current role, row scopes and field permissions apply.
        * `job.business(tx, system="Nightly import", role="admin")` acts as the system under that name.
          The role's permissions apply (a row scope that names the account matches nothing), writes are
          recorded under no account, and the Journal names the system as `via`.

        Job handlers are trusted code, so the context may also make system moves (Move(..., system=True)).
        Writes commit or roll back with the job's own transaction (or the step of a resumable task)."""
        from .documents import Context
        business = self._business
        if business is None:
            raise ValueError('This job runs without an application, so it has no business context')
        if not business.database.owns(tx):
            raise ValueError("Pass the job's own transaction")
        if sum(value is not None for value in (user, system)) > 1:
            raise ValueError('Act as a user or as the system, not both')
        if role is not None and system is None:
            raise ValueError('role= goes with system=; a user acts with its own role')
        source, correlation = 'Automation', f'job:{self.id}'
        if system is not None:
            if not isinstance(system, str) or not system.strip() or len(system) > 60 or '\x00' in system:
                raise ValueError('Name the system in 1 to 60 characters, such as system="Nightly import"')
            if role not in business.policy.roles:
                raise ValueError('A system context needs role=, one of: ' + ', '.join(business.policy.roles))
            acting, source = User(0, system.strip(), role, system=system.strip()), 'System'
        elif user is not None:
            account_id = user.id if isinstance(user, User) else user
            if type(account_id) is not int or account_id <= 0:
                raise ValueError('Pass user= an account id or a User with one')
            row = tx.connection.run(qb.select('evlet_users', 'id', 'username', 'role').where(id=account_id, active=1)).one()
            if row is None:
                raise AccessError(403, 'The account this job acts as is not active')
            acting = User(row['id'], row['username'], row['role'], user_links(tx, account_id))
        elif self.user is not None:
            # The job's user keeps any API key or assistant narrowing; its record links are read now for row scopes.
            acting = replace(self.user, links=user_links(tx, self.user.id)) if self.user.id else self.user
        else:
            raise ValueError('This job was queued without a user. Pass user=account_id, or system="Name" with role=')
        return Context(business, tx, acting, source=source, correlation_id=correlation)

    def progress(self, done, total=None, message=''):
        """Record how far the job is. It is saved with the handler's writes, so a rolled-back step leaves the last
        committed progress; an external task's progress is saved at once."""
        if type(done) is not int or not 0 <= done <= 2**63 - 1:
            raise ValueError('Use a nonnegative integer for done')
        if total is not None and (type(total) is not int or not done <= total <= 2**63 - 1):
            raise ValueError('Use an integer total no smaller than done, or None')
        if not isinstance(message, str) or len(message) > 200 or '\x00' in message:
            raise ValueError('Use a progress message of at most 200 characters')
        self._report(done, total, message)


def user_links(tx, user_id):
    """The (model, record id) pairs row scopes such as own_customer read for an account."""
    return tuple((row['model'], RecordId(row['record_id'])) for row in tx.connection.run(
        qb.select('evlet_user_links', 'model', 'record_id').where(user_id=user_id).order_by('model', 'record_id')))


def progress_text(done, total, message=''):
    """How the CLI shows a job's progress: "40 of 100 (40%) Customers 1 to 40"."""
    if done is None:
        return ''
    text = f'{done} of {total} ({100 * done // total if total else 100}%)' if total is not None else str(done)
    return f'{text} {message}' if message else text


def table(rows):
    """The text `evlet jobs list` prints for JobQueue.recent() rows."""
    if not rows:
        return 'No jobs'
    header = ('Job', 'Task', 'State', 'Attempts', 'Progress')
    lines = [header] + [(f"#{row['id']}", row['task'], row['state'] + (f" ({row['error']})" if row['error'] else ''), str(row['attempts']),
                         progress_text(row['done'], row['total'], row['message'] or '')) for row in rows]
    widths = [max(len(line[index]) for line in lines) for index in range(len(header))]
    return '\n'.join('  '.join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip() for line in lines)


def encode_checkpoint(value):
    encoded = json.dumps(value, allow_nan=False, separators=(',', ':'))
    if len(encoded.encode()) > 16384:
        raise ValueError('A job checkpoint must be JSON of at most 16 KiB')
    return encoded


NAMESPACE = TableDefinition('evlet_job_namespace', (
    Column('id', 'INTEGER', primary=True), Column('tenant', 'TEXT', required=True), Column('namespace', 'TEXT', required=True),
), checks=(qb.eq('id', 1),))
JOBS = TableDefinition('evlet_jobs', (
    Column('task', 'TEXT', required=True), Column('payload', 'TEXT', required=True),
    Column('actor', 'INTEGER'), Column('model', 'TEXT'), Column('record_id', 'INTEGER'), Column('state', 'TEXT', required=True),
    Column('attempts', 'INTEGER', required=True, default=0), Column('max_attempts', 'INTEGER', required=True),
    Column('run_at', 'INTEGER', required=True), Column('created_at', 'INTEGER', required=True), Column('completed_at', 'INTEGER'),
    Column('lease', 'TEXT'), Column('lease_until', 'INTEGER'), Column('dedupe', 'TEXT'), Column('error', 'TEXT'),
), numbered=True, unique=(('dedupe',),))
# The last committed progress and, for resumable tasks, the checkpoint the next step starts from.
PROGRESS = TableDefinition('evlet_job_progress', (
    Column('job_id', 'INTEGER', primary=True), Column('done', 'INTEGER', required=True), Column('total', 'INTEGER'),
    Column('message', 'TEXT', required=True, default=''), Column('checkpoint', 'TEXT'), Column('updated_at', 'INTEGER', required=True),
))
IDENTITIES = TableDefinition('evlet_job_identities', (Column('job_id', 'INTEGER', primary=True), Column('role', 'TEXT', required=True)))
# Jobs queued with an API key run with that key's scope, and not at all once it is revoked.
KEYS = TableDefinition('evlet_job_keys', (Column('job_id', 'INTEGER', primary=True), Column('key_id', 'INTEGER', required=True)))
# Likewise for AI assistants: the connection (0 for the local connector), narrowed again when the job runs.
ASSISTANTS = TableDefinition('evlet_job_assistants', (Column('job_id', 'INTEGER', primary=True), Column('client_id', 'INTEGER', required=True)))
SCHEDULES = TableDefinition('evlet_schedules', (
    Column('name', 'TEXT', primary=True), Column('task', 'TEXT', required=True), Column('payload', 'TEXT', required=True),
    Column('actor', 'INTEGER'), Column('model', 'TEXT'), Column('record_id', 'INTEGER'),
    Column('interval', 'INTEGER', required=True), Column('next_run', 'INTEGER', required=True),
    Column('enabled', 'INTEGER', required=True, default=1),
))
CALENDAR_SCHEDULES = TableDefinition('evlet_calendar_schedules', (
    Column('name', 'TEXT', primary=True), Column('expression', 'TEXT', required=True), Column('timezone', 'TEXT', required=True),
))
# The tables holding who a job runs as, and the column each keeps.
JOB_IDENTITY = (('evlet_job_identities', 'role'), ('evlet_job_keys', 'key_id'), ('evlet_job_assistants', 'client_id'))


class _Validated(Exception):
    """Raised inside a savepoint to undo a validation-only enqueue, carrying the job row it wrote."""
    def __init__(self, row):
        self.row = row


class JobQueue:
    def __init__(self, database, tasks, *, tenant='local', events=None, policy=None):
        if not isinstance(tenant, str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}', tenant):
            raise ValueError('Use an explicit tenant slug')
        self.database, self.tenant, self.events = database, tenant, events
        self.policy = policy or Policy(database.registry)
        # Set when a transaction that queued a job commits (Context.enqueue), so a worker in this process runs it now.
        self._woken = threading.Event()
        if self.policy.registry is not database.registry or (events and events.database is not database):
            raise ValueError('Jobs must use the same database, event store, and policy')
        self.tasks = {}
        for task in tasks:
            name(task.name)
            if type(task.external) is not bool: raise ValueError('Task external mode must be a boolean')
            if type(task.resumable) is not bool or task.resumable and task.external:
                raise ValueError('A resumable task is a boolean flag and runs in transactions, so it cannot be external')
            if task.name in self.tasks or not callable(task.handler) or type(task.max_attempts) is not int or not 1 <= task.max_attempts <= 10 or type(task.retry_delay) is not int or not 1 <= task.retry_delay <= 86400:
                raise ValueError('Invalid or duplicate task')
            self.tasks[task.name] = task

    def initialize(self):
        with self.database.transaction() as tx:
            tx.connection.ensure_table(NAMESPACE)
            tx.connection.run(qb.insert('evlet_job_namespace', id=1, tenant=self.tenant, namespace=secrets.token_hex(32)).on_conflict().do_nothing())
            self._bound(tx)
            for definition in (JOBS, PROGRESS, IDENTITIES, KEYS, ASSISTANTS, SCHEDULES, CALENDAR_SCHEDULES):
                tx.connection.ensure_table(definition)
            tx.connection.ensure_index('evlet_jobs_due', 'evlet_jobs', ('state', 'run_at'))
            # retired=1 marks a schedule disabled because its declaration was removed, not by an operator.
            if 'retired' not in tx.connection.columns('evlet_calendar_schedules'):
                tx.connection.add_column('evlet_calendar_schedules', Column('retired', 'INTEGER', required=True), default=0)

    def _bound(self, tx):
        row = tx.connection.run(qb.select('evlet_job_namespace', 'tenant').where(id=1)).one()
        if not self.database.owns(tx) or not row or row[0] != self.tenant:
            raise ValueError('Job transaction belongs to another tenant database')

    def enqueue(self, tx, task, payload, *, user=None, target=None, run_at=None, key=None, system_role=None):
        self._bound(tx)
        definition = self.tasks.get(task)
        if definition is None:
            raise ValueError('Register the task before enqueueing it')
        if not isinstance(payload, dict):
            raise ValueError('Use a JSON object task payload')
        encoded = json.dumps(payload, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode()) > 16384:
            raise ValueError('Task payload exceeds 16 KiB')
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 200):
            raise ValueError('Use an idempotency key of 1 to 200 characters')
        model, record_id = (None, None) if target is None else (self.database.registry.name_for(target[0]), target[1])
        if target is not None:
            tx.get(target[0], record_id)
        now = int(time.time())
        due = now if run_at is None else timestamp(run_at)
        actor = None if user is None else user.id
        access = getattr(user, 'access', None)
        key_id = None if access is None else access.key_id
        assistant = None if access is None else access.assistant
        if system_role is not None and (user is not None or system_role not in ('admin', 'editor', 'viewer')):
            raise ValueError('System jobs require an explicit supported role and no browser actor')
        if key is not None:
            old = tx.connection.run(qb.select('evlet_jobs').where(dedupe=key)).one()
            if old:
                identity = tx.connection.run(qb.select('evlet_job_identities', 'role').where(job_id=old['id'])).value()
                if identity != system_role: raise ValueError('Idempotency key was already used for another execution identity')
                queued_by = tx.connection.run(qb.select('evlet_job_keys', 'key_id').where(job_id=old['id'])).value()
                if queued_by != key_id: raise ValueError('Idempotency key was already used for another execution identity')
                queued_by = tx.connection.run(qb.select('evlet_job_assistants', 'client_id').where(job_id=old['id'])).value()
                if queued_by != assistant: raise ValueError('Idempotency key was already used for another execution identity')
                if (old['task'], old['payload'], old['actor'], old['model'], old['record_id']) != (task, encoded, actor, model, record_id):
                    raise ValueError('Idempotency key was already used for another job')
                return JobReceipt(old['id'], old['state'])
        job_id = tx.connection.run(qb.insert('evlet_jobs', task=task, payload=encoded, actor=actor, model=model, record_id=record_id, state='queued',
                                             max_attempts=definition.max_attempts, run_at=due, created_at=now, dedupe=key).returning_id()).id
        if system_role is not None: tx.connection.run(qb.insert('evlet_job_identities', job_id=job_id, role=system_role))
        if key_id is not None: tx.connection.run(qb.insert('evlet_job_keys', job_id=job_id, key_id=key_id))
        if assistant is not None: tx.connection.run(qb.insert('evlet_job_assistants', job_id=job_id, client_id=assistant))
        return JobReceipt(job_id, 'queued')

    def _follow_up(self, tx, context, payload, run_at, key):
        self._bound(tx)
        row = tx.connection.run(qb.select('evlet_jobs').where(id=context.id)).one()
        if row is None:
            raise ValueError('This job is no longer queued')
        due = timestamp(run_at)
        encoded = row['payload'] if payload is None else None
        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError('Use a JSON object task payload')
            encoded = json.dumps(payload, allow_nan=False, separators=(',', ':'))
            if len(encoded.encode()) > 16384:
                raise ValueError('Task payload exceeds 16 KiB')
        key = f'follow:{context.id}:{due}' if key is None else key
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise ValueError('Use an idempotency key of 1 to 200 characters')
        identity = {(table, column): tx.connection.run(qb.select(table, column).where(job_id=context.id)).value()
                    for table, column in JOB_IDENTITY}
        old = tx.connection.run(qb.select('evlet_jobs').where(dedupe=key)).one()
        if old:
            if (old['task'], old['payload'], old['actor'], old['model'], old['record_id']) != (row['task'], encoded, row['actor'], row['model'], row['record_id']):
                raise ValueError('Idempotency key was already used for another job')
            return JobReceipt(old['id'], old['state'])
        job_id = tx.connection.run(qb.insert('evlet_jobs', task=row['task'], payload=encoded, actor=row['actor'], model=row['model'], record_id=row['record_id'],
                                             state='queued', max_attempts=row['max_attempts'], run_at=due, created_at=int(time.time()), dedupe=key).returning_id()).id
        for (table, column), value in identity.items():
            if value is not None:
                tx.connection.run(qb.insert(table, job_id=job_id, **{column: value}))
        if self.wake not in tx._after_commit:
            tx._after_commit.append(self.wake)
        return JobReceipt(job_id, 'queued')

    def wake(self):
        """Tell a worker in this process that a job is due now. Context.enqueue calls it once its transaction commits."""
        self._woken.set()

    def wait(self, timeout):
        """Wait up to `timeout` seconds for wake(); True when woken. Workers wait with it between passes."""
        woken = self._woken.wait(timeout)
        self._woken.clear()
        return woken

    def schedule(self, schedule, task, payload, *, every, start_at=None, user=None, target=None, calendar=None):
        name(schedule)
        if type(every) is not int or not 60 <= every <= 31536000:
            raise ValueError('Use an interval between 60 seconds and one year')
        with self.database.transaction() as tx:
            # Validate using the same enqueue contract, without publishing a job.
            try:
                with tx.connection.savepoint('validate_schedule'):
                    receipt = self.enqueue(tx, task, payload, user=user, target=target)
                    raise _Validated(tx.connection.run(qb.select('evlet_jobs').where(id=receipt.id)).one())
            except _Validated as validated:
                row = validated.row
            due = int(time.time()) + every if start_at is None else timestamp(start_at)
            tx.connection.run(qb.insert('evlet_schedules', name=schedule, task=task, payload=row['payload'], actor=row['actor'], model=row['model'],
                                        record_id=row['record_id'], interval=every, next_run=due))
            if calendar is not None:
                # Same transaction, so a crash cannot leave a calendar schedule running as an interval.
                tx.connection.run(qb.delete('evlet_calendar_schedules').where(name=schedule))
                tx.connection.run(qb.insert('evlet_calendar_schedules', name=schedule, expression=calendar[0], timezone=calendar[1]))

    def set_schedule_active(self, schedule, active):
        if type(active) is not bool:
            raise ValueError('Use a boolean active flag')
        with self.database.transaction() as tx:
            self._bound(tx)
            if not tx.connection.run(qb.update('evlet_schedules', enabled=active).where(name=name(schedule))).rowcount:
                raise ValueError('Unknown schedule')

    def tick(self, *, now=None):
        now = int(time.time()) if now is None else timestamp(now)
        with self.database.transaction() as tx:
            self._bound(tx)
            rows = tx.connection.run(qb.select('evlet_schedules').where(qb.eq('enabled', 1), qb.le('next_run', now))
                                     .order_by('next_run', 'name').limit(100)).all()
            for row in rows:
                # One occurrence after downtime; missed intervals are coalesced.
                definition = self.tasks.get(row['task'])
                if definition is None:
                    tx.connection.run(qb.update('evlet_schedules', enabled=0).where(name=row['name']))
                    logger.error('Disabled schedule %s: its task is not registered', row['name'])
                    continue
                key = f"schedule:{row['name']}:{row['next_run']}"
                tx.connection.run(qb.insert('evlet_jobs', task=row['task'], payload=row['payload'], actor=row['actor'], model=row['model'],
                                            record_id=row['record_id'], state='queued', max_attempts=definition.max_attempts, run_at=now,
                                            created_at=now, dedupe=key).on_conflict().do_nothing())
                calendar = tx.connection.run(qb.select('evlet_calendar_schedules', 'expression', 'timezone').where(name=row['name'])).one()
                if calendar:
                    from .services import Calendar
                    next_run = Calendar(calendar['expression'], calendar['timezone']).next(now)
                else:
                    next_run = row['next_run'] + ((now - row['next_run']) // row['interval'] + 1) * row['interval']
                tx.connection.run(qb.update('evlet_schedules', next_run=next_run).where(name=row['name']))
            return len(rows)

    def claim(self, *, now=None, lease_seconds=300):
        now = int(time.time()) if now is None else timestamp(now)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError('Use a lease of 1 to 3600 seconds')
        with self.database.transaction() as tx:
            self._bound(tx)
            tx.connection.run(qb.update('evlet_jobs', state='failed', error='Worker attempts exhausted', lease=None, lease_until=None, completed_at=now)
                              .where(qb.eq('state', 'running'), qb.le('lease_until', now), qb.ge('attempts', qb.col('max_attempts'))))
            row = tx.connection.run(qb.select('evlet_jobs').where(qb.or_(
                qb.and_(qb.eq('state', 'queued'), qb.le('run_at', now)),
                qb.and_(qb.eq('state', 'running'), qb.le('lease_until', now), qb.lt('attempts', qb.col('max_attempts'))),
            )).order_by('run_at', 'id').limit(1)).one()
            if row is None:
                return None
            token = secrets.token_hex(24)
            tx.connection.run(qb.update('evlet_jobs', state='running', attempts=qb.col('attempts') + 1, lease=token, lease_until=now + lease_seconds, error=None)
                              .where(id=row['id']))
            return row['id'], token

    def execute(self, claim, *, active=lambda: True, now=None):
        """Run a claimed job. A job passes the business's gate like a request, so adding or removing an
        instance field waits for it."""
        from contextlib import nullcontext
        from .database import using
        business = getattr(self, 'business', None)
        # A job's writes queue behind requests for each file, and never wait long (evlet/writes.py).
        with business.gate.shared() if business is not None else nullcontext(), using(self.database), job_writes(f'job #{claim[0]}'):
            return self._execute(claim, active=active, now=now)

    def _execute(self, claim, *, active=lambda: True, now=None):
        job_id, token = claim
        now = int(time.time()) if now is None else timestamp(now)
        began = time.monotonic()
        task = None
        steps = 0
        try:
            while True:
                running = False
                # A resumable task runs one step per transaction; every step reloads the job, the lease and the account.
                clock = now + int(time.monotonic() - began)
                with self.database.transaction() as tx:
                    self._bound(tx)
                    row = tx.connection.run(qb.select('evlet_jobs').where(qb.eq('id', job_id), qb.eq('state', 'running'), qb.eq('lease', token), qb.gt('lease_until', clock))).one()
                    if row is None:
                        return steps > 0
                    if not active():
                        # Stopped between steps: the job goes back to the queue and resumes from its checkpoint.
                        self._release_claim(tx, job_id, token, clock)
                        return steps > 0
                    task, context, payload = self._prepare(tx, row, job_id, token)
                    WRITE_LABEL.set(f'job #{job_id} {task.name}')
                    running = True
                    tx._business = getattr(self, 'business', None)
                    if task.external:
                        break
                    # Job handlers are trusted app code, even when a request runs the job at once.
                    with trusted():
                        result = task.handler(tx, context, payload)
                    if task.resumable and result is not None:
                        checkpoint = encode_checkpoint(result)
                        tx.connection.run(qb.insert('evlet_job_progress', job_id=job_id, done=0, checkpoint=checkpoint, updated_at=int(time.time()))
                                          .on_conflict('job_id').do_update(checkpoint=qb.col('excluded.checkpoint'), updated_at=qb.col('excluded.updated_at')))
                        # Each committed step renews the lease, so a long job is not reclaimed while it makes progress.
                        tx.connection.run(qb.update('evlet_jobs', lease_until=qb.when((qb.lt('lease_until', clock + LEASE), clock + LEASE), otherwise=qb.col('lease_until')))
                                          .where(id=job_id, lease=token))
                        steps += 1
                        continue
                    if task.resumable:
                        tx.connection.run(qb.update('evlet_job_progress', checkpoint=None).where(job_id=job_id))
                    tx.connection.run(qb.update('evlet_jobs', state='succeeded', completed_at=int(time.time()), lease=None, lease_until=None).where(id=job_id, lease=token))
                    if self.events and row['model']:
                        self.events.publish(tx, self.database.registry.models[row['model']], row['record_id'])
                    return True
            # No open database transaction or supplied Session during external I/O.
            with trusted():
                task.handler(context, payload)
            with self.database.transaction() as tx:
                self._bound(tx)
                completed = tx.connection.run(qb.update('evlet_jobs', state='succeeded', completed_at=int(time.time()), lease=None, lease_until=None)
                                              .where(qb.eq('id', job_id), qb.eq('state', 'running'), qb.eq('lease', token), qb.gt('lease_until', int(time.time())))).rowcount
                if not completed: return False
                if self.events and row['model']:
                    self.events.publish(tx, self.database.registry.models[row['model']], row['record_id'])
            return True
        except Exception as exc:
            logger.exception('Background task %s failed', job_id)
            with self.database.transaction() as tx:
                row = tx.connection.run(qb.select('evlet_jobs').where(id=job_id, state='running', lease=token)).one()
                if row is None:
                    return False
                if not running and isinstance(exc, OperationalError):
                    self._release_claim(tx, job_id, token, now + 1)
                    return False
                retry = task is not None and row['attempts'] < row['max_attempts'] and not isinstance(exc, (AccessError, NotFoundError, ValidationError))
                due = now + min(86400, task.retry_delay * 2 ** (row['attempts'] - 1)) if retry else now
                # A resumable task keeps its checkpoint, so the retry resumes after the last committed step.
                tx.connection.run(qb.update('evlet_jobs', state='queued' if retry else 'failed', run_at=due, completed_at=None if retry else now, lease=None, lease_until=None,
                                            error=exc.message if isinstance(exc, AccessError) else type(exc).__name__).where(id=job_id, lease=token))
            return True

    def _prepare(self, tx, row, job_id, token):
        """The task, a fresh JobContext and the payload for one claimed job, after the account and target checks."""
        task = self.tasks.get(row['task'])
        if task is None:
            raise ValidationError({'task': 'Task is no longer registered'})
        user = None
        if row['actor'] is not None:
            account = tx.connection.run(qb.select('evlet_users', 'id', 'username', 'role').where(id=row['actor'], active=1)).one()
            if account is None:
                raise AccessError(403, 'The requesting account is inactive')
            user = User(*account)
            queued_by = tx.connection.run(qb.select(qb.table('evlet_job_keys', 'j'), 'k.id', 'k.scope', 'k.revoked_at')
                                          .join(qb.table('evlet_api_keys', 'k'), qb.eq('k.id', qb.col('j.key_id')), left=True).where(qb.eq('j.job_id', job_id))).one()
            if queued_by is not None:
                if queued_by['id'] is None or queued_by['revoked_at'] is not None:
                    raise AccessError(403, 'The API key that queued this job was revoked.')
                user = User(*account, access=self.business.auth.key_access(queued_by['id'], queued_by['scope']))
            queued_by = tx.connection.run(qb.select('evlet_job_assistants', 'client_id').where(job_id=job_id)).one()
            if queued_by is not None:
                connection = None
                if queued_by[0]:
                    connection = tx.connection.run(qb.select('evlet_mcp_clients').where(id=queued_by[0], user_id=account['id'], revoked_at=None)).one()
                    if connection is None:
                        raise AccessError(403, 'The AI assistant that queued this job was disconnected.')
                user = self.business.assistants.narrow(user, connection)
        else:
            identity = tx.connection.run(qb.select('evlet_job_identities', 'role').where(job_id=job_id)).one()
            if identity: user = User(0, 'schedule', identity[0])
        if user is not None and row['model']:
            model = self.database.registry.models.get(row['model'])
            if model is None: raise NotFoundError()
            self.policy.record(tx, user, model, row['record_id'], 'update')
        namespace = tx.connection.run(qb.select('evlet_job_namespace', 'namespace').where(id=1)).one()[0]
        stable_key = hashlib.sha256((namespace + ':' + (row['dedupe'] or f'job:{job_id}')).encode()).hexdigest()
        saved = tx.connection.run(qb.select('evlet_job_progress', 'checkpoint').where(job_id=job_id)).one()
        checkpoint = json.loads(saved[0]) if task.resumable and saved and saved[0] is not None else None
        def saved_progress(done, total, message):
            return (qb.insert('evlet_job_progress', job_id=job_id, done=done, total=total, message=message, updated_at=int(time.time()))
                    .on_conflict('job_id').do_update(done=qb.col('excluded.done'), total=qb.col('excluded.total'), message=qb.col('excluded.message'),
                                                     updated_at=qb.col('excluded.updated_at')))
        if task.external:
            def report(done, total, message):
                with self.database.transaction() as own:
                    if own.connection.run(qb.select('evlet_jobs', qb.value(1)).where(id=job_id, state='running', lease=token)).one():
                        own.connection.run(saved_progress(done, total, message))
        else:
            def report(done, total, message):
                tx.connection.run(saved_progress(done, total, message))
        context = JobContext(job_id, self.tenant, row['attempts'], user, stable_key, checkpoint)
        object.__setattr__(context, '_report', report)
        object.__setattr__(context, '_queue', self)
        object.__setattr__(context, '_business', getattr(self, 'business', None))
        return task, context, json.loads(row['payload'])

    @staticmethod
    def _release_claim(tx, job_id, token, due):
        tx.connection.run(qb.update('evlet_jobs', state='queued', attempts=qb.col('attempts') - 1, run_at=due, lease=None, lease_until=None, error=None)
                          .where(id=job_id, state='running', lease=token))

    def run_one(self, *, active=lambda: True, now=None):
        if not active():
            return False
        if getattr(self, 'business', None) is not None:
            self.business.controls.refresh()
        with job_writes('job queue'):
            self.tick(now=now)
            claim = self.claim(now=now)
        return False if claim is None else self.execute(claim, active=active, now=now)

    def status(self, job_id, user):
        with self.database.transaction() as tx:
            self._bound(tx)
            row = tx.connection.run(qb.select('evlet_jobs').where(id=job_id)).one()
            if row is None or (row['actor'] != user.id and user.role != 'admin'):
                raise AccessError(404, 'Job not found')
            if row['model']:
                self.policy.record(tx, user, self.database.registry.models[row['model']], row['record_id'])
            result = {key: row[key] for key in ('id', 'state', 'attempts', 'run_at', 'created_at', 'completed_at', 'error')}
            # Progress the handler reported; the checkpoint and payload stay private.
            progress = tx.connection.run(qb.select('evlet_job_progress', 'done', 'total', 'message').where(job_id=job_id)).one()
            result['progress'] = None if progress is None else {'done': progress['done'], 'total': progress['total'], 'message': progress['message']}
            return result

    def recent(self):
        """Trusted operator summary; never includes payloads or exception messages."""
        with self.database._connection() as db:
            return [{**dict(row), 'record_id': text_id(row['record_id'])}
                    for row in db.run(qb.select(qb.table('evlet_jobs', 'j'), 'j.id', 'j.task', 'j.actor', 'j.model', 'j.record_id', 'j.state', 'j.attempts',
                                                'j.run_at', 'j.error', 'p.done', 'p.total', 'p.message')
                                      .join(qb.table('evlet_job_progress', 'p'), qb.eq('p.job_id', qb.col('j.id')), left=True)
                                      .order_by('-j.id').limit(100))]

    def retry(self, job_id):
        with self.database.transaction() as tx:
            self._bound(tx)
            if not tx.connection.run(qb.update('evlet_jobs', state='queued', attempts=0, run_at=int(time.time()), error=None, completed_at=None)
                                     .where(id=job_id, state='failed')).rowcount:
                raise ValueError('Only failed jobs can be retried')
