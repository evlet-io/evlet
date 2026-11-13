"""SQLite storage files and fair write turns (docs/performance.md).

An instance keeps four SQLite files in data/:

* database.sqlite3: records, audit, journal, the job queue, accounts, roles and settings. Everything that must
  save in the same transaction as a record stays here.
* sessions.sqlite3: sign-in sessions, two-step tickets and sign-in attempts.
* logs.sqlite3: the tool-call log, public rate counts and proxy header notes.
* eva.sqlite3: Eva chats, messages, files, usage and tasks.

Every SQLite connection attaches the other three files as the schemas sessions, logs and eva. SQL keeps
naming tables without a schema: SQLite finds a table in whichever file holds it, so reads and joins across
files work unchanged. Writes take turns per file (WriteLine): a write transaction holds the turn of each
file it changes, so a long job step on database.sqlite3 does not hold up a sign-in on sessions.sqlite3.

SQLite cannot enforce a foreign key from one file to another; Evlet checks those links itself. After a crash,
the main file is atomic as before, while the other files are best effort for the last moment of writes.
"""

from collections import deque
from contextvars import ContextVar
from contextlib import closing, contextmanager
from pathlib import Path
import atexit
import logging
import re
import sqlite3
import threading
import time
import weakref

logger = logging.getLogger(__name__)

# The side files and the framework tables each holds. Every other table stays in the main file.
STORES = {
    'sessions': ('evlet_sessions', 'evlet_two_step_tickets', 'evlet_login_limits'),
    'logs': ('evlet_tool_calls', 'evlet_public_limits', 'evlet_forwarded_peers'),
    'eva': ('evlet_eva_lock', 'evlet_eva_conversations', 'evlet_eva_runs', 'evlet_eva_messages', 'evlet_eva_events',
            'evlet_eva_files', 'evlet_eva_usage', 'evlet_eva_reservations', 'evlet_eva_tasks', 'evlet_eva_task_runs'),
}
STORE_OF = {table: store for store, tables in STORES.items() for table in tables}

# Seconds a writer waits for its turn before the usual "database is locked" error (SQLite's own busy wait is the same).
WRITE_WAIT = 5.0
# A job step that has waited this long goes before the requests behind it, so steady requests never starve jobs.
JOB_WAIT = 0.25
# While people are writing, a job rests after each step this many times as long as the step held the file.
JOB_SHARE = 1.0
# People count as writing while a request took the file within this many seconds.
ACTIVE = 1.0
# A write transaction holding a file longer than this logs a warning naming the route or the job.
SLOW_WRITE = 1.0
# How often buffered log writes are saved, in seconds.
FLUSH_EVERY = 0.25

# 'job' while a background job runs, so its writes queue behind requests; 'interactive' otherwise.
WRITE_KIND = ContextVar('evlet_write_kind', default='interactive')
# What is writing, for slow-write warnings: "GET /api/..." or "job #12 import_invoices".
WRITE_LABEL = ContextVar('evlet_write_label', default='')


def side_path(main, store):
    """The file of a side store next to a main SQLite file: data/database.sqlite3 has data/sessions.sqlite3;
    any other name, such as app.sqlite3, has app-sessions.sqlite3."""
    main = Path(main)
    if main.name == 'database.sqlite3':
        return main.with_name(store + '.sqlite3')
    return main.with_name(f'{main.stem}-{store}{main.suffix}')


def files(main):
    """The main SQLite file and its side files, main first: one set for backups, restores and removal."""
    return (Path(main),) + tuple(side_path(main, store) for store in STORES)


def data_files(path):
    """The four SQLite files of an instance directory, main first. Each may also have -wal and -shm files."""
    return files(Path(path) / 'data' / 'database.sqlite3')


