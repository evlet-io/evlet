"""Ledger models (Meta.ledger = True) and named transaction locks (Session.lock). See docs/ledger.md.

A ledger model is a lean table that trusted Python writes: an integer `id` the database assigns in
insert order, then the declared columns, and nothing else (no standard name and title, no record
times, no public surface). It computes nothing; rows are stored as given.

    class PointMove(Model):
        member = Link("Member")
        points = Decimal(scale=2, required=True)

        class Meta:
            ledger = True
            append_only = True          # database triggers refuse UPDATE and DELETE
            timestamps = None           # "created", "modified" or "both"
            indexes = [("member",)]     # composite and unique indexes

This module checks the declaration when the class is created, describes the options for the saved
schema, and holds the append-only triggers and the lock keys. Session (evlet/database.py) does the
reads and writes; migrations (evlet/migrations.py, evlet/postgres_schema.py) create the tables.
"""

from datetime import datetime, timezone
import hashlib
import json

# Field types a ledger model may declare, matched exactly (a subclass such as State, LongText, Email or
# Duration is a different type).
FIELD_TYPES = ('Text', 'Integer', 'Decimal', 'Currency', 'Percent', 'Boolean', 'Date', 'DateTime', 'Select', 'Link')
# Meta options a ledger model may set; every other option belongs to records and their screens.
META = frozenset(('ledger', 'append_only', 'journal', 'timestamps', 'indexes', 'period', 'period_scope', 'api', 'measures',
                  'label', 'description'))
# Meta options that only a ledger model may set.
LEDGER_ONLY = ('append_only', 'timestamps', 'indexes')
TIMESTAMPS = (None, 'created', 'modified', 'both')
TIMESTAMP_FIELDS = ('created_at', 'modified_at')
# Field options a ledger field may not use: calculations, rules and options that only screens read.
REFUSED_OPTIONS = {
    'compute': 'computed fields', 'query': 'query fields', 'fetch': 'fetched fields', 'initial': 'initial values',
    'applies_when': 'field rules', 'required_when': 'field rules', 'locked_when': 'field rules',
    'visible_if': 'screen rules', 'required_if': 'screen rules', 'read_only_if': 'screen rules',
    'help_text': 'screen text', 'tooltip': 'screen text', 'placeholder': 'screen text', 'suffix': 'screen text',
}
APPEND_ONLY = 'is append only'
LOCK_PART_LENGTH = 200


def is_ledger(model):
    """Whether a model class (or anything else) is a ledger model."""
    return isinstance(model, type) and getattr(model, '__ledger__', False) is True


def meta_of(name, namespace, bases):
    if 'Meta' in namespace:
        return namespace['Meta']
    return next((base.Meta for base in bases if hasattr(base, 'Meta')), None)


def option(meta, key, default=None):
    return getattr(meta, key, default) if meta is not None else default


def declared(name, namespace, bases):
    """True for a ledger declaration, after checking the ledger-only options of any model."""
    meta = meta_of(name, namespace, bases)
    ledger = option(meta, 'ledger', False)
    if type(ledger) is not bool:
        raise ValueError(f'{name}: Meta.ledger is True or False')
    if not ledger:
        for key in LEDGER_ONLY:
            if meta is not None and hasattr(meta, key):
                raise ValueError(f'{name}: Meta.{key} is for ledger models; set Meta.ledger = True as well')
    return ledger


def timestamp_fields(timestamps):
    """The engine fields Meta.timestamps adds, as {name: DateTime}."""
    from .fields import DateTime
    names = {'created': ('created_at',), 'modified': ('modified_at',), 'both': TIMESTAMP_FIELDS}.get(timestamps, ())
    result = {}
    for key in names:
        field = DateTime(read_only=True)
        field.engine_timestamp = key
        result[key] = field
    return result


