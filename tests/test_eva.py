"""Eva on the Tool Engine, against the Playground, SQLite and the ASGI application. Only the AI provider is scripted."""
import asyncio
import base64
import io
import json
import zipfile

import pytest

from evlet import AI, LocalAuth, Security
from examples.playground.app import build_app, playground_routes
from examples.playground.migrate import configured_database
from tests.test_business import client, hashed, http, PASSWORD  # noqa: F401


class Script:
    """A provider that answers from a list of turns. Each turn is a list of ('text', ...), ('tool_call', {...}) and ('usage', {...})."""

    def __init__(self, *turns):
        self.turns, self.requests, self.before = list(turns), [], {}

    def stream(self, request):
        self.requests.append(request)
        hook = self.before.pop(len(self.requests), None)
        if hook is not None:
            hook()
        turn = self.turns.pop(0) if self.turns else [('text', 'Done.')]
        yield from turn


def call(tool, **arguments):
    return ('tool_call', {'id': f'call_{tool}_{len(arguments)}', 'name': tool, 'arguments': json.dumps(arguments)})


def usage(input=1000, output=200, cached=0):
    return ('usage', {'input': input, 'output': output, 'cached': cached, 'reasoning': 0})


def make(tmp_path, monkeypatch, hashed, script=None, *, ai=True, enabled=True):
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'eva.sqlite3'))
    database = configured_database()
    routes = playground_routes(8000)
    auth = LocalAuth(database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False, security=Security())
    config = AI(enabled=True, provider=script or Script(), models={'Standard': 'test-model', 'Fast': 'test-mini'}) if ai else None
    app = build_app(database, auth, routes=routes, ai=config)
    app.initialize()
    with app.database.transaction() as tx:
        for role in ('admin', 'editor', 'viewer'):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role,allow_eva) VALUES (?,?,?,1)', (role, hashed, role))
        if enabled:
            for key, value in (('eva.enabled', True), ('eva.rates', {'Standard': {'input': '7.35', 'cached': '1.84', 'output': '29.38'}})):
                tx.connection.execute("INSERT INTO evlet_overrides(kind,key,value) VALUES ('setting',?,?)", (key, json.dumps(value)))
            tx.connection.execute('UPDATE evlet_config SET version=version+1')
    return app


def setting(app, key, value):
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_overrides(kind,key,value) VALUES ('setting',?,?) ON CONFLICT(kind,key) DO UPDATE SET value=excluded.value",
                              (key, json.dumps(value)))
        tx.connection.execute('UPDATE evlet_config SET version=version+1')


def ask(app, person, text, conversation=None, **extra):
    """Send a message and wait for Eva to finish. Returns (conversation id, run id, the stored conversation)."""
    if conversation is None:
        status, created, _ = person('POST', '/api/_eva/conversations', {})
        assert status == 201, created
        conversation = created['id']
    status, sent, _ = person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': text, **extra})
    assert status == 202, sent
    app.business.eva.wait(sent['run']['id'])
    return conversation, sent['run']['id'], person('GET', f'/api/_eva/conversations/{conversation}')[1]


@pytest.fixture
def eva(tmp_path, monkeypatch, hashed):
    script = Script()
    app = make(tmp_path, monkeypatch, hashed, script)
    return app, script


def test_eva_does_not_exist_until_config_controls_and_the_person_allow_it(tmp_path, monkeypatch, hashed):
    plain = make(tmp_path / 'a', monkeypatch, hashed, ai=False)
    person = client(plain, 'editor')
    assert person('GET', '/api/_eva')[0] == 404
    with plain.database._connection() as connection:
        assert not plain.database.columns(connection, 'evlet_eva_conversations')
    assert client(plain)('GET', '/api/_controls/eva')[1]['configured'] is False
    # Turned on in config.py but not yet in Controls.
    app = make(tmp_path / 'b', monkeypatch, hashed, enabled=False)
    assert client(app, 'editor')('GET', '/api/_eva')[0] == 404
    setting(app, 'eva.enabled', True)
    editor = client(app, 'editor')
    assert editor('GET', '/api/_eva')[0] == 200
    with app.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_users SET allow_eva=0 WHERE username='editor'")
    assert editor('GET', '/api/_eva')[0] == 404
    assert editor('POST', '/api/_eva/conversations', {})[0] == 404


