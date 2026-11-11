"""Local accounts and revocable cookie sessions for a single application database."""

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import ipaddress
import json
import logging
import re
import secrets
import sqlite3
import time
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit

from .models import ValidationError, identifier
from .identity import RecordId

# Framework roles. Custom roles live in evlet_roles; Policy.roles maps every role to its base.
ROLES = ('admin', 'editor', 'viewer')
# The framework portal role: realm portal, sees nothing until an app grants it.
PORTAL = 'portal'
# Public endpoints run as this role. No account can have it.
PUBLIC = 'public'
# Ids no custom role can take.
RESERVED_ROLES = ('controls', PORTAL, PUBLIC)
ROLE_ID = re.compile(r'[a-z][a-z0-9_]{1,30}')
TWO_STEP_FIRST = 'Your role needs two-step sign-in. Sign in again to set it up.'
LAST_ADMIN = 'This is the only active admin. Make someone else an admin first, so the instance always has one.'
# The built-in Administrator: every instance has it, with the admin role and unlimited control. Its username,
# name and role never change, and it cannot be deactivated or deleted. It starts with no password.
ADMINISTRATOR = 'administrator'
ADMINISTRATOR_NAME = 'System Administrator'
PROTECTED = ('The Administrator account is built in. Its username, name and role cannot be changed, '
             'and it cannot be deactivated or deleted.')
NO_PASSWORD = 'This account has no password yet.'
_HASH_SLOTS = BoundedSemaphore(2)
# API keys: evk_<8-character prefix>_<secret>. Only the SHA-256 of the whole key is stored.
API_KEY = re.compile(r'(evk_[a-z0-9]{8})_[A-Za-z0-9_-]{43}')
KEY_RATE, KEY_FAILURES = 300, 20  # requests per key and failed keys per network, each per minute and process
# A peer that sends forwarded headers without being a trusted proxy is noted after FORWARDED_REQUESTS such
# requests within FORWARDED_NOTE_SECONDS, and written at most that often per peer and process. Each process
# remembers FORWARDED_CACHE peers (the oldest is dropped) and the table keeps the newest FORWARDED_ROWS.
FORWARDED_NOTE_SECONDS, FORWARDED_REQUESTS, FORWARDED_CACHE, FORWARDED_ROWS = 600, 20, 64, 50
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Security:
    """Sign-in rules an instance starts from: `security = Security(...)` in config.py, or LocalAuth(security=...).

    password_min takes 1 to 64 characters; 1 means any password will do. admin_two_step decides whether
    accounts with the admin role (the Administrator too) must use two-step sign-in, and two_step whether
    everyone else must. admin_sign_in opens the sign-in page with the administrator username filled in.

    Rules for admin accounts come only from here. Controls can change the password minimum and two-step
    sign-in for everyone else, in either direction, and can only shorten sessions and the lockout."""
    session_hours: int = 12
    lockout: int = 5
    password_min: int = 15
    admin_two_step: bool = True
    two_step: bool = False
    admin_sign_in: bool = False

    def __post_init__(self):
        for name, low, high, what in (('session_hours', 1, 168, 'hours'), ('lockout', 3, 50, 'failed sign-ins'), ('password_min', 1, 64, 'characters')):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'Security {name} takes {low} to {high} {what}')
        for name in ('admin_two_step', 'two_step', 'admin_sign_in'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'Security {name} takes True or False')


def is_administrator(user):
    """Whether user (a User or an account row) is the built-in Administrator."""
    if isinstance(user, (dict, sqlite3.Row)) or hasattr(user, 'keys'):
        return user['username'] == ADMINISTRATOR and user['role'] == 'admin'
    # A job's system identity (User.system) is never the Administrator, whatever name it is given.
    return getattr(user, 'username', None) == ADMINISTRATOR and getattr(user, 'role', None) == 'admin' and not getattr(user, 'system', None)


def has_role(user, roles):
    """A role check: the user's role is in roles. The Administrator passes every one."""
    return is_administrator(user) or user.role in roles


class AccessError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


@dataclass(frozen=True)
class KeyAccess:
    """What an API key narrows its user to: reads only or reads and writes, on every model or on these."""
    write: bool
    models: frozenset | None = None
    # The key's row, so background jobs it queues can check it is still valid.
    key_id: int | None = None
    # Set for AI assistants: the evlet_mcp_clients row, or 0 for a local (stdio) connector.
    assistant: int | None = None
    # Set for Eva: 'eva' in a chat, 'schedule' in a scheduled task. Eva acts as the person, like an assistant.
    origin: str | None = None


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str
    # (model, record id) pairs from evlet_user_links, read by row scopes such as own_customer.
    links: tuple = ()
    # The session finished two-step sign-in (a code, or set-up while signing in).
    two_step: bool = False
    # Set for API key requests; a key never does more than its user.
    access: KeyAccess | None = None
    # When this session last gave a two-step code (sign-in or a later check), as a Unix time.
    verified: int | None = None
    # Set when trusted code acts as the system (JobContext.business(tx, system=...)): the name the
    # Journal and Controls audit record for its writes. Such a user is no account; its id is 0.
    system: str | None = None

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


