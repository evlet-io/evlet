"""Connectors to other services: declarations, settings, encrypted secrets, tests and shared email accounts.

Secrets are sealed with evlet.secrets (AES-GCM) under the instance key kept outside the database:
instances/<name>/secret.key, or EVLET_SECRET_KEY. No API ever returns a secret.
"""

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from collections import deque
import imaplib
import ipaddress
import json
import logging
import re
import smtplib
import socket
import ssl
import threading
import time
from urllib.parse import quote, quote_plus

from .auth import ROLE_ID, AccessError
from .models import ValidationError, checked_body
from . import secrets as vault
from .secrets import SecretKeyError

logger = logging.getLogger(__name__)
TIMEOUT = 10
TYPES = ('text', 'number', 'select', 'toggle', 'role', 'secret')
ID = re.compile(r'[a-z][a-z0-9_]{1,30}')
ROW_ID = re.compile(r'([a-z][a-z0-9_]{1,30})(?::([a-z0-9][a-z0-9_-]{0,39}))?')
ADDRESS = re.compile(r'[^@\s<>,;"]{1,64}@[^@\s<>,;"]{1,190}\.[^@\s<>,;".]{2,63}')
UNREADABLE = 'Saved secrets could not be read. The secret key changed or the stored data was altered. Enter them again.'
# One message for unreachable, silent and confused servers, so a test can't map ports.
UNREACHABLE = "Couldn't connect to the service. Check the server, port and security, and try again."
NOT_ALLOWED = "This server address is not allowed. Use the service's public address, or None with a mail server on this machine."
REJECTED = 'The server rejected the username or password.'
BUSY = 'Two connection tests are already running. Try again in a moment.'
AGAIN = 'Enter the {label} again. The server details changed, so the saved one is not used.'
TEST_RATE = 10  # connection tests per person per minute
# Settings that say where a secret is sent: changing one drops the saved secrets.
IDENTIFYING = re.compile(r'url|host|server|port|user|address|account|endpoint|gateway|issuer|domain|tenant|region|bucket|security')
# Cloud metadata services outside the link-local ranges.
METADATA = tuple(ipaddress.ip_network(net) for net in ('fd00:ec2::254/128', '100.100.100.200/32'))


class ConnectionFailed(Exception):
    """Raised by a connector test with a plain message for the person setting it up."""



@dataclass(frozen=True)
class ConnectorField:
    name: str
    label: str
    type: str = 'text'
    options: tuple = ()
    default: object = None
    required: bool = True
    help: str = ''
    # Whether the field says where secrets go (a host, URL, port or username). None: decided by its name.
    identifies: bool | None = None

    def __post_init__(self):
        if self.identifies is None:
            object.__setattr__(self, 'identifies', self.type != 'secret' and bool(IDENTIFYING.search(self.name)))
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
    # Ports a "port" field may use, unless the connection stays on this machine.
    ports: tuple = ()

    def __post_init__(self):
        if not isinstance(self.id, str) or not ID.fullmatch(self.id):
            raise ValueError(f'Connector ids are 2 to 31 lowercase letters, digits or underscores: {self.id!r}')
        object.__setattr__(self, 'fields', tuple(self.fields))
        names = [field.name for field in self.fields]
        if any(not isinstance(field, ConnectorField) for field in self.fields) or len(set(names)) != len(names):
            raise ValueError(f'{self.id}: fields are ConnectorField definitions with unique names')
        if self.test is not None and not callable(self.test):
            raise ValueError(f'{self.id}: test must be callable')


def row_context(row_id):
    return b'evlet-connector:' + row_id.encode()


def mailbox_context(user_id):
    return b'evlet-mailbox:%d' % user_id


def state_context(digest):
    return b'evlet-mail-state:' + digest.encode()


# ---------- framework connectors: email ----------

SECURITY = (('starttls', 'STARTTLS'), ('ssl', 'SSL/TLS'), ('none', 'None (this server only)'))


