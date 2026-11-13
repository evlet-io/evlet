"""Personal mailboxes: people connect their own mail to send from records as themselves and see
replies on the records they can read.

Microsoft 365 and Google sign in with OAuth (authorization code with PKCE); any other mailbox uses
IMAP and SMTP with an app password. Tokens and passwords are sealed with the instance secret key.
A sync job copies in only messages whose addresses match records the person can read, in the
models apps opt in with Meta.mail, as `message` entries in each record's Journal (the person is the
actor); nothing else is stored, not even headers. evlet_mail_links(model, record_id, message_key,
entry_id) keeps a message once per record and finds the records of a thread. No route lets an
admin read a mailbox or its credentials.
"""

import base64
from collections import defaultdict
from dataclasses import dataclass
import email
from email import policy as email_policy
from email.utils import getaddresses
import hashlib
from html import escape
from html.parser import HTMLParser
import http.client
import imaplib
import json
import logging
import re
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .auth import AccessError, User
from . import secrets as vault
from .connectors import (ADDRESS, NOT_ALLOWED, PUBLIC_ONLY, REJECTED, TIMEOUT, UNREACHABLE, ConnectionFailed, Connector, ConnectorField,
                         Connectors, SecretKeyError, _loopback, check_imap, check_smtp, mailbox_context, open_imap, run_with_timeout,
                         safe_address, scrub, state_context)
from .controls import NOT_FOUND, audit
from .fields import Email
from .models import ValidationError, checked_body, identifier

logger = logging.getLogger(__name__)
SETTING = 'mail.personal'
TASK = 'evlet_mail_sync'
EVERY = 300           # seconds between scheduled syncs
BATCH = 50            # messages read per mailbox per run
PER_RUN = 20          # mailboxes per scheduled run
FIRST_DAYS = 30       # a new mailbox starts with the last 30 days
MAX_SIZE = 1_000_000  # bytes of a message read for its text
HEADER_SIZE = 65_536  # bytes of headers parsed per message
RUN_SECONDS = 120     # one mailbox's sync stops after this long
STATE_SECONDS = 600
KINDS = ('customer', 'contact', 'lead', 'other')
OFF = "Your workspace doesn't let people connect their own mailbox. Ask an admin to turn it on in Controls, under Email."
FAILED = 'Syncing stopped with an error. It will try again.'
AREA = 'Email'
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
OPTIONS = {'send_from': ('mine', 'shared'), 'link_replies': (True, False), 'only_crm': (True, False)}
DEFAULT_OPTIONS = {'send_from': 'mine', 'link_replies': True, 'only_crm': True}
PORTS = {'imap': (143, 993), 'smtp': (25, 465, 587)}
# A mailbox a person connects with a password reaches only public mail servers, encrypted, on the
# standard mail ports. A private office server is set up by an admin as a connector instead.
# The test suite turns this off to talk to stub mail servers on this machine.
PUBLIC_SERVERS_ONLY = True

# Provider defaults. The connector can replace each one; tests use a local fake provider.
PROVIDERS = {
    'microsoft': {'title': 'Microsoft 365', 'connector': 'mail_microsoft',
                  'authorize_url': 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize',
                  'token_url': 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token', 'revoke_url': '',
                  'scope': 'openid email offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send',
                  'imap_host': 'outlook.office365.com', 'imap_port': 993, 'smtp_host': 'smtp.office365.com', 'smtp_port': 587},
    'google': {'title': 'Google Workspace', 'connector': 'mail_google',
               'authorize_url': 'https://accounts.google.com/o/oauth2/v2/auth', 'token_url': 'https://oauth2.googleapis.com/token',
               'revoke_url': 'https://oauth2.googleapis.com/revoke', 'scope': 'openid email https://mail.google.com/',
               'imap_host': 'imap.gmail.com', 'imap_port': 993, 'smtp_host': 'smtp.gmail.com', 'smtp_port': 465},
}
TITLES = {'imap': 'Other mailbox', **{name: item['title'] for name, item in PROVIDERS.items()}}
# The page the provider sends the person back to. Their session cookie is SameSite=Strict, so it is not
# sent on that cross-site redirect; the page posts the code from this origin, where it is.
CONTINUE = ('<!doctype html><meta charset="utf-8"><title>Connecting your mailbox</title>'
            '<form method="post" action="/_/mail/callback">{fields}<p>Connecting your mailbox…</p>'
            '<noscript><button>Continue</button></noscript></form><script src="/_/mail/continue.js"></script>')
CONTINUE_CSP = b"default-src 'none'; script-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"


@dataclass(frozen=True)
class Mail:
    """Meta.mail = Mail(match=("email",), kind="customer"): the Email fields a personal mailbox matches addresses in.

    kind is customer, contact, lead or other. "Only read emails with customers and leads" matches the
    first three; with it off, every model with Meta.mail.
    """
    match: tuple
    kind: str = 'other'

    def __post_init__(self):
        match = (self.match,) if isinstance(self.match, str) else tuple(self.match)
        if not match or any(not isinstance(name, str) for name in match) or len(set(match)) != len(match):
            raise ValueError('Mail(match=...) names Email fields, each once')
        if self.kind not in KINDS:
            raise ValueError(f'Mail kind is one of {", ".join(KINDS)}')
        object.__setattr__(self, 'match', match)


def _provider_fields(tenant):
    standard = 'Leave empty for the standard address.'
    return (ConnectorField('client_id', 'Client ID', identifies=True), ConnectorField('client_secret', 'Client secret', 'secret'),
            *((ConnectorField('tenant', 'Directory (tenant) ID', default='organizations', required=False),) if tenant else ()),
            ConnectorField('authorize_url', 'Sign-in address', required=False, help=standard),
            ConnectorField('token_url', 'Token address', required=False, help=standard),
            *(() if tenant else (ConnectorField('revoke_url', 'Revoke address', required=False, help=standard),)),
            ConnectorField('imap_host', 'IMAP server', required=False, help=standard),
            ConnectorField('imap_port', 'IMAP port', 'number', required=False),
            ConnectorField('smtp_host', 'SMTP server', required=False, help=standard),
            ConnectorField('smtp_port', 'SMTP port', 'number', required=False))


