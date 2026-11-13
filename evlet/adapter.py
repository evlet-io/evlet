"""The database adapter boundary (docs/database-adapters.md).

Evlet runs on SQLite (evlet/sqlite.py). Everything that depends on the database sits behind the methods
below, so another database can be added in one place: a Database subclass that opens its connections
(evlet/database.py) and a connection class that implements this interface. Framework code outside the
database layer calls these methods on the connection it was given and never asks which database it is.

The interface is provisional: it may change in a minor release, with a CHANGELOG note.
"""

from dataclasses import dataclass
import sqlite3

# The error types framework code catches. They are the standard DB-API classes from Python's sqlite3 module;
# another adapter raises them, or subclasses of them, for its driver's errors at the connection boundary.
DatabaseError = sqlite3.Error
IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError


@dataclass(frozen=True)
class Column:
    """One column of a table. `type` is Evlet's column type (TEXT, INTEGER, REAL or BLOB, from Field.columns),
    `references` the table a foreign key points at (its "id"), or None."""
    name: str
    type: str
    required: bool = False
    primary: bool = False
    references: str = None


@dataclass(frozen=True)
class Index:
    """One index. `partial` is True for an index that covers only some rows (a Sequence's assigned numbers)."""
    name: str
    columns: tuple
    unique: bool = False
    partial: bool = False


@dataclass(frozen=True)
class TableShape:
    """A table as migrations compare it with the models: the same shape on every database."""
    columns: tuple
    indexes: tuple

    def names(self):
        return [column.name for column in self.columns]


@dataclass(frozen=True)
class TableDefinition:
    """A framework table (evlet_*), created when missing with connection.ensure_table(definition).

    numbered: the table starts with an integer "id" the database numbers and never reuses. A column with
    primary=True is the primary key; primary_key names a key of several columns instead. unique lists
    groups of columns that must be unique together. checks are query builder conditions every row meets."""
    name: str
    columns: tuple
    numbered: bool = False
    primary_key: tuple = ()
    unique: tuple = ()
    checks: tuple = ()


@dataclass(frozen=True)
class IntegrityFailure:
    """What an integrity error says: its message and, for a unique violation, the table and its columns."""
    message: str
    table: str = None
    columns: tuple = ()


class Result:
    """What connection.run(statement) returns: the rows of a query, or what a write changed.

    one() is the first row or None, all() every row, value() the first column of the first row or None.
    rowcount counts the rows a write changed; id is the new row's id after insert(...).returning_id()."""

    def __init__(self, cursor, *, returning=False):
        self.cursor = cursor
        self.id = None
        if returning:
            row = cursor.fetchone()
            self.id = None if row is None else row[0]
            self.rowcount = 0 if row is None else 1
        else:
            self.rowcount = cursor.rowcount

    def one(self):
        return self.cursor.fetchone()

    def all(self):
        return self.cursor.fetchall()

    def value(self):
        row = self.cursor.fetchone()
        return None if row is None else row[0]

    def __iter__(self):
        return iter(self.cursor)


class Connection:
    """What every Evlet connection provides: run(statement) for query builder statements, the schema methods
    migrations use, ids, locks and error reading, plus commit(), rollback() and in_transaction. Rows read by
    index and by column name.

    Framework code outside the dialect never writes SQL text; it builds statements with evlet/qb.py. Modules
    not yet moved onto the query builder still call execute() with SQLite SQL (see the plan in
    docs/database-adapters.md); that bridge goes away module by module."""

    # Running statements

    def run(self, statement):
        """Compile a query builder statement (evlet/qb.py) for this database, run it and return a Result."""
        raise NotImplementedError

    # Reading

    def snapshot(self):
        """Begin a read-only transaction whose reads all see the same moment."""
        raise NotImplementedError

    # Reading the schema

    def table_exists(self, name):
        """Whether the table exists."""
        raise NotImplementedError

    def tables(self):
        """The names of every table, without the database's own system tables."""
        raise NotImplementedError

    def columns(self, table):
        """The column names of a table, in order."""
        raise NotImplementedError

    def inspect(self, table):
        """The table's TableShape: its columns and its indexes."""
        raise NotImplementedError

    def broken_foreign_key(self):
        """The name of a table holding a value its foreign key does not find, or None when every link holds."""
        raise NotImplementedError

    # Changing the schema

    def ensure_table(self, definition):
        """Create a framework table from its TableDefinition when it does not exist yet. An existing table is
        left as it is: a change to a framework table is a framework step (evlet/framework.py)."""
        raise NotImplementedError

    def ensure_index(self, name, table, columns, *, unique=False):
        """Create an index of a framework table when it does not exist yet."""
        raise NotImplementedError

    def create_table(self, table, columns):
        """A model table: an integer "id" primary key the database numbers, then the given Columns."""
        raise NotImplementedError

    def add_column(self, table, column, default=None):
        """Add one Column; existing rows get `default`, a fixed value."""
        raise NotImplementedError

    def rename_column(self, table, old, new):
        """Rename a column, keeping its data."""
        raise NotImplementedError

    def drop_column(self, table, column):
        """Delete a column and its data."""
        raise NotImplementedError

    def cannot_drop_columns(self):
        """None, or the reason this database cannot drop columns."""
        raise NotImplementedError

    def set_required(self, table, column, required):
        """Add or remove NOT NULL on a column, keeping the stored rows."""
        raise NotImplementedError

    def drop_reference(self, table, column):
        """Remove a column's foreign key, keeping the column and its values."""
        raise NotImplementedError

    def create_index(self, table, base, columns, *, unique=False, partial=None):
        """Create an index named `base`, or `base` with a number when that name is taken, and return its name.
        With `partial` (a column name) the index covers only rows where that column is not null."""
        raise NotImplementedError

    def drop_index(self, name):
        """Drop one index."""
        raise NotImplementedError

    def make_append_only(self, table, message):
        """Refuse UPDATE and DELETE of the table's rows, and any insert that replaces a row, with an integrity
        error whose message ends with `message`. Idempotent."""
        raise NotImplementedError

    def drop_append_only(self, table):
        """Undo make_append_only."""
        raise NotImplementedError

    def is_append_only(self, table):
        """True when the table refuses changes, False when it does not; SchemaError when it is half way."""
        raise NotImplementedError

    def guard_id(self, table, message):
        """Refuse an UPDATE that changes a row's id, with an integrity error whose message ends with `message`."""
        raise NotImplementedError

    def has_id_guard(self, table):
        """Whether guard_id was applied to the table."""
        raise NotImplementedError

    # Ids and locks

    def insert_many(self, table, columns, rows):
        """Insert rows (tuples of values for the named columns) and return their new ids, in order."""
        raise NotImplementedError

    def savepoint(self, name):
        """A context manager: changes inside it are undone, and the error raised again, when it fails."""
        raise NotImplementedError

    def reserve_ids(self, table, count):
        """Reserve `count` ids of a ledger table, in increasing order, that later inserts never take.
        Only inside a write transaction."""
        raise NotImplementedError

    def lock(self, numbers, milliseconds=None):
        """Hold the named locks (sorted 64-bit numbers) until the transaction ends. Only inside a write
        transaction. With milliseconds, a longer wait raises evlet.ledger.LockTimeout."""
        raise NotImplementedError

    # Errors

    def integrity_failure(self, exc):
        """An IntegrityFailure for an integrity error, None for any other error."""
        raise NotImplementedError