def test_chat_runs_tools_as_the_person_and_keeps_history_usage_and_the_log(eva):
    app, script = eva
    script.turns = [[('text', 'Let me look.'), call('list_records', model='project'), usage(1200, 40)],
                    [('text', 'You have '), ('text', 'one project.'), usage(1500, 30, cached=1000)]]
    editor = client(app, 'editor')
    editor('POST', '/api/project', {'name': 'Fit-out'})
    conversation, run, stored = ask(app, editor, 'How many projects do I have?')
    assert [message['role'] for message in stored['messages']] == ['user', 'assistant', 'assistant']
    assert stored['messages'][-1]['content'] == 'You have one project.'
    assert stored['conversation']['title'] == 'How many projects do I have?'
    [activity] = stored['runs'][0]['activities']
    assert activity['status'] == 'done' and activity['label'] == 'Read project' and activity['summary'] == '1 record'
    assert activity['records'] == [{'model': 'project', 'id': 1}] and activity['inputs'] == {'model': 'project'}
    assert stored['runs'][0]['status'] == 'completed'
    # The provider saw the tools this person may use, and the tool result as data.
    names = {tool['function']['name'] for tool in script.requests[0]['tools']}
    assert {'list_records', 'create_record', 'create_file'} <= names and 'delete_record' in names
    assert script.requests[1]['messages'][-1]['role'] == 'tool' and 'Fit-out' in script.requests[1]['messages'][-1]['content']
    assert script.requests[0]['model'] == 'test-model'
    # Cost: (200 input + 1000 cached at their rates + 30 output) for the second request, both kept exactly.
    assert stored['usage']['tokens'] == 1240 + 1530
    log = client(app)('GET', '/api/_controls/tools?origin=eva')[1]['items']
    assert [(item['tool'], item['outcome'], item['client'], item['username']) for item in log] == [('list_records', 'ok', 'Eva', 'editor')]
    # Someone else's chat is not found, admins included.
    assert client(app)('GET', f'/api/_eva/conversations/{conversation}')[0] == 404
    assert client(app, 'viewer')('GET', f'/api/_eva/runs/{run}/events')[0] == 404


def test_a_refused_tool_is_reported_and_eva_carries_on(eva):
    app, script = eva
    script.turns = [[call('create_record', model='project', values={'name': 'Not mine'})], [('text', "Your role can't create projects.")]]
    viewer = client(app, 'viewer')
    _, _, stored = ask(app, viewer, 'Create a project called Not mine')
    [activity] = stored['runs'][0]['activities']
    assert activity['status'] == 'refused' and activity['error']
    assert app.database.count(__import__('examples.playground.models', fromlist=['Project']).Project) == 0
    assert json.loads(script.requests[1]['messages'][-1]['content'])['status'] == 403