def connect(main, *, uri=False, named_rows=False, **options):
    """A SQLite connection to a main file with its side files attached, for server-shell commands that open the
    database themselves. uri=True opens the main file with mode=rw (it must exist); named_rows=True reads rows
    by column name as well as by index."""
    from .sqlite import SQLiteConnection
    main = Path(main)
    sides = {store: side_path(main, store) for store in STORES}
    connection = sqlite3.connect(main.absolute().as_uri() + '?mode=rw' if uri else str(main), uri=uri,
                                 factory=SQLiteConnection, **options)
    if named_rows:
        connection.row_factory = sqlite3.Row
    try:
        for store, path in sides.items():
            connection.execute(f'ATTACH DATABASE ? AS {store}', (path.absolute().as_uri() + '?mode=rwc' if uri else str(path),))
        prepare(connection, sides)
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def shell_transaction(main):
    """A write transaction on the database at main, for server-shell commands: it holds the write lock from the
    start, commits when the block ends and rolls back on an error. Rows read by name and by index."""
    connection = connect(main, isolation_level=None, named_rows=True)
    try:
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


# SQLite file tools for the instance commands (backup, restore, install, the tenant catalog): they open one
# file themselves, without its side files, and hand framework code a connection that runs qb statements.

def read_file(path):
    """A read-only connection to one SQLite file, rows by name and by index. A WAL file whose shared memory
    file cannot be created read only (no -shm file next to it yet) is opened normally with writes refused."""
    from .sqlite import SQLiteConnection
    path = Path(path).resolve()
    try:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, factory=SQLiteConnection)
        connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
    except sqlite3.OperationalError:
        connection = sqlite3.connect(path, timeout=5, factory=SQLiteConnection)
        connection.execute('PRAGMA query_only = ON')
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def file_transaction(path, *, write=False):
    """A transaction on one existing SQLite file with foreign keys on (the tenant catalog): write=True holds
    the write lock from the start. It commits when the block ends; an error leaves nothing written."""
    from .sqlite import SQLiteConnection
    connection = sqlite3.connect(Path(path).as_uri() + '?mode=rw', uri=True, timeout=5, factory=SQLiteConnection)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield connection
        connection.commit()
    finally:
        connection.close()


def copy_set(source_main, target_main):
    """Copy the main file and every side file of source_main into the set named by target_main, from one
    read snapshot of all of them, with SQLite's online backup API. A running server may keep writing.
    Returns the written paths."""
    with closing(connect(source_main, uri=True, isolation_level=None)) as connection:
        connection.execute('BEGIN')
        for schema in ('main', *STORES):
            connection.execute(f'SELECT count(*) FROM {schema}.sqlite_master').fetchone()
        written = []
        for schema, target in zip(('main', *STORES), files(target_main)):
            with closing(sqlite3.connect(target)) as copy:
                connection.backup(copy, name=schema)
                copy.execute('PRAGMA journal_mode=DELETE').fetchone()
            Path(target).chmod(0o600)
            written.append(target)
        connection.execute('COMMIT')
    return written


def snapshot(source, target):
    """Copy one SQLite file into target with the online backup API, readable only by its owner."""
    with closing(sqlite3.connect(source)) as old, closing(sqlite3.connect(target)) as new:
        old.backup(new)
    Path(target).chmod(0o600)


def snapshot_set(source, target):
    """Copy a main SQLite file and each of its side files that exists with the online backup API."""
    for old, new in zip(files(source), files(target)):
        if old.exists():
            snapshot(old, new)


def copy_file(source, target):
    """Copy one SQLite file, read only, into target in rollback mode, with the online backup API."""
    with closing(sqlite3.connect(Path(source).absolute().as_uri() + '?mode=ro', uri=True)) as reader, \
            closing(sqlite3.connect(target)) as copy:
        reader.backup(copy)
        copy.execute('PRAGMA journal_mode=DELETE').fetchone()
    Path(target).chmod(0o600)


def check_file(path):
    """(problem, table names) for a SQLite file: problem is None when its quick check passes."""
    with closing(sqlite3.connect(Path(path).absolute().as_uri() + '?mode=ro', uri=True)) as check:
        try:
            ok = check.execute('PRAGMA quick_check').fetchone()[0]
            names = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError:
            return 'not a SQLite database', set()
    return (None if ok == 'ok' else ok), names


