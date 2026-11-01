"""Connectors to other services: declarations, settings, encrypted secrets, tests and shared email accounts.

Secrets are encrypted with AES-GCM (the `cryptography` package) under a key kept outside the
database: instances/<name>/secret.key, or EVLET_SECRET_KEY. No API ever returns a secret.
"""

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
import hashlib
import imaplib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import smtplib
import socket
import ssl
import stat
import threading
import time

from .auth import ROLE_ID, AccessError
from .models import ValidationError

logger = logging.getLogger(__name__)
KEY_ENV = 'EVLET_SECRET_KEY'
TIMEOUT = 10
TYPES = ('text', 'number', 'select', 'toggle', 'role', 'secret')
ID = re.compile(r'[a-z][a-z0-9_]{1,30}')
ROW_ID = re.compile(r'([a-z][a-z0-9_]{1,30})(?::([a-z0-9][a-z0-9_-]{0,39}))?')
ADDRESS = re.compile(r'[^@\s<>,;"]{1,64}@[^@\s<>,;"]{1,190}\.[^@\s<>,;".]{2,63}')
UNREADABLE = 'Saved secrets could not be read. The secret key changed or the stored data was altered. Enter them again.'


class ConnectionFailed(Exception):
    """Raised by a connector test with a plain message for the person setting it up."""


class SecretKeyError(ValueError):
    pass


@dataclass(frozen=True)
class ConnectorField:
    name: str
    label: str
    type: str = 'text'
    options: tuple = ()
    default: object = None
    required: bool = True
    help: str = ''

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', self.name) or self.name == 'default':
            raise ValueError(f'Connector field names are lowercase identifiers other than default: {self.name!r}')
        if self.type not in TYPES:
            raise ValueError(f'Connector field type must be one of {", ".join(TYPES)}')
        options = tuple((item, item) if isinstance(item, str) else tuple(item) for item in self.options)
        if (self.type == 'select') != bool(options) or any(len(item) != 2 or not all(isinstance(part, str) for part in item) for item in options):
            raise ValueError('Select fields need options, as values or (value, label) pairs; other types take none')
        object.__setattr__(self, 'options', options)

    def public(self):
        return {'name': self.name, 'label': self.label, 'type': self.type, 'options': [list(item) for item in self.options],
                'default': None if self.type == 'secret' else self.default, 'required': self.required, 'help': self.help}


@dataclass(frozen=True)
class Connector:
    """Connector("whatsapp", title="WhatsApp", fields=(...), test=check). test(values) raises ConnectionFailed."""

    id: str
    title: str
    fields: tuple = ()
    test: object = None
    description: str = ''
    category: str = 'Other'
    accounts: bool = False

    def __post_init__(self):
        if not isinstance(self.id, str) or not ID.fullmatch(self.id):
            raise ValueError(f'Connector ids are 2 to 31 lowercase letters, digits or underscores: {self.id!r}')
        object.__setattr__(self, 'fields', tuple(self.fields))
        names = [field.name for field in self.fields]
        if any(not isinstance(field, ConnectorField) for field in self.fields) or len(set(names)) != len(names):
            raise ValueError(f'{self.id}: fields are ConnectorField definitions with unique names')
        if self.test is not None and not callable(self.test):
            raise ValueError(f'{self.id}: test must be callable')


# ---------- the secret key ----------

class SecretKey:
    """AES-GCM keys from EVLET_SECRET_KEY or a 0600 key file; the first key encrypts, any listed key decrypts."""

    def __init__(self, path=None):
        self.path = None if path is None else Path(path)
        self.lock = threading.Lock()

    @property
    def from_environment(self):
        return bool(os.environ.get(KEY_ENV))

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

    def keys(self, create=False):
        with self.lock:
            if self.from_environment:
                return self.parse(os.environ[KEY_ENV], KEY_ENV)
            if self.path is None:
                raise SecretKeyError(f'No secret key is set up. Set {KEY_ENV}, or give the application a secret key file.')
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

    def encrypt(self, data, context):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key = self.keys(create=True)[0]
        nonce = secrets.token_bytes(12)
        return b'\x01' + self.key_id(key) + nonce + AESGCM(key).encrypt(nonce, data, context)

    def decrypt(self, blob, context):
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = bytes(blob)
        if len(blob) < 33 or blob[:1] != b'\x01':
            raise SecretKeyError(UNREADABLE)
        for key in self.keys():
            if self.key_id(key) == blob[1:5]:
                try:
                    return AESGCM(key).decrypt(blob[5:17], blob[17:], context)
                except InvalidTag:
                    break
        raise SecretKeyError(UNREADABLE)


