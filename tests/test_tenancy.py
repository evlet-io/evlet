import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest

from evlet import TenantApplication, TenantRegistry
from evlet.tenancy import dns_txt, hostname
from examples.playground.manage import provision
from examples.playground.tenants import tenant_app
from tests.test_business import http, PASSWORD, hashed


@pytest.fixture
def tenants(tmp_path, hashed):
    catalog = TenantRegistry.create(tmp_path / 'tenants', development=True)
    for slug in ('alpha', 'beta'):
        tenant = catalog.create_tenant(slug, slug.title(), f'{slug}.localhost')
        tenant.database_path.parent.mkdir(parents=True)
        tenant.database_path.touch()
        app = tenant_app(tenant); app.initialize()
        with app.database.transaction() as tx:
            for role in ('admin', 'viewer'):
                tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (role, hashed, role))
        catalog.set_active(slug, True)
    return catalog, TenantApplication(catalog, tenant_app)


def call(router, host, method='GET', path='/api/auth/session', data=None, **kwargs):
    return http(router, method, path, data, headers={b'host': host.encode(), **kwargs.pop('headers', {})}, **kwargs)


def login(router, slug, role='admin'):
    host = slug + '.localhost:8000'
    status, result, headers = call(router, host, 'POST', '/api/auth/login', {'username': role, 'password': PASSWORD})
    assert status == 200, result
    return {'cookie': headers[b'set-cookie'].decode().split(';')[0], 'csrf': result['csrf']}


def test_isolated_sessions_records_children_actions_and_files(tenants):
    catalog, router = tenants
    alpha, beta = login(router, 'alpha'), login(router, 'beta')
    for slug, auth in (('alpha', alpha), ('beta', beta)):
        host = slug + '.localhost:8000'
        status, project, _ = call(router, host, 'POST', '/api/project', {'name': slug, 'children': {'tasks': [{'title': slug, 'done': True}]}}, **auth)
        assert status == 201 and project['id'] == 1
        assert call(router, host, 'POST', '/api/project/1/actions/start', {}, **auth)[0] == 200
        content = base64.b64encode(slug.encode()).decode()
        assert call(router, host, 'POST', '/api/project/1/attachments', {'name': 'same.txt', 'content': content}, **auth)[0] == 201
    for slug, auth, other in (('alpha', alpha, beta), ('beta', beta, alpha)):
        host = slug + '.localhost:8000'
        assert call(router, host, path='/api/project', **auth)[1]['items'][0]['name'] == slug
        assert call(router, host, path='/api/project/1', **auth)[1]['children']['tasks'][0]['title'] == slug
        assert call(router, host, path='/api/project/1/attachments/1', **auth)[1] == slug.encode()
        for path in ('/api/schema', '/api/project/1', '/api/project/1/attachments/1', '/api/auth/users'):
            assert call(router, host, path=path, **other)[0] == 401
        assert call(router, host, 'PATCH', '/api/project/1', {'name': 'wrong'}, cookie=auth['cookie'], csrf=other['csrf'])[0] == 403
        assert call(router, host, 'POST', '/api/project/1/actions/complete', {}, **other)[0] == 401
        schema = call(router, host, path='/api/schema', **auth)[1]
        assert schema['tenant'] == {'slug': slug, 'label': slug.title()}
        assert 'database_path' not in json.dumps(schema)
        tenant = catalog.get(slug)
        with sqlite3.connect(tenant.database_path) as db:
            row = db.execute('SELECT content,storage_key FROM evlet_attachments').fetchone()
            assert row[0] == b'' and (tenant.files_path / row[1]).read_bytes() == slug.encode()
    viewer = login(router, 'alpha', 'viewer')
    assert call(router, 'alpha.localhost:8000', 'POST', '/api/project/1/attachments', {'name': 'x', 'content': 'eA=='}, **viewer)[0] == 403
    restarted = TenantApplication(TenantRegistry(catalog.root), tenant_app)
    assert call(restarted, 'alpha.localhost:8000', path='/api/project/1/attachments/1', **alpha)[1] == b'alpha'


@pytest.mark.parametrize('host', ['unknown.localhost:8000', 'alpha.localhost.evil.test:8000', 'alpha.localhost.:8000', 'alpha.localhost:80', 'alpha.localhost:08000', 'alpha.localhost', '127.0.0.1:8000', 'alpha.localhost:8000:80', 'alpha.localhost\n:8000'])
def test_unknown_and_malformed_hosts_do_not_open_database(tenants, host):
    catalog, _ = tenants
    opened = []
    router = TenantApplication(catalog, lambda tenant: opened.append(tenant))
    assert call(router, host, path='/health')[0] == 421
    assert opened == []


