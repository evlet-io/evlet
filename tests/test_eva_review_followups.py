"""Review regressions: real SQLite/ASGI, bounded scripted provider work, no external requests."""
from dataclasses import replace
import json
import threading
import time
from types import SimpleNamespace

import pytest

from evlet import qb
from tests.test_eva import Script, NEVER, ask, call, eva, hashed, make, setting
from tests.test_business import client
from tests.test_assistants import connect, rpc, tool


def state(app, run):
    with app.database._connection() as connection:
        return dict(connection.run(qb.select('evlet_eva_runs').where(id=run)).one())


def send(person, text='Work'):
    conversation = person('POST', '/api/_eva/conversations', {})[1]['id']
    return conversation, person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': text})


def test_batched_tools_stop_at_the_exact_limit_and_leave_valid_history(eva):
    app, script = eva
    setting(app, 'eva.max_tools', 1)
    script.turns = [[('tool_call', {'id': f'call-{i}', 'name': 'list_records', 'arguments': '{"model":"project"}'}) for i in range(3)]]
    person = client(app, 'editor')
    conversation, run, stored = ask(app, person, 'Read projects')
    assert state(app, run)['tool_calls'] == 1
    assert len(stored['runs'][0]['activities']) == 1
    ask(app, person, 'Continue', conversation)
    messages = script.requests[-1]['messages']
    replies = [item for item in messages if item['role'] == 'tool']
    assert {item['tool_call_id'] for item in replies} == {'call-0', 'call-1', 'call-2'}
    assert sum('Not executed' in item['content'] for item in replies) == 2


def test_approval_resume_counts_each_completed_tool_without_resetting_budget(eva):
    app, script = eva
    setting(app, 'eva.max_tools', 1)
    setting(app, 'eva.confirm', {**NEVER, 'create': 'ask'})
    script.turns = [[('tool_call', {'id': f'create-{i}', 'name': 'create_record', 'arguments': json.dumps({'model': 'project', 'values': {'title': f'Budget {i}'}})}) for i in range(3)]]
    person = client(app, 'editor')
    _, run, stored = ask(app, person, 'Create')
    approval = stored['runs'][0]['activities'][0]['approval']['id']
    assert person('POST', f'/api/_tools/approvals/{approval}', {'approve': True})[0] == 200
    app.business.eva.wait(run)
    assert state(app, run)['tool_calls'] == 1
    assert person('GET', '/api/project?q=Budget')[1]['total'] == 1


@pytest.mark.parametrize('permission', ['allow_eva', 'active'])
def test_resumed_batch_rechecks_account_access_before_each_tool(eva, monkeypatch, permission):
    app, script = eva
    setting(app, 'eva.confirm', {**NEVER, 'create': 'ask'})
    script.turns = [[call('create_record', model='project', values={'title': 'Approved first'}),
                     call('list_records', model='project')]]
    person = client(app, 'editor')
    _, run, stored = ask(app, person, 'Create then read')
    approval = stored['runs'][0]['activities'][0]['approval']['id']
    execute, completed = app.business.tools.execute, []

    def revoke_after_first(user, name, *args, **kwargs):
        result = execute(user, name, *args, **kwargs)
        completed.append(name)
        if len(completed) == 1:
            with app.database.transaction() as tx:
                tx.connection.run(qb.update('evlet_users', **{permission: 0}).where(id=user.id))
        return result

    monkeypatch.setattr(app.business.tools, 'execute', revoke_after_first)
    assert person('POST', f'/api/_tools/approvals/{approval}', {'approve': True})[0] == 200
    app.business.eva.wait(run)
    saved = state(app, run)
    assert completed == ['create_record']
    assert saved['status'] == 'failed' and saved['tool_calls'] == 1
    with app.database._connection() as connection:
        assert connection.run(qb.select('project', qb.count()).where(title='Approved first')).value() == 1


