"""Real SQLite/ASGI and a loopback IdP with signed tokens: no external credentials or network."""

import base64
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection
import json
import secrets
from threading import Thread
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

pytest.importorskip('authlib', reason='Optional OIDC extra is not installed')
from joserfc.jwk import RSAKey, OctKey
from joserfc import jwt
from evlet import Application, Database, LocalAuth, Registry, Security, qb
from evlet.auth import OIDC
from evlet.oidc import ProviderError
from test_business import PASSWORD, hashed, http  # noqa: F401


@pytest.fixture
def provider():
    class Provider:
        claims = {}
        alg = 'RS256'
        wrong_key = False
        delay = 0
        metadata_changes = {}
        omit_pkce_metadata = False
        metadata_status = 200
        codes = {}
        calls = {}
        key = RSAKey.generate_key(2048, parameters={'kid': 'test'})
        alternate = RSAKey.generate_key(2048, parameters={'kid': 'test'})
    value = Provider()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def send(self, data, status=200, location=None):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            if location:
                self.send_header('Location', location)
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            path = urlsplit(self.path).path
            value.calls[path] = value.calls.get(path, 0) + 1
            if path == '/.well-known/openid-configuration':
                if value.delay:
                    time.sleep(value.delay)
                metadata = {'issuer': value.issuer, 'authorization_endpoint': value.issuer + '/authorize',
                           'token_endpoint': value.issuer + '/token', 'jwks_uri': value.issuer + '/keys',
                           'code_challenge_methods_supported': ['S256'], 'response_types_supported': ['code'],
                           'token_endpoint_auth_methods_supported': ['none', 'client_secret_basic'], **value.metadata_changes}
                if value.omit_pkce_metadata:
                    metadata.pop('code_challenge_methods_supported')
                self.send(metadata, value.metadata_status, value.issuer + '/unexpected-redirect')
            elif path == '/keys':
                self.send({'keys': [value.key.as_dict()]})
            elif path == '/authorize':
                query = {key: items[0] for key, items in parse_qs(urlsplit(self.path).query).items()}
                assert query['client_id'] == 'evlet-test' and query['response_type'] == 'code'
                assert query['scope'] == 'openid' and query['code_challenge_method'] == 'S256'
                code = secrets.token_urlsafe(16)
                value.codes[code] = query
                self.send({}, 302, query['redirect_uri'] + '?' + urlencode({'code': code, 'state': query['state'], 'iss': value.issuer}))
            else:
                self.send({}, 404)
        def do_POST(self):
            value.calls[self.path] = value.calls.get(self.path, 0) + 1
            query = {key: items[0] for key, items in parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode()).items()}
            pending = value.codes.pop(query.get('code'), None)
            if pending is None:
                self.send({'error': 'invalid_grant'}, 400)
                return
            challenge = base64.urlsafe_b64encode(hashlib.sha256(query.get('code_verifier', '').encode()).digest()).rstrip(b'=').decode()
            assert query['grant_type'] == 'authorization_code' and query['redirect_uri'] == pending['redirect_uri']
            assert challenge == pending['code_challenge']
            value.last_token_request = query
            value.last_authorization = self.headers.get('Authorization')
            claims = {'iss': value.issuer, 'sub': 'staff-opaque-id', 'aud': 'evlet-test', 'exp': int(time.time()) + 120,
                      'iat': int(time.time()), 'nonce': pending['nonce'], **value.claims}
            key = OctKey.import_key(b'attacker-secret-at-least-32-bytes-long') if value.alg == 'HS256' else value.alternate if value.wrong_key else value.key
            token = jwt.encode({'alg': value.alg, 'kid': 'test'}, claims, key)
            self.send({'id_token': token, 'access_token': 'provider-access-token', 'token_type': 'Bearer'})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    value.issuer = f'http://127.0.0.1:{server.server_port}'
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield value
    server.shutdown()
    server.server_close()
    thread.join(3)