def rotate(database_path, key):
    """Re-encrypt every saved secret of a SQLite instance under a new key file.

    The old key stays in the file, after the new one, until every row is re-encrypted, so a
    crash at any point leaves every secret readable. Returns how many connectors were re-encrypted.
    """
    import sqlite3
    if key.from_environment:
        raise SecretKeyError(f'{KEY_ENV} is set, so the key is kept outside Evlet. Rotation works on a secret.key file.')
    old = key.keys()
    if not old:
        return 0
    key.write([secrets.token_bytes(32), *old])
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.execute('BEGIN IMMEDIATE')
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_connectors'").fetchone()
        rows = connection.execute('SELECT id, secrets FROM evlet_connectors WHERE secrets IS NOT NULL').fetchall() if exists else []
        for row_id, blob in rows:
            context = row_context(row_id)
            connection.execute('UPDATE evlet_connectors SET secrets=? WHERE id=?', (key.encrypt(key.decrypt(blob, context), context), row_id))
        connection.execute('COMMIT')
    except BaseException:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise
    finally:
        connection.close()
    key.write(key.keys()[:1])
    return len(rows)


def row_context(row_id):
    return b'evlet-connector:' + row_id.encode()


# ---------- framework connectors: email ----------

SECURITY = (('starttls', 'STARTTLS'), ('ssl', 'SSL/TLS'), ('none', 'None (this server only)'))


def _loopback(host):
    try:
        return host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _mail_fields(port):
    return (ConnectorField('address', 'Email address'), ConnectorField('host', 'Server'),
            ConnectorField('port', 'Port', 'number', default=port),
            ConnectorField('security', 'Security', 'select', options=SECURITY, default='ssl' if port in (465, 993) else 'starttls'),
            ConnectorField('username', 'Username', required=False), ConnectorField('password', 'Password', 'secret'))


def _plain(exc, values):
    """A plain message for a mail failure, never including a secret."""
    host = f"{values.get('host')}:{values.get('port')}"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'The server rejected the username or password.'
    if isinstance(exc, smtplib.SMTPNotSupportedError) or 'STARTTLS' in str(exc).upper() and isinstance(exc, (smtplib.SMTPException, imaplib.IMAP4.error)):
        return "The server doesn't offer STARTTLS. Choose SSL/TLS, or check the port."
    if isinstance(exc, ssl.SSLError):
        return 'The secure connection failed. Check the security setting and the port.'
    if isinstance(exc, (socket.timeout, TimeoutError)) or 'timed out' in str(exc):
        return f"{host} didn't answer in time."
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return 'The server refused the sender or recipient address.'
    if isinstance(exc, OSError):
        return f"Couldn't reach {host}. Check the server and port."
    return 'The server answered with an error. Check the details and try again.'


def _smtp(values):
    host, port, security = values['host'], int(values['port']), values['security']
    context = ssl.create_default_context()
    if security == 'ssl':
        client = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=TIMEOUT)
    try:
        client.ehlo()
        if security == 'starttls':
            client.starttls(context=context)
            client.ehlo()
        if values.get('username') or values.get('password'):
            client.login(values.get('username') or values['address'], values['password'])
    except BaseException:
        client.close()
        raise
    return client


def check_smtp(values):
    try:
        _smtp(values).quit()
    except (OSError, smtplib.SMTPException) as exc:
        raise ConnectionFailed(_plain(exc, values)) from None


def check_imap(values):
    host, port, security = values['host'], int(values['port']), values['security']
    try:
        if security == 'ssl':
            client = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        else:
            client = imaplib.IMAP4(host, port, timeout=TIMEOUT)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise ConnectionFailed(_plain(exc, values)) from None
    try:
        if security == 'starttls':
            client.starttls(ssl_context=ssl.create_default_context())
        try:
            client.login(values.get('username') or values['address'], values['password'])
        except imaplib.IMAP4.error:
            raise ConnectionFailed('The server rejected the username or password.') from None
        if client.select('INBOX', readonly=True)[0] != 'OK':
            raise ConnectionFailed('Signed in, but the inbox could not be opened.')
        client.logout()
    except (OSError, imaplib.IMAP4.error) as exc:
        client.shutdown()
        raise ConnectionFailed(_plain(exc, values)) from None
    except ConnectionFailed:
        client.shutdown()
        raise


