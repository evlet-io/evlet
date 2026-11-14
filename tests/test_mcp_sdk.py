"""Compatibility with the official client through a real stdio subprocess."""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys

import pytest

pytest.importorskip('mcp')
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from jsonschema import Draft202012Validator

from tests.test_connector import approve, live_connector, signed
from tests.test_business import hashed


def test_official_client_can_discover_read_write_and_receive_validation(live_connector, tmp_path):
    file = tmp_path / 'session.json'; signed(live_connector).save(file)
    async def run():
        parameters = StdioServerParameters(command=sys.executable, args=['-m', 'evlet', 'mcp', '--session', str(file), '--allow-writes'], cwd=Path(__file__).resolve().parents[1])
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                initialized = await session.initialize()
                assert initialized.protocolVersion == '2025-11-25'
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {'discover_models', 'describe_model', 'get_record', 'list_records', 'create_record', 'update_record', 'delete_record', 'run_action', 'job_status',
                        'aggregate_records', 'change_state'} <= names
                for tool in tools.tools:
                    Draft202012Validator.check_schema(tool.inputSchema)
                description = await session.call_tool('describe_model', {'model': 'project'})
                metadata = description.structuredContent
                for schema in metadata['schemas'].values():
                    Draft202012Validator.check_schema(schema)
                values = {'title': 'Official client', 'children': {'tasks': [{'title': 'Done', 'done': True}]}}
                Draft202012Validator(metadata['schemas']['create']).validate(values)
                created = await session.call_tool('create_record', {'model': 'project', 'values': values})
                assert not created.isError
                record = created.structuredContent
                Draft202012Validator(metadata['schemas']['read']).validate(record)
                assert next(move for move in record['_moves'] if move['key'] == 'start')['primary'] is True
                assert json.loads(created.content[0].text) == record
                read = await session.call_tool('get_record', {'model': 'project', 'id': record['id']})
                assert not read.isError
                Draft202012Validator(metadata['schemas']['read']).validate(read.structuredContent)
                assert read.structuredContent['children']['tasks'][0]['title'] == 'Done'
                invalid = await session.call_tool('update_record', {'model': 'project', 'id': record['id'], 'values': {'progress': 101}})
                assert invalid.isError and invalid.structuredContent['status'] == 422
                assert 'progress' in invalid.structuredContent['fields']
                # State moves and deletes wait for the person's approval in Evlet; the same call then runs.
                waiting = await session.call_tool('change_state', {'model': 'project', 'id': record['id'], 'rule': 'start'})
                assert waiting.isError and 'approval' in waiting.structuredContent['error']
                approve(live_connector)
                moved = await session.call_tool('change_state', {'model': 'project', 'id': record['id'], 'rule': 'start'})
                assert not moved.isError and moved.structuredContent['status'] == 'active'
                Draft202012Validator(metadata['schemas']['read']).validate(moved.structuredContent)
                selected = await session.call_tool('list_records', {'model': 'project', 'filters': {'id': record['id']}})
                assert selected.structuredContent['total'] == 1
                assert (await session.call_tool('delete_record', {'model': 'project', 'id': record['id']})).isError
                approve(live_connector)
                deleted = await session.call_tool('delete_record', {'model': 'project', 'id': record['id']})
                assert deleted.structuredContent == {'deleted': True}
                # Revocation of this exact session is observed on the next tool call.
                from evlet import HTTPClient
                HTTPClient.load(file).request('POST', '/api/auth/logout', {})
                denied = await session.call_tool('get_record', {'model': 'project', 'id': 1})
                assert denied.isError and denied.structuredContent['status'] == 401
    asyncio.run(run())


def remote_app():
    """uvicorn factory for the remote test: the Playground on a disposable database and a free loopback port."""
    import os
    from evlet import LocalAuth
    from examples.playground.app import build_app
    from examples.playground.migrate import configured_database
    database = configured_database()
    return build_app(database, LocalAuth(database, origins=(f"http://127.0.0.1:{os.environ['EVLET_TEST_PORT']}",), secure_cookies=False))


@pytest.fixture
def remote_server(tmp_path, monkeypatch, hashed):
    import os, socket, subprocess, time
    from evlet import ConnectorError, HTTPClient
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'remote.sqlite3'))
    monkeypatch.setenv('EVLET_TEST_PORT', str(port))
    app = remote_app(); app.initialize()
    with app.database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role,allow_mcp) VALUES (?,?,?,1) ON CONFLICT(username) DO UPDATE SET password=excluded.password, allow_mcp=1', ('editor', hashed, 'editor'))
    app.database.close()
    with (tmp_path / 'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'tests.test_mcp_sdk:remote_app', '--factory', '--no-proxy-headers', '--host', '127.0.0.1', '--port', str(port)],
                                   env=dict(os.environ), stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    HTTPClient(f'http://127.0.0.1:{port}').get('/api/auth/session'); break
                except ConnectorError:
                    time.sleep(.05)
            yield f'http://127.0.0.1:{port}'
        finally:
            process.terminate(); process.wait(timeout=10)


def test_official_client_signs_in_with_oauth_and_calls_tools_over_http(remote_server):
    from urllib.parse import parse_qs, urlsplit
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    from mcp.shared.auth import OAuthClientMetadata
    from evlet import HTTPClient
    from tests.test_business import PASSWORD

    class Memory:
        tokens = client = None
        async def get_tokens(self): return self.tokens
        async def set_tokens(self, tokens): self.tokens = tokens
        async def get_client_info(self): return self.client
        async def set_client_info(self, client): self.client = client

    seen = {}
    async def redirect(url):
        seen['url'] = url
    async def callback():
        # The person: sign in, read the consent page's request, approve.
        query = urlsplit(seen['url']).query
        person = HTTPClient(remote_server).login('editor', PASSWORD)
        assert person.get('/api/_oauth/authorize?' + query)['client'] == 'Official client'
        answer, _ = person.request('POST', '/api/_oauth/authorize', {'query': query, 'approve': True, 'allow_changes': True})
        params = parse_qs(urlsplit(answer['redirect']).query)
        return params['code'][0], params.get('state', [None])[0]

    storage = Memory()
    provider = OAuthClientProvider(server_url=remote_server + '/_/mcp', storage=storage, redirect_handler=redirect, callback_handler=callback,
                                   client_metadata=OAuthClientMetadata(client_name='Official client', redirect_uris=['http://127.0.0.1:33418/callback'],
                                                                       grant_types=['authorization_code', 'refresh_token'], response_types=['code']))
    async def run():
        async with create_mcp_http_client(auth=provider) as http, streamable_http_client(remote_server + '/_/mcp', http_client=http) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert 'discover_models' in tools and 'create_record' not in tools  # mcp.allow_writes is off
                found = await session.call_tool('discover_models', {})
                assert not found.isError and 'project' in [item['name'] for item in found.structuredContent['models']]
                blocked = await session.call_tool('create_record', {'model': 'project', 'values': {'title': 'No'}})
                assert blocked.isError and blocked.structuredContent['status'] == 403
    asyncio.run(run())
    assert storage.tokens.access_token.startswith('evm_') and storage.tokens.refresh_token.startswith('evr_')