def settle(path):
    """Fold a SQLite file's write-ahead log into it and leave it in rollback mode, ready to move into place.
    Refuses when another process is still writing it."""
    with closing(sqlite3.connect(path)) as connection:
        busy, _, _ = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if busy:
            raise ValueError('Database is busy; stop all writers before installation')
        connection.execute('PRAGMA journal_mode=DELETE').fetchone()


def tables(connection):
    """The names of the tables in the main file and every attached file."""
    names = set()
    for row in connection.execute('PRAGMA database_list').fetchall():
        if row[1] != 'temp':
            names.update(item[0] for item in connection.execute(f'SELECT name FROM "{row[1]}".sqlite_master WHERE type=\'table\''))
    return names


def prepare(connection, sides):
    """Put each attached side file in WAL mode and give it the empty table a write transaction claims
    (Database._claim). Both are no-ops on a file that already has them."""
    for store in sides:
        if connection.execute(f'PRAGMA {store}.journal_mode').fetchone()[0].lower() != 'wal':
            connection.execute(f'PRAGMA {store}.journal_mode = WAL')
        connection.execute(f'CREATE TABLE IF NOT EXISTS {store}.evlet_turn (id INTEGER PRIMARY KEY)')


class Busy(sqlite3.OperationalError):
    """No turn within the wait limit: the same error SQLite gives when its own busy wait runs out."""

    def __init__(self):
        super().__init__('database is locked')


@contextmanager
def job_writes(label):
    """Mark the writes inside as a background job's, named label in slow-write warnings."""
    kind, name = WRITE_KIND.set('job'), WRITE_LABEL.set(label)
    try:
        yield
    finally:
        WRITE_LABEL.reset(name)
        WRITE_KIND.reset(kind)


@contextmanager
def labelled(label):
    """Name the writes inside in slow-write warnings, such as the route of a request."""
    token = WRITE_LABEL.set(label)
    try:
        yield
    finally:
        WRITE_LABEL.reset(token)


class WriteLine:
    """The turns of one SQLite file within this process, first come first served.

    Requests wait in one queue and job steps in another. When the turn is free, the oldest request goes next,
    unless the oldest job step has waited job_wait seconds: then it goes, so jobs always get a turn within about
    job_wait plus one request.

    While people are writing (a request waited during the step, or took the file within the last ACTIVE seconds),
    a job rests after each step for job_share times as long as the step held the file before it may take the file
    again. Requests that arrive meanwhile go at once, so under load a job gets about half of the file's time; a
    job alone runs at full speed. Its job_wait guarantee counts from the end of the rest.

    A thread that holds the turn may take it again (nested use). Other processes are not in the line; against
    them SQLite's own busy wait still applies."""

    def __init__(self, job_wait=JOB_WAIT, job_share=JOB_SHARE):
        self.job_wait, self.job_share = job_wait, job_share
        self._condition = threading.Condition()
        self._owner = None
        self._depth = 0
        self._queues = {'interactive': deque(), 'job': deque()}
        # Who holds the turn and since when, whether a request waited meanwhile, and when a resting job may go.
        self._holder, self._since, self._contended, self._rest_until = None, 0.0, False, 0.0
        self._last_request = float('-inf')

    def _job_ready(self, now):
        """When the oldest waiting job step may take the turn ahead of requests: (may go at all, may go first)."""
        jobs = self._queues['job']
        if not jobs or now < self._rest_until:
            return False, False
        return True, now - max(jobs[0][1], self._rest_until) >= self.job_wait

    def _next(self, now):
        requests = self._queues['interactive']
        allowed, first = self._job_ready(now)
        if allowed and (not requests or first):
            return self._queues['job'][0]
        return requests[0] if requests else None

    def acquire(self, kind='interactive', timeout=WRITE_WAIT):
        """Wait for the turn; returns the seconds waited. Raises Busy after timeout seconds."""
        me = threading.get_ident()
        kind = 'job' if kind == 'job' else 'interactive'
        with self._condition:
            if self._owner == me:
                self._depth += 1
                return 0.0
            arrived = time.monotonic()
            ticket = (object(), arrived)
            queue = self._queues[kind]
            queue.append(ticket)
            while True:
                now = time.monotonic()
                if self._owner is None and self._next(now) is ticket:
                    queue.popleft()
                    self._owner, self._depth, self._holder, self._since = me, 1, kind, now
                    if kind == 'interactive':
                        self._last_request = now
                    return now - arrived
                if kind == 'interactive' and self._holder == 'job':
                    self._contended = True
                remaining = arrived + timeout - now
                if remaining <= 0:
                    queue.remove(ticket)
                    self._condition.notify_all()
                    raise Busy()
                # A rest ending or a job step reaching job_wait changes who is next, so look again by then.
                wake = remaining
                jobs = self._queues['job']
                if jobs and self._owner is None:
                    for moment in (self._rest_until, max(jobs[0][1], self._rest_until) + self.job_wait):
                        if moment > now:
                            wake = min(wake, moment - now)
                self._condition.wait(max(0.001, wake))

    def release(self):
        with self._condition:
            if self._owner != threading.get_ident():
                raise RuntimeError('Release a write turn from the thread that holds it')
            self._depth -= 1
            if self._depth == 0:
                now = time.monotonic()
                if self._holder == 'job' and (self._contended or self._queues['interactive'] or now - self._last_request < ACTIVE):
                    self._rest_until = now + self.job_share * (now - self._since)
                self._owner, self._holder, self._contended = None, None, False
                self._condition.notify_all()

    def waiting(self):
        with self._condition:
            return {kind: len(queue) for kind, queue in self._queues.items()}


