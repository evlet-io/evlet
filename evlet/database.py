"""Small SQLite persistence API with explicit transaction boundaries."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .fields import Link, MISSING, Reference
from .models import ValidationError, identifier
from .query import SqlQuery

# Most rows one list call returns, and most rows one child collection may hold in an editor.
ROW_LIMIT = 100


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


class Database:
    def __init__(self, path, registry, *, migrations=None, must_exist=False):
        if str(path) == ":memory:":
            raise ValueError("Use a SQLite file (a temporary file in tests)")
        configured = Path(path)
        self.path = configured.parent.resolve() / configured.name if must_exist else configured
        self.must_exist = must_exist
        self.registry = registry
        self.migrations = None if migrations is None else tuple(migrations)

    @contextmanager
    def _connection(self):
        if self.must_exist and (self.path.is_symlink() or self.path.absolute().resolve() != self.path.absolute()):
            raise sqlite3.OperationalError('Database paths must not use symbolic links')
        target = self.path.absolute().as_uri() + '?mode=rw' if self.must_exist else self.path
        connection = sqlite3.connect(target, timeout=5, uri=self.must_exist)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    def owns(self, session):
        """Check the physical database before accepting a bound feature write."""
        if not isinstance(session.connection, sqlite3.Connection):
            return False
        path = session.connection.execute('PRAGMA database_list').fetchone()[2]
        return Path(path).resolve() == self.path.resolve()

    def columns(self, connection, table):
        return {row['name'] for row in connection.execute(f'PRAGMA table_info({identifier(table)})')}

    def initialize(self):
        from .migrations import initialize
        initialize(self)

    def migrate(self, migrations):
        from .migrations import migrate
        return migrate(self, migrations)

    @contextmanager
    def transaction(self):
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield Session(connection, self.registry)
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise ConflictError("A unique value or linked record constraint was violated") from exc
            except BaseException:
                connection.rollback()
                raise

    def create(self, model, /, **values):
        with self.transaction() as session:
            return session.create(model, **values)

    def get(self, model, record_id):
        with self._connection() as connection:
            return Session(connection, self.registry).get(model, record_id)

    def update(self, model, record_id, /, **changes):
        with self.transaction() as session:
            return session.update(model, record_id, **changes)

    def delete(self, model, record_id):
        with self.transaction() as session:
            session.delete(model, record_id)

    def related(self, record, field_name):
        with self._connection() as connection:
            return Session(connection, self.registry).related(record, field_name)

    def list(self, model, *, limit=50, offset=0, filters=None, order_by=None, search=""):
        with self._connection() as connection:
            return Session(connection, self.registry).list(
                model, limit=limit, offset=offset, filters=filters, order_by=order_by, search=search)

    def count(self, model, *, filters=None, search=""):
        with self._connection() as connection:
            return Session(connection, self.registry).count(model, filters=filters, search=search)

    def page(self, model, **query):
        """Return records and their filtered count from one read snapshot."""
        with self._connection() as connection:
            connection.execute("BEGIN")
            session = Session(connection, self.registry)
            records = session.list(model, **query)
            total = session.count(model, filters=query.get('filters'), search=query.get('search', ''))
            return records, total

    def close(self):
        """SQLite connections already close at the end of each operation."""

    def check(self):
        with self._connection() as connection:
            connection.execute("SELECT 1").fetchone()


def storage_columns(model, values, names):
    """Column names and storage values for the named fields; a Reference field fills two columns."""
    columns, stored = [], []
    for name in names:
        field = model.__fields__[name]
        for (column, _), value in zip(field.columns(name), field.to_columns(values.get(name))):
            columns.append(column)
            stored.append(value)
    return columns, stored


class Session:
    """Available inside Database.transaction(); do not retain it afterwards."""

    def __init__(self, connection, registry):
        self.connection = connection
        self.registry = registry
        self._maintenance = False

    @contextmanager
    def maintenance(self):
        """Audited repairs only: raw writes to finite-v1 documents skip settlement, so the
        caller must restore calculated invariants (for example by resaving through Context)."""
        previous, self._maintenance = self._maintenance, True
        try:
            yield self
        finally:
            self._maintenance = previous

    def _guard(self, model):
        plan_for = getattr(self.registry, 'plan_for', None)
        if not self._maintenance and plan_for is not None and plan_for(model) is not None:
            raise ValueError(f'{model.__name__} is a finite-v1 document; write it through Context.create/update '
                             '(document settlement) or inside session.maintenance() for an audited repair')

    def _state(self, model, value, current):
        """A State changes only through its moves; raw writes may set it only inside maintenance()."""
        name = getattr(model, '__state__', None)
        if name is None or self._maintenance or value is MISSING:
            return
        expected = model.__declared_fields__[name].default if current is None else current._values.get(name)
        if value != expected:
            raise ValueError(f'{model.__name__}.{name} changes only through its moves; use Context.move(), '
                             'or session.maintenance() for an audited repair')

    def create(self, model, /, **values):
        self._guard(model)
        self._state(model, values.get(getattr(model, '__state__', None) or '', MISSING), None)
        table = identifier(self.registry.name_for(model))
        record = model(**values)
        self.check_references(model, record._values)
        names, stored = storage_columns(model, record._values, model.__fields__)
        if names:
            columns = ", ".join(identifier(name) for name in names)
            placeholders = ", ".join("?" for _ in names)
            cursor = self.connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", stored)
        else:
            cursor = self.connection.execute(f"INSERT INTO {table} DEFAULT VALUES")
        return self.get(model, cursor.lastrowid)

    def get(self, model, record_id):
        if type(record_id) is not int or not 1 <= record_id <= 2**63 - 1:
            raise ValidationError({"id": "Expected a positive 64-bit integer"})
        table = identifier(self.registry.name_for(model))
        row = self.connection.execute(
            f'SELECT * FROM {table} WHERE "id" = ?', (record_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("Record not found")
        return model._from_row(row)

    def list(self, model, *, limit=50, offset=0, filters=None, order_by=None, search=""):
        if type(limit) is not int or not 1 <= limit <= ROW_LIMIT:
            raise ValidationError({"limit": f"Use an integer between 1 and {ROW_LIMIT}"})
        if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
            raise ValidationError({"offset": "Use a non-negative 64-bit integer"})
        table = identifier(self.registry.name_for(model))
        query = SqlQuery(self.registry, model)
        where, values = query.where(filters, search)
        order = query.ordering(order_by)
        rows = self.connection.execute(
            f'SELECT "record".* FROM {table} AS "record"{where}{order} LIMIT ? OFFSET ?',
            [*values, limit, offset],
        ).fetchall()
        return [model._from_row(row) for row in rows]

    def update(self, model, record_id, /, **changes):
        self._guard(model)
        current = self.get(model, record_id)
        self._state(model, changes.get(getattr(model, '__state__', None) or '', MISSING), current)
        record = model(**{**current._values, **changes})
        if changes:
            self.check_references(model, {name: record._values[name] for name in changes})
            table = identifier(self.registry.name_for(model))
            names, stored = storage_columns(model, record._values, changes)
            assignments = ', '.join(f'{identifier(name)} = ?' for name in names)
            self.connection.execute(f'UPDATE {table} SET {assignments} WHERE "id" = ?', stored + [record_id])
        return self.get(model, record_id)

    def check_references(self, model, values):
        """Reference fields have no SQL foreign key, so each write checks that the record exists."""
        for name, value in values.items():
            if isinstance(model.__declared_fields__.get(name), Reference) and value is not None:
                target = self.registry.models.get(value[0])
                if target is None:
                    raise ValidationError({name: 'Use a registered model name'})
                if self.connection.execute(f'SELECT 1 FROM {identifier(value[0])} WHERE "id" = ?', (value[1],)).fetchone() is None:
                    raise ValidationError({name: 'The referenced record does not exist'})

    def delete(self, model, record_id):
        self._guard(model)
        self.get(model, record_id)
        table = identifier(self.registry.name_for(model))
        self.connection.execute(f'DELETE FROM {table} WHERE "id" = ?', (record_id,))

    def related(self, record, field_name):
        self.registry.name_for(type(record))
        field = record.__fields__.get(field_name)
        if not isinstance(field, Link):
            raise ValidationError({field_name: 'Expected a Link field'})
        record_id = getattr(record, field_name)
        return None if record_id is None else self.get(field.target, record_id)

    def count(self, model, *, filters=None, search=""):
        table = identifier(self.registry.name_for(model))
        where, values = SqlQuery(self.registry, model).where(filters, search)
        return self.connection.execute(
            f'SELECT count(*) FROM {table} AS "record"{where}', values,
        ).fetchone()[0]
