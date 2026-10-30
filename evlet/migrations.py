"""Explicit, atomic SQLite schema changes with validated data and history."""

from dataclasses import dataclass
import copy
import hashlib
import json
import re
import sqlite3

from .fields import Field, Link, MISSING
from .models import identifier


class SchemaError(ValueError):
    pass


def _column(name, field):
    sql = f'{identifier(name)} {field.sql_type}'
    if field.required:
        sql += ' NOT NULL'
    if isinstance(field, Link):
        sql += f' REFERENCES {identifier(field.target.__name__.lower())}("id")'
    return sql


def _index(connection, table, field, *, unique=False):
    base = f'evlet_{table}_{field}_{"unique" if unique else "index"}'
    name, suffix = base, 1
    while connection.execute('SELECT 1 FROM sqlite_master WHERE name = ?', (name,)).fetchone():
        suffix += 1
        name = f'{base}_{suffix}'
    connection.execute(f'CREATE {"UNIQUE " if unique else ""}INDEX {identifier(name)} ON {identifier(table)} ({identifier(field)})')


def _create(connection, table, model):
    columns = ['"id" INTEGER PRIMARY KEY AUTOINCREMENT']
    columns.extend(_column(name, field) for name, field in model.__fields__.items())
    connection.execute(f'CREATE TABLE {identifier(table)} ({", ".join(columns)})')
    for name, field in model.__fields__.items():
        if field.index or field.unique:
            _index(connection, table, name, unique=field.unique)


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
            key: (field.sql_type, field.required, 0) for key, field in model.__fields__.items()}}
        if actual != expected:
            raise SchemaError(f'{name}: database columns differ from models; run explicit migrations')
        links = {(row['from'], row['table'], row['to'], row['on_delete'])
                 for row in connection.execute(f'PRAGMA foreign_key_list({table})')}
        expected_links = {(key, field.target.__name__.lower(), 'id', 'NO ACTION')
                          for key, field in model.__fields__.items() if isinstance(field, Link)}
        if links != expected_links:
            raise SchemaError(f'{name}: foreign keys differ from models')
        unique, indexed = set(), set()
        for index in connection.execute(f'PRAGMA index_list({table})').fetchall():
            if index['partial']:
                continue
            fields = tuple(row['name'] for row in connection.execute('SELECT * FROM pragma_index_info(?)', (index['name'],)))
            indexed.add(fields)
            if index['unique']:
                unique.add(fields)
        if unique != {(key,) for key, field in model.__fields__.items() if field.unique}:
            raise SchemaError(f'{name}: unique constraints differ from models')
        if any((key,) not in indexed for key, field in model.__fields__.items() if field.index):
            raise SchemaError(f'{name}: an explicit index migration is required')
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
        return cleaned

    def signature(self):
        identifier(self.table)
        identifier(self.name)
        return {'operation': 'add_field', 'table': self.table, 'name': self.name,
                'field': self.field.metadata(), 'backfill': self.field.serialize(self.value())}

    def apply(self, connection):
        value = self.field.to_storage(self.value())
        if isinstance(self.field, Link) and value is not None:
            raise SchemaError('Add nullable Link fields with a null backfill; required Link backfills are not supported')
        literal = connection.execute('SELECT quote(?)', (value,)).fetchone()[0]
        connection.execute(f'ALTER TABLE {identifier(self.table)} ADD COLUMN {_column(self.name, self.field)} DEFAULT {literal}')
        if self.field.unique or self.field.index:
            _index(connection, self.table, self.name, unique=self.field.unique)


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

    def apply(self, connection):
        connection.execute(f'ALTER TABLE {identifier(self.table)} RENAME COLUMN {identifier(self.old_name)} TO {identifier(self.new_name)}')


@dataclass(frozen=True)
class AddIndex:
    table: str
    field: str
    unique: bool = False

    def signature(self):
        identifier(self.table)
        identifier(self.field)
        return {'operation': 'add_index', 'table': self.table, 'field': self.field, 'unique': self.unique}

    def apply(self, connection):
        _index(connection, self.table, self.field, unique=self.unique)


@dataclass(frozen=True)
class CreateModel:
    model: type

    def signature(self):
        return {'operation': 'create_model', 'name': self.model.__name__.lower(),
                'fields': [field.metadata() for field in self.model.__fields__.values()]}

    def apply(self, connection):
        _create(connection, self.model.__name__.lower(), self.model)


class Migration:
    def __init__(self, name, *operations):
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_]+', name) or not operations:
            raise ValueError('A migration needs a name and at least one operation')
        if any(not isinstance(op, (AddField, RenameField, AddIndex, CreateModel)) for op in operations):
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
                name = operation.model.__name__.lower()
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
            elif isinstance(operation, AddIndex):
                if operation.field not in fields:
                    raise SchemaError('Index field does not exist')
                fields[operation.field]['unique' if operation.unique else 'index'] = True
            models[operation.table]['fields'] = list(fields.values())
    schema['models'] = list(models.values())
    return schema


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
            _check(connection, database.registry, rows=True)
            _save_definition(connection, database.registry)
            connection.commit()
            return [item.name for item in pending]
        except (sqlite3.Error, ValueError, TypeError) as exc:
            connection.rollback()
            if isinstance(exc, SchemaError):
                raise
            raise SchemaError(f'Migration rolled back: {exc}') from exc
        except BaseException:
            connection.rollback()
            raise