def test_changes_that_need_approval_wait_then_run_exactly_once(eva):
    app, script = eva
    editor = client(app, 'editor')
    project = editor('POST', '/api/project', {'name': 'Approval'})[1]
    script.turns = [[call('run_action', model='project', id=project['id'], action='start')], [('text', 'Started.')]]
    conversation, run, stored = ask(app, editor, 'Start the Approval project')
    assert stored['runs'][0]['status'] == 'awaiting_approval'
    [activity] = stored['runs'][0]['activities']
    approval = activity['approval']
    assert activity['status'] == 'approval' and approval['summary']['action'] == 'start' and approval['summary']['record']['name'] == 'Approval'
    assert editor('GET', f"/api/project/{project['id']}")[1]['status'] == 'planned'
    # Another message waits until this is answered; an API key or assistant can't answer it.
    assert editor('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'And?'})[0] == 409
    assert client(app)('POST', f"/api/_tools/approvals/{approval['id']}", {'approve': True})[0] == 404
    assert editor('POST', f"/api/_tools/approvals/{approval['id']}", {'approve': True})[0] == 200
    app.business.eva.wait(run)
    stored = editor('GET', f'/api/_eva/conversations/{conversation}')[1]
    assert stored['runs'][0]['status'] == 'completed' and stored['messages'][-1]['content'] == 'Started.'
    assert stored['runs'][0]['activities'][0]['status'] == 'done'
    assert editor('GET', f"/api/project/{project['id']}")[1]['status'] == 'active'
    assert editor('POST', f"/api/_tools/approvals/{approval['id']}", {'approve': True})[0] == 409
    log = client(app)('GET', '/api/_controls/tools?tool=run_action')[1]['items']
    assert [item['outcome'] for item in log] == ['ok', 'approval']


def test_a_declined_change_is_not_made(eva):
    app, script = eva
    editor = client(app, 'editor')
    project = editor('POST', '/api/project', {'name': 'Keep'})[1]
    script.turns = [[call('delete_record', model='project', id=project['id'])], [('text', 'I left it.')]]
    conversation, run, stored = ask(app, editor, 'Delete Keep')
    approval = stored['runs'][0]['activities'][0]['approval']
    editor('POST', f"/api/_tools/approvals/{approval['id']}", {'approve': False})
    app.business.eva.wait(run)
    stored = editor('GET', f'/api/_eva/conversations/{conversation}')[1]
    assert stored['runs'][0]['status'] == 'completed' and editor('GET', f"/api/project/{project['id']}")[0] == 200
    assert 'declined' in script.requests[1]['messages'][-1]['content']


def test_losing_eva_during_a_run_stops_the_next_step(eva):
    app, script = eva
    script.turns = [[call('list_records', model='project')], [('text', 'never')]]
    def revoke():
        with app.database.transaction() as tx:
            tx.connection.execute("UPDATE evlet_users SET allow_eva=0 WHERE username='editor'")
    script.before[1] = revoke
    editor = client(app, 'editor')
    status, created, _ = editor('POST', '/api/_eva/conversations', {})
    run = editor('POST', f"/api/_eva/conversations/{created['id']}/messages", {'text': 'List'})[1]['run']['id']
    app.business.eva.wait(run)
    with app.database._connection() as connection:
        row = connection.execute('SELECT status, error FROM evlet_eva_runs WHERE id=?', (run,)).fetchone()
        calls = connection.execute("SELECT count(*) FROM evlet_tool_calls WHERE origin='eva'").fetchone()[0]
    assert row['status'] == 'failed' and 'no longer allowed' in row['error'] and calls == 0


def test_allowances_stop_new_requests_and_say_when_they_reset(eva):
    app, script = eva
    setting(app, 'eva.user_limit', {'amount': 1, 'unit': 'requests', 'period': 'week'})
    script.turns = [[('text', 'One.'), usage()]]
    editor = client(app, 'editor')
    conversation, _, _ = ask(app, editor, 'First')
    status = editor('GET', '/api/_eva')[1]['allowance']
    assert status['exhausted'] and status['used'] == 1 and status['resets_text']
    refused = editor('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Second'})
    assert refused[0] == 429 and 'resets' in refused[1]['error']
    # A request is held at its largest cost (the full answer length) before it is sent.
    setting(app, 'eva.user_limit', {'amount': '0.05', 'unit': 'aed', 'period': 'week'})
    _, run, stored = ask(app, editor, 'Third', conversation)
    assert stored['runs'][-1]['status'] == 'failed' and stored['runs'][-1]['error'].startswith('quota:person:')
    # The instance allowance applies to everyone: 0.0132 is already spent.
    setting(app, 'eva.user_limit', {'amount': None, 'unit': 'aed', 'period': 'week'})
    setting(app, 'eva.instance_limit', {'amount': '0.01', 'unit': 'aed', 'period': 'month'})
    assert editor('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Fourth'})[0] == 429


def test_concurrent_reservations_cannot_share_the_last_of_an_allowance(eva):
    app, _ = eva
    setting(app, 'eva.user_limit', {'amount': 2, 'unit': 'requests', 'period': 'day'})
    app.business.controls.refresh()
    from evlet.eva import QuotaError
    from concurrent.futures import ThreadPoolExecutor
    def take():
        try:
            return app.business.eva.reserve(2, 'eva', tokens=10)
        except QuotaError:
            return None
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: take(), range(8)))
    assert sum(result is not None for result in results) == 2


