import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from evlet import ConnectorError, HTTPClient, MCPServer, TenantRegistry, TenantApplication, Policy, Rule
from evlet.connector import origin
from examples.playground.models import Customer, Project
from examples.playground.tenants import tenant_app
from tests.test_business import hashed, PASSWORD


def connector_app():
    def factory(tenant):
        app = tenant_app(tenant)
        app.business.policy = Policy(app.registry, rules={
            ('viewer', Customer): Rule(read_fields=('name',), scope=lambda user: {'active': True}),
            ('editor', Customer): Rule(operations=('read', 'update'), read_fields=('name',), write_fields=('name',), scope=lambda user: {'active': True}),
        })
        return app
    return TenantApplication(TenantRegistry(os.environ['EVLET_TENANT_ROOT']), factory)


@pytest.fixture
def live_connector(tmp_path, hashed):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    catalog = TenantRegistry.create(tmp_path / 'tenants', development=True, ports=(port,))
    apps = {}
    for slug in ('alpha', 'beta'):
        tenant = catalog.create_tenant(slug, slug.title(), slug + '.localhost')
        tenant.database_path.parent.mkdir(parents=True); tenant.database_path.touch()
        app = apps[slug] = tenant_app(tenant); app.initialize()
        with app.database.transaction() as tx:
            for role in ('admin', 'viewer', 'editor'):
                # Allow MCP (Controls, Users): the connector acts for these people.
                tx.connection.execute('INSERT INTO evlet_users(username,password,role,allow_mcp) VALUES (?,?,?,1)', (role, hashed, role))
            # The stdio tests change records, so this workspace lets AI assistants make changes.
            tx.connection.execute("INSERT INTO evlet_overrides(kind,key,value) VALUES ('setting','mcp.allow_writes','true')")
            tx.connection.execute('UPDATE evlet_config SET version=version+1')
        app.database.create(Project, name=slug)
        catalog.set_active(slug, True)
    env = {**os.environ, 'EVLET_TENANT_ROOT': str(catalog.root)}
    with (tmp_path / 'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'tests.test_connector:connector_app', '--factory', '--no-proxy-headers', '--host', '127.0.0.1', '--port', str(port)], env=env, stdout=log, stderr=log)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Disposable server exited')
                try:
                    HTTPClient(f'http://alpha.localhost:{port}').get('/api/auth/session')
                    break
                except ConnectorError:
                    time.sleep(.05)
            else:
                raise RuntimeError('Disposable server did not start')
            yield catalog, apps, port
        finally:
            process.terminate(); process.wait(timeout=10)


