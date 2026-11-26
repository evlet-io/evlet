"""The project's operation log in project.db: one append-only row when a lifecycle operation starts and one when it ends.

Only a project with an endpoint has project.db (.evlet/project.sqlite3, docs/project-endpoint.md). There every
lifecycle function of evlet/cli/core.py marked @logged appends its rows, whether the CLI, the portal or an MCP client
called it; without project.db the functions run exactly as before and nothing is written.
"""

from contextlib import closing
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import functools
import inspect
import json
from pathlib import Path
import sys

from .. import qb
from ..fields import Integer, Text
from ..models import Model, Registry
from ..permissions import Rule

DATABASE = Path('.evlet/project.sqlite3')
ORIGINS = ('cli', 'portal', 'mcp')
# Arguments that are callables or test doubles, never written to the log.
UNLOGGED = frozenset(('root', 'confirm', 'ask', 'out', 'run', 'which', 'fetch', 'sleep', 'clock', 'unit_dir', 'capture'))
OUTPUT_LIMIT = 400_000


class Operation(Model):
    """One row when an operation starts (result 'started', ends 0) and one when it ends (ends: the start row's id)."""
    kind = Text(max_length=64, required=True)
    instance = Text(max_length=48, default='')
    arguments = Text(max_length=20_000, default='{}')
    origin = Text(max_length=16, required=True)
    result = Text(max_length=16, required=True)
    started = Text(max_length=32, required=True)
    finished = Text(max_length=32, default='')
    output = Text(max_length=OUTPUT_LIMIT + 100, default='')
    lock_before = Text(max_length=20_000, default='')  # apps.lock before the operation
    error = Text(max_length=4_000, default='')
    ends = Integer(default=0)

    class Meta:
        ledger = True
        append_only = True
        indexes = [('ends',)]


class Project(Model):
    """What every project service names as its permission: the owner (the admin role) reads it. It holds no rows;
    a ledger model has no public surface, so the services cannot name the operation log itself."""
    permissions = {'admin': Rule(('read',))}

    class Meta:
        api = 'read'


def registry():
    return Registry(Operation, Project)


def database_path(root):
    return Path(root) / DATABASE


def exists(root):
    path = database_path(root)
    return path.is_file() and not path.is_symlink()


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _text(value, limit):
    value = '' if value is None else value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return value if len(value) <= limit else value[:limit - 40] + f'\n[cut: {len(value) - limit + 40} more characters]'


def _insert(root, row, tx=None):
    if tx is not None:
        return tx.insert_many(Operation, [row])[0]
    from ..database import Database
    database = Database(database_path(root), registry())
    try:
        with database.transaction() as tx:
            return tx.insert_many(Operation, [row])[0]
    finally:
        database.close()


def begin(root, kind, instance, arguments, origin, started, tx=None):
    """Append the start row of an operation and return its id. tx: the endpoint's own transaction on project.db, when
    the caller holds one (a service of the endpoint writes through the transaction of its request)."""
    if origin not in ORIGINS:
        raise ValueError('origin is cli, portal or mcp')
    return _insert(root, {'kind': kind, 'instance': instance or '', 'arguments': _text(arguments, 20_000), 'origin': origin,
                          'result': 'started', 'started': started}, tx)


def end(root, start, *, kind, instance, origin, started, ok, output=(), before=None, error='', tx=None):
    """Append the end row of the operation whose start row is start."""
    return _insert(root, {'kind': kind, 'instance': instance or '', 'origin': origin, 'result': 'ok' if ok else 'failed',
                          'started': started, 'finished': now(), 'output': _text('\n'.join(output), OUTPUT_LIMIT),
                          'lock_before': _text(before, 20_000), 'error': _text(error, 4_000), 'ends': start}, tx)


COLUMNS = ('id', 'kind', 'instance', 'arguments', 'origin', 'result', 'started', 'finished', 'output', 'lock_before', 'error', 'ends')


