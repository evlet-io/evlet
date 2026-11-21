"""Optional OpenID Connect sign-in; joserfc verifies ID tokens, Evlet keeps local authority."""

import base64
import hashlib
import hmac
import http.client
from http.cookies import CookieError, SimpleCookie
import ipaddress
import json
import math
import re
import secrets
import socket
import ssl
from threading import BoundedSemaphore, Lock, Timer
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from . import qb
from .adapter import Column, TableDefinition
from .auth import AccessError, allow, client_network

TICKETS = TableDefinition('evlet_oidc_tickets', (
    Column('token', 'TEXT', primary=True), Column('browser', 'TEXT', required=True),
    Column('kind', 'TEXT', required=True), Column('expires', 'INTEGER', required=True),
    Column('configuration', 'TEXT', required=True), Column('realm', 'TEXT', required=True),
    Column('nonce', 'TEXT'), Column('user_id', 'INTEGER'), Column('session_generation', 'INTEGER'),
))
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
TIMEOUT, CACHE_SECONDS, TICKET_SECONDS, MAX_RESPONSE = 10, 300, 300, 128 * 1024
FAILED = 'Company sign-in could not be completed. Start again or use your local password.'


def ensure_tickets(connection):
    """Framework step 0015_oidc_tickets; also run at startup for databases that skip the steps (a no-op once present)."""
    connection.ensure_table(TICKETS)
    connection.ensure_index('evlet_oidc_ticket_expiry', 'evlet_oidc_tickets', ('expires',))


