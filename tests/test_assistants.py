"""Remote MCP at /_/mcp with OAuth 2.1, against the real ASGI application and SQLite."""
import asyncio
import base64
import hashlib
import json
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

import evlet.ui
from tests.test_business import client, hashed, http, secured  # noqa: F401

ORIGIN = 'http://127.0.0.1:8000'
CALLBACK = 'https://claude.ai/api/mcp/auth_callback'


def send(app, method, path, body=b'', headers=None, host=b'127.0.0.1:8000', address='127.0.0.1'):
    async def run():
        events = [{'type': 'http.request', 'body': body}]
        sent = []
        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}
        async def push(event):
            sent.append(event)
        route, _, query = path.partition('?')
        await app({'type': 'http', 'method': method, 'path': route, 'query_string': query.encode(), 'scheme': 'http',
                   'headers': [(b'host', host), *(headers or {}).items()], 'client': (address, 1234)}, receive, push)
        response = dict(sent[0]['headers'])
        content = sent[1]['body']
        data = json.loads(content) if content and response.get(b'content-type', b'').startswith(b'application/json') else content
        return sent[0]['status'], data, response
    return asyncio.run(run())


def form(app, path, values, **kwargs):
    return send(app, 'POST', path, urlencode(values).encode(), {b'content-type': b'application/x-www-form-urlencoded'}, **kwargs)


def register(app, uris=(CALLBACK,), **extra):
    return send(app, 'POST', '/_/oauth/register', json.dumps({'client_name': 'Claude', 'redirect_uris': list(uris), **extra}).encode(),
                {b'content-type': b'application/json'})


def pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()


def authorize_query(client_id, challenge, redirect=CALLBACK, **extra):
    return urlencode({'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect, 'code_challenge': challenge,
                      'code_challenge_method': 'S256', 'state': 'xyz', 'resource': ORIGIN + '/_/mcp', **extra})


def connect(app, role='editor', allow_changes=True):
    """Register, approve on the consent page as role, and exchange the code. Returns (client_id, tokens, person)."""
    status, registered, _ = register(app)
    assert status == 201, registered
    verifier, challenge = pkce()
    person = client(app, role)
    query = authorize_query(registered['client_id'], challenge)
    status, info, _ = person('GET', '/api/_oauth/authorize?' + query)
    assert status == 200 and info['client'] == 'Claude' and info['user'] == role, info
    status, approved, _ = person('POST', '/api/_oauth/authorize', {'query': query, 'approve': True, 'allow_changes': allow_changes})
    assert status == 200, approved
    back = urlsplit(approved['redirect'])
    params = {key: values[0] for key, values in parse_qs(back.query).items()}
    assert approved['redirect'].startswith(CALLBACK + '?') and params['state'] == 'xyz' and params['iss'] == ORIGIN
    status, tokens, headers = form(app, '/_/oauth/token', {'grant_type': 'authorization_code', 'code': params['code'], 'redirect_uri': CALLBACK,
                                                          'client_id': registered['client_id'], 'code_verifier': verifier})
    assert status == 200 and tokens['token_type'] == 'Bearer' and headers[b'cache-control'] == b'no-store', tokens
    return registered['client_id'], tokens, person


def rpc(app, token, method, params=None, id=1):
    message = {'jsonrpc': '2.0', 'method': method, **({'id': id} if id is not None else {}), **({'params': params} if params is not None else {})}
    return send(app, 'POST', '/_/mcp', json.dumps(message).encode(), {b'content-type': b'application/json', b'authorization': f'Bearer {token}'.encode(),
                                                                      b'accept': b'application/json, text/event-stream'})


def tool(app, token, name, arguments):
    status, data, _ = rpc(app, token, 'tools/call', {'name': name, 'arguments': arguments})
    assert status == 200, data
    return data['result']


def tools(app, token):
    return [entry['name'] for entry in rpc(app, token, 'tools/list')[1]['result']['tools']]


def settings(app, **values):
    admin = client(app)
    status, data, _ = admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'mcp.' + key, 'value': value} for key, value in values.items()]})
    assert status == 200, data