def operations(root, *, limit=50, before=0):
    """The newest operations, newest first: each start row merged with its end row. An operation with no end row is
    'started' (still running, or interrupted when the process that ran it ended)."""
    from ..writes import read_file
    if not exists(root):
        return []
    with closing(read_file(database_path(root))) as connection:
        query = qb.select('operation', *COLUMNS).where(qb.eq('ends', 0))
        if before:
            query = query.where(qb.lt('id', before))
        starts = [dict(row) for row in connection.run(query.order_by(qb.desc('id')).limit(limit)).all()]
        ids = [row['id'] for row in starts]
        ends = {row['ends']: dict(row) for row in connection.run(qb.select('operation', *COLUMNS).where(qb.in_('ends', ids))).all()} if ids else {}
    return [merged(row, ends.get(row['id'])) for row in starts]


def operation(root, start):
    """One operation by the id of its start row, or None."""
    from ..writes import read_file
    if not exists(root):
        return None
    with closing(read_file(database_path(root))) as connection:
        row = connection.run(qb.select('operation', *COLUMNS).where(qb.eq('id', start), qb.eq('ends', 0))).one()
        if row is None:
            return None
        finish = connection.run(qb.select('operation', *COLUMNS).where(qb.eq('ends', start))).one()
    return merged(dict(row), dict(finish) if finish else None)


def merged(start, finish):
    result = {'id': start['id'], 'kind': start['kind'], 'instance': start['instance'], 'origin': start['origin'],
              'arguments': json.loads(start['arguments'] or '{}'), 'started': start['started'],
              'result': 'started', 'finished': None, 'output': [], 'before': None, 'error': ''}
    if finish:
        result.update(result=finish['result'], finished=finish['finished'], output=finish['output'].split('\n') if finish['output'] else [],
                      before=json.loads(finish['lock_before']) if finish['lock_before'] else None, error=finish['error'])
    return result


@dataclass
class Running:
    """The operation this thread is running, set by whoever logged its start (the endpoint, or @logged for the CLI).
    It is the out of the functions it runs; lifecycle functions called inside it do not log again."""
    origin: str = 'cli'
    id: int | None = None
    lines: list = field(default_factory=list)
    sink: object = None

    def __call__(self, line):
        self.lines.append(str(line))
        if self.sink is not None:
            self.sink(str(line))


CURRENT = ContextVar('evlet_operation', default=None)


def logged(kind, *, instance='instance'):
    """Log each call of a lifecycle function of evlet/cli/core.py when the project has an endpoint.

    instance names the argument that holds the instance (None: the operation is for the whole project). The function's
    out lines become the operation's output; its Outcome.before its before state."""
    def decorate(function):
        signature = inspect.signature(function)

        @functools.wraps(function)
        def call(*args, **kwargs):
            values = signature.bind(*args, **kwargs).arguments
            if CURRENT.get() is not None:
                # Inside an operation already (app_update runs migrate): its lines reach that operation through out,
                # and that operation logs once.
                return function(*args, **kwargs)
            root = values['root']
            if not exists(root):
                return function(*args, **kwargs)
            arguments = {key: value for key, value in values.items() if key not in UNLOGGED}
            name = values.get(instance) if instance else ''
            running, started = Running(origin='cli'), now()
            try:
                running.id = begin(root, kind, name, arguments, 'cli', started)
            except Exception as exc:  # the log must never stop a lifecycle command
                print(f'Note: the operation log in {database_path(root)} could not be written: {exc}', file=sys.stderr)
                return function(*args, **kwargs)
            token = CURRENT.set(running)
            try:
                running.sink = values.get('out')
                outcome = function(*args, **{**kwargs, 'out': running})
            except BaseException as exc:
                _end(root, running, kind, name, started, False, None, str(exc) or type(exc).__name__)
                raise
            finally:
                CURRENT.reset(token)
            _end(root, running, kind, name, started, outcome.ok, outcome.before, '')
            return outcome
        call.__logged__ = kind
        return call
    return decorate


def _end(root, running, kind, instance, started, ok, before, error):
    try:
        end(root, running.id, kind=kind, instance=instance, origin=running.origin, started=started, ok=ok,
            output=running.lines, before=before, error=error)
    except Exception as exc:
        print(f'Note: the operation log in {database_path(root)} could not be written: {exc}', file=sys.stderr)