def _loopback(host):
    try:
        return host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def safe_address(host, port, *, local=False):
    """Resolve host once and refuse link-local, metadata, multicast, unspecified and reserved addresses.

    Loopback is allowed only for a local connection, and a local connection only reaches loopback.
    Returns the (address, port) to connect to, so the checked address is the one used.
    """
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError):
        raise ConnectionFailed(UNREACHABLE) from None
    chosen = None
    for *_, sockaddr in found:
        address = ipaddress.ip_address(sockaddr[0].split('%')[0])
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        if (address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved
                or any(address in network for network in METADATA) or address.is_loopback != local):
            raise ConnectionFailed(NOT_ALLOWED)
        chosen = chosen or (sockaddr[0], port)
    if chosen is None:
        raise ConnectionFailed(UNREACHABLE)
    return chosen


class _SMTP(smtplib.SMTP):
    def __init__(self, address, host, port, timeout):
        self._address = address
        super().__init__(host, port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        return socket.create_connection(self._address, timeout)


class _SMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, address, host, port, timeout, context):
        self._address = address
        super().__init__(host, port, timeout=timeout, context=context)

    def _get_socket(self, host, port, timeout):
        return self.context.wrap_socket(socket.create_connection(self._address, timeout), server_hostname=self._host)


class _Limits:
    """IMAP reads with a cap on each literal and, with a deadline, a socket shut at that time."""
    LITERAL = 2_000_000

    def _limit(self, sock):
        if self._deadline is not None:
            def stop():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.watchdog = threading.Timer(max(0.0, self._deadline - time.monotonic()), stop)
            self.watchdog.daemon = True
            self.watchdog.start()
        return sock

    def read(self, size):
        if size > self.LITERAL:
            raise imaplib.IMAP4.abort('The server sent more than this connection reads.')
        return super().read(size)


class _IMAP(_Limits, imaplib.IMAP4):
    def __init__(self, address, host, port, timeout, deadline=None):
        self._address, self._deadline, self.watchdog = address, deadline, None
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout=None):
        return self._limit(socket.create_connection(self._address, timeout))


class _IMAPSSL(_Limits, imaplib.IMAP4_SSL):
    def __init__(self, address, host, port, timeout, context, deadline=None):
        self._address, self._deadline, self.watchdog = address, deadline, None
        super().__init__(host, port, ssl_context=context, timeout=timeout)

    def _create_socket(self, timeout=None):
        return self.ssl_context.wrap_socket(self._limit(socket.create_connection(self._address, timeout)), server_hostname=self.host)


def _mail_fields(port):
    return (ConnectorField('address', 'Email address'), ConnectorField('host', 'Server'),
            ConnectorField('port', 'Port', 'number', default=port),
            ConnectorField('security', 'Security', 'select', options=SECURITY, default='ssl' if port in (465, 993) else 'starttls'),
            ConnectorField('username', 'Username', required=False), ConnectorField('password', 'Password', 'secret'))