def test_duplicate_host_and_forwarded_host(tenants):
    catalog, router = tenants
    sent = []
    async def receive(): return {'type': 'http.request'}
    async def send(event): sent.append(event)
    asyncio.run(router({'type': 'http', 'path': '/health', 'method': 'GET', 'headers': [(b'host', b'alpha.localhost:8000'), (b'host', b'beta.localhost:8000')]}, receive, send))
    assert sent[0]['status'] == 421
    assert call(router, 'unknown.localhost:8000', headers={b'x-forwarded-host': b'alpha.localhost:8000'})[0] == 421
    assert call(router, 'ALPHA.localhost:8000', headers={b'x-forwarded-host': b'beta.localhost:8000', b'x-tenant': b'beta'})[1]['tenant']['slug'] == 'alpha'
    assert call(router, 'alpha.localhost:8000', headers={b'origin': b'http://beta.localhost:8000'})[0] == 403


def test_disabling_tenant_or_domain_invalidates_cached_routes(tenants):
    catalog, router = tenants
    assert call(router, 'alpha.localhost:8000')[0] == 200
    catalog.set_active('alpha', False)
    assert call(router, 'alpha.localhost:8000')[0] == 421
    catalog.set_active('alpha', True)
    assert call(router, 'alpha.localhost:8000')[0] == 200
    catalog.set_domain_active('alpha.localhost', False)
    assert call(router, 'alpha.localhost:8000')[0] == 421
    assert call(router, 'beta.localhost:8000')[0] == 200


def test_missing_cached_database_does_not_get_recreated(tenants):
    catalog, router = tenants
    assert call(router, 'alpha.localhost:8000')[0] == 200
    database = catalog.get('alpha').database_path
    database.unlink()
    assert call(router, 'alpha.localhost:8000')[0] == 503
    assert not database.exists()
    assert call(router, 'beta.localhost:8000')[0] == 200


def test_parallel_requests_keep_tenant_context(tenants):
    _, router = tenants
    hosts = ['alpha.localhost:8000', 'beta.localhost:8000'] * 12
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda host: call(router, host)[1]['tenant']['slug'], hosts))
    assert results == [host.split('.')[0] for host in hosts]


def test_catalog_rejects_duplicate_unsafe_and_unowned_identifiers(tmp_path):
    with pytest.raises(sqlite3.OperationalError): TenantRegistry(tmp_path / 'missing')
    assert not (tmp_path / 'missing').exists()
    catalog = TenantRegistry.create(tmp_path, managed_domains=('example.test',))
    with pytest.raises(FileExistsError): TenantRegistry.create(tmp_path, managed_domains=('example.test',))
    for slug, host in (('../escape', 'a.example.test'), ('fine', 'evil.test'), ('fine', 'example.test.evil.test')):
        with pytest.raises(ValueError): catalog.create_tenant(slug, 'Title', host)
    catalog.create_tenant('alpha', 'Alpha', 'a.example.test')
    with pytest.raises(sqlite3.IntegrityError): catalog.create_tenant('beta', 'Beta', 'a.example.test')
    assert [row['slug'] for row in catalog.inventory()['tenants']] == ['alpha']
    with pytest.raises(ValueError): catalog.set_active('alpha', True)
    for value in ('127.0.0.1', '*.example.test', 'a.test:443', 'é.test', '-a.test', 'a..test', 'a.test\n'):
        with pytest.raises(ValueError): hostname(value)


def test_custom_domain_requires_exact_fresh_proof_and_unique_owner(tmp_path):
    catalog = TenantRegistry.create(tmp_path, managed_domains=('example.test',))
    for slug in ('alpha', 'beta'):
        tenant = catalog.create_tenant(slug, slug.title(), slug + '.example.test')
        tenant.database_path.parent.mkdir(parents=True); tenant.database_path.touch()
        catalog.set_active(slug, True)
    challenge = catalog.add_domain('alpha', 'customer.test')
    assert catalog.resolve('customer.test') is None
    with pytest.raises(sqlite3.IntegrityError): catalog.add_domain('beta', 'customer.test')
    with pytest.raises(ValueError): catalog.verify_domain('customer.test', resolver=lambda name: ('wrong',))
    def proof(name):
        assert name == challenge['name']
        return (challenge['value'],)
    catalog.verify_domain('customer.test', resolver=proof)
    assert catalog.resolve('customer.test').slug == 'alpha'
    assert 'https://customer.test' in catalog.get('alpha').origins
    challenge = catalog.challenge('customer.test')
    assert catalog.resolve('customer.test') is None
    def race(name):
        catalog.challenge('customer.test')
        return (challenge['value'],)
    with pytest.raises(ValueError): catalog.verify_domain('customer.test', resolver=race)
    with catalog._connection(write=True) as db: db.execute('UPDATE domains SET expires=0 WHERE hostname=?', ('customer.test',))
    with pytest.raises(ValueError): catalog.verify_domain('customer.test', resolver=proof)
    assert catalog.resolve('customer.test') is None