def test_files_go_to_the_model_and_eva_can_make_files(eva):
    app, script = eva
    editor = client(app, 'editor')
    upload = editor('POST', '/api/_eva/files', {'name': 'prices.csv', 'content': base64.b64encode(b'item,price\nBolt,2.50\n').decode()})
    assert upload[0] == 201
    assert editor('POST', '/api/_eva/files', {'name': 'fake.pdf', 'content': base64.b64encode(b'not a pdf').decode()})[0] == 422
    script.turns = [[call('create_file', name='prices.xlsx', rows={'rows': [['Item', 'Price'], ['Bolt', 2.5]]})], [('text', 'Here it is.')]]
    conversation, _, stored = ask(app, editor, 'Make this a spreadsheet', files=[upload[1]['id']])
    parts = script.requests[0]['messages'][-1]['content']
    assert parts[0]['text'] == 'Make this a spreadsheet' and 'Bolt,2.50' in parts[1]['text']
    made = [item for item in stored['files'] if item['kind'] == 'generated']
    assert [item['name'] for item in made] == ['prices.xlsx']
    status, content, headers = editor('GET', f"/api/_eva/files/{made[0]['id']}")
    assert status == 200 and b'attachment' in headers[b'content-disposition']
    sheet = zipfile.ZipFile(io.BytesIO(content)).read('xl/worksheets/sheet1.xml').decode()
    assert 'Bolt' in sheet and '<v>2.5</v>' in sheet
    assert client(app, 'viewer')('GET', f"/api/_eva/files/{made[0]['id']}")[0] == 404
    # Files on records the person can read can be attached too.
    project = editor('POST', '/api/project', {'name': 'Docs'})[1]
    editor('POST', f"/api/project/{project['id']}/attachments", {'name': 'brief.txt', 'content': base64.b64encode(b'Scope').decode()})
    found = editor('GET', '/api/_eva/library?q=brief')[1]['records']
    assert [item['name'] for item in found] == ['brief.txt']
    imported = editor('POST', '/api/_eva/files/import', {'model': 'project', 'id': project['id'], 'file_id': found[0]['file_id']})
    assert imported[0] == 201 and imported[1]['kind'] == 'evlet'


def test_conversations_can_be_searched_renamed_archived_and_deleted(eva):
    app, script = eva
    editor = client(app, 'editor')
    first, _, _ = ask(app, editor, 'Overdue invoices')
    second, _, _ = ask(app, editor, 'Project margins')
    assert [item['id'] for item in editor('GET', '/api/_eva/conversations?q=overdue')[1]['items']] == [first]
    assert editor('PATCH', f'/api/_eva/conversations/{first}', {'title': 'Collections'})[1]['title'] == 'Collections'
    editor('PATCH', f'/api/_eva/conversations/{second}', {'archived': True})
    listing = editor('GET', '/api/_eva/conversations')[1]
    assert [item['id'] for item in listing['items']] == [first] and listing['archived'] == 1
    assert [item['id'] for item in editor('GET', '/api/_eva/conversations?archived=1')[1]['items']] == [second]
    assert editor('DELETE', f'/api/_eva/conversations/{second}')[0] == 204
    assert editor('GET', f'/api/_eva/conversations/{second}')[0] == 404


def test_a_record_can_be_the_context_of_a_chat(eva):
    app, script = eva
    editor = client(app, 'editor')
    project = editor('POST', '/api/project', {'name': 'Context'})[1]
    created = editor('POST', '/api/_eva/conversations', {'title': 'Context', 'context': {'model': 'project', 'id': project['id']}})
    assert created[0] == 201 and created[1]['context'] == {'model': 'project', 'id': project['id']}
    ask(app, editor, 'Why is this late?', created[1]['id'])
    assert f"project record with id {project['id']}" in script.requests[0]['messages'][0]['content']
    assert editor('POST', '/api/_eva/conversations', {'context': {'model': 'project', 'id': 999}})[0] == 404


def test_the_stream_replays_stored_events_and_ends(eva):
    app, script = eva
    script.turns = [[('text', 'Hello there.')]]
    editor = client(app, 'editor')
    _, run, _ = ask(app, editor, 'Hi')
    status, headers, text = stream(app, f'/api/_eva/runs/{run}/stream', editor.cookie)
    assert status == 200 and headers[b'content-type'] == b'text/event-stream'
    assert 'event: run.started' in text and 'event: run.completed' in text and 'Hello there.' in text and 'event: end' in text
    last = [line for line in text.splitlines() if line.startswith('id: ')][-1][4:]
    _, _, again = stream(app, f'/api/_eva/runs/{run}/stream', editor.cookie, last)
    assert 'run.completed' not in again and 'event: end' in again
    assert stream(app, f'/api/_eva/runs/{run}/stream', client(app, 'viewer').cookie)[0] == 404


