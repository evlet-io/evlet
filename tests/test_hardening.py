import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from evlet import Database, Integer, Model, Registry
from tests.test_tenancy import tenants, hashed, call, login


def test_fifty_concurrent_writers_do_not_lose_read_modify_write_updates(tmp_path):
    class Counter(Model):
        value = Integer(default=0)
    db = Database(tmp_path / 'counter.sqlite3', Registry(Counter))
    db.initialize(); record = db.create(Counter)
    def increment(_):
        with db.transaction() as tx:
            current = tx.get(Counter, record.id)
            tx.update(Counter, record.id, value=current.value+1)
    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(increment, range(200)))
    assert db.get(Counter, record.id).value == 200


def test_concurrent_tenant_cookie_replay_and_warmed_route_revocation(tenants):
    catalog, router = tenants
    tokens = {slug: login(router, slug) for slug in ('alpha','beta')}
    projects = {}
    for slug in tokens:
        status, project, _ = call(router, slug+'.localhost:8000', 'POST', '/api/project', {'title': slug}, **tokens[slug])
        assert status == 201
        projects[slug] = f"/api/project/{project['id']}"
    paths = ['project', '/api/_discovery', '/api/schema', '/api/_events']
    def read(index):
        slug = 'alpha' if index%2 else 'beta'
        other = 'beta' if slug == 'alpha' else 'alpha'
        path = paths[(index//2)%len(paths)]
        path = projects[slug] if path == 'project' else path
        assert call(router, slug+'.localhost:8000', path=path, **tokens[other])[0] == 401
        assert call(router, slug+'.localhost:8000', path=path, **tokens[slug])[0] == 200
    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(read, range(100)))
    catalog.set_domain_active('alpha.localhost', False)
    with ThreadPoolExecutor(max_workers=20) as pool:
        assert all(status == 421 for status in pool.map(lambda _: call(router, 'alpha.localhost:8000', path=projects['alpha'], **tokens['alpha'])[0], range(40)))
    assert call(router, 'beta.localhost:8000', path=projects['beta'], **tokens['beta'])[1]['title'] == 'beta'


def test_session_cleanup_and_revocation_use_indexes(tenants):
    catalog, _ = tenants
    with sqlite3.connect(catalog.get('alpha').database_path) as db:
        for query, expected in [('DELETE FROM evlet_sessions WHERE user_id=42', 'evlet_session_user'),
                                ('DELETE FROM evlet_sessions WHERE expires<100', 'evlet_session_expiry'),
                                ('DELETE FROM evlet_login_limits WHERE resets<100', 'evlet_login_reset')]:
            assert expected in str(db.execute('EXPLAIN QUERY PLAN '+query).fetchall())


@pytest.mark.parametrize('header', [b'host', b'origin', b'cookie', b'x-csrf-token', b'x-evlet-request', b'sec-fetch-site'])
def test_duplicate_security_headers_are_rejected_before_authentication(tmp_path, header):
    from evlet import Application, LocalAuth
    db = Database(tmp_path / 'headers.sqlite3', Registry())
    app = Application(db, auth=LocalAuth(db, origins=['http://127.0.0.1:8000'], secure_cookies=False))
    app.initialize()
    async def run():
        sent = []
        async def receive(): return {'type': 'http.request'}
        async def send(event): sent.append(event)
        headers = [(header, b'first'), (header, b'second')]
        if header != b'host': headers.append((b'host', b'127.0.0.1:8000'))
        await app({'type': 'http', 'method': 'GET', 'path': '/api/auth/session', 'headers': headers}, receive, send)
        return sent
    events = asyncio.run(run())
    assert events[0]['status'] == 403
    assert json.loads(events[1]['body'])['error'] == 'Repeated security header'
