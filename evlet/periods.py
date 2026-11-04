"""Period locks: Meta.period names the stored date that places a record in a period, and a lock date closes
every period on or before it, for every writer. See docs/model-engine.md#closed-periods.

    class Meta:
        period = "posting_date"       # a stored Date or DateTime field
        period_scope = "company"      # optional: a stored Link whose records can each have their own lock

Locks live in evlet_period_locks, beside the other Controls tables: scope "" is the instance lock and
"<model>:<id>" the lock of one scope record. The lock that applies is the later of the two. Only trusted
Python may write a closed period, inside `session.unlocked_periods(reason)`, which is logged and audited.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import sqlite3
from types import MappingProxyType
from zoneinfo import ZoneInfo

from .fields import Link
from .models import ValidationError

logger = logging.getLogger('evlet.periods')
TABLE = ('CREATE TABLE IF NOT EXISTS evlet_period_locks (scope TEXT PRIMARY KEY, lock_date TEXT NOT NULL, '
         'changed_by INTEGER, changed_at INTEGER NOT NULL)')


@dataclass(frozen=True)
class Period:
    field: str
    scope: str | None
    kind: str


def compile_periods(registry):
    """{model: Period} from each Meta.period, checked against the model's stored fields."""
    result = {}
    for model in registry.models.values():
        name, scope = getattr(model.Meta, 'period', None), getattr(model.Meta, 'period_scope', None)
        if name is None:
            if scope is not None:
                raise ValueError(f'{model.__name__}: Meta.period_scope needs Meta.period')
            continue
        field = model.__app_stored__.get(name) if isinstance(name, str) else None
        if field is None or field.kind not in ('date', 'datetime'):
            raise ValueError(f'{model.__name__}: Meta.period names a stored Date or DateTime field ({name!r} is not one)')
        if scope is not None and not isinstance(model.__app_stored__.get(scope) if isinstance(scope, str) else None, Link):
            raise ValueError(f'{model.__name__}: Meta.period_scope names a stored Link field ({scope!r} is not one)')
        result[model] = Period(name, scope, field.kind)
    return MappingProxyType(result)


def exists(connection, table):
    if isinstance(connection, sqlite3.Connection):
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
    return bool(connection.execute('SELECT to_regclass(?) IS NOT NULL', (table,)).fetchone()[0])


def saved(session):
    """{scope: date} of every lock, read once per transaction. No table (no Controls) means no lock."""
    found = getattr(session, '_period_locks', None)
    if found is None:
        found = {}
        if exists(session.connection, 'evlet_period_locks'):
            found = {row[0]: date.fromisoformat(row[1]) for row in session.connection.execute('SELECT scope, lock_date FROM evlet_period_locks')}
        session._period_locks = found
    return found


def scope_key(registry, model, record_id):
    return f'{registry.name_for(model)}:{record_id}'


def lock_for(session, model, values):
    """The lock date that applies to a record with these values, or None."""
    period, locks = session.registry.periods.get(model), saved(session)
    if period is None or not locks:
        return None
    dates = [locks.get('')]
    if period.scope is not None and values.get(period.scope) is not None:
        target = model.__fields__[period.scope].target
        dates.append(locks.get(scope_key(session.registry, target, values[period.scope])))
    return max((item for item in dates if item is not None), default=None)


def day(session, period, value):
    """The calendar day of a period value; a DateTime is read in the instance timezone."""
    if period.kind == 'date':
        return value
    zone = timezone.utc if session.timezone == 'UTC' else ZoneInfo(session.timezone)
    return value.astimezone(zone).date()


def message(lock):
    return f'This date is in a closed period (locked up to {lock:%d-%m-%Y})'


def closed(session, model, values):
    """The lock date when these values place the record in a closed period, else None."""
    period = session.registry.periods.get(model)
    if period is None or values is None or values.get(period.field) is None:
        return None
    lock = lock_for(session, model, values)
    return lock if lock is not None and day(session, period, values[period.field]) <= lock else None


def check(session, model, before=None, after=None):
    """Refuse a write that touches a closed period: `before` is the saved record's values (an update or delete),
    `after` the values it would have (a create or update). Moving a date into or out of a closed period fails too."""
    period = session.registry.periods.get(model)
    if period is None or getattr(session, '_periods_unlocked', False):
        return
    for values in (before, after):
        lock = closed(session, model, values)
        if lock is not None:
            raise ValidationError({period.field: message(lock)})


def reopened(session, model, values):
    """Mirror rows keep the original date while its period is open. In a closed period they take the first
    open date: the day after the lock that applies, or today (instance timezone) when that is later."""
    period = session.registry.periods.get(model)
    lock = None if getattr(session, '_periods_unlocked', False) else closed(session, model, values)
    if lock is None:
        return values
    zone = timezone.utc if session.timezone == 'UTC' else ZoneInfo(session.timezone)
    now = datetime.now(timezone.utc)
    first = max(lock + timedelta(days=1), now.astimezone(zone).date())
    if period.kind == 'date':
        value = first
    else:
        value = now if first == now.astimezone(zone).date() else datetime.combine(first, time(0), zone).astimezone(timezone.utc)
    return {**values, period.field: value}


@contextmanager
def unlocked(session, reason):
    """Let trusted Python write closed periods for a migration or a data repair. The reason is logged and,
    with Controls, written to the audit log in the same transaction. No HTTP route reaches this."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Give the reason for writing a closed period')
    logger.warning('Period locks bypassed: %s', reason.strip())
    if exists(session.connection, 'evlet_audit'):
        from .controls import audit
        audit(session.connection, 'python', 'Periods', 'All periods', 'Locks bypassed: ' + reason.strip()[:500])
    previous, session._periods_unlocked = getattr(session, '_periods_unlocked', False), True
    try:
        yield session
    finally:
        session._periods_unlocked = previous


def set_lock(connection, scope, value, actor):
    """Save (value is a date) or clear (None) one lock and write its audit row; returns the earlier date."""
    from .controls import audit
    import time as clock
    row = connection.execute('SELECT lock_date FROM evlet_period_locks WHERE scope=?', (scope,)).fetchone()
    before = None if row is None else row[0]
    after = None if value is None else value.isoformat()
    if before == after:
        return before
    if after is None:
        connection.execute('DELETE FROM evlet_period_locks WHERE scope=?', (scope,))
    else:
        connection.execute('INSERT INTO evlet_period_locks(scope,lock_date,changed_by,changed_at) VALUES (?,?,?,?) '
                           'ON CONFLICT(scope) DO UPDATE SET lock_date=excluded.lock_date, changed_by=excluded.changed_by, '
                           'changed_at=excluded.changed_at', (scope, after, actor.id, int(clock.time())))
    audit(connection, actor.username, 'Periods', scope or 'All records', 'Opened' if after is None else 'Locked up to ' + value.strftime('%d-%m-%Y'),
          before, after)
    return before