SMTP = Connector('smtp', title='Email sending', fields=_mail_fields(587), test=check_smtp, category='Email', accounts=True,
                 description='Sends framework and app emails over SMTP.')
IMAP = Connector('imap', title='Email receiving', fields=_mail_fields(993), test=check_imap, category='Email', accounts=True,
                 description='Reads mailboxes over IMAP so apps can act on new mail.')
FRAMEWORK = (SMTP, IMAP)


# ---------- the instance's connectors ----------

class Connectors:
    """Declared connectors, their saved settings and secrets. Trusted code reads values() and sends mail."""

    def __init__(self, business):
        self.business, self.database = business, business.database
        self.declared, self.app_of = {}, {}
        self.key = SecretKey()
        for connector in FRAMEWORK:
            self.register('evlet', connector)

    def register(self, app, connector):
        if not isinstance(connector, Connector):
            raise ValueError(f'{app} must register Connector definitions')
        if connector.id in self.declared:
            raise ValueError(f'Connector {connector.id} is declared twice')
        self.declared[connector.id], self.app_of[connector.id] = connector, app

    def initialize(self):
        self.key.check()
        with self.database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_connectors (id TEXT PRIMARY KEY, app TEXT NOT NULL, status TEXT NOT NULL, settings TEXT NOT NULL, secrets BLOB, changed_by TEXT, changed_at INTEGER)')

    # ---------- reading ----------

    def resolve(self, row_id):
        match = ROW_ID.fullmatch(row_id or '')
        connector = self.declared.get(match[1]) if match else None
        if connector is None or bool(match[2]) != connector.accounts:
            raise AccessError(404, 'Connector not found')
        return connector, match[2]

    def public(self, row):
        connector, account = self.resolve(row['id'])
        settings = json.loads(row['settings'])
        saved = set(json.loads(settings.pop('_secrets', '[]')))
        return {'id': row['id'], 'connector': connector.id, 'account': account, 'status': row['status'],
                'default': bool(settings.pop('default', False)), 'settings': settings,
                'secrets': {field.name: {'saved': field.name in saved} for field in connector.fields if field.type == 'secret'},
                'changed_by': row['changed_by'], 'changed_at': row['changed_at']}

    def row(self, connection, row_id):
        return connection.execute('SELECT * FROM evlet_connectors WHERE id=?', (row_id,)).fetchone()

    def listing(self):
        with self.database._connection() as connection:
            rows = [self.public(row) for row in connection.execute('SELECT * FROM evlet_connectors ORDER BY id') if self.known(row['id'])]
        return {'connectors': [{'id': connector.id, 'title': connector.title, 'app': self.app_of[connector.id], 'category': connector.category,
                                'description': connector.description, 'accounts': connector.accounts, 'testable': connector.test is not None,
                                'fields': [field.public() for field in connector.fields]} for connector in self.declared.values()],
                'items': rows}

    def known(self, row_id):
        try:
            self.resolve(row_id)
            return True
        except AccessError:
            return False

    def get(self, row_id):
        self.resolve(row_id)
        with self.database._connection() as connection:
            row = self.row(connection, row_id)
        if row is None:
            raise AccessError(404, 'This connector is not set up yet')
        return self.public(row)

    def values(self, row_id):
        """Trusted server code: settings and decrypted secrets of a saved connector, or None when it is off."""
        connector, _ = self.resolve(row_id)
        with self.database._connection() as connection:
            row = self.row(connection, row_id)
        if row is None:
            return None
        settings = json.loads(row['settings'])
        settings.pop('_secrets', None)
        settings.pop('default', None)
        if row['secrets'] is not None:
            settings.update(json.loads(self.key.decrypt(row['secrets'], row_context(row_id))))
        return settings

    # ---------- checking values ----------

    def clean(self, connector, account, body, saved_settings, saved_secrets, *, partial):
        if not isinstance(body, dict) or set(body) - {'settings', 'secrets'}:
            raise ValidationError({'_form': 'Send settings and secrets.'})
        given, replaced = body.get('settings', {}), body.get('secrets', {})
        if not isinstance(given, dict) or not isinstance(replaced, dict):
            raise ValidationError({'_form': 'Send settings and secrets as objects.'})
        fields = {field.name: field for field in connector.fields}
        errors = {}
        for name in given:
            if name not in fields or fields[name].type == 'secret':
                errors[name] = 'Unknown setting.'
        for name in replaced:
            if name not in fields or fields[name].type != 'secret':
                errors[name] = 'Unknown secret.'
        settings, new_secrets = {}, {}
        roles = self.business.controls.roles or self.business.auth.roles()
        for field in connector.fields:
            if field.name in errors:
                continue
            if field.type == 'secret':
                if field.name in replaced:
                    value = replaced[field.name]
                    if not isinstance(value, str) or not value.strip():
                        errors[field.name] = 'Paste a new value, or leave it out to keep the saved one.'
                    elif len(value) > 4096:
                        errors[field.name] = 'Use at most 4096 characters.'
                    else:
                        new_secrets[field.name] = value
                elif field.required and field.name not in saved_secrets and not partial:
                    errors[field.name] = f'Enter the {field.label.lower()}.'
                continue
            value = given.get(field.name, saved_settings.get(field.name, field.default))
            try:
                settings[field.name] = self.check_value(field, value, roles)
            except ValueError as exc:
                errors[field.name] = str(exc)
        if connector in FRAMEWORK and not errors:
            if not ADDRESS.fullmatch(settings['address']):
                errors['address'] = 'Enter a valid email address.'
            if settings['security'] == 'none' and not _loopback(settings['host']):
                errors['security'] = 'Without encryption the mail server must be on this machine. Choose STARTTLS or SSL/TLS.'
        if errors:
            raise ValidationError(errors)
        return settings, new_secrets

    @staticmethod
    def check_value(field, value, roles):
        if value is None or value == '':
            if field.required and field.type != 'toggle':
                raise ValueError(f'Fill in {field.label.lower()}.')
            return False if field.type == 'toggle' else None
        if field.type == 'text':
            if not isinstance(value, str) or len(value) > 500 or any(ord(char) < 32 for char in value):
                raise ValueError('Use at most 500 characters on one line.')
            return value.strip()
        if field.type == 'number':
            if type(value) is not int or not 0 <= value <= 2**31:
                raise ValueError('Use a whole number.')
            if field.name == 'port' and not 1 <= value <= 65535:
                raise ValueError('Use a port from 1 to 65535.')
            return value
        if field.type == 'toggle':
            if type(value) is not bool:
                raise ValueError('Use true or false.')
            return value
        if field.type == 'select':
            if value not in [item[0] for item in field.options]:
                raise ValueError('Choose one of the options.')
            return value
        if not isinstance(value, str) or not ROLE_ID.fullmatch(value) or value not in roles or value == 'admin' or roles[value]['realm'] != 'desk':
            raise ValueError('Choose an existing desk role other than admin.')
        return value

    # ---------- changing ----------

    def save(self, actor, row_id, body):
        connector, account = self.resolve(row_id)
        from .controls import audit
        with self.database._connection() as connection:
            existing = self.row(connection, row_id)
        saved_settings = {} if existing is None else json.loads(existing['settings'])
        saved_names = set(json.loads(saved_settings.pop('_secrets', '[]')))
        settings, replaced = self.clean(connector, account, body, saved_settings, saved_names, partial=False)
        with self.database.transaction() as tx:
            current = self.row(tx.connection, row_id)
            if (current is None) != (existing is None):
                raise AccessError(409, 'Someone else changed this connector. Open it again.')
            secret_values = {}
            if current is not None and current['secrets'] is not None and saved_names - replaced.keys():
                try:
                    secret_values = json.loads(self.key.decrypt(current['secrets'], row_context(row_id)))
                except SecretKeyError:
                    raise ValidationError({name: UNREADABLE for name in saved_names - replaced.keys()}) from None
            secret_values.update(replaced)
            default = bool(saved_settings.get('default')) if current is not None else (
                connector is SMTP and not tx.connection.execute("SELECT 1 FROM evlet_connectors WHERE id LIKE 'smtp:%'").fetchone())
            stored = {**settings, **({'default': True} if default else {}), '_secrets': json.dumps(sorted(secret_values))}
            blob = self.key.encrypt(json.dumps(secret_values, sort_keys=True).encode(), row_context(row_id)) if secret_values else None
            tx.connection.execute('INSERT INTO evlet_connectors(id,app,status,settings,secrets,changed_by,changed_at) VALUES (?,?,?,?,?,?,?) '
                                  'ON CONFLICT(id) DO UPDATE SET status=excluded.status, settings=excluded.settings, secrets=excluded.secrets, '
                                  'changed_by=excluded.changed_by, changed_at=excluded.changed_at',
                                  (row_id, self.app_of[connector.id], 'connected', json.dumps(stored, sort_keys=True), blob, actor.username, int(time.time())))
            before = None if existing is None else {key: value for key, value in saved_settings.items() if key != 'default'}
            details = ['Set up' if existing is None else 'Settings changed' if before != settings else None,
                       *(f'{label} replaced' for label in (field.label for field in connector.fields if field.name in replaced) if existing is not None)]
            audit(tx.connection, actor.username, self.area(connector), self.target(connector, account),
                  ' · '.join(item for item in details if item) or 'Saved', before, {**settings, 'secrets': sorted(secret_values)})
        return self.get(row_id)

    def remove(self, actor, row_id):
        connector, account = self.resolve(row_id)
        from .controls import audit
        with self.database.transaction() as tx:
            row = self.row(tx.connection, row_id)
            if row is None:
                raise AccessError(404, 'This connector is not set up yet')
            settings = json.loads(row['settings'])
            if settings.get('default') and tx.connection.execute("SELECT count(*) FROM evlet_connectors WHERE id LIKE 'smtp:%'").fetchone()[0] > 1:
                raise AccessError(409, 'This is the default outgoing account. Make another one the default first.')
            tx.connection.execute('DELETE FROM evlet_connectors WHERE id=?', (row_id,))
            settings.pop('_secrets', None)
            settings.pop('default', None)
            audit(tx.connection, actor.username, self.area(connector), self.target(connector, account),
                  'Removed' if account else 'Disconnected; saved credentials deleted', settings, None)

    def make_default(self, actor, row_id):
        connector, account = self.resolve(row_id)
        if connector is not SMTP:
            raise AccessError(404, 'Only outgoing accounts can be the default')
        from .controls import audit
        with self.database.transaction() as tx:
            if self.row(tx.connection, row_id) is None:
                raise AccessError(404, 'This connector is not set up yet')
            for row in tx.connection.execute("SELECT id, settings FROM evlet_connectors WHERE id LIKE 'smtp:%'").fetchall():
                settings = json.loads(row['settings'])
                settings.pop('default', None)
                if row['id'] == row_id:
                    settings['default'] = True
                tx.connection.execute('UPDATE evlet_connectors SET settings=? WHERE id=?', (json.dumps(settings, sort_keys=True), row['id']))
            audit(tx.connection, actor.username, 'Email', account, 'Made the default outgoing account')
        return self.get(row_id)

    @staticmethod
    def area(connector):
        return 'Email' if connector in FRAMEWORK else 'Connectors'

    @staticmethod
    def target(connector, account):
        return account or connector.title

    # ---------- testing ----------

    def test(self, actor, row_id, body):
        """Run the connector's test on saved values, or on draft values over them, within TIMEOUT seconds."""
        connector, account = self.resolve(row_id)
        if connector.test is None:
            raise AccessError(404, 'This connector has no test')
        body = dict(body or {})
        to = body.pop('to', None)
        if to is not None and (connector is not SMTP or not isinstance(to, str) or not ADDRESS.fullmatch(to)):
            raise ValidationError({'to': 'Enter a valid email address.'})
        with self.database._connection() as connection:
            row = self.row(connection, row_id)
        saved = {} if row is None else json.loads(row['settings'])
        names = set(json.loads(saved.pop('_secrets', '[]')))
        saved.pop('default', None)
        settings, replaced = self.clean(connector, account, body, saved, names, partial=False)
        values = dict(settings)
        if row is not None and row['secrets'] is not None and names - replaced.keys():
            try:
                values.update(json.loads(self.key.decrypt(row['secrets'], row_context(row_id))))
            except SecretKeyError as exc:
                return {'ok': False, 'error': str(exc)}
        values.update(replaced)
        hidden = [value for name, value in values.items() if isinstance(value, str) and name in {field.name for field in connector.fields if field.type == 'secret'}]
        check = connector.test if to is None else lambda values: self.deliver(values, to, 'Test email from Evlet', 'This is a test email from Evlet. The account works.')
        started = time.monotonic()
        result = run_with_timeout(check, values)
        elapsed = round((time.monotonic() - started) * 1000)
        if result is None:
            outcome = {'ok': True, 'message': 'Sent. Check the inbox in a minute.' if to else f'Connected. The service answered in {elapsed} ms.'}
        else:
            outcome = {'ok': False, 'error': scrub(result, hidden)}
        # A test of exactly the saved values sets the status Controls shows.
        if row is not None and not to and not replaced and settings == saved:
            with self.database.transaction() as tx:
                tx.connection.execute('UPDATE evlet_connectors SET status=? WHERE id=?', ('connected' if outcome['ok'] else 'error', row_id))
        return outcome

    # ---------- sending mail ----------

    def accounts(self, connector_id='smtp'):
        with self.database._connection() as connection:
            return [self.public(row) for row in connection.execute('SELECT * FROM evlet_connectors WHERE id LIKE ? ORDER BY id', (connector_id + ':%',))]

    def send_mail(self, to, subject, text, *, account=None, html=None, reply_to=None):
        """Send one email through a named outgoing account, or the default one. Returns the Message-ID."""
        rows = self.accounts('smtp')
        if account is not None:
            chosen = next((row for row in rows if row['account'] == account), None)
            if chosen is None:
                raise ConnectionFailed(f'There is no outgoing email account named {account}.')
        else:
            chosen = next((row for row in rows if row['default']), rows[0] if rows else None)
            if chosen is None:
                raise ConnectionFailed('No outgoing email account is set up. Add one in Controls, under Email.')
        recipients = [to] if isinstance(to, str) else list(to)
        if not recipients or len(recipients) > 50 or any(not isinstance(item, str) or not ADDRESS.fullmatch(item) for item in recipients):
            raise ValueError('Send to between 1 and 50 valid email addresses')
        values = self.values(chosen['id'])
        return self.deliver(values, recipients, subject, text, html=html, reply_to=reply_to)

    @staticmethod
    def deliver(values, to, subject, text, *, html=None, reply_to=None):
        message = EmailMessage()
        message['From'] = values['address']
        message['To'] = ', '.join([to] if isinstance(to, str) else to)
        message['Subject'] = subject
        message['Message-ID'] = make_msgid(domain=values['address'].rsplit('@', 1)[1])
        if reply_to:
            message['Reply-To'] = reply_to
        message.set_content(text)
        if html is not None:
            message.add_alternative(html, subtype='html')
        try:
            client = _smtp(values)
            try:
                client.send_message(message)
            finally:
                try:
                    client.quit()
                except (OSError, smtplib.SMTPException):
                    client.close()
        except (OSError, smtplib.SMTPException) as exc:
            raise ConnectionFailed(_plain(exc, values)) from None
        return message['Message-ID']


def scrub(message, hidden):
    for value in hidden:
        if value:
            message = message.replace(value, '••••')
    return message


def run_with_timeout(check, values):
    """None when check(values) passes; a plain error otherwise. A check still running after TIMEOUT is abandoned."""
    outcome = []

    def run():
        try:
            check(dict(values))
            outcome.append(None)
        except ConnectionFailed as exc:
            outcome.append(str(exc) or 'The service rejected these details. Check them and try again.')
        except Exception as exc:
            # Only the type is logged: an exception message may quote a secret.
            logger.warning('Connector test failed with %s', type(exc).__name__)
            outcome.append('The test failed. Check the details and try again.')

    worker = threading.Thread(target=run, name='evlet-connector-test', daemon=True)
    worker.start()
    worker.join(TIMEOUT)
    if worker.is_alive() or not outcome:
        return f"The service didn't answer within {TIMEOUT} seconds. Check the address and try again."
    return outcome[0]
