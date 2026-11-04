"""When and by whom each record was created and last changed (docs/model-engine.md, "Record times").

Every model table has four framework columns, filled on every write in the same statement as the
write itself:

    evlet_created_at   TEXT     UTC time, for example 2026-11-04T08:15:30.125Z
    evlet_created_by   INTEGER  the user id, or NULL for the engine (jobs, schedules, trusted Python)
    evlet_modified_at  TEXT
    evlet_modified_by  INTEGER

They are not fields: they never appear in a model's fields, forms, imports or field lists, and no
client can write them. A Session carries the acting user in `session.actor`; a document Context and
the record routes set it, and raw trusted Python leaves it None.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import itertools

from .identity import EPOCH_MS, RANDOM_BITS

COLUMNS = ('evlet_created_at', 'evlet_created_by', 'evlet_modified_at', 'evlet_modified_by')
MODIFIED = COLUMNS[2:]
# Journal kinds that change the record itself; notes, messages, files and effects do not.
CHANGES = ('created', 'changed', 'moved', 'voided', 'revised', 'unposted', 'reposted', 'number', 'recomputed')
ENGINE = 'engine'


_clock = None


def now():
    moment = datetime.now(timezone.utc) if _clock is None else next(_clock)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


@contextmanager
def fixed_times(start=datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc), step=timedelta(minutes=1)):
    """Stamp writes from a clock that starts at `start` and moves `step` per stamp, so recorded fixtures
    stay the same between runs (see identity.sequential_ids). Process-wide; tests and fixture scripts only."""
    global _clock
    previous, _clock = _clock, (start + step * index for index in itertools.count())
    try:
        yield
    finally:
        _clock = previous


def actor_id(user):
    """The user id a write is recorded under, or None for the engine."""
    value = getattr(user, 'id', None)
    return int(value) if type(value) is int and value > 0 else None


def created(actor):
    """Column names and values of a new row."""
    at = now()
    return list(COLUMNS), [at, actor, at, actor]


def modified(actor):
    return list(MODIFIED), [now(), actor]


def touch(connection, table, record_id, actor):
    """Mark a record changed by a write that sets its columns directly (void, unpost)."""
    connection.execute(f'UPDATE {table} SET "evlet_modified_at" = ?, "evlet_modified_by" = ? WHERE "id" = ?',
                       (now(), actor, record_id))


def id_time(record_id):
    """The creation time an id carries, or None for an id made before time-ordered ids."""
    millis = int(record_id) >> RANDOM_BITS
    if millis <= 0:
        return None
    return datetime.fromtimestamp((EPOCH_MS + millis) / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _present(connection, postgres, table):
    from .models import identifier
    if postgres:
        return {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?", (table,))}
    return {row[1] for row in connection.execute(f'PRAGMA table_info({identifier(table)})')}


def column_sql(postgres):
    """The column definitions for CREATE TABLE."""
    number = 'BIGINT' if postgres else 'INTEGER'
    return [f'"{name}" {"TEXT" if name.endswith("_at") else number}' for name in COLUMNS]


def add_columns(connection, postgres, table):
    """Give an existing table the four columns and fill them, once. Idempotent; returns whether it added them."""
    from .models import identifier
    present = _present(connection, postgres, table)
    if set(COLUMNS) <= present:
        return False
    for name, sql in zip(COLUMNS, column_sql(postgres)):
        if name not in present:
            connection.execute(f'ALTER TABLE {identifier(table)} ADD COLUMN {sql}')
    backfill(connection, postgres, table)
    return True


def _journal(connection, postgres):
    if postgres:
        return connection.execute("SELECT to_regclass('evlet_journal') IS NOT NULL").fetchone()[0]
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evlet_journal'").fetchone() is not None


def backfill(connection, postgres, table):
    """Fill rows that have no times: from the record's Journal where it has one (the first and the last
    entry that changed the record, with their actors), otherwise from the time in the id with no actor.
    Rows whose id holds no time (made before time-ordered ids) stay empty."""
    from .models import identifier
    quoted = identifier(table)
    found = {}
    if _journal(connection, postgres):
        kinds = ', '.join('?' for _ in CHANGES)
        for row in connection.execute(f'SELECT record_id, actor, at FROM evlet_journal WHERE model=? AND kind IN ({kinds}) ORDER BY id',
                                      (table, *CHANGES)):
            actor = None if row[1] == ENGINE else int(row[1])
            first = found.get(row[0])
            found[row[0]] = (first[0], first[1], row[2], actor) if first else (row[2], actor, row[2], actor)
    last = 0
    while True:
        ids = [row[0] for row in connection.execute(
            f'SELECT "id" FROM {quoted} WHERE "evlet_created_at" IS NULL AND "id" > ? ORDER BY "id" LIMIT 500', (last,))]
        if not ids:
            return
        last = ids[-1]
        updates = []
        for record_id in ids:
            values = found.get(record_id)
            if values is None:
                at = id_time(record_id)
                values = None if at is None else (at, None, at, None)
            if values is not None:
                updates.append((*values, record_id))
        if updates:
            connection.executemany(f'UPDATE {quoted} SET "evlet_created_at" = ?, "evlet_created_by" = ?, '
                                   '"evlet_modified_at" = ?, "evlet_modified_by" = ? WHERE "id" = ?', updates)


def detail(business, tx, user, record):
    """`_audit` of a record detail: the times, and who as {"id", "name"} for viewers who may see other
    users (desk roles). Portal roles get the times only. Engine writes have no who."""
    stored = getattr(record, '_audit', None)
    if stored is None:
        return None
    created_at, created_by, modified_at, modified_by = stored
    result = {'created_at': created_at, 'created_by': None, 'modified_at': modified_at, 'modified_by': None}
    role = business.controls.roles.get(user.role) or {} if getattr(business, 'controls', None) is not None else {}
    if role.get('realm', 'desk') != 'desk':
        return result
    ids = sorted({value for value in (created_by, modified_by) if value is not None})
    if not ids:
        return result
    names = {row[0]: row[1] or row[2] for row in tx.connection.execute(
        'SELECT u.id, COALESCE(NULLIF(p.short_name, \'\'), NULLIF(p.full_name, \'\')), u.username FROM evlet_users u '
        f'LEFT JOIN evlet_user_profile p ON p.user_id = u.id WHERE u.id IN ({", ".join("?" for _ in ids)})', ids)}
    for key, value in (('created_by', created_by), ('modified_by', modified_by)):
        if value in names:
            result[key] = {'id': value, 'name': names[value]}
    return result
