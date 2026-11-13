"""The instance's secret key and sealed secrets (AES-GCM): one key for connector secrets,
two-step secrets, backup codes, sign-ins in progress and personal mailboxes.

Keys come from EVLET_SECRET_KEY (comma-separated) or from instances/<name>/secret.key: one
32-byte key per line in URL-safe base64, mode 0600, owned by the server's user, never a link.
The first key seals; any listed key opens, so a rotation can keep the old key after the new one.
Each sealed value is bound to a purpose string (such as "two_step:42") that must match to open it.
AES-GCM comes from the `cryptography` package, a core dependency. SEALED lists every column that
holds sealed values, so `evlet secrets rotate` re-encrypts them all under a new key.

    seal(key, b"value", "two_step:42") -> bytes
    open(key, sealed, "two_step:42")   -> b"value"
    available(key)                      -> whether secrets can be sealed and opened now
    sealed_exists(connection)           -> whether anything is sealed (a missing key is then an error)
    rotate(database_path, key)          -> (re-encrypted count, unreadable rows), for a SQLite instance

key is a SecretKey or a key file path. A missing key file is created only when seal is asked to
create it; callers that already hold sealed data never ask, so a lost key is an error, not a new key.
"""

import base64
import hashlib
import os
from pathlib import Path
import secrets
import stat
import threading

KEY_ENV = 'EVLET_SECRET_KEY'
UNREADABLE = 'Saved secrets could not be read. The secret key changed or the stored data was altered.'
MISSING = 'The secret key is missing. Restore secret.key from a backup, or set EVLET_SECRET_KEY, then start again.'


class SecretKeyError(ValueError):
    pass


