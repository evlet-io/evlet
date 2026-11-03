"""Explicit, atomic SQLite schema changes with validated data and history."""

from dataclasses import dataclass
import copy
import hashlib
import json
import logging
import re
import sqlite3

from .fields import Field, Integer, Link, MISSING, Reference
from .models import identifier, storage_name

logger = logging.getLogger(__name__)


class SchemaError(ValueError):
    pass


def link_target(field):
    """The storage name a Link points at; a migration may name its target table as a string."""
    return field.target if isinstance(field.target, str) else storage_name(field.target)


def _column(name, field):
    """Column definitions for one field; most fields have one, a Reference has two."""
    result = []
    for column, sql_type in field.columns(name):
        sql = f'{identifier(column)} {sql_type}'
        if field.required:
            sql += ' NOT NULL'
        if isinstance(field, Link):
            sql += f' REFERENCES {identifier(link_target(field))}("id")'
        result.append(sql)
    return result


def index_columns(name, field):
    """The columns an index on a field covers: one, or both columns of a Reference."""
    return tuple(column for column, _ in field.columns(name))


def stored_columns(columns, name):
    """The columns of a field as stored, from a table's column names: a Reference has <name>_model and <name>_id."""
    if name not in columns and {name + '_model', name + '_id'} <= set(columns):
        return (name + '_model', name + '_id')
    return (name,)


def _index(connection, table, field, *, unique=False, columns=None):
    base = f'evlet_{table}_{field}_{"unique" if unique else "index"}'
    name, suffix = base, 1
    while connection.execute('SELECT 1 FROM sqlite_master WHERE name = ?', (name,)).fetchone():
        suffix += 1
        name = f'{base}_{suffix}'
    covered = ', '.join(identifier(column) for column in (columns or (field,)))
    connection.execute(f'CREATE {"UNIQUE " if unique else ""}INDEX {identifier(name)} ON {identifier(table)} ({covered})')


def _sequence_index(connection, table, name, field):
    """The unique index of a Sequence: its scope fields and the number, for assigned numbers only."""
    from .sequences import index_sql
    base, suffix = f'evlet_{table}_{name}_sequence', 1
    index = base
    while connection.execute('SELECT 1 FROM sqlite_master WHERE name = ?', (index,)).fetchone():
        suffix += 1
        index = f'{base}_{suffix}'
    connection.execute(index_sql(index, table, name, field))


def _create(connection, table, model):
    columns = ['"id" INTEGER PRIMARY KEY AUTOINCREMENT']
    columns.extend(sql for name, field in model.__fields__.items() for sql in _column(name, field))
    connection.execute(f'CREATE TABLE {identifier(table)} ({", ".join(columns)})')
    for name, field in model.__fields__.items():
        if field.index or field.unique:
            _index(connection, table, name, unique=field.unique, columns=index_columns(name, field))
        if field.kind == 'sequence':
            _sequence_index(connection, table, name, field)


