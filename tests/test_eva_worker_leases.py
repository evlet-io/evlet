"""OS execution leases and live cost holds survive blocked SQLite writers; local providers only."""
import json
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
from dataclasses import replace
from pathlib import Path

import pytest
from evlet import qb
from tests.test_business import client, hashed
from tests.test_eva import make, setting, Script, NEVER, ask, call
from evlet.eva import QuotaError

class Blocked:
    def __init__(self):
        self.entered=threading.Event(); self.release=threading.Event(); self.closed=threading.Event()
    def stream(self, request):
        self.entered.set()
        try:
            assert self.release.wait(40)
            yield 'text','Done'
        finally: self.closed.set()

@pytest.fixture
def world(tmp_path, monkeypatch, hashed):
    provider=Blocked(); app=make(tmp_path,monkeypatch,hashed,provider)
    worker=app.business.eva
    worker.ai=replace(worker.ai,max_concurrent_runs=1,max_runs_per_user=1)
    person=client(app,'editor')
    conversation=person('POST','/api/_eva/conversations',{})[1]['id']
    def begin():
        response=person('POST',f'/api/_eva/conversations/{conversation}/messages',{'text':'Wait'})
        assert response[0]==202,response
        run=response[1]['run']['id']; assert provider.entered.wait(5)
        return run
    yield app,worker,person,provider,begin
    provider.release.set()
    with worker.lock: runs=list(worker.threads)
    for run in runs: worker.wait(run,10)


def row(app,run):
    with app.database._connection() as c: return dict(c.run(qb.select('evlet_eva_runs').where(id=run)).one())