CONNECTORS = (
    Connector('mail_microsoft', title='Microsoft 365 mail', category='Email', fields=_provider_fields(True),
              description='Lets people connect their Microsoft 365 mailbox. Register an app in Microsoft Entra and paste its client ID and secret.'),
    Connector('mail_google', title='Google mail', category='Email', fields=_provider_fields(False),
              description='Lets people connect their Google Workspace mailbox. Create an OAuth client in Google Cloud and paste its client ID and secret.'),
)


class Ended(Exception):
    """The mailbox's sign-in no longer works; the person has to connect again."""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


# ---------- sealed secrets and Journal messages ----------

def seal(business, connection, data, context):
    """Seal with the instance key (evlet.secrets). The key file is created only while nothing is sealed yet."""
    return business.connectors.key.encrypt(json.dumps(data, sort_keys=True).encode(), context, create=not vault.sealed_exists(connection))


def unseal(business, blob, context):
    return json.loads(business.connectors.key.decrypt(blob, context))


def link_message(business, tx, user, model, record_id, key, data, field=None):
    """Store one copied message on a record as a Journal `message` entry, with the person as the actor.

    The same message is stored once per record; a different message reusing its Message-ID is kept too.
    `field` names the Email field of the record that matched; the Journal hides `party` from readers who
    can't read that field. Returns the entry id, or None when the record already has the message.
    """
    from . import journal
    if model not in business.journals:
        return None
    entry = {**data, 'source': 'mailbox', **({'field': field} if field else {})}
    same = journal.canonical({name: value for name, value in entry.items() if name != 'field'})
    for row in tx.connection.execute('SELECT j.data FROM evlet_mail_links l JOIN evlet_journal j ON j.id=l.entry_id '
                                     'WHERE l.model=? AND l.record_id=? AND l.message_key=?', (business.registry.name_for(model), record_id, key)):
        if journal.canonical({name: value for name, value in json.loads(row[0]).items() if name not in ('field', 'via')}) == same:
            return None
    entry_id = journal.append(business, tx, user, model, record_id, 'message', entry)
    tx.connection.execute('INSERT INTO evlet_mail_links(model,record_id,message_key,entry_id) VALUES (?,?,?,?)',
                          (business.registry.name_for(model), record_id, key, entry_id))
    business.changed(tx, model, record_id, journal=True)
    return entry_id


# ---------- reading messages ----------

class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ('script', 'style')
        if tag in ('br', 'p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(message):
    """The message's text, at most 10,000 characters; empty when it can't be read."""
    try:
        part = message.get_body(preferencelist=('plain', 'html'))
        text = part.get_content() if part is not None else ''
        if part is not None and part.get_content_subtype() == 'html':
            parser = _Text()
            parser.feed(text[:MAX_SIZE])
            text = ''.join(parser.parts)
    except Exception:
        text = ''
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text.replace('\x00', '').replace('\r', '')).strip()
    return text[:10000]


def addresses(message, *names):
    found = []
    for name in names:
        for _, address in getaddresses([str(value) for value in message.get_all(name, [])]):
            address = address.strip().lower()
            if ADDRESS.fullmatch(address) and address not in found:
                found.append(address)
    return found[:50]


def message_ids(message, *names):
    found = []
    for name in names:
        found += re.findall(r'<[^<>\s]{1,250}>', ' '.join(str(value) for value in message.get_all(name, [])))
    return found[:20]


def key_of(message_id):
    return digest('mid:' + message_id)


def security_for(host, port):
    return 'none' if _loopback(host) else 'ssl' if port in (465, 993) else 'starttls'


def safe_url(value, *, fetch):
    """An https address, or http on this machine only. Fetched addresses resolve to an allowed address first."""
    url = urlsplit(value or '')
    try:
        port = url.port or (443 if url.scheme == 'https' else 80)
    except ValueError:
        raise ConnectionFailed(NOT_ALLOWED) from None
    if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password or url.fragment \
            or (url.scheme == 'http' and not _loopback(url.hostname)):
        raise ConnectionFailed(NOT_ALLOWED)
    return (url, port, safe_address(url.hostname, port, local=url.scheme == 'http')) if fetch else (url, port, None)