def _plain(exc, values):
    """A plain message for a mail failure, never including a secret."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return REJECTED
    if isinstance(exc, smtplib.SMTPNotSupportedError) or 'STARTTLS' in str(exc).upper() and isinstance(exc, (smtplib.SMTPException, imaplib.IMAP4.error)):
        return "The server doesn't offer STARTTLS. Choose SSL/TLS, or check the port."
    if isinstance(exc, ssl.SSLError):
        return 'The secure connection failed. Check the security setting and the port.'
    if isinstance(exc, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
        return 'The server refused the sender or recipient address.'
    return UNREACHABLE


def _smtp(values):
    host, port, security = values['host'], int(values['port']), values['security']
    context = ssl.create_default_context()
    address = safe_address(host, port, local=security == 'none')
    if security == 'ssl':
        client = _SMTPSSL(address, host, port, TIMEOUT, context)
    else:
        client = _SMTP(address, host, port, TIMEOUT)
    try:
        client.ehlo()
        if security == 'starttls':
            client.starttls(context=context)
            client.ehlo()
        if values.get('token'):
            client.auth('XOAUTH2', lambda challenge=None: xoauth2(values['address'], values['token']), initial_response_ok=True)
        elif values.get('username') or values.get('password'):
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


def xoauth2(address, token):
    return f'user={address}\x01auth=Bearer {token}\x01\x01'


def open_imap(values):
    """Sign in to an IMAP server with a password, or with an OAuth token (XOAUTH2). Raises ConnectionFailed.

    values['deadline'] (time.monotonic()) closes the connection at that time, however slowly the server answers.
    """
    host, port, security = values['host'], int(values['port']), values['security']
    address = safe_address(host, port, local=security == 'none')
    deadline = values.get('deadline')
    try:
        if security == 'ssl':
            client = _IMAPSSL(address, host, port, TIMEOUT, ssl.create_default_context(), deadline)
        else:
            client = _IMAP(address, host, port, TIMEOUT, deadline)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise ConnectionFailed(_plain(exc, values)) from None
    try:
        if security == 'starttls':
            client.starttls(ssl_context=ssl.create_default_context())
        try:
            if values.get('token'):
                client.authenticate('XOAUTH2', lambda challenge: xoauth2(values['address'], values['token']).encode())
            else:
                client.login(values.get('username') or values['address'], values['password'])
        except imaplib.IMAP4.abort:
            # The connection dropped or timed out mid sign-in; the server never said no.
            raise
        except imaplib.IMAP4.error:
            raise ConnectionFailed(REJECTED) from None
    except (OSError, imaplib.IMAP4.error) as exc:
        client.shutdown()
        raise ConnectionFailed(_plain(exc, values)) from None
    except ConnectionFailed:
        client.shutdown()
        raise
    return client


def check_imap(values):
    client = open_imap(values)
    try:
        if client.select('INBOX', readonly=True)[0] != 'OK':
            raise ConnectionFailed('Signed in, but the inbox could not be opened.')
        client.logout()
    except (OSError, imaplib.IMAP4.error) as exc:
        client.shutdown()
        raise ConnectionFailed(_plain(exc, values)) from None
    except ConnectionFailed:
        client.shutdown()
        raise


SMTP = Connector('smtp', title='Email sending', fields=_mail_fields(587), test=check_smtp, category='Email', accounts=True, ports=(25, 465, 587),
                 description='Sends framework and app emails over SMTP.')
IMAP = Connector('imap', title='Email receiving', fields=_mail_fields(993), test=check_imap, category='Email', accounts=True, ports=(143, 993),
                 description='Reads mailboxes over IMAP so apps can act on new mail.')
FRAMEWORK = (SMTP, IMAP)


# ---------- the instance's connectors ----------

class Connectors:
    """Declared connectors, their saved settings and secrets. Trusted code reads values() and sends mail."""

    def __init__(self, business):
        self.business, self.database = business, business.database
        self.declared, self.app_of = {}, {}
        # An application built in code without a secret key keeps connector secrets in a key file next
        # to its SQLite file (Application sets it); instances use business.secret_key, shared with two-step sign-in.
        self.fallback = None
        self._key = None
        # Connection tests run on at most two threads; an abandoned test keeps its slot until it ends.
        self.slots = threading.BoundedSemaphore(2)
        self.recent, self.recent_lock = {}, threading.Lock()
        for connector in FRAMEWORK:
            self.register('evlet', connector)

    def register(self, app, connector):
        if not isinstance(connector, Connector):
            raise ValueError(f'{app} must register Connector definitions')
        if connector.id in self.declared:
            raise ValueError(f'Connector {connector.id} is declared twice')
        self.declared[connector.id], self.app_of[connector.id] = connector, app

    @property
    def key(self):
        """The instance's SecretKey (evlet.secrets): the same key file as two-step sign-in and backup codes."""
        if self._key is not None:
            return self._key
        configured = getattr(self.business, 'secret_key', None)
        return vault.secret_key(configured if configured is not None else self.fallback)

    @key.setter
    def key(self, value):
        self._key = None if value is None else vault.secret_key(value)

    def seal(self, connection, data, row_id):
        """Encrypt a connector's secrets. The key file is created only while nothing else is sealed with it."""
        return self.key.encrypt(data, row_context(row_id), create=not vault.sealed_exists(connection))

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
        """Check settings and new secrets. Returns (settings, new secrets, saved secret names still usable).

        Saved secrets are usable only while every identifying setting (host, URL, port, username...)
        is unchanged, so a saved password is never sent to a different server.
        """
        checked_body(body, {'settings', 'secrets'}, 'Send settings and secrets.')
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
            if field.name in errors or field.type == 'secret':
                continue
            value = given.get(field.name, saved_settings.get(field.name, field.default))
            try:
                settings[field.name] = self.check_value(field, value, roles)
            except ValueError as exc:
                errors[field.name] = str(exc)
        moved = bool(saved_settings) and any(field.identifies and field.name in settings and settings[field.name] != saved_settings.get(field.name)
                                             for field in connector.fields)
        kept = set() if moved else set(saved_secrets)
        for field in connector.fields:
            if field.name in errors or field.type != 'secret':
                continue
            if field.name in replaced:
                value = replaced[field.name]
                if not isinstance(value, str) or not value.strip():
                    errors[field.name] = 'Paste a new value, or leave it out to keep the saved one.'
                elif len(value) > 4096:
                    errors[field.name] = 'Use at most 4096 characters.'
                else:
                    new_secrets[field.name] = value
            elif field.required and field.name not in kept and not partial:
                errors[field.name] = AGAIN.format(label=field.label.lower()) if field.name in saved_secrets else f'Enter the {field.label.lower()}.'
        if connector.ports and 'port' in settings and settings.get('security') != 'none' and settings['port'] not in connector.ports:
            *head, last = (str(port) for port in connector.ports)
            errors['port'] = f"Use port {', '.join(head)} or {last}." if head else f'Use port {last}.'
        if connector in FRAMEWORK and not errors:
            if not ADDRESS.fullmatch(settings['address']):
                errors['address'] = 'Enter a valid email address.'
            if settings['security'] == 'none' and not _loopback(settings['host']):
                errors['security'] = 'Without encryption the mail server must be on this machine. Choose STARTTLS or SSL/TLS.'
        if errors:
            raise ValidationError(errors)
        return settings, new_secrets, kept

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
        settings, replaced, kept = self.clean(connector, account, body, saved_settings, saved_names, partial=False)
        with self.database.transaction() as tx:
            current = self.row(tx.connection, row_id)
            if (current is None) != (existing is None):
                raise AccessError(409, 'Someone else changed this connector. Open it again.')
            secret_values = {}
            if current is not None and current['secrets'] is not None and kept - replaced.keys():
                try:
                    secret_values = json.loads(self.key.decrypt(current['secrets'], row_context(row_id)))
                except SecretKeyError:
                    raise ValidationError({name: UNREADABLE for name in kept - replaced.keys()}) from None
                secret_values = {name: value for name, value in secret_values.items() if name in kept}
            secret_values.update(replaced)
            default = bool(saved_settings.get('default')) if current is not None else (
                connector is SMTP and not tx.connection.execute("SELECT 1 FROM evlet_connectors WHERE id LIKE 'smtp:%'").fetchone())
            stored = {**settings, **({'default': True} if default else {}), '_secrets': json.dumps(sorted(secret_values))}
            blob = self.seal(tx.connection, json.dumps(secret_values, sort_keys=True).encode(), row_id) if secret_values else None
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

    def throttle(self, actor):
        """At most TEST_RATE connection tests a minute per person."""
        with self.recent_lock:
            now = time.monotonic()
            times = self.recent.setdefault(actor.id, deque())
            while times and times[0] < now - 60:
                times.popleft()
            if len(times) >= TEST_RATE:
                raise AccessError(429, 'Too many connection tests. Try again in a minute.')
            times.append(now)

    def test(self, actor, row_id, body):
        """Run the connector's test on saved values, or on draft values over them, within TIMEOUT seconds."""
        connector, account = self.resolve(row_id)
        if connector.test is None:
            raise AccessError(404, 'This connector has no test')
        self.throttle(actor)
        body = dict(body or {})
        to = body.pop('to', None)
        if to is not None and (connector is not SMTP or not isinstance(to, str) or not ADDRESS.fullmatch(to)):
            raise ValidationError({'to': 'Enter a valid email address.'})
        with self.database._connection() as connection:
            row = self.row(connection, row_id)
        saved = {} if row is None else json.loads(row['settings'])
        names = set(json.loads(saved.pop('_secrets', '[]')))
        saved.pop('default', None)
        settings, replaced, kept = self.clean(connector, account, body, saved, names, partial=False)
        values = dict(settings)
        if row is not None and row['secrets'] is not None and kept - replaced.keys():
            try:
                values.update({name: value for name, value in json.loads(self.key.decrypt(row['secrets'], row_context(row_id))).items() if name in kept})
            except SecretKeyError as exc:
                return {'ok': False, 'error': UNREADABLE if str(exc) == vault.UNREADABLE else str(exc)}
        values.update(replaced)
        hidden = [value for name, value in values.items() if isinstance(value, str) and name in {field.name for field in connector.fields if field.type == 'secret'}]
        check = connector.test if to is None else lambda values: self.deliver(values, to, 'Test email from Evlet', 'This is a test email from Evlet. The account works.')
        result = run_with_timeout(check, values, self.slots)
        if result is None:
            outcome = {'ok': True, 'message': 'Sent. Check the inbox in a minute.' if to else 'Connected.'}
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

    def send_mail(self, to, subject, text, *, account=None, html=None, reply_to=None, inline=()):
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
        return self.deliver(values, recipients, subject, text, html=html, reply_to=reply_to, inline=inline)

    @staticmethod
    def deliver(values, to, subject, text, *, html=None, reply_to=None, inline=()):
        """inline: [(content id, bytes, image type)] parts the HTML refers to as cid: (rich text images)."""
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
            if inline:
                part = message.get_payload()[-1]
                for cid, content, kind in inline:
                    part.add_related(content, maintype='image', subtype=kind.split('/', 1)[1], cid=f'<{cid}>')
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
    """Remove secrets from a message: as written, quoted, escaped, URL-encoded, and long leading parts."""
    for value in hidden:
        if not value:
            continue
        forms = {value, repr(value)[1:-1], json.dumps(value)[1:-1], quote(value, safe=''), quote_plus(value, safe=''), quote(value)}
        for form in sorted(forms, key=len, reverse=True):
            message = message.replace(form, '••••')
            for length in range(len(form) - 1, 7, -1):
                if form[:length] in message:
                    message = message.replace(form[:length], '••••')
                    break
    return message


def run_with_timeout(check, values, slots):
    """None when check(values) passes; a plain error otherwise. A check still running after TIMEOUT is
    abandoned but keeps its slot until it ends, so at most as many test threads as slots ever run."""
    if not slots.acquire(blocking=False):
        raise AccessError(429, BUSY)
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
        finally:
            slots.release()

    worker = threading.Thread(target=run, name='evlet-connector-test', daemon=True)
    try:
        worker.start()
    except BaseException:
        slots.release()
        raise
    worker.join(TIMEOUT)
    if worker.is_alive() or not outcome:
        return UNREACHABLE
    return outcome[0]
