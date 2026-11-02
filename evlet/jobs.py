"""A small durable queue for short single-node SQLite tasks."""

from dataclasses import dataclass
import json
import hashlib
import logging
from pathlib import Path
import re
import secrets
import sqlite3
import time

from .auth import AccessError, User
from .database import NotFoundError
from .models import ValidationError
from .permissions import Policy

logger = logging.getLogger(__name__)


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


class JobQueue:
    def __init__(self, database, tasks, *, tenant='local', events=None, policy=None):
        if not isinstance(tenant, str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}', tenant):
            raise ValueError('Use an explicit tenant slug')
        self.database, self.tenant, self.events = database, tenant, events
        self.policy = policy or Policy(database.registry)
        if self.policy.registry is not database.registry or (events and events.database is not database):
            raise ValueError('Jobs must use the same database, event store, and policy')
        self.tasks = {}
        for task in tasks:
            name(task.name)
            if type(task.external) is not bool: raise ValueError('Task external mode must be a boolean')
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
        task = None
        started = False
        try:
            with self.database.transaction() as tx:
                self._bound(tx)
                row = tx.connection.execute("SELECT * FROM evlet_jobs WHERE id=? AND state='running' AND lease=? AND lease_until>?", (job_id, token, now)).fetchone()
                if row is None:
                    return False
                if not active():
                    self._release_claim(tx, job_id, token, now)
                    return False
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
                context = JobContext(job_id, self.tenant, row['attempts'], user, stable_key)
                payload = json.loads(row['payload'])
                started = True
                tx._business = getattr(self, 'business', None)
                if not task.external:
                    task.handler(tx, context, payload)
                    tx.connection.execute("UPDATE evlet_jobs SET state='succeeded',completed_at=?,lease=NULL,lease_until=NULL WHERE id=? AND lease=?", (int(time.time()), job_id, token))
                    if self.events and row['model']:
                        self.events.publish(tx, self.database.registry.models[row['model']], row['record_id'])
            if task.external:
                # No open database transaction or supplied Session during external I/O.
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
                if not started and isinstance(exc, sqlite3.OperationalError):
                    self._release_claim(tx, job_id, token, now + 1)
                    return False
                retry = task is not None and row['attempts'] < row['max_attempts'] and not isinstance(exc, (AccessError, NotFoundError, ValidationError))
                due = now + min(86400, task.retry_delay * 2 ** (row['attempts'] - 1)) if retry else now
                tx.connection.execute('UPDATE evlet_jobs SET state=?,run_at=?,completed_at=?,lease=NULL,lease_until=NULL,error=? WHERE id=? AND lease=?', ('queued' if retry else 'failed', due, None if retry else now, exc.message if isinstance(exc, AccessError) else type(exc).__name__, job_id, token))
            return True

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
            return {key: row[key] for key in ('id', 'state', 'attempts', 'run_at', 'created_at', 'completed_at', 'error')}

    def recent(self):
        """Trusted operator summary; never includes payloads or exception messages."""
        with self.database._connection() as db:
            return [dict(row) for row in db.execute('SELECT id,task,actor,model,record_id,state,attempts,run_at,error FROM evlet_jobs ORDER BY id DESC LIMIT 100')]

    def retry(self, job_id):
        with self.database.transaction() as tx:
            self._bound(tx)
            if not tx.connection.execute("UPDATE evlet_jobs SET state='queued',attempts=0,run_at=?,error=NULL,completed_at=NULL WHERE id=? AND state='failed'", (int(time.time()), job_id)).rowcount:
                raise ValueError('Only failed jobs can be retried')