@pytest.fixture
def configured(tmp_path, provider, hashed):
    database = Database(tmp_path / 'oidc.sqlite3', Registry())
    config = OIDC(provider.issuer, 'evlet-test', 'http://127.0.0.1:8000/_/oidc/callback', {'staff-opaque-id': 'editor', 'admin-opaque-id': 'admin'})
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, oidc=config)
    app = Application(database, auth=auth, secret_key=tmp_path / 'secret.key')
    database.initialize()
    app.business.initialize()
    with database.transaction() as tx:
        tx.connection.run(qb.insert('evlet_users', username='editor', password=hashed, role='editor'))
        tx.connection.run(qb.update('evlet_users', password=hashed).where(username='admin'))
    yield app
    database.close()


def begin(app):
    status, _, headers = http(app, 'GET', '/_/oidc/start')
    assert status == 303, (status, _)
    cookie = headers[b'set-cookie'].decode().split(';')[0]
    return headers[b'location'].decode(), cookie


def authorize(url):
    parsed = urlsplit(url)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    try:
        connection.request('GET', parsed.path + '?' + parsed.query)
        response = connection.getresponse()
        assert response.status == 302
        result = urlsplit(response.getheader('Location'))
        response.read()
        return result.path + '?' + result.query
    finally:
        connection.close()


def callback(app):
    url, cookie = begin(app)
    path = authorize(url)
    result = http(app, 'GET', path, cookie=cookie, headers={b'sec-fetch-site': b'cross-site'})
    return result, path, cookie


def finish(app, cookie, **kwargs):
    return http(app, 'POST', '/_/oidc/finish', {}, cookie=cookie,
                headers=kwargs.pop('headers', {b'origin': b'http://127.0.0.1:8000'}), **kwargs)


def test_company_signin_uses_pkce_and_existing_account_and_local_password(configured, provider):
    app = configured
    session = http(app, 'GET', '/api/auth/session')[1]
    assert session['oidc'] == {'label': 'Company sign-in', 'start': '/_/oidc/start'}
    result, path, pending = callback(app)
    assert result[0] == 303 and result[2][b'location'] == b'/login?oidc=finish'
    assert result[2][b'referrer-policy'] == b'no-referrer'
    receipt = result[2][b'set-cookie'].decode().split(';')[0]
    status, answer, headers = finish(app, receipt)
    assert status == 200 and answer['user']['username'] == 'editor' and answer['user']['role'] == 'editor'
    session_cookie = headers[b'set-cookie'].decode().split(';')[0]
    assert http(app, 'GET', '/api/auth/session', cookie=session_cookie)[1]['user']['username'] == 'editor'
    assert finish(app, receipt)[0] == 401
    assert http(app, 'GET', path, cookie=pending)[2][b'location'] == b'/login?oidc=error'
    assert provider.calls['/token'] == 1
    assert provider.last_token_request['client_id'] == 'evlet-test'
    assert http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[0] == 200
    with app.database._connection() as connection:
        assert connection.run(qb.select('evlet_users', qb.count())).value() == 2


@pytest.mark.parametrize('claims', [
    {'iss': 'https://attacker.invalid'}, {'aud': 'other-client'}, {'nonce': 'wrong'}, {'exp': 1},
    {'iat': int(time.time()) + 600}, {'sub': ''}, {'sub': 'unknown', 'email': 'editor', 'role': 'admin'},
    {'aud': ['evlet-test', 'other']}, {'azp': 'other-client'},
])
def test_untrusted_claims_never_issue_session(configured, provider, claims):
    provider.claims = claims
    result, path, cookie = callback(configured)
    assert result[0] == 303 and result[2][b'location'] == b'/login?oidc=error'
    assert http(configured, 'GET', path, cookie=cookie)[2][b'location'] == b'/login?oidc=error'
    with configured.database._connection() as connection:
        assert connection.run(qb.select('evlet_sessions', qb.count())).value() == 0
        assert connection.run(qb.select('evlet_users', qb.count())).value() == 2


@pytest.mark.parametrize('kind', ['algorithm', 'signature'])
def test_wrong_signature_or_algorithm_fails(configured, provider, kind):
    if kind == 'algorithm':
        provider.alg = 'HS256'
    else:
        provider.wrong_key = True
    assert callback(configured)[0][2][b'location'] == b'/login?oidc=error'