@pytest.mark.parametrize('name,arguments,disabled,expected', [
    ('list_records', {'model': 'project', 'unexpected': 'x'}, False, 422),
    ('list_records', {'model': 'project'}, True, 403),
    ('list_records', [], False, 422),
    ('get_record', {'model': 'x' * 5000, 'id': 2**100}, False, 422),
    ('not_a_tool', {}, False, 404),
])
def test_rejected_mcp_calls_obey_admission_before_logging(eva, name, arguments, disabled, expected):
    app, _ = eva
    _, tokens, _ = connect(app)
    setting(app, 'mcp.rate', 1)
    if disabled:
        setting(app, 'mcp.disabled_tools', ['list_records'])
    first = rpc(app, tokens['access_token'], 'tools/call', {'name': name, 'arguments': arguments})[1]
    if expected == 404:
        assert first['error']['code'] == -32602
    else:
        assert first['result']['isError'] and first['result']['structuredContent']['status'] == expected
    for _ in range(3):
        refused = tool(app, tokens['access_token'], name, arguments)
        assert refused['isError'] and refused['structuredContent']['status'] == 429
    with app.database._connection() as connection:
        assert connection.run(qb.select('evlet_tool_calls', qb.count()).where(origin='mcp')).value() == 1


def test_large_rejected_inputs_and_historical_broken_json_do_not_hide_the_log(eva):
    app, _ = eva
    person = client(app, 'editor')
    assert person('POST', '/api/_tools/execute', {'tool': 'list_records', 'arguments': {'model': 'project', 'unexpected': 'x' * 5000}})[0] == 422
    with app.database.transaction() as tx:
        row = tx.connection.run(qb.select('evlet_tool_calls', 'id', 'inputs').order_by('-id').limit(1)).one()
        assert json.loads(row['inputs'])['truncated'] is True
        assert len(row['inputs'].encode()) <= 4000
        tx.connection.run(qb.update('evlet_tool_calls', inputs='{"cut":"unterminated').where(id=row['id']))
    status, page, _ = client(app)('GET', '/api/_controls/tools')
    assert status == 200 and page['items'][0]['inputs']['truncated'] is True


@pytest.mark.parametrize('legacy', [False, True])
def test_read_content_approval_survives_tool_log_retention(eva, legacy):
    app, script = eva
    setting(app, 'eva.confirm', NEVER)
    person = client(app, 'editor')
    script.turns = [[call('list_records', model='project')], [('text', 'Read.')]]
    conversation, run, _ = ask(app, person, 'Read')
    with app.database.transaction() as tx:
        tx.connection.run(qb.update('evlet_tool_calls', at=int(time.time()) - 91 * 86400))
        if legacy:
            tx.connection.run(qb.delete('evlet_eva_events').where(type='content.read'))
    app.business.tools.pruned = 0
    app.business.tools.prune()
    assert app.business.tools.read_content(run)
    script.turns = [[call('create_record', model='project', values={'title': 'Needs approval'})]]
    _, _, stored = ask(app, person, 'Create', conversation)
    assert stored['runs'][-1]['status'] == 'awaiting_approval'
    assert stored['runs'][-1]['activities'][0]['approval']['summary']['after_reading'] is True
    assert person('GET', '/api/project?q=Needs')[1]['total'] == 0


class Blocked:
    def __init__(self):
        self.entered, self.release, self.closed = threading.Event(), threading.Event(), threading.Event()

    def stream(self, request):
        self.entered.set()
        try:
            assert self.release.wait(10)
            yield 'text', 'Should not overwrite cancellation.'
        finally:
            self.closed.set()


def test_cancelled_underlying_worker_holds_capacity_until_it_really_exits(tmp_path, monkeypatch, hashed):
    provider = Blocked()
    app = make(tmp_path, monkeypatch, hashed, provider)
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1, max_runs_per_user=1)
    person = client(app, 'editor')
    _, response = send(person)
    run = response[1]['run']['id']
    try:
        assert response[0] == 202 and provider.entered.wait(3)
        assert person('POST', f'/api/_eva/runs/{run}/cancel', {})[1]['status'] == 'cancelled'
        assert state(app, run)['status'] == 'cancelled'
        assert send(person)[1][0] == 429
        # Another Eva object models admission from a second serving process against the same DB.
        from evlet.eva import Eva
        other = Eva(app.business, worker.ai)
        with pytest.raises(Exception) as refused:
            with app.database.transaction() as tx:
                other.admit_run(tx.connection, 2)
        assert refused.value.status == 429
    finally:
        provider.release.set()
        worker.wait(run)
    assert provider.closed.is_set()
    assert state(app, run)['status'] == 'cancelled' and state(app, run)['process'] is None
    with app.database._connection() as connection:
        assert connection.run(qb.select('evlet_eva_events', qb.count()).where(run_id=run, type='run.completed')).value() == 0
    assert send(person)[1][0] == 202
    with worker.lock:
        pending = list(worker.threads)
    for item in pending:
        worker.wait(item)