def check_username(name):
    """The rule every account creation shares."""
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', name):
        raise ValidationError({'username': 'Use 3 to 64 letters, numbers, dots, hyphens, or underscores'})


def proxy_networks(values):
    """Validate trusted proxy addresses and CIDR ranges; returns them as normalized network strings."""
    if isinstance(values, (str, bytes)):
        raise ValueError('trusted_proxies takes a tuple of IP addresses or CIDR ranges')
    try:
        return tuple(str(ipaddress.ip_network(value, strict=False)) for value in values)
    except (TypeError, ValueError):
        raise ValueError('trusted_proxies takes IP addresses or CIDR ranges, such as "127.0.0.1"') from None


def trusted_proxies_line(*groups):
    """The Instance(...) argument that trusts every address or range in groups, such as trusted_proxies=("127.0.0.1", "::1")."""
    values = []
    for value in (item for group in groups for item in group):
        network = ipaddress.ip_network(value, strict=False)
        text = str(network.network_address) if network.num_addresses == 1 else str(network)
        if text not in values:
            values.append(text)
    return 'trusted_proxies=(' + ', '.join(f'"{value}"' for value in values) + (',)' if len(values) == 1 else ')')


def _trusted(networks, address):
    """Whether address is in one of networks; None when it is not an IP address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    ip = ip.ipv4_mapped or ip if ip.version == 6 else ip
    return any(ip in network for network in networks if network.version == ip.version)


def client_address(scope, trusted_proxies=()):
    """The client's IP address. X-Forwarded-For is read only when the peer is a trusted proxy: the rightmost
    entry that is not itself a trusted proxy is the client. Without trusted proxies the peer is the client."""
    networks = [ipaddress.ip_network(value, strict=False) for value in trusted_proxies]
    def trusted(address):
        return _trusted(networks, address)
    current = (scope.get('client') or ('unknown',))[0]
    if not networks or not trusted(current):
        return current
    forwarded = []
    for key, value in scope.get('headers', []):
        if key.lower() == b'x-forwarded-for':
            forwarded.extend(part.strip() for part in value.decode('latin1').split(','))
    for entry in reversed(forwarded):
        state = trusted(entry)
        if state is None:
            break
        current = entry
        if not state:
            break
    return current


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
    def __init__(self, database, *, origins, secure_cookies=True, allow_setup=False, cookie_name='evlet_session', realms=None, trusted_proxies=(),
                 security=None):
        self.trusted_proxies = proxy_networks(trusted_proxies)
        self._proxy_networks = [ipaddress.ip_network(value) for value in self.trusted_proxies]
        # Peers whose forwarded headers were ignored, with when this process last wrote them down.
        self._forwarded = OrderedDict()
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
        self.realms = {}
        for origin, realm in dict(realms or {}).items():
            if origin not in self.origins or realm not in ('portal', 'public'):
                raise ValueError('Realms map configured origins to portal or public')
            self.realms[authority(origin).encode()] = realm
        self.secure_cookies = secure_cookies
        self.allow_setup = allow_setup
        # Security settings: the starting values. Controls overrides change them for everyone but admins (Controls.apply).
        if security is None:
            security = Security()
        if not isinstance(security, Security):
            raise ValueError('LocalAuth security takes Security(...)')
        self.security = security
        self.password_min, self.session_hours, self.lockout = security.password_min, security.session_hours, security.lockout
        # Admin accounts always use config.py's minimum; password_min above is the one for everyone else.
        self.admin_password_min = security.password_min
        # Two-step sign-in (evlet.settings): challenge(tx, account, realm) and finish(scope, body).
        self.second_step = None
        # Child model -> parent model, so a key scoped to a model also covers its rows (Business fills it).
        self.owners = {}
        self._key_lock, self._key_window, self._key_counts = Lock(), 0, {}

    def minimum(self, role):
        """The shortest password an account with role may have."""
        return self.admin_password_min if role == 'admin' else self.password_min

    def client_address(self, scope):
        """The request's client address, through the configured trusted proxies only."""
        return client_address(scope, self.trusted_proxies)

    def initialize(self):
        with self.database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
            # Allow Eva and Allow MCP (Controls, Users): both off for new people. They open Eva or MCP and grant no record access.
            users = self.database.columns(tx.connection, 'evlet_users')
            for name in ('allow_eva', 'allow_mcp'):
                if name not in users:
                    tx.connection.execute(f'ALTER TABLE evlet_users ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
            if 'allow_mcp' not in users and self.database.columns(tx.connection, 'evlet_mcp_clients'):
                # On upgrade, people with a connected assistant keep MCP; everyone else starts without it.
                tx.connection.execute('UPDATE evlet_users SET allow_mcp=1 WHERE id IN (SELECT user_id FROM evlet_mcp_clients WHERE revoked_at IS NULL)')
            tx.connection.execute("CREATE TABLE IF NOT EXISTS evlet_sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires INTEGER NOT NULL, realm TEXT NOT NULL DEFAULT 'desk')")
            columns = self.database.columns(tx.connection, 'evlet_sessions')
            if 'realm' not in columns:
                tx.connection.execute("ALTER TABLE evlet_sessions ADD COLUMN realm TEXT NOT NULL DEFAULT 'desk'")
            for name, kind in (('started', 'INTEGER'), ('agent', "TEXT NOT NULL DEFAULT ''"), ('two_step', 'INTEGER')):
                if name not in columns:
                    tx.connection.execute(f'ALTER TABLE evlet_sessions ADD COLUMN {name} {kind}')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_login_limits (client TEXT PRIMARY KEY, attempts INTEGER NOT NULL, resets INTEGER NOT NULL)')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_forwarded_peers (peer TEXT PRIMARY KEY, last_seen INTEGER NOT NULL)')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_user_links (user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, model TEXT NOT NULL, record_id INTEGER NOT NULL, PRIMARY KEY (user_id, model, record_id))')

            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_session_user ON evlet_sessions(user_id)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_session_expiry ON evlet_sessions(expires)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_login_reset ON evlet_login_limits(resets)')
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, name TEXT NOT NULL, prefix TEXT NOT NULL UNIQUE, hash TEXT NOT NULL, scope TEXT NOT NULL, created_at INTEGER NOT NULL, last_used INTEGER, revoked_at INTEGER)')
            tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_api_key_user ON evlet_api_keys(user_id)')
            # AI assistants: registered OAuth clients, each person's connections, their tokens (hashed) and calls.
            for statement in (
                "CREATE TABLE IF NOT EXISTS evlet_oauth_clients (id TEXT PRIMARY KEY, name TEXT NOT NULL, redirect_uris TEXT NOT NULL, secret TEXT, "
                "auth_method TEXT NOT NULL DEFAULT 'none' CHECK(auth_method IN ('none','client_secret_post','client_secret_basic')), created_at INTEGER NOT NULL)",
                'CREATE TABLE IF NOT EXISTS evlet_mcp_clients (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, '
                'oauth_client TEXT NOT NULL REFERENCES evlet_oauth_clients(id) ON DELETE CASCADE, client_name TEXT NOT NULL, created_at INTEGER NOT NULL, last_used INTEGER, '
                'calls INTEGER NOT NULL DEFAULT 0, allow_changes INTEGER NOT NULL DEFAULT 0, apps TEXT, revoked_at INTEGER)',
                'CREATE INDEX IF NOT EXISTS evlet_mcp_client_user ON evlet_mcp_clients(user_id)',
                'CREATE TABLE IF NOT EXISTS evlet_oauth_codes (hash TEXT PRIMARY KEY, oauth_client TEXT NOT NULL REFERENCES evlet_oauth_clients(id) ON DELETE CASCADE, '
                'user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, redirect_uri TEXT NOT NULL, challenge TEXT NOT NULL, '
                'allow_changes INTEGER NOT NULL, expires INTEGER NOT NULL, grant_id INTEGER)',
                "CREATE TABLE IF NOT EXISTS evlet_mcp_tokens (hash TEXT PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES evlet_mcp_clients(id) ON DELETE CASCADE, "
                "kind TEXT NOT NULL CHECK(kind IN ('access','refresh')), expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)",
                'CREATE INDEX IF NOT EXISTS evlet_mcp_token_client ON evlet_mcp_tokens(client_id)',
            ):
                tx.connection.execute(statement)
            tx.connection.execute("CREATE TABLE IF NOT EXISTS evlet_roles (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('framework','custom')), base TEXT, realm TEXT NOT NULL DEFAULT 'desk' CHECK(realm IN ('desk','portal')), opens_controls INTEGER NOT NULL DEFAULT 0, description TEXT NOT NULL DEFAULT '')")
            # Customize workspaces (Controls, Roles): edit the site's workspace overrides. Admin always may.
            if 'customize_workspaces' not in self.database.columns(tx.connection, 'evlet_roles'):
                tx.connection.execute('ALTER TABLE evlet_roles ADD COLUMN customize_workspaces INTEGER NOT NULL DEFAULT 0')
            # A role's realm decides where its users sign in: desk roles on desk hosts, portal roles on portal hosts.
            for role, realm, description in (('admin', 'desk', 'Full access to every app. Opens Controls.'), ('editor', 'desk', 'Reads and changes records.'),
                                             ('viewer', 'desk', 'Reads records only.'), (PORTAL, 'portal', 'Signs in on portal hosts. Sees only what an app grants.')):
                tx.connection.execute("INSERT OR IGNORE INTO evlet_roles(id,kind,base,realm,opens_controls,description) VALUES (?,'framework',NULL,?,?,?)", (role, realm, int(role == 'admin'), description))
            ensure_administrator(tx.connection, getattr(self.database, 'driver', None) is not None)

    def roles(self):
        """Every role by id, with its kind, base, realm and whether it opens Controls."""
        with self.database._connection() as connection:
            return {row['id']: dict(row) for row in connection.execute('SELECT * FROM evlet_roles ORDER BY kind DESC, id')}

    def _role(self, connection, role):
        if not isinstance(role, str) or not connection.execute('SELECT 1 FROM evlet_roles WHERE id=?', (role,)).fetchone():
            raise ValidationError({'role': 'Choose an existing role'})

    def guard(self, scope):
        protected = {b'host', b'origin', b'cookie', b'x-csrf-token', b'x-evlet-request', b'sec-fetch-site', b'authorization'}
        seen = set()
        for key, _ in scope.get('headers', []):
            key = key.lower()
            if key in protected and key in seen:
                raise AccessError(403, 'Repeated security header')
            seen.add(key)
        if b'x-forwarded-for' in seen or b'forwarded' in seen:
            self.note_forwarded(scope)
        headers = dict(scope.get('headers', []))
        if headers.get(b'host') not in self.hosts:
            raise AccessError(403, 'Unrecognized host')
        origin = headers.get(b'origin')
        if origin is not None and origin.decode('latin1') not in self.origins:
            raise AccessError(403, 'Unrecognized origin')
        if headers.get(b'sec-fetch-site') == b'cross-site':
            raise AccessError(403, 'Cross-site request rejected')
        # A key in the Authorization header is never sent by a browser on its own, so it needs no CSRF header.
        if scope['method'] not in ('GET', 'HEAD') and headers.get(b'x-evlet-request') != b'1' and not API_KEY.fullmatch(self.bearer(scope) or ''):
            raise AccessError(403, 'Missing request protection header')

    def note_forwarded(self, scope):
        """A request names a forwarded client. The header counts only from a trusted proxy; from any other
        peer it is ignored, and a peer on this machine or a private network is noted for Controls, where it
        shows under Needs attention. Public peers are clients behind their own proxies and are not noted."""
        peer = (scope.get('client') or (None,))[0]
        try:
            ip = ipaddress.ip_address(peer)
        except (TypeError, ValueError):
            return
        ip = (ip.ipv4_mapped or ip) if ip.version == 6 else ip
        if ip.is_global or _trusted(self._proxy_networks, str(ip)):
            return
        address, now = str(ip), int(time.time())
        with self._key_lock:
            # [window start, requests in the window, last written]
            entry = self._forwarded.get(address) or [now, 0, 0]
            if now - entry[0] >= FORWARDED_NOTE_SECONDS:
                entry[0], entry[1] = now, 0
            entry[1] += 1
            self._forwarded[address] = entry
            self._forwarded.move_to_end(address)
            while len(self._forwarded) > FORWARDED_CACHE:
                self._forwarded.popitem(last=False)
            # One stray request is not a proxy: only a steady stream from the peer is noted.
            if entry[1] < FORWARDED_REQUESTS or now - entry[2] < FORWARDED_NOTE_SECONDS:
                return
            entry[2] = now
        try:
            with self.database.transaction() as tx:
                tx.connection.execute('INSERT INTO evlet_forwarded_peers(peer,last_seen) VALUES (?,?) ON CONFLICT(peer) DO UPDATE SET last_seen=excluded.last_seen',
                                      (address, now))
                tx.connection.execute('DELETE FROM evlet_forwarded_peers WHERE last_seen<? OR peer NOT IN '
                                      '(SELECT peer FROM evlet_forwarded_peers ORDER BY last_seen DESC, peer LIMIT ?)', (now - 7 * 86400, FORWARDED_ROWS))
        except Exception:
            logger.warning('Could not note that %s sends forwarded headers without being a trusted proxy.', address, exc_info=True)

    def forwarded_peers(self):
        """Peers seen in the last day sending forwarded headers that are still not trusted proxies, newest first."""
        with self.database._connection() as connection:
            rows = connection.execute('SELECT peer FROM evlet_forwarded_peers WHERE last_seen>? ORDER BY last_seen DESC, peer LIMIT 20',
                                      (int(time.time()) - 86400,)).fetchall()
        return [row['peer'] for row in rows if not _trusted(self._proxy_networks, row['peer'])]

    def cookie_for(self, realm):
        """Host-only cookies, one name per realm, so a portal and a desk session never mix. Public hosts have none."""
        return {'desk': self.cookie_name, 'portal': self.cookie_name.replace('evlet_session', 'evlet_portal_session')}.get(realm)

    def realm(self, scope):
        """The realm of the request's host: desk unless the route table made it a portal or public host."""
        return self.realms.get(dict(scope.get('headers', [])).get(b'host'), 'desk')

    @staticmethod
    def bearer(scope):
        value = dict(scope.get('headers', [])).get(b'authorization')
        if value is None:
            return None
        scheme, _, token = value.decode('latin1').partition(' ')
        return token.strip() if scheme.lower() == 'bearer' else ''

    def token(self, scope):
        name = self.cookie_for(self.realm(scope))
        if name is None:
            return ''
        cookie = SimpleCookie()
        try:
            cookie.load(dict(scope.get('headers', [])).get(b'cookie', b'').decode('latin1'))
            value = cookie.get(name)
            token = value.value if value else ''
            return hashlib.sha256(token.encode()).hexdigest()
        except (CookieError, UnicodeError):
            return ''

    def session(self, scope):
        # A tool call of a remote AI assistant, dispatched in process with its already checked user.
        if scope.get('evlet.assistant') is not None:
            return scope['evlet.assistant'], None
        if self.bearer(scope) is not None:
            return self.key_session(scope), None
        realm = self.realm(scope)
        with self.database._connection() as connection:
            # One statement: the session, its user and the user's record links, while the role's realm is the host's.
            rows = connection.execute('SELECT u.id, u.username, u.role, s.csrf, s.two_step, l.model, l.record_id FROM evlet_sessions s JOIN evlet_users u ON u.id=s.user_id '
                                      'JOIN evlet_roles r ON r.id=u.role AND r.realm=s.realm LEFT JOIN evlet_user_links l ON l.user_id=u.id WHERE s.token=? AND s.realm=? AND s.expires>? AND u.active=1 ORDER BY l.model, l.record_id',
                                      (self.token(scope), realm, int(time.time()))).fetchall()
        row = rows[0] if rows else None
        links = tuple((item['model'], RecordId(item['record_id'])) for item in rows if item['model'] is not None)
        if row is None:
            raise AccessError(401, 'Sign in to continue')
        # A role that requires two-step sign-in uses nothing until it is set up: a session from before the
        # requirement (or before the instance could keep secrets) signs in again, and sets it up then.
        if row['two_step'] is None and self.second_step is not None and self.second_step.required(row['role'], realm):
            raise AccessError(401, TWO_STEP_FIRST)
        if scope['method'] not in ('GET', 'HEAD'):
            supplied = dict(scope.get('headers', [])).get(b'x-csrf-token', b'')
            if not hmac.compare_digest(supplied, row['csrf'].encode('ascii')):
                raise AccessError(403, 'Invalid request token')
        return User(row['id'], row['username'], row['role'], links, row['two_step'] is not None, verified=row['two_step']), row['csrf']

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
        """Authorization: Bearer evk_...: the key's user, narrowed to the key's scope, with the same checks as a session.

        A valid key is never throttled by others' failures: invalid keys are counted per network and
        prefix (and per network overall) only after the lookup.
        """
        token = self.bearer(scope)
        # Keys are for the desk: a portal or public host refuses them, and so does a user whose role is a portal role.
        if self.realm(scope) != 'desk':
            raise AccessError(403, 'API keys work on desk hosts only.')
        match = API_KEY.fullmatch(token or '')
        row, links = None, ()
        if match:
            with self.database._connection() as connection:
                row = connection.execute('SELECT k.id AS key_id, k.hash, k.scope, k.last_used, u.id, u.username, u.role FROM evlet_api_keys k JOIN evlet_users u ON u.id=k.user_id '
                                         "JOIN evlet_roles r ON r.id=u.role AND r.realm='desk' WHERE k.prefix=? AND k.revoked_at IS NULL AND u.active=1", (match[1],)).fetchone()
                if row is not None:
                    links = tuple((item['model'], RecordId(item['record_id'])) for item in connection.execute(
                        'SELECT model, record_id FROM evlet_user_links WHERE user_id=? ORDER BY model, record_id', (row['id'],)))
        if row is None or not hmac.compare_digest(row['hash'], hashlib.sha256(token.encode()).hexdigest()):
            network = 'key-failure:' + client_network(self.client_address(scope))
            prefix = network + ':' + (match[1] if match else (token or '')[:12])
            with self._key_lock:
                current = self._key_window == int(time.time()) // 60
                limited = current and (self._key_counts.get(prefix, 0) >= KEY_FAILURES or self._key_counts.get(network, 0) >= KEY_FAILURES * 5)
            if limited:
                raise AccessError(429, 'Too many requests with an invalid key. Try again in a minute.')
            self._count(prefix, KEY_FAILURES)
            self._count(network, KEY_FAILURES * 5)
            raise AccessError(401, 'This API key is not valid. It may have been revoked.')
        if not self._count('key:' + str(row['key_id']), KEY_RATE):
            raise AccessError(429, 'Too many requests with this key. Try again in a minute.')
        now = int(time.time())
        if row['last_used'] is None or row['last_used'] < now - 60:
            with self.database.transaction() as tx:
                tx.connection.execute('UPDATE evlet_api_keys SET last_used=? WHERE id=?', (now, row['key_id']))
        return User(row['id'], row['username'], row['role'], links, access=self.key_access(row['key_id'], row['scope']))

    def address(self, scope):
        """The client address as text, through the configured trusted proxies (client_address)."""
        return str(self.client_address(scope))

    def covered(self, keep):
        """The models whose top-level model passes keep(name), with their child rows."""
        def root(model):
            while model in self.owners:
                model = self.owners[model]
            return model
        return frozenset(model for model in self.database.registry.models.values() if keep(root(model).__name__))

    def key_access(self, key_id, scope):
        """The KeyAccess of a key's stored scope: models named by the key, with their child rows."""
        value = json.loads(scope)
        models = None if value['models'] is None else self.covered(set(value['models']).__contains__)
        return KeyAccess(value['access'] == 'write', models, key_id)

    @staticmethod
    def end_sessions(connection, user_id, *, keep=None, keys=False):
        """End a person's access. Every revocation goes through here.

        Always ends their browser sessions (except the session token keep), their connected AI assistants
        (access and refresh tokens) and their unused authorization codes. keys=True also revokes their API keys,
        for takeover recovery: a two-step reset, deactivation and the break-glass grant."""
        now = int(time.time())
        present = None
        if isinstance(connection, sqlite3.Connection):
            # The server-shell commands may meet a database from before assistants or keys existed.
            present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        connection.execute('DELETE FROM evlet_sessions WHERE user_id=?' + (' AND token<>?' if keep else ''), (user_id,) + ((keep,) if keep else ()))
        if present is None or 'evlet_mcp_clients' in present:
            connection.execute('DELETE FROM evlet_oauth_codes WHERE user_id=?', (user_id,))
            connection.execute('DELETE FROM evlet_mcp_tokens WHERE client_id IN (SELECT id FROM evlet_mcp_clients WHERE user_id=?)', (user_id,))
            connection.execute('UPDATE evlet_mcp_clients SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL', (now, user_id))
        if keys and (present is None or 'evlet_api_keys' in present):
            connection.execute('UPDATE evlet_api_keys SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL', (now, user_id))

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
        check_username(name)
        if role not in ROLES and not ROLE_ID.fullmatch(str(role)):
            raise ValidationError({'role': 'Choose an existing role'})
        return name.lower(), password_hash(body.get('password'), minimum=self.minimum(role)), role

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

    @staticmethod
    def _can_sign_in(connection):
        """Whether any account has a password. The Administrator has none until someone sets it."""
        return connection.execute("SELECT 1 FROM evlet_users WHERE password<>'' LIMIT 1").fetchone() is not None

    def password_needed(self, user):
        """Whether a signed-in account still has no password (the Administrator after a development sign-in link)."""
        if user.access is not None:
            return False
        with self.database._connection() as connection:
            row = connection.execute('SELECT password FROM evlet_users WHERE id=?', (user.id,)).fetchone()
        return row is not None and row['password'] == ''

    def setup_needed(self):
        """No account can sign in yet: there are no accounts, or only the Administrator without a password."""
        with self.database._connection() as connection:
            return not self._can_sign_in(connection)

    def setup(self, scope, body):
        try:
            loopback = ipaddress.ip_address(self.client_address(scope)).is_loopback
        except ValueError:
            loopback = False
        if not self.allow_setup or not loopback or not self.setup_needed():
            raise AccessError(403, 'Initial setup is unavailable')
        name, password, _ = self.account_values({**body, 'role': 'admin'})
        with self.database.transaction() as tx:
            if self._can_sign_in(tx.connection):
                raise AccessError(409, 'An administrator already exists')
            # Setting up as administrator gives the built-in Administrator its password.
            if name == ADMINISTRATOR:
                tx.connection.execute("UPDATE evlet_users SET password=? WHERE username=? AND role='admin'", (password, ADMINISTRATOR))
            else:
                tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (name, password, 'admin'))

    def login(self, scope, body):
        if self.second_step is not None and set(body) == {'ticket', 'code'}:
            return self.second_step.finish(scope, body)
        if set(body) != {'username', 'password'} or not all(isinstance(body[k], str) for k in body):
            raise AccessError(401, 'Invalid username or password')
        now = int(time.time())
        username = body['username'].lower()
        if not re.fullmatch(r'[a-z0-9_.-]{3,64}', username):
            raise AccessError(401, 'Invalid username or password')
        address = client_network(self.client_address(scope))
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
                account = tx.connection.execute('SELECT u.*, r.realm AS role_realm FROM evlet_users u LEFT JOIN evlet_roles r ON r.id=u.role WHERE u.username=?', (username,)).fetchone()
            # An account without a password (the Administrator before it is set) never signs in with one.
            usable = account is not None and account['password'].startswith('scrypt:')
            stored = account['password'] if usable else 'scrypt:' + '00' * 16 + ':' + '00' * 64
            try:
                candidate = _scrypt(_encode(body['password'], 1), stored.split(':')[1])
            except (ValueError, UnicodeError):
                candidate = ''
        # A portal user on a desk host, or a desk user on a portal host, gets the same answer as a wrong password.
        realm = self.realm(scope)
        if not hmac.compare_digest(candidate, stored) or not usable or not account['active'] or account['role_realm'] != realm:
            raise AccessError(401, 'Invalid username or password')
        with self.database.transaction() as tx:
            # A password/role change during hashing must not revive an old login.
            current = tx.connection.execute('SELECT * FROM evlet_users WHERE id=?', (account['id'],)).fetchone()
            if not current or not current['active'] or current['password'] != stored or current['role'] != account['role']:
                raise AccessError(401, 'Invalid username or password')
            # With two-step sign-in the failed-attempt count stays until the code is right too.
            challenge = self.second_step.challenge(tx, current, realm) if self.second_step is not None else None
            if challenge is not None:
                return challenge, []
            tx.connection.execute('DELETE FROM evlet_login_limits WHERE client=?', (pair,))
            return self.start_session(tx, scope, current, realm)

    def start_session(self, tx, scope, account, realm, verified=False):
        """Create a session for a checked account in an open transaction; verified marks a finished two-step sign-in.
        Returns the login answer and cookie."""
        token, csrf = secrets.token_urlsafe(32), secrets.token_hex(32)
        agent = dict(scope.get('headers', [])).get(b'user-agent', b'').decode('latin1')[:200]
        now = int(time.time())
        tx.connection.execute('DELETE FROM evlet_sessions WHERE token=?', (self.token(scope),))
        tx.connection.execute('INSERT INTO evlet_sessions(token,user_id,csrf,expires,realm,started,agent,two_step) VALUES (?,?,?,?,?,?,?,?)',
                              (hashlib.sha256(token.encode()).hexdigest(), account['id'], csrf, now + self.session_hours * 3600, realm, now, agent,
                               now if verified else None))
        return {'user': User(account['id'], account['username'], account['role']).public(), 'csrf': csrf}, [self.cookie(token, realm)]

    def cookie(self, token, realm='desk'):
        value = f'{self.cookie_for(realm)}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={self.session_hours * 3600 if token else 0}'
        if self.secure_cookies:
            value += '; Secure'
        return b'set-cookie', value.encode()

    def logout(self, scope):
        with self.database.transaction() as tx:
            tx.connection.execute('DELETE FROM evlet_sessions WHERE token=?', (self.token(scope),))
        return [self.cookie('', self.realm(scope))]

    def _link_target(self, model, record_id):
        name = self.database.registry.name_for(self.database.registry.resolve(model))
        try:
            return name, RecordId(record_id)
        except ValueError:
            raise ValidationError({'record_id': 'Use a record id'}) from None

    def link_user(self, user_id, model, record_id, *, actor='system'):
        """Link an account to a record, such as a portal user to their customer. Row scopes read these links.

        A link widens what the account's row scopes allow, so it is written to the audit log as actor.
        """
        name, record_id = self._link_target(model, record_id)
        with self.database.transaction() as tx:
            account = tx.connection.execute('SELECT username FROM evlet_users WHERE id=?', (user_id,)).fetchone()
            if not account:
                raise AccessError(404, 'Account not found')
            if not tx.connection.execute(f'SELECT 1 FROM {identifier(name)} WHERE id=?', (record_id,)).fetchone():
                raise AccessError(404, 'Record not found')
            if tx.connection.execute('INSERT INTO evlet_user_links(user_id,model,record_id) VALUES (?,?,?) ON CONFLICT DO NOTHING', (user_id, name, record_id)).rowcount:
                self._audit_link(tx.connection, actor, account['username'], f'Linked to {name} {record_id}')

    def unlink_user(self, user_id, model, record_id, *, actor='system'):
        name, record_id = self._link_target(model, record_id)
        with self.database.transaction() as tx:
            account = tx.connection.execute('SELECT username FROM evlet_users WHERE id=?', (user_id,)).fetchone()
            if account and tx.connection.execute('DELETE FROM evlet_user_links WHERE user_id=? AND model=? AND record_id=?', (user_id, name, record_id)).rowcount:
                self._audit_link(tx.connection, actor, account['username'], f'Unlinked from {name} {record_id}')

    @staticmethod
    def _audit_link(connection, actor, username, detail):
        from .controls import audit, ensure_schema
        # LocalAuth also runs without Business (which creates the Controls tables), so make sure the log exists.
        ensure_schema(connection)
        audit(connection, actor, 'Users', username, detail)

    def user_links(self, user_id):
        with self.database._connection() as connection:
            return tuple((row['model'], RecordId(row['record_id'])) for row in connection.execute(
                'SELECT model, record_id FROM evlet_user_links WHERE user_id=? ORDER BY model, record_id', (user_id,)))

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
            with self.database._connection() as connection:
                found = connection.execute('SELECT role FROM evlet_users WHERE id=?', (user_id,)).fetchone()
            role = changes.get('role', found['role'] if found else 'viewer')
            changes['password'] = password_hash(changes['password'], minimum=self.minimum(role if isinstance(role, str) else 'admin'))
        with self.database.transaction() as tx:
            if 'role' in changes:
                self._role(tx.connection, changes['role'])
            row = tx.connection.execute('SELECT * FROM evlet_users WHERE id=?', (user_id,)).fetchone()
            if not row:
                raise AccessError(404, 'Account not found')
            if is_administrator(row) and (changes.get('role', 'admin') != 'admin' or changes.get('active', True) is not True):
                raise AccessError(409, PROTECTED)
            after = check(tx.connection, row, changes) if check is not None else None
            if row['role'] == 'admin' and row['active'] and (changes.get('role', 'admin') != 'admin' or not changes.get('active', True)):
                # Admins who can sign in: the Administrator counts once it has a password.
                if tx.connection.execute("SELECT count(*) FROM evlet_users WHERE role='admin' AND active=1 AND password<>''").fetchone()[0] <= 1:
                    raise AccessError(409, LAST_ADMIN)
            for key, value in changes.items():
                tx.connection.execute(f'UPDATE evlet_users SET {key}=? WHERE id=?', (value, user_id))
            self.end_sessions(tx.connection, user_id, keys=changes.get('active') is False)
            if after is not None:
                after()