def stream(app, path, cookie, last=None):
    """GET an SSE route and collect every chunk until the response ends."""
    async def run():
        sent = []
        async def receive():
            await asyncio.sleep(5)
            return {'type': 'http.disconnect'}
        async def send(event):
            sent.append(event)
        headers = [(b'host', b'127.0.0.1:8000'), (b'cookie', cookie.encode()), *([(b'last-event-id', last.encode())] if last else [])]
        await app({'type': 'http', 'method': 'GET', 'path': path, 'query_string': b'', 'headers': headers, 'client': ('127.0.0.1', 1)}, receive, send)
        return sent[0]['status'], dict(sent[0]['headers']), b''.join(event.get('body', b'') for event in sent[1:]).decode()
    return asyncio.run(run())


def test_scheduled_tasks_run_as_their_owner_through_the_jobs_system(eva):
    app, script = eva
    editor = client(app, 'editor')
    parsed = editor('POST', '/api/_eva/tasks/parse', {'text': 'Every Monday at 9 AM, review my projects and summarise them.', 'timezone': 'Asia/Dubai'})[1]
    assert parsed['cron'] == '0 9 * * 1' and parsed['summary'] == 'Mondays at 09:00' and len(parsed['next']) == 3
    assert parsed['instructions'] == 'Review my projects and summarise them.'
    assert editor('POST', '/api/_eva/tasks', {'name': 'Too often', 'instructions': 'x', 'cron': '*/5 * * * *'})[0] == 422
    task = editor('POST', '/api/_eva/tasks', {'name': 'Weekly projects', 'instructions': parsed['instructions'], 'cron': parsed['cron'], 'timezone': 'Asia/Dubai'})
    assert task[0] == 201 and task[1]['next_run_at']
    with app.database._connection() as connection:
        assert connection.execute("SELECT expression, timezone FROM evlet_calendar_schedules WHERE name=?", (f"eva_task_{task[1]['id']}",)).fetchone()[:] == ('0 9 * * 1', 'Asia/Dubai')
    script.turns = [[call('list_records', model='project')], [('text', 'No projects yet.')]]
    assert editor('POST', f"/api/_eva/tasks/{task[1]['id']}/run", {})[0] == 202
    assert app.business.jobs.run_one()
    details = editor('GET', f"/api/_eva/tasks/{task[1]['id']}")[1]
    assert details['last_status'] == 'done' and details['runs'][0]['status'] == 'done'
    result = editor('GET', f"/api/_eva/conversations/{details['runs'][0]['conversation_id']}")[1]
    assert result['conversation']['task_id'] == task[1]['id'] and result['messages'][-1]['content'] == 'No projects yet.'
    # Read only by default: the task's request offered no writing tools.
    assert not {'create_record', 'update_record', 'create_file'} & {tool['function']['name'] for tool in script.requests[0]['tools']}
    assert 'scheduled task' in script.requests[0]['messages'][0]['content']
    # Pause and resume use the jobs schedule; losing Allow Eva stops the task at its next run.
    assert editor('PATCH', f"/api/_eva/tasks/{task[1]['id']}", {'active': False})[1]['active'] is False
    assert editor('PATCH', f"/api/_eva/tasks/{task[1]['id']}", {'active': True})[1]['active'] is True
    with app.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_users SET allow_eva=0 WHERE username='editor'")
    from evlet.auth import User
    with app.database.transaction() as tx:
        row = tx.connection.execute("SELECT id, username, role FROM evlet_users WHERE username='editor'").fetchone()
        app.business.jobs.enqueue(tx, 'eva_task', {'task': task[1]['id'], 'manual': True}, user=User(*row))
    assert app.business.jobs.run_one()
    with app.database._connection() as connection:
        last = connection.execute('SELECT status, error FROM evlet_eva_task_runs ORDER BY id DESC LIMIT 1').fetchone()
        active = connection.execute('SELECT active FROM evlet_eva_tasks').fetchone()[0]
        enabled = connection.execute("SELECT enabled FROM evlet_schedules WHERE name LIKE 'eva_task_%'").fetchone()[0]
    assert last['status'] == 'skipped' and 'no longer allowed' in last['error'] and active == 0 and not enabled