class _HTTP(http.client.HTTPConnection):
    def __init__(self, address, host, port, context=None):
        super().__init__(host, port, timeout=TIMEOUT)
        self._address, self._context = address, context

    def connect(self):
        self.sock = socket.create_connection(self._address, self.timeout)
        if self._context is not None:
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def post_form(value, form):
    """POST a form to a token address and read its JSON answer: (status, object). Raises ConnectionFailed."""
    url, port, address = safe_url(value, fetch=True)
    connection = _HTTP(address, url.hostname, port, ssl.create_default_context() if url.scheme == 'https' else None)
    try:
        connection.request('POST', (url.path or '/') + (f'?{url.query}' if url.query else ''), urlencode(form),
                           {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
        response = connection.getresponse()
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ConnectionFailed(UNREACHABLE)
        body = json.loads(raw or b'{}')
        return response.status, body if isinstance(body, dict) else {}
    except (OSError, ValueError, http.client.HTTPException):
        raise ConnectionFailed(UNREACHABLE) from None
    finally:
        connection.close()


def claims(id_token):
    """The claims of an ID token received straight from the token address over TLS (OpenID Connect 3.1.3.7)."""
    try:
        part = id_token.split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        return value if isinstance(value, dict) else {}
    except (AttributeError, IndexError, ValueError, TypeError):
        return {}


# ---------- personal mailboxes ----------

class Mailboxes:
    def __init__(self, business):
        self.business, self.database = business, business.database
        self.locks, self.locks_lock = defaultdict(threading.Lock), threading.Lock()
        for connector in CONNECTORS:
            business.connectors.register('evlet', connector)
        if business.jobs is not None:
            from .jobs import Task
            if TASK in business.jobs.tasks:
                raise ValueError(f'The task name {TASK} is reserved')
            business.jobs.tasks[TASK] = Task(TASK, self.run_job, max_attempts=1, external=True)
        # Models apps opt in to matching, with the Email fields to match.
        self.declared = {}
        for model in business.registry.models.values():
            mail = getattr(getattr(model, 'Meta', None), 'mail', None)
            if mail is None:
                continue
            if not isinstance(mail, Mail) or model in business.child_models:
                raise ValueError(f'{model.__name__}: Meta.mail is a Mail(...) on a model that is not a table row')
            for name in mail.match:
                if not isinstance(model.__declared_fields__.get(name), Email) or not getattr(model.__declared_fields__[name], 'stored', True):
                    raise ValueError(f'{model.__name__}: Meta.mail matches stored Email fields; {name} is not one')
            if model not in business.journals:
                raise ValueError(f'{model.__name__}: Meta.mail keeps messages in the record journal. Add Meta.journal = Journal().')
            self.declared[model] = mail

    def initialize(self):
        with self.database.transaction() as tx:
            for statement in (
                'CREATE TABLE IF NOT EXISTS evlet_mailboxes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE REFERENCES evlet_users(id) ON DELETE CASCADE, '
                'provider TEXT NOT NULL, address TEXT NOT NULL, settings TEXT NOT NULL, secrets BLOB, status TEXT NOT NULL, error TEXT, cursor TEXT, '
                'linking INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, attempted_at INTEGER, synced_at INTEGER)',
                'CREATE TABLE IF NOT EXISTS evlet_mail_states (hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, '
                'provider TEXT NOT NULL, secrets BLOB NOT NULL, expires INTEGER NOT NULL)',
                # Messages themselves are Journal entries; this index keeps one per record and finds a thread's records.
                'CREATE TABLE IF NOT EXISTS evlet_mail_links (model TEXT NOT NULL, record_id INTEGER NOT NULL, message_key TEXT NOT NULL, '
                'entry_id INTEGER NOT NULL, PRIMARY KEY (model, record_id, message_key, entry_id))',
                'CREATE INDEX IF NOT EXISTS evlet_mail_links_key ON evlet_mail_links(message_key)',
            ):
                tx.connection.execute(statement)

    def enabled(self):
        return bool(self.business.controls.settings[SETTING])

    def require_on(self):
        if not self.enabled():
            raise AccessError(403, OFF)

    def row(self, connection, user_id):
        return connection.execute('SELECT * FROM evlet_mailboxes WHERE user_id=?', (user_id,)).fetchone()

    def enqueue(self, tx, mailbox_id, key):
        if self.business.jobs is not None:
            self.business.jobs.enqueue(tx, TASK, {'mailbox': mailbox_id}, key=key)

    # ---------- the personal page ----------

    def state(self, user):
        with self.database._connection() as connection:
            row = self.row(connection, user.id)
            set_up = {item['id'] for item in connection.execute("SELECT id FROM evlet_connectors WHERE id IN ('mail_microsoft','mail_google')")}
        shared = next((item for item in self.business.connectors.accounts('smtp') if item['default']), None)
        mailbox = None
        if row is not None:
            settings = json.loads(row['settings'])
            mailbox = {'provider': row['provider'], 'title': TITLES[row['provider']], 'address': row['address'], 'status': row['status'],
                       'error': row['error'], 'connected_at': row['created_at'], 'synced_at': row['synced_at'],
                       **{name: settings[name] for name in OPTIONS}}
        return {'allowed': self.enabled(), 'providers': {name: item['connector'] in set_up for name, item in PROVIDERS.items()},
                'shared': shared['settings']['address'] if shared else None, 'mailbox': mailbox}

    def connect_password(self, user, body):
        """Connect any mailbox with IMAP and SMTP details and a password, after both servers accept it."""
        self.require_on()
        allowed = {'address', 'imap_host', 'imap_port', 'imap_security', 'smtp_host', 'smtp_port', 'smtp_security', 'username', 'password'}
        checked_body(body, allowed, 'Send ' + ', '.join(sorted(allowed)) + '.')
        errors = {}
        address = body.get('address')
        if not isinstance(address, str) or not ADDRESS.fullmatch(address.strip()):
            errors['address'] = 'Enter a valid email address.'
        servers = {}
        for kind, ports in PORTS.items():
            host, port = body.get(f'{kind}_host'), body.get(f'{kind}_port')
            if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.:\[\]-]{1,253}', host.strip()):
                errors[f'{kind}_host'] = 'Enter the server name, like mail.example.com.'
                continue
            if type(port) is not int or not 1 <= port <= 65535:
                errors[f'{kind}_port'] = 'Use a port from 1 to 65535.'
                continue
            host = host.strip()
            if PUBLIC_SERVERS_ONLY and _loopback(host):
                errors[f'{kind}_host'] = PUBLIC_ONLY
                continue
            security = body.get(f'{kind}_security', security_for(host, port))
            if security not in ('ssl', 'starttls', 'none'):
                errors[f'{kind}_security'] = 'Choose SSL/TLS, STARTTLS or None.'
            elif security == 'none' and PUBLIC_SERVERS_ONLY:
                errors[f'{kind}_security'] = 'Choose STARTTLS or SSL/TLS. A personal mailbox needs an encrypted connection.'
            elif security == 'none' and not _loopback(host):
                errors[f'{kind}_security'] = 'Without encryption the mail server must be on this machine. Choose STARTTLS or SSL/TLS.'
            elif (security != 'none' or PUBLIC_SERVERS_ONLY) and port not in ports:
                *head, last = (str(item) for item in ports)
                errors[f'{kind}_port'] = f"Use port {', '.join(head)} or {last}."
            servers[kind] = {'host': host, 'port': port, 'security': security}
        username, password = body.get('username') or None, body.get('password')
        if username is not None and (not isinstance(username, str) or len(username) > 254 or any(ord(c) < 32 for c in username)):
            errors['username'] = 'Use at most 254 characters on one line.'
        if not isinstance(password, str) or not password.strip() or len(password) > 4096:
            errors['password'] = 'Enter the password or app password.'
        if errors:
            raise ValidationError(errors)
        address = address.strip().lower()
        values = {'address': address, 'username': username, 'password': password}
        self.business.connectors.throttle(user)
        for kind, check in (('imap', check_imap), ('smtp', check_smtp)):
            error = run_with_timeout(check, {**values, **servers[kind], 'public': PUBLIC_SERVERS_ONLY}, self.business.connectors.slots)
            if error is not None:
                raise ValidationError({'password' if error == REJECTED else f'{kind}_host': scrub(error, [password])})
        settings = {'imap': servers['imap'], 'smtp': servers['smtp'], 'username': username, **DEFAULT_OPTIONS}
        self.store(user, 'imap', address, settings, {'password': password})
        return self.state(user)

    def store(self, user, provider, address, settings, secret):
        with self.database.transaction() as tx:
            old = self.row(tx.connection, user.id)
            if old is not None:
                settings.update({name: json.loads(old['settings'])[name] for name in OPTIONS})
            tx.connection.execute('DELETE FROM evlet_mailboxes WHERE user_id=?', (user.id,))
            tx.connection.execute('INSERT INTO evlet_mailboxes(user_id,provider,address,settings,secrets,status,linking,created_at) VALUES (?,?,?,?,?,?,?,?)',
                                  (user.id, provider, address, json.dumps(settings, sort_keys=True), seal(self.business, tx.connection, secret, mailbox_context(user.id)),
                                   'connected', int(settings['link_replies']), int(time.time())))
            audit(tx.connection, user.username, AREA, f'{user.username} · own mailbox', f'Connected ({TITLES[provider]})')
            mailbox_id = self.row(tx.connection, user.id)['id']
            if self.business.jobs is not None:
                # Sync now, then every connected mailbox every five minutes.
                self.enqueue(tx, mailbox_id, f'mail:{mailbox_id}:first')
                tx.connection.execute('INSERT INTO evlet_schedules(name,task,payload,actor,model,record_id,interval,next_run) VALUES (?,?,?,?,?,?,?,?) '
                                      'ON CONFLICT(name) DO NOTHING', (TASK, TASK, '{}', None, None, None, EVERY, int(time.time()) + EVERY))

    def change(self, user, body):
        if not isinstance(body, dict) or not body or any(name not in OPTIONS or body[name] not in OPTIONS[name] or type(body[name]) is not type(OPTIONS[name][0])
                                                          for name in body):
            raise ValidationError({'_form': 'Send send_from (mine or shared), link_replies or only_crm (true or false).'})
        with self.database.transaction() as tx:
            row = self.row(tx.connection, user.id)
            if row is None:
                raise AccessError(404, 'Connect a mailbox first.')
            settings = json.loads(row['settings'])
            before = {name: settings[name] for name in OPTIONS}
            settings.update(body)
            after = {name: settings[name] for name in OPTIONS}
            if before != after:
                tx.connection.execute('UPDATE evlet_mailboxes SET settings=?, linking=? WHERE id=?',
                                      (json.dumps(settings, sort_keys=True), int(settings['link_replies']), row['id']))
                audit(tx.connection, user.username, AREA, f'{user.username} · own mailbox', 'Settings changed', before, after)
        return self.state(user)

    def disconnect(self, user):
        """Delete the saved sign-in and stop syncing; revoke the sign-in at the provider when it can. Linked messages stay."""
        with self.database._connection() as connection:
            found = self.row(connection, user.id)
        # Waits for a token refresh of this mailbox to finish, so it can't write the tokens back.
        with self.lock(found['id'] if found else None):
            with self.database.transaction() as tx:
                row = self.row(tx.connection, user.id)
                tx.connection.execute('DELETE FROM evlet_mail_states WHERE user_id=?', (user.id,))
                if row is None:
                    raise AccessError(404, 'No mailbox is connected.')
                tx.connection.execute('DELETE FROM evlet_mailboxes WHERE id=?', (row['id'],))
                audit(tx.connection, user.username, AREA, f'{user.username} · own mailbox', 'Disconnected; saved sign-in deleted')
        revoke = json.loads(row['settings']).get('revoke_url')
        if revoke and row['secrets'] is not None:
            try:
                post_form(revoke, {'token': unseal(self.business, row['secrets'], mailbox_context(user.id))['refresh_token']})
            except Exception as exc:  # Best effort: the tokens are already deleted here.
                logger.info('Mailbox %s: revoking at the provider failed with %s', row['id'], type(exc).__name__)
        return self.state(user)

    def sync_now(self, user):
        self.require_on()
        self.business.connectors.throttle(user)
        with self.database.transaction() as tx:
            row = self.row(tx.connection, user.id)
            if row is None:
                raise AccessError(404, 'Connect a mailbox first.')
            if self.business.jobs is not None:
                self.enqueue(tx, row['id'], f"mail:{row['id']}:now:{int(time.time()) // 60}")
                return {**self.state(user), 'queued': True}
        return {**self.state(user), 'copied': self.sync(row['id'])}

    # ---------- OAuth ----------

    def provider(self, name):
        """The provider's addresses and client, from its connector over the defaults."""
        if name not in PROVIDERS:
            raise ValidationError({'provider': 'Choose microsoft or google.'})
        found = PROVIDERS[name]
        values = self.business.connectors.values(found['connector'])
        if not values or not values.get('client_id') or not values.get('client_secret'):
            raise AccessError(409, f"{found['title']} sign-in isn't set up yet. Ask an admin to add it in Controls, under Connectors.")
        config = {**found, **{key: value for key, value in values.items() if value not in (None, '')}}
        for key in ('authorize_url', 'token_url'):
            config[key] = config[key].replace('{tenant}', quote(str(config.get('tenant') or 'organizations'), safe=''))
        return config

    def start(self, user, body, origin):
        """POST .../oauth: the provider's sign-in address with a single-use state and a PKCE challenge."""
        self.require_on()
        checked_body(body, {'provider'}, 'Choose microsoft or google.', exact=True, field='provider')
        config = self.provider(body['provider'])
        try:
            authorize = safe_url(config['authorize_url'], fetch=False)[0]
        except ConnectionFailed as exc:
            raise ValidationError({'provider': str(exc)}) from None
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        redirect = origin + '/_/mail/callback'
        now = int(time.time())
        with self.database.transaction() as tx:
            tx.connection.execute('DELETE FROM evlet_mail_states WHERE expires<=? OR user_id=?', (now, user.id))
            hashed = digest(state)
            tx.connection.execute('INSERT INTO evlet_mail_states(hash,user_id,provider,secrets,expires) VALUES (?,?,?,?,?)',
                                  (hashed, user.id, body['provider'], seal(self.business, tx.connection, {'verifier': verifier, 'redirect': redirect}, state_context(hashed)),
                                   now + STATE_SECONDS))
        params = {'client_id': config['client_id'], 'response_type': 'code', 'redirect_uri': redirect, 'scope': config['scope'],
                  'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
                  **({'access_type': 'offline', 'prompt': 'consent'} if body['provider'] == 'google' else {'prompt': 'select_account'})}
        separator = '&' if authorize.query else '?'
        return {'url': config['authorize_url'] + separator + urlencode(params)}

    def callback(self, scope, raw):
        """/_/mail/callback. GET (from the provider, without the session cookie): a page that posts the code
        and state back from this origin. POST (with the cookie): checks the session, the origin and the
        state, finishes the sign-in and redirects to the email settings page."""
        from .business import Download
        if self.business.assistants.origin(scope) is None:
            raise AccessError(404, NOT_FOUND)
        headers = dict(scope.get('headers', []))
        if scope['method'] == 'GET' and scope['path'] == '/_/mail/continue.js':
            return 200, Download(b'document.forms[0].submit();\n'), [(b'content-type', b'text/javascript; charset=utf-8')]
        if scope['path'] != '/_/mail/callback' or scope['method'] not in ('GET', 'POST'):
            raise AccessError(404 if scope['path'] != '/_/mail/callback' else 405, NOT_FOUND)
        source = scope.get('query_string', b'') if scope['method'] == 'GET' else raw
        try:
            query = parse_qs(source.decode('ascii'), keep_blank_values=True, max_num_fields=10)
        except (ValueError, UnicodeError):
            query = {}
        query = {key: values[0][:2000] for key, values in query.items() if key in ('code', 'state', 'error') and len(values) == 1}
        if scope['method'] == 'GET':
            fields = ''.join(f'<input type="hidden" name="{key}" value="{escape(value)}">' for key, value in query.items())
            return 200, Download(CONTINUE.format(fields=fields).encode()), [
                (b'content-type', b'text/html; charset=utf-8'), (b'content-security-policy', CONTINUE_CSP), (b'referrer-policy', b'no-referrer')]
        back = lambda result: (302, Download(b''), [(b'location', f'/settings/email?mail={result}'.encode())])
        # The form comes from this origin; with the state bound to the person, that replaces the CSRF header.
        sent = headers.get(b'origin', b'').decode('latin1')
        if sent not in self.business.auth.origins or headers.get(b'sec-fetch-site') == b'cross-site':
            raise AccessError(403, 'Unrecognized origin')
        try:
            user, _ = self.business.auth.session({**scope, 'method': 'GET'})
        except AccessError:
            return back('signin')
        now = int(time.time())
        hashed = digest(query.get('state', ''))
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_mail_states WHERE hash=?', (hashed,)).fetchone()
            tx.connection.execute('DELETE FROM evlet_mail_states WHERE expires<=?', (now,))
            if row is None or row['user_id'] != user.id or row['expires'] <= now:
                return back('expired')
            tx.connection.execute('DELETE FROM evlet_mail_states WHERE hash=?', (hashed,))
        if 'error' in query or not query.get('code'):
            return back('denied')
        if not self.enabled():
            return back('off')
        try:
            saved = unseal(self.business, row['secrets'], state_context(hashed))
            config = self.provider(row['provider'])
            status, answer = post_form(config['token_url'], {
                'grant_type': 'authorization_code', 'code': query['code'], 'redirect_uri': saved['redirect'],
                'client_id': config['client_id'], 'client_secret': config['client_secret'], 'code_verifier': saved['verifier']})
            identity = claims(answer.get('id_token'))
            address = str(identity.get('email') or identity.get('preferred_username') or '').strip().lower()
            audience = identity.get('aud')
            if status != 200 or not isinstance(answer.get('access_token'), str) or not isinstance(answer.get('refresh_token'), str) \
                    or not ADDRESS.fullmatch(address) or config['client_id'] not in (audience if isinstance(audience, list) else [audience]):
                return back('failed')
            servers = {}
            for kind in ('imap', 'smtp'):
                host, port = str(config[f'{kind}_host']), int(config[f'{kind}_port'])
                servers[kind] = {'host': host, 'port': port, 'security': security_for(host, port)}
                if servers[kind]['security'] != 'none' and port not in PORTS[kind]:
                    return back('failed')
            secret = {'access_token': answer['access_token'], 'refresh_token': answer['refresh_token'],
                      'expires': now + min(max(int(answer.get('expires_in') or 3600), 60), 86400)}
            if run_with_timeout(check_imap, {'address': address, 'token': secret['access_token'], **servers['imap']}, self.business.connectors.slots) is not None:
                return back('mailbox')
            # The servers and token addresses are fixed now, so a later change to the connector can't send this person's tokens elsewhere.
            settings = {**servers, 'token_url': config['token_url'], 'revoke_url': config.get('revoke_url') or None, **DEFAULT_OPTIONS}
            self.store(user, row['provider'], address, settings, secret)
        except (ConnectionFailed, SecretKeyError, AccessError, ValidationError, ValueError, TypeError):
            return back('failed')
        return back('connected')

    def lock(self, key):
        with self.locks_lock:
            return self.locks[key]

    def token(self, mailbox_id):
        """A current access token, refreshed when it is about to expire. Raises Ended only when the provider
        says the sign-in itself is no longer valid (invalid_grant); anything else can be tried again."""
        with self.lock(mailbox_id):
            with self.database._connection() as connection:
                row = connection.execute('SELECT * FROM evlet_mailboxes WHERE id=?', (mailbox_id,)).fetchone()
            if row is None or row['secrets'] is None:
                raise Ended('Connect your mailbox again.')
            secret = unseal(self.business, row['secrets'], mailbox_context(row['user_id']))
            if secret['expires'] > time.time() + 60:
                return secret['access_token']
            title = TITLES[row['provider']]
            try:
                config = self.provider(row['provider'])
            except AccessError:
                raise ConnectionFailed(f"{title} sign-in isn't set up in this workspace any more. Ask an admin.") from None
            form = {'grant_type': 'refresh_token', 'refresh_token': secret['refresh_token'], 'client_id': config['client_id'], 'client_secret': config['client_secret']}
            status, answer = post_form(json.loads(row['settings'])['token_url'], form)
            if status != 200 or not isinstance(answer.get('access_token'), str):
                if answer.get('error') != 'invalid_grant':
                    raise ConnectionFailed(f"{title} didn't accept this workspace's sign-in settings. Ask an admin to check them in Controls."
                                           if answer.get('error') in ('invalid_client', 'unauthorized_client') else UNREACHABLE)
                # Another process may have refreshed it first with the same refresh token.
                with self.database._connection() as connection:
                    now = connection.execute('SELECT secrets FROM evlet_mailboxes WHERE id=?', (mailbox_id,)).fetchone()
                latest = None if now is None or now['secrets'] is None else unseal(self.business, now['secrets'], mailbox_context(row['user_id']))
                if latest and latest['refresh_token'] != secret['refresh_token']:
                    return latest['access_token']
                raise Ended(f'Your {title} sign-in ended. Connect your mailbox again.')
            fresh = {'access_token': answer['access_token'], 'refresh_token': answer.get('refresh_token') or secret['refresh_token'],
                     'expires': int(time.time()) + min(max(int(answer.get('expires_in') or 3600), 60), 86400)}
            with self.database.transaction() as tx:
                # Disconnected meanwhile: the row is gone, so nothing is written back.
                tx.connection.execute('UPDATE evlet_mailboxes SET secrets=? WHERE id=? AND secrets IS NOT NULL',
                                      (seal(self.business, tx.connection, fresh, mailbox_context(row['user_id'])), mailbox_id))
            return fresh['access_token']

    def values(self, row, kind):
        """Connection values for the mailbox's IMAP or SMTP server, with its password or a current token."""
        if row['secrets'] is None:
            raise Ended('Connect your mailbox again.')
        settings = json.loads(row['settings'])
        try:
            secret = unseal(self.business, row['secrets'], mailbox_context(row['user_id']))
        except SecretKeyError:
            raise Ended('The saved sign-in could not be read. Connect your mailbox again.') from None
        values = {'address': row['address'], **settings[kind]}
        if row['provider'] == 'imap':
            # Typed in by the person, so checked again on every connection: public servers only.
            return {**values, 'username': settings.get('username'), 'password': secret['password'], 'public': PUBLIC_SERVERS_ONLY}
        return {**values, 'token': self.token(row['id'])}

    def end(self, mailbox_id, message):
        """Delete a sign-in that stopped working; syncing stops until the person connects again."""
        with self.database.transaction() as tx:
            tx.connection.execute("UPDATE evlet_mailboxes SET status='reconnect', error=?, secrets=NULL WHERE id=?", (message, mailbox_id))

    def failed(self, mailbox_id, message):
        with self.database.transaction() as tx:
            tx.connection.execute("UPDATE evlet_mailboxes SET status='error', error=? WHERE id=? AND status IN ('connected','error')", (message, mailbox_id))

    # ---------- sending ----------

    def send_mail(self, user, to, subject, text, *, html=None, record=None):
        """Send as the person from their own mailbox, or from the shared account when they chose it or have none.

        With record=(Model, id), which the person must be able to update, the message is linked to that
        record, so replies to it link there too. Returns the Message-ID.
        """
        recipients = [to] if isinstance(to, str) else list(to)
        if not recipients or len(recipients) > 50 or any(not isinstance(item, str) or not ADDRESS.fullmatch(item) for item in recipients):
            raise ValueError('Send to between 1 and 50 valid email addresses')
        inline = ()
        if record is not None:
            with self.database.transaction() as tx:
                self.business.policy.record(tx, user, record[0], record[1], 'update')
                if html is not None:
                    # Rich text images of the record go inline (cid:), never as links.
                    from .images import for_email
                    html, inline = for_email(self.business, tx, record[0], record[1], html)
        with self.database._connection() as connection:
            row = self.row(connection, user.id)
        own = (row is not None and self.enabled() and row['status'] in ('connected', 'error')
               and json.loads(row['settings'])['send_from'] == 'mine')
        if own:
            try:
                values = self.values(row, 'smtp')
            except Ended as exc:
                self.end(row['id'], str(exc))
                raise ConnectionFailed(str(exc)) from None
            message_id = Connectors.deliver(values, recipients, subject, text, html=html, inline=inline)
        else:
            message_id = self.business.connectors.send_mail(recipients, subject, text, html=html, inline=inline)
        if record is not None:
            with self.database.transaction() as tx:
                link_message(self.business, tx, user, record[0], record[1], key_of(message_id),
                             {'text': text[:10000] or subject[:200], 'subject': subject[:200], 'party': ', '.join(recipients)[:200], 'direction': 'out'})
        return message_id

    # ---------- syncing ----------

    def run_job(self, context, payload):
        if 'mailbox' in payload:
            self.sync(int(payload['mailbox']))
            return
        # Only mailboxes that read mail, of active people, the longest unattempted first, so failing ones can't hold the others back.
        with self.database._connection() as connection:
            due = [row[0] for row in connection.execute(
                "SELECT m.id FROM evlet_mailboxes m JOIN evlet_users u ON u.id=m.user_id WHERE m.status IN ('connected','error') "
                'AND m.linking=1 AND u.active=1 ORDER BY COALESCE(m.attempted_at,0), m.id LIMIT ?', (PER_RUN,))]
        for mailbox_id in due:
            self.sync(mailbox_id)

    def sync(self, mailbox_id):
        """Copy in one batch of new messages that match records the person can read. Returns how many were copied.

        Never raises: a failure is recorded on the mailbox, and only exception types and mailbox ids are logged,
        since a message from anyone could reach an exception's text.
        """
        try:
            return self._sync(mailbox_id)
        except Exception as exc:
            logger.warning('Mailbox %s sync failed with %s', mailbox_id, type(exc).__name__)
            try:
                self.failed(mailbox_id, FAILED)
            except Exception as again:
                logger.warning('Mailbox %s status could not be saved: %s', mailbox_id, type(again).__name__)
            return 0

    def _sync(self, mailbox_id):
        if not self.enabled():
            return 0
        with self.database._connection() as connection:
            row = connection.execute('SELECT m.*, u.username, u.role, u.active FROM evlet_mailboxes m JOIN evlet_users u ON u.id=m.user_id WHERE m.id=?',
                                     (mailbox_id,)).fetchone()
        if row is None or not row['active'] or row['status'] not in ('connected', 'error') or not row['linking']:
            return 0
        with self.database.transaction() as tx:
            tx.connection.execute('UPDATE evlet_mailboxes SET attempted_at=? WHERE id=?', (int(time.time()), row['id']))
        settings = json.loads(row['settings'])
        user = User(row['user_id'], row['username'], row['role'])
        deadline = time.monotonic() + RUN_SECONDS
        client = None
        try:
            client = open_imap({**self.values(row, 'imap'), 'deadline': deadline})
            try:
                copied, cursor, more = self.read(client, row, settings, user, deadline)
            finally:
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    client.shutdown()
        except Ended as exc:
            self.end(row['id'], str(exc))
            return 0
        except (ConnectionFailed, OSError, imaplib.IMAP4.error) as exc:
            if isinstance(exc, ConnectionFailed) and str(exc) == REJECTED:
                self.end(row['id'], 'The mail server rejected the sign-in. Connect your mailbox again.')
            else:
                self.failed(row['id'], str(exc) if isinstance(exc, ConnectionFailed) else UNREACHABLE)
            return 0
        finally:
            if client is not None and client.watchdog is not None:
                client.watchdog.cancel()
        with self.database.transaction() as tx:
            # Disconnected (or connected again) while reading: nothing is kept.
            if tx.connection.execute("SELECT 1 FROM evlet_mailboxes WHERE id=? AND status IN ('connected','error')", (row['id'],)).fetchone() is None:
                return 0
            for key, data, targets in copied:
                for model, record_id, field in targets:
                    link_message(self.business, tx, user, model, record_id, key, data, field)
            tx.connection.execute("UPDATE evlet_mailboxes SET cursor=?, synced_at=?, status='connected', error=NULL WHERE id=?",
                                  (json.dumps(cursor), int(time.time()), row['id']))
            if more:
                self.enqueue(tx, row['id'], f"mail:{row['id']}:{cursor['validity']}:{cursor['uid']}")
        return len(copied)

    def read(self, client, row, settings, user, deadline):
        """Read headers of the next batch, match them, and fetch the text of matching messages only.

        A message that can't be parsed is skipped; the cursor still moves past it.
        """
        status, _ = client.select('INBOX', readonly=True)
        if status != 'OK':
            raise ConnectionFailed('Signed in, but the inbox could not be opened.')
        validity = int((client.response('UIDVALIDITY')[1] or [b'0'])[0] or 0)
        next_uid = (client.response('UIDNEXT')[1] or [None])[0]
        cursor = json.loads(row['cursor']) if row['cursor'] else None
        if cursor is None or cursor['validity'] != validity:
            since = time.gmtime(time.time() - FIRST_DAYS * 86400)
            found = client.uid('SEARCH', None, 'SINCE', f'{since.tm_mday:02d}-{MONTHS[since.tm_mon - 1]}-{since.tm_year}')
            last = 0
        else:
            last = cursor['uid']
            found = client.uid('SEARCH', None, 'UID', f'{last + 1}:*')
        if found[0] != 'OK':
            raise ConnectionFailed(UNREACHABLE)
        uids = sorted(uid for uid in (int(item) for item in b' '.join(part for part in found[1] if part).split()) if uid > last)
        batch, more = uids[:BATCH], len(uids) > BATCH
        if not batch:
            if cursor is None or cursor['validity'] != validity:
                # Nothing in the first days: start after the newest message, or search by date again next time.
                return [], {'validity': validity, 'uid': int(next_uid) - 1} if next_uid else None, False
            return [], cursor, False
        headers = self.fetch(client, batch, '(BODY.PEEK[HEADER.FIELDS (FROM TO CC MESSAGE-ID IN-REPLY-TO REFERENCES SUBJECT)])')
        own, parsed = row['address'], {}
        for uid, raw in headers.items():
            try:
                message = email.message_from_bytes(raw[:HEADER_SIZE], policy=email_policy.default)
                parties = [address for address in addresses(message, 'From', 'To', 'Cc') if address != own]
                ids = message_ids(message, 'Message-ID')
                parsed[uid] = (parties, addresses(message, 'From')[:1] == [own], ids[0] if ids else None,
                               message_ids(message, 'In-Reply-To', 'References'), str(message.get('Subject', '') or '').replace('\x00', '').strip()[:200])
            except Exception as exc:
                logger.info('Mailbox %s skipped a message it could not read (%s)', row['id'], type(exc).__name__)
        matched = self.match(user, {address for parties, *_ in parsed.values() for address in parties}, settings['only_crm'])
        threads = self.threads(user, {item for *_, references, _ in parsed.values() for item in references})
        copied, done = [], batch[-1]
        for index, uid in enumerate(batch):
            if time.monotonic() > deadline - 2 * TIMEOUT:
                done, more = batch[index - 1] if index else last, True
                break
            if uid not in parsed:
                continue
            parties, outgoing, message_id, references, subject = parsed[uid]
            targets = [target for address in parties for target in matched.get(address, ())]
            # A reply to a message this person linked to a record links there, even from someone not on any record yet.
            targets += [target for reference in references for target in threads.get(key_of(reference), ())]
            if not targets:
                continue  # Not linked to any record: nothing of it is kept.
            targets = list(dict.fromkeys(targets))[:10]
            body = self.fetch(client, [uid], f'(BODY.PEEK[]<0.{MAX_SIZE}>)').get(uid)
            try:
                text = plain_text(email.message_from_bytes(body[:MAX_SIZE], policy=email_policy.default)) if body else ''
            except Exception:
                text = ''
            party = next((address for address in parties if matched.get(address)), parties[0] if parties else '')
            key = key_of(message_id) if message_id else digest(f"uid:{row['id']}:{validity}:{uid}")
            copied.append((key, {'text': text or subject or '(No text)', 'subject': subject, 'party': party[:200],
                                 'direction': 'out' if outgoing else 'in'}, targets))
        return copied, {'validity': validity, 'uid': done}, more

    @staticmethod
    def fetch(client, uids, parts):
        """UID FETCH: {uid: bytes}."""
        status, data = client.uid('FETCH', ','.join(str(uid) for uid in uids), parts)
        if status != 'OK':
            raise ConnectionFailed(UNREACHABLE)
        found = {}
        for index, item in enumerate(data):
            if not isinstance(item, tuple):
                continue
            meta = item[0] + (data[index + 1] if index + 1 < len(data) and isinstance(data[index + 1], bytes) else b'')
            uid = re.search(rb'UID (\d+)', meta)
            if uid:
                found[int(uid[1])] = item[1]
        return found

    def models(self, user, only_crm):
        """Opted-in models and their matched Email fields this person can read."""
        policy = self.business.policy
        for model, mail in self.declared.items():
            if (only_crm and mail.kind == 'other') or not policy.allows(user, model, 'read'):
                continue
            readable = policy.fields(user, model)
            fields = [name for name in mail.match if name in readable]
            if fields:
                yield model, fields

    def match(self, user, wanted, only_crm):
        """{address: [(Model, id, field), ...]} for records this person can read (row scope and field rules applied)."""
        found = {}
        wanted = sorted(wanted)
        with self.database._connection() as connection:
            for model, fields in self.models(user, only_crm):
                where, values = self.business.policy.scope_sql(user, model)
                columns = [f'record.{identifier(name)}' for name in fields]
                table = identifier(self.business.registry.name_for(model))
                size = max(1, 400 // len(fields))  # well under every database's limit on SQL values
                for start in range(0, len(wanted), size):
                    part = wanted[start:start + size]
                    marks = ', '.join('?' for _ in part)
                    condition = ' OR '.join(f'lower({column}) IN ({marks})' for column in columns)
                    sql = (f'SELECT record.id, {", ".join(columns)} FROM {table} AS record{where} {"AND" if where else "WHERE"} ({condition}) '
                           f'ORDER BY record.id LIMIT 500')
                    for row in connection.execute(sql, [*values, *(part * len(fields))]).fetchall():
                        for name, value in zip(fields, tuple(row)[1:]):
                            if isinstance(value, str) and value.lower() in part:
                                found.setdefault(value.lower(), []).append((model, row[0], name))
        return {address: list(dict.fromkeys(targets))[:10] for address, targets in found.items()}

    def threads(self, user, references):
        """{message key: [(Model, id, None)]}: records this person put an earlier message of the thread on, and can still read.
        Who put it there is the actor of its Journal entry, so someone else's links never pull a message along."""
        if not references:
            return {}
        keys = [key_of(item) for item in references]
        found = {}
        with self.database.transaction() as tx:
            rows = tx.connection.execute('SELECT DISTINCT l.message_key, l.model, l.record_id FROM evlet_mail_links l JOIN evlet_journal j ON j.id=l.entry_id '
                                         f'WHERE j.actor=? AND l.message_key IN ({", ".join("?" for _ in keys)})', [str(user.id), *keys]).fetchall()
            by_model = {}
            for row in rows:
                model = self.business.registry.models.get(row['model'])
                if model is not None:
                    by_model.setdefault(model, []).append((row['message_key'], row['record_id']))
            for model, items in by_model.items():
                visible = self.business.policy.visible(tx, user, model, [record_id for _, record_id in items])
                for key, record_id in items:
                    if record_id in visible:
                        found.setdefault(key, []).append((model, record_id, None))
        return found

    # ---------- HTTP ----------

    def api(self, scope, body, user):
        """/api/_settings/me/mailbox: a person's own mailbox. Signed-in sessions only."""
        path, method = scope['path'].removeprefix('/api/_settings/me/mailbox'), scope['method']
        if self.business.settings.portal(scope, user):
            raise AccessError(404, NOT_FOUND)  # Personal mailboxes are for desk users.
        try:
            if path == '' and method == 'GET':
                return 200, self.state(user), []
            if path == '' and method == 'POST':
                return 200, self.connect_password(user, body), []
            if path == '' and method == 'PATCH':
                return 200, self.change(user, body), []
            if path == '' and method == 'DELETE':
                return 200, self.disconnect(user), []
            if path == '/oauth' and method == 'POST':
                origin = self.business.assistants.origin(scope)
                if origin is None:
                    raise AccessError(404, NOT_FOUND)
                return 200, self.start(user, body, origin), []
            if path == '/sync' and method == 'POST':
                return 200, self.sync_now(user), []
        except SecretKeyError as exc:
            raise AccessError(503, str(exc)) from None
        raise AccessError(404, NOT_FOUND)
