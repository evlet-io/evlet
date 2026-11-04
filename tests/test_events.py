import asyncio
import json

import pytest

from evlet import EventStore, Policy, Rule, User
from examples.playground.models import Customer, Project
from test_business import secured, hashed, client, http


def test_transactional_events_and_filtered_replay(secured):
    c = client(secured)
    baseline = c('GET', '/api/_events')[1]['cursor']
    assert baseline == '0'
    customer = c('POST', '/api/customer', {'customer_name': 'Visible'})[1]
    page = c('GET', '/api/_events?cursor=0')[1]
    assert page['items'] == [{'model': 'customer', 'id': customer['id'], 'deleted': False}]
    assert len(page['cursor']) == 48
    assert c('GET', '/api/_events?cursor=' + page['cursor'])[1]['items'] == []
    with pytest.raises(RuntimeError):
        with secured.database.transaction() as tx:
            secured.business.events.publish(tx, Customer, 1)
            raise RuntimeError('rollback')
    assert c('GET', '/api/_events?cursor=' + page['cursor'])[1]['items'] == []
    assert http(secured, 'GET', '/api/_events')[0] == 401
    assert c('GET', '/api/_events?cursor=bad')[0] == 422
    assert c('GET', '/api/_events?cursor=0&cursor=0')[0] == 422


def test_events_never_include_hidden_rows_fields_or_deleted_scoped_ids(secured):
    c = client(secured)
    visible = c('POST', '/api/customer', {'customer_name': 'visible', 'notes': 'secret'})[1]
    c('POST', '/api/customer', {'customer_name': 'hidden', 'notes': 'secret'})
    secured.business.policy = Policy(secured.registry, rules={('viewer', Customer): Rule(('read',), read_fields=('customer_name',), scope=lambda user: {'customer_name': 'visible'})})
    viewer = client(secured, 'viewer')
    page = viewer('GET', '/api/_events?cursor=0')[1]
    assert page['items'] == [{'model': 'customer', 'id': visible['id'], 'deleted': False}]
    assert 'secret' not in json.dumps(page) and 'hidden' not in json.dumps(page)
    c('DELETE', f"/api/customer/{visible['id']}")
    assert viewer('GET', '/api/_events?cursor=' + page['cursor'])[1]['items'] == []
    assert c('GET', '/api/_events?cursor=' + page['cursor'])[1]['items'][0]['deleted'] is True


def test_retention_resets_stale_cursor(secured):
    events = EventStore(secured.database, retention=100)
    secured.database.create(Customer, customer_name='One')
    with secured.database.transaction() as tx:
        old = events.publish(tx, Customer, 1)
        for _ in range(101): events.publish(tx, Customer, 1)
    page = events.page(User(1, 'admin', 'admin'), secured.business.policy, after=old)
    assert page['reset'] and page['items'] == []
    assert events.page(User(1, 'admin', 'admin'), secured.business.policy, after='0')['reset']


def test_stream_rechecks_sessions_and_closes_on_revocation(secured):
    c = client(secured)
    async def run():
        sent = []
        blocker = asyncio.Event()
        async def receive():
            await blocker.wait()
            return {'type': 'http.disconnect'}
        async def send(event):
            sent.append(event)
            if event['type'] == 'http.response.body' and event.get('more_body') and b'event: evlet' in event['body']:
                with secured.database.transaction() as tx: tx.connection.execute('DELETE FROM evlet_sessions')
        scope = {'type': 'http', 'method': 'GET', 'path': '/api/_events/stream', 'query_string': b'', 'headers': [(b'host', b'127.0.0.1:8000'), (b'cookie', c.cookie.encode())]}
        await asyncio.wait_for(secured(scope, receive, send), timeout=3)
        return sent
    sent = asyncio.run(run())
    assert sent[0]['status'] == 200
    assert any(b'event: unavailable' in event.get('body', b'') for event in sent)
    assert sent[-1]['more_body'] is False


def test_stream_checks_tenant_revocation(secured):
    c = client(secured)
    assert http(secured, 'GET', '/api/_events/stream', headers={b'host': b'evil.test'})[0] == 403
    async def run():
        sent = []; active = True; blocked = asyncio.Event()
        async def receive(): await blocked.wait(); return {'type': 'http.disconnect'}
        async def send(event):
            nonlocal active
            sent.append(event)
            if event['type'] == 'http.response.body': active = False
        scope = {'type': 'http', 'method': 'GET', 'path': '/api/_events/stream', 'headers': [(b'host', b'127.0.0.1:8000'), (b'cookie', c.cookie.encode())], 'evlet.tenant_active': lambda: active}
        await asyncio.wait_for(secured(scope, receive, send), timeout=3)
        return sent
    assert any(b'event: unavailable' in event.get('body', b'') for event in asyncio.run(run()))