_lines = {}
_lines_lock = threading.Lock()
# Guards each Database's idle connections (Database._reuse).
IDLE_LOCK = threading.Lock()
# Every SQLite Database in this process, so lifecycle commands can close their idle connections (release_idle).
DATABASES = weakref.WeakSet()


def release_idle():
    """Close every connection kept for reuse in this process, before a command swaps or settles database files."""
    for database in list(DATABASES):
        database._close_idle()


def line(path):
    """The WriteLine of a file, shared by every Database on that file in this process."""
    key = str(Path(path).absolute())
    with _lines_lock:
        found = _lines.get(key)
        if found is None:
            found = _lines[key] = WriteLine()
        return found


class Turns:
    """The write turns one transaction holds, taken in order (main first, then side files) and released together."""

    def __init__(self, database, store):
        self.database, self.store = database, store
        self.held = {}
        self.failed = None

    def take(self, name):
        if name in self.held:
            return True
        path = self.database.path if name == 'main' else side_path(self.database.path, name)
        turn = line(path)
        turn.acquire(WRITE_KIND.get(), self.database.write_wait)
        self.held[name] = (turn, time.monotonic())
        return True

    def release(self):
        slow = self.database.slow_write
        for name, (turn, since) in reversed(self.held.items()):
            held = time.monotonic() - since
            turn.release()
            if slow is not None and held > slow:
                logger.warning('A write held %s for %.2f s (%s)', 'database.sqlite3' if name == 'main' else name + '.sqlite3',
                               held, WRITE_LABEL.get() or 'no route or job named')
        self.held.clear()

    # The SQLite authorizer: it runs while a statement is prepared, before it reads or writes anything.
    def authorize(self, action, first, second, schema, trigger):
        if schema not in STORES:
            if self.store is not None and schema == 'main' and action in WRITES:
                self.failed = f'A {self.store} transaction cannot change {first}; use database.transaction()'
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if self.store is not None:
            if schema != self.store and action in WRITES:
                self.failed = f'A {self.store} transaction cannot change {first}; use database.transaction()'
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        # A main transaction that reads or writes a side table takes that file's turn first, so its read and
        # a later write see one version of the file, and two writers of it never meet inside SQLite.
        if action in WRITES or action == sqlite3.SQLITE_READ:
            try:
                self.take(schema)
            except Busy:
                self.failed = 'busy'
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


WRITES = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE,
          sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_ALTER_TABLE,
          sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER}