def check_class(name, namespace, bases, fields, tables, sources):
    """Refuse what a ledger model cannot have, with a message naming it. Returns the Meta options as
    (append_only, timestamps, indexes)."""
    from . import fields as types
    meta = meta_of(name, namespace, bases)
    unknown = sorted(key for key in vars(meta) if not key.startswith('_') and key not in META) if isinstance(meta, type) else []
    if unknown:
        raise ValueError(f'{name}: a ledger model has no screens or document behaviour, so Meta.{unknown[0]} is not '
                         f'available; it supports Meta.{", Meta.".join(sorted(META))}')
    if 'api' in vars(meta) and meta.api is not None:
        raise ValueError(f'{name}: a ledger model has no public surface; Meta.api is None (or left out)')
    append_only = option(meta, 'append_only', False)
    if type(append_only) is not bool:
        raise ValueError(f'{name}: Meta.append_only is True or False')
    timestamps = option(meta, 'timestamps', None)
    if timestamps not in TIMESTAMPS:
        raise ValueError(f'{name}: Meta.timestamps is None, "created", "modified" or "both"')
    if tables:
        raise ValueError(f'{name}: a ledger model has no child tables; use another ledger model with a Link')
    if sources:
        raise ValueError(f'{name}: a ledger model has no query sources')
    for flag in ('confirmable', 'track_changes'):
        if namespace.get(flag) or any(getattr(base, flag, False) for base in bases):
            raise ValueError(f'{name}: a ledger model has no lifecycle; {flag} is not available')
    for key, value in namespace.items():
        if hasattr(value, '__action__') or hasattr(value, '__lifecycle__') or hasattr(value, '__depends__'):
            raise ValueError(f'{name}.{key}: a ledger model has no actions, moves, lifecycle handlers or reactions')
    if callable(namespace.get('changed')):
        raise ValueError(f'{name}.changed: a ledger model has no change reactions; trusted code writes its rows')
    allowed = tuple(getattr(types, kind) for kind in FIELD_TYPES)
    for key, field in fields.items():
        if getattr(field, 'engine_timestamp', None):
            continue  # Inherited from a ledger base class; replaced below.
        if key in TIMESTAMP_FIELDS:
            raise ValueError(f'{name}.{key} is kept for Meta.timestamps on ledger models; rename the field')
        if type(field) not in allowed:
            raise ValueError(f'{name}.{key}: a ledger model stores {", ".join(FIELD_TYPES)} fields only, not '
                             f'{type(field).__name__}')
        if not field.stored:
            raise ValueError(f'{name}.{key}: a ledger model stores every field; virtual fields are not available')
        for attribute, what in REFUSED_OPTIONS.items():
            value = getattr(field, attribute, None)
            if value not in (None, ''):
                raise ValueError(f'{name}.{key}: a ledger model has no {what} ({attribute}=)')
        if field.hidden or field.read_only or field.editable_when_locked:
            raise ValueError(f'{name}.{key}: hidden, read_only and editable_when_locked are screen options; a ledger '
                             'model has no screens')
        if field.filters:
            raise ValueError(f'{name}.{key}: Link filters choose records in a form; a ledger model has no forms')
        if getattr(field, 'colors', None):
            raise ValueError(f'{name}.{key}: Select colors are a screen option; a ledger model has no screens')
        if field.default is types.CurrentUser:
            raise ValueError(f'{name}.{key}: default=CurrentUser needs a signed-in person; trusted code writes ledger rows')
    indexes = normalize_indexes(name, option(meta, 'indexes', ()), {**fields, **timestamp_fields(timestamps)})
    return append_only, timestamps, indexes


