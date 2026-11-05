"""Private attachment storage, with metadata committed alongside record changes."""

import os
from pathlib import Path
import re
import secrets
import stat


# The largest private file read back: a rich text image of 8 MiB at most (Controls, files.image_max_kb).
# Attachments and profile photos have their own lower limits.
MAX_FILE = 8 * 1024 * 1024


class StorageError(RuntimeError):
    pass


class SQLiteStorage:
    """Store bytes in the same SQLite transaction as attachment metadata."""

    def initialize(self, database):
        with database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, record_id INTEGER NOT NULL, name TEXT NOT NULL, content BLOB NOT NULL)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_attachment_owner ON evlet_attachments(model,record_id)')
            columns = database.columns(tx.connection, 'evlet_attachments')
            for name, kind in (('storage_key', 'TEXT'), ('file_size', 'INTEGER'), ('storage_kind', 'TEXT')):
                if name not in columns:
                    tx.connection.execute(f'ALTER TABLE evlet_attachments ADD COLUMN {name} {kind}')

    def add(self, tx, model, record_id, name, content):
        return tx.connection.execute('INSERT INTO evlet_attachments(model,record_id,name,content) VALUES (?,?,?,?)', (model, record_id, name, content)).lastrowid

    def read(self, row):
        if row['storage_key'] is not None:
            raise StorageError('This attachment requires its file storage adapter')
        return row['content']


class LocalFileStorage(SQLiteStorage):
    """Immutable private files. Deletes remove metadata; collect reclaims files.

    A failed transaction may leave an unreferenced file. Collection holds the
    database write lock, so it cannot remove an upload awaiting its commit.
    """

    def __init__(self, root):
        configured = Path(root).absolute()
        self.root = configured.parent.resolve() / configured.name
        self.database = None
        self.namespace = None

    def initialize(self, database):
        super().initialize(database)
        with database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_file_store (id INTEGER PRIMARY KEY CHECK(id=1), namespace TEXT NOT NULL)')
            tx.connection.execute('INSERT OR IGNORE INTO evlet_file_store VALUES (1,?)', (secrets.token_hex(32),))
            namespace = tx.connection.execute('SELECT namespace FROM evlet_file_store WHERE id=1').fetchone()[0]
        try:
            self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
            if self.root.is_symlink() or self.root.resolve() != self.root:
                raise StorageError('File storage must not use symbolic links')
            marker = self.root / '.evlet-store'
            try:
                fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(namespace)
                    stream.flush()
                    os.fsync(stream.fileno())
            self.database, self.namespace = database, namespace
            self._check_binding()
        except (OSError, UnicodeError) as exc:
            raise StorageError('Private file storage is unavailable') from exc

    def _check_binding(self):
        if self.database is None or self.root.is_symlink() or self.root.resolve() != self.root:
            raise StorageError('File storage is not initialized')
        with os.fdopen(os.open(self.root / '.evlet-store', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'r') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(65) != self.namespace:
                raise StorageError('File storage belongs to another database')

    def _path(self, key):
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
            raise StorageError('Invalid private file key')
        return self.root / key

    def add(self, tx, model, record_id, name, content):
        if self.database is None or not self.database.owns(tx):
            raise StorageError('Attachment transaction belongs to another database')
        key = secrets.token_hex(32)
        try:
            self._check_binding()
            fd = os.open(self._path(key), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, UnicodeError) as exc:
            raise StorageError('Private file storage is unavailable') from exc
        return tx.connection.execute('INSERT INTO evlet_attachments(model,record_id,name,content,storage_key,file_size) VALUES (?,?,?,X\'\',?,?)', (model, record_id, name, key, len(content))).lastrowid

    def read(self, row):
        if row['storage_key'] is None:
            return super().read(row)
        if row['storage_kind'] not in (None, 'local'):
            raise StorageError('Attachment requires its original storage adapter')
        try:
            self._check_binding()
            fd = os.open(self._path(row['storage_key']), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != row['file_size'] or not 1 <= info.st_size <= MAX_FILE:
                    raise StorageError('Private file is damaged')
                return stream.read()
        except (OSError, UnicodeError) as exc:
            raise StorageError('Private file storage is unavailable') from exc

    def remove(self, key):
        """Delete one file whose metadata row is already gone, such as a replaced photo."""
        try:
            self._check_binding()
            with self.database._connection() as connection:
                if connection.execute('SELECT 1 FROM evlet_attachments WHERE storage_key=?', (key,)).fetchone():
                    return
            self._path(key).unlink(missing_ok=True)
        except (OSError, UnicodeError) as exc:
            raise StorageError('Private file storage is unavailable') from exc

    def collect(self):
        """Reclaim unreferenced generated files, including rolled-back uploads."""
        try:
            self._check_binding()
            with self.database.transaction() as tx:
                referenced = {row[0] for row in tx.connection.execute('SELECT storage_key FROM evlet_attachments WHERE storage_key IS NOT NULL')}
                removed = 0
                for path in self.root.iterdir():
                    if re.fullmatch('[0-9a-f]{64}', path.name) and path.name not in referenced:
                        path.unlink()
                        removed += 1
                return removed
        except (OSError, UnicodeError) as exc:
            raise StorageError('Private file storage is unavailable') from exc