_CREATE_TABLE = re.compile(r'\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(evlet_\w+)', re.IGNORECASE)
_CREATE_INDEX = re.compile(r'\s*CREATE\s+(UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+ON\s+(evlet_\w+)', re.IGNORECASE)
_REFERENCE = re.compile(r'\s+REFERENCES\s+"?(\w+)"?\s*\([^)]*\)(\s+ON\s+(DELETE|UPDATE)\s+(CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION))*',
                        re.IGNORECASE)


def placed(statement):
    """A CREATE TABLE or CREATE INDEX of a side table, written for the file that holds it. A reference to a
    table in another file is dropped: SQLite cannot check it, and Evlet checks those links itself."""
    match = _CREATE_TABLE.match(statement)
    if match and match[1] in STORE_OF:
        store = STORE_OF[match[1]]
        body = statement[:match.start(1)] + f'{store}.' + statement[match.start(1):]
        return _REFERENCE.sub(lambda found: found[0] if STORE_OF.get(found[1]) == store else '', body)
    match = _CREATE_INDEX.match(statement)
    if match and match[3] in STORE_OF:
        return statement[:match.start(2)] + f'{STORE_OF[match[3]]}.' + statement[match.start(2):]
    return statement


class LogBuffer:
    """Busy log and last-seen writes, saved together by one background thread about every FLUSH_EVERY seconds.

    add() only appends to a list, so a request never waits on these writes. Each flush is one short transaction
    per file. close() and process exit flush what is left; a crash loses at most the writes since the last flush."""

    def __init__(self, database):
        self.database = database
        self._lock = threading.Lock()
        self._pending = []
        self._thread = None
        _buffers.add(self)

    def add(self, store, statement):
        """Queue one query builder statement (evlet/qb.py) for the store's file (None: the main file)."""
        from .sqlite import compile_statement
        sql, values = compile_statement(statement)
        with self._lock:
            self._pending.append((store, sql, tuple(values)))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name='evlet-log-writer', daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            time.sleep(FLUSH_EVERY)
            if not self.flush():
                with self._lock:
                    if not self._pending:
                        self._thread = None
                        return

    def flush(self):
        """Save every buffered write now; returns how many were saved."""
        with self._lock:
            pending, self._pending = self._pending, []
        if not pending:
            return 0
        groups = {}
        for store, sql, values in pending:
            groups.setdefault(store, []).append((sql, values))
        saved = 0
        for store, statements in groups.items():
            try:
                with self.database.transaction(store=store) as tx:
                    for sql, values in statements:
                        tx.connection.execute(sql, values)
                saved += len(statements)
            except Exception:
                logger.warning('Could not save %d buffered %s writes', len(statements), store or 'database', exc_info=True)
        return saved


_buffers = weakref.WeakSet()


@atexit.register
def _flush_all():
    for buffer in list(_buffers):
        try:
            buffer.flush()
        except Exception:
            pass


# ---------- Framework step 0010_split_files: move the side tables out of an older single file ----------

class SplitError(RuntimeError):
    """The move stopped; the tables still in the main file are untouched and the next evlet migrate goes on."""


_TABLE_SQL = re.compile(r'\s*CREATE\s+TABLE\s+("?)(\w+)\1', re.IGNORECASE)
_INDEX_SQL = re.compile(r'\s*CREATE\s+(UNIQUE\s+)?INDEX\s+("?)(\w+)\2\s+ON', re.IGNORECASE)


def _in_store(sql, store, pattern, group):
    match = pattern.match(sql)
    return sql[:match.start(group)] + f'{store}.' + sql[match.start(group):]


def require_space(directory, needed, what):
    """Refuse early, with a clear message, when the disk holding directory has less than needed bytes free."""
    import shutil
    free = shutil.disk_usage(directory).free
    if free < needed:
        raise SplitError(f'Not enough free disk space to {what}: it needs about {needed // 2**20 + 1} MiB and '
                         f'{free // 2**20} MiB is free in {directory}. Free some space and run evlet migrate again; nothing was changed.')


