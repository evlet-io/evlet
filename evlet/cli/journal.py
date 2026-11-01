"""Check the hash chains of an instance's record journals."""

from contextlib import closing
import sqlite3

from .project import child


def verify_instance(root, name):
    """(entries checked, breaks) for an instance's SQLite database, read in one snapshot without writing."""
    from ..instances import read_instance
    from ..journal import verify_connection
    path = child(root, 'instances', name)
    read_instance(path)
    database = path / 'data/database.sqlite3'
    if not database.is_file():
        raise ValueError(f'Instance {name} has no database yet')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evlet_journal'").fetchone() is None:
            return 0, []
        connection.execute('BEGIN')
        try:
            return verify_connection(connection)
        finally:
            connection.rollback()