def test_foreign_process_cannot_reclaim_a_live_worker_after_sqlite_heartbeat_stalls(world):
    app,worker,person,provider,begin=world; run=begin()
    # Model the persisted state after a writer has blocked heartbeat updates beyond STALE_SECONDS.
    with app.database.transaction(store='eva') as tx:
        tx.connection.run(qb.update('evlet_eva_runs',updated_at=int(time.time())-601).where(id=run))
    code='''
import json,sys,threading
from types import SimpleNamespace
from evlet import Database,qb
from evlet.eva import Eva
from examples.playground.models import registry
worker=object.__new__(Eva); worker.database=Database(sys.argv[1],registry)
worker.threads={}; worker.lock=threading.Lock(); worker.ai=SimpleNamespace(max_concurrent_runs=1,max_runs_per_user=1)
print('READY',flush=True)
with worker.database.transaction(store='eva') as tx:
 try:
  worker.admit_run(tx.connection,2)
  status=200
 except Exception as exc:
  status=getattr(exc,'status',500)
 result=dict(tx.connection.run(qb.select('evlet_eva_runs').where(id=int(sys.argv[2]))).one())
 print(json.dumps({'admission':status,'run':result}),flush=True)
'''
    with sqlite3.connect(app.database.files()[3]) as blocking:
        blocking.execute('BEGIN IMMEDIATE')
        process=subprocess.Popen([sys.executable,'-c',code,str(app.database.path),str(run)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=Path(__file__).resolve().parents[1])
        try:
            assert process.stdout.readline().strip()=='READY'
            assert process.poll() is None
            blocking.commit()
            output,error=process.communicate(timeout=10)
        finally:
            if process.poll() is None: process.kill(); process.wait()
    assert process.returncode==0,error
    result=json.loads(output)
    assert worker.alive(run) and not provider.closed.is_set()
    assert result['admission']==429
    assert result['run']['status']=='running' and result['run']['process'] is not None


def test_expired_cost_hold_stays_held_until_underlying_provider_exits(world):
    app,worker,person,provider,begin=world
    setting(app,'eva.user_limit',{'amount':1,'unit':'requests','period':'day'})
    app.business.controls.refresh()
    run=begin(); owner=row(app,run)['user_id']
    limit={'amount':1,'unit':'requests','period':'day'}
    with app.database.transaction(store='eva') as tx:
        before=worker.used(tx.connection,'person',limit,owner,int(time.time()))
        tx.connection.run(qb.update('evlet_eva_reservations',expires_at=0).where(user_id=owner))
        after=worker.used(tx.connection,'person',limit,owner,int(time.time()))
    assert before[1]==1 and after[1]==1
    assert worker.alive(run) and row(app,run)['process'] is not None
    with pytest.raises(QuotaError):
        worker.reserve(owner,'eva',check_only=True,model='Standard')
    assert person('POST',f'/api/_eva/runs/{run}/cancel',{})[1]['status']=='cancelled'
    with pytest.raises(QuotaError):
        worker.reserve(owner,'eva',check_only=True,model='Standard')
    provider.release.set(); worker.wait(run)
    with app.database._connection() as connection:
        spent, held, _ = worker.used(connection, 'person', limit, owner, int(time.time()))
    assert spent == 1 and held == 0  # A cancelled request was sent; missing provider usage is estimated.


def test_running_cancel_finishes_while_unrelated_main_writer_is_held(world):
    app,worker,person,provider,begin=world; run=begin()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with app.database.transaction() as tx:
            tx.connection.run(qb.update('evlet_users',active=1).where(username='editor'))
            began=time.monotonic()
            cancel=pool.submit(person,'POST',f'/api/_eva/runs/{run}/cancel',{})
            result=cancel.result(timeout=3)
            assert row(app,run)['status']=='cancelled'
    assert result[0]==200 and row(app,run)['status']=='cancelled'
    assert worker.alive(run)


def test_cancelled_approval_cannot_resume_or_authorize_a_later_run(tmp_path, monkeypatch, hashed):
    script = Script()
    app = make(tmp_path, monkeypatch, hashed, script)
    worker = app.business.eva
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


def test_crashed_lease_is_reclaimed_and_delayed_old_execution_does_no_work(tmp_path, monkeypatch, hashed):
    script = Script()
    app = make(tmp_path, monkeypatch, hashed, script)
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1, max_runs_per_user=1)
    person = client(app, 'editor')
    conversation = person('POST', '/api/_eva/conversations', {})[1]['id']
    with monkeypatch.context() as patch:
        patch.setattr(worker, 'spawn', lambda run: None)
        result = person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Delayed'})
    run = result[1]['run']['id']
    owner = row(app, run)['process']
    code = '''
import sys,time
from evlet import Database
from examples.playground.models import registry
database=Database(sys.argv[1],registry)
with database.worker_lease(sys.argv[2]) as acquired:
 assert acquired
 print('LOCKED',flush=True)
 time.sleep(30)
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(app.database.path), worker.lease_key(owner)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'LOCKED'
        with app.database.transaction(store='eva') as tx:
            tx.connection.run(qb.update('evlet_eva_runs', updated_at=int(time.time()) - 601).where(id=run))
        with pytest.raises(Exception) as busy:
            with app.database.transaction(store='eva') as tx:
                worker.admit_run(tx.connection, row(app, run)['user_id'])
        assert busy.value.status == 429
    finally:
        process.kill()
        process.communicate(timeout=5)
    with app.database.transaction(store='eva') as tx:
        worker.admit_run(tx.connection, row(app, run)['user_id'])
    assert row(app, run)['status'] == 'failed' and row(app, run)['process'] is None
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


def test_cancel_before_thread_start_releases_slot_and_blocks_delayed_execution(tmp_path, monkeypatch, hashed):
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
    assert person('POST', f'/api/_eva/runs/{run}/cancel', {})[0] == 200
    assert row(app, run)['status'] == 'cancelled' and row(app, run)['process'] is None
    worker.execute(run)
    assert script.requests == []
    next_run = person('POST', f'/api/_eva/conversations/{conversation}/messages', {'text': 'Next'})
    assert next_run[0] == 202
    worker.wait(next_run[1]['run']['id'])
    assert len(script.requests) == 1



# Independent peer regressions: startup handoff, final cleanup and real local SSE.
def lease_world(tmp_path, monkeypatch, hashed, provider=None):
    app = make(tmp_path, monkeypatch, hashed, provider or Script())
    worker = app.business.eva
    worker.ai = replace(worker.ai, max_concurrent_runs=1, max_runs_per_user=1)
    person = client(app, 'editor')
    return app, worker, person


def admit(person, text='Hello'):
    status, conversation, _ = person('POST', '/api/_eva/conversations', {})
    assert status == 201
    response = person('POST', f"/api/_eva/conversations/{conversation['id']}/messages", {'text': text})
    assert response[0] == 202, response
    return response[1]['run']['id']


def test_provider_close_retains_execution_lease(tmp_path, monkeypatch, hashed):
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
    app, worker, person = lease_world(tmp_path, monkeypatch, hashed, Provider())
    run = admit(person)
    try:
        assert closing_now.wait(5)
        owner = row(app, run)['process']
        with app.database.worker_lease(worker.lease_key(owner)) as acquired:
            assert not acquired
        assert worker.alive(run)
    finally:
        finish_close.set()
        worker.wait(run, 10)
    assert row(app, run)['process'] is None


def test_completed_run_retains_lease_while_final_sqlite_cleanup_waits(tmp_path, monkeypatch, hashed):
    app, worker, person = lease_world(tmp_path, monkeypatch, hashed)
    completed, continue_cleanup = threading.Event(), threading.Event()
    original = worker.finish
    def finish(*args, **kwargs):
        original(*args, **kwargs)
        completed.set()
        assert continue_cleanup.wait(10)
    monkeypatch.setattr(worker, 'finish', finish)
    run = admit(person)
    try:
        assert completed.wait(5)
        saved = row(app, run)
        assert saved['status'] == 'completed' and saved['process'] is not None
        with closing(sqlite3.connect(app.database.files()[3], isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            continue_cleanup.set()
            with app.database.worker_lease(worker.lease_key(saved['process'])) as acquired:
                assert not acquired
            assert worker.alive(run)
            blocker.rollback()
    finally:
        continue_cleanup.set()
        worker.wait(run, 10)
    assert row(app, run)['process'] is None


def test_delayed_reclaimed_worker_cannot_strand_new_owner(tmp_path, monkeypatch, hashed):
    app, worker, person = lease_world(tmp_path, monkeypatch, hashed)
    monkeypatch.setattr(worker, 'spawn', lambda *args: None)
    old = admit(person, 'Delayed old work')
    captured, continue_old, acquired, release_old = [threading.Event() for _ in range(4)]
    original_lease, original_connection = app.database.worker_lease, app.database._connection
    first_read = True
    @contextmanager
    def connection(*args, **kwargs):
        nonlocal first_read
        delay = threading.current_thread().name == 'delayed-old-worker' and first_read
        if delay:
            first_read = False
        with original_connection(*args, **kwargs) as db:
            yield db
        if delay:
            captured.set()
            assert continue_old.wait(10)
    @contextmanager
    def lease(key):
        with original_lease(key) as result:
            if threading.current_thread().name == 'delayed-old-worker':
                acquired.set()
                assert release_old.wait(10)
            yield result
    monkeypatch.setattr(app.database, '_connection', connection)
    monkeypatch.setattr(app.database, 'worker_lease', lease)
    old_thread = threading.Thread(target=worker.execute, args=(old,), name='delayed-old-worker')
    old_thread.start()
    try:
        assert captured.wait(5)
        with app.database.transaction(store='eva') as tx:
            tx.connection.run(qb.update('evlet_eva_runs', updated_at=int(time.time())-601).where(id=old))
            worker.reap(tx.connection)
        assert row(app, old)['status'] == 'failed'
        new = admit(person, 'New owner of same slot')
        continue_old.set()
        # Old implementation grabs the reused lease; fixed implementation exits before acquiring.
        end = time.monotonic() + 5
        while old_thread.is_alive() and not acquired.is_set() and time.monotonic() < end:
            old_thread.join(0.01)
        assert not old_thread.is_alive() or acquired.is_set()
        new_thread = threading.Thread(target=worker.execute, args=(new,), name='new-worker')
        new_thread.start()
        new_thread.join(5)
        release_old.set()
        old_thread.join(5)
        new_thread.join(5)
        assert not old_thread.is_alive() and not new_thread.is_alive()
        saved = row(app, new)
        assert saved['status'] == 'completed' and saved['process'] is None
        assert len(worker.ai.provider.requests) == 1
        assert not acquired.is_set(), 'Obsolete worker acquired a reassigned slot'
    finally:
        continue_old.set()
        release_old.set()
        old_thread.join(10)


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
    app, worker, person = lease_world(tmp_path, monkeypatch, hashed, provider)
    run = admit(person)
    try:
        assert fragment_sent.wait(5)
        assert person('POST', f'/api/_eva/runs/{run}/cancel', {})[1]['status'] == 'cancelled'
        continue_stream.set()
        worker.wait(run, 10)
        assert not worker.alive(run)
        assert row(app, run)['status'] == 'cancelled' and row(app, run)['process'] is None
        assert opened and all(response.closed for response in opened)
        assert person('GET', '/api/project')[1]['total'] == 0
    finally:
        continue_stream.set()
        worker.wait(run, 10)
        server.shutdown()
        server.server_close()
        serving.join(5)