def test_discovery_metadata_and_the_bearer_challenge(secured):
    status, resource, _ = send(secured, 'GET', '/.well-known/oauth-protected-resource/_/mcp')
    assert status == 200 and resource['resource'] == ORIGIN + '/_/mcp' and resource['authorization_servers'] == [ORIGIN]
    status, server, _ = send(secured, 'GET', '/.well-known/oauth-authorization-server')
    assert server['issuer'] == ORIGIN and server['code_challenge_methods_supported'] == ['S256']
    assert server['token_endpoint'] == ORIGIN + '/_/oauth/token' and server['registration_endpoint'] == ORIGIN + '/_/oauth/register'
    status, _, headers = rpc(secured, 'evm_' + 'a' * 43, 'ping')
    assert status == 401 and b'resource_metadata="http://127.0.0.1:8000/.well-known/oauth-protected-resource/_/mcp"' in headers[b'www-authenticate']
    assert send(secured, 'GET', '/_/mcp')[0] == 405
    assert send(secured, 'GET', '/_/other')[0] == 404
    assert send(secured, 'GET', '/.well-known/oauth-authorization-server', headers={b'origin': b'https://evil.test'})[0] == 403


def test_full_flow_with_pkce_refresh_rotation_and_revocation(secured):
    client_id, tokens, person = connect(secured)
    status, init, _ = rpc(secured, tokens['access_token'], 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'Claude', 'version': '1'}})
    assert init['result']['protocolVersion'] == '2025-06-18' and init['result']['capabilities'] == {'tools': {}}
    assert rpc(secured, tokens['access_token'], 'notifications/initialized', id=None)[0] == 202
    assert tool(secured, tokens['access_token'], 'discover_models', {})['structuredContent']['total'] >= 1
    # Refresh tokens rotate; the old pair stops working.
    status, fresh, _ = form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': client_id})
    assert status == 200 and fresh['refresh_token'] != tokens['refresh_token']
    assert rpc(secured, fresh['access_token'], 'ping')[0] == 200
    # Another client can't use this client's refresh token.
    _, other, _ = register(secured)
    assert form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': fresh['refresh_token'], 'client_id': other['client_id']})[1]['error'] == 'invalid_grant'
    # Replaying a rotated refresh token signs the connection out.
    status, replay, _ = form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': client_id})
    assert status == 400 and replay['error'] == 'invalid_grant'
    assert rpc(secured, fresh['access_token'], 'ping')[0] == 401
    assert form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': fresh['refresh_token'], 'client_id': client_id})[0] == 400
    # Revocation: either token ends the connection, and replay after it fails.
    client_id, tokens, person = connect(secured)
    assert form(secured, '/_/oauth/revoke', {'token': tokens['refresh_token'], 'client_id': client_id})[0] == 200
    assert rpc(secured, tokens['access_token'], 'ping')[0] == 401
    assert form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': client_id})[1]['error'] == 'invalid_grant'
    with secured.database._connection() as connection:
        stored = json.dumps([list(row) for table in ('evlet_mcp_tokens', 'evlet_oauth_codes', 'evlet_audit') for row in connection.execute(f'SELECT * FROM {table}')])
        areas = [row[0] for row in connection.execute("SELECT detail FROM evlet_audit WHERE area='MCP'")]
    assert tokens['access_token'] not in stored and tokens['refresh_token'] not in stored and tokens['access_token'][4:] not in stored
    assert 'Client connected, can make changes' in areas and 'Signed out by the client' in areas


