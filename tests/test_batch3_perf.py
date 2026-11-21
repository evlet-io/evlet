"""Request deadlines, bounded counts, connection pool sizing, the shared event poller and the schema timezone."""

import asyncio
import sqlite3

import pytest

import evlet.app
from evlet import writes
from evlet.database import DEADLINE, Deadline, capped_count, page_total
from evlet import qb
from examples.playground.models import Customer
from examples.playground.reports import MOST_CUSTOMERS, aed_customers
from tests.support import client


def test_report_runs_stop_at_their_deadline(secured, monkeypatch):
    from examples.playground.reports import SalesByCustomer
    admin = client(secured)
    assert admin('POST', '/api/_reports/SalesByCustomer', {'filters': {}})[0] == 200
    endless = 'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n'
    original = SalesByCustomer.run
    def slow(report):
        report.context._tx.connection.execute(endless).fetchone()
    monkeypatch.setattr(SalesByCustomer, 'run', slow)
    monkeypatch.setenv('EVLET_REPORT_SECONDS', '0.2')
    status, payload, _ = admin('POST', '/api/_reports/SalesByCustomer', {'filters': {}})
    assert status == 504 and 'time limit' in payload['error']
    # Other requests have no limit unless EVLET_REQUEST_SECONDS sets one, and pooled connections lose the handler.
    assert admin('GET', '/api/customer')[0] == 200
    monkeypatch.setattr(SalesByCustomer, 'run', original)
    monkeypatch.setenv('EVLET_REPORT_SECONDS', '0')
    assert admin('POST', '/api/_reports/SalesByCustomer', {'filters': {}})[0] == 200
    monkeypatch.setenv('EVLET_REPORT_SECONDS', 'soon')
    with pytest.raises(ValueError):
        evlet.app.deadline_seconds('/api/_reports/SalesByCustomer', 'POST')


def test_deadline_interrupts_sqlite_and_cancel_moves_it(secured):
    database = secured.database
    deadline = Deadline(60)
    token = DEADLINE.set(deadline)
    try:
        database.check()
        deadline.cancel()
        assert deadline.passed()
        recursive = 'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n'
        with pytest.raises(sqlite3.OperationalError, match='interrupt'):
            with database._connection() as connection:
                connection.execute(recursive).fetchone()
    finally:
        DEADLINE.reset(token)
    database.check()


def test_list_totals_skip_the_count_when_the_page_tells_it(secured):
    assert page_total(50, 0, 0) == 0
    assert page_total(50, 100, 7) == 107
    assert page_total(50, 0, 50) is None and page_total(50, 100, 0) is None
    database = secured.database
    with database.transaction() as tx:
        for number in range(5):
            tx.create(Customer, customer_name=f'Counted {number}', currency='AED')
    total = database.count(Customer)
    assert database.page(Customer, limit=2, offset=0)[1] == total
    assert database.page(Customer, limit=50, offset=0)[1] == total
    assert database.count(Customer, cap=3) == min(3, total)
    with database._connection() as connection:
        query = qb.select(qb.table('customer', 'record'), qb.col('record.id'))
        assert capped_count(connection, query, total + 10) == total
        with pytest.raises(ValueError):
            capped_count(connection, query, 0)


def test_schema_names_the_instance_timezone(secured):
    admin = client(secured)
    status, schema, _ = admin('GET', '/api/schema')
    assert status == 200 and schema['timezone'] == 'UTC'
    secured.business.timezone = 'Asia/Dubai'
    assert admin('GET', '/api/schema')[1]['timezone'] == 'Asia/Dubai'


def test_idle_pool_follows_the_descriptor_limit(monkeypatch):
    monkeypatch.setattr(writes, 'IDLE_LIMIT', None)
    assert 8 <= writes.idle_limit() <= 32
    monkeypatch.setenv('EVLET_IDLE_CONNECTIONS', '64')
    assert writes._configured_idle() == 64
    monkeypatch.setenv('EVLET_IDLE_CONNECTIONS', 'many')
    assert writes._configured_idle() is None


def test_several_workers_are_detected(monkeypatch):
    monkeypatch.delenv('WEB_CONCURRENCY', raising=False)
    monkeypatch.setattr(evlet.app.sys, 'argv', ['uvicorn', 'app:app'])
    assert not evlet.app.several_workers()
    monkeypatch.setattr(evlet.app.sys, 'argv', ['uvicorn', 'app:app', '--workers', '4'])
    assert evlet.app.several_workers()
    monkeypatch.setattr(evlet.app.sys, 'argv', ['uvicorn'])
    monkeypatch.setenv('WEB_CONCURRENCY', '2')
    assert evlet.app.several_workers()


def test_json_is_encoded_in_the_worker(secured):
    status, payload, _ = secured._encoded(lambda: (200, {'a': 1}, []))
    assert isinstance(payload, evlet.app._Encoded) and payload.content == b'{"a": 1}'
    assert secured._encoded(lambda: (204, None, []))[1] is None


def test_playground_reads_aed_customers_by_keyset(secured):
    business = secured.business
    with business.database.transaction() as tx:
        for number in range(MOST_CUSTOMERS + 1):
            tx.create(Customer, customer_name=f'Keyset {number}', currency='AED')
    admin = client(secured)
    status, payload, _ = admin('POST', '/api/_reports/SalesByCustomer', {'filters': {}})
    assert status == 422 and 'pick one customer' in str(payload)


def test_streams_share_one_poller(secured, monkeypatch):
    monkeypatch.setattr(evlet.app, 'STREAM_RECHECK', 30)
    admin = client(secured)
    assert secured.business.events.latest() >= 0
    async def run():
        blocker = asyncio.Event()
        bodies = [[], []]
        async def receive():
            await blocker.wait()
            return {'type': 'http.disconnect'}
        def sender(index):
            async def send(event):
                if event['type'] == 'http.response.body' and b'event: evlet' in event.get('body', b''):
                    bodies[index].append(event['body'])
            return send
        scope = {'type': 'http', 'method': 'GET', 'path': '/api/_events/stream', 'query_string': b'',
                 'headers': [(b'host', b'127.0.0.1:8000'), (b'cookie', admin.cookie.encode())]}
        streams = [asyncio.create_task(secured(dict(scope), receive, sender(index))) for index in range(2)]
        for _ in range(100):
            await asyncio.sleep(0.02)
            if all(bodies):
                break
        pump = secured._event_pump
        assert pump is not None and secured._event_streams == 2
        # The poller's first read (after a second) is its baseline; the write comes after it.
        await asyncio.sleep(1.3)
        await asyncio.to_thread(admin, 'POST', '/api/customer', {'customer_name': 'Woken', 'currency': 'AED'})
        for _ in range(150):
            await asyncio.sleep(0.02)
            if all(any(b'customer' in body for body in items) for items in bodies):
                break
        assert secured._event_pump is pump
        blocker.set()
        await asyncio.gather(*streams)
        return bodies
    bodies = asyncio.run(run())
    assert all(any(b'"customer"' in body for body in items) for items in bodies)
