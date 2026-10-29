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

from tests.test_connector import live_connector, signed
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
                assert {tool.name for tool in tools.tools} == {'discover_models', 'describe_model', 'get_record', 'list_records', 'create_record', 'update_record', 'delete_record', 'run_action', 'job_status'}
                for tool in tools.tools:
                    Draft202012Validator.check_schema(tool.inputSchema)
                description = await session.call_tool('describe_model', {'model': 'project'})
                metadata = description.structuredContent
                for schema in metadata['schemas'].values():
                    Draft202012Validator.check_schema(schema)
                values = {'name': 'Official client', 'children': {'tasks': [{'title': 'Done', 'done': True}]}}
                Draft202012Validator(metadata['schemas']['create']).validate(values)
                created = await session.call_tool('create_record', {'model': 'project', 'values': values})
                assert not created.isError
                record = created.structuredContent
                Draft202012Validator(metadata['schemas']['read']).validate(record)
                assert json.loads(created.content[0].text) == record
                read = await session.call_tool('get_record', {'model': 'project', 'id': record['id']})
                assert read.structuredContent['children']['tasks'][0]['title'] == 'Done'
                invalid = await session.call_tool('update_record', {'model': 'project', 'id': record['id'], 'values': {'progress': 101}})
                assert invalid.isError and invalid.structuredContent['status'] == 422
                assert 'progress' in invalid.structuredContent['fields']
                action = await session.call_tool('run_action', {'model': 'project', 'id': record['id'], 'action': 'start'})
                assert action.structuredContent['status'] == 'active'
                selected = await session.call_tool('list_records', {'model': 'project', 'filters': {'id': record['id']}})
                assert selected.structuredContent['total'] == 1
                deleted = await session.call_tool('delete_record', {'model': 'project', 'id': record['id']})
                assert deleted.structuredContent == {'deleted': True}
                # Revocation of this exact session is observed on the next tool call.
                from evlet import HTTPClient
                HTTPClient.load(file).request('POST', '/api/auth/logout', {})
                denied = await session.call_tool('get_record', {'model': 'project', 'id': 1})
                assert denied.isError and denied.structuredContent['status'] == 401
    asyncio.run(run())