class SecretKey:
    """AES-GCM keys from EVLET_SECRET_KEY or a 0600 key file; the first key encrypts, any listed key decrypts."""

    def __init__(self, path=None):
        self.path = None if path is None else Path(path)
        self.lock = threading.Lock()

    @property
    def from_environment(self):
        return bool(os.environ.get(KEY_ENV))

    @property
    def configured(self):
        return self.from_environment or self.path is not None

    @staticmethod
    def parse(text, source):
        keys = []
        for item in (part.strip() for part in text.replace(',', '\n').splitlines()):
            if not item:
                continue
            try:
                raw = base64.urlsafe_b64decode(item + '=' * (-len(item) % 4))
            except (ValueError, TypeError):
                raw = b''
            if len(raw) != 32:
                raise SecretKeyError(f'{source} must hold 32-byte keys in URL-safe base64, one per line.')
            keys.append(raw)
        if not keys:
            raise SecretKeyError(f'{source} is empty.')
        return keys

    def check(self):
        """Refuse a key file that others can read, a link, or another owner's file. Called on start."""
        if self.from_environment:
            self.parse(os.environ[KEY_ENV], KEY_ENV)
            return
        if self.path is None or not (self.path.exists() or self.path.is_symlink()):
            return
        info = os.lstat(self.path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SecretKeyError(f'{self.path} must be a regular file, not a link.')
        if hasattr(os, 'getuid') and info.st_uid != os.getuid():
            raise SecretKeyError(f'{self.path} belongs to another user. The server must own its secret key.')
        if info.st_mode & 0o077:
            raise SecretKeyError(f'{self.path} can be read by other users. Run chmod 600 {self.path} and start again.')

    def exists(self):
        return self.from_environment or (self.path is not None and (self.path.exists() or self.path.is_symlink()))

    def keys(self, create=False):
        with self.lock:
            if self.from_environment:
                return self.parse(os.environ[KEY_ENV], KEY_ENV)
            if self.path is None:
                raise SecretKeyError(f'No secret key is set up. Set {KEY_ENV}, or run the application as an instance.')
            # Read on every use, so a rotation is picked up without a restart.
            self.check()
            if not self.path.exists():
                if not create:
                    return []
                try:
                    self.write([secrets.token_bytes(32)], new=True)
                except FileExistsError:
                    self.check()  # Another process created it first.
            return self.parse(self.path.read_text(), str(self.path))

    def write(self, keys, new=False):
        """Write the key file with mode 0600: a new file exclusively, a replacement atomically."""
        text = ''.join(base64.urlsafe_b64encode(key).decode().rstrip('=') + '\n' for key in keys)
        target = self.path if new else self.path.with_name(self.path.name + '.new')
        if not new and target.exists():
            target.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if not new:
            os.replace(target, self.path)

    @staticmethod
    def key_id(key):
        return hashlib.sha256(key).digest()[:4]

    def encrypt(self, data, context, create=True):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        keys = self.keys(create=create)
        if not keys:
            raise SecretKeyError(MISSING)
        nonce = secrets.token_bytes(12)
        return b'\x01' + self.key_id(keys[0]) + nonce + AESGCM(keys[0]).encrypt(nonce, bytes(data), context)

    def decrypt(self, blob, context):
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = bytes(blob)
        if len(blob) < 33 or blob[:1] != b'\x01':
            raise SecretKeyError(UNREADABLE)
        keys = self.keys()
        if not keys:
            raise SecretKeyError(MISSING)
        for key in keys:
            if self.key_id(key) == blob[1:5]:
                try:
                    return AESGCM(key).decrypt(blob[5:17], blob[17:], context)
                except InvalidTag:
                    break
        raise SecretKeyError(UNREADABLE)


def secret_key(key):
    return key if isinstance(key, SecretKey) else SecretKey(key)


def cryptography_installed():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except ImportError:
        return False
    return True


def available(key, existing=False):
    """Secrets can be kept: cryptography is installed and the key is configured and valid.
    existing=True also needs the key to be there already (sealed data depends on it)."""
    key = secret_key(key)
    if not key.configured or not cryptography_installed():
        return False
    try:
        key.check()
        return bool(key.keys()) if existing or key.exists() else True
    except (SecretKeyError, OSError):
        return False


def seal(key, value, purpose, create=False):
    """Encrypt bytes bound to purpose. create=True may make the key file; only when nothing sealed exists yet."""
    if not cryptography_installed():
        raise SecretKeyError('Install the cryptography package on the server to keep secrets.')
    return secret_key(key).encrypt(value, purpose.encode(), create=create)


def open(key, sealed, purpose):
    """Decrypt what seal returned for the same purpose; SecretKeyError when the key or purpose does not match."""
    if not cryptography_installed():
        raise SecretKeyError('Install the cryptography package on the server to keep secrets.')
    return secret_key(key).decrypt(sealed, purpose.encode())


# Every column holding sealed values: (table, sealed column, row id column, purpose column,
# purpose prefix, stored as base64 text, how a row that can't be read is named).
SEALED = (
    ('evlet_connectors', 'secrets', 'id', 'id', 'evlet-connector:', False, '{}'),
    ('evlet_user_profile', 'two_step_secret', 'user_id', 'user_id', 'two_step:', False, 'two-step sign-in of user {}'),
    ('evlet_user_profile', 'two_step_pending', 'user_id', 'user_id', 'two_step_pending:', False, 'two-step set-up of user {}'),
    ('evlet_user_profile', 'backup_codes', 'user_id', 'user_id', 'backup_codes:', True, 'backup codes of user {}'),
    ('evlet_two_step_tickets', 'secret', 'token_hash', 'user_id', 'two_step_ticket:', False, 'a sign-in in progress of user {}'),
    ('evlet_mailboxes', 'secrets', 'user_id', 'user_id', 'evlet-mailbox:', False, 'the mailbox of user {}'),
    ('evlet_mail_states', 'secrets', 'hash', 'hash', 'evlet-mail-state:', False, 'a mailbox sign-in in progress ({})'),
)


def sealed_exists(connection):
    """Whether any SEALED column holds a value; then a missing key is an error, never a new key."""
    query = ' UNION ALL '.join(f'SELECT 1 FROM {table} WHERE {column} IS NOT NULL' for table, column, *_ in SEALED)
    return connection.execute(f'SELECT 1 FROM ({query}) AS sealed LIMIT 1').fetchone() is not None


def rotate(database_path, key):
    """Re-encrypt every sealed value of a SQLite instance under a new key file.

    The old key stays in the file, after the new one, until every row is re-encrypted, so a crash at
    any point leaves every secret readable. A row that can't be read is skipped and reported, and the
    old key is then kept for it. Returns (re-encrypted count, names of unreadable rows).
    """
    key = secret_key(key)
    if key.from_environment:
        raise SecretKeyError(f'{KEY_ENV} is set, so the key is kept outside Evlet. Rotation works on a secret.key file.')
    if not Path(database_path).is_file():
        raise ValueError('This instance has no database yet, so there is nothing to rotate.')
    old = key.keys()
    if not old:
        return 0, []
    key.write([secrets.token_bytes(32), *old])
    from .writes import connect
    connection = connect(database_path, uri=True, isolation_level=None)
    done, failed = 0, []
    try:
        connection.execute('BEGIN IMMEDIATE')
        tables = connection.tables()
        for table, column, id_column, purpose_column, prefix, text, name in SEALED:
            if table not in tables:
                continue
            rows = connection.execute(f'SELECT {id_column}, {purpose_column}, {column} FROM {table} WHERE {column} IS NOT NULL').fetchall()
            for row_id, owner, value in rows:
                purpose = (prefix + str(owner)).encode()
                try:
                    plain = key.decrypt(base64.b64decode(value) if text else value, purpose)
                except (SecretKeyError, ValueError):
                    failed.append(name.format(owner))
                    continue
                sealed = key.encrypt(plain, purpose, create=False)
                connection.execute(f'UPDATE {table} SET {column}=? WHERE {id_column}=?', (base64.b64encode(sealed).decode() if text else sealed, row_id))
                done += 1
        connection.execute('COMMIT')
    except BaseException:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise
    finally:
        connection.close()
    if not failed:
        key.write(key.keys()[:1])
    return done, failed