def test_codes_are_single_use_bound_to_client_redirect_and_verifier(secured):
    _, registered, _ = register(secured)
    verifier, challenge = pkce()
    person = client(secured, 'editor')
    query = authorize_query(registered['client_id'], challenge)
    assert http(secured, 'POST', '/api/_oauth/authorize', {'query': query, 'approve': True}, cookie=person.cookie)[0] == 403  # no CSRF token
    code = parse_qs(urlsplit(person('POST', '/api/_oauth/authorize', {'query': query, 'approve': True})[1]['redirect']).query)['code'][0]
    exchange = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': CALLBACK, 'client_id': registered['client_id'], 'code_verifier': verifier}
    assert form(secured, '/_/oauth/token', {**exchange, 'code_verifier': pkce()[0]})[1]['error'] == 'invalid_grant'
    assert form(secured, '/_/oauth/token', {**exchange, 'redirect_uri': CALLBACK + '/x'})[1]['error'] == 'invalid_grant'
    _, other, _ = register(secured)
    assert form(secured, '/_/oauth/token', {**exchange, 'client_id': other['client_id']})[1]['error'] == 'invalid_grant'
    assert form(secured, '/_/oauth/token', {**exchange, 'client_id': 'mcp_unknown'})[0] == 401
    status, tokens, _ = form(secured, '/_/oauth/token', exchange)
    assert status == 200
    assert form(secured, '/_/oauth/token', exchange)[1]['error'] == 'invalid_grant'
    assert rpc(secured, tokens['access_token'], 'ping')[0] == 401  # the replayed code signed its connection out
    denied = person('POST', '/api/_oauth/authorize', {'query': query, 'approve': False})[1]['redirect']
    assert parse_qs(urlsplit(denied).query) == {'error': ['access_denied'], 'state': ['xyz'], 'iss': [ORIGIN]}


@pytest.mark.parametrize('uri', ['http://evil.example/cb', 'javascript:alert(1)', 'https://claude.ai/cb#frag', 'https://user:pw@claude.ai/cb',
                                 '/relative', 'myapp:/cb', 'https://claude.ai/c b'])
def test_registration_rejects_unsafe_redirect_uris(secured, uri):
    status, data, _ = register(secured, (uri,))
    assert status == 400 and data['error'] == 'invalid_redirect_uri'


def test_authorize_never_redirects_to_unregistered_addresses(secured):
    _, registered, _ = register(secured, (CALLBACK, 'http://127.0.0.1:33418/callback', 'com.example.app:/cb'))
    _, challenge = pkce()
    person = client(secured, 'editor')
    for redirect in (CALLBACK + 'x', CALLBACK + '/../evil', 'https://evil.example/api/mcp/auth_callback', 'http://127.0.0.1:33419/callback'):
        status, data, _ = person('GET', '/api/_oauth/authorize?' + authorize_query(registered['client_id'], challenge, redirect))
        assert status == 400 and 'address' in data['error']
    assert person('GET', '/api/_oauth/authorize?' + authorize_query('mcp_unknown', challenge))[0] == 400
    for extra in ({'code_challenge_method': 'plain'}, {'code_challenge': 'short'}, {'response_type': 'token'}, {'resource': 'https://other.test/_/mcp'}):
        assert person('GET', '/api/_oauth/authorize?' + authorize_query(registered['client_id'], challenge, **extra))[0] == 400
    assert person('GET', '/api/_oauth/authorize?' + authorize_query(registered['client_id'], challenge, 'http://127.0.0.1:33418/callback'))[0] == 200
    assert http(secured, 'GET', '/api/_oauth/authorize?' + authorize_query(registered['client_id'], challenge))[0] == 401


