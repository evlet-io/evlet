"""Personal mailboxes: people connect their own mail to send from records as themselves and see
replies on the records they can read.

Microsoft 365 and Google sign in with OAuth (authorization code with PKCE); any other mailbox uses
IMAP and SMTP with an app password. Tokens and passwords are sealed with the instance secret key.
A sync job copies in only messages whose addresses match records the person can read; nothing
else is stored, not even headers. No route lets an admin read a mailbox or its credentials.
"""

import base64
import email
from email import policy as email_policy
from email.utils import getaddresses
import hashlib
from html.parser import HTMLParser
import http.client
import imaplib
import json
import logging
import re
import secrets
import socket
import ssl
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .auth import AccessError, User
from .connectors import (ADDRESS, NOT_ALLOWED, REJECTED, TIMEOUT, UNREACHABLE, ConnectionFailed, Connector, ConnectorField,
                         Connectors, SecretKeyError, _loopback, check_imap, check_smtp, mailbox_context, open_imap, run_with_timeout,
                         safe_address, scrub, state_context)
from .controls import NOT_FOUND, audit
from .fields import Email
from .models import ValidationError, identifier

logger = logging.getLogger(__name__)
SETTING = 'mail.personal'
TASK = 'evlet_mail_sync'
EVERY = 300           # seconds between scheduled syncs
BATCH = 50            # messages read per mailbox per run
PER_RUN = 20          # mailboxes per scheduled run
FIRST_DAYS = 30       # a new mailbox starts with the last 30 days
MAX_SIZE = 1_000_000  # larger messages are linked without their text
STATE_SECONDS = 600
MATCHED = ('Customer', 'Contact', 'Lead')
OFF = "Your workspace doesn't let people connect their own mailbox. Ask an admin to turn it on in Controls, under Email."
AREA = 'Email'
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
OPTIONS = {'send_from': ('mine', 'shared'), 'link_replies': (True, False), 'only_crm': (True, False)}
DEFAULT_OPTIONS = {'send_from': 'mine', 'link_replies': True, 'only_crm': True}
PORTS = {'imap': (143, 993), 'smtp': (25, 465, 587)}  # unless the server is on this machine

# Provider defaults. The connector can replace each one; tests use a local fake provider.
PROVIDERS = {
    'microsoft': {'title': 'Microsoft 365', 'connector': 'mail_microsoft',
                  'authorize_url': 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize',
                  'token_url': 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',
                  'scope': 'openid email offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send',
                  'imap_host': 'outlook.office365.com', 'imap_port': 993, 'smtp_host': 'smtp.office365.com', 'smtp_port': 587},
    'google': {'title': 'Google Workspace', 'connector': 'mail_google',
               'authorize_url': 'https://accounts.google.com/o/oauth2/v2/auth', 'token_url': 'https://oauth2.googleapis.com/token',
               'scope': 'openid email https://mail.google.com/',
               'imap_host': 'imap.gmail.com', 'imap_port': 993, 'smtp_host': 'smtp.gmail.com', 'smtp_port': 465},
}
TITLES = {'imap': 'Other mailbox', **{name: item['title'] for name, item in PROVIDERS.items()}}


def _provider_fields(tenant):
    standard = 'Leave empty for the standard address.'
    return (ConnectorField('client_id', 'Client ID', identifies=True), ConnectorField('client_secret', 'Client secret', 'secret'),
            *((ConnectorField('tenant', 'Directory (tenant) ID', default='organizations', required=False),) if tenant else ()),
            ConnectorField('authorize_url', 'Sign-in address', required=False, help=standard),
            ConnectorField('token_url', 'Token address', required=False, help=standard),
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


# ---------- seams for the restack ----------
# evlet/secrets.py (Settings) seals secrets with the same key format; the Journal stores messages.

def seal(business, data, context):
    return business.connectors.key.encrypt(json.dumps(data, sort_keys=True).encode(), context)


def unseal(business, blob, context):
    return json.loads(business.connectors.key.decrypt(blob, context))


def link_message(tx, user, model_name, record_id, key, data):
    """Store one copied message on a record, once per record (a Journal `message` entry after the restack)."""
    tx.connection.execute('INSERT INTO evlet_mail_links(model,record_id,message_key,user_id,at,data) VALUES (?,?,?,?,?,?) '
                          'ON CONFLICT(model,record_id,message_key) DO NOTHING',
                          (model_name, record_id, key, user.id, int(time.time()), json.dumps(data, sort_keys=True)))


# ---------- small helpers ----------

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
    try:
        part = message.get_body(preferencelist=('plain', 'html'))
        text = part.get_content() if part is not None else ''
        if part is not None and part.get_content_subtype() == 'html':
            parser = _Text()
            parser.feed(text)
            text = ''.join(parser.parts)
    except (LookupError, ValueError, AssertionError):
        text = ''
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text.replace('\x00', '').replace('\r', '')).strip()
    return text[:10000]


