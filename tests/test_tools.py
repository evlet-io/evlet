"""The Tool Engine over /api/_tools: one tool list for Eva, MCP and the API, the person's permissions, and one log."""
import json

import pytest

from evlet import Application, Database, LocalAuth, Registry, api
from evlet.services import Endpoint
from evlet.tools import BY_NAME, arguments, redacted
from evlet.connector import ConnectorError
from test_business import PASSWORD, client, http
from test_forms_services import Ticket, Tickets, count_tickets


@api(permission=(Ticket, 'update'), tool=True)
def rename_ticket(context, ticket: str, name: str):
    """Rename one ticket."""
    context.update(Ticket, ticket, name=name)
    return {'renamed': ticket}


@pytest.fixture
def app(tmp_path):
    db = Database(tmp_path / 'tools.sqlite3', Registry(Ticket))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(db, auth=auth, reports=(Tickets,), apis=(
        Endpoint('test.count', count_tickets, 'GET', (Ticket, 'read')),
        Endpoint('test.rename', rename_ticket, 'POST', (Ticket, 'update'), tool=True)))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


def key(app, access):
    admin = client(app)
    token = admin('POST', '/api/_controls/keys', {'name': f'{access} script', 'user': 'admin', 'scope': {'access': access, 'models': None}})[1]['token']
    def call(method, path, data=None):
        return http(app, method, path, data, headers={b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''})
    return call


def execute(caller, tool, **values):
    return caller('POST', '/api/_tools/execute', {'tool': tool, 'arguments': values})


def test_the_listing_follows_the_callers_access_and_the_app(app):
    admin = client(app)
    names = {tool['name'] for tool in admin('GET', '/api/_tools')[1]['tools']}
    # Reports, opted-in services and the record tools; no jobs, actions or lifecycle here, and Eva's own file tool is Eva's.
    assert {'list_records', 'create_record', 'run_report', 'list_reports', 'list_services', 'call_service', 'delete_record'} <= names
    assert not {'job_status', 'run_action', 'change_state', 'create_file'} & names
    reader = {tool['name']: tool for tool in key(app, 'read')('GET', '/api/_tools')[1]['tools']}
    assert all(tool['kind'] == 'read' for tool in reader.values()) and 'list_records' in reader
    assert reader['list_records']['input_schema']['additionalProperties'] is False


def test_api_calls_run_as_the_key_and_are_logged(app):
    writer, reader = key(app, 'write'), key(app, 'read')
    created = execute(writer, 'create_record', model='ticket', values={'name': 'Leak'})
    assert created[0] == 200 and created[1]['result']['name'] == 'Leak'
    ticket = created[1]['result']['id']
    # A read-only key reads through the tool route, but its writes are refused there too.
    assert execute(reader, 'list_records', model='ticket')[1]['result']['total'] == 1
    refused = execute(reader, 'update_record', model='ticket', id=ticket, values={'name': 'Changed'})
    assert refused[0] == 403
    assert execute(reader, 'list_records', model='ticket', limit=1000)[0] == 422
    assert execute(reader, 'drop_tables')[0] == 404
    # Reports run with POST but only read.
    report = execute(reader, 'run_report', report='Tickets', filters={'term': 'Le'})
    assert report[0] == 200 and report[1]['result']['total'] == 1
    assert execute(reader, 'list_reports')[1]['result']['reports'][0]['name'] == 'Tickets'
    # The API asks no approval: the key is the person's own program. Changes still need a current revision.
    assert execute(writer, 'update_record', model='ticket', id=ticket, values={'name': 'Fixed'})[1]['result']['name'] == 'Fixed'
    assert execute(writer, 'delete_record', model='ticket', id=ticket)[1]['result'] == {'deleted': True}
    log = client(app)('GET', '/api/_controls/tools?origin=api')[1]['items']
    assert [(item['tool'], item['outcome'], item['client']) for item in log][:3] == [('delete_record', 'ok', 'write script'), ('update_record', 'ok', 'write script'),
                                                                                    ('list_reports', 'ok', 'read script')]
    assert ('update_record', 'denied') in [(item['tool'], item['outcome']) for item in log]
    # Values are never logged, only which fields were sent.
    assert all('Leak' not in json.dumps(item) and 'Fixed' not in json.dumps(item) for item in log)
    assert next(item for item in log if item['tool'] == 'update_record' and item['outcome'] == 'ok')['inputs'] == {'model': 'ticket', 'id': ticket, 'values': ['name']}


def test_only_opted_in_services_are_tools(app):
    admin = client(app)
    ticket = admin('POST', '/api/ticket', {'name': 'Old'})[1]['id']
    services = execute(admin, 'list_services')[1]['result']['services']
    assert [service['name'] for service in services] == ['test.rename']
    assert services[0]['description'] == 'Rename one ticket.' and services[0]['arguments']['required'] == ['ticket', 'name']
    assert execute(admin, 'call_service', service='test.count')[0] == 404
    assert execute(admin, 'call_service', service='test.rename', arguments={'ticket': ticket, 'name': 'New'})[1]['result'] == {'renamed': ticket}
    assert admin('GET', f'/api/ticket/{ticket}')[1]['name'] == 'New'
    with pytest.raises(ValueError):
        api(permission=(Ticket, 'read'), public=True, tool=True)


def test_people_answer_only_their_own_approvals_from_a_session(app):
    admin = client(app)
    assert admin('GET', '/api/_tools/approvals')[1] == {'items': []}
    assert admin('POST', '/api/_tools/approvals/1', {'approve': True})[0] == 404
    assert key(app, 'write')('GET', '/api/_tools/approvals')[0] == 403


def test_arguments_and_redaction():
    tool = BY_NAME['update_record']
    with pytest.raises(ConnectorError):
        arguments(tool, {'model': 'ticket', 'id': '0000000000001'})
    with pytest.raises(ConnectorError):
        arguments(tool, {'model': '../auth', 'id': '0000000000001', 'values': {}})
    with pytest.raises(ConnectorError):
        arguments(BY_NAME['change_state'], {'model': 'ticket', 'id': '0000000000001', 'operation': 'post'})
    assert redacted({'model': 'x', 'values': {'salary': 1}, 'search': 'secret', 'filters': {'email': 'a@b'}}) == \
        {'model': 'x', 'values': ['salary'], 'search': '…', 'filters': ['email']}