def _metadata(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS evlet_schema (id INTEGER PRIMARY KEY CHECK(id = 1), definition TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS evlet_migrations (position INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, checksum TEXT NOT NULL)')


def _definition(registry):
    return json.dumps(_normalized(registry.schema()), sort_keys=True, separators=(',', ':'))


def _comparable(schema):
    """A schema as migrations compare it: sorted, without the Python class names (`label`), which store nothing."""
    schema = _normalized(schema)
    for model in schema['models']:
        model.pop('label', None)
    return schema


def _same(saved, registry):
    """Whether a saved definition (JSON text) describes the same stored schema as the models."""
    return saved == _definition(registry) or _comparable(json.loads(saved)) == _comparable(registry.schema())


def _tables(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB 'evlet_*'")}


def app_fields(model):
    """Stored fields from the app files. Instance fields (x_) are added and removed by Controls, so
    schema checks and app migrations leave their columns alone."""
    return model.__app_stored__


def instance_columns(connection, postgres=False):
    """{table: {column}} of the instance fields Controls added, from evlet_custom_fields."""
    exists = "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='evlet_custom_fields'" if postgres \
        else "SELECT 1 FROM sqlite_master WHERE name='evlet_custom_fields'"
    if connection.execute(exists).fetchone() is None:
        return {}
    result = {}
    for row in connection.execute('SELECT model, name FROM evlet_custom_fields'):
        result.setdefault(row[0], set()).add(row[1])
    return result


def _check(connection, registry, *, rows=False):
    added = instance_columns(connection)
    for name, model in registry.models.items():
        table = identifier(name)
        skip = added.get(name, set())
        columns = connection.execute(f'PRAGMA table_info({table})').fetchall()
        actual = {row['name']: (row['type'].upper(), bool(row['notnull']), row['pk']) for row in columns if row['name'] not in skip}
        expected = {'id': ('INTEGER', False, 1), **{
            column: (sql_type, field.required, 0) for key, field in app_fields(model).items() for column, sql_type in field.columns(key)}}
        if actual != expected:
            raise SchemaError(f'{name}: database columns differ from models; run explicit migrations')
        links = {(row['from'], row['table'], row['to'], row['on_delete'])
                 for row in connection.execute(f'PRAGMA foreign_key_list({table})') if row['from'] not in skip}
        expected_links = {(key, storage_name(field.target), 'id', 'NO ACTION')
                          for key, field in app_fields(model).items() if isinstance(field, Link)}
        if links != expected_links:
            raise SchemaError(f'{name}: foreign keys differ from models')
        unique, indexed, partial = set(), set(), set()
        for index in connection.execute(f'PRAGMA index_list({table})').fetchall():
            fields = tuple(row['name'] for row in connection.execute('SELECT * FROM pragma_index_info(?)', (index['name'],)))
            if index['partial']:
                if index['unique']:
                    partial.add(fields)
                continue
            indexed.add(fields)
            if index['unique']:
                unique.add(fields)
        if unique != {(key,) for key, field in model.__fields__.items() if field.unique}:
            raise SchemaError(f'{name}: unique constraints differ from models')
        if any(index_columns(key, field) not in indexed for key, field in model.__fields__.items() if field.index):
            raise SchemaError(f'{name}: an explicit index migration is required')
        if any((*field.scope, key) not in partial for key, field in model.__fields__.items() if field.kind == 'sequence'):
            raise SchemaError(f'{name}: a Sequence field is missing its unique index')
        if rows:
            for row in connection.execute(f'SELECT * FROM {table}'):
                try:
                    model._from_row(row)
                except (ValueError, TypeError, OverflowError) as exc:
                    raise SchemaError(f'{name} #{row["id"]}: stored data fails validation: {exc}') from exc
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise SchemaError('Stored data contains a broken foreign key')


def _save_definition(connection, registry):
    connection.execute('INSERT OR REPLACE INTO evlet_schema (id, definition) VALUES (1, ?)', (_definition(registry),))


def _migration_plan(migrations):
    migrations = tuple(migrations)
    if len({item.name for item in migrations}) != len(migrations):
        raise SchemaError('Migration names must be unique')
    return migrations, [item.checksum() for item in migrations]


def _history(connection, migrations, checksums):
    history = connection.execute('SELECT name, checksum FROM evlet_migrations ORDER BY position').fetchall()
    if len(history) > len(migrations) or any(
        row['name'] != migrations[i].name or row['checksum'] != checksums[i]
        for i, row in enumerate(history)
    ):
        raise SchemaError('Applied migration history was changed, removed, or reordered')
    return history


def initialize(database):
    plan = None if database.migrations is None else _migration_plan(database.migrations)
    database.path.parent.mkdir(parents=True, exist_ok=True)
    with database._connection() as connection:
        connection.execute('PRAGMA journal_mode = WAL')
        connection.execute('BEGIN IMMEDIATE')
        try:
            tables = _tables(connection)
            if any(getattr(model, 'confirmable', False) for model in database.registry.models.values()):
                from .lifecycle import ensure_tables
                ensure_tables(connection)
            _metadata(connection)
            saved = connection.execute('SELECT definition FROM evlet_schema WHERE id = 1').fetchone()
            if saved and not _same(saved[0], database.registry):
                raise SchemaError('Model definitions changed; run explicit migrations before startup')
            baseline = False
            if plan is not None:
                migrations, checksums = plan
                history = _history(connection, migrations, checksums)
                if history and len(history) != len(migrations):
                    raise SchemaError('Unapplied migration history; run explicit migrations before startup')
                baseline = not history
            if not tables:
                for name, model in database.registry.models.items():
                    _create(connection, name, model)
            _check(connection, database.registry, rows=not saved or baseline)
            if baseline:
                connection.executemany(
                    'INSERT INTO evlet_migrations (position, name, checksum) VALUES (?, ?, ?)',
                    [(i + 1, migration.name, checksums[i]) for i, migration in enumerate(migrations)],
                )
            _save_definition(connection, database.registry)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


@dataclass(frozen=True)
class AddField:
    table: str
    name: str
    field: Field
    backfill: object = MISSING

    def value(self):
        value = self.backfill if self.backfill is not MISSING else self.field.default
        if callable(value):
            raise SchemaError('Migration backfills must be fixed values')
        try:
            cleaned = self.field.clean(value)
        except ValueError as exc:
            raise SchemaError(str(exc)) from exc
        if isinstance(cleaned, str) and '\x00' in cleaned:
            raise SchemaError('Migration backfills cannot contain NUL characters')
        if self.field.kind == 'sequence' and cleaned is not None:
            raise SchemaError('Add a Sequence field without a backfill; number existing rows with sequences.backfill()')
        return cleaned

    def signature(self):
        identifier(self.table)
        identifier(self.name)
        return {'operation': 'add_field', 'table': self.table, 'name': self.name,
                'field': self.field.metadata(), 'backfill': self.field.serialize(self.value())}

    def apply(self, connection):
        values = self.field.to_columns(self.value())
        if isinstance(self.field, (Link, Reference)) and any(value is not None for value in values):
            raise SchemaError('Add nullable Link and Reference fields with a null backfill; required backfills are not supported')
        for sql, value in zip(_column(self.name, self.field), values):
            literal = connection.execute('SELECT quote(?)', (value,)).fetchone()[0]
            connection.execute(f'ALTER TABLE {identifier(self.table)} ADD COLUMN {sql} DEFAULT {literal}')
        if self.field.unique or self.field.index:
            _index(connection, self.table, self.name, unique=self.field.unique, columns=index_columns(self.name, self.field))
        if self.field.kind == 'sequence':
            _sequence_index(connection, self.table, self.name, self.field)


@dataclass(frozen=True)
class RenameField:
    table: str
    old_name: str
    new_name: str

    def signature(self):
        for name in (self.table, self.old_name, self.new_name):
            identifier(name)
        if self.old_name == 'id' or self.new_name == 'id':
            raise SchemaError('Record IDs cannot be renamed')
        return {'operation': 'rename_field', 'table': self.table, 'old_name': self.old_name, 'new_name': self.new_name}

    def apply(self, connection, columns=None):
        """columns: the table's column names (read from SQLite when not given)."""
        if columns is None:
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info({identifier(self.table)})')}
        # A Reference occupies <name>_model and <name>_id; rename both.
        pairs = [(self.old_name + suffix, self.new_name + suffix) for suffix in ('_model', '_id')] \
            if self.old_name not in columns and {self.old_name + '_model', self.old_name + '_id'} <= columns else [(self.old_name, self.new_name)]
        for old, new in pairs:
            connection.execute(f'ALTER TABLE {identifier(self.table)} RENAME COLUMN {identifier(old)} TO {identifier(new)}')


@dataclass(frozen=True)
class AddIndex:
    table: str
    field: str
    unique: bool = False

    def signature(self):
        identifier(self.table)
        identifier(self.field)
        return {'operation': 'add_index', 'table': self.table, 'field': self.field, 'unique': self.unique}

    def apply(self, connection, columns=None):
        """columns: the table's column names (read from SQLite when not given); a Reference indexes both of its columns."""
        if columns is None:
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info({identifier(self.table)})')}
        _index(connection, self.table, self.field, unique=self.unique, columns=stored_columns(columns, self.field))


@dataclass(frozen=True)
class CreateModel:
    model: type

    def signature(self):
        return {'operation': 'create_model', 'name': storage_name(self.model),
                'fields': [field.metadata() for field in self.model.__fields__.values()]}

    def apply(self, connection):
        _create(connection, storage_name(self.model), self.model)


@dataclass(frozen=True)
class AddTable:
    """Add an owned table (`Table(ChildClass)`) to an existing model.

    `parent` is the owning model's storage name and `name` the Table attribute; `child` is a frozen
    copy of the Child class. The engine adds the hidden owner Link and row position, as a registry does,
    so the table matches `Table(child, storage_name=..., owner_field=...)` on the live model."""
    parent: str
    name: str
    child: type
    storage_name: str = None
    owner_field: str = 'evlet_owner'

    def table(self):
        return self.storage_name or f'{self.parent}_{self.name}'

    def model(self):
        from .models import Child
        for name in (self.parent, self.name, self.table(), self.owner_field):
            identifier(name)
        if not isinstance(self.child, type) or not issubclass(self.child, Child) or self.child is Child:
            raise SchemaError('AddTable needs a Child subclass')
        if self.child.__tables__:
            raise SchemaError(f'{self.table()}: add a nested table with its own AddTable')
        if self.owner_field in self.child.__declared_fields__ or 'evlet_position' in self.child.__declared_fields__:
            raise SchemaError('Child owner and order fields are framework-managed')
        owner = type(self.parent, (), {'storage_name': self.parent})
        return type(self.child.__name__, (self.child,), {
            '__module__': self.child.__module__, 'storage_name': self.table(),
            self.owner_field: Link(owner, required=True, index=True, hidden=True, read_only=True),
            'evlet_position': Integer(required=True, default=0, hidden=True, read_only=True),
            '__internal_fields__': (self.owner_field, 'evlet_position'),
        })

    def signature(self):
        return {'operation': 'add_table', 'parent': self.parent, 'name': self.name, 'table': self.table(),
                'fields': [{**field.metadata(), 'name': key} for key, field in self.model().__app_stored__.items()]}

    def apply(self, connection):
        _create(connection, self.table(), self.model())


@dataclass(frozen=True)
class Recompute:
    """Recalculate stored calculated fields of existing rows with the current formulas.

    It runs inside the migration transaction after the batch's structural changes, so the columns
    the formulas read exist. Results are rounded like any save; any failure rolls back the batch.
    """
    table: str
    fields: tuple

    def __post_init__(self):
        if isinstance(self.fields, str) or not isinstance(self.fields, (tuple, list)) or not self.fields \
                or any(not isinstance(name, str) for name in self.fields) or len(set(self.fields)) != len(self.fields):
            raise ValueError('Recompute needs a model name and distinct field names')
        object.__setattr__(self, 'fields', tuple(self.fields))

    def signature(self):
        identifier(self.table)
        for name in self.fields:
            identifier(name)
        return {'operation': 'recompute', 'table': self.table, 'fields': list(self.fields)}

    def apply(self, connection):
        """No structural change; migrate() recalculates after the batch's other operations."""


class Applied(list):
    """The names of the migrations a migrate() call applied; `skipped` counts the records Recompute kept, and
    `posted` the posted records it changed when the run included them (`recompute_posted=True`)."""
    skipped = 0
    posted = 0


def _recompute(connection, registry, operations, versions, *, postgres=False, include_posted=False):
    """Run Recompute operations in order. versions(connection) tells whether formula versions are recorded.

    A record with posted effects (voided or not) keeps its saved results, so it is skipped, unless an admin runs the
    migration with `include_posted`: then posted records are recalculated too, and each one that changes gets
    `"posted": true` in its entry. Records whose results change get a `recomputed` Journal entry. Returns
    (records skipped, posted records changed)."""
    from .database import Session
    from .calc import CalcError
    from .journal import recomputed
    session = Session(connection, registry)
    covered, skipped, changes = {}, set(), {}
    posted = set() if include_posted else None
    for operation in operations:
        model = registry.models.get(operation.table)
        if model is None:
            raise SchemaError(f'Unknown migration table: {operation.table}')
        for name in operation.fields:
            field = model.__declared_fields__.get(name)
            if field is None or field.compute is None or not field.stored:
                raise SchemaError(f'{operation.table}.{name}: Recompute needs a stored calculated field')
        plan = registry.plan_for(model)
        try:
            if plan is None:
                _recompute_legacy(session, model, operation, skipped, changes, posted)
            else:
                path = _recompute_finite(session, registry, plan, model, operation, skipped, changes, posted)
                covered.setdefault(registry.root_of(model), set()).update((path, name) for name in operation.fields)
        except (CalcError, ArithmeticError) as exc:
            raise SchemaError(f'Recompute {operation.table} failed: {exc}') from exc
    for root, done in covered.items():
        plan = registry.plans[root]
        stored = {(tuple(field['path']), field['field']) for field in plan['fields'] if field.get('kind') is None and field['stored']}
        if stored <= done and versions(connection):
            # Every stored result was recalculated, so the documents now match the active formulas; kept ones do not.
            kept = {record_id for model, record_id in skipped if model is root}
            table = identifier(registry.name_for(root))
            ids = [record_id for (record_id,) in connection.execute(f'SELECT "id" FROM {table} ORDER BY "id"').fetchall()
                   if record_id not in kept]
            for start in range(0, len(ids), 500):
                part = ids[start:start + 500]
                connection.execute(f'UPDATE evlet_calculation_versions SET plan_hash=? WHERE resource=? AND record_id IN ({", ".join("?" for _ in part)})',
                                   (registry.formula_hashes[root], registry.name_for(root), *part))
    changed_posted = sorted((key for key in changes if key in (posted or ())), key=lambda key: (registry.name_for(key[0]), key[1]))
    for key in changed_posted:
        changes[key]['posted'] = True
    recomputed(connection, registry, changes, postgres=postgres)
    if skipped:
        logger.info('Recompute kept the saved results of %d posted records', len(skipped))
    if changed_posted:
        from .controls import audit, ensure_schema
        ensure_schema(connection)
        names = ', '.join(sorted({registry.name_for(model) for model, _ in changed_posted}))
        count = len(changed_posted)
        audit(connection, 'cli', 'Records', names, f'Recompute changed {count} posted record{"s" if count != 1 else ""}, as asked')
        logger.warning('Recompute changed the saved results of %d posted records', count)
    return len(skipped), len(changed_posted)


def _skip(session, model, record, skipped, posted):
    """Whether Recompute leaves this root record alone. With `posted` (a set, when the run includes posted
    records), posted records are recalculated and collected there instead."""
    key = (model, record.id)
    if getattr(model, 'confirmable', False):
        # Revision snapshots are permanent; migrate draft calculations explicitly after reopening.
        row = session.connection.execute('SELECT revision FROM evlet_document_lifecycle WHERE model=? AND record_id=?',
                                         (session.registry.name_for(model), record.id)).fetchone()
        if row is not None and row[0]:
            skipped.add(key)
            return True
    if key in skipped:
        return True
    if posted is not None and key in posted:
        return False
    if _kept(session, model, record):
        # A void record keeps its results even on request: its rows and their reversal must keep matching it.
        if posted is None or model.__state__ is not None and record._values.get(model.__state__) == 'void':
            skipped.add(key)
            return True
        posted.add(key)
    return False


def _kept(session, model, record):
    """Whether Recompute leaves a root record alone: it has posted effects, voided or not."""
    from .effects import has_postings
    return has_postings(session.connection, session.registry, model, record, voided=True)


def _changed(changes, item, record, values):
    """Record old and new values in a `changed`-shaped entry; `item` is the root key, then (table, id) steps."""
    key, steps = item
    entry = changes.setdefault(key, {})
    for table, row_id in steps:
        updated = entry.setdefault('rows', {}).setdefault(table, {}).setdefault('updated', [])
        entry = next((row for row in updated if row['id'] == row_id), None) or updated.append({'id': row_id}) or updated[-1]
    fields = type(record).__declared_fields__
    entry.setdefault('fields', {}).update({name: {'old': fields[name].serialize(record._values.get(name)), 'new': fields[name].serialize(value)}
                                           for name, value in values.items()})


def _rows(session, model, record_id, child):
    # Every row in position order; owned tables may hold more rows than one list page.
    from .documents import owned_rows
    return owned_rows(session, child, [record_id]).get(record_id, ([], 0))[0]


def _recompute_finite(session, registry, plan, model, operation, skipped, changes, posted=None):
    from datetime import datetime, timezone
    from .calc import Document, Num, is_finite, to_decimal
    from .documents import fill_lookups, MESSAGES
    from .fields import Integer
    root = registry.root_of(model)
    owned = {child.model: child for child in registry.children}
    path, item = [], model
    while item in owned:
        path.insert(0, owned[item].name)
        item = owned[item].parent
    path = tuple(path)

    def data(record):
        definition = type(record)
        saved[definition, record.id] = record
        values = {name: record._values.get(name) for name, field in definition.__declared_fields__.items()
                  if field.stored and not is_finite(field) and name not in getattr(definition, '__internal_fields__', ())}
        values['id'], values['_key'] = record.id, str(record.id)
        children = {child.name: [data(row) for row in _rows(session, definition, record.id, child)]
                    for child in registry.children if child.parent is definition}
        if children:
            values['children'] = children
        return values
    today = datetime.now(timezone.utc).date().isoformat()
    table = identifier(registry.name_for(root))
    for (record_id,) in session.connection.execute(f'SELECT "id" FROM {table} ORDER BY "id"').fetchall():
        record, saved = session.get(root, record_id), {}
        if _skip(session, root, record, skipped, posted):
            continue
        document = Document(plan, data(record), checked=True)
        if any(read[0] == 'today' for field in plan['fields'] for read in field['reads']):
            document.set_today(today)
        fill_lookups(registry, session, document)
        for row in list(document.rows.values()):
            if row.path != path:
                continue
            values = {}
            for name in operation.fields:
                result, field = document.results[row.handle, name], model.__declared_fields__[name]
                if result.state != 'ready':
                    raise SchemaError(f'{operation.table} #{row.id}.{name}: {result.code}: {MESSAGES.get(result.code, "The calculation failed")}')
                value = result.value
                if isinstance(value, Num):
                    value = int(to_decimal(value)) if isinstance(field, Integer) else to_decimal(value)
                try:
                    values[name] = None if value is None else field.validate(value)
                except ValueError as exc:
                    raise SchemaError(f'{operation.table} #{row.id}.{name}: {exc}') from exc
            # The results above read null for fields that do not apply; store them that way too.
            values.update({name: None for name, state in document.rule_states(row.handle).items() if not state['applies']})
            old = saved[model, row.id]
            values = {name: value for name, value in values.items() if old._values.get(name) != value}
            if values:
                _write(session, model, row.id, values)
                steps, parent = [], row
                while parent.parent is not None:
                    steps.insert(0, (parent.path[-1], parent.id))
                    parent = parent.parent
                _changed(changes, ((root, record_id), steps), old, values)
    return path


def _recompute_legacy(session, model, operation, skipped, changes, posted=None):
    from .documents import Draft
    from .reactivity import evaluate, graph, round_field
    if model.__tables__ or model.__sources__ or any(field.query is not None for field in model.__declared_fields__.values()) \
            or any(child.model is model for child in session.registry.children):
        raise SchemaError(f'{operation.table}: Recompute supports string formulas only on models without tables or queries')
    nodes, order = graph(session.registry, model)
    table = identifier(session.registry.name_for(model))
    for (record_id,) in session.connection.execute(f'SELECT "id" FROM {table} ORDER BY "id"').fetchall():
        record = session.get(model, record_id)
        if _skip(session, model, record, skipped, posted):
            continue
        draft = Draft(model, dict(record._values), record_id=record_id)
        for key in order:
            field = model.__declared_fields__.get(key)
            if field is not None and field.compute is not None:
                try:
                    value = round_field(field, evaluate(nodes[key]['compute'], draft.read))
                    draft._values[key] = None if value is None else field.validate(value)
                except (ValueError, ArithmeticError, TypeError) as exc:
                    raise SchemaError(f'{operation.table} #{record_id}.{key}: {exc}') from exc
        values = {name: draft._values[name] for name in operation.fields if draft._values[name] != record._values.get(name)}
        if values:
            _write(session, model, record_id, values)
            _changed(changes, ((model, record_id), []), record, values)


def _write(session, model, record_id, values):
    from .database import storage_columns
    names, stored = storage_columns(model, values, values)
    assignments = ', '.join(f'{identifier(name)} = ?' for name in names)
    session.connection.execute(f'UPDATE {identifier(session.registry.name_for(model))} SET {assignments} WHERE "id" = ?', [*stored, record_id])


OPERATIONS = (AddField, RenameField, AddIndex, CreateModel, AddTable, Recompute)


class Migration:
    def __init__(self, name, *operations):
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_]+', name) or not operations:
            raise ValueError('A migration needs a name and at least one operation')
        if any(not isinstance(op, OPERATIONS) for op in operations):
            raise ValueError('Unsupported migration operation')
        self.name, self.operations = name, operations

    def checksum(self):
        value = json.dumps([op.signature() for op in self.operations], sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(value.encode()).hexdigest()


def _planned_definition(saved, pending):
    schema = copy.deepcopy(json.loads(saved))
    models = {model['name']: model for model in schema['models']}
    for migration in pending:
        for operation in migration.operations:
            if isinstance(operation, (CreateModel, AddTable)):
                model = operation.model() if isinstance(operation, AddTable) else operation.model
                name = storage_name(model)
                if name in models:
                    raise SchemaError(f'{name} already exists in the schema')
                if isinstance(operation, AddTable) and operation.parent not in models:
                    raise SchemaError(f'AddTable {name}: unknown parent model {operation.parent}')
                models[name] = {'name': name, 'label': model.__name__,
                                'fields': [field.metadata() for field in model.__fields__.values()]}
                continue
            if operation.table not in models:
                raise SchemaError(f'Unknown migration table: {operation.table}')
            fields = {field['name']: field for field in models[operation.table]['fields']}
            if isinstance(operation, AddField):
                if operation.name in fields or operation.name == 'id':
                    raise SchemaError('Added field already exists or is reserved')
                fields[operation.name] = {**operation.field.metadata(), 'name': operation.name}
            elif isinstance(operation, RenameField):
                if operation.old_name not in fields or operation.new_name in fields:
                    raise SchemaError('Rename source is missing or destination exists')
                fields = {operation.new_name if key == operation.old_name else key:
                          {**field, 'name': operation.new_name} if key == operation.old_name else field
                          for key, field in fields.items()}
                # A Sequence's scope names fields; its counters key the scope by values, not names.
                for field in fields.values():
                    if 'scope' in field:
                        field['scope'] = [operation.new_name if name == operation.old_name else name for name in field['scope']]
            elif isinstance(operation, AddIndex):
                if operation.field not in fields:
                    raise SchemaError('Index field does not exist')
                fields[operation.field]['unique' if operation.unique else 'index'] = True
            elif isinstance(operation, Recompute):
                if set(operation.fields) - set(fields):
                    raise SchemaError('Recompute names a field that does not exist')
            models[operation.table]['fields'] = list(fields.values())
    schema['models'] = list(models.values())
    return schema


def differences(planned, current):
    """Field-by-field differences between the schema the migrations produce and the current models.

    Each item is (model, field or None, text); `text` names what differs, for errors and `--plan`."""
    planned, current = _comparable(planned), _comparable(current)
    before = {model['name']: {field['name']: field for field in model['fields']} for model in planned['models']}
    after = {model['name']: {field['name']: field for field in model['fields']} for model in current['models']}
    result = []
    for name in sorted(before.keys() | after.keys()):
        if name not in before:
            result.append((name, None, f'{name}: model is in the models but not in the migrations'))
            continue
        if name not in after:
            result.append((name, None, f'{name}: model is in the migrations but not in the models'))
            continue
        old, new = before[name], after[name]
        for field in sorted(old.keys() | new.keys()):
            if field not in old:
                result.append((name, field, f'{name}.{field}: field is in the models but not in the migrations'))
            elif field not in new:
                result.append((name, field, f'{name}.{field}: field is in the migrations but not in the models'))
            else:
                for key in sorted(old[field].keys() | new[field].keys()):
                    if old[field].get(key) != new[field].get(key):
                        result.append((name, field, f'{name}.{field}: {key} is {old[field].get(key)!r} after the migrations '
                                                    f'and {new[field].get(key)!r} in the models'))
    return result


def mismatch(planned, current):
    """The error for migrations that do not produce the current models, listing each difference."""
    lines = [text for _, _, text in differences(planned, current)]
    return SchemaError('Migrations do not match model changes:\n  ' + '\n  '.join(lines)
                       + '\nRun `evlet migrate <instance> --plan` (or database.plan(migrations)) for the operations to add')


def recomputes(migrations):
    return [operation for migration in migrations for operation in migration.operations if isinstance(operation, Recompute)]


def _normalized(schema):
    schema = copy.deepcopy(schema)
    schema['models'].sort(key=lambda model: model['name'])
    for model in schema['models']:
        model['fields'].sort(key=lambda field: field['name'])
    return schema


def migrate(database, migrations, *, recompute_posted=False):
    """Apply pending migrations in one transaction. `recompute_posted=True`, an admin's explicit choice (the
    `--recompute-posted` flag of `evlet migrate`), lets Recompute recalculate posted records too."""
    migrations, checksums = _migration_plan(migrations)
    database.path.parent.mkdir(parents=True, exist_ok=True)
    with database._connection() as connection:
        connection.execute('PRAGMA journal_mode = WAL')
        connection.execute('BEGIN IMMEDIATE')
        try:
            fresh = not _tables(connection)
            _metadata(connection)
            history = _history(connection, migrations, checksums)
            saved = connection.execute('SELECT definition FROM evlet_schema WHERE id = 1').fetchone()
            if not fresh and not saved:
                raise SchemaError('Initialize the legacy database with its original models before migrating')
            pending = migrations[len(history):]
            if saved and (history or not _same(saved[0], database.registry)):
                planned = _planned_definition(saved[0], pending)
                if _comparable(planned) != _comparable(database.registry.schema()):
                    raise mismatch(planned, database.registry.schema())
            # A new database initialized directly from current models already has
            # the target schema. Explicit migration adopts that verified baseline.
            baseline = not history and saved and _same(saved[0], database.registry)
            if fresh:
                for name, model in database.registry.models.items():
                    _create(connection, name, model)
            pending = migrations[len(history):]
            if not pending and saved and not _same(saved[0], database.registry):
                raise SchemaError('Changed model definitions require a new migration')
            for position, migration in enumerate(pending, len(history) + 1):
                if not fresh and not baseline:
                    for operation in migration.operations:
                        operation.apply(connection)
                connection.execute('INSERT INTO evlet_migrations (position, name, checksum) VALUES (?, ?, ?)',
                                   (position, migration.name, checksums[position - 1]))
            if any(getattr(model, 'confirmable', False) for model in database.registry.models.values()):
                from .lifecycle import ensure_tables
                ensure_tables(connection)
            applied = Applied(item.name for item in pending)
            if not fresh and not baseline:
                applied.skipped, applied.posted = _recompute(connection, database.registry, recomputes(pending), lambda db: db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evlet_calculation_versions'").fetchone() is not None,
                    include_posted=recompute_posted)
            _check(connection, database.registry, rows=True)
            _save_definition(connection, database.registry)
            connection.commit()
            return applied
        except (sqlite3.Error, ValueError, TypeError) as exc:
            connection.rollback()
            if isinstance(exc, SchemaError):
                raise
            raise SchemaError(f'Migration rolled back: {exc}') from exc
        except BaseException:
            connection.rollback()
            raise


@dataclass(frozen=True)
class MigrationPlan:
    """What `migrate()` would do, worked out without writing.

    `state` is "fresh" (a new database records the history as its baseline), "baseline" (an initialized
    database adopts the history without replaying it) or "upgrade". `pending` holds the migrations not yet
    applied, `differences` the (model, field, text) items that still separate the migrations from the
    models, and `suggestion` Python for a migration that covers the ones it can infer. `planned` is the
    schema the applied and pending migrations produce, for suggest()."""
    state: str
    pending: tuple
    differences: tuple
    suggestion: str
    planned: dict = None

    def text(self, suggestion=True):
        lines = []
        if self.pending:
            verb = 'to record without running' if self.state != 'upgrade' else 'to apply'
            lines.append(f'Pending migrations ({verb}):')
            for migration in self.pending:
                lines.append(f'  {migration.name}')
                lines.extend(f'    {describe(operation)}' for operation in migration.operations)
        else:
            lines.append('Pending migrations: none')
        if not self.differences:
            lines.append('The migrations produce the current models.' if self.state == 'upgrade'
                         else 'The database is created or adopted from the current models.')
            return '\n'.join(lines)
        lines.append('Differences between the migrations and the models:')
        lines.extend(f'  {text}' for _, _, text in self.differences)
        if suggestion and self.suggestion:
            lines.append('Suggested migration (review it; fill in any ...):')
            lines.extend(('    ' + line).rstrip() for line in self.suggestion.splitlines())
        return '\n'.join(lines)


def describe(operation):
    """One line naming what an operation changes."""
    if isinstance(operation, CreateModel):
        return f'create model {storage_name(operation.model)}'
    if isinstance(operation, AddTable):
        return f'add table {operation.table()} to {operation.parent} as {operation.name}'
    if isinstance(operation, AddField):
        return f'add field {operation.table}.{operation.name} ({operation.field.kind})'
    if isinstance(operation, RenameField):
        return f'rename field {operation.table}.{operation.old_name} to {operation.new_name}'
    if isinstance(operation, AddIndex):
        return f'add {"unique " if operation.unique else ""}index on {operation.table}.{operation.field}'
    if isinstance(operation, Recompute):
        return f'recompute {operation.table}: {", ".join(operation.fields)}'
    return type(operation).__name__


# Field arguments that shape the stored schema or a backfill; the rest (labels, rules) do not matter to a migration.
SHAPE = ('required', 'unique', 'index', 'max_length', 'minimum', 'maximum', 'scale', 'currency', 'default', 'choices')


def field_code(field):
    """(Python for a frozen copy of a field, class names it uses). A frozen copy keeps only what is stored."""
    from . import __all__ as exported
    from .controls_export import UNKNOWN, call
    args, kwargs = field.arguments
    if isinstance(field, Link):
        args = (link_target(field), *args[1:]) if args else args
        kwargs = {**kwargs, **({'target': link_target(field)} if 'target' in kwargs else {})}
    small = {key: value for key, value in kwargs.items() if key in SHAPE
             and not (key == 'default' and (callable(value) or value is MISSING or type(value).__name__ == '_CurrentUser'))}
    try:
        copy_ = type(field)(*args, **small)
        same = {**copy_.metadata(), 'name': field.name} == field.metadata()
    except (TypeError, ValueError):
        same = False
    code = call(type(field).__name__, args, small if same else kwargs)
    names = {type(field).__name__}
    if UNKNOWN in code or type(field).__name__ not in exported:
        code += '  # copy what ... stands for from models.py'
    return code, names


def _frozen_class(model, base, fields, storage=None):
    """Source for a frozen model class holding copies of `fields`; returns (lines, class names used)."""
    names = {base}
    lines = [f'class {model.__name__}({base}):']
    if storage:
        lines.append(f'    storage_name = {storage!r}')
    for key, field in fields.items():
        code, used = field_code(field)
        names |= used
        lines.append(f'    {key} = {code}')
    if len(lines) == 1:
        lines.append('    pass')
    return lines, names


def suggest(registry, planned, name='0002_changes', tables=None):
    """Python for one migration covering what can be inferred from the differences between `planned` (the
    schema after the migrations) and the models: new models, owned tables, fields, renames and indexes.
    Removals and type changes are written as comments to resolve by hand. `tables` limits it to those
    storage names, such as one app's models."""
    before = {model['name']: {field['name']: field for field in model['fields']} for model in _comparable(planned)['models']}
    after = {model['name']: {field['name']: field for field in model['fields']} for model in _comparable(registry.schema())['models']}
    children = {storage_name(child.model): child for child in registry.children}
    names, classes, operations, notes = {'Migration'}, [], [], []
    for table, model in registry.models.items():
        if table in before or tables is not None and table not in tables:
            continue
        child = children.get(table)
        if child is None:
            storage = table if table != model.__name__.lower() else None
            lines, used = _frozen_class(model, 'Model', dict(model.__app_stored__), storage)
            operations.append(f'CreateModel({model.__name__})')
            names |= used | {'CreateModel'}
        else:
            internal = getattr(model, '__internal_fields__', ())
            lines, used = _frozen_class(model, 'Child', {key: field for key, field in model.__app_stored__.items() if key not in internal})
            parent = registry.name_for(child.parent)
            options = (f', storage_name={table!r}' if table != f'{parent}_{child.name}' else '') + \
                (f', owner_field={child.link!r}' if child.link != 'evlet_owner' else '')
            operations.append(f'AddTable({parent!r}, {child.name!r}, {model.__name__}{options})')
            names |= used | {'AddTable'}
        classes.append('\n'.join(lines))
    for table in sorted(before):
        if tables is not None and table not in tables:
            continue
        if table not in after:
            notes.append(f'# {table}: the model was removed; removing a model is not supported')
            continue
        old, new = before[table], after[table]
        added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
        shape = lambda field: {key: value for key, value in field.items() if key != 'name'}
        if len(added) == len(removed) == 1 and shape(new[added[0]]) == shape(old[removed[0]]):
            operations.append(f'RenameField({table!r}, {removed[0]!r}, {added[0]!r})')
            names.add('RenameField')
            added, removed = [], []
        model = registry.models[table]
        for key in added:
            field = model.__app_stored__[key]
            code, used = field_code(field)
            default = field.default
            backfill = ''
            if field.required and (default is MISSING or callable(default) or type(default).__name__ == '_CurrentUser'):
                backfill = ', backfill=...'
                notes.append(f'# {table}.{key}: set backfill= to the value existing rows get')
            comment = ''
            if '  #' in code:
                code, comment = code.split('  #', 1)
                comment = '  #' + comment
            operations.append(f'AddField({table!r}, {key!r}, {code}{backfill}){comment}')
            names |= used | {'AddField'}
        for key in removed:
            notes.append(f'# {table}.{key}: the field was removed; removing a field is not supported')
        for key in sorted(old.keys() & new.keys()):
            changed = {item for item in old[key].keys() | new[key].keys() if old[key].get(item) != new[key].get(item)}
            if changed and changed <= {'index', 'unique'} and all(new[key].get(item) for item in changed):
                unique = 'unique' in changed
                operations.append(f'AddIndex({table!r}, {key!r}' + (', unique=True)' if unique else ')'))
                names.add('AddIndex')
            elif changed:
                notes.append(f'# {table}.{key}: {", ".join(sorted(changed))} changed; this change is not supported')
    if not operations and not notes:
        return ''
    parts = [f'from evlet import {", ".join(sorted(names))}', *classes]
    body = ''.join(f'\n    {operation},' for operation in operations)
    parts.append(f'migration = Migration(\n    {name!r},{body}\n)' if operations else '')
    return '\n\n\n'.join(part for part in parts if part) + ('\n\n' + '\n'.join(notes) if notes else '') + '\n'


def plan_from(saved, history, migrations, registry, *, tables=True):
    """The MigrationPlan for a database with a saved definition (JSON text or None), its applied history
    rows (name, checksum) and whether it has app tables."""
    migrations, checksums = _migration_plan(migrations)
    if len(history) > len(migrations) or any(
            row[0] != migrations[i].name or row[1] != checksums[i] for i, row in enumerate(history)):
        raise SchemaError('Applied migration history was changed, removed, or reordered')
    pending = migrations[len(history):]
    if saved is None:
        if tables:
            raise SchemaError('Initialize the legacy database with its original models before migrating')
        return MigrationPlan('fresh', pending, (), '')
    if not history and _same(saved, registry):
        return MigrationPlan('baseline', pending, (), '')
    planned = _planned_definition(saved, pending)
    found = tuple(differences(planned, registry.schema()))
    if not found and not pending and not _same(saved, registry):
        found = (('', None, 'Changed model definitions require a new migration'),)
    number = len(migrations) + 1
    return MigrationPlan('upgrade', pending, found, suggest(registry, planned, f'{number:04d}_changes') if found else '', planned)


def plan(database, migrations):
    """What migrate(database, migrations) would do. Reads the SQLite file read-only and writes nothing."""
    if not database.path.exists():
        return plan_from(None, [], migrations, database.registry, tables=False)
    connection = sqlite3.connect(database.path.absolute().as_uri() + '?mode=ro', uri=True)
    try:
        def exists(name):
            return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
        saved = connection.execute('SELECT definition FROM evlet_schema WHERE id = 1').fetchone() if exists('evlet_schema') else None
        history = connection.execute('SELECT name, checksum FROM evlet_migrations ORDER BY position').fetchall() \
            if exists('evlet_migrations') else []
        return plan_from(saved and saved[0], history, migrations, database.registry, tables=bool(_tables(connection)))
    finally:
        connection.close()