def addresses(message, *names):
    found = []
    for name in names:
        try:
            values = [str(value) for value in message.get_all(name, [])]
        except (ValueError, TypeError, IndexError):
            continue
        for _, address in getaddresses(values):
            address = address.strip().lower()
            if ADDRESS.fullmatch(address) and address not in found:
                found.append(address)
    return found[:50]


def message_ids(message, *names):
    found = []
    for name in names:
        try:
            found += re.findall(r'<[^<>\s]{1,250}>', ' '.join(str(value) for value in message.get_all(name, [])))
        except (ValueError, TypeError, IndexError):
            continue
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
        for connector in CONNECTORS:
            business.connectors.register('evlet', connector)
        if business.jobs is not None:
            from .jobs import Task
            if TASK in business.jobs.tasks:
                raise ValueError(f'The task name {TASK} is reserved')
            business.jobs.tasks[TASK] = Task(TASK, self.run_job, max_attempts=1, external=True)

    def initialize(self):
        with self.database.transaction() as tx:
            for statement in (
                'CREATE TABLE IF NOT EXISTS evlet_mailboxes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE REFERENCES evlet_users(id) ON DELETE CASCADE, '
                'provider TEXT NOT NULL, address TEXT NOT NULL, settings TEXT NOT NULL, secrets BLOB, status TEXT NOT NULL, error TEXT, cursor TEXT, created_at INTEGER NOT NULL, synced_at INTEGER)',
                'CREATE TABLE IF NOT EXISTS evlet_mail_states (hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, '
                'provider TEXT NOT NULL, secrets BLOB NOT NULL, expires INTEGER NOT NULL)',
                'CREATE TABLE IF NOT EXISTS evlet_mail_links (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, record_id INTEGER NOT NULL, '
                'message_key TEXT NOT NULL, user_id INTEGER, at INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE (model, record_id, message_key))',
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
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValidationError({'_form': 'Send ' + ', '.join(sorted(allowed)) + '.'})
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
            security = body.get(f'{kind}_security', security_for(host, port))
            if security not in ('ssl', 'starttls', 'none'):
                errors[f'{kind}_security'] = 'Choose SSL/TLS, STARTTLS or None.'
            elif security == 'none' and not _loopback(host):
                errors[f'{kind}_security'] = 'Without encryption the mail server must be on this machine. Choose STARTTLS or SSL/TLS.'
            elif security != 'none' and port not in ports:
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
            error = run_with_timeout(check, {**values, **servers[kind]}, self.business.connectors.slots)
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
            tx.connection.execute('INSERT INTO evlet_mailboxes(user_id,provider,address,settings,secrets,status,created_at) VALUES (?,?,?,?,?,?,?)',
                                  (user.id, provider, address, json.dumps(settings, sort_keys=True), seal(self.business, secret, mailbox_context(user.id)),
                                   'connected', int(time.time())))
            audit(tx.connection, user.username, AREA, f'{user.username} · own mailbox', f'Connected ({TITLES[provider]})')
            mailbox_id = self.row(tx.connection, user.id)['id']
            if self.business.jobs is not None:
                # Sync now, then every connected mailbox every five minutes.
                self.business.jobs.enqueue(tx, TASK, {'mailbox': mailbox_id})
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
                tx.connection.execute('UPDATE evlet_mailboxes SET settings=? WHERE id=?', (json.dumps(settings, sort_keys=True), row['id']))
                audit(tx.connection, user.username, AREA, f'{user.username} · own mailbox', 'Settings changed', before, after)
        return self.state(user)

    def disconnect(self, user):
        """Delete the saved sign-in and stop syncing. Messages already linked to records stay there."""
        with self.database.transaction() as tx:
            row = self.row(tx.connection, user.id)
            tx.connection.execute('DELETE FROM evlet_mail_states WHERE user_id=?', (user.id,))
            if row is None:
                raise AccessError(404, 'No mailbox is connected.')
            tx.connection.execute('DELETE FROM evlet_mailboxes WHERE id=?', (row['id'],))
            audit(tx.connection, user.username, AREA, f'{user.username} · own mailbox', 'Disconnected; saved sign-in deleted')
        return self.state(user)

    def sync_now(self, user):
        self.require_on()
        with self.database.transaction() as tx:
            row = self.row(tx.connection, user.id)
            if row is None:
                raise AccessError(404, 'Connect a mailbox first.')
            if self.business.jobs is not None:
                self.business.jobs.enqueue(tx, TASK, {'mailbox': row['id']})
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
        if not isinstance(body, dict) or set(body) != {'provider'}:
            raise ValidationError({'provider': 'Choose microsoft or google.'})
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
                                  (hashed, user.id, body['provider'], seal(self.business, {'verifier': verifier, 'redirect': redirect}, state_context(hashed)),
                                   now + STATE_SECONDS))
        params = {'client_id': config['client_id'], 'response_type': 'code', 'redirect_uri': redirect, 'scope': config['scope'],
                  'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
                  **({'access_type': 'offline', 'prompt': 'consent'} if body['provider'] == 'google' else {'prompt': 'select_account'})}
        separator = '&' if authorize.query else '?'
        return {'url': config['authorize_url'] + separator + urlencode(params)}

    def callback(self, scope):
        """GET /_/mail/callback: the provider sends the person back here. Returns a redirect to the email settings page."""
        from .business import Download
        if self.business.assistants.origin(scope) is None:
            raise AccessError(404, NOT_FOUND)
        if scope['method'] != 'GET':
            raise AccessError(405, 'Method not allowed')
        back = lambda result: (302, Download(b''), [(b'location', f'/settings/email?mail={result}'.encode())])
        try:
            user, _ = self.business.auth.session(scope)
        except AccessError:
            return back('signin')
        try:
            query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True, max_num_fields=10)
        except (ValueError, UnicodeError):
            query = {}
        if any(len(values) != 1 for values in query.values()):
            return back('failed')
        query = {key: values[0] for key, values in query.items()}
        now = int(time.time())
        with self.database.transaction() as tx:
            hashed = digest(query.get('state', ''))
            row = tx.connection.execute('SELECT * FROM evlet_mail_states WHERE hash=?', (hashed,)).fetchone()
            tx.connection.execute('DELETE FROM evlet_mail_states WHERE hash=? OR expires<=?', (hashed, now))
        if row is None or row['user_id'] != user.id or row['expires'] <= now:
            return back('expired')
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
            audience = identity.get('aud', config['client_id'])
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
            # The servers and token address are fixed now, so a later change to the connector can't send this person's tokens elsewhere.
            settings = {**servers, 'token_url': config['token_url'], **DEFAULT_OPTIONS}
            self.store(user, row['provider'], address, settings, secret)
        except (ConnectionFailed, SecretKeyError, AccessError, ValidationError, ValueError, TypeError):
            return back('failed')
        return back('connected')

    def token(self, row, secret):
        """A current access token, refreshed when it is about to expire. Raises Ended when the sign-in was revoked."""
        if secret['expires'] > time.time() + 60:
            return secret['access_token']
        settings = json.loads(row['settings'])
        try:
            config = self.provider(row['provider'])
        except AccessError:
            raise Ended(f"{TITLES[row['provider']]} sign-in was removed from this workspace. Ask an admin, then connect again.") from None
        form = {'grant_type': 'refresh_token', 'refresh_token': secret['refresh_token'], 'client_id': config['client_id'], 'client_secret': config['client_secret']}
        status, answer = post_form(settings['token_url'], form)
        if status in (400, 401) and answer.get('error') in ('invalid_grant', 'invalid_client', 'unauthorized_client'):
            raise Ended(f"Your {TITLES[row['provider']]} sign-in ended. Connect your mailbox again.")
        if status != 200 or not isinstance(answer.get('access_token'), str):
            raise ConnectionFailed(UNREACHABLE)
        fresh = {'access_token': answer['access_token'], 'refresh_token': answer.get('refresh_token') or secret['refresh_token'],
                 'expires': int(time.time()) + min(max(int(answer.get('expires_in') or 3600), 60), 86400)}
        with self.database.transaction() as tx:
            tx.connection.execute('UPDATE evlet_mailboxes SET secrets=? WHERE id=?', (seal(self.business, fresh, mailbox_context(row['user_id'])), row['id']))
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
            return {**values, 'username': settings.get('username'), 'password': secret['password']}
        return {**values, 'token': self.token(row, secret)}

    def end(self, mailbox_id, message):
        """Delete a sign-in that stopped working; syncing stops until the person connects again."""
        with self.database.transaction() as tx:
            tx.connection.execute("UPDATE evlet_mailboxes SET status='reconnect', error=?, secrets=NULL WHERE id=?", (message, mailbox_id))

    # ---------- sending ----------

    def send_mail(self, user, to, subject, text, *, html=None, record=None):
        """Send as the person from their own mailbox, or from the shared account when they chose it or have none.

        With record=(Model, id), the message is linked to that record, so replies to it link there too. Returns the Message-ID.
        """
        recipients = [to] if isinstance(to, str) else list(to)
        if not recipients or len(recipients) > 50 or any(not isinstance(item, str) or not ADDRESS.fullmatch(item) for item in recipients):
            raise ValueError('Send to between 1 and 50 valid email addresses')
        if record is not None:
            with self.database.transaction() as tx:
                self.business.policy.record(tx, user, record[0], record[1])
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
            message_id = Connectors.deliver(values, recipients, subject, text, html=html)
        else:
            message_id = self.business.connectors.send_mail(recipients, subject, text, html=html)
        if record is not None:
            with self.database.transaction() as tx:
                link_message(tx, user, self.business.registry.name_for(record[0]), record[1], key_of(message_id),
                             {'text': text[:10000] or subject[:200], 'subject': subject[:200], 'party': ', '.join(recipients)[:200], 'direction': 'out'})
        return message_id

    # ---------- syncing ----------

    def run_job(self, context, payload):
        if 'mailbox' in payload:
            self.sync(int(payload['mailbox']))
            return
        with self.database._connection() as connection:
            due = [row[0] for row in connection.execute("SELECT id FROM evlet_mailboxes WHERE status IN ('connected','error') "
                                                         'ORDER BY COALESCE(synced_at,0), id LIMIT ?', (PER_RUN,))]
        for mailbox_id in due:
            try:
                self.sync(mailbox_id)
            except Exception as exc:
                # Only the type: a message may quote mail content.
                logger.warning('Mailbox sync failed with %s', type(exc).__name__)

    def sync(self, mailbox_id):
        """Copy in one batch of new messages that match records the person can read. Returns how many were copied."""
        if not self.enabled():
            return 0
        with self.database._connection() as connection:
            row = connection.execute('SELECT m.*, u.username, u.role, u.active FROM evlet_mailboxes m JOIN evlet_users u ON u.id=m.user_id WHERE m.id=?',
                                     (mailbox_id,)).fetchone()
        if row is None or not row['active'] or row['status'] not in ('connected', 'error'):
            return 0
        settings = json.loads(row['settings'])
        if not settings['link_replies']:
            return 0
        user = User(row['user_id'], row['username'], row['role'])
        try:
            client = open_imap(self.values(row, 'imap'))
            try:
                copied, cursor, more = self.read(client, row, settings, user)
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
                return 0
            with self.database.transaction() as tx:
                tx.connection.execute("UPDATE evlet_mailboxes SET status='error', error=? WHERE id=?",
                                      (str(exc) if isinstance(exc, ConnectionFailed) else UNREACHABLE, row['id']))
            return 0
        with self.database.transaction() as tx:
            # Disconnected (or connected again) while reading: nothing is kept.
            if tx.connection.execute("SELECT 1 FROM evlet_mailboxes WHERE id=? AND status IN ('connected','error')", (row['id'],)).fetchone() is None:
                return 0
            for key, data, targets in copied:
                for model, record_id in targets:
                    link_message(tx, user, self.business.registry.name_for(model), record_id, key, data)
            tx.connection.execute("UPDATE evlet_mailboxes SET cursor=?, synced_at=?, status='connected', error=NULL WHERE id=?",
                                  (json.dumps(cursor), int(time.time()), row['id']))
            if more and self.business.jobs is not None:
                self.business.jobs.enqueue(tx, TASK, {'mailbox': row['id']}, key=f"mail:{row['id']}:{cursor['validity']}:{cursor['uid']}")
        return len(copied)

    def read(self, client, row, settings, user):
        """Read headers of the next batch, match them, and fetch the text of matching messages only."""
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
        headers = self.fetch(client, batch, '(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO CC MESSAGE-ID IN-REPLY-TO REFERENCES SUBJECT)])')
        own = row['address']
        parsed = {}
        for uid, (size, raw) in headers.items():
            message = email.message_from_bytes(raw, policy=email_policy.default)
            parties = [address for address in addresses(message, 'From', 'To', 'Cc') if address != own]
            ids = message_ids(message, 'Message-ID')
            parsed[uid] = (message, size, parties, addresses(message, 'From')[:1] == [own], ids[0] if ids else None,
                           message_ids(message, 'In-Reply-To', 'References'))
        matched = self.match(user, {address for _, _, parties, _, _, _ in parsed.values() for address in parties}, settings['only_crm'])
        threads = self.threads(user, {item for *_, references in parsed.values() for item in references})
        copied = []
        for uid in batch:
            if uid not in parsed:
                continue
            message, size, parties, outgoing, message_id, references = parsed[uid]
            targets = [target for address in parties for target in matched.get(address, ())]
            if not targets:
                continue  # Not linked to any record: nothing of it is kept.
            targets += [target for reference in references for target in threads.get(key_of(reference), ())]
            targets = list(dict.fromkeys(targets))[:10]
            text = ''
            if size <= MAX_SIZE:
                body = self.fetch(client, [uid], '(BODY.PEEK[])').get(uid)
                text = plain_text(email.message_from_bytes(body[1], policy=email_policy.default)) if body else ''
            subject = str(message.get('Subject', '') or '').replace('\x00', '').strip()[:200]
            party = next(address for address in parties if matched.get(address))
            key = key_of(message_id) if message_id else digest(f"uid:{row['id']}:{validity}:{uid}")
            copied.append((key, {'text': text or subject or '(No text)', 'subject': subject, 'party': party[:200],
                                 'direction': 'out' if outgoing else 'in'}, targets))
        return copied, {'validity': validity, 'uid': batch[-1]}, more

    @staticmethod
    def fetch(client, uids, parts):
        """UID FETCH: {uid: (size, bytes)}."""
        status, data = client.uid('FETCH', ','.join(str(uid) for uid in uids), parts)
        if status != 'OK':
            raise ConnectionFailed(UNREACHABLE)
        found = {}
        for index, item in enumerate(data):
            if not isinstance(item, tuple):
                continue
            meta = item[0] + (data[index + 1] if index + 1 < len(data) and isinstance(data[index + 1], bytes) else b'')
            uid = re.search(rb'UID (\d+)', meta)
            size = re.search(rb'RFC822\.SIZE (\d+)', meta)
            if uid:
                found[int(uid[1])] = (int(size[1]) if size else 0, item[1])
        return found

    def models(self, user, only_crm):
        """Models and their readable Email fields to match addresses in, as this person."""
        policy, children = self.business.policy, self.business.child_models
        for model in self.business.registry.models.values():
            if model in children or (only_crm and model.__name__ not in MATCHED) or not policy.allows(user, model, 'read'):
                continue
            readable = policy.fields(user, model)
            fields = [name for name, field in model.__declared_fields__.items()
                      if isinstance(field, Email) and name in readable and getattr(field, 'stored', True)]
            if fields:
                yield model, fields

    def match(self, user, wanted, only_crm):
        """{address: [(Model, id), ...]} for records this person can read (row scope and field rules applied)."""
        found = {}
        if not wanted:
            return found
        wanted = sorted(wanted)
        with self.database._connection() as connection:
            for model, fields in self.models(user, only_crm):
                where, values = self.business.policy.scope_sql(user, model)
                marks = ', '.join('?' for _ in wanted)
                columns = [f'record.{identifier(name)}' for name in fields]
                condition = ' OR '.join(f'lower({column}) IN ({marks})' for column in columns)
                table = identifier(self.business.registry.name_for(model))
                sql = (f'SELECT record.id, {", ".join(columns)} FROM {table} AS record{where} {"AND" if where else "WHERE"} ({condition}) '
                       f'ORDER BY record.id LIMIT 500')
                for row in connection.execute(sql, [*values, *(wanted * len(fields))]).fetchall():
                    for value in tuple(row)[1:]:
                        if isinstance(value, str) and value.lower() in wanted:
                            found.setdefault(value.lower(), []).append((model, row[0]))
        return {address: list(dict.fromkeys(targets))[:10] for address, targets in found.items()}

    def threads(self, user, references):
        """{message key: [(Model, id)]} for earlier messages this one replies to, on records this person can read."""
        if not references:
            return {}
        keys = [key_of(item) for item in references]
        found = {}
        with self.database.transaction() as tx:
            rows = tx.connection.execute(f'SELECT message_key, model, record_id FROM evlet_mail_links WHERE message_key IN ({", ".join("?" for _ in keys)})',
                                                  keys).fetchall()
            by_model = {}
            for row in rows:
                model = self.business.registry.models.get(row['model'])
                if model is not None:
                    by_model.setdefault(model, []).append((row['message_key'], row['record_id']))
            for model, items in by_model.items():
                visible = self.business.policy.visible(tx, user, model, [record_id for _, record_id in items])
                for key, record_id in items:
                    if record_id in visible:
                        found.setdefault(key, []).append((model, record_id))
        return found

    # ---------- linked messages on a record ----------

    def messages(self, scope, user, model, record_id):
        """GET /api/{model}/{id}/messages: messages linked to a record, for people who can read it."""
        try:
            query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True, max_num_fields=2)
            if set(query) - {'limit', 'offset'} or any(len(values) != 1 for values in query.values()):
                raise ValueError()
            limit, offset = int(query.get('limit', ['50'])[0]), int(query.get('offset', ['0'])[0])
            if not 1 <= limit <= 100 or not 0 <= offset <= 2**63 - 1:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValidationError({'query': 'Use a limit of 1 to 100 and a nonnegative offset'}) from None
        name = self.business.registry.name_for(model)
        with self.database.transaction() as tx:
            self.business.policy.record(tx, user, model, record_id)
            connection = tx.connection
            rows = connection.execute('SELECT l.id, l.at, l.data, u.username FROM evlet_mail_links l LEFT JOIN evlet_users u ON u.id=l.user_id '
                                      'WHERE l.model=? AND l.record_id=? ORDER BY l.id DESC LIMIT ? OFFSET ?', (name, record_id, limit, offset)).fetchall()
            total = connection.execute('SELECT count(*) FROM evlet_mail_links WHERE model=? AND record_id=?', (name, record_id)).fetchone()[0]
        items = [{'id': row['id'], 'at': row['at'], 'by': row['username'], **json.loads(row['data'])} for row in rows]
        return 200, {'items': items, 'total': total, 'limit': limit, 'offset': offset}, []

    # ---------- HTTP ----------

    def api(self, scope, body, user):
        """/api/_settings/me/mailbox: a person's own mailbox. Signed-in sessions only."""
        path, method = scope['path'].removeprefix('/api/_settings/me/mailbox'), scope['method']
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