def normalize_indexes(name, value, fields):
    """Meta.indexes as a tuple of (field names, unique). An entry is a tuple of field names, or
    (field names, {"unique": True})."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{name}: Meta.indexes is a list such as [("member", "on_date"), (("member",), {{"unique": True}})]')
    result = []
    for entry in value:
        unique = False
        if isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[0], (list, tuple)) and isinstance(entry[1], dict):
            entry, extra = entry
            if set(extra) - {'unique'} or type(extra.get('unique', False)) is not bool:
                raise ValueError(f'{name}: Meta.indexes options are {{"unique": True or False}}')
            unique = extra.get('unique', False)
        if not isinstance(entry, (list, tuple)) or not entry or any(not isinstance(key, str) for key in entry):
            raise ValueError(f'{name}: each Meta.indexes entry names one or more fields, such as ("member", "on_date")')
        if len(set(entry)) != len(entry):
            raise ValueError(f'{name}: Meta.indexes entry {tuple(entry)} names a field twice')
        for key in entry:
            if key not in fields:
                raise ValueError(f'{name}: Meta.indexes names {key}, which is not a field of the model')
        item = (tuple(entry), unique)
        if any(other[0] == item[0] for other in result):
            raise ValueError(f'{name}: Meta.indexes lists {tuple(entry)} twice')
        result.append(item)
    return tuple(result)


def schema(model):
    """The ledger options the saved schema records, or None for a normal model. Timestamps are fields
    (created_at, modified_at), so they are not repeated here."""
    if not is_ledger(model):
        return None
    return {'append_only': model.__append_only__,
            'indexes': [{'fields': list(fields), 'unique': unique} for fields, unique in model.__indexes__]}


def indexes_of(entry):
    """(fields, unique) pairs from a saved schema's ledger entry."""
    return {(tuple(item['fields']), item['unique']) for item in (entry or {}).get('indexes', ())}


def now():
    """The time a timestamp field takes."""
    return datetime.now(timezone.utc)


# Append-only triggers

def trigger_names(table):
    """SQLite trigger names of an append-only ledger table."""
    return tuple(f'evlet_{table}_no_{event}' for event in ('update', 'delete', 'replace'))


def add_triggers(connection, postgres, table):
    """Refuse UPDATE and DELETE on a ledger table, so also REPLACE and upsert updates, as the
    evlet_audit and evlet_journal tables do. Idempotent."""
    from .models import identifier
    quoted, message = identifier(table), f'{table} {APPEND_ONLY}'
    if postgres:
        connection.execute("CREATE OR REPLACE FUNCTION evlet_ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS "
                           f"$$ BEGIN RAISE EXCEPTION '% {APPEND_ONLY}', TG_TABLE_NAME; END $$")
        present = {row[0] for row in connection.execute(
            'SELECT tgname FROM pg_trigger WHERE tgrelid = ?::regclass AND NOT tgisinternal', (quoted,))}
        if 'evlet_append_only' not in present:
            connection.execute(f'CREATE TRIGGER evlet_append_only BEFORE UPDATE OR DELETE ON {quoted} '
                               'FOR EACH ROW EXECUTE FUNCTION evlet_ledger_append_only()')
        if 'evlet_no_truncate' not in present:
            connection.execute(f'CREATE TRIGGER evlet_no_truncate BEFORE TRUNCATE ON {quoted} '
                               'FOR EACH STATEMENT EXECUTE FUNCTION evlet_ledger_append_only()')
        return
    refuse = f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
    update, delete, replace = (identifier(name) for name in trigger_names(table))
    connection.execute(f'CREATE TRIGGER IF NOT EXISTS {update} BEFORE UPDATE ON {quoted} {refuse}')
    connection.execute(f'CREATE TRIGGER IF NOT EXISTS {delete} BEFORE DELETE ON {quoted} {refuse}')
    # REPLACE deletes the old row without firing DELETE triggers (unless recursive triggers are on, as
    # Evlet's own connections have them), so an insert may not reuse an id.
    connection.execute(f'CREATE TRIGGER IF NOT EXISTS {replace} BEFORE INSERT ON {quoted} '
                       f'WHEN EXISTS (SELECT 1 FROM {quoted} WHERE "id" = NEW."id") {refuse}')


def drop_triggers(connection, postgres, table):
    from .models import identifier
    if postgres:
        connection.execute(f'DROP TRIGGER IF EXISTS evlet_append_only ON {identifier(table)}')
        connection.execute(f'DROP TRIGGER IF EXISTS evlet_no_truncate ON {identifier(table)}')
        return
    for name in trigger_names(table):
        connection.execute(f'DROP TRIGGER IF EXISTS {identifier(name)}')