def test_tools_respect_permissions_writes_hidden_models_disabled_tools_and_rate(secured):
    _, tokens, _ = connect(secured, 'viewer')
    _, editor, _ = connect(secured, 'editor')
    _, refused, _ = connect(secured, 'editor', allow_changes=False)
    access, write = tokens['access_token'], editor['access_token']
    # Writes stay off while mcp.allow_writes is off, even for a client that may make changes.
    assert 'create_record' not in tools(secured, write)
    result = tool(secured, write, 'create_record', {'model': 'customer', 'values': {'name': 'Blocked'}})
    assert result['isError'] and 'turned off changes' in result['structuredContent']['error']
    settings(secured, allow_writes=True)
    assert 'create_record' in tools(secured, write) and 'create_record' not in tools(secured, refused['access_token'])
    assert tool(secured, write, 'create_record', {'model': 'customer', 'values': {'name': 'From Claude'}})['structuredContent']['name'] == 'From Claude'
    assert tool(secured, refused['access_token'], 'create_record', {'model': 'customer', 'values': {'name': 'No'}})['structuredContent']['status'] == 403
    # A viewer's assistant has the viewer's access, even with writes on.
    assert 'create_record' not in tools(secured, access)
    with secured.database._connection() as connection:
        viewer_client = connection.execute("SELECT m.id FROM evlet_mcp_clients m JOIN evlet_users u ON u.id=m.user_id WHERE u.username='viewer'").fetchone()[0]
        connection.execute('UPDATE evlet_mcp_clients SET allow_changes=1 WHERE id=?', (viewer_client,))
        connection.commit()
    assert tool(secured, access, 'create_record', {'model': 'customer', 'values': {'name': 'No'}})['structuredContent']['status'] == 403
    # Hidden models never appear, even to an admin.
    _, admin, _ = connect(secured, 'admin')
    settings(secured, hidden_models=['Customer'])
    names = [item['name'] for item in tool(secured, admin['access_token'], 'discover_models', {})['structuredContent']['models']]
    assert 'customer' not in names and 'project' in names
    assert tool(secured, admin['access_token'], 'list_records', {'model': 'customer'})['isError']
    assert client(secured)('GET', '/api/customer')[0] == 200  # the browser still sees it
    # Disabled tools and the per-client rate.
    settings(secured, disabled_tools=['list_records'], rate=3)
    _, admin, _ = connect(secured, 'admin')
    assert 'list_records' not in tools(secured, admin['access_token'])
    assert tool(secured, admin['access_token'], 'list_records', {'model': 'project'})['structuredContent']['status'] == 403
    for _ in range(3):
        assert tool(secured, admin['access_token'], 'get_record', {'model': 'project', 'id': 999999})['structuredContent']['status'] == 404
    assert tool(secured, admin['access_token'], 'discover_models', {})['structuredContent']['status'] == 429
    assert not tool(secured, write, 'discover_models', {})['isError']  # the rate is per client
    # Turning MCP off stops every client and hides the metadata.
    settings(secured, enabled=False)
    status, data, _ = rpc(secured, write, 'ping')
    assert status == 403 and data['error'] == 'AI access is turned off for this workspace.'
    assert send(secured, 'GET', '/.well-known/oauth-authorization-server')[0] == 404


def test_apps_only_narrow_and_tokens_never_reach_other_routes(secured):
    controls = secured.business.controls
    controls.apps = [{'id': 'crm', 'version': '', 'models': ['Customer']}, {'id': 'projects', 'version': '', 'models': ['Project']}]
    controls.app_of = {'Customer': 'crm', 'Project': 'projects'}
    _, tokens, person = connect(secured, 'editor')
    page = person('GET', '/api/_settings/me/ai')[1]
    assert page['apps'] == ['crm', 'projects'] and page['chosen'] is None
    assert person('PATCH', '/api/_settings/me/ai', {'apps': ['crm', 'billing']})[0] == 422
    assert person('PATCH', '/api/_settings/me/ai', {'apps': ['crm']})[1]['chosen'] == ['crm']
    names = [item['name'] for item in tool(secured, tokens['access_token'], 'discover_models', {})['structuredContent']['models']]
    assert 'customer' in names and 'project' not in names
    bearer = {b'authorization': f"Bearer {tokens['access_token']}".encode()}
    for path in ('/api/customer', '/api/_settings/me/ai', '/api/_controls/state', '/api/auth/session'):
        status, data, _ = http(secured, 'GET', path, headers=bearer)
        assert status in (401, 403) or data.get('user') is None


def test_sub_host_gets_404_everywhere(secured):
    other = b'portal.localhost:8000'
    for method, path in (('GET', '/.well-known/oauth-authorization-server'), ('GET', '/.well-known/oauth-protected-resource'),
                         ('POST', '/_/oauth/register'), ('POST', '/_/mcp'), ('GET', '/_/oauth/authorize')):
        assert send(secured, method, path, b'{}', {b'content-type': b'application/json'}, host=other)[0] == 404