def test_same_second_chat_pages_include_every_conversation_once(eva):
    app, _ = eva
    person = client(app, 'editor')
    ids = {person('POST', '/api/_eva/conversations', {'title': f'Chat {i}'})[1]['id'] for i in range(53)}
    with app.database.transaction() as tx:
        tx.connection.run(qb.update('evlet_eva_conversations', updated_at=1234))
    first = person('GET', '/api/_eva/conversations')[1]
    second = person('GET', '/api/_eva/conversations?before=' + first['next'])[1]
    assert len(first['items']) == 50 and len(second['items']) == 3 and second['next'] is None
    assert {row['id'] for row in first['items'] + second['items']} == ids
    assert person('GET', '/api/_eva/conversations?before=invalid')[0] == 422


def test_resume_and_scheduled_runs_share_capacity_without_consuming_an_unstarted_approval(eva):
    app, script = eva
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1, max_runs_per_user=1)
    setting(app, 'eva.confirm', {**NEVER, 'create': 'ask'})
    person = client(app, 'editor')
    script.turns = [[call('create_record', model='project', values={'title': 'After capacity'})]]
    _, waiting, stored = ask(app, person, 'Create')
    approval = stored['runs'][0]['activities'][0]['approval']['id']
    entered, release = threading.Event(), threading.Event()
    def block():
        entered.set()
        assert release.wait(10)
    script.before[2] = block
    _, response = send(person)
    busy = response[1]['run']['id']
    try:
        assert entered.wait(3)
        assert person('POST', f'/api/_tools/approvals/{approval}', {'approve': True})[0] == 429
        with app.database._connection() as connection:
            assert connection.run(qb.select('evlet_tool_approvals', 'status').where(id=approval)).value() == 'pending'
        task = person('POST', '/api/_eva/tasks', {'name': 'Busy task', 'instructions': 'Read projects', 'cron': '0 9 * * 1', 'timezone': 'Asia/Dubai'})[1]
        assert person('POST', f"/api/_eva/tasks/{task['id']}/run", {})[0] == 202
        assert app.business.jobs.run_one()
        with app.database._connection() as connection:
            row = connection.run(qb.select('evlet_eva_task_runs').where(task_id=task['id'])).one()
            assert row['status'] == 'skipped' and 'busy' in row['error']
        assert len(script.requests) == 2
    finally:
        release.set()
        worker.wait(busy)
    assert person('POST', f'/api/_tools/approvals/{approval}', {'approve': True})[0] == 200
    worker.wait(waiting)
    assert person('GET', '/api/project?q=After')[1]['total'] == 1


def test_one_person_cannot_take_the_other_persons_capacity(tmp_path, monkeypatch, hashed):
    provider = Blocked()
    app = make(tmp_path, monkeypatch, hashed, provider)
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=2, max_runs_per_user=1)
    editor, admin = client(app, 'editor'), client(app)
    runs = []
    try:
        first = send(editor)[1]
        assert first[0] == 202 and provider.entered.wait(3)
        runs.append(first[1]['run']['id'])
        assert send(editor)[1][0] == 429
        second = send(admin)[1]
        assert second[0] == 202
        runs.append(second[1]['run']['id'])
        assert send(client(app, 'viewer'))[1][0] == 429
        assert editor('GET', '/api/project')[0] == 200
    finally:
        provider.release.set()
        for run in runs:
            worker.wait(run)


