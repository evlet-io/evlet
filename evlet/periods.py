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
from datetime import date, timezone
import logging
from types import MappingProxyType
from zoneinfo import ZoneInfo

from .fields import Link
from .models import ValidationError
from . import qb
from .adapter import Column, TableDefinition

logger = logging.getLogger('evlet.periods')
TABLE = TableDefinition('evlet_period_locks', (Column('scope', 'TEXT', primary=True), Column('lock_date', 'TEXT', required=True),
                                              Column('changed_by', 'INTEGER'), Column('changed_at', 'INTEGER', required=True)))


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
    return connection.table_exists(table)


def saved(session):
    """{scope: date} of every lock, read once per transaction. No table (no Controls) means no lock."""
    found = getattr(session, '_period_locks', None)
    if found is None:
        found = {}
        if exists(session.connection, 'evlet_period_locks'):
            found = {row[0]: date.fromisoformat(row[1]) for row in session.connection.run(qb.select('evlet_period_locks', 'scope', 'lock_date'))}
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


def save_lock(connection, scope, value, actor, reason=None):
    """Save (value is a date) or clear (None) one lock and write its audit row; returns the earlier date.
    The Periods page and set_lock() both write through it. A reason is added to the audit detail."""
    from .controls import audit
    import time as clock
    row = connection.run(qb.select('evlet_period_locks', 'lock_date').where(scope=scope)).one()
    before = None if row is None else row[0]
    after = None if value is None else value.isoformat()
    if before == after:
        return before
    if after is None:
        connection.run(qb.delete('evlet_period_locks').where(scope=scope))
    else:
        changed_at = int(clock.time())
        connection.run(qb.insert('evlet_period_locks', scope=scope, lock_date=after, changed_by=actor.id, changed_at=changed_at)
                       .on_conflict('scope').do_update(lock_date=after, changed_by=actor.id, changed_at=changed_at))
    detail = 'Opened' if after is None else 'Locked up to ' + value.strftime('%d-%m-%Y')
    audit(connection, actor.username, 'Periods', scope or 'All records', detail if reason is None else f'{detail}: {reason[:500]}',
          before, after)
    return before


# ---------- the public API: evlet.periods.lock_date and evlet.periods.set_lock ----------

def scope_of(context, model, scope_id):
    """(session, lock key) for a model with Meta.period and an optional record of its Meta.period_scope target."""
    session = getattr(context, '_tx', context)
    if not hasattr(session, 'connection') or not hasattr(session, 'registry'):
        raise ValueError('Pass a document context (business.context(user), self.context) or a transaction session')
    registry = session.registry
    model = registry.resolve(model)
    period = registry.periods.get(model)
    if period is None:
        raise ValueError(f'{model.__name__} has no Meta.period')
    if scope_id is None:
        return session, ''
    if period.scope is None:
        raise ValueError(f'{model.__name__} has no Meta.period_scope, so its periods have one lock: pass scope_id=None')
    from .identity import RecordId
    target = model.__fields__[period.scope].target
    try:
        record_id = RecordId(scope_id)
    except (TypeError, ValueError):
        raise ValidationError({'scope': 'Use a record id'}) from None
    if session.connection.run(qb.select(registry.name_for(target), 'id').where(id=record_id)).one() is None:
        raise ValidationError({'scope': f'No {target.__name__} has that id'})
    return session, scope_key(registry, target, record_id)


def lock_date(context, model, scope_id=None):
    """The lock date that applies to records of `model` (a model with Meta.period) in one scope: the later of the
    instance lock and the lock of the scope record `scope_id` (a record of its Meta.period_scope Link's target).
    scope_id=None reads the instance lock alone. None when no period is closed. `context` is a document context
    (business.context(user), self.context) or a transaction session. Reading a lock needs no permission."""
    session, key = scope_of(context, model, scope_id)
    locks = saved(session)
    dates = [locks.get('')] + ([locks.get(key)] if key else [])
    return max((item for item in dates if item is not None), default=None)


def set_lock(context, model, scope_id, date, reason=None):
    """Close every period of `model` on or before `date` for one scope (scope_id None: every record), or open them
    all with date=None. Trusted code only (an action, a job, a script); a caller served over HTTP is refused. It
    writes the same lock and audit row as Controls, Periods, with the reason, in the context's transaction. Moving
    a lock earlier or clearing it reopens closed periods, which only an admin may do, as on the page. Returns the
    scope's earlier lock date (None when it had none).

    The 0.4.21 internal form set_lock(connection, scope_key, date, actor) still works, with a DeprecationWarning."""
    if hasattr(context, 'run') and not hasattr(context, 'connection') and not hasattr(context, '_tx'):
        import warnings
        warnings.warn('set_lock(connection, scope, date, actor) is deprecated: call '
                      'evlet.periods.set_lock(context, model, scope_id, date, reason)', DeprecationWarning, stacklevel=2)
        return save_lock(context, model, scope_id, date)
    from .controls import set_period_lock
    return set_period_lock(context, model, scope_id, date, reason)
