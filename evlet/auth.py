"""Local accounts and revocable cookie sessions for a single application database."""

from dataclasses import dataclass
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import ipaddress
import re
import secrets
import time
from urllib.parse import urlsplit

from .models import ValidationError

ROLES = ('admin', 'editor', 'viewer')


class AccessError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str

    def public(self):
        return {'id': self.id, 'username': self.username, 'role': self.role}


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise ValidationError({'password': 'Use between 15 and 128 characters'})
    try:
        encoded = password.encode('utf-8')
    except UnicodeError:
        raise ValidationError({'password': 'Use valid Unicode text'})
    salt = secrets.token_hex(16) if salt is None else salt
    digest = hashlib.scrypt(encoded, salt=bytes.fromhex(salt),
                            n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024).hex()
    return f'scrypt:{salt}:{digest}'


class LocalAuth:
    def __init__(self, database, *, origins, secure_cookies=True, allow_setup=False, cookie_name='evlet_session'):
        if cookie_name not in ('evlet_session', '__Host-evlet_session') or (cookie_name.startswith('__Host-') and not secure_cookies):
            raise ValueError('Invalid session cookie configuration')
        self.cookie_name = cookie_name
        self.database = database
        self.origins = frozenset(origins)
        if not self.origins:
            raise ValueError('Configure explicit browser origins')
        for origin in self.origins:
            url = urlsplit(origin)
            if url.scheme not in ('http', 'https') or not url.netloc or url.path or url.query or url.fragment or url.username:
                raise ValueError('Use exact HTTP origins without paths')
            if not secure_cookies and url.hostname not in ('localhost', '127.0.0.1', '::1') and not (url.hostname or '').endswith('.localhost'):
                raise ValueError('Insecure cookies are limited to loopback development origins')
        self.hosts = {urlsplit(origin).netloc.encode() for origin in self.origins}
        self.secure_cookies = secure_cookies
        self.allow_setup = allow_setup

    def initialize(self):
        with self.database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires INTEGER NOT NULL)')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_login_limits (client TEXT PRIMARY KEY, attempts INTEGER NOT NULL, resets INTEGER NOT NULL)')

            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_session_user ON evlet_sessions(user_id)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_session_expiry ON evlet_sessions(expires)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_login_reset ON evlet_login_limits(resets)')

    def guard(self, scope):
        protected = {b'host', b'origin', b'cookie', b'x-csrf-token', b'x-evlet-request', b'sec-fetch-site'}
        seen = set()
        for key, _ in scope.get('headers', []):
            key = key.lower()
            if key in protected and key in seen:
                raise AccessError(403, 'Repeated security header')
            seen.add(key)
        headers = dict(scope.get('headers', []))
        if headers.get(b'host') not in self.hosts:
            raise AccessError(403, 'Unrecognized host')
        origin = headers.get(b'origin')
        if origin is not None and origin.decode('latin1') not in self.origins:
            raise AccessError(403, 'Unrecognized origin')
        if headers.get(b'sec-fetch-site') == b'cross-site':
            raise AccessError(403, 'Cross-site request rejected')
        if scope['method'] not in ('GET', 'HEAD') and headers.get(b'x-evlet-request') != b'1':
            raise AccessError(403, 'Missing request protection header')

    def token(self, scope):
        cookie = SimpleCookie()
        try:
            cookie.load(dict(scope.get('headers', [])).get(b'cookie', b'').decode('latin1'))
            value = cookie.get(self.cookie_name)
            token = value.value if value else ''
            return hashlib.sha256(token.encode()).hexdigest()
        except (CookieError, UnicodeError):
            return ''

    def session(self, scope):
        with self.database._connection() as connection:
            row = connection.execute('SELECT u.id, u.username, u.role, s.csrf FROM evlet_sessions s JOIN evlet_users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1',
                                     (self.token(scope), int(time.time()))).fetchone()
        if row is None:
            raise AccessError(401, 'Sign in to continue')
        if scope['method'] not in ('GET', 'HEAD'):
            supplied = dict(scope.get('headers', [])).get(b'x-csrf-token', b'')
            if not hmac.compare_digest(supplied, row['csrf'].encode('ascii')):
                raise AccessError(403, 'Invalid request token')
        return User(row['id'], row['username'], row['role']), row['csrf']

    @staticmethod
    def account_values(body):
        if set(body) - {'username', 'password', 'role'}:
            raise ValidationError({'_form': 'Unknown account field'})
        name = body.get('username', '')
        role = body.get('role', 'viewer')
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', name):
            raise ValidationError({'username': 'Use 3 to 64 letters, numbers, dots, hyphens, or underscores'})
        if role not in ROLES:
            raise ValidationError({'role': 'Choose admin, editor, or viewer'})
        return name.lower(), password_hash(body.get('password')), role

    def create_user(self, **values):
        name, password, role = self.account_values(values)
        with self.database.transaction() as tx:
            user_id = tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (name, password, role)).lastrowid
        return User(user_id, name, role)

    def setup_needed(self):
        with self.database._connection() as connection:
            return not connection.execute('SELECT 1 FROM evlet_users LIMIT 1').fetchone()

    def setup(self, scope, body):
        try:
            loopback = ipaddress.ip_address(scope.get('client', ('',))[0]).is_loopback
        except ValueError:
            loopback = False
        if not self.allow_setup or not loopback or not self.setup_needed():
            raise AccessError(403, 'Initial setup is unavailable')
        name, password, _ = self.account_values({**body, 'role': 'admin'})
        with self.database.transaction() as tx:
            if tx.connection.execute('SELECT 1 FROM evlet_users LIMIT 1').fetchone():
                raise AccessError(409, 'An administrator already exists')
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (name, password, 'admin'))

    def login(self, scope, body):
        if set(body) != {'username', 'password'} or not all(isinstance(body[k], str) for k in body):
            raise AccessError(401, 'Invalid username or password')
        now = int(time.time())
        client = hashlib.sha256(scope.get('client', ('unknown',))[0].encode()).hexdigest()
        with self.database.transaction() as tx:
            tx.connection.execute('DELETE FROM evlet_login_limits WHERE resets<=?', (now,))
            tx.connection.execute('DELETE FROM evlet_sessions WHERE expires<=?', (now,))
            row = tx.connection.execute('SELECT attempts FROM evlet_login_limits WHERE client=?', (client,)).fetchone()
            if row and row['attempts'] >= 10:
                raise AccessError(429, 'Too many sign-in attempts. Try again in 15 minutes.')
            tx.connection.execute('INSERT INTO evlet_login_limits VALUES (?,1,?) ON CONFLICT(client) DO UPDATE SET attempts=evlet_login_limits.attempts+1', (client, now + 900))
            account = tx.connection.execute('SELECT * FROM evlet_users WHERE username=?', (body['username'].lower(),)).fetchone()
        stored = account['password'] if account else 'scrypt:' + '00' * 16 + ':' + '00' * 64
        try:
            candidate = password_hash(body['password'], stored.split(':')[1])
        except (ValueError, UnicodeError):
            candidate = ''
        if not hmac.compare_digest(candidate, stored) or not account or not account['active']:
            raise AccessError(401, 'Invalid username or password')
        token, csrf = secrets.token_urlsafe(32), secrets.token_hex(32)
        with self.database.transaction() as tx:
            # A password/role change during hashing must not revive an old login.
            current = tx.connection.execute('SELECT * FROM evlet_users WHERE id=?', (account['id'],)).fetchone()
            if not current or not current['active'] or current['password'] != stored or current['role'] != account['role']:
                raise AccessError(401, 'Invalid username or password')
            tx.connection.execute('DELETE FROM evlet_login_limits WHERE client=?', (client,))
            tx.connection.execute('DELETE FROM evlet_sessions WHERE token=?', (self.token(scope),))
            tx.connection.execute('INSERT INTO evlet_sessions VALUES (?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), account['id'], csrf, now + 28800))
        return {'user': User(account['id'], account['username'], account['role']).public(), 'csrf': csrf}, [self.cookie(token)]

    def cookie(self, token):
        value = f'{self.cookie_name}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={28800 if token else 0}'
        if self.secure_cookies:
            value += '; Secure'
        return b'set-cookie', value.encode()

    def logout(self, scope):
        with self.database.transaction() as tx:
            tx.connection.execute('DELETE FROM evlet_sessions WHERE token=?', (self.token(scope),))
        return [self.cookie('')]

    def users(self):
        return self.user_page()['items']

    def user_page(self, offset=0):
        if type(offset) is not int or not 0 <= offset <= 2**63-1:
            raise ValidationError({'offset': 'Use a nonnegative offset'})
        with self.database._connection() as connection:
            connection.execute('BEGIN')
            items = [dict(row) for row in connection.execute('SELECT id,username,role,active FROM evlet_users ORDER BY id LIMIT 100 OFFSET ?', (offset,))]
            total = connection.execute('SELECT count(*) FROM evlet_users').fetchone()[0]
            return {'items': items, 'total': total, 'offset': offset, 'limit': 100}

    def update_user(self, user_id, body):
        if not body or set(body) - {'role', 'active', 'password'}:
            raise ValidationError({'_form': 'Use role, active, or password'})
        if 'role' in body and body['role'] not in ROLES:
            raise ValidationError({'role': 'Unknown role'})
        if 'active' in body and type(body['active']) is not bool:
            raise ValidationError({'active': 'Use true or false'})
        changes = dict(body)
        if 'password' in changes:
            changes['password'] = password_hash(changes['password'])
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_users WHERE id=?', (user_id,)).fetchone()
            if not row:
                raise AccessError(404, 'Account not found')
            if row['role'] == 'admin' and row['active'] and (changes.get('role', 'admin') != 'admin' or not changes.get('active', True)):
                if tx.connection.execute("SELECT count(*) FROM evlet_users WHERE role='admin' AND active=1").fetchone()[0] == 1:
                    raise AccessError(409, 'Keep at least one active administrator')
            for key, value in changes.items():
                tx.connection.execute(f'UPDATE evlet_users SET {key}=? WHERE id=?', (value, user_id))
            tx.connection.execute('DELETE FROM evlet_sessions WHERE user_id=?', (user_id,))