def test_state_browser_binding_and_origin_protection(configured, provider):
    url, browser = begin(configured)
    path = authorize(url)
    other = begin(configured)[1]
    assert http(configured, 'GET', path, cookie=other)[2][b'location'] == b'/login?oidc=error'
    assert http(configured, 'GET', path.replace('state=', 'state=wrong'), cookie=browser)[2][b'location'] == b'/login?oidc=error'
    assert provider.calls.get('/token', 0) == 0
    assert http(configured, 'GET', '/_/oidc/start', headers={b'sec-fetch-site': b'cross-site'})[0] == 403
    response = http(configured, 'GET', path, cookie=browser)
    receipt = response[2][b'set-cookie'].decode().split(';')[0]
    assert finish(configured, receipt, headers={})[0] == 403
    assert finish(configured, receipt, headers={b'origin': b'https://attacker.invalid'})[0] == 403
    assert finish(configured, receipt, headers={b'origin': b'http://127.0.0.1:8000', b'x-evlet-request': b''})[0] == 403
    assert finish(configured, receipt)[0] == 200


@pytest.mark.parametrize('change', ['revoke', 'deactivate', 'role'])
def test_finish_checks_current_generation_active_and_realm(configured, change):
    response = callback(configured)[0]
    receipt = response[2][b'set-cookie'].decode().split(';')[0]
    auth = configured.business.auth
    with configured.database.transaction() as tx:
        owner = tx.connection.run(qb.select('evlet_users', 'id').where(username='editor')).value()
        if change == 'revoke':
            auth.end_sessions(tx.connection, owner)
        elif change == 'deactivate':
            tx.connection.run(qb.update('evlet_users', active=0).where(id=owner))
        else:
            tx.connection.run(qb.update('evlet_users', role='portal').where(id=owner))
    assert finish(configured, receipt)[0] == 401
    with configured.database._connection() as connection:
        assert connection.run(qb.select('evlet_sessions', qb.count())).value() == 0


