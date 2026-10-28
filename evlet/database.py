"""Small SQLite persistence API with explicit transaction boundaries."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .fields import Link
from .models import ValidationError, identifier


class NotFoundError(LookupError):
    pass


class ConflictError(ValueError):
    pass


class Database:
    def __init__(self, path, registry):
        if str(path) == ":memory:":
            raise ValueError("Use a SQLite file (a temporary file in tests)")
        self.path = Path(path)
        self.registry = registry

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                for name, model in self.registry.models.items():
                    columns = ['"id" INTEGER PRIMARY KEY AUTOINCREMENT']
                    for field_name, field in model.__fields__.items():
                        column = f"{identifier(field_name)} {field.sql_type}"
                        if field.required:
                            column += " NOT NULL"
                        if field.unique:
                            column += " UNIQUE"
                        if isinstance(field, Link):
                            target = self.registry.name_for(field.target)
                            column += f' REFERENCES {identifier(target)}("id")'
                        columns.append(column)
                    connection.execute(
                        f"CREATE TABLE IF NOT EXISTS {identifier(name)} ({', '.join(columns)})"
                    )

    @contextmanager
    def transaction(self):
        with self._connection() as connection:
            try:
                connection.execute("BEGIN")
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

    def list(self, model, *, limit=50, offset=0):
        with self._connection() as connection:
            return Session(connection, self.registry).list(model, limit=limit, offset=offset)

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

    def list(self, model, *, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError({"limit": "Use an integer between 1 and 100"})
        if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
            raise ValidationError({"offset": "Use a non-negative 64-bit integer"})
        table = identifier(self.registry.name_for(model))
        rows = self.connection.execute(
            f'SELECT * FROM {table} ORDER BY "id" DESC LIMIT ? OFFSET ?', (limit, offset)
        ).fetchall()
        return [model._from_row(row) for row in rows]