def test_controls_shows_configuration_usage_and_people_get_ai_switches(eva):
    app, script = eva
    script.turns = [[('text', 'Hi.'), usage(1_000_000, 100_000)]]
    editor = client(app, 'editor')
    ask(app, editor, 'Hello')
    admin = client(app)
    config = admin('GET', '/api/_controls/eva')[1]
    assert config['configured'] and config['models'] == ['Standard', 'Fast'] and config['connector'] is None and config['allowed_users'] == 3
    report = admin('GET', '/api/_controls/eva/usage?period=month')[1]
    # 1,000,000 input at 7.35 and 100,000 output at 29.38 per million: AED 10.29.
    assert report['totals']['cost'] == '10.29' and report['totals']['requests'] == 1
    assert report['people'][0]['username'] == 'editor' and report['people'][0]['cost'] == '10.29'
    assert report['conversations'][0]['username'] == 'editor' and 'title' not in report['conversations'][0]
    users = admin('GET', '/api/_controls/users')[1]['items']
    viewer = next(item for item in users if item['username'] == 'viewer')
    assert viewer['allow_eva'] is True and viewer['allow_mcp'] is False
    assert admin('PATCH', f"/api/_controls/users/{viewer['id']}", {'allow_eva': False})[0] in (200, 204)
    assert client(app, 'viewer')('GET', '/api/_eva')[0] == 404
    assert admin('PATCH', f"/api/_controls/users/{viewer['id']}", {'allow_eva': 'no'})[0] == 422
    for key, value in (('eva.confirm', {'create': 'maybe'}), ('eva.model', 'Unknown'), ('eva.user_limit', {'amount': '1.001', 'unit': 'aed', 'period': 'week'}),
                       ('eva.max_steps', 100), ('eva.disabled_tools', ['drop_table'])):
        assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': key, 'value': value}]})[0] == 422, key
    assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'eva.model', 'value': 'Fast'}]})[0] == 200


def test_eva_gets_the_same_answers_as_every_other_entry_point(eva):
    """Rows, fields and operations through Eva match a session's own tool calls (tests/test_tool_matrix.py)."""
    from evlet import Policy, Rule
    from examples.playground.models import Customer, registry
    app, script = eva
    editor = client(app, 'editor')
    app.business.policy = Policy(registry, rules={
        ('editor', Customer): Rule(operations=('read', 'update'), read_fields=('name',), write_fields=('name',), scope=lambda user: {'active': True})})
    visible = app.database.create(Customer, name='Visible', email='seen@example.test', active=True).id
    hidden = app.database.create(Customer, name='Hidden', email='hidden@example.test', active=False).id
    calls = [('list_records', {'model': 'customer'}), ('get_record', {'model': 'customer', 'id': hidden}),
             ('update_record', {'model': 'customer', 'id': visible, 'values': {'email': 'x@example.test'}}),
             ('delete_record', {'model': 'customer', 'id': visible})]
    script.turns = [[('tool_call', {'id': f'call_{index}', 'name': name, 'arguments': json.dumps(arguments)})] for index, (name, arguments) in enumerate(calls)]
    script.turns.append([('text', 'Done.')])
    _, _, stored = ask(app, editor, 'Check customers')
    seen = [json.loads(message['content']) for request in script.requests[1:] for message in request['messages'][-1:]]
    direct = [editor('POST', '/api/_tools/execute', {'tool': name, 'arguments': arguments}) for name, arguments in calls]
    assert seen[0]['items'] == direct[0][1]['result']['items'] == [{'id': visible, 'name': 'Visible'}]
    assert [answer['status'] for answer in seen[1:]] == [status for status, _, _ in direct[1:]] == [404, 403, 403]
    assert [activity['status'] for activity in stored['runs'][0]['activities']] == ['done', 'failed', 'refused', 'refused']


