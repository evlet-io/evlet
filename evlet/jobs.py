"""A small durable queue for short single-node SQLite tasks, and long tasks run in committed steps."""

from dataclasses import dataclass, field, replace
import json
import hashlib
import logging
import re
import secrets
import sqlite3
import threading
import time

from .auth import AccessError, User
from .identity import RecordId, text as text_id
from .database import NotFoundError
from .models import ValidationError
from .permissions import Policy, trusted

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
            row = tx.connection.execute('SELECT id,username,role FROM evlet_users WHERE id=? AND active=1', (account_id,)).fetchone()
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
    return tuple((row['model'], RecordId(row['record_id'])) for row in tx.connection.execute(
        'SELECT model, record_id FROM evlet_user_links WHERE user_id=? ORDER BY model, record_id', (user_id,)))


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
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_job_namespace (id INTEGER PRIMARY KEY CHECK(id=1), tenant TEXT NOT NULL, namespace TEXT NOT NULL)')
            tx.connection.execute('INSERT OR IGNORE INTO evlet_job_namespace VALUES (1,?,?)', (self.tenant, secrets.token_hex(32)))
            self._bound(tx)
            tx.connection.execute('''CREATE TABLE IF NOT EXISTS evlet_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, payload TEXT NOT NULL,
                actor INTEGER, model TEXT, record_id INTEGER, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                run_at INTEGER NOT NULL, created_at INTEGER NOT NULL, completed_at INTEGER,
                lease TEXT, lease_until INTEGER, dedupe TEXT UNIQUE, error TEXT)''')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_jobs_due ON evlet_jobs(state,run_at)')
            # The last committed progress and, for resumable tasks, the checkpoint the next step starts from.
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_job_progress (job_id INTEGER PRIMARY KEY, done INTEGER NOT NULL, total INTEGER, '
                                  "message TEXT NOT NULL DEFAULT '', checkpoint TEXT, updated_at INTEGER NOT NULL)")
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_job_identities (job_id INTEGER PRIMARY KEY, role TEXT NOT NULL)')
            # Jobs queued with an API key run with that key's scope, and not at all once it is revoked.
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_job_keys (job_id INTEGER PRIMARY KEY, key_id INTEGER NOT NULL)')
            # Likewise for AI assistants: the connection (0 for the local connector), narrowed again when the job runs.
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_job_assistants (job_id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL)')
            tx.connection.execute('''CREATE TABLE IF NOT EXISTS evlet_schedules (
                name TEXT PRIMARY KEY, task TEXT NOT NULL, payload TEXT NOT NULL, actor INTEGER,
                model TEXT, record_id INTEGER, interval INTEGER NOT NULL, next_run INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1)''')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_calendar_schedules (name TEXT PRIMARY KEY, expression TEXT NOT NULL, timezone TEXT NOT NULL)')
            # retired=1 marks a schedule disabled because its declaration was removed, not by an operator.
            if 'retired' not in self.database.columns(tx.connection, 'evlet_calendar_schedules'):
                tx.connection.execute('ALTER TABLE evlet_calendar_schedules ADD COLUMN retired INTEGER NOT NULL DEFAULT 0')

    def _bound(self, tx):
        row = tx.connection.execute('SELECT tenant FROM evlet_job_namespace WHERE id=1').fetchone()
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
            old = tx.connection.execute('SELECT * FROM evlet_jobs WHERE dedupe=?', (key,)).fetchone()
            if old:
                identity = tx.connection.execute('SELECT role FROM evlet_job_identities WHERE job_id=?', (old['id'],)).fetchone()
                if (identity[0] if identity else None) != system_role: raise ValueError('Idempotency key was already used for another execution identity')
                queued_by = tx.connection.execute('SELECT key_id FROM evlet_job_keys WHERE job_id=?', (old['id'],)).fetchone()
                if (queued_by[0] if queued_by else None) != key_id: raise ValueError('Idempotency key was already used for another execution identity')
                queued_by = tx.connection.execute('SELECT client_id FROM evlet_job_assistants WHERE job_id=?', (old['id'],)).fetchone()
                if (queued_by[0] if queued_by else None) != assistant: raise ValueError('Idempotency key was already used for another execution identity')
                if (old['task'], old['payload'], old['actor'], old['model'], old['record_id']) != (task, encoded, actor, model, record_id):
                    raise ValueError('Idempotency key was already used for another job')
                return JobReceipt(old['id'], old['state'])
        job_id = tx.connection.execute('INSERT INTO evlet_jobs(task,payload,actor,model,record_id,state,max_attempts,run_at,created_at,dedupe) VALUES (?,?,?,?,?,?,?,?,?,?)', (task, encoded, actor, model, record_id, 'queued', definition.max_attempts, due, now, key)).lastrowid
        if system_role is not None: tx.connection.execute('INSERT INTO evlet_job_identities(job_id,role) VALUES (?,?)', (job_id, system_role))
        if key_id is not None: tx.connection.execute('INSERT INTO evlet_job_keys(job_id,key_id) VALUES (?,?)', (job_id, key_id))
        if assistant is not None: tx.connection.execute('INSERT INTO evlet_job_assistants(job_id,client_id) VALUES (?,?)', (job_id, assistant))
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
            tx.connection.execute('SAVEPOINT validate_schedule')
            receipt = self.enqueue(tx, task, payload, user=user, target=target)
            row = tx.connection.execute('SELECT * FROM evlet_jobs WHERE id=?', (receipt.id,)).fetchone()
            tx.connection.execute('ROLLBACK TO validate_schedule')
            tx.connection.execute('RELEASE validate_schedule')
            due = int(time.time()) + every if start_at is None else timestamp(start_at)
            tx.connection.execute('INSERT INTO evlet_schedules(name,task,payload,actor,model,record_id,interval,next_run) VALUES (?,?,?,?,?,?,?,?)', (schedule, task, row['payload'], row['actor'], row['model'], row['record_id'], every, due))
            if calendar is not None:
                # Same transaction, so a crash cannot leave a calendar schedule running as an interval.
                tx.connection.execute('DELETE FROM evlet_calendar_schedules WHERE name=?', (schedule,))
                tx.connection.execute('INSERT INTO evlet_calendar_schedules(name,expression,timezone) VALUES (?,?,?)', (schedule, *calendar))

    def set_schedule_active(self, schedule, active):
        if type(active) is not bool:
            raise ValueError('Use a boolean active flag')
        with self.database.transaction() as tx:
            self._bound(tx)
            if not tx.connection.execute('UPDATE evlet_schedules SET enabled=? WHERE name=?', (active, name(schedule))).rowcount:
                raise ValueError('Unknown schedule')

    def tick(self, *, now=None):
        now = int(time.time()) if now is None else timestamp(now)
        with self.database.transaction() as tx:
            self._bound(tx)
            rows = tx.connection.execute('SELECT * FROM evlet_schedules WHERE enabled=1 AND next_run<=? ORDER BY next_run,name LIMIT 100', (now,)).fetchall()
            for row in rows:
                # One occurrence after downtime; missed intervals are coalesced.
                definition = self.tasks.get(row['task'])
                if definition is None:
                    tx.connection.execute('UPDATE evlet_schedules SET enabled=0 WHERE name=?', (row['name'],))
                    logger.error('Disabled schedule %s: its task is not registered', row['name'])
                    continue
                key = f"schedule:{row['name']}:{row['next_run']}"
                tx.connection.execute('INSERT OR IGNORE INTO evlet_jobs(task,payload,actor,model,record_id,state,max_attempts,run_at,created_at,dedupe) VALUES (?,?,?,?,?,?,?,?,?,?)', (row['task'], row['payload'], row['actor'], row['model'], row['record_id'], 'queued', definition.max_attempts, now, now, key))
                calendar = tx.connection.execute('SELECT expression,timezone FROM evlet_calendar_schedules WHERE name=?', (row['name'],)).fetchone()
                if calendar:
                    from .services import Calendar
                    next_run = Calendar(calendar['expression'], calendar['timezone']).next(now)
                else:
                    next_run = row['next_run'] + ((now - row['next_run']) // row['interval'] + 1) * row['interval']
                tx.connection.execute('UPDATE evlet_schedules SET next_run=? WHERE name=?', (next_run, row['name']))
            return len(rows)

    def claim(self, *, now=None, lease_seconds=300):
        now = int(time.time()) if now is None else timestamp(now)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError('Use a lease of 1 to 3600 seconds')
        with self.database.transaction() as tx:
            self._bound(tx)
            tx.connection.execute("UPDATE evlet_jobs SET state='failed',error='Worker attempts exhausted',lease=NULL,lease_until=NULL,completed_at=? WHERE state='running' AND lease_until<=? AND attempts>=max_attempts", (now, now))
            row = tx.connection.execute("SELECT * FROM evlet_jobs WHERE (state='queued' AND run_at<=?) OR (state='running' AND lease_until<=? AND attempts<max_attempts) ORDER BY run_at,id LIMIT 1", (now, now)).fetchone()
            if row is None:
                return None
            token = secrets.token_hex(24)
            tx.connection.execute("UPDATE evlet_jobs SET state='running',attempts=attempts+1,lease=?,lease_until=?,error=NULL WHERE id=?", (token, now + lease_seconds, row['id']))
            return row['id'], token

    def execute(self, claim, *, active=lambda: True, now=None):
        """Run a claimed job. A job passes the business's gate like a request, so adding or removing an
        instance field waits for it."""
        from contextlib import nullcontext
        from .database import using
        business = getattr(self, 'business', None)
        with business.gate.shared() if business is not None else nullcontext(), using(self.database):
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
                    row = tx.connection.execute("SELECT * FROM evlet_jobs WHERE id=? AND state='running' AND lease=? AND lease_until>?", (job_id, token, clock)).fetchone()
                    if row is None:
                        return steps > 0
                    if not active():
                        # Stopped between steps: the job goes back to the queue and resumes from its checkpoint.
                        self._release_claim(tx, job_id, token, clock)
                        return steps > 0
                    task, context, payload = self._prepare(tx, row, job_id, token)
                    running = True
                    tx._business = getattr(self, 'business', None)
                    if task.external:
                        break
                    # Job handlers are trusted app code, even when a request runs the job at once.
                    with trusted():
                        result = task.handler(tx, context, payload)
                    if task.resumable and result is not None:
                        checkpoint = encode_checkpoint(result)
                        tx.connection.execute("INSERT INTO evlet_job_progress(job_id,done,checkpoint,updated_at) VALUES (?,0,?,?) "
                                              'ON CONFLICT(job_id) DO UPDATE SET checkpoint=excluded.checkpoint, updated_at=excluded.updated_at',
                                              (job_id, checkpoint, int(time.time())))
                        # Each committed step renews the lease, so a long job is not reclaimed while it makes progress.
                        tx.connection.execute('UPDATE evlet_jobs SET lease_until=CASE WHEN lease_until<? THEN ? ELSE lease_until END WHERE id=? AND lease=?',
                                              (clock + LEASE, clock + LEASE, job_id, token))
                        steps += 1
                        continue
                    if task.resumable:
                        tx.connection.execute('UPDATE evlet_job_progress SET checkpoint=NULL WHERE job_id=?', (job_id,))
                    tx.connection.execute("UPDATE evlet_jobs SET state='succeeded',completed_at=?,lease=NULL,lease_until=NULL WHERE id=? AND lease=?", (int(time.time()), job_id, token))
                    if self.events and row['model']:
                        self.events.publish(tx, self.database.registry.models[row['model']], row['record_id'])
                    return True
            # No open database transaction or supplied Session during external I/O.
            with trusted():
                task.handler(context, payload)
            with self.database.transaction() as tx:
                self._bound(tx)
                completed = tx.connection.execute("UPDATE evlet_jobs SET state='succeeded',completed_at=?,lease=NULL,lease_until=NULL WHERE id=? AND state='running' AND lease=? AND lease_until>?", (int(time.time()), job_id, token, int(time.time()))).rowcount
                if not completed: return False
                if self.events and row['model']:
                    self.events.publish(tx, self.database.registry.models[row['model']], row['record_id'])
            return True
        except Exception as exc:
            logger.exception('Background task %s failed', job_id)
            with self.database.transaction() as tx:
                row = tx.connection.execute("SELECT * FROM evlet_jobs WHERE id=? AND state='running' AND lease=?", (job_id, token)).fetchone()
                if row is None:
                    return False
                if not running and isinstance(exc, sqlite3.OperationalError):
                    self._release_claim(tx, job_id, token, now + 1)
                    return False
                retry = task is not None and row['attempts'] < row['max_attempts'] and not isinstance(exc, (AccessError, NotFoundError, ValidationError))
                due = now + min(86400, task.retry_delay * 2 ** (row['attempts'] - 1)) if retry else now
                # A resumable task keeps its checkpoint, so the retry resumes after the last committed step.
                tx.connection.execute('UPDATE evlet_jobs SET state=?,run_at=?,completed_at=?,lease=NULL,lease_until=NULL,error=? WHERE id=? AND lease=?', ('queued' if retry else 'failed', due, None if retry else now, exc.message if isinstance(exc, AccessError) else type(exc).__name__, job_id, token))
            return True

    def _prepare(self, tx, row, job_id, token):
        """The task, a fresh JobContext and the payload for one claimed job, after the account and target checks."""
        task = self.tasks.get(row['task'])
        if task is None:
            raise ValidationError({'task': 'Task is no longer registered'})
        user = None
        if row['actor'] is not None:
            account = tx.connection.execute('SELECT id,username,role FROM evlet_users WHERE id=? AND active=1', (row['actor'],)).fetchone()
            if account is None:
                raise AccessError(403, 'The requesting account is inactive')
            user = User(*account)
            queued_by = tx.connection.execute('SELECT k.id, k.scope, k.revoked_at FROM evlet_job_keys j LEFT JOIN evlet_api_keys k ON k.id=j.key_id WHERE j.job_id=?', (job_id,)).fetchone()
            if queued_by is not None:
                if queued_by['id'] is None or queued_by['revoked_at'] is not None:
                    raise AccessError(403, 'The API key that queued this job was revoked.')
                user = User(*account, access=self.business.auth.key_access(queued_by['id'], queued_by['scope']))
            queued_by = tx.connection.execute('SELECT client_id FROM evlet_job_assistants WHERE job_id=?', (job_id,)).fetchone()
            if queued_by is not None:
                connection = None
                if queued_by[0]:
                    connection = tx.connection.execute('SELECT * FROM evlet_mcp_clients WHERE id=? AND user_id=? AND revoked_at IS NULL', (queued_by[0], account['id'])).fetchone()
                    if connection is None:
                        raise AccessError(403, 'The AI assistant that queued this job was disconnected.')
                user = self.business.assistants.narrow(user, connection)
        else:
            identity = tx.connection.execute('SELECT role FROM evlet_job_identities WHERE job_id=?', (job_id,)).fetchone()
            if identity: user = User(0, 'schedule', identity[0])
        if user is not None and row['model']:
            model = self.database.registry.models.get(row['model'])
            if model is None: raise NotFoundError()
            self.policy.record(tx, user, model, row['record_id'], 'update')
        namespace = tx.connection.execute('SELECT namespace FROM evlet_job_namespace WHERE id=1').fetchone()[0]
        stable_key = hashlib.sha256((namespace + ':' + (row['dedupe'] or f'job:{job_id}')).encode()).hexdigest()
        saved = tx.connection.execute('SELECT checkpoint FROM evlet_job_progress WHERE job_id=?', (job_id,)).fetchone()
        checkpoint = json.loads(saved[0]) if task.resumable and saved and saved[0] is not None else None
        sql = ('INSERT INTO evlet_job_progress(job_id,done,total,message,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET '
               'done=excluded.done, total=excluded.total, message=excluded.message, updated_at=excluded.updated_at')
        if task.external:
            def report(done, total, message):
                with self.database.transaction() as own:
                    if own.connection.execute("SELECT 1 FROM evlet_jobs WHERE id=? AND state='running' AND lease=?", (job_id, token)).fetchone():
                        own.connection.execute(sql, (job_id, done, total, message, int(time.time())))
        else:
            def report(done, total, message):
                tx.connection.execute(sql, (job_id, done, total, message, int(time.time())))
        context = JobContext(job_id, self.tenant, row['attempts'], user, stable_key, checkpoint)
        object.__setattr__(context, '_report', report)
        object.__setattr__(context, '_business', getattr(self, 'business', None))
        return task, context, json.loads(row['payload'])

    @staticmethod
    def _release_claim(tx, job_id, token, due):
        tx.connection.execute("UPDATE evlet_jobs SET state='queued',attempts=attempts-1,run_at=?,lease=NULL,lease_until=NULL,error=NULL WHERE id=? AND state='running' AND lease=?", (due, job_id, token))

    def run_one(self, *, active=lambda: True, now=None):
        if not active():
            return False
        if getattr(self, 'business', None) is not None:
            self.business.controls.refresh()
        self.tick(now=now)
        claim = self.claim(now=now)
        return False if claim is None else self.execute(claim, active=active, now=now)

    def status(self, job_id, user):
        with self.database.transaction() as tx:
            self._bound(tx)
            row = tx.connection.execute('SELECT * FROM evlet_jobs WHERE id=?', (job_id,)).fetchone()
            if row is None or (row['actor'] != user.id and user.role != 'admin'):
                raise AccessError(404, 'Job not found')
            if row['model']:
                self.policy.record(tx, user, self.database.registry.models[row['model']], row['record_id'])
            result = {key: row[key] for key in ('id', 'state', 'attempts', 'run_at', 'created_at', 'completed_at', 'error')}
            # Progress the handler reported; the checkpoint and payload stay private.
            progress = tx.connection.execute('SELECT done,total,message FROM evlet_job_progress WHERE job_id=?', (job_id,)).fetchone()
            result['progress'] = None if progress is None else {'done': progress['done'], 'total': progress['total'], 'message': progress['message']}
            return result

    def recent(self):
        """Trusted operator summary; never includes payloads or exception messages."""
        with self.database._connection() as db:
            return [{**dict(row), 'record_id': text_id(row['record_id'])}
                    for row in db.execute('SELECT j.id,j.task,j.actor,j.model,j.record_id,j.state,j.attempts,j.run_at,j.error,p.done,p.total,p.message '
                                          'FROM evlet_jobs j LEFT JOIN evlet_job_progress p ON p.job_id=j.id ORDER BY j.id DESC LIMIT 100')]

    def retry(self, job_id):
        with self.database.transaction() as tx:
            self._bound(tx)
            if not tx.connection.execute("UPDATE evlet_jobs SET state='queued',attempts=0,run_at=?,error=NULL,completed_at=NULL WHERE id=? AND state='failed'", (int(time.time()), job_id)).rowcount:
                raise ValueError('Only failed jobs can be retried')