def test_stream_deadline_closes_provider_and_does_not_execute_late_calls(tmp_path, monkeypatch, hashed):
    import evlet.eva as module
    now, closed = [0.0], []
    class SlowStream:
        def stream(self, request):
            try:
                yield 'text', 'Started'
                now[0] = 11.0
                yield call('create_record', model='project', values={'title': 'Too late'})
            finally:
                closed.append(True)
    monkeypatch.setattr(module, 'time', SimpleNamespace(time=time.time, sleep=time.sleep, monotonic=lambda: now[0]))
    app = make(tmp_path, monkeypatch, hashed, SlowStream())
    setting(app, 'eva.max_seconds', 10)
    person = client(app, 'editor')
    _, run, stored = ask(app, person, 'Work')
    assert closed == [True] and state(app, run)['process'] is None
    assert 'time limit' in stored['messages'][-1]['content']
    assert person('GET', '/api/project?q=Too')[1]['total'] == 0


def test_malformed_remote_envelopes_are_bounded_without_tool_log_growth(eva, monkeypatch):
    import evlet.assistants as module
    from tests.test_assistants import send as http_send
    app, _ = eva
    _, tokens, _ = connect(app)
    monkeypatch.setattr(module, 'LIGHT_RATE', 2)
    headers = {b'content-type': b'application/json', b'authorization': f"Bearer {tokens['access_token']}".encode()}
    assert http_send(app, 'POST', '/_/mcp', b'{bad', headers)[0] == 400
    assert rpc(app, tokens['access_token'], 'tools/call', {'name': []})[1]['error']['code'] == -32602
    assert http_send(app, 'POST', '/_/mcp', b'{bad', headers)[0] == 429
    with app.database._connection() as connection:
        assert connection.run(qb.select('evlet_tool_calls', qb.count())).value() == 0


def test_cancellation_cannot_be_overwritten_by_a_late_approval_pause(eva, monkeypatch):
    from evlet.tools import ApprovalNeeded
    app, script = eva
    setting(app, 'eva.confirm', {**NEVER, 'create': 'ask'})
    script.turns = [[call('create_record', model='project', values={'title': 'Cancelled approval'})]]
    entered, release = threading.Event(), threading.Event()
    execute = app.business.tools.execute
    def delayed(*args, **kwargs):
        try:
            return execute(*args, **kwargs)
        except ApprovalNeeded:
            entered.set()
            assert release.wait(10)
            raise
    monkeypatch.setattr(app.business.tools, 'execute', delayed)
    person = client(app, 'editor')
    _, response = send(person)
    run = response[1]['run']['id']
    try:
        assert entered.wait(3)
        assert person('POST', f'/api/_eva/runs/{run}/cancel', {})[1]['status'] == 'cancelled'
    finally:
        release.set()
        app.business.eva.wait(run)
    assert state(app, run)['status'] == 'cancelled'
    with app.database._connection() as connection:
        assert connection.run(qb.select('evlet_tool_approvals', 'status').where(run_id=run)).value() == 'declined'
    assert person('GET', '/api/project?q=Cancelled')[1]['total'] == 0


def test_thread_start_failure_releases_the_reserved_slot(eva, monkeypatch):
    app, _ = eva
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1, max_runs_per_user=1)
    person = client(app, 'editor')
    original = threading.Thread.start
    def fail(thread):
        if thread.name.startswith('eva-run-'):
            raise RuntimeError('No thread available')
        return original(thread)
    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, 'start', fail)
        assert send(person)[1][0] == 500
    with app.database._connection() as connection:
        row = connection.run(qb.select('evlet_eva_runs').order_by('-id').limit(1)).one()
        assert row['status'] == 'failed' and row['process'] is None
    next_run = send(person)[1]
    assert next_run[0] == 202
    worker.wait(next_run[1]['run']['id'])


def test_openai_checks_cancellation_even_between_tool_argument_fragments():
    from evlet.eva import OpenAI, RunStopped
    closed = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): closed.append(True)
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{"}}]}}]}\n'
            yield b'data: {"choices":[]}\n'
    provider = OpenAI('http://127.0.0.1:1', 'test-unused')
    provider.opener = SimpleNamespace(open=lambda *args, **kwargs: Response())
    calls = []
    def checkpoint():
        calls.append(True)
        if len(calls) == 2:
            raise RunStopped()
    with pytest.raises(RunStopped):
        list(provider.stream({'model': 'test', 'messages': [], 'max_output_tokens': 256}, checkpoint=checkpoint))
    assert closed == [True] and len(calls) == 2