class ProviderError(Exception):
    """Carries no provider response, URL query, code, secret or token."""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def encoded(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def s256(verifier):
    """RFC 7636 S256 code challenge: base64url SHA-256 of the ASCII verifier, unpadded."""
    return encoded(hashlib.sha256(verifier.encode('ascii')).digest())


def at_hash(access_token):
    """OIDC Core 3.1.3.6 at_hash for RS256/ES256: base64url of the left half of SHA-256."""
    return encoded(hashlib.sha256(access_token.encode('ascii')).digest()[:16])


def auth_method(metadata, secret):
    """client_secret_basic is the discovery default; client_secret_post only when advertised instead."""
    if not secret:
        return 'none'
    methods = metadata.get('token_endpoint_auth_methods_supported', ['client_secret_basic'])
    return next((method for method in ('client_secret_basic', 'client_secret_post') if method in methods), None)


class OIDCFlow:
    def __init__(self, business):
        try:
            from joserfc import jwt
            from joserfc.jwk import KeySet
        except ImportError:
            raise ValueError('OIDC sign-in requires the optional package: pip install "evlet[oidc]"') from None
        self.business, self.auth, self.database = business, business.auth, business.database
        self.config = self.auth.oidc
        self.jwt, self.key_set = jwt, KeySet
        callback = urlsplit(self.config.redirect_uri)
        self.origin, self.host = f'{callback.scheme}://{callback.netloc}', callback.netloc.encode()
        self.cookie_name = '__Host-evlet_oidc' if self.auth.secure_cookies else 'evlet_oidc'
        self.fingerprint = digest(json.dumps([self.config.issuer, self.config.client_id, self.config.redirect_uri,
                                             sorted(self.config.subjects.items())], separators=(',', ':')))
        self.slots, self.cache_lock = BoundedSemaphore(4), Lock()
        self.metadata_cache, self.keys_cache = None, None

    def initialize(self):
        with self.database.transaction(store='sessions') as tx:
            ensure_tickets(tx.connection)
            tx.connection.run(qb.delete('evlet_oidc_tickets').where(qb.le('expires', int(time.time()))))

    def options(self, scope):
        return {'label': self.config.label, 'start': '/_/oidc/start'} if dict(scope.get('headers', [])).get(b'host') == self.host else None

    def cookie(self, value):
        text = f'{self.cookie_name}={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={TICKET_SECONDS if value else 0}'
        if self.auth.secure_cookies:
            text += '; Secure'
        return b'set-cookie', text.encode()

    def browser_ticket(self, scope):
        jar = SimpleCookie()
        try:
            jar.load(dict(scope.get('headers', [])).get(b'cookie', b'').decode('latin1'))
            item = jar.get(self.cookie_name)
            token, proof = (item.value if item else '').split('.')
            if TOKEN.fullmatch(token) and TOKEN.fullmatch(proof):
                return token, proof
        except (CookieError, ValueError, UnicodeError):
            pass
        raise AccessError(401, FAILED)

    def dispatch(self, scope, raw):
        path, method = scope['path'], scope['method']
        callback = path == '/_/oidc/callback'
        allow(method, 'POST' if path == '/_/oidc/finish' else 'GET')
        headers = scope.get('headers', [])
        # IdP callbacks are cross-site navigations, protected by one-use state and browser proof.
        if callback:
            names = [name.lower() for name, _ in headers]
            if any(names.count(name) > 1 for name in (b'host', b'cookie', b'origin', b'sec-fetch-site')):
                raise AccessError(403, 'Repeated security header')
            self.auth.guard({**scope, 'headers': [(name, value) for name, value in headers if name.lower() not in (b'origin', b'sec-fetch-site')]})
        else:
            self.auth.guard(scope)
        if dict(headers).get(b'host') != self.host:
            raise AccessError(404, 'Route not found')
        if path == '/_/oidc/finish':
            if dict(headers).get(b'origin') != self.origin.encode():
                raise AccessError(403, 'Company sign-in must finish on its original browser origin')
            if dict(headers).get(b'content-type', b'').split(b';', 1)[0].strip().lower() != b'application/json' or raw.strip() != b'{}':
                raise AccessError(400, 'Send an empty JSON object')
            return self.finish(scope)
        if not self.slots.acquire(blocking=False):
            raise AccessError(503, 'Company sign-in is busy. Try again shortly.')
        try:
            if callback:
                try:
                    return self.callback(scope)
                except (AccessError, ProviderError):
                    return self.redirect('error', self.cookie(''))
            return self.start(scope)
        except ProviderError:
            raise AccessError(503, 'Company sign-in is temporarily unavailable. Use your local password or try again.') from None
        finally:
            self.slots.release()

    @staticmethod
    def redirect(result, cookie):
        return 303, {}, [(b'location', ('/login?oidc=' + result).encode()), cookie,
                         (b'cache-control', b'no-store'), (b'referrer-policy', b'no-referrer')]

    def start(self, scope):
        now = int(time.time())
        client = digest('oidc:' + client_network(self.auth.client_address(scope)))
        with self.database.transaction(store='sessions') as tx:
            tx.connection.run(qb.delete('evlet_login_limits').where(qb.le('resets', now)))
            row = tx.connection.run(qb.select('evlet_login_limits', 'attempts').where(client=client)).one()
            if row and row['attempts'] >= 100:
                raise AccessError(429, 'Too many company sign-in attempts. Try again in 15 minutes.')
            tx.connection.run(qb.insert('evlet_login_limits', client=client, attempts=1, resets=now + 900)
                              .on_conflict('client').do_update(attempts=qb.col('evlet_login_limits.attempts') + 1))
        metadata = self.metadata()
        token, proof, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        # The browser proof is also the PKCE verifier: 43 unreserved characters, sent only to the token endpoint.
        endpoint = metadata['authorization_endpoint']
        url = endpoint + ('&' if urlsplit(endpoint).query else '?') + urlencode({
            'response_type': 'code', 'client_id': self.config.client_id, 'redirect_uri': self.config.redirect_uri,
            'scope': 'openid', 'state': token, 'code_challenge': s256(proof), 'code_challenge_method': 'S256',
            'nonce': nonce, 'response_mode': 'query'})
        with self.database.transaction(store='sessions') as tx:
            tx.connection.run(qb.delete('evlet_oidc_tickets').where(qb.le('expires', now)))
            if tx.connection.run(qb.select('evlet_oidc_tickets', qb.count())).value() >= 5000:
                raise AccessError(503, 'Company sign-in is busy. Try again shortly.')
            tx.connection.run(qb.insert('evlet_oidc_tickets', token=digest(token), browser=digest(proof), kind='pending',
                                       expires=now + TICKET_SECONDS, configuration=self.fingerprint, realm=self.auth.realm(scope), nonce=nonce))
        return 303, {}, [(b'location', url.encode()), self.cookie(token + '.' + proof),
                         (b'cache-control', b'no-store'), (b'referrer-policy', b'no-referrer')]

    def consume(self, connection, scope, kind):
        token, proof = self.browser_ticket(scope)
        row = connection.run(qb.select('evlet_oidc_tickets').where(token=digest(token), kind=kind)).one()
        if row is None or not hmac.compare_digest(row['browser'], digest(proof)) or row['expires'] <= int(time.time()) \
                or row['configuration'] != self.fingerprint or row['realm'] != self.auth.realm(scope):
            raise AccessError(401, FAILED)
        connection.run(qb.delete('evlet_oidc_tickets').where(token=row['token']))
        return row, proof

    def callback(self, scope):
        query = scope.get('evlet.oidc_query', scope.get('query_string', b''))
        try:
            if len(query) > 8192:
                raise ValueError()
            values = parse_qs(query.decode('ascii'), keep_blank_values=True, strict_parsing=True, max_num_fields=20)
            if any(len(value) != 1 for value in values.values()):
                raise ValueError()
            token, _ = self.browser_ticket(scope)
            if not hmac.compare_digest(values.get('state', [''])[0], token):
                raise ValueError()
            if 'iss' in values and values['iss'][0] != self.config.issuer:
                raise ValueError()
        except (ValueError, UnicodeError, TypeError):
            raise AccessError(401, FAILED) from None
        # Consume before network exchange: concurrent callbacks/provider errors cannot retry this state.
        with self.database.transaction(store='sessions') as tx:
            pending, verifier = self.consume(tx.connection, scope, 'pending')
        code = values.get('code', [''])[0]
        if 'error' in values or not code or len(code) > 4096:
            raise AccessError(401, FAILED)
        metadata = self.metadata()
        # RFC 6749 4.1.3 with client authentication from 2.3.1 (Basic credentials are form-encoded first).
        form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': self.config.redirect_uri, 'code_verifier': verifier}
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        method = auth_method(metadata, self.config.client_secret)
        if method == 'client_secret_basic':
            pair = quote(self.config.client_id) + ':' + quote(self.config.client_secret)
            headers['Authorization'] = 'Basic ' + base64.b64encode(pair.encode()).decode('ascii')
        else:
            form['client_id'] = self.config.client_id
            if method == 'client_secret_post':
                form['client_secret'] = self.config.client_secret
        result = self.request(metadata['token_endpoint'], body=urlencode(form).encode('ascii'), headers=headers)
        claims = self.validate(result, pending['nonce'], metadata)
        username = self.config.subjects.get(claims['sub'])
        if username is None:
            raise AccessError(401, FAILED)
        token, proof = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.database.transaction(store='sessions') as tx:
            account = self.account(tx.connection, username=username)
            if account is None or not account['active'] or account['role_realm'] != pending['realm']:
                raise AccessError(401, FAILED)
            tx.connection.run(qb.insert('evlet_oidc_tickets', token=digest(token), browser=digest(proof), kind='finish',
                                       expires=int(time.time()) + 60, configuration=self.fingerprint, realm=pending['realm'],
                                       user_id=account['id'], session_generation=account['session_generation']))
        return self.redirect('finish', self.cookie(token + '.' + proof))

    def account(self, connection, **where):
        return connection.run(qb.select(qb.table('evlet_users', 'u'), qb.all_columns('u'), qb.named('r.realm', 'role_realm'))
                              .join(qb.table('evlet_roles', 'r'), qb.eq('r.id', qb.col('u.role')))
                              .where(**{'u.' + key: value for key, value in where.items()})).one()

    def finish(self, scope):
        with self.database.transaction(store='sessions') as tx:
            pending, _ = self.consume(tx.connection, scope, 'finish')
            account = self.account(tx.connection, id=pending['user_id'])
            if account is None or not account['active'] or account['role_realm'] != pending['realm'] \
                    or account['session_generation'] != pending['session_generation']:
                raise AccessError(401, FAILED)
            challenge = self.auth.second_step.challenge(tx, account, pending['realm']) if self.auth.second_step is not None else None
            if challenge is not None:
                return 200, challenge, [self.cookie(''), (b'cache-control', b'no-store')]
            answer, headers = self.auth.start_session(tx, scope, account, pending['realm'])
            return 200, answer, [self.cookie(''), *headers, (b'cache-control', b'no-store')]

    def endpoint(self, url):
        if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url):
            raise ProviderError()
        try:
            target = urlsplit(url)
            target.port
        except ValueError:
            raise ProviderError() from None
        try:
            local = target.hostname == 'localhost' or ipaddress.ip_address(target.hostname).is_loopback
        except ValueError:
            local = False
        if target.scheme != 'https' and not (urlsplit(self.config.issuer).scheme == 'http' and target.scheme == 'http' and local):
            raise ProviderError()
        if not target.hostname or target.username is not None or target.password is not None or target.fragment or '\\' in url:
            raise ProviderError()
        return target

    def request(self, url, *, body=None, headers=None):
        """No redirects/proxies; bounded bytes, socket timeout and watchdog for slow headers/bodies."""
        target = self.endpoint(url)
        started = time.monotonic()
        connection = (http.client.HTTPSConnection(target.hostname, target.port, timeout=TIMEOUT, context=ssl.create_default_context())
                      if target.scheme == 'https' else http.client.HTTPConnection(target.hostname, target.port, timeout=TIMEOUT))
        watchdog = None
        try:
            connection.connect()
            remaining = TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                raise ProviderError()
            sock = connection.sock
            def stop():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            watchdog = Timer(remaining, stop)
            watchdog.daemon = True
            watchdog.start()
            connection.request('GET' if body is None else 'POST', (target.path or '/') + ('?' + target.query if target.query else ''),
                               body=body, headers={'Accept': 'application/json', **(headers or {})})
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderError()
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE or time.monotonic() - started >= TIMEOUT:
                raise ProviderError()
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise ProviderError()
            return value
        except (OSError, ValueError, http.client.HTTPException, RecursionError):
            raise ProviderError() from None
        finally:
            if watchdog is not None:
                watchdog.cancel()
            connection.close()

    def metadata(self):
        # Network fetches run outside cache_lock so a slow provider never stalls cached sign-ins.
        with self.cache_lock:
            if self.metadata_cache is not None and self.metadata_cache[0] > time.monotonic():
                return self.metadata_cache[1]
        result = self.request(self.config.issuer.rstrip('/') + '/.well-known/openid-configuration')
        # PKCE metadata is optional in OIDC discovery. Always send S256; reject providers
        # that explicitly advertise an incompatible or malformed method list.
        methods = result.get('code_challenge_methods_supported', ['S256'])
        if result.get('issuer') != self.config.issuer or not isinstance(methods, list) or 'S256' not in methods \
                or not isinstance(result.get('response_types_supported'), list) or 'code' not in result['response_types_supported']:
            raise ProviderError()
        for name in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            self.endpoint(result.get(name))
        methods = result.get('token_endpoint_auth_methods_supported', ['client_secret_basic'])
        method = auth_method(result, self.config.client_secret) if isinstance(methods, list) else None
        if method is None or method not in methods:
            raise ProviderError()
        with self.cache_lock:
            self.metadata_cache = time.monotonic() + CACHE_SECONDS, result
        return result

    def keys(self, metadata, *, refresh=False):
        with self.cache_lock:
            now = time.monotonic()
            if self.keys_cache is not None and self.keys_cache[0] > now and (not refresh or now - self.keys_cache[2] < 30):
                return self.keys_cache[1]
        result = self.request(metadata['jwks_uri'])
        keys = result.get('keys')
        if not isinstance(keys, list) or not 1 <= len(keys) <= 50 or any(not isinstance(key, dict) for key in keys):
            raise ProviderError()
        result = {'keys': [key for key in keys if key.get('kty') in ('RSA', 'EC') and key.get('use', 'sig') == 'sig'
                          and isinstance(key.get('key_ops', ['verify']), list) and 'verify' in key.get('key_ops', ['verify'])
                          and key.get('alg', 'RS256') in ('RS256', 'ES256')]}
        if not result['keys']:
            raise ProviderError()
        with self.cache_lock:
            now = time.monotonic()
            self.keys_cache = now + CACHE_SECONDS, result, now
        return result

    def validate(self, result, nonce, metadata):
        """OIDC Core 3.1.3.7: signature and RS256/ES256 pin, iss, aud, azp, exp, iat (30 s leeway), nonce, at_hash."""
        from joserfc.errors import JoseError
        from joserfc.jwt import JWTClaimsRegistry
        token = result.get('id_token')
        if not isinstance(token, str) or len(token) > 32768 or 'error' in result:
            raise ProviderError()
        registry = JWTClaimsRegistry(leeway=30, iss={'essential': True, 'value': self.config.issuer},
                                     aud={'essential': True, 'value': self.config.client_id}, sub={'essential': True},
                                     exp={'essential': True}, iat={'essential': True}, nonce={'essential': True, 'value': nonce})
        decoded = None
        for refresh in (False, True):
            # An unknown kid or bad signature refetches the JWKS once (rate-limited by keys()).
            try:
                decoded = self.jwt.decode(token, self.key_set.import_key_set(self.keys(metadata, refresh=refresh)), algorithms=['RS256', 'ES256'])
                break
            except (JoseError, ValueError, TypeError, KeyError, OverflowError):
                if refresh:
                    raise ProviderError() from None
        claims = decoded.claims
        try:
            if decoded.header.get('alg') not in ('RS256', 'ES256') or not isinstance(claims, dict):
                raise ProviderError()
            if any(type(claims.get(name)) not in (int, float) or not math.isfinite(claims[name]) for name in ('iat', 'exp')):
                raise ProviderError()
            registry.validate(claims)
        except (JoseError, ValueError, TypeError, OverflowError):
            raise ProviderError() from None
        # The registry also accepts a list containing the expected value; nonce and sub must be plain strings.
        if not isinstance(claims['nonce'], str) or not hmac.compare_digest(claims['nonce'].encode(), nonce.encode()) \
                or not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 255:
            raise ProviderError()
        # The registry accepts a list audience containing client_id; several audiences need azp = client_id.
        audience = claims['aud']
        if (isinstance(audience, list) and len(audience) != 1 or 'azp' in claims) and claims.get('azp') != self.config.client_id:
            raise ProviderError()
        if 'at_hash' in claims:
            access = result.get('access_token')
            expected = claims['at_hash']
            if not isinstance(access, str) or not isinstance(expected, str) or not access.isascii() \
                    or not expected.isascii() or not hmac.compare_digest(at_hash(access), expected):
                raise ProviderError()
        return claims