def pending_split(connection):
    """The (store, table) pairs whose table is still in the main file."""
    present = {row[0] for row in connection.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    return [(store, table) for store, names in STORES.items() for table in names if table in present]


def _size(connection, table):
    try:
        return connection.execute("SELECT sum(pgsize) FROM dbstat('main') WHERE name=?", (table,)).fetchone()[0] or 0
    except sqlite3.OperationalError:
        return None


def split_files(database, *, after_copy=None):
    """Move each side table still in the main file into its own file (framework step 0010_split_files).

    Per table: in one transaction the side file gets the table, its indexes, every row and its AUTOINCREMENT
    counter, and the row counts are compared; then, in a second transaction, the counts are compared again and
    the table is dropped from the main file. While the main file still holds a table it is the one SQL finds, so
    after a crash at any point a re-run copies that table again and finishes. A failure names the table, leaves
    it in the main file and raises SplitError. Returns the moved tables. after_copy(table) is for tests."""
    moved = []
    # A connection of its own: foreign keys are off while tables move, and it is never reused.
    with closing(connect(database.path, uri=database.must_exist, isolation_level=None)) as connection:
        connection.execute('PRAGMA foreign_keys = OFF')
        todo = pending_split(connection)
        if not todo:
            return moved
        sizes = [_size(connection, table) for _, table in todo]
        estimate = sum(sizes) if None not in sizes else database.path.stat().st_size
        # The copies, their write-ahead logs and a margin; the main file only shrinks.
        require_space(database.path.parent, 2 * estimate + 16 * 2**20, 'move sessions, logs and Eva into their own files')
        for store, table in todo:
            try:
                connection.execute('BEGIN IMMEDIATE')
                sql = connection.execute("SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                indexes = [row[0] for row in connection.execute(
                    "SELECT sql FROM main.sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,))]
                connection.execute(f'DROP TABLE IF EXISTS {store}.{table}')
                created = _REFERENCE.sub(lambda found: found[0] if STORE_OF.get(found[1]) == store else '', _in_store(sql, store, _TABLE_SQL, 2))
                connection.execute(created)
                for index in indexes:
                    connection.execute(_in_store(index, store, _INDEX_SQL, 3))
                columns = ', '.join(f'"{row[1]}"' for row in connection.execute(f'PRAGMA main.table_info({table})'))
                connection.execute(f'INSERT INTO {store}.{table} ({columns}) SELECT {columns} FROM main.{table}')
                if connection.execute("SELECT 1 FROM main.sqlite_master WHERE name='sqlite_sequence'").fetchone():
                    counter = connection.execute('SELECT seq FROM main.sqlite_sequence WHERE name=?', (table,)).fetchone()
                    if counter is not None and connection.execute(f"SELECT 1 FROM {store}.sqlite_master WHERE name='sqlite_sequence'").fetchone():
                        connection.execute(f'DELETE FROM {store}.sqlite_sequence WHERE name=?', (table,))
                        connection.execute(f'INSERT INTO {store}.sqlite_sequence(name, seq) VALUES (?, ?)', (table, counter[0]))
                _same(connection, store, table)
                connection.execute('COMMIT')
                if after_copy is not None:
                    after_copy(table)
                connection.execute('BEGIN IMMEDIATE')
                _same(connection, store, table)
                connection.execute(f'DROP TABLE main.{table}')
                connection.execute('COMMIT')
            except BaseException as exc:
                if connection.in_transaction:
                    connection.execute('ROLLBACK')
                if isinstance(exc, SplitError) or not isinstance(exc, Exception):
                    raise
                raise SplitError(f'Could not move {table} into {store}.sqlite3: {exc}. It stays in the main file; '
                                 'fix the cause and run evlet migrate again.') from exc
            moved.append(table)
    return moved


def _same(connection, store, table):
    old = connection.execute(f'SELECT count(*) FROM main.{table}').fetchone()[0]
    new = connection.execute(f'SELECT count(*) FROM {store}.{table}').fetchone()[0]
    if old != new:
        raise SplitError(f'Moving {table} into {store}.sqlite3 copied {new} of {old} rows. It stays in the main file; '
                         'run evlet migrate again.')
