"""The Tool Engine over /api/_tools: one tool list for Eva, MCP and the API, the person's permissions, and one log."""
import json

import pytest

from evlet import Application, Database, LocalAuth, Model, Move, Registry, Rule, State, Text, api
from evlet.services import Endpoint
from evlet.tools import BY_NAME, arguments, redacted
from evlet.connector import ConnectorError
from test_business import PASSWORD, client, http
from test_rules_types import add_user
from test_forms_services import Ticket, Tickets, count_tickets


@api(permission=(Ticket, 'update'), tool=True)
def rename_ticket(context, ticket: str, name: str):
    """Rename one ticket."""
    context.update(Ticket, ticket, title=name)
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
    created = execute(writer, 'create_record', model='ticket', values={'title': 'Leak'})
    assert created[0] == 200 and created[1]['result']['title'] == 'Leak'
    ticket = created[1]['result']['id']
    # A read-only key reads through the tool route, but its writes are refused there too.
    assert execute(reader, 'list_records', model='ticket')[1]['result']['total'] == 1
    refused = execute(reader, 'update_record', model='ticket', id=ticket, values={'title': 'Changed'})
    assert refused[0] == 403
    assert execute(reader, 'list_records', model='ticket', limit=1000)[0] == 422
    assert execute(reader, 'drop_tables')[0] == 404
    # Reports run with POST but only read.
    report = execute(reader, 'run_report', report='Tickets', filters={'term': 'Le'})
    assert report[0] == 200 and report[1]['result']['total'] == 1
    assert report[1]['result']['view'] == '/reports/Tickets?term=Le'
    assert execute(reader, 'list_reports')[1]['result']['reports'][0]['name'] == 'Tickets'
    # The API asks no approval: the key is the person's own program. Changes still need a current revision.
    assert execute(writer, 'update_record', model='ticket', id=ticket, values={'title': 'Fixed'})[1]['result']['title'] == 'Fixed'
    assert execute(writer, 'delete_record', model='ticket', id=ticket)[1]['result'] == {'deleted': True}
    log = client(app)('GET', '/api/_controls/tools?origin=api')[1]['items']
    assert [(item['tool'], item['outcome'], item['client']) for item in log][:3] == [('delete_record', 'ok', 'write script'), ('update_record', 'ok', 'write script'),
                                                                                    ('list_reports', 'ok', 'read script')]
    assert ('update_record', 'denied') in [(item['tool'], item['outcome']) for item in log]
    # Values are never logged, only which fields were sent.
    assert all('Leak' not in json.dumps(item) and 'Fixed' not in json.dumps(item) for item in log)
    assert next(item for item in log if item['tool'] == 'update_record' and item['outcome'] == 'ok')['inputs'] == {'model': 'ticket', 'id': ticket, 'values': ['title']}


def test_only_opted_in_services_are_tools(app):
    admin = client(app)
    ticket = admin('POST', '/api/ticket', {'title': 'Old'})[1]['id']
    services = execute(admin, 'list_services')[1]['result']['services']
    assert [service['name'] for service in services] == ['test.rename']
    assert services[0]['description'] == 'Rename one ticket.' and services[0]['arguments']['required'] == ['ticket', 'name']
    assert execute(admin, 'call_service', service='test.count')[0] == 404
    assert execute(admin, 'call_service', service='test.rename', arguments={'ticket': ticket, 'name': 'New'})[1]['result'] == {'renamed': ticket}
    assert admin('GET', f'/api/ticket/{ticket}')[1]['title'] == 'New'
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
    with pytest.raises(ConnectorError):
        arguments(BY_NAME['change_state'], {'model': 'ticket', 'id': '0000000000001', 'to': '../actions/delete'})
    assert redacted({'model': 'x', 'values': {'salary': 1}, 'search': 'secret', 'filters': {'email': 'a@b'}}) == \
        {'model': 'x', 'values': ['salary'], 'search': '…', 'filters': ['email']}


