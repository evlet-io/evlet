"""AI assistants: OAuth 2.1 sign-in for MCP clients and the remote MCP endpoint at /_/mcp.

A client registers itself, a person approves it on the consent page, and every tool call then runs as
that person through Business.dispatch, with the same account, tenant, row, field and action checks as
the browser. Tokens and client secrets are stored as SHA-256 hashes only.
"""

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from http.client import HTTPException
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from .auth import AccessError, KeyAccess, User, client_network
from .connector import ConnectorError, strict_json
from .controls import ADMIN_ONLY, NOT_FOUND, audit
from .database import ConflictError, NotFoundError
from .mcp import SUPPORTED, MCPServer
from .tools import Dispatch
from .models import ValidationError

ACCESS_SECONDS, REFRESH_SECONDS, CODE_SECONDS, KEEP = 3600, 30 * 86400, 300, 30 * 86400
MAX_CLIENTS, MAX_PER_USER, LIGHT_RATE = 1000, 20, 120
ACCESS = re.compile(r'evm_[A-Za-z0-9_-]{43}')
VERIFIER = re.compile(r'[A-Za-z0-9._~-]{43,128}')
CHALLENGE = re.compile(r'[A-Za-z0-9_-]{43}')
OFF = 'AI access is turned off for this workspace.'
NOT_ALLOWED = "Your administrator hasn't allowed you to connect AI assistants. Ask them to turn on Allow MCP for you in Controls."
AREA = 'MCP'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class OAuthError(Exception):
    def __init__(self, code, description, status=400):
        self.code, self.description, self.status = code, description, status


def redirect_uri_ok(value):
    """https, http on a loopback address (RFC 8252) or a reverse-domain app scheme; no fragment or credentials."""
    # ASCII only, so the consent page shows the real (punycode) host.
    if not isinstance(value, str) or not 8 <= len(value) <= 2000 or not value.isascii() or '#' in value or any(ord(c) < 33 or ord(c) == 127 for c in value):
        return False
    try:
        url = urlsplit(value)
        url.port
    except ValueError:
        return False
    if url.username or url.password:
        return False
    if url.scheme == 'https':
        return bool(url.hostname)
    if url.scheme == 'http':
        return url.hostname in ('127.0.0.1', 'localhost', '::1')
    return bool(re.fullmatch(r'[a-z][a-z0-9+-]*(\.[a-z0-9+-]+)+', url.scheme))


class LocalClient:
    """The MCP server's API client inside the application: each request goes through Business.dispatch as the assistant's user."""

    def __init__(self, business, scope, user):
        self.dispatch = Dispatch(business, scope, user)

    def get(self, path):
        return self.dispatch.get(path)

    def request(self, method, path, body=None):
        return self.dispatch(method, path, body)[0], None


class RemoteServer(MCPServer):
    """One HTTP request of a connected assistant. The Tool Engine applies Controls and counts calls per client."""
    no_writes = "This assistant can't make changes. You can allow it in Settings, under AI."
    instructions = 'Each call acts as the person who approved this assistant.'

    def __init__(self, client, row):
        super().__init__(client, allow_writes=bool(row['allow_changes']))
        # Streamable HTTP without sessions: every request after initialize stands on its own.
        self.state = 'ready'

    def limit(self):
        pass


