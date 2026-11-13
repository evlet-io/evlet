"""Check the hash chains of an instance's record journals."""

from contextlib import closing
import sqlite3

from .project import child
from ..sqlite import SQLiteConnection


def verify_instance(root, name):
    """(entries checked, breaks) for an instance's SQLite database, read in one snapshot without writing."""
    from ..instances import read_instance
    from ..journal import verify_connection
    path = child(root, 'instances', name)
    read_instance(path)
    database = path / 'data/database.sqlite3'
    if not database.is_file():
        raise ValueError(f'Instance {name} has no database yet')
    with closing(connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'evlet_journal' not in tables:
            return 0, []
        connection.execute('BEGIN')
        try:
            # The first read starts the snapshot both statements share.
            hashed = {row[0] for row in connection.execute('SELECT model FROM evlet_journal_hashed')} if 'evlet_journal_hashed' in tables else set()
            return verify_connection(connection, hashed=hashed)
        finally:
            connection.rollback()


def connect(database):
    """A read-only connection. A WAL database whose shared memory file cannot be created read only
    (no -shm file next to it yet) is opened normally with writes refused instead."""
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, factory=SQLiteConnection)
        connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
        return connection
    except sqlite3.OperationalError:
        connection = sqlite3.connect(database.resolve(), timeout=5, factory=SQLiteConnection)
        connection.execute('PRAGMA query_only = ON')
        return connection