def has_triggers(connection, postgres, table):
    """True when all the append-only triggers are present, False when none is; SchemaError otherwise."""
    from .migrations import SchemaError
    from .models import identifier
    if postgres:
        present = {row[0] for row in connection.execute(
            'SELECT tgname FROM pg_trigger WHERE tgrelid = ?::regclass AND NOT tgisinternal', (identifier(table),))}
        expected = {'evlet_append_only', 'evlet_no_truncate'}
    else:
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (table,))}
        expected = set(trigger_names(table))
    found = present & expected
    if found and found != expected:
        raise SchemaError(f'{table}: the append-only triggers are incomplete; run explicit migrations')
    return bool(found)


# Transaction locks

class LockTimeout(RuntimeError):
    """tx.lock() waited longer than its timeout: another transaction holds one of the keys. The
    transaction rolls back; try again."""

    def __init__(self, message='Another transaction is busy with the same records; try again'):
        super().__init__(message)


def lock_key(key):
    """The canonical text of a lock key: a nonempty tuple of str and int parts."""
    if not isinstance(key, tuple) or not key:
        raise ValueError('A lock key is a nonempty tuple of text and integer parts, such as ("stock", item_id, warehouse_id)')
    parts = []
    for part in key:
        if isinstance(part, int) and type(part) is not bool:
            part = int(part)  # A record id is an int.
            if not -2**63 <= part <= 2**63 - 1:
                raise ValueError('A lock key integer is a signed 64-bit integer')
        elif type(part) is str:
            if not part or len(part) > LOCK_PART_LENGTH or '\x00' in part:
                raise ValueError(f'A lock key text part has 1 to {LOCK_PART_LENGTH} characters and no NUL')
        else:
            raise ValueError(f'A lock key part is text or an integer, not {type(part).__name__}')
        parts.append(part)
    # JSON keeps 1 and "1" apart, and a part's text cannot run into the next part.
    return json.dumps(parts, ensure_ascii=False, separators=(',', ':'))


def lock_hash(key):
    """The signed 64-bit number PostgreSQL locks for a key. Stable across processes and releases; two keys
    that share a number only make a writer wait, they never let two writers in at once."""
    return int.from_bytes(hashlib.blake2b(lock_key(key).encode('utf-8'), digest_size=8).digest(), 'big', signed=True)


def lock_timeout(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not 0 < value <= 86400:
        raise ValueError('tx.lock timeout is a number of seconds, more than 0 and at most 86400')
    return max(1, int(value * 1000))


# Review

def describe(database):
    """One dict per ledger model of a database: `model`, `table`, `rows`, `append_only`, `journal` ("off",
    "on" or "tamper evident"), `timestamps` and `period` (the field name, with " per <scope>" when scoped).
    Read in one snapshot; `evlet ledger list` prints it."""
    from .models import identifier
    registry = database.registry
    models = [(name, model) for name, model in registry.models.items() if is_ledger(model)]
    journals, periods = getattr(registry, 'journals', {}), getattr(registry, 'periods', {})
    result = []
    with database._connection() as connection:
        connection.execute('BEGIN')
        try:
            for name, model in models:
                journal = journals.get(model)
                period = periods.get(model)
                result.append({
                    'model': model.__name__, 'table': name,
                    'rows': connection.execute(f'SELECT count(*) FROM {identifier(name)}').fetchone()[0],
                    'append_only': model.__append_only__,
                    'journal': 'off' if journal is None else 'tamper evident' if journal.tamper_evident else 'on',
                    'timestamps': model.__timestamps__ or 'off',
                    'period': 'off' if period is None else period.field + (f' per {period.scope}' if period.scope else '')})
        finally:
            connection.rollback()
    return result


def table(rows):
    """The text `evlet ledger list` prints for describe() rows."""
    if not rows:
        return 'No ledger models'
    header = ('Model', 'Table', 'Rows', 'Append only', 'Journal', 'Timestamps', 'Period')
    lines = [header] + [(row['model'], row['table'], str(row['rows']), 'yes' if row['append_only'] else 'no', row['journal'],
                         row['timestamps'], row['period']) for row in rows]
    widths = [max(len(line[index]) for line in lines) for index in range(len(header))]
    return '\n'.join('  '.join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip() for line in lines)