def test_registration_and_token_endpoints_are_rate_limited_per_network(secured):
    for _ in range(20):
        assert register(secured)[0] == 201
    assert register(secured)[0] == 429
    assert send(secured, 'POST', '/_/oauth/register', json.dumps({'redirect_uris': [CALLBACK]}).encode(), {b'content-type': b'application/json'}, address='10.0.0.9')[0] == 201
    for _ in range(60):
        form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': 'x', 'client_id': 'mcp_none'})
    assert form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': 'x', 'client_id': 'mcp_none'})[0] == 429
    # Behind a trusted proxy, the address it reports is the one counted.
    secured.business.auth.client_address = lambda scope: '10.1.1.1'
    assert form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': 'x', 'client_id': 'mcp_none'})[0] == 401


def test_confidential_clients_need_their_secret(secured):
    status, registered, _ = register(secured, token_endpoint_auth_method='client_secret_basic')
    assert status == 201 and registered['client_secret'].startswith('evs_')
    basic = base64.b64encode(f"{registered['client_id']}:wrong".encode())
    status, data, headers = send(secured, 'POST', '/_/oauth/token', b'grant_type=refresh_token&refresh_token=x',
                                 {b'content-type': b'application/x-www-form-urlencoded', b'authorization': b'Basic ' + basic})
    assert status == 401 and data['error'] == 'invalid_client' and headers[b'www-authenticate'] == b'Basic'
    basic = base64.b64encode(f"{registered['client_id']}:{registered['client_secret']}".encode())
    status, data, _ = send(secured, 'POST', '/_/oauth/token', b'grant_type=refresh_token&refresh_token=x',
                           {b'content-type': b'application/x-www-form-urlencoded', b'authorization': b'Basic ' + basic})
    assert status == 400 and data['error'] == 'invalid_grant'
    with secured.database._connection() as connection:
        assert registered['client_secret'] not in json.dumps([list(row) for row in connection.execute('SELECT * FROM evlet_oauth_clients')])


def test_controls_shows_totals_and_clients_and_signs_one_out(secured):
    settings(secured, allow_writes=True)
    _, tokens, person = connect(secured, 'editor')
    _, viewer, _ = connect(secured, 'viewer', allow_changes=False)
    tool(secured, tokens['access_token'], 'discover_models', {})
    tool(secured, tokens['access_token'], 'create_record', {'model': 'customer', 'values': {'name': 'Made'}})
    tool(secured, tokens['access_token'], 'update_record', {'model': 'customer', 'id': 999999, 'values': {'name': 'Gone'}})
    tool(secured, viewer['access_token'], 'list_records', {'model': 'customer'})
    with secured.database._connection() as connection:
        viewer_client = connection.execute("SELECT m.id FROM evlet_mcp_clients m JOIN evlet_users u ON u.id=m.user_id WHERE u.username='viewer'").fetchone()[0]
        connection.execute('UPDATE evlet_mcp_clients SET allow_changes=1 WHERE id=?', (viewer_client,))
        connection.commit()
    tool(secured, viewer['access_token'], 'create_record', {'model': 'project', 'values': {'name': 'Denied'}})
    admin = client(secured)
    status, overview, _ = admin('GET', '/api/_controls/mcp')
    assert status == 200 and overview['address'] == ORIGIN + '/_/mcp'
    assert overview['totals'] == {'reads': 2, 'writes': 2, 'denied': 1}
    assert sorted(row['username'] for row in overview['clients']) == ['editor', 'viewer']
    assert 'requests' not in overview and 'tool' not in json.dumps(overview)
    assert client(secured, 'editor')('GET', '/api/_controls/mcp')[0] == 404
    editor_client = next(row['id'] for row in overview['clients'] if row['username'] == 'editor')
    assert len(admin('DELETE', f'/api/_controls/mcp/{editor_client}')[1]['clients']) == 1
    assert rpc(secured, tokens['access_token'], 'ping')[0] == 401
    users = {row['username']: row for row in admin('GET', '/api/_controls/users')[1]['items']}
    assert users['viewer']['assistants'] == 1 and users['editor']['assistants'] == 0
    admin('PATCH', f"/api/_controls/users/{users['viewer']['id']}", {'sessions': 'end'})
    assert rpc(secured, viewer['access_token'], 'ping')[0] == 401
    with secured.database._connection() as connection:
        audit = [tuple(row) for row in connection.execute("SELECT target, detail FROM evlet_audit WHERE area='MCP'")]
    assert ('Claude · editor', 'Signed out') in audit and ('mcp.allow_writes', 'Changed') in audit


