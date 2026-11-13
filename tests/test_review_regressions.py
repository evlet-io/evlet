"""Regression coverage for the review of the first CLI release."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time

import pytest

from evlet import Application, Database, Integer, Model, Registry, Text, ValidationError, validate
from evlet.auth import AccessError, password_hash
from test_business import PASSWORD, client, hashed, http, secured
from test_jobs import queue, raw, submit


def test_cancelled_claim_returns_attempt_even_for_one_attempt_task(secured):
    from dataclasses import replace
    jobs = queue(secured, lambda tx, context, payload: None)
    jobs.tasks['work'] = replace(jobs.tasks['work'], max_attempts=1)
    receipt = submit(jobs)
    claim = jobs.claim()
    assert not jobs.execute(claim, active=lambda: False)
    assert raw(jobs, receipt.id)['state'] == 'queued'
    assert raw(jobs, receipt.id)['attempts'] == 0
    assert jobs.run_one()
    assert raw(jobs, receipt.id)['state'] == 'succeeded'


def test_busy_database_before_handler_preserves_job_and_attempt(secured, monkeypatch):
    from contextlib import contextmanager
    calls = []
    jobs = queue(secured, lambda tx, context, payload: calls.append(context.attempt))
    receipt = submit(jobs)
    claim = jobs.claim()
    original = jobs.database._connection
    @contextmanager
    def short_wait():
        with original() as connection:
            connection.execute('PRAGMA busy_timeout=100')
            yield connection
    monkeypatch.setattr(jobs.database, '_connection', short_wait)
    locked = threading.Event()
    def writer():
        with original() as connection:
            connection.execute('BEGIN IMMEDIATE')
            locked.set()
            time.sleep(0.17)
            connection.commit()
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocker = pool.submit(writer)
        assert locked.wait(2)
        assert not jobs.execute(claim)
        blocker.result()
    assert calls == []
    state = raw(jobs, receipt.id)
    assert state['state'] == 'queued' and state['attempts'] == 0
    assert jobs.run_one(now=state['run_at'])
    assert calls == [1]


def test_stored_rows_remain_readable_and_repairable_after_validation_changes(tmp_path):
    original = type('Stock', (Model,), {'qty': Integer()})
    path = tmp_path/'stored.sqlite3'
    db = Database(path, Registry(original)); db.initialize()
    item = db.create(original, qty=-5)
    def nonnegative(value):
        if value < 0:
            raise ValueError('Use a nonnegative quantity')
    @validate
    def check(self):
        if self.qty < 0:
            raise ValidationError({'qty':'Use a nonnegative quantity'})
    revised = type('Stock', (Model,), {'qty': Integer(validator=nonnegative), 'check': check})
    db = Database(path, Registry(revised)); db.initialize()
    assert db.get(revised, item.id).qty == -5
    assert db.list(revised)[0].qty == -5
    with pytest.raises(ValidationError): db.update(revised, item.id, qty=-1)
    assert db.update(revised, item.id, qty=1).qty == 1


def test_successful_login_does_not_reset_another_username(secured):
    auth = secured.business.auth
    scope = {'client': ('127.0.0.1', 3000), 'headers': []}
    for _ in range(auth.lockout - 1):
        with pytest.raises(AccessError) as failure:
            auth.login(scope, {'username': 'admin', 'password': 'wrong'})
        assert failure.value.status == 401
    auth.login(scope, {'username': 'viewer', 'password': PASSWORD})
    with pytest.raises(AccessError) as failure:
        auth.login(scope, {'username': 'admin', 'password': 'wrong'})
    assert failure.value.status == 401
    with pytest.raises(AccessError) as failure:
        auth.login(scope, {'username': 'admin', 'password': PASSWORD})
    assert failure.value.status == 429
    assert auth.login(scope, {'username': 'editor', 'password': PASSWORD})[0]['user']['username'] == 'editor'


def test_ipv6_prefix_shares_throttle_and_uds_login_does_not_crash(secured):
    auth = secured.business.auth
    for index in range(auth.lockout):
        with pytest.raises(AccessError) as failure:
            auth.login({'client': (f'2001:db8::{index + 1}', 1)}, {'username':'admin','password':'wrong'})
        assert failure.value.status == 401
    with pytest.raises(AccessError) as failure:
        auth.login({'client': ('2001:db8::99', 1)}, {'username':'admin','password':PASSWORD})
    assert failure.value.status == 429
    assert auth.login({'client':None}, {'username':'admin','password':PASSWORD})[0]['user']['username'] == 'admin'
    with pytest.raises(AccessError) as failure:
        auth.setup({'client':None}, {'username':'owner','password':PASSWORD})
    assert failure.value.status == 403


def test_password_hash_capacity_rejects_excess_work_and_recovers(monkeypatch):
    import evlet.auth as module
    entered = threading.Barrier(3)
    release = threading.Event()
    def hashing(*args, **kwargs):
        entered.wait(3)
        assert release.wait(3)
        return b'x' * 64
    monkeypatch.setattr(module.hashlib, 'scrypt', hashing)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(password_hash, PASSWORD) for _ in range(2)]
        entered.wait(3)
        try:
            with pytest.raises(AccessError) as failure: password_hash(PASSWORD)
            assert failure.value.status == 503
        finally:
            release.set()
        assert all(future.result().startswith('scrypt:') for future in pending)
    monkeypatch.setattr(module.hashlib, 'scrypt', lambda *args, **kwargs: b'x' * 64)
    assert password_hash(PASSWORD).startswith('scrypt:')


def test_no_auth_application_rejects_rebinding_before_database_access(database):
    app = Application(database)
    for method, path, body in [('GET','/api/customer',None), ('POST','/api/customer',{'customer_name':'No'})]:
        assert http(app, method, path, body, headers={b'host':b'attacker.example',b'origin':b'http://attacker.example'})[0] == 403
    assert http(app, 'GET','/api/customer',headers={b'origin':b'http://attacker.example'})[0] == 403
    assert http(app, 'POST','/api/customer',{'customer_name':'Allowed'})[0] == 201
    custom = Application(database, hosts=['development.example'])
    assert http(custom, 'GET','/api/customer',headers={b'host':b'development.example',b'origin':b'http://development.example'})[0] == 200


def test_operator_named_fields_can_be_filtered(tmp_path):
    record = type('Named', (Model,), {'gt':Text(), 'contains':Text()})
    db = Database(tmp_path/'names.sqlite3', Registry(record)); db.initialize()
    saved = db.create(record, gt='x', contains='abc')
    assert db.list(record, filters={'gt':'x', 'contains':'abc'})[0].id == saved.id
    assert db.list(record, filters={'contains__contains':'b'})[0].id == saved.id


def test_text_rejects_nul():
    with pytest.raises(ValueError, match='NUL'): Text().clean('before\0after')


def test_stream_finishes_promptly_when_application_stops(secured):
    account = client(secured)
    async def check():
        events = asyncio.Queue()
        events.put_nowait({'type':'http.request', 'body':b''})
        ready = asyncio.Event()
        sent = []
        async def send(message):
            sent.append(message)
            if message['type'] == 'http.response.body' and message.get('more_body'):
                ready.set()
        scope = {'type':'http', 'method':'GET', 'path':'/api/_events/stream',
                 'headers':[(b'host',b'127.0.0.1:8000'),(b'cookie', account.cookie.encode())]}
        task = asyncio.create_task(secured(scope, events.get, send))
        await asyncio.wait_for(ready.wait(), 2)
        secured.stop()
        await asyncio.wait_for(task, 0.5)
        assert sent[0]['status'] == 200 and sent[-1]['more_body'] is False
    asyncio.run(check())


@pytest.mark.parametrize('debug', [False, True])
def test_expected_runtime_startup_errors_only_trace_in_debug(debug, caplog):
    from evlet.cli.serve import ManagedRuntime
    def factory():
        raise ValueError('notes differs from apps.lock')
    async def check():
        runtime = ManagedRuntime(factory, debug=debug)
        sent = []
        async def receive(): return {'type':'lifespan.startup'}
        async def send(message): sent.append(message)
        await runtime({'type':'lifespan'}, receive, send)
        assert sent == [{'type':'lifespan.startup.failed', 'message':'notes differs from apps.lock'}]
    asyncio.run(check())
    assert bool(caplog.records[-1].exc_info) is debug


def test_scoped_child_edit_preserves_hidden_children(secured):
    from evlet import Policy, Rule
    from examples.playground.models import Project, ProjectTask, registry
    editor = client(secured, 'editor')
    parent = editor('POST', '/api/project', {'title':'Rows', 'children':{'tasks':[{'title':'Visible', 'done':True}, {'title':'Hidden', 'done':False}]}})[1]
    secured.business.policy = Policy(registry, rules={('editor',ProjectTask): Rule(('read','create','update','delete'),scope=lambda user:{'done':True})})
    path = f"/api/project/{parent['id']}"
    visible = editor('GET',path)[1]['children']['tasks'][0]
    assert editor('PATCH',path, {'children':{'tasks':[{'id':visible['id'],'title':'Edited','done':True}]}})[0] == 200
    assert secured.database.count(ProjectTask) == 2
    hidden = next(row for row in secured.database.list(ProjectTask) if not row.done)
    assert hidden.title == 'Hidden'
    assert editor('PATCH',path, {'children':{'tasks':[{'id':hidden.id,'title':'Forbidden'}]}})[0] == 422
    assert secured.database.get(ProjectTask, hidden.id).title == 'Hidden'


def test_mapped_ipv4_and_loopback_clients_do_not_share_one_throttle_key(secured):
    auth = secured.business.auth
    for index in range(10):
        with pytest.raises(AccessError) as failure:
            auth.login({'client': (f'::ffff:198.51.100.{index + 1}', 1)}, {'username': 'admin', 'password': 'wrong'})
        assert failure.value.status == 401
    assert auth.login({'client': ('::ffff:203.0.113.77', 1)}, {'username': 'admin', 'password': PASSWORD})[0]['user']['username'] == 'admin'
    assert auth.login({'client': ('::1', 1)}, {'username': 'admin', 'password': PASSWORD})[0]['user']['username'] == 'admin'
    # A mapped address and its plain IPv4 form are the same client.
    for _ in range(10):
        with pytest.raises(AccessError):
            auth.login({'client': ('::ffff:198.51.100.200', 1)}, {'username': 'editor', 'password': 'wrong'})
    with pytest.raises(AccessError) as failure:
        auth.login({'client': ('198.51.100.200', 1)}, {'username': 'editor', 'password': PASSWORD})
    assert failure.value.status == 429
    # Link-local neighbours are not merged either, while routable IPv6 still shares its /64.
    from evlet.auth import client_network
    assert client_network('fe80::1') == 'fe80::1' and client_network('fe80::2') == 'fe80::2'
    assert client_network('2001:db8::1') == client_network('2001:db8::ffff') == '2001:db8::/64'
    assert client_network('::ffff:10.0.0.1') == '10.0.0.1' and client_network('::') == '::'
    assert client_network('::10.0.0.2') == '10.0.0.2' and client_network('::1') == '::1'
    assert client_network('not an address') == client_network(None) == 'unknown'


def test_busy_password_service_does_not_consume_sign_in_attempts(secured, monkeypatch):
    import evlet.auth as module
    auth = secured.business.auth
    busy = threading.BoundedSemaphore(1)
    assert busy.acquire(blocking=False)
    monkeypatch.setattr(module, '_HASH_SLOTS', busy)
    scope = {'client': ('203.0.113.9', 1), 'headers': []}
    for _ in range(12):
        with pytest.raises(AccessError) as failure:
            auth.login(scope, {'username': 'admin', 'password': PASSWORD})
        assert failure.value.status == 503
    with secured.database._connection() as connection:
        assert connection.execute('SELECT count(*) FROM evlet_login_limits').fetchone()[0] == 0
    busy.release()
    assert auth.login(scope, {'username': 'admin', 'password': PASSWORD})[0]['user']['username'] == 'admin'
    with pytest.raises(AccessError) as failure:
        auth.login(scope, {'username': 'admin', 'password': 'wrong'})
    assert failure.value.status == 401