def _user_columns(connection, postgres):
    if postgres:
        return {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='evlet_users'")}
    return {row[1] for row in connection.execute('PRAGMA table_info(evlet_users)')}


def ensure_administrator(connection, postgres=False):
    """Create the built-in Administrator when it is missing: role admin, no password, so it cannot sign in until one is set.

    evlet_users.builtin marks it. An account named administrator without the mark belongs to someone else (a database
    from before 0.4.2): it is refused, never taken over. A changed role or status of the Administrator is put back.
    """
    if 'builtin' not in _user_columns(connection, postgres):
        connection.execute('ALTER TABLE evlet_users ADD COLUMN builtin INTEGER NOT NULL DEFAULT 0')
    row = connection.execute('SELECT id, role, active, builtin FROM evlet_users WHERE username=?', (ADMINISTRATOR,)).fetchone()
    if row is None:
        connection.execute("INSERT INTO evlet_users(username,password,role,builtin) VALUES (?,'','admin',1)", (ADMINISTRATOR,))
        return True
    if not row[3]:
        from .migrations import SchemaError
        raise SchemaError('An account named administrator already exists. Evlet now keeps that username for its built-in Administrator. '
                          'Rename that account in the database (for example to administrator-old), then run evlet migrate again.')
    if row[1] != 'admin' or not row[2]:
        connection.execute("UPDATE evlet_users SET role='admin', active=1 WHERE id=?", (row[0],))
    return False
