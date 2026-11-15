"""Opt-in Eva capacity, live cost holds and Stop with a live worker: real SQLite, local providers only."""
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from evlet import qb
from tests.test_business import client, hashed  # noqa: F401
from tests.test_eva import make, setting, Script, NEVER, ask, call
from evlet.eva import QuotaError


class Blocked:
    def __init__(self):
        self.entered = threading.Event(); self.release = threading.Event(); self.closed = threading.Event()

    def stream(self, request):
        self.entered.set()
        try:
            assert self.release.wait(40)
            yield 'text', 'Done'
        finally:
            self.closed.set()


@pytest.fixture
def world(tmp_path, monkeypatch, hashed):
    provider = Blocked(); app = make(tmp_path, monkeypatch, hashed, provider)
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1, max_runs_per_user=1)
    person = client(app, 'editor')
    conversation = person('POST', '/api/_eva/conversations', {})[1]['id']

    def begin():
        response = person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Wait'})
        assert response[0] == 202, response
        run = response[1]['run']['id']; assert provider.entered.wait(5)
        return run
    yield app, worker, person, provider, begin
    provider.release.set()
    with worker.lock:
        runs = list(worker.threads)
    for run in runs:
        worker.wait(run, 10)


def row(app, run):
    with app.database._connection() as c:
        return dict(c.run(qb.select('evlet_eva_runs').where(id=run)).one())


def admit(person, text='Hello'):
    status, conversation, _ = person('POST', '/api/_eva/conversations', {})
    assert status == 201
    response = person('POST', f"/api/_eva/conversations/{conversation['id']}/messages", {'text': text})
    assert response[0] == 202, response
    return response[1]['run']['id']


def test_expired_cost_hold_stays_held_until_underlying_provider_exits(world):
    app, worker, person, provider, begin = world
    setting(app, 'eva.user_limit', {'amount': 1, 'unit': 'requests', 'period': 'day'})
    app.business.controls.refresh()
    run = begin(); owner = row(app, run)['user_id']
    limit = {'amount': 1, 'unit': 'requests', 'period': 'day'}
    with app.database.transaction(store='eva') as tx:
        before = worker.used(tx.connection, 'person', limit, owner, int(time.time()))
        tx.connection.run(qb.update('evlet_eva_reservations', expires_at=0).where(user_id=owner))
        after = worker.used(tx.connection, 'person', limit, owner, int(time.time()))
    assert before[1] == 1 and after[1] == 1
    with pytest.raises(QuotaError):
        worker.reserve(owner, 'eva', check_only=True, model='Standard')
    assert person('POST', f'/api/_eva/runs/{run}/cancel', {})[1]['status'] == 'stopping'
    with pytest.raises(QuotaError):
        worker.reserve(owner, 'eva', check_only=True, model='Standard')
    provider.release.set(); worker.wait(run)
    assert row(app, run)['status'] == 'cancelled'
    with app.database._connection() as connection:
        spent, held, _ = worker.used(connection, 'person', limit, owner, int(time.time()))
    assert spent == 1 and held == 0  # A cancelled request was sent; missing provider usage is estimated.


def test_running_cancel_answers_while_unrelated_main_writer_is_held(world):
    app, worker, person, provider, begin = world; run = begin()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with app.database.transaction() as tx:
            tx.connection.run(qb.update('evlet_users', active=1).where(username='editor'))
            result = pool.submit(person, 'POST', f'/api/_eva/runs/{run}/cancel', {}).result(timeout=3)
            assert row(app, run)['cancel'] == 1
    assert result[0] == 200 and result[1]['status'] == 'stopping'
    provider.release.set(); worker.wait(run)
    assert row(app, run)['status'] == 'cancelled'


def test_cancelled_approval_cannot_resume_or_authorize_a_later_run(tmp_path, monkeypatch, hashed):
    script = Script()
    app = make(tmp_path, monkeypatch, hashed, script)
    setting(app, 'eva.confirm', {**NEVER, 'create': 'ask'})
    script.turns = [[call('create_record', model='project', values={'title': 'Needs a new approval'})]]
    person = client(app, 'editor')
    _, run, stored = ask(app, person, 'Create')
    approval = stored['runs'][0]['activities'][0]['approval']['id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        with app.database.transaction() as tx:
            tx.connection.run(qb.update('evlet_users', active=1).where(username='editor'))
            result = pool.submit(person, 'POST', f'/api/_eva/runs/{run}/cancel', {}).result(timeout=3)
            assert result[0] == 200 and row(app, run)['status'] == 'cancelled'
    assert person('POST', f'/api/_tools/approvals/{approval}', {'approve': True})[0] == 409
    assert person('GET', '/api/_eva')[1]['pending_approvals'] == 0
    assert person('GET', '/api/_tools/approvals')[1]['items'][0]['status'] == 'declined'
    # Even a stale formerly-approved row cannot transfer consent to another run.
    with app.database.transaction() as tx:
        tx.connection.run(qb.update('evlet_tool_approvals', status='approved').where(id=approval))
    script.turns = [[call('create_record', model='project', values={'title': 'Needs a new approval'})]]
    _, _, stored = ask(app, person, 'Create again')
    assert stored['runs'][0]['status'] == 'awaiting_approval'
    assert stored['runs'][0]['activities'][0]['approval']['id'] != approval
    assert person('GET', '/api/project')[1]['total'] == 0


def test_silent_run_without_a_worker_is_reclaimed_and_a_late_execution_does_no_work(tmp_path, monkeypatch, hashed):
    script = Script()
    app = make(tmp_path, monkeypatch, hashed, script)
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1, max_runs_per_user=1)
    person = client(app, 'editor')
    with monkeypatch.context() as patch:
        patch.setattr(worker, 'spawn', lambda run: None)
        run = admit(person, 'Delayed')
        assert person('POST', '/api/_eva/conversations', {})[0] == 201
        with app.database.transaction() as tx:
            assert not worker.admit(tx.connection, row(app, run)['user_id'])
    with app.database.transaction(store='eva') as tx:
        tx.connection.run(qb.update('evlet_eva_runs', updated_at=int(time.time()) - 601).where(id=run))
    with app.database.transaction(store='eva') as tx:
        assert worker.admit(tx.connection, row(app, run)['user_id'])
    assert row(app, run)['status'] == 'failed'
    worker.execute(run)
    assert script.requests == []


