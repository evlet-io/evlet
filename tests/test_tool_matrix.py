"""The same person through every entry point gets the same answer: a session, an API key and an MCP client all
go through the Tool Engine, so rows, fields, operations and actions are refused alike. Real SQLite and ASGI."""
import pytest

from evlet import Policy, Rule
from examples.playground.models import Customer, Project, registry
from tests.test_assistants import allow, connect, settings, tool
from tests.test_business import client, hashed, http, secured  # noqa: F401


def outcome(status, payload):
    """('ok', result) or ('error', status), whichever way the entry point reports it."""
    return ('ok', payload) if status == 200 else ('error', status)


@pytest.fixture
def callers(secured):
    """For each role, three ways to call a tool as that person: session, API key and MCP client."""
    settings(secured, allow_writes=True)
    allow(secured)
    admin = client(secured)
    made = {}
    for role in ('editor', 'viewer'):
        person = client(secured, role)
        created = admin('POST', '/api/_controls/keys', {'name': f'{role} key', 'user': role, 'scope': {'access': 'write' if role == 'editor' else 'read', 'models': None}})
        assert created[0] == 201, created
        token = created[1]['token']
        _, tokens, _ = connect(secured, role)

        def by_session(name, arguments, person=person):
            status, body, _ = person('POST', '/api/_tools/execute', {'tool': name, 'arguments': arguments})
            return outcome(status, body.get('result') if status == 200 else body)

        def by_key(name, arguments, token=token):
            status, body, _ = http(secured, 'POST', '/api/_tools/execute', {'tool': name, 'arguments': arguments},
                                   headers={b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''})
            return outcome(status, body.get('result') if status == 200 else body)

        def by_mcp(name, arguments, access=tokens['access_token']):
            result = tool(secured, access, name, arguments)
            return ('error', result['structuredContent']['status']) if result['isError'] else ('ok', result['structuredContent'])

        made[role] = {'session': by_session, 'key': by_key, 'mcp': by_mcp}
    # Row and field rules, set after the settings above so a Controls refresh doesn't replace them.
    secured.business.policy = Policy(registry, rules={
        ('viewer', Customer): Rule(read_fields=('name',), scope=lambda user: {'active': True}),
        ('editor', Customer): Rule(operations=('read', 'update'), read_fields=('name',), write_fields=('name',), scope=lambda user: {'active': True}),
        ('viewer', Project): Rule(()),
    })
    visible = secured.database.create(Customer, name='Visible', email='seen@example.test', active=True)
    hidden = secured.database.create(Customer, name='Hidden', email='hidden@example.test', active=False)
    return made, visible.id, hidden.id


def same(calls, name, arguments):
    """Call through every entry point and require one answer."""
    answers = {origin: call(name, arguments) for origin, call in calls.items()}
    assert len({repr(answer) for answer in answers.values()}) == 1, answers
    return next(iter(answers.values()))


def test_reads_follow_row_and_field_rules_everywhere(callers):
    made, visible, hidden = callers
    for role in ('editor', 'viewer'):
        kind, page = same(made[role], 'list_records', {'model': 'customer'})
        assert kind == 'ok' and page['total'] == 1 and page['items'] == [{'id': visible, 'name': 'Visible'}]
        assert same(made[role], 'get_record', {'model': 'customer', 'id': hidden}) == ('error', 404)
        # Filtering on a field the role can't read is refused, not answered by inference.
        assert same(made[role], 'list_records', {'model': 'customer', 'filters': {'email': 'seen@example.test'}}) == ('error', 403)
        assert same(made[role], 'aggregate_records', {'model': 'customer', 'measures': 'count:count', 'group_by': 'email'})[0] == 'error'
    assert same(made['viewer'], 'list_records', {'model': 'project'}) == ('error', 403)


def test_changes_follow_operations_and_writable_fields_everywhere(callers):
    made, visible, hidden = callers
    editor, viewer = made['editor'], made['viewer']
    assert same(editor, 'update_record', {'model': 'customer', 'id': visible, 'values': {'email': 'changed@example.test'}}) == ('error', 403)
    assert same(editor, 'update_record', {'model': 'customer', 'id': hidden, 'values': {'name': 'Leaked'}}) == ('error', 404)
    # A delete the role can't make is refused at once, not turned into an approval request.
    assert same(editor, 'delete_record', {'model': 'customer', 'id': visible}) == ('error', 403)
    assert same(viewer, 'create_record', {'model': 'customer', 'values': {'name': 'No'}}) == ('error', 403)
    assert same(viewer, 'update_record', {'model': 'customer', 'id': visible, 'values': {'name': 'No'}}) == ('error', 403)
    # Allowed changes run, through each entry point in turn.
    for index, call in enumerate(editor.values()):
        kind, record = call('update_record', {'model': 'customer', 'id': visible, 'values': {'name': f'Renamed {index}'}})
        assert kind == 'ok' and record['name'] == f'Renamed {index}'


def test_the_playground_offers_a_service_as_a_tool(secured):
    editor, viewer = client(secured, 'editor'), client(secured, 'viewer')
    editor('POST', '/api/project', {'name': 'Fit-out', 'children': {'tasks': [{'title': 'Order', 'done': True}, {'title': 'Fit', 'done': False}]}})
    services = editor('POST', '/api/_tools/execute', {'tool': 'list_services', 'arguments': {}})[1]['result']['services']
    assert [service['name'] for service in services] == ['playground.project_overview']
    overview = editor('POST', '/api/_tools/execute', {'tool': 'call_service', 'arguments': {'service': 'playground.project_overview', 'arguments': {'project': 1}}})
    assert overview[1]['result'] == {'project': 'Fit-out', 'status': 'planned', 'progress': 0, 'budget': '0.00', 'tasks': 2, 'done': 1}
    # The service declares (Project, 'read'), so a viewer may call it too; the route checks that, not the tool.
    assert viewer('POST', '/api/_tools/execute', {'tool': 'call_service', 'arguments': {'service': 'playground.project_overview', 'arguments': {'project': 1}}})[1]['result']['done'] == 1
