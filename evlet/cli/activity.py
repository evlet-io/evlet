"""Check the hash chains of the Activity of an instance's records."""

from contextlib import closing

from .project import child
from ..writes import read_file


def verify_instance(root, name):
    """(entries checked, breaks) for an instance's SQLite database, read in one snapshot without writing."""
    from ..instances import read_instance
    from ..activity import LEGACY_TABLE, hashed_models, verify_connection
    path = child(root, 'instances', name)
    read_instance(path)
    database = path / 'data/database.sqlite3'
    if not database.is_file():
        raise ValueError(f'Instance {name} has no database yet')
    with closing(read_file(database)) as connection:
        if connection.table_exists(LEGACY_TABLE):
            # Framework step 0016_activity renames it; until then the instance does not serve either.
            raise ValueError(f'Instance {name} has framework migrations pending; run evlet migrate {name} first')
        if not connection.table_exists('evlet_activity'):
            return 0, []
        connection.snapshot()
        try:
            # The first read starts the snapshot both statements share.
            return verify_connection(connection, hashed=hashed_models(connection))
        finally:
            connection.rollback()