def test_personal_page_shows_only_my_assistants_and_requests(secured):
    _, tokens, person = connect(secured, 'editor')
    _, other, _ = connect(secured, 'viewer')
    tool(secured, tokens['access_token'], 'get_record', {'model': 'project', 'id': 999999})
    tool(secured, other['access_token'], 'discover_models', {})
    page = person('GET', '/api/_settings/me/ai')[1]
    assert page['address'] == ORIGIN + '/_/mcp' and page['enabled'] is True and page['writes'] is False
    assert [item['client_name'] for item in page['clients']] == ['Claude'] and page['clients'][0]['calls'] == 1
    assert [(item['tool'], item['model'], item['outcome']) for item in page['requests']] == [('get_record', 'project', 'failed')]
    mine = page['clients'][0]['id']
    assert person('PATCH', f'/api/_settings/me/ai/{mine}', {'allow_changes': False})[1]['clients'][0]['allow_changes'] is False
    theirs = client(secured, 'viewer')('GET', '/api/_settings/me/ai')[1]['clients'][0]['id']
    assert person('DELETE', f'/api/_settings/me/ai/{theirs}')[0] == 404
    assert person('DELETE', f'/api/_settings/me/ai/{mine}')[1]['clients'] == []
    assert rpc(secured, tokens['access_token'], 'ping')[0] == 401
    # A password change signs assistants out, like browser sessions.
    with secured.database._connection() as connection:
        viewer = connection.execute("SELECT id FROM evlet_users WHERE username='viewer'").fetchone()[0]
    assert rpc(secured, other['access_token'], 'ping')[0] == 200
    secured.business.auth.update_user(viewer, {'password': 'another long test password'})
    assert rpc(secured, other['access_token'], 'ping')[0] == 401


def test_settings_are_validated_and_stdio_follows_them(secured):
    admin = client(secured)
    for key, value in (('mcp.rate', 61), ('mcp.rate', 0), ('mcp.allow_writes', 'yes'), ('mcp.hidden_models', ['Nope']), ('mcp.disabled_tools', ['drop_table'])):
        assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': key, 'value': value}]})[0] == 422
    stdio = {b'x-evlet-client': b'mcp'}
    capabilities = admin('GET', '/api/_discovery', headers=stdio)[1]['capabilities']
    assert capabilities['assistant'] == {'writes': False, 'rate': 60, 'disabled_tools': []}
    assert 'assistant' not in admin('GET', '/api/_discovery')[1]['capabilities']
    assert admin('POST', '/api/customer', {'name': 'Local'}, headers=stdio)[0] == 403
    settings(secured, hidden_models=['Customer'], allow_writes=True)
    assert admin('GET', '/api/customer', headers=stdio)[0] in (403, 404)
    assert admin('POST', '/api/project', {'name': 'Local'}, headers=stdio)[0] == 201
    settings(secured, enabled=False)
    assert admin('GET', '/api/_discovery', headers=stdio)[0] == 403


@pytest.mark.skipif(not (Path(evlet.ui.__file__).with_name('static') / 'index.html').is_file(), reason='Build browser assets before checking static serving')
def test_consent_page_is_the_desk_shell_with_its_csp(secured):
    status, page, headers = send(secured, 'GET', '/_/oauth/authorize?client_id=x')
    assert status == 200 and b'<div id="evlet-app">' in page and b"frame-ancestors 'none'" in headers[b'content-security-policy']