class Assistants:
    def __init__(self, business):
        self.business, self.database, self.auth = business, business.database, business.auth

    def settings(self):
        return self.business.controls.settings

    def initialize(self):
        self.pruned = 0.0
        self.prune()

    def prune(self):
        """Remove signed-out connections after 30 days, and registrations nobody connected within a day (calls: evlet.tools).
        Runs at start and at most once an hour after that, whether or not assistants are used."""
        now = time.time()
        if now - getattr(self, 'pruned', 0.0) < 3600:
            return
        self.pruned = now
        now = int(now)
        with self.database.transaction() as tx:
            connection = tx.connection
            connection.execute('DELETE FROM evlet_mcp_clients WHERE revoked_at<?', (now - KEEP,))
            connection.execute('DELETE FROM evlet_oauth_codes WHERE expires<=?', (now,))
            connection.execute('DELETE FROM evlet_mcp_tokens WHERE expires<=?', (now,))
            connection.execute('DELETE FROM evlet_oauth_clients WHERE created_at<? AND NOT EXISTS '
                               '(SELECT 1 FROM evlet_mcp_clients c WHERE c.oauth_client=evlet_oauth_clients.id)', (now - 86400,))

    def allowed(self, user_id):
        """Allow MCP on the person (Controls, Users)."""
        with self.database._connection() as connection:
            row = connection.execute('SELECT allow_mcp FROM evlet_users WHERE id=?', (user_id,)).fetchone()
        return row is not None and bool(row[0])

    def narrow(self, user, row=None):
        """The user an assistant acts as: never more than the person, minus hidden models and apps left out."""
        settings = self.settings()
        if not settings['mcp.enabled']:
            raise AccessError(403, OFF)
        if not self.allowed(user.id):
            raise AccessError(403, NOT_ALLOWED)
        hidden = set(settings['mcp.hidden_models'])
        apps = None if row is None or row['apps'] is None else set(json.loads(row['apps']))
        app_of = self.business.controls.app_of
        models = None
        if hidden or apps is not None:
            models = self.auth.covered(lambda name: '*' not in hidden and name not in hidden and (apps is None or app_of.get(name) in apps))
        write = settings['mcp.allow_writes'] and (row is None or bool(row['allow_changes']))
        return User(user.id, user.username, user.role, user.links, user.two_step, access=KeyAccess(write, models, None, 0 if row is None else row['id']))

    def origin(self, scope):
        """The origin of the primary host this request came to, or None (sub-hosts and unknown hosts)."""
        if not self.business.controls.host_allowed(scope):
            return None
        host = dict(scope.get('headers', [])).get(b'host', b'').decode('latin1')
        return next((origin for origin in sorted(self.auth.origins) if urlsplit(origin).netloc == host), None)

    # ---------- /.well-known and /_/ ----------

    def dispatch(self, scope, raw):
        from .app import HTTPError
        from .business import Download
        path, method = scope['path'], scope['method']
        names = [key.lower() for key, _ in scope.get('headers', [])]
        if any(names.count(name) > 1 for name in (b'host', b'origin', b'authorization', b'content-type')):
            raise AccessError(400, 'Repeated header')
        origin = self.origin(scope)
        if origin is None or (not self.settings()['mcp.enabled'] and path != '/_/mcp'):
            raise AccessError(404, NOT_FOUND)
        sent = dict(scope.get('headers', [])).get(b'origin')
        if sent is not None and sent.decode('latin1') not in self.auth.origins:
            raise AccessError(403, 'Unrecognized origin')
        expect = 'POST' if path in ('/_/mcp', '/_/oauth/register', '/_/oauth/token', '/_/oauth/revoke') else 'GET'
        if method != expect:
            raise HTTPError(405, 'Method not allowed', [(b'allow', expect.encode())])
        if path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/_/mcp'):
            # RFC 9728: the metadata path names the resource it describes.
            resource = origin + path.removeprefix('/.well-known/oauth-protected-resource')
            return 200, {'resource': resource, 'authorization_servers': [origin], 'bearer_methods_supported': ['header'],
                         'scopes_supported': ['mcp'], 'resource_name': (self.business.application or {}).get('name', 'Evlet')}, []
        if path == '/.well-known/oauth-authorization-server':  # RFC 8414: the issuer has no path
            methods = ['none', 'client_secret_post', 'client_secret_basic']
            return 200, {'issuer': origin, 'authorization_endpoint': origin + '/_/oauth/authorize', 'token_endpoint': origin + '/_/oauth/token',
                         'registration_endpoint': origin + '/_/oauth/register', 'revocation_endpoint': origin + '/_/oauth/revoke',
                         'response_types_supported': ['code'], 'grant_types_supported': ['authorization_code', 'refresh_token'],
                         'code_challenge_methods_supported': ['S256'], 'token_endpoint_auth_methods_supported': methods,
                         'revocation_endpoint_auth_methods_supported': methods, 'scopes_supported': ['mcp'],
                         'authorization_response_iss_parameter_supported': True}, []
        self.prune()
        if path == '/_/oauth/authorize':
            from .ui.routes import page_theme
            from .ui.serving import static_response
            try:
                user = self.business.auth.session(scope)[0]
            except AccessError:
                user = None
            response = static_response('/', theme=page_theme(self.business, scope, user))
            if response is None:
                raise AccessError(404, 'UI assets are not built. Run the frontend production build.')
            return response
        try:
            if path == '/_/oauth/register':
                return 201, self.register(scope, self.body(scope, raw, 'application/json')), []
            if path == '/_/oauth/token':
                return 200, self.token(scope, self.body(scope, raw, 'application/x-www-form-urlencoded')), [(b'pragma', b'no-cache')]
            if path == '/_/oauth/revoke':
                self.revoke_token(scope, self.body(scope, raw, 'application/x-www-form-urlencoded'))
                return 200, {}, []
        except OAuthError as exc:
            return exc.status, {'error': exc.code, 'error_description': exc.description}, [(b'www-authenticate', b'Basic')] if exc.status == 401 else []
        if path == '/_/mcp':
            status, payload = self.mcp(scope, raw, origin)
            return status, Download(b'') if payload is None else payload, []
        raise AccessError(404, NOT_FOUND)

    @staticmethod
    def body(scope, raw, kind):
        sent = dict(scope.get('headers', [])).get(b'content-type', b'').split(b';', 1)[0].strip().lower()
        if sent != kind.encode():
            raise OAuthError('invalid_request', f'Send {kind}.')
        try:
            if kind == 'application/json':
                value = strict_json(raw)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
            pairs = parse_qs(raw.decode('ascii'), keep_blank_values=True, max_num_fields=20)
        except (ValueError, UnicodeError, RecursionError):
            raise OAuthError('invalid_request', 'The request body could not be read.') from None
        if any(len(values) != 1 for values in pairs.values()):
            raise OAuthError('invalid_request', 'Send each parameter once.')
        return {key: values[0] for key, values in pairs.items()}

    def throttle(self, scope, kind, limit, seconds):
        """A per-network limit shared by every process, kept with the sign-in limits."""
        key = digest(f'{kind}:' + client_network(self.auth.address(scope)))
        now = int(time.time())
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT attempts, resets FROM evlet_login_limits WHERE client=?', (key,)).fetchone()
            if row is not None and row['resets'] <= now:
                tx.connection.execute('DELETE FROM evlet_login_limits WHERE client=?', (key,))
                row = None
            if row is not None and row['attempts'] >= limit:
                raise OAuthError('slow_down', 'Too many requests. Try again later.', 429)
            tx.connection.execute('INSERT INTO evlet_login_limits VALUES (?,1,?) ON CONFLICT(client) DO UPDATE SET attempts=evlet_login_limits.attempts+1', (key, now + seconds))

    def register(self, scope, body):
        """Dynamic client registration (RFC 7591). Public clients use PKCE; others also get a secret."""
        self.throttle(scope, 'oauth-register', 20, 3600)
        uris = body.get('redirect_uris')
        if not isinstance(uris, list) or not 1 <= len(uris) <= 5 or not all(redirect_uri_ok(uri) for uri in uris):
            raise OAuthError('invalid_redirect_uri', 'Use 1 to 5 redirect URIs: https, http on a loopback address, or an app scheme.')
        name = body.get('client_name', 'AI assistant')
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise OAuthError('invalid_client_metadata', 'Use a client name of at most 100 characters.')
        method = body.get('token_endpoint_auth_method', 'none')
        grants, responses = body.get('grant_types', ['authorization_code']), body.get('response_types', ['code'])
        if method not in ('none', 'client_secret_post', 'client_secret_basic') or not isinstance(grants, list) or not isinstance(responses, list) \
                or set(map(str, grants)) - {'authorization_code', 'refresh_token'} or set(map(str, responses)) - {'code'}:
            raise OAuthError('invalid_client_metadata', 'This server supports the authorization code flow with PKCE and refresh tokens.')
        client_id = 'mcp_' + secrets.token_urlsafe(16)
        secret = None if method == 'none' else 'evs_' + secrets.token_urlsafe(32)
        now = int(time.time())
        with self.database.transaction() as tx:
            # Registrations with no connected client after a day are removed; signed-out connections don't keep them.
            tx.connection.execute('DELETE FROM evlet_oauth_clients WHERE created_at<? AND NOT EXISTS '
                                  '(SELECT 1 FROM evlet_mcp_clients c WHERE c.oauth_client=evlet_oauth_clients.id AND c.revoked_at IS NULL)', (now - 86400,))
            if tx.connection.execute('SELECT count(*) FROM evlet_oauth_clients').fetchone()[0] >= MAX_CLIENTS:
                raise OAuthError('temporarily_unavailable', 'Too many registered clients. Try again tomorrow.', 503)
            tx.connection.execute('INSERT INTO evlet_oauth_clients(id,name,redirect_uris,secret,auth_method,created_at) VALUES (?,?,?,?,?,?)',
                                  (client_id, name.strip(), json.dumps(uris), secret and digest(secret), method, now))
        result = {'client_id': client_id, 'client_id_issued_at': now, 'client_name': name.strip(), 'redirect_uris': uris,
                  'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'], 'token_endpoint_auth_method': method}
        return {**result, 'client_secret': secret, 'client_secret_expires_at': 0} if secret else result

    def client(self, scope, form):
        """The registered client making a token or revocation request, with its secret checked when it has one."""
        client_id, secret = form.get('client_id'), form.get('client_secret')
        value = dict(scope.get('headers', [])).get(b'authorization', b'').decode('latin1')
        used = 'client_secret_post' if secret is not None else 'none'
        if value[:6].lower() == 'basic ':
            used = 'client_secret_basic' if secret is None else 'both'
            try:
                client_id, _, secret = base64.b64decode(value[6:].strip(), validate=True).decode().partition(':')
                client_id, secret = unquote(client_id), unquote(secret)
            except (binascii.Error, UnicodeError):
                raise OAuthError('invalid_client', 'Unknown client.', 401) from None
        with self.database._connection() as connection:
            row = connection.execute('SELECT * FROM evlet_oauth_clients WHERE id=?', (client_id or '',)).fetchone()
        # The client authenticates exactly the way it registered: a public client never sends a secret.
        if row is None or used != row['auth_method'] or (row['secret'] is not None and not (secret and hmac.compare_digest(row['secret'], digest(secret)))):
            raise OAuthError('invalid_client', 'Unknown client.', 401)
        return row

    @staticmethod
    def end(connection, grant_id):
        connection.execute('DELETE FROM evlet_mcp_tokens WHERE client_id=?', (grant_id,))
        connection.execute('UPDATE evlet_mcp_clients SET revoked_at=? WHERE id=? AND revoked_at IS NULL', (int(time.time()), grant_id))

    @staticmethod
    def issue(connection, grant_id):
        access, refresh, now = 'evm_' + secrets.token_urlsafe(32), 'evr_' + secrets.token_urlsafe(32), int(time.time())
        for token, kind, seconds in ((access, 'access', ACCESS_SECONDS), (refresh, 'refresh', REFRESH_SECONDS)):
            connection.execute('INSERT INTO evlet_mcp_tokens(hash,client_id,kind,expires,used) VALUES (?,?,?,?,0)', (digest(token), grant_id, kind, now + seconds))
        return {'access_token': access, 'token_type': 'Bearer', 'expires_in': ACCESS_SECONDS, 'refresh_token': refresh, 'scope': 'mcp'}

    def token(self, scope, form):
        """Authorization code with PKCE (S256), and refresh tokens that rotate on every use."""
        self.throttle(scope, 'oauth-token', 60, 60)
        client = self.client(scope, form)
        invalid = OAuthError('invalid_grant', 'This sign-in is no longer valid. Connect the assistant again.')
        now = int(time.time())
        with self.database.transaction() as tx:
            connection = tx.connection
            connection.execute('DELETE FROM evlet_mcp_tokens WHERE expires<=?', (now,))
            connection.execute('DELETE FROM evlet_oauth_codes WHERE expires<=?', (now,))
            if form.get('grant_type') == 'authorization_code':
                code = connection.execute('SELECT * FROM evlet_oauth_codes WHERE hash=?', (digest(form.get('code', '')),)).fetchone()
                if code is None or code['oauth_client'] != client['id']:
                    raise invalid
                if code['grant_id'] is not None:
                    # A code used twice: the connection it made is signed out (RFC 6749, 4.1.2).
                    self.end(connection, code['grant_id'])
                    result = invalid
                else:
                    verifier = form.get('code_verifier', '')
                    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                    if form.get('redirect_uri') != code['redirect_uri'] or not VERIFIER.fullmatch(verifier) or not hmac.compare_digest(expected, code['challenge']):
                        raise invalid
                    if not connection.execute("SELECT 1 FROM evlet_users u JOIN evlet_roles r ON r.id=u.role AND r.realm='desk' WHERE u.id=? AND u.active=1", (code['user_id'],)).fetchone():
                        raise invalid
                    latest = connection.execute('SELECT apps FROM evlet_mcp_clients WHERE user_id=? ORDER BY id DESC LIMIT 1', (code['user_id'],)).fetchone()
                    # Connecting the same client again replaces the earlier connection.
                    for row in connection.execute('SELECT id FROM evlet_mcp_clients WHERE user_id=? AND oauth_client=? AND revoked_at IS NULL', (code['user_id'], client['id'])).fetchall():
                        self.end(connection, row['id'])
                    grant_id = connection.execute('INSERT INTO evlet_mcp_clients(user_id,oauth_client,client_name,created_at,calls,allow_changes,apps) VALUES (?,?,?,?,0,?,?)',
                                                  (code['user_id'], client['id'], client['name'], now, code['allow_changes'], latest['apps'] if latest else None)).lastrowid
                    connection.execute('UPDATE evlet_oauth_codes SET grant_id=? WHERE hash=?', (grant_id, code['hash']))
                    person = connection.execute('SELECT username FROM evlet_users WHERE id=?', (code['user_id'],)).fetchone()['username']
                    audit(connection, person, AREA, f"{client['name']} · {person}", 'Client connected' + (', can make changes' if code['allow_changes'] else ''))
                    result = self.issue(connection, grant_id)
            elif form.get('grant_type') == 'refresh_token':
                row = connection.execute("SELECT t.hash, t.client_id, t.used, m.oauth_client, u.active FROM evlet_mcp_tokens t JOIN evlet_mcp_clients m ON m.id=t.client_id "
                                         "JOIN evlet_users u ON u.id=m.user_id JOIN evlet_roles r ON r.id=u.role AND r.realm='desk' "
                                         "WHERE t.hash=? AND t.kind='refresh' AND m.revoked_at IS NULL", (digest(form.get('refresh_token', '')),)).fetchone()
                if row is None or row['oauth_client'] != client['id'] or not row['active']:
                    raise invalid
                if row['used']:
                    # A rotated refresh token came back: someone else may hold it, so the connection is signed out.
                    self.end(connection, row['client_id'])
                    result = invalid
                else:
                    connection.execute('UPDATE evlet_mcp_tokens SET used=1 WHERE hash=?', (row['hash'],))
                    connection.execute("DELETE FROM evlet_mcp_tokens WHERE client_id=? AND kind='access'", (row['client_id'],))
                    result = self.issue(connection, row['client_id'])
            else:
                raise OAuthError('unsupported_grant_type', 'Use authorization_code or refresh_token.')
        if isinstance(result, OAuthError):
            raise result
        return result

    def revoke_token(self, scope, form):
        """Token revocation (RFC 7009): either token signs the whole connection out. Unknown tokens are ignored."""
        self.throttle(scope, 'oauth-token', 60, 60)
        client = self.client(scope, form)
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT t.client_id, m.oauth_client, m.client_name, u.username FROM evlet_mcp_tokens t JOIN evlet_mcp_clients m ON m.id=t.client_id '
                                        'JOIN evlet_users u ON u.id=m.user_id WHERE t.hash=?', (digest(form.get('token', '')),)).fetchone()
            if row is not None and row['oauth_client'] == client['id']:
                self.end(tx.connection, row['client_id'])
                audit(tx.connection, row['username'], AREA, f"{row['client_name']} · {row['username']}", 'Signed out by the client')

    # ---------- the MCP endpoint ----------

    def mcp(self, scope, raw, origin):
        from .app import HTTPError
        token = self.auth.bearer(scope)
        row = None
        if token and ACCESS.fullmatch(token):
            with self.database._connection() as connection:
                # Assistants are for the desk: a person whose role is now a portal role is refused like a signed-out one.
                row = connection.execute('SELECT m.*, u.username, u.role FROM evlet_mcp_tokens t JOIN evlet_mcp_clients m ON m.id=t.client_id JOIN evlet_users u ON u.id=m.user_id '
                                         "JOIN evlet_roles r ON r.id=u.role AND r.realm='desk' "
                                         "WHERE t.hash=? AND t.kind='access' AND t.expires>? AND m.revoked_at IS NULL AND u.active=1", (digest(token), int(time.time()))).fetchone()
                links = () if row is None else tuple((item['model'], item['record_id']) for item in connection.execute(
                    'SELECT model, record_id FROM evlet_user_links WHERE user_id=? ORDER BY model, record_id', (row['user_id'],)))
        if row is None:
            challenge = f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/_/mcp"' + (', error="invalid_token"' if token else '')
            raise HTTPError(401, 'Sign in to continue', [(b'www-authenticate', challenge.encode())])
        user = self.narrow(User(row['user_id'], row['username'], row['role'], links), row)
        if dict(scope.get('headers', [])).get(b'content-type', b'').split(b';', 1)[0].strip().lower() != b'application/json':
            raise HTTPError(415, 'Send application/json')
        try:
            message = strict_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            return 400, MCPServer.error(None, -32700, 'Invalid JSON')
        if isinstance(message, list):
            return 400, MCPServer.error(None, -32600, 'Send one JSON-RPC message per request')
        version = dict(scope.get('headers', [])).get(b'mcp-protocol-version')
        if version is not None and version.decode('latin1') not in SUPPORTED:
            return 400, MCPServer.error(None, -32600, 'Unsupported MCP protocol version')
        if isinstance(message, dict) and 'method' not in message and ('result' in message or 'error' in message):
            return 202, None  # A client's reply; this server sends no requests, so there is nothing to match it with.
        if isinstance(message, dict) and message.get('method') != 'tools/call' and not self.auth._count(f"mcp:{row['id']}", LIGHT_RATE):
            return 429, MCPServer.error(message.get('id') if type(message.get('id')) in (str, int) else None, -32000, 'Too many requests. Try again in a minute.')
        server = RemoteServer(LocalClient(self.business, scope, user), row)
        if isinstance(message, dict) and message.get('method') == 'initialize':
            server.state = 'new'
        output = server.message(message)
        return (202, None) if output is None else (200, output)

    # ---------- consent, the personal page and Controls ----------

    def request(self, query, origin):
        """Check an authorization request. A problem with the client or its redirect URI is shown, never redirected."""
        try:
            params = parse_qs(query.decode('ascii'), keep_blank_values=True, max_num_fields=20)
        except (ValueError, UnicodeError):
            params = {}
        if any(len(values) != 1 for values in params.values()):
            raise AccessError(400, 'The assistant sent each detail more than once. Start connecting again from the assistant.')
        params = {key: values[0] for key, values in params.items()}
        with self.database._connection() as connection:
            client = connection.execute('SELECT * FROM evlet_oauth_clients WHERE id=?', (params.get('client_id', ''),)).fetchone()
        if client is None:
            raise AccessError(400, "This assistant isn't registered here. Start connecting again from the assistant.")
        if params.get('redirect_uri') not in json.loads(client['redirect_uris']):
            raise AccessError(400, "The assistant asked to return to an address it didn't register. Start connecting again from the assistant.")
        if params.get('response_type') != 'code' or params.get('code_challenge_method') != 'S256' or not CHALLENGE.fullmatch(params.get('code_challenge', '')):
            raise AccessError(400, 'The assistant sent an incomplete sign-in request. Update the assistant and try again.')
        if len(params.get('state', '')) > 1000 or ('resource' in params and params['resource'].rstrip('/') not in (origin, origin + '/_/mcp')):
            raise AccessError(400, 'This sign-in request is for another server. Start connecting again from the assistant.')
        return client, params

    def back(self, params, origin, **values):
        uri = params['redirect_uri']
        values.update({'state': params['state']} if 'state' in params else {})
        return uri + ('&' if urlsplit(uri).query else '?') + urlencode({**values, 'iss': origin})

    def consent(self, user, body, origin):
        if not isinstance(body, dict) or set(body) - {'query', 'approve', 'allow_changes'} or not isinstance(body.get('query'), str) \
                or type(body.get('approve')) is not bool or type(body.get('allow_changes', False)) is not bool:
            raise ValidationError({'_form': 'Send query, approve and allow_changes'})
        client, params = self.request(body['query'].encode('ascii', 'replace'), origin)
        if body['approve'] and not self.allowed(user.id):
            raise AccessError(403, NOT_ALLOWED)
        if not body['approve']:
            return {'redirect': self.back(params, origin, error='access_denied')}
        with self.database._connection() as connection:
            active = connection.execute('SELECT count(*) FROM evlet_mcp_clients WHERE user_id=? AND revoked_at IS NULL AND oauth_client<>?', (user.id, client['id'])).fetchone()[0]
        if active >= MAX_PER_USER:
            raise AccessError(409, f'You have {MAX_PER_USER} connected assistants. Disconnect one in Settings first.')
        code = secrets.token_urlsafe(32)
        # The person's choice is kept; it only has effect while Controls allows changes (mcp.allow_writes).
        changes = body.get('allow_changes', False)
        with self.database.transaction() as tx:
            tx.connection.execute('INSERT INTO evlet_oauth_codes(hash,oauth_client,user_id,redirect_uri,challenge,allow_changes,expires) VALUES (?,?,?,?,?,?,?)',
                                  (digest(code), client['id'], user.id, params['redirect_uri'], params['code_challenge'], int(changes), int(time.time()) + CODE_SECONDS))
            audit(tx.connection, user.username, AREA, f"{client['name']} · {user.username}", 'Consent given')
        return {'redirect': self.back(params, origin, code=code)}

    def visible_apps(self, user):
        models = self.business.controls.models()
        return [app['id'] for app in self.business.controls.apps
                if any(name in models and self.business.policy.allows(user, models[name], 'read') for name in app['models'])]

    def mine(self, user, origin):
        settings = self.settings()
        # Show the latest counts and times: save the batched ones first (evlet/writes.py LogBuffer).
        self.database.logs.flush()
        with self.database._connection() as connection:
            clients = [dict(row) for row in connection.execute('SELECT id, client_name, created_at, last_used, calls, allow_changes, apps FROM evlet_mcp_clients '
                                                                'WHERE user_id=? AND revoked_at IS NULL ORDER BY id DESC', (user.id,))]
            requests = [dict(row) for row in connection.execute("SELECT at, client AS client_name, tool, model, kind, outcome FROM evlet_tool_calls "
                                                                 "WHERE user_id=? AND origin IN ('mcp','eva','schedule') AND at>? ORDER BY id DESC LIMIT 50",
                                                                 (user.id, int(time.time()) - KEEP))]
        for item in clients:
            item['allow_changes'], item['apps'] = bool(item['allow_changes']), None if item['apps'] is None else json.loads(item['apps'])
        return {'enabled': settings['mcp.enabled'], 'allowed': self.allowed(user.id), 'writes': settings['mcp.allow_writes'], 'address': origin + '/_/mcp',
                'clients': clients, 'requests': requests, 'apps': self.visible_apps(user), 'chosen': clients[0]['apps'] if clients else None}

    def mine_row(self, connection, user, client_id):
        row = connection.execute('SELECT * FROM evlet_mcp_clients WHERE id=? AND user_id=? AND revoked_at IS NULL', (client_id, user.id)).fetchone()
        if row is None:
            raise AccessError(404, 'Assistant not found')
        return row

    def choose(self, user, client_id, body):
        """Change one assistant's "Can make changes", or (client_id None) the apps every assistant of this person can use."""
        if client_id is None:
            apps = body.get('apps') if isinstance(body, dict) and set(body) == {'apps'} else False
            visible = self.visible_apps(user)
            if apps is not None and (not isinstance(apps, list) or not apps or any(app not in visible for app in apps) or len(set(apps)) != len(apps)):
                raise ValidationError({'apps': "Choose apps you can use, each once. You can't give an assistant more than your own access."})
            # An explicit list stays explicit: apps installed later aren't added on their own.
            stored = None if apps is None else json.dumps([app for app in visible if app in apps])
            with self.database.transaction() as tx:
                tx.connection.execute('UPDATE evlet_mcp_clients SET apps=? WHERE user_id=? AND revoked_at IS NULL', (stored, user.id))
            return
        if not isinstance(body, dict) or set(body) != {'allow_changes'} or type(body['allow_changes']) is not bool:
            raise ValidationError({'allow_changes': 'Use true or false'})
        with self.database.transaction() as tx:
            row = self.mine_row(tx.connection, user, client_id)
            if bool(row['allow_changes']) != body['allow_changes']:
                tx.connection.execute('UPDATE evlet_mcp_clients SET allow_changes=? WHERE id=?', (int(body['allow_changes']), client_id))
                audit(tx.connection, user.username, AREA, f"{row['client_name']} · {user.username}", 'Can make changes: ' + ('on' if body['allow_changes'] else 'off'))

    def revoke(self, actor, client_id, personal):
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT m.client_name, m.user_id, u.username, u.role FROM evlet_mcp_clients m JOIN evlet_users u ON u.id=m.user_id '
                                        'WHERE m.id=? AND m.revoked_at IS NULL', (client_id,)).fetchone()
            if row is None or (personal and row['user_id'] != actor.id):
                raise AccessError(404, 'Assistant not found')
            if not personal and row['role'] == 'admin' and actor.role != 'admin':
                raise AccessError(403, ADMIN_ONLY)
            self.end(tx.connection, client_id)
            audit(tx.connection, actor.username, AREA, f"{row['client_name']} · {row['username']}", 'Disconnected' if personal else 'Signed out')

    def overview(self, scope):
        """Controls: the address, connected clients and totals for the last 24 hours. Per-request detail stays with each person."""
        # Show the latest counts and times: save the batched ones first (evlet/writes.py LogBuffer).
        self.database.logs.flush()
        with self.database._connection() as connection:
            clients = [dict(row) for row in connection.execute('SELECT m.id, m.client_name, u.username, u.role, m.created_at, m.last_used, m.calls FROM evlet_mcp_clients m '
                                                                'JOIN evlet_users u ON u.id=m.user_id WHERE m.revoked_at IS NULL ORDER BY m.id DESC LIMIT 500')]
            counts = connection.execute("SELECT kind, outcome, count(*) AS n FROM evlet_tool_calls WHERE origin='mcp' AND at>? GROUP BY kind, outcome", (int(time.time()) - 86400,)).fetchall()
            # Calls in the last 24 hours per connected client, for the clients table.
            recent = {row['client_id']: row['n'] for row in connection.execute(
                "SELECT client_id, count(*) AS n FROM evlet_tool_calls WHERE origin='mcp' AND at>? GROUP BY client_id", (int(time.time()) - 86400,))}
        totals = {'reads': 0, 'writes': 0, 'denied': 0}
        for row in counts:
            if row['outcome'] != 'approval':
                totals['denied' if row['outcome'] == 'denied' else row['kind'] + 's'] += row['n']
        for item in clients:
            item['recent'] = recent.get(item['id'], 0)
        return {'address': (self.origin(scope) or '') + '/_/mcp', 'clients': clients, 'totals': totals}

    # ---------- reachability (Controls, MCP) ----------

    REACH_SECONDS = 30

    @staticmethod
    def fetch(method, url, body=None, headers=None):
        """One request to this site's public address, without redirects or proxies. Returns (status, headers, body)."""
        from urllib.error import HTTPError as Failure
        from urllib.request import ProxyHandler, Request, build_opener
        from .connector import NoRedirect
        request = Request(url, data=body, method=method, headers={'User-Agent': 'Evlet reachability check', **(headers or {})})
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
                return response.status, {key.lower(): value for key, value in response.headers.items()}, response.read(65536)
        except Failure as exc:
            with exc:
                return exc.code, {key.lower(): value for key, value in exc.headers.items()}, exc.read(65536)

    def reach(self, scope, user):
        """Whether the public address reaches /_/mcp: its discovery document, then an unsigned call that must get 401
        with a Bearer challenge. Admins only, on demand, and kept for REACH_SECONDS so a busy button can't flood it."""
        if user.role != 'admin':
            raise AccessError(403, ADMIN_ONLY)
        origin = self.origin(scope)
        url = urlsplit(origin or '')
        local = url.hostname in (None, 'localhost') or url.hostname.endswith('.localhost') or url.scheme != 'https'
        if not local:
            try:
                local = ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                pass
        if local:
            return {'checked': False, 'reachable': None, 'reason': 'Only on a site with a domain.'}
        if not self.settings()['mcp.enabled']:
            return {'checked': False, 'reachable': None, 'reason': 'Turn the MCP server on, then check again.'}
        now = time.time()
        cached = getattr(self, 'reached', {}).get(origin)
        if cached and now - cached['at'] < self.REACH_SECONDS:
            return cached
        result = {'checked': True, 'at': int(now), **self.probe(origin)}
        self.reached = {origin: result}
        return result

    def probe(self, origin):
        blocked = ('Blocked by the proxy. On the server, run evlet setup again with --host and --apply, '
                   'or write the proxy file again with evlet proxy.')
        try:
            status, _, body = self.fetch('GET', origin + '/.well-known/oauth-protected-resource/_/mcp')
            try:
                resource = json.loads(body).get('resource') if status == 200 else None
            except (ValueError, AttributeError):
                resource = None
            if resource != origin + '/_/mcp':
                return {'reachable': False, 'reason': f'The sign-in details at {origin}/.well-known/oauth-protected-resource/_/mcp did not come from '
                                                      f'this instance (status {status}). Check the DNS record and the proxy.'}
            status, headers, _ = self.fetch('POST', origin + '/_/mcp', b'{}', {'Content-Type': 'application/json'})
        except (OSError, ValueError, HTTPException) as exc:
            return {'reachable': False, 'reason': f'Could not connect to {origin}: {getattr(exc, "reason", exc)}'}
        challenge = headers.get('www-authenticate', '')
        if status == 401 and challenge.startswith('Bearer ') and 'resource_metadata=' in challenge:
            return {'reachable': True, 'reason': 'Reachable from the internet.'}
        if status == 404:
            return {'reachable': False, 'reason': blocked}
        return {'reachable': False, 'reason': f'{origin}/_/mcp answered {status} instead of asking to sign in.'}

    def api(self, scope, body, user):
        """/api/_oauth/authorize (the consent page) and /api/_settings/me/ai (a person's own assistants). Signed-in sessions only."""
        path, method = scope['path'], scope['method']
        origin = self.origin(scope)
        # Desk only: the primary host (origin), and never a portal session.
        if origin is None or self.business.settings.portal(scope, user):
            raise AccessError(404, NOT_FOUND)
        self.prune()
        if path == '/api/_oauth/authorize':
            if not self.settings()['mcp.enabled']:
                raise AccessError(403, OFF)
            if method == 'GET':
                client, params = self.request(scope.get('query_string', b''), origin)
                if not self.allowed(user.id):
                    raise AccessError(403, NOT_ALLOWED)
                return 200, {'client': client['name'], 'returns_to': urlsplit(params['redirect_uri']).netloc or urlsplit(params['redirect_uri']).scheme,
                             'user': user.username, 'writes': self.settings()['mcp.allow_writes']}, []
            if method == 'POST':
                return 200, self.consent(user, body, origin), []
        match = re.fullmatch(r'/api/_settings/me/ai(?:/([1-9][0-9]{0,18}))?', path)
        if match and (match[1] is None or int(match[1]) <= 2**63 - 1):
            client_id = None if match[1] is None else int(match[1])
            if method == 'GET' and client_id is None:
                return 200, self.mine(user, origin), []
            if method == 'PATCH':
                self.choose(user, client_id, body)
                return 200, self.mine(user, origin), []
            if method == 'DELETE' and client_id is not None:
                self.revoke(user, client_id, personal=True)
                return 200, self.mine(user, origin), []
        raise AccessError(404, NOT_FOUND)