class Shipment(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update'))}
    title = Text(default='')
    status = State(('draft', 'packed', 'sent', 'lost'), moves=(
        Move('draft', 'packed', 'Pack'),
        Move('packed', 'sent', 'Send', roles=('admin',)),
        Move(('draft', 'packed'), 'lost', 'Mark lost'),
    ), cancelled=('lost',), final=('sent', 'lost'))


@pytest.fixture
def moves(tmp_path):
    db = Database(tmp_path / 'moves.sqlite3', Registry(Shipment))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


def test_change_state_makes_state_moves_through_the_move_route(moves):
    admin, editor = client(moves), add_user(moves, 'editor')
    assert 'change_state' in {tool['name'] for tool in editor('GET', '/api/_tools')[1]['tools']}
    shipment = admin('POST', '/api/shipment', {'title': 'Box'})[1]['id']
    # A move the role has.
    moved = execute(editor, 'change_state', model='shipment', id=shipment, to='packed')
    assert moved[0] == 200 and moved[1]['result']['status'] == 'packed'
    # A move for other roles is refused as the route refuses it.
    refused = execute(editor, 'change_state', model='shipment', id=shipment, to='sent')
    assert refused[0] == 403 and admin('GET', f'/api/shipment/{shipment}')[1]['status'] == 'packed'
    # A move that does not start from the current state is refused too.
    assert execute(admin, 'change_state', model='shipment', id=shipment, to='draft')[0] == 409
    assert execute(admin, 'change_state', model='shipment', id=shipment, to='sent')[1]['result']['status'] == 'sent'
    assert execute(admin, 'change_state', model='shipment', id=shipment, to='lost')[0] == 409
    assert execute(admin, 'change_state', model='shipment', id=shipment, to='a/../b')[0] == 422
    log = admin('GET', '/api/_controls/tools?tool=change_state')[1]['items']
    assert [item['outcome'] for item in log] == ['failed', 'failed', 'ok', 'failed', 'denied', 'ok']
    assert log[-1]['inputs'] == {'model': 'shipment', 'id': shipment, 'to': 'packed'}


def test_change_state_asks_before_a_move_and_never_for_one_the_role_lacks(moves):
    from evlet.auth import KeyAccess, User
    from evlet.tools import ApprovalNeeded
    admin = client(moves)
    add_user(moves, 'editor')
    shipment = admin('POST', '/api/shipment', {'title': 'Box'})[1]['id']
    with moves.database._connection() as connection:
        editor_id = connection.execute("SELECT id FROM evlet_users WHERE username='editor'").fetchone()[0]
    eva = User(editor_id, 'editor', 'editor', access=KeyAccess(True, None, None, None, 'eva'))
    tools = moves.business.tools
    # Packing is a move this role has: it waits for the person's click, in the confirm group.
    with pytest.raises(ApprovalNeeded) as asked:
        tools.execute(eva, 'change_state', {'model': 'shipment', 'id': shipment, 'to': 'packed'})
    assert asked.value.approval['summary']['group'] == 'confirm' and asked.value.approval['summary']['operation'] == 'packed'
    # Marking it lost is a move into a cancelled state, so it is destructive.
    with pytest.raises(ApprovalNeeded) as asked:
        tools.execute(eva, 'change_state', {'model': 'shipment', 'id': shipment, 'to': 'lost'})
    assert asked.value.approval['summary']['group'] == 'destructive'
    # Nobody is asked to approve a move for another role, or one that does not start here.
    with pytest.raises(ConnectorError) as refused:
        tools.execute(eva, 'change_state', {'model': 'shipment', 'id': shipment, 'to': 'sent'})
    assert refused.value.status == 409
    admin('POST', f'/api/shipment/{shipment}/moves/packed', {}, headers={b'if-match': admin('GET', f'/api/shipment/{shipment}')[2][b'etag']})
    with pytest.raises(ConnectorError) as refused:
        tools.execute(eva, 'change_state', {'model': 'shipment', 'id': shipment, 'to': 'sent'})
    assert refused.value.status == 403
    assert len(tools.approvals(eva, pending_only=True)) == 2


class Voucher(Model):
    confirmable = True
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'confirm', 'cancel', 'reopen'))}
    title = Text(default='')


def test_change_state_confirms_cancels_and_reopens_confirmable_documents(tmp_path):
    db = Database(tmp_path / 'vouchers.sqlite3', Registry(Voucher))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    admin = client(app)
    assert 'change_state' in {tool['name'] for tool in admin('GET', '/api/_tools')[1]['tools']}
    voucher = admin('POST', '/api/voucher', {'title': 'Fuel'})[1]['id']
    assert execute(admin, 'change_state', model='voucher', id=voucher, to='posted')[0] == 422
    assert execute(admin, 'change_state', model='voucher', id=voucher, to='confirm')[0] == 200
    assert execute(admin, 'change_state', model='voucher', id=voucher, to='cancel', reason='Entered twice')[0] == 200
    assert execute(admin, 'change_state', model='voucher', id=voucher, to='reopen')[0] == 200
    assert admin('GET', f'/api/voucher/{voucher}')[1]['_lifecycle']['state'] == 'draft'