def test_dns_txt_chunks_and_command_errors(monkeypatch):
    import subprocess
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, 'alias.test.\n"evlet-verification=" "token"\n"other"\n', '')
    monkeypatch.setattr(subprocess, 'run', run)
    assert dns_txt('_evlet-challenge.customer.test') == ('evlet-verification=token', 'other')
    assert calls[0][1]['timeout'] == 8 and calls[0][1]['check'] is True
    assert calls[0][0][-1] == '_evlet-challenge.customer.test'


def test_provision_and_production_cookie_requirements(tmp_path):
    catalog = TenantRegistry.create(tmp_path, managed_domains=('example.test',))
    tenant = catalog.create_tenant('alpha', 'Alpha', 'alpha.example.test')
    app = provision(catalog, 'alpha', 'owner', PASSWORD)
    assert catalog.resolve('alpha.example.test') == tenant
    assert app.business.auth.allow_setup is False
    cookie = app.business.auth.cookie('value')[1].decode()
    assert cookie.startswith('__Host-evlet_session=') and '; Secure' in cookie and 'Domain=' not in cookie
    with pytest.raises(ValueError): provision(catalog, 'alpha', 'other', PASSWORD)
    router = TenantApplication(catalog, tenant_app)
    assert call(router, 'alpha.example.test')[0] == 421  # Plain HTTP is never accepted.
    async def run():
        events = []
        async def receive(): return {'type': 'http.request'}
        async def send(event): events.append(event)
        await router({'type': 'http', 'scheme': 'https', 'method': 'GET', 'path': '/api/auth/session', 'headers': [(b'host', b'alpha.example.test:443')]}, receive, send)
        return events
    events = asyncio.run(run())
    assert events[0]['status'] == 200 and json.loads(events[1]['body'])['setup'] is False


def test_factory_cannot_share_database_or_storage(tenants):
    catalog, _ = tenants
    other = catalog.get('beta')
    router = TenantApplication(catalog, lambda tenant: tenant_app(other))
    assert call(router, 'alpha.localhost:8000')[0] == 503
    def bad_storage(tenant):
        app = tenant_app(tenant)
        app.business.storage.root = other.files_path
        return app
    assert call(TenantApplication(catalog, bad_storage), 'alpha.localhost:8000')[0] == 503


def test_router_shutdown_stops_each_tenant(tenants):
    catalog, router = tenants
    login(router, 'alpha'); login(router, 'beta')
    apps = [app for _, app in router._apps.values()]
    assert len(apps) == 2 and not any(app._stopping.is_set() for app in apps)
    async def run():
        events = iter([{'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}])
        sent = []
        async def receive(): return next(events)
        async def send(message): sent.append(message['type'])
        await router({'type': 'lifespan'}, receive, send)
        return sent
    assert asyncio.run(run()) == ['lifespan.startup.complete', 'lifespan.shutdown.complete']
    assert all(app._stopping.is_set() for app in apps)


def test_router_stop_reaches_tenants_created_later(tenants):
    catalog, router = tenants
    login(router, 'alpha')
    stopped = router.stop()
    assert [app._stopping.is_set() for app in stopped] == [True]
    login(router, 'beta')
    assert all(app._stopping.is_set() for _, app in router._apps.values())


def test_each_tenant_manages_its_own_users_through_controls_on_its_own_host(tenants):
    _, router = tenants
    alpha, beta = login(router, 'alpha'), login(router, 'beta')
    for slug, auth in (('alpha', alpha), ('beta', beta)):
        host = slug + '.localhost:8000'
        status, state, _ = call(router, host, path='/api/_controls/state', **auth)
        assert status == 200 and state['instance']['host'] == slug + '.localhost'
        created = call(router, host, 'POST', '/api/_controls/users', {'username': slug + '-clerk', 'password': PASSWORD, 'role': 'editor'}, **auth)
        assert created[0] == 201
        assert call(router, host, 'POST', '/api/auth/users', {'username': slug + '-old', 'password': PASSWORD, 'role': 'viewer'}, **auth)[0] == 201
        names = {item['username'] for item in call(router, host, path='/api/auth/users', **auth)[1]['items']}
        assert names == {'admin', 'viewer', slug + '-clerk', slug + '-old'}
        assert {item['username'] for item in call(router, host, path='/api/_controls/users', **auth)[1]['items']} == names
    for slug, other in (('alpha', beta), ('beta', alpha)):
        host = slug + '.localhost:8000'
        for path in ('/api/_controls/state', '/api/_controls/users', '/api/auth/users'):
            assert call(router, host, path=path, **other)[0] == 401
        stranger = ('beta' if slug == 'alpha' else 'alpha') + '-clerk'
        assert call(router, host, 'POST', '/api/auth/login', {'username': stranger, 'password': PASSWORD})[0] == 401
    viewer = login(router, 'alpha', 'viewer')
    assert call(router, 'alpha.localhost:8000', path='/api/_controls/state', **viewer)[0] == 404
    assert call(router, 'alpha.localhost:8000', path='/api/auth/users', **viewer)[0] == 403