def test_provider_mfa_claims_never_bypass_local_mfa_and_code_replay(configured, provider):
    from evlet.settings import totp
    provider.claims = {'sub': 'admin-opaque-id', 'amr': ['mfa', 'otp'], 'acr': 'strong'}
    result = callback(configured)[0]
    receipt = result[2][b'set-cookie'].decode().split(';')[0]
    status, pending, _ = finish(configured, receipt)
    assert status == 200 and pending['two_step'] == 'setup' and 'user' not in pending
    assert finish(configured, receipt)[0] == 401
    secret = base64.b32decode(pending['secret'] + '=' * (-len(pending['secret']) % 8))
    code = totp(secret, int(time.time() // 30))
    status, answer, headers = http(configured, 'POST', '/api/auth/login', {'ticket': pending['ticket'], 'code': code})
    assert status == 200 and answer['user']['role'] == 'admin'
    assert http(configured, 'POST', '/api/auth/login', {'ticket': pending['ticket'], 'code': code})[0] == 401
    with configured.database._connection() as connection:
        assert connection.run(qb.select('evlet_sessions', 'two_step')).value() is not None


def test_confidential_client_uses_library_basic_auth_and_cached_keys(configured, provider):
    from evlet.oidc import OIDCFlow
    configured.business.auth.oidc = replace(configured.business.auth.oidc, client_secret='client secret:with punctuation')
    configured.business.oidc = OIDCFlow(configured.business)
    for _ in range(2):
        assert callback(configured)[0][2][b'location'] == b'/login?oidc=finish'
    assert provider.last_authorization.startswith('Basic ')
    assert 'client_secret' not in provider.last_token_request
    assert provider.calls['/.well-known/openid-configuration'] == 1
    assert provider.calls['/keys'] == 1


@pytest.mark.parametrize('changes', [
    {'issuer': 'https://other.invalid'}, {'code_challenge_methods_supported': None},
    {'token_endpoint': 'http://169.254.169.254/token'}, {'jwks_uri': 'https://[broken'},
    {'token_endpoint_auth_methods_supported': None},
    {'token_endpoint': 'https://:443'},
])
def test_invalid_discovery_fails_closed_without_leaking_provider_details(configured, provider, changes):
    provider.metadata_changes = changes
    status, answer, _ = http(configured, 'GET', '/_/oidc/start')
    assert status == 503 and 'temporarily unavailable' in answer['error']
    assert provider.issuer not in json.dumps(answer)


def test_http_deadline_is_bounded(configured, provider, monkeypatch):
    import evlet.oidc as module
    monkeypatch.setattr(module, 'TIMEOUT', 0.1)
    provider.delay = 0.4
    before = time.monotonic()
    with pytest.raises(ProviderError):
        configured.business.oidc.metadata()
    assert time.monotonic() - before < 0.35


@pytest.mark.parametrize('values', [
    {'issuer': 'http://company.example'}, {'redirect_uri': 'https://desk.example/wrong'},
    {'redirect_uri': 'https://desk.example/_/oidc/callback?next=https://other.example'},
    {'issuer': 'https://name:secret@company.example'}, {'issuer': 'https://company.example:invalid'},
    {'subjects': {'subject': 'Email@Example.com'}}, {'subjects': {}}, {'client_id': 'bad\nclient'},
    {'issuer': 'https://:443'},
])
def test_invalid_operator_configuration_is_rejected(values):
    default = dict(issuer='https://company.example', client_id='evlet', redirect_uri='https://desk.example/_/oidc/callback', subjects={'subject': 'editor'})
    with pytest.raises(ValueError):
        OIDC(**{**default, **values})


def test_config_is_immutable_secret_safe_and_uses_allowlisted_origin(tmp_path):
    subjects = {'person': 'editor'}
    config = OIDC('https://company.example', 'evlet', 'https://desk.example/_/oidc/callback', subjects, client_secret='private-client-secret')
    subjects['person'] = 'admin'
    assert config.subjects['person'] == 'editor' and 'private-client-secret' not in repr(config)
    with pytest.raises(TypeError):
        config.subjects['person'] = 'admin'
    database = Database(tmp_path / 'config.sqlite3', Registry())
    with pytest.raises(ValueError, match='authenticated browser origin'):
        LocalAuth(database, origins=('https://another.example',), oidc=config)
    with pytest.raises(ValueError, match='authenticated browser origin'):
        LocalAuth(database, origins=('https://desk.example',), realms={'https://desk.example': 'public'}, oidc=config)
    database.close()


def test_expired_and_configuration_changed_browser_tickets_cannot_finish(configured):
    from evlet.oidc import OIDCFlow
    response = callback(configured)[0]
    cookie = response[2][b'set-cookie'].decode().split(';')[0]
    with configured.database.transaction(store='sessions') as tx:
        tx.connection.run(qb.update('evlet_oidc_tickets', expires=0).where(kind='finish'))
    assert finish(configured, cookie)[0] == 401
    response = callback(configured)[0]
    cookie = response[2][b'set-cookie'].decode().split(';')[0]
    configured.business.auth.oidc = replace(configured.business.auth.oidc, subjects={'staff-opaque-id': 'admin'})
    configured.business.oidc = OIDCFlow(configured.business)
    assert finish(configured, cookie)[0] == 401


def test_instance_reads_config_and_propagates_into_runtime(tmp_path, provider):
    from evlet.instances import read_instance, build_instance
    path = tmp_path / 'instances' / 'acme'
    path.mkdir(parents=True)
    text = ('from evlet import Instance\nfrom evlet.auth import OIDC\ninstance=Instance(name="acme")\n'
            f'oidc=OIDC({provider.issuer!r}, "evlet-test", "http://127.0.0.1:8000/_/oidc/callback", {{"staff-opaque-id":"editor"}})\n')
    (path / 'config.py').write_text(text)
    (path / 'apps.lock').write_text('{"apps": {}}')
    config = read_instance(path)
    app, schedules = build_instance(config, path, [])
    assert app.business.auth.oidc is config.oidc and app.business.oidc.config is config.oidc and schedules == ()
    app.database.close()
    (path / 'config.py').write_text('from evlet import Instance\ninstance=Instance(name="acme")\noidc="bad"\n')
    with pytest.raises(ValueError, match='OIDC'):
        read_instance(path)


def test_callback_codes_are_absent_from_real_uvicorn_access_logs(configured, provider, caplog):
    import logging
    import socket
    import uvicorn
    url, browser = begin(configured)
    callback_path = authorize(url)
    sentinels = [items[0] for name, items in parse_qs(urlsplit(callback_path).query).items() if name in ('code', 'state')]
    caplog.set_level(logging.INFO, logger='uvicorn.access')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(10)
    server = uvicorn.Server(uvicorn.Config(configured, log_config=None, access_log=True, lifespan='off'))
    worker = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        connection = HTTPConnection('127.0.0.1', sock.getsockname()[1], timeout=5)
        try:
            connection.request('GET', callback_path, headers={'Host': '127.0.0.1:8000', 'Cookie': browser})
            response = connection.getresponse()
            assert response.status == 303 and response.getheader('Location') == '/login?oidc=finish'
            response.read()
        finally:
            connection.close()
    finally:
        server.should_exit = True
        worker.join(5)
        sock.close()
    assert not worker.is_alive()
    access = '\n'.join(record.getMessage() for record in caplog.records if record.name == 'uvicorn.access')
    assert '/_/oidc/callback' in access
    assert all(secret not in caplog.text for secret in sentinels)


def test_optional_pkce_metadata_omission_still_uses_and_verifies_s256(configured, provider):
    provider.omit_pkce_metadata = True
    result = callback(configured)[0]
    assert result[2][b'location'] == b'/login?oidc=finish'
    assert len(provider.last_token_request['code_verifier']) == 43


def test_es256_provider_is_supported_without_symmetric_algorithm_fallback(configured, provider):
    from joserfc.jwk import ECKey
    provider.alg = 'ES256'
    provider.key = ECKey.generate_key('P-256', parameters={'kid': 'test'})
    assert callback(configured)[0][2][b'location'] == b'/login?oidc=finish'


def test_callback_and_finish_are_one_use_under_concurrent_requests(configured, provider):
    from concurrent.futures import ThreadPoolExecutor
    url, browser = begin(configured)
    path = authorize(url)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: http(configured, 'GET', path, cookie=browser), range(2)))
    assert sorted(row[2][b'location'] for row in responses) == [b'/login?oidc=error', b'/login?oidc=finish']
    assert provider.calls['/token'] == 1
    completed = next(row for row in responses if row[2][b'location'] == b'/login?oidc=finish')
    receipt = completed[2][b'set-cookie'].decode().split(';')[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finish(configured, receipt), range(2)))
    assert sorted(row[0] for row in results) == [200, 401]
    with configured.database._connection() as connection:
        assert connection.run(qb.select('evlet_sessions', qb.count())).value() == 1