def initialize(server):
    result = server.message({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'tests', 'version': '1'}}})
    assert result['result']['capabilities'] == {'tools': {}}
    assert server.message({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def invoke(server, name, arguments):
    return server.message({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})


def signed(live_connector, slug='alpha', role='admin'):
    return HTTPClient(f'http://{slug}.localhost:{live_connector[2]}').login(role, PASSWORD)


def approve(live_connector, role='admin', slug='alpha'):
    """The person approves the changes an assistant asked for, from their own browser session."""
    person = signed(live_connector, slug, role)
    pending = [item for item in person.get('/api/_tools/approvals')['items'] if item['status'] == 'pending']
    assert pending
    for item in pending:
        person.request('POST', f"/api/_tools/approvals/{item['id']}", {'approve': True})


def approved(live_connector, server, name, arguments, role='admin'):
    """Call a tool that needs approval: the first call waits, the person approves, the same call runs."""
    waiting = invoke(server, name, arguments)['result']
    assert waiting['isError'] and 'approval' in waiting['structuredContent']['error'] and waiting['structuredContent']['fields']['approval']
    approve(live_connector, role)
    return invoke(server, name, arguments)


def test_live_tools_use_tenant_authentication_permissions_actions_and_validation(live_connector, tmp_path):
    client = signed(live_connector)
    server = MCPServer(client, allow_writes=True); initialize(server)
    assert invoke(server, 'get_record', {'model': 'project', 'id': 1})['result']['structuredContent']['name'] == 'alpha'
    created = invoke(server, 'create_record', {'model': 'project', 'values': {'name': 'Connector', 'children': {'tasks': [{'title': 'Complete', 'done': True}]}}})['result']['structuredContent']
    record_id = created['id']
    rejected = invoke(server, 'update_record', {'model': 'project', 'id': record_id, 'values': {'status': 'done'}})['result']
    assert rejected['isError'] and rejected['structuredContent']['status'] == 403
    invalid = invoke(server, 'create_record', {'model': 'project', 'values': {'name': ''}})['result']
    assert invalid['isError'] and 'name' in invalid['structuredContent']['fields']
    assert approved(live_connector, server, 'run_action', {'model': 'project', 'id': record_id, 'action': 'start'})['result']['structuredContent']['status'] == 'active'
    job = approved(live_connector, server, 'run_action', {'model': 'project', 'id': record_id, 'action': 'refresh_progress'})['result']['structuredContent']['job']
    assert invoke(server, 'job_status', {'id': job['id']})['result']['structuredContent']['state'] == 'queued'
    live_connector[1]['alpha'].business.jobs.run_one()
    assert invoke(server, 'job_status', {'id': job['id']})['result']['structuredContent']['state'] == 'succeeded'
    assert invoke(server, 'get_record', {'model': 'project', 'id': record_id})['result']['structuredContent']['progress'] == 100
    assert approved(live_connector, server, 'delete_record', {'model': 'project', 'id': record_id})['result']['structuredContent'] == {'deleted': True}
    assert signed(live_connector, 'beta').get('/api/project/1')['name'] == 'beta'
    assert live_connector[1]['beta'].database.count(Project) == 1
    file = tmp_path / 'session.json'; client.save(file)
    assert file.stat().st_mode & 0o777 == 0o600 and PASSWORD not in file.read_text()
    assert HTTPClient.load(file).get('/api/project/1')['name'] == 'alpha'
    with pytest.raises(FileExistsError): client.save(file)


def test_read_only_connector_and_viewer_cannot_dispatch_mutations(live_connector):
    reader = MCPServer(signed(live_connector)); initialize(reader)
    assert all(tool['annotations']['readOnlyHint'] for tool in reader.available())
    result = invoke(reader, 'create_record', {'model': 'customer', 'values': {'name': 'Blocked'}})['result']
    assert result['isError'] and result['structuredContent']['status'] == 403
    viewer = MCPServer(signed(live_connector, role='viewer'), allow_writes=True); initialize(viewer)
    # call_service is offered for the Playground's read-only service; each service keeps its own permission.
    assert all(tool['annotations']['readOnlyHint'] or tool['name'] == 'call_service' for tool in viewer.available())
    result = invoke(viewer, 'create_record', {'model': 'customer', 'values': {'name': 'Blocked'}})['result']
    assert result['isError'] and result['structuredContent']['status'] == 403
    for value in ('../auth/users', 'customer/1', '_jobs', 'project?limit=100', 'project%2F1'):
        assert invoke(viewer, 'get_record', {'model': value, 'id': 1})['result']['structuredContent']['status'] == 422
    assert invoke(viewer, 'get_record', {'model': 'project', 'id': True})['result']['isError']
    assert invoke(viewer, 'discover_models', {'url': 'https://other.test'})['result']['isError']


def test_sessions_cannot_replay_across_tenants_or_survive_revocation(live_connector):
    catalog, apps, port = live_connector
    alpha = signed(live_connector)
    beta = HTTPClient(f'http://beta.localhost:{port}', cookie=alpha.cookie, csrf=alpha.csrf)
    with pytest.raises(ConnectorError) as failure: beta.get('/api/_discovery')
    assert failure.value.status == 401
    apps['alpha'].business.auth.update_user(1, {'password': 'replacement long test password'})
    with pytest.raises(ConnectorError) as failure: alpha.get('/api/project/1')
    assert failure.value.status == 401
    beta = signed(live_connector, 'beta')
    catalog.set_active('beta', False)
    with pytest.raises(ConnectorError) as failure: beta.get('/api/_discovery')
    assert failure.value.status == 421


@pytest.mark.parametrize('url', ['http://example.test', 'http://127.0.0.1.evil.test', 'https://user:secret@example.test', 'https://example.test/api', 'https://example.test?q=x', 'file:///tmp/a', 'https://example.test\n', 'http://alpha..localhost'])
def test_origin_rejects_unsafe_or_ambiguous_destinations(url):
    with pytest.raises(ValueError): origin(url)


def test_private_session_files_reject_symlinks_permissions_and_oversized_content(tmp_path):
    client = HTTPClient('http://127.0.0.1:8000', cookie='evlet_session=' + 'a' * 43, csrf='b' * 64)
    path = tmp_path / 'session'; client.save(path)
    link = tmp_path / 'link'; link.symlink_to(path)
    with pytest.raises(OSError): HTTPClient.load(link)
    path.chmod(0o644)
    with pytest.raises(ValueError): HTTPClient.load(path)
    path.chmod(0o600); path.write_text('x' * 16385)
    with pytest.raises(ValueError): HTTPClient.load(path)


def test_protocol_lifecycle_errors_notifications_and_input_bounds():
    class Client:
        calls = 0
        def get(self, path):
            self.calls += 1
            return {'tools': [{'name': 'discover_models', 'description': 'List models', 'input_schema': {'type': 'object'}, 'kind': 'read'}]}
        def request(self, method, path, body):
            self.calls += 1
            if body['tool'] not in ('discover_models',):
                raise ConnectorError(404, 'Unknown tool')
            return {'result': {'models': []}}, None
    client = Client(); server = MCPServer(client)
    assert server.message({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['error']['code'] == -32000
    initialize(server)
    assert 'error' not in server.message({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    assert invoke(server, 'unknown', {})['error']['code'] == -32602
    before = client.calls
    assert server.message({'jsonrpc': '2.0', 'method': 'tools/call', 'params': {'name': 'discover_models'}}) is None
    assert client.calls == before
    output = io.BytesIO()
    server.serve(io.BytesIO(b'{"jsonrpc":"2.0","id":9,"id":10,"method":"ping"}\nnot-json\n[]\n'), output)
    errors = [json.loads(line)['error']['code'] for line in output.getvalue().splitlines()]
    assert errors == [-32700, -32700, -32600]
    output = io.BytesIO(); server.serve(io.BytesIO(b'x' * (128 * 1024 + 1)), output)
    assert json.loads(output.getvalue())['error']['code'] == -32700
    for _ in range(60): server.call('discover_models', {})
    with pytest.raises(ConnectorError) as failure: server.call('discover_models', {})
    assert failure.value.status == 429


def test_tools_enforce_row_and_field_rules_from_the_live_application(live_connector):
    admin = signed(live_connector)
    visible = admin.request('POST', '/api/customer', {'name': 'Visible', 'email': 'private@example.test', 'active': True})[0]
    hidden = admin.request('POST', '/api/customer', {'name': 'Hidden', 'email': 'hidden@example.test', 'active': False})[0]
    server = MCPServer(signed(live_connector, role='editor'), allow_writes=True); initialize(server)
    page = invoke(server, 'list_records', {'model': 'customer'})['result']['structuredContent']
    assert page['total'] == 1 and page['items'] == [{'id': visible['id'], 'name': 'Visible'}]
    assert invoke(server, 'get_record', {'model': 'customer', 'id': hidden['id']})['result']['structuredContent']['status'] == 404
    assert invoke(server, 'update_record', {'model': 'customer', 'id': hidden['id'], 'values': {'name': 'Leaked'}})['result']['structuredContent']['status'] == 404
    assert invoke(server, 'update_record', {'model': 'customer', 'id': visible['id'], 'values': {'email': 'changed@example.test'}})['result']['structuredContent']['status'] == 403
    assert invoke(server, 'list_records', {'model': 'customer', 'filters': {'email': 'private@example.test'}})['result']['structuredContent']['status'] == 403
    schema = invoke(server, 'describe_model', {'model': 'customer'})['result']['structuredContent']
    assert [field['name'] for field in schema['fields']] == ['name']
    assert invoke(server, 'update_record', {'model': 'customer', 'id': visible['id'], 'values': {'name': 'Updated'}})['result']['structuredContent']['name'] == 'Updated'


@pytest.fixture
def wire_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from evlet.connector import MAX_RESPONSE
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get('Cookie')))
            if self.path == '/api/redirect':
                self.send_response(302)
                self.send_header('Location', '/capture')
                self.end_headers()
                return
            bodies = {'/api/duplicate': b'{"value":1,"value":2}', '/api/nonfinite': b'{"value":NaN}',
                      '/api/array': b'[]', '/api/oversized': b' ' * (MAX_RESPONSE + 1), '/api/text': b'{}'}
            body = bodies.get(self.path, b'{}')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html' if self.path == '/api/text' else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append((self.path, self.headers.get('Cookie')))
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            # Simulate a lost reply after the application may have accepted a write.
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
    thread.start()
    try:
        yield HTTPClient(f'http://alpha.localhost:{server.server_port}', cookie='evlet_session=' + 'a' * 43, csrf='b' * 64), requests
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_connector_does_not_follow_redirects_or_forward_session(wire_server):
    client, requests = wire_server
    with pytest.raises(ConnectorError) as failure: client.get('/api/redirect')
    assert failure.value.status == 302
    assert [path for path, cookie in requests] == ['/api/redirect']


@pytest.mark.parametrize('path', ['duplicate', 'nonfinite', 'array', 'oversized', 'text'])
def test_connector_rejects_unusable_or_unbounded_responses(wire_server, path):
    with pytest.raises(ConnectorError) as failure: wire_server[0].get('/api/' + path)
    assert failure.value.status == 502


def test_connector_does_not_retry_uncertain_writes(wire_server):
    client, requests = wire_server
    with pytest.raises(ConnectorError) as failure: client.request('POST', '/api/customer', {'name': 'Once'})
    assert failure.value.status == 503
    assert 'never retried' in failure.value.message
    assert [path for path, cookie in requests] == ['/api/customer']
    with pytest.raises(ConnectorError) as failure: client.request('POST', '/api/customer', {'name': 'x' * 65536})
    assert failure.value.status == 413 and len(requests) == 1


def test_tool_calls_stay_in_their_tenant(live_connector):
    catalog, apps, port = live_connector
    alpha = signed(live_connector)
    found = alpha.request('POST', '/api/_tools/execute', {'tool': 'list_records', 'arguments': {'model': 'project'}})[0]['result']
    assert [item['name'] for item in found['items']] == ['alpha']
    # An alpha session is not a session on beta, for tools as for everything else.
    replay = HTTPClient(f'http://beta.localhost:{port}', cookie=alpha.cookie, csrf=alpha.csrf)
    with pytest.raises(ConnectorError) as failure: replay.request('POST', '/api/_tools/execute', {'tool': 'list_records', 'arguments': {'model': 'project'}})
    assert failure.value.status == 401
    beta = signed(live_connector, 'beta')
    assert [item['name'] for item in beta.request('POST', '/api/_tools/execute', {'tool': 'list_records', 'arguments': {'model': 'project'}})[0]['result']['items']] == ['beta']
    # Each tenant keeps its own tool log.
    with apps['beta'].database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM evlet_tool_calls WHERE origin='api'").fetchone()[0] == 1
    with apps['alpha'].database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM evlet_tool_calls WHERE origin='api'").fetchone()[0] == 1
