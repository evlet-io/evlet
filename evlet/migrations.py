"""Explicit, atomic SQLite schema changes with validated data and history."""

from dataclasses import dataclass
import copy
import hashlib
import json
import logging
import re
import sqlite3

from .fields import Field, Link, MISSING, Reference
from .models import identifier, storage_name

logger = logging.getLogger(__name__)


class SchemaError(ValueError):
    pass


def _column(name, field):
    """Column definitions for one field; most fields have one, a Reference has two."""
    result = []
    for column, sql_type in field.columns(name):
        sql = f'{identifier(column)} {sql_type}'
        if field.required:
            sql += ' NOT NULL'
        if isinstance(field, Link):
            sql += f' REFERENCES {identifier(storage_name(field.target))}("id")'
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


def _tables(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT GLOB 'sqlite_*' AND name NOT GLOB 'evlet_*'")}


def _check(connection, registry, *, rows=False):
    for name, model in registry.models.items():
        table = identifier(name)
        columns = connection.execute(f'PRAGMA table_info({table})').fetchall()
        actual = {row['name']: (row['type'].upper(), bool(row['notnull']), row['pk']) for row in columns}
        expected = {'id': ('INTEGER', False, 1), **{
            column: (sql_type, field.required, 0) for key, field in model.__fields__.items() for column, sql_type in field.columns(key)}}
        if actual != expected:
            raise SchemaError(f'{name}: database columns differ from models; run explicit migrations')
        links = {(row['from'], row['table'], row['to'], row['on_delete'])
                 for row in connection.execute(f'PRAGMA foreign_key_list({table})')}
        expected_links = {(key, storage_name(field.target), 'id', 'NO ACTION')
                          for key, field in model.__fields__.items() if isinstance(field, Link)}
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
            _metadata(connection)
            saved = connection.execute('SELECT definition FROM evlet_schema WHERE id = 1').fetchone()
            if saved and saved[0] != _definition(database.registry):
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
    """The names of the migrations a migrate() call applied; `skipped` counts the records Recompute kept."""
    skipped = 0


def _recompute(connection, registry, operations, versions, *, postgres=False):
    """Run Recompute operations in order. versions(connection) tells whether formula versions are recorded.

    A record that posted effects (voided or not) keeps its saved results, so it is skipped. Records whose results change get
    a `recomputed` Journal entry. Returns the number of records skipped."""
    from .database import Session
    from .calc import CalcError
    from .journal import recomputed
    session = Session(connection, registry)
    covered, skipped, changes = {}, set(), {}
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
                _recompute_legacy(session, model, operation, skipped, changes)
            else:
                path = _recompute_finite(session, registry, plan, model, operation, skipped, changes)
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
    recomputed(connection, registry, changes, postgres=postgres)
    if skipped:
        logger.info('Recompute kept the saved results of %d posted records', len(skipped))
    return len(skipped)


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


def _recompute_finite(session, registry, plan, model, operation, skipped, changes):
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
        if (root, record_id) in skipped or _kept(session, root, record):
            skipped.add((root, record_id))
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


def _recompute_legacy(session, model, operation, skipped, changes):
    from .documents import Draft
    from .reactivity import evaluate, graph, round_field
    if model.__tables__ or model.__sources__ or any(field.query is not None for field in model.__declared_fields__.values()) \
            or any(child.model is model for child in session.registry.children):
        raise SchemaError(f'{operation.table}: Recompute supports string formulas only on models without tables or queries')
    nodes, order = graph(session.registry, model)
    table = identifier(session.registry.name_for(model))
    for (record_id,) in session.connection.execute(f'SELECT "id" FROM {table} ORDER BY "id"').fetchall():
        record = session.get(model, record_id)
        if (model, record_id) in skipped or _kept(session, model, record):
            skipped.add((model, record_id))
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


class Migration:
    def __init__(self, name, *operations):
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_]+', name) or not operations:
            raise ValueError('A migration needs a name and at least one operation')
        if any(not isinstance(op, (AddField, RenameField, AddIndex, CreateModel, Recompute)) for op in operations):
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
            if isinstance(operation, CreateModel):
                name = storage_name(operation.model)
                if name in models:
                    raise SchemaError(f'{name} already exists in the schema')
                models[name] = {'name': name, 'label': operation.model.__name__,
                                'fields': [field.metadata() for field in operation.model.__fields__.values()]}
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


def recomputes(migrations):
    return [operation for migration in migrations for operation in migration.operations if isinstance(operation, Recompute)]


def _normalized(schema):
    schema = copy.deepcopy(schema)
    schema['models'].sort(key=lambda model: model['name'])
    for model in schema['models']:
        model['fields'].sort(key=lambda field: field['name'])
    return schema


def migrate(database, migrations):
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
            if saved and (history or saved[0] != _definition(database.registry)):
                planned = _planned_definition(saved[0], pending)
                if _normalized(planned) != _normalized(database.registry.schema()):
                    raise SchemaError('Migrations do not match model changes; type changes and removals are not supported')
            # A new database initialized directly from current models already has
            # the target schema. Explicit migration adopts that verified baseline.
            baseline = not history and saved and saved[0] == _definition(database.registry)
            if fresh:
                for name, model in database.registry.models.items():
                    _create(connection, name, model)
            pending = migrations[len(history):]
            if not pending and saved and saved[0] != _definition(database.registry):
                raise SchemaError('Changed model definitions require a new migration')
            for position, migration in enumerate(pending, len(history) + 1):
                if not fresh and not baseline:
                    for operation in migration.operations:
                        operation.apply(connection)
                connection.execute('INSERT INTO evlet_migrations (position, name, checksum) VALUES (?, ?, ?)',
                                   (position, migration.name, checksums[position - 1]))
            applied = Applied(item.name for item in pending)
            if not fresh and not baseline:
                applied.skipped = _recompute(connection, database.registry, recomputes(pending), lambda db: db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evlet_calculation_versions'").fetchone() is not None)
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
