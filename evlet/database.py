"""Small SQLite persistence API with explicit transaction boundaries."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .fields import Link
from .models import ValidationError, identifier
from .query import Query


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


class Database:
    def __init__(self, path, registry, *, migrations=None):
        if str(path) == ":memory:":
            raise ValueError("Use a SQLite file (a temporary file in tests)")
        self.path = Path(path)
        self.registry = registry
        self.migrations = None if migrations is None else tuple(migrations)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

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

    def check(self):
        with self._connection() as connection:
            connection.execute("SELECT 1").fetchone()


class Session:
    """Available inside Database.transaction(); do not retain it afterwards."""

    def __init__(self, connection, registry):
        self.connection = connection
        self.registry = registry

    def create(self, model, /, **values):
        table = identifier(self.registry.name_for(model))
        record = model(**values)
        names = list(model.__fields__)
        if names:
            columns = ", ".join(identifier(name) for name in names)
            placeholders = ", ".join("?" for _ in names)
            cursor = self.connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                [model.__fields__[name].to_storage(record._values[name]) for name in names],
            )
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
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError({"limit": "Use an integer between 1 and 100"})
        if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
            raise ValidationError({"offset": "Use a non-negative 64-bit integer"})
        table = identifier(self.registry.name_for(model))
        query = Query(self.registry, model)
        where, values = query.where(filters, search)
        order = query.ordering(order_by)
        rows = self.connection.execute(
            f'SELECT "record".* FROM {table} AS "record"{where}{order} LIMIT ? OFFSET ?',
            [*values, limit, offset],
        ).fetchall()
        return [model._from_row(row) for row in rows]

    def update(self, model, record_id, /, **changes):
        current = self.get(model, record_id)
        record = model(**{**current._values, **changes})
        if changes:
            table = identifier(self.registry.name_for(model))
            assignments = ', '.join(f'{identifier(name)} = ?' for name in changes)
            self.connection.execute(
                f'UPDATE {table} SET {assignments} WHERE "id" = ?',
                [model.__fields__[name].to_storage(record._values[name]) for name in changes] + [record_id],
            )
        return self.get(model, record_id)

    def delete(self, model, record_id):
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
        where, values = Query(self.registry, model).where(filters, search)
        return self.connection.execute(
            f'SELECT count(*) FROM {table} AS "record"{where}', values,
        ).fetchone()[0]