@pytest.mark.parametrize('claims', [
    {'nonce': None}, {'exp': True}, {'exp': 'tomorrow'}, {'exp': 10**1000}, {'iat': False},
    {'aud': ['evlet-test', 'other'], 'azp': 'evil'},
])
def test_malformed_signed_claims_fail_without_session(configured, provider, claims):
    provider.claims = claims
    result = callback(configured)[0]
    assert result[0] == 303 and result[2][b'location'] == b'/login?oidc=error'
    with configured.database._connection() as connection:
        assert connection.run(qb.select('evlet_sessions', qb.count())).value() == 0


def test_multiple_audiences_require_matching_authorized_party(configured, provider):
    provider.claims = {'aud': ['evlet-test', 'resource-api'], 'azp': 'evlet-test'}
    result = callback(configured)[0]
    assert result[2][b'location'] == b'/login?oidc=finish'
    assert finish(configured, result[2][b'set-cookie'].decode().split(';')[0])[0] == 200


def test_provider_http_redirect_is_not_followed(configured, provider):
    provider.metadata_status = 302
    assert http(configured, 'GET', '/_/oidc/start')[0] == 503
    assert provider.calls.get('/unexpected-redirect', 0) == 0


def test_browser_fixture_matches_real_oidc_session_login_and_mfa(configured, provider):
    from pathlib import Path
    session = http(configured, 'GET', '/api/auth/session')[1]
    result = callback(configured)[0]
    cookie = result[2][b'set-cookie'].decode().split(';')[0]
    login = finish(configured, cookie)[1]
    login['csrf'] = 'fixture-csrf'
    provider.claims = {'sub': 'admin-opaque-id'}
    result = callback(configured)[0]
    cookie = result[2][b'set-cookie'].decode().split(';')[0]
    challenge = finish(configured, cookie)[1]
    uri = challenge['uri'].replace(challenge['secret'], 'fixture-secret')
    challenge.update(ticket='fixture-ticket', secret='fixture-secret', uri=uri)
    path = Path(__file__).resolve().parents[1] / 'frontend/src/evlet/tests/oidc-fixture.json'
    assert json.loads(path.read_text()) == {'session': session, 'finish': login, 'challenge': challenge}