def test_an_administrators_pause_holds_against_the_owner(eva):
    app, script = eva
    editor, admin = client(app, 'editor'), client(app)
    task = editor('POST', '/api/_eva/tasks', {'name': 'Weekly', 'instructions': 'Summarise projects.', 'cron': '0 9 * * 1', 'timezone': 'Asia/Dubai'})[1]
    key = f"schedule.eva_task_{task['id']}.active"
    assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': key, 'value': False}]})[0] == 200
    paused = editor('GET', f"/api/_eva/tasks/{task['id']}")[1]
    assert paused['active'] is False and paused['paused_by_admin'] is True and paused['next_run_at'] is None
    # The owner can't turn it back on, run it now, or re-enable it by changing the timing.
    assert editor('PATCH', f"/api/_eva/tasks/{task['id']}", {'active': True})[0] == 409
    assert editor('POST', f"/api/_eva/tasks/{task['id']}/run", {})[0] == 409
    assert editor('PATCH', f"/api/_eva/tasks/{task['id']}", {'cron': '0 10 * * 1'})[0] == 200
    with app.database._connection() as connection:
        assert connection.execute('SELECT enabled FROM evlet_schedules WHERE name=?', (f"eva_task_{task['id']}",)).fetchone()[0] == 0
    # A job queued before the pause doesn't run either.
    from evlet.auth import User
    with app.database.transaction() as tx:
        row = tx.connection.execute("SELECT id, username, role FROM evlet_users WHERE username='editor'").fetchone()
        app.business.jobs.enqueue(tx, 'eva_task', {'task': task['id'], 'manual': True}, user=User(*row))
    assert app.business.jobs.run_one()
    with app.database._connection() as connection:
        assert connection.execute('SELECT count(*) FROM evlet_eva_task_runs').fetchone()[0] == 0
    assert script.requests == []
    # Resuming in Controls gives the owner the task back.
    assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': key, 'value': None}]})[0] == 200
    assert editor('PATCH', f"/api/_eva/tasks/{task['id']}", {'active': True})[1]['active'] is True


def test_an_aed_allowance_needs_a_rate_for_the_model(eva):
    app, script = eva
    admin, editor = client(app), client(app, 'editor')
    setting(app, 'eva.model', 'Fast')
    assert admin('GET', '/api/_controls/eva')[1]['missing_rates'] == ['Fast']
    # Without an AED limit, a model with no rate still works; its cost shows as zero.
    conversation, _, stored = ask(app, editor, 'Hello')
    assert stored['runs'][0]['status'] == 'completed'
    setting(app, 'eva.user_limit', {'amount': '25.00', 'unit': 'aed', 'period': 'week'})
    refused = editor('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Again'})
    assert refused[0] == 409 and 'no rate' in refused[1]['error']
    setting(app, 'eva.rates', {'Standard': {'input': '7.35', 'cached': '1.84', 'output': '29.38'}, 'Fast': {'input': '1.00', 'cached': '0.25', 'output': '4.00'}})
    assert admin('GET', '/api/_controls/eva')[1]['missing_rates'] == []
    assert ask(app, editor, 'Again', conversation)[2]['runs'][-1]['status'] == 'completed'


def test_runs_nobody_is_working_on_never_block_a_chat(eva):
    app, script = eva
    editor = client(app, 'editor')
    conversation, run, _ = ask(app, editor, 'First')
    import time
    with app.database.transaction() as tx:
        # A run another process started and then stopped: still "running", silent for six minutes.
        stuck = tx.connection.execute("INSERT INTO evlet_eva_runs(conversation_id,user_id,origin,model,status,started_at,updated_at,process) "
                                      "VALUES (?,2,'eva','Standard','running',?,?,'gone')", (conversation, int(time.time()) - 400, int(time.time()) - 360)).lastrowid
    _, _, stored = ask(app, editor, 'Second', conversation)
    statuses = {item['id']: item['status'] for item in stored['runs']}
    assert statuses[stuck] == 'failed' and stored['runs'][-1]['status'] == 'completed'
    # Stopping a run whose thread is gone closes it at once instead of leaving a flag nobody reads.
    with app.database.transaction() as tx:
        orphan = tx.connection.execute("INSERT INTO evlet_eva_runs(conversation_id,user_id,origin,model,status,started_at,updated_at,process) "
                                       "VALUES (?,2,'eva','Standard','running',?,?,?)", (conversation, int(time.time()), int(time.time()), app.business.eva.process)).lastrowid
    assert editor('POST', f'/api/_eva/runs/{orphan}/cancel', {})[1]['status'] == 'cancelled'
    assert editor('DELETE', f'/api/_eva/conversations/{conversation}')[0] == 204


def test_read_only_tasks_get_no_writing_tools_and_the_provider_address_stays_safe(eva):
    from evlet.eva import safe_address
    from evlet.tools import BY_NAME
    app, _ = eva
    assert app.business.tools.definition('create_file').kind == 'write' and 'create_file' not in BY_NAME
    assert safe_address('https://api.openai.com/v1') and safe_address('http://127.0.0.1:9000/v1')
    for address in ('http://api.example.test/v1', 'https://user:secret@example.test/v1', 'ftp://example.test', 'https://example.test/v1?x=1'):
        assert not safe_address(address), address