def test_existing_reservation_schema_upgrades_without_losing_unexpired_holds(tmp_path, monkeypatch, hashed):
    app = make(tmp_path, monkeypatch, hashed, Script())
    worker = app.business.eva
    hold = worker.reserve(2, 'eva', tokens=10)
    # Construct the old side-file layout; schema retirement helpers target application tables in main.
    with sqlite3.connect(app.database.files()[3]) as connection:
        connection.execute('ALTER TABLE evlet_eva_reservations DROP COLUMN run_id')
    worker.initialize()
    with app.database._connection() as connection:
        saved = connection.run(qb.select('evlet_eva_reservations').where(id=hold)).one()
        assert saved['run_id'] is None and saved['tokens'] == 10 and saved['requests'] == 1
        _, held, _ = worker.used(connection, 'person', {'amount': 1, 'unit': 'requests', 'period': 'day'}, 2, int(time.time()))
        assert held == 1


def test_cancel_before_thread_start_frees_the_place_and_blocks_delayed_execution(tmp_path, monkeypatch, hashed):
    script = Script()
    app = make(tmp_path, monkeypatch, hashed, script)
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1, max_runs_per_user=1)
    person = client(app, 'editor')
    conversation = person('POST', '/api/_eva/conversations', {})[1]['id']
    with monkeypatch.context() as patch:
        patch.setattr(worker, 'spawn', lambda run: None)
        result = person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Not started'})
    run = result[1]['run']['id']
    # No worker reads the flag here, so Stop closes the run at once.
    assert person('POST', f'/api/_eva/runs/{run}/cancel', {})[1]['status'] == 'cancelled'
    assert row(app, run)['status'] == 'cancelled'
    worker.execute(run)
    assert script.requests == []
    next_run = person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Next'})
    assert next_run[0] == 202
    worker.wait(next_run[1]['run']['id'])
    assert len(script.requests) == 1


def test_run_keeps_its_place_while_the_provider_stream_closes(tmp_path, monkeypatch, hashed):
    closing_now, finish_close = threading.Event(), threading.Event()

    class Stream:
        def __init__(self): self.items = iter([('text', 'Quick answer')])
        def __iter__(self): return self
        def __next__(self): return next(self.items)
        def close(self):
            closing_now.set()
            assert finish_close.wait(10)

    class Provider:
        def stream(self, request): return Stream()
    app = make(tmp_path, monkeypatch, hashed, Provider())
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1)
    person = client(app, 'editor')
    run = admit(person)
    try:
        assert closing_now.wait(5)
        assert row(app, run)['status'] == 'running'
        assert person('POST', '/api/_eva/conversations', {})[0] == 201
        conversation = person('GET', '/api/_eva/conversations')[1]['items'][0]['id']
        assert person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Second'})[0] == 429
    finally:
        finish_close.set()
        worker.wait(run, 10)
    assert row(app, run)['status'] == 'completed'
    assert person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Second'})[0] == 202
    with worker.lock:
        pending = list(worker.threads)
    for item in pending:
        worker.wait(item, 10)


def test_real_loopback_sse_fragment_cancel_closes_response(tmp_path, monkeypatch, hashed):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from evlet.eva import OpenAI
    fragment_sent, continue_stream = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            try:
                self.wfile.write(b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"pending","function":{"name":"create_record","arguments":"{"}}]}}]}\n\n')
                self.wfile.flush()
                fragment_sent.set()
                assert continue_stream.wait(10)
                self.wfile.write(b'data: {"choices":[]}\n\ndata: [DONE]\n\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    provider = OpenAI(f'http://127.0.0.1:{server.server_port}', 'unused-local-test-key', timeout=5)
    opened = []
    original = provider.opener.open

    def open_response(*args, **kwargs):
        response = original(*args, **kwargs)
        opened.append(response)
        return response
    monkeypatch.setattr(provider.opener, 'open', open_response)
    app = make(tmp_path, monkeypatch, hashed, provider)
    worker = app.business.eva
    person = client(app, 'editor')
    run = admit(person)
    try:
        assert fragment_sent.wait(5)
        assert person('POST', f'/api/_eva/runs/{run}/cancel', {})[1]['status'] == 'stopping'
        continue_stream.set()
        worker.wait(run, 10)
        assert not worker.alive(run)
        assert row(app, run)['status'] == 'cancelled'
        assert opened and all(response.closed for response in opened)
        assert person('GET', '/api/project')[1]['total'] == 0
    finally:
        continue_stream.set()
        worker.wait(run, 10)
        server.shutdown()
        server.server_close()
        serving.join(5)
