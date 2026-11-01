"""Local accounts and revocable cookie sessions for a single application database."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import ipaddress
import json
import re
import secrets
import time
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit

from .models import ValidationError

# Framework roles. Custom roles live in evlet_roles; Policy.roles maps every role to its base.
ROLES = ('admin', 'editor', 'viewer')
ROLE_ID = re.compile(r'[a-z][a-z0-9_]{1,30}')
LAST_ADMIN = 'This is the only active admin. Make someone else an admin first, so the instance always has one.'
_HASH_SLOTS = BoundedSemaphore(2)
# API keys: evk_<8-character prefix>_<secret>. Only the SHA-256 of the whole key is stored.
API_KEY = re.compile(r'(evk_[a-z0-9]{8})_[A-Za-z0-9_-]{43}')
KEY_RATE, KEY_FAILURES = 300, 20  # requests per key and failed keys per network, each per minute and process


class AccessError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


@dataclass(frozen=True)
class KeyAccess:
    """What an API key narrows its user to: reads only or reads and writes, on every model or on these."""
    write: bool
    models: frozenset | None = None


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str
    # Set for API key requests; a key never does more than its user.
    access: KeyAccess | None = None

    def public(self):
        return {'id': self.id, 'username': self.username, 'role': self.role}


@contextmanager
def hashing_slot():
    """Hold one bounded password-hashing slot, or fail fast with 503 instead of queueing."""
    if not _HASH_SLOTS.acquire(blocking=False):
        raise AccessError(503, 'Password service is busy. Try again shortly.')
    try:
        yield
    finally:
        _HASH_SLOTS.release()


def _encode(password, minimum=15):
    if not isinstance(password, str) or not minimum <= len(password) <= 128:
        raise ValidationError({'password': f'Use between {minimum} and 128 characters'})
    try:
        return password.encode('utf-8')
    except UnicodeError:
        raise ValidationError({'password': 'Use valid Unicode text'})


def _scrypt(encoded, salt):
    """Callers hold a hashing slot."""
    digest = hashlib.scrypt(encoded, salt=bytes.fromhex(salt),
                            n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024).hex()
    return f'scrypt:{salt}:{digest}'


def password_hash(password, salt=None, minimum=15):
    encoded = _encode(password, minimum)
    salt = secrets.token_hex(16) if salt is None else salt
    with hashing_slot():
        return _scrypt(encoded, salt)


def client_network(address):
    """Throttle key: IPv4 and non-routable IPv6 addresses exactly, routable IPv6 by /64 prefix."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return 'unknown'
    if ip.version == 6 and ip.ipv4_mapped is not None:
        # IPv4 clients of a dual-stack socket keep their own address instead of sharing ::/64.
        ip = ip.ipv4_mapped
    elif ip.version == 6 and 1 < int(ip) < 2**32:
        # Deprecated IPv4-compatible form (::a.b.c.d) likewise identifies one IPv4 client.
        ip = ipaddress.IPv4Address(int(ip))
    if ip.version == 6 and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
        return str(ipaddress.ip_network(f'{ip}/64', strict=False))
    return str(ip)


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
        from .routing import authority
        self.hosts = {authority(origin).encode() for origin in self.origins}
        self.secure_cookies = secure_cookies
        self.allow_setup = allow_setup
        # Security settings; Controls overrides can only make them stricter.
        self.password_min, self.session_hours, self.lockout = 15, 8, 10
        # Child model -> parent model, so a key scoped to a model also covers its rows (Business fills it).
        self.owners = {}
        self._key_lock, self._key_window, self._key_counts = Lock(), 0, {}

    def initialize(self):
        with self.database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires INTEGER NOT NULL)')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_login_limits (client TEXT PRIMARY KEY, attempts INTEGER NOT NULL, resets INTEGER NOT NULL)')

            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_session_user ON evlet_sessions(user_id)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_session_expiry ON evlet_sessions(expires)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_login_reset ON evlet_login_limits(resets)')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, name TEXT NOT NULL, prefix TEXT NOT NULL UNIQUE, hash TEXT NOT NULL, scope TEXT NOT NULL, created_at INTEGER NOT NULL, last_used INTEGER, revoked_at INTEGER)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_api_key_user ON evlet_api_keys(user_id)')
            tx.connection.execute("CREATE TABLE IF NOT EXISTS evlet_roles (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('framework','custom')), base TEXT, realm TEXT NOT NULL DEFAULT 'desk' CHECK(realm IN ('desk','portal')), opens_controls INTEGER NOT NULL DEFAULT 0, description TEXT NOT NULL DEFAULT '')")
            for role, description in zip(ROLES, ('Full access to every app. Opens Controls.', 'Reads and changes records.', 'Reads records only.')):
                tx.connection.execute("INSERT OR IGNORE INTO evlet_roles(id,kind,base,realm,opens_controls,description) VALUES (?,'framework',NULL,'desk',?,?)", (role, int(role == 'admin'), description))

    def roles(self):
        """Every role by id, with its kind, base, realm and whether it opens Controls."""
        with self.database._connection() as connection:
            return {row['id']: dict(row) for row in connection.execute('SELECT * FROM evlet_roles ORDER BY kind DESC, id')}

    def _role(self, connection, role):
        row = connection.execute('SELECT realm FROM evlet_roles WHERE id=?', (role,)).fetchone() if isinstance(role, str) else None
        if row is None or row['realm'] != 'desk':
            raise ValidationError({'role': 'Choose an existing role'})

    def guard(self, scope):
        protected = {b'host', b'origin', b'cookie', b'x-csrf-token', b'x-evlet-request', b'sec-fetch-site', b'authorization'}
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
        # A key in the Authorization header is never sent by a browser on its own, so it needs no CSRF header.
        if scope['method'] not in ('GET', 'HEAD') and headers.get(b'x-evlet-request') != b'1' and self.bearer(scope) is None:
            raise AccessError(403, 'Missing request protection header')

    @staticmethod
    def bearer(scope):
        value = dict(scope.get('headers', [])).get(b'authorization')
        if value is None:
            return None
        scheme, _, token = value.decode('latin1').partition(' ')
        return token.strip() if scheme.lower() == 'bearer' else ''

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
        if self.bearer(scope) is not None:
            return self.key_session(scope), None
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

    # ---------- API keys ----------

    def _count(self, key, limit):
        """A fixed one-minute window per process; True while key stays within limit."""
        with self._key_lock:
            window = int(time.time()) // 60
            if window != self._key_window:
                self._key_window, self._key_counts = window, {}
            self._key_counts[key] = self._key_counts.get(key, 0) + 1
            return self._key_counts[key] <= limit

    def key_session(self, scope):
        """Authorization: Bearer evk_...: the key's user, narrowed to the key's scope, with the same checks as a session."""
        token = self.bearer(scope)
        network = 'key-failure:' + client_network((scope.get('client') or ('unknown',))[0])
        with self._key_lock:
            failed = self._key_window == int(time.time()) // 60 and self._key_counts.get(network, 0) >= KEY_FAILURES
        if failed:
            raise AccessError(429, 'Too many requests with an invalid key. Try again in a minute.')
        match = API_KEY.fullmatch(token or '')
        row = None
        if match:
            with self.database._connection() as connection:
                row = connection.execute('SELECT k.id AS key_id, k.hash, k.scope, k.last_used, u.id, u.username, u.role FROM evlet_api_keys k JOIN evlet_users u ON u.id=k.user_id '
                                         'WHERE k.prefix=? AND k.revoked_at IS NULL AND u.active=1', (match[1],)).fetchone()
        if row is None or not hmac.compare_digest(row['hash'], hashlib.sha256(token.encode()).hexdigest()):
            self._count(network, KEY_FAILURES)
            raise AccessError(401, 'This API key is not valid. It may have been revoked.')
        if not self._count('key:' + str(row['key_id']), KEY_RATE):
            raise AccessError(429, 'Too many requests with this key. Try again in a minute.')
        now = int(time.time())
        if row['last_used'] is None or row['last_used'] < now - 60:
            with self.database.transaction() as tx:
                tx.connection.execute('UPDATE evlet_api_keys SET last_used=? WHERE id=?', (now, row['key_id']))
        scope_value = json.loads(row['scope'])
        models = None
        if scope_value['models'] is not None:
            names = set(scope_value['models'])
            def root(model):
                while model in self.owners:
                    model = self.owners[model]
                return model
            models = frozenset(model for model in self.database.registry.models.values() if root(model).__name__ in names)
        return User(row['id'], row['username'], row['role'], KeyAccess(scope_value['access'] == 'write', models))

    def create_key(self, user_id, name, scope, check=None):
        """Store a new key and return (row, key). The key itself is shown once and only its hash is kept."""
        alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
        with self.database.transaction() as tx:
            for _ in range(5):
                prefix = 'evk_' + ''.join(secrets.choice(alphabet) for _ in range(8))
                if not tx.connection.execute('SELECT 1 FROM evlet_api_keys WHERE prefix=?', (prefix,)).fetchone():
                    break
            else:
                raise AccessError(503, 'Could not create a key. Try again.')
            token = prefix + '_' + secrets.token_urlsafe(32)
            tx.connection.execute('INSERT INTO evlet_api_keys(user_id,name,prefix,hash,scope,created_at) VALUES (?,?,?,?,?,?)',
                                  (user_id, name, prefix, hashlib.sha256(token.encode()).hexdigest(), json.dumps(scope, sort_keys=True), int(time.time())))
            row = self.key_rows(tx.connection, 'k.prefix=?', (prefix,))[0]
            if check is not None:
                check(tx.connection, row)
        return row, token

    @staticmethod
    def key_rows(connection, where='1=1', values=()):
        rows = connection.execute('SELECT k.id, k.user_id, u.username, u.role, k.name, k.prefix, k.scope, k.created_at, k.last_used FROM evlet_api_keys k '
                                  f'JOIN evlet_users u ON u.id=k.user_id WHERE k.revoked_at IS NULL AND {where} ORDER BY k.id DESC LIMIT 200', values)
        return [{**dict(row), 'scope': json.loads(row['scope'])} for row in rows]

    def keys(self, user_id=None):
        with self.database._connection() as connection:
            return self.key_rows(connection, *(('k.user_id=?', (user_id,)) if user_id is not None else ()))

    def revoke_key(self, key_id, user_id=None, check=None):
        """Revoke a key now; with user_id, only that user's own key. check(connection, row) runs first."""
        with self.database.transaction() as tx:
            found = self.key_rows(tx.connection, 'k.id=?' + (' AND k.user_id=?' if user_id is not None else ''), (key_id,) + ((user_id,) if user_id is not None else ()))
            if not found:
                raise AccessError(404, 'Key not found')
            if check is not None:
                check(tx.connection, found[0])
            tx.connection.execute('UPDATE evlet_api_keys SET revoked_at=? WHERE id=?', (int(time.time()), key_id))
            return found[0]

    def account_values(self, body):
        if set(body) - {'username', 'password', 'role'}:
            raise ValidationError({'_form': 'Unknown account field'})
        name = body.get('username', '')
        role = body.get('role', 'viewer')
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', name):
            raise ValidationError({'username': 'Use 3 to 64 letters, numbers, dots, hyphens, or underscores'})
        if role not in ROLES and not ROLE_ID.fullmatch(str(role)):
            raise ValidationError({'role': 'Choose an existing role'})
        return name.lower(), password_hash(body.get('password'), minimum=self.password_min), role

    def create_user(self, **values):
        return self.add_user(values)

    def add_user(self, values, check=None):
        """Create an account; check(connection, user) runs in the same transaction (audit, limits)."""
        name, password, role = self.account_values(values)
        with self.database.transaction() as tx:
            self._role(tx.connection, role)
            if tx.connection.execute('SELECT 1 FROM evlet_users WHERE username=?', (name,)).fetchone():
                raise ValidationError({'username': 'Someone already uses this username'})
            user_id = tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (name, password, role)).lastrowid
            if check is not None:
                check(tx.connection, User(user_id, name, role))
        return User(user_id, name, role)

    def setup_needed(self):
        with self.database._connection() as connection:
            return not connection.execute('SELECT 1 FROM evlet_users LIMIT 1').fetchone()

    def setup(self, scope, body):
        try:
            loopback = ipaddress.ip_address((scope.get('client') or ('',))[0]).is_loopback
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
        username = body['username'].lower()
        if not re.fullmatch(r'[a-z0-9_.-]{3,64}', username):
            raise AccessError(401, 'Invalid username or password')
        address = client_network((scope.get('client') or ('unknown',))[0])
        client = hashlib.sha256(('network:' + address).encode()).hexdigest()
        pair = hashlib.sha256(('account:' + address + ':' + username).encode()).hexdigest()
        # Take the hashing slot first: a busy 503 is answered before any attempt is recorded.
        with hashing_slot():
            with self.database.transaction() as tx:
                tx.connection.execute('DELETE FROM evlet_login_limits WHERE resets<=?', (now,))
                tx.connection.execute('DELETE FROM evlet_sessions WHERE expires<=?', (now,))
                for key, limit in ((pair, self.lockout), (client, 100)):
                    row = tx.connection.execute('SELECT attempts FROM evlet_login_limits WHERE client=?', (key,)).fetchone()
                    if row and row['attempts'] >= limit:
                        raise AccessError(429, 'Too many sign-in attempts. Try again in 15 minutes.')
                for key in (pair, client):
                    tx.connection.execute('INSERT INTO evlet_login_limits VALUES (?,1,?) ON CONFLICT(client) DO UPDATE SET attempts=evlet_login_limits.attempts+1', (key, now + 900))
                account = tx.connection.execute('SELECT * FROM evlet_users WHERE username=?', (username,)).fetchone()
            stored = account['password'] if account else 'scrypt:' + '00' * 16 + ':' + '00' * 64
            try:
                candidate = _scrypt(_encode(body['password']), stored.split(':')[1])
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
            tx.connection.execute('DELETE FROM evlet_login_limits WHERE client=?', (pair,))
            tx.connection.execute('DELETE FROM evlet_sessions WHERE token=?', (self.token(scope),))
            tx.connection.execute('INSERT INTO evlet_sessions VALUES (?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), account['id'], csrf, now + self.session_hours * 3600))
        return {'user': User(account['id'], account['username'], account['role']).public(), 'csrf': csrf}, [self.cookie(token)]

    def cookie(self, token):
        value = f'{self.cookie_name}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={self.session_hours * 3600 if token else 0}'
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

    def update_user(self, user_id, body, check=None):
        """check(connection, row, changes) runs first in the transaction; what it returns runs after the writes."""
        if not body or set(body) - {'role', 'active', 'password'}:
            raise ValidationError({'_form': 'Use role, active, or password'})
        if 'active' in body and type(body['active']) is not bool:
            raise ValidationError({'active': 'Use true or false'})
        changes = dict(body)
        if 'password' in changes:
            changes['password'] = password_hash(changes['password'], minimum=self.password_min)
        with self.database.transaction() as tx:
            if 'role' in changes:
                self._role(tx.connection, changes['role'])
            row = tx.connection.execute('SELECT * FROM evlet_users WHERE id=?', (user_id,)).fetchone()
            if not row:
                raise AccessError(404, 'Account not found')
            after = check(tx.connection, row, changes) if check is not None else None
            if row['role'] == 'admin' and row['active'] and (changes.get('role', 'admin') != 'admin' or not changes.get('active', True)):
                if tx.connection.execute("SELECT count(*) FROM evlet_users WHERE role='admin' AND active=1").fetchone()[0] == 1:
                    raise AccessError(409, LAST_ADMIN)
            for key, value in changes.items():
                tx.connection.execute(f'UPDATE evlet_users SET {key}=? WHERE id=?', (value, user_id))
            tx.connection.execute('DELETE FROM evlet_sessions WHERE user_id=?', (user_id,))
            if after is not None:
                after()
