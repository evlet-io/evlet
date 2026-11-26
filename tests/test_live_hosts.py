"""Hosts from data (evlet/hosts.py): evlet_hosts rows join the route table without a restart."""

import functools
import socket
import time

import pytest

from evlet import AppDefinition, Application, Database, Frontend, Host, LocalAuth, Registry
from evlet import hosts
from evlet.cli import proxy
from evlet.cli.instance import set_domain
from evlet.cli.main import main
from evlet.routing import resolve_routes, with_hosts
from support import http
from test_cli_hosts import write_site
from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401  (fixtures)

HERE = [(2, 1, 6, '', ('203.0.113.7', 0))]


def here(host, port, **options):
    return HERE


def nowhere(host, port, **options):
    return []


def shop_routes(tmp_path, code_hosts=()):
    dist = tmp_path / 'shop/dist'
    dist.mkdir(parents=True, exist_ok=True)
    (dist / 'index.html').write_text('<!doctype html><html><head><title>Shop</title></head><body>Shop</body></html>')
    shop = Frontend('shop', kind='static', dist='dist', path='/shop', auth=None)
    portal = Frontend('portal', kind='static', dist='dist', path='/portal', auth='portal')
    desk = Frontend('crm', path='/crm')
    definition = AppDefinition(frontends=(shop, portal, desk))
    module = {'primary': Host('erp.acme.com'), 'hosts': tuple(code_hosts)}
    return resolve_routes([('shop', definition)], None, module, roots={'shop': tmp_path / 'shop'})


def build(tmp_path, **options):
    routes = shop_routes(tmp_path, **options)
    database = Database(tmp_path / 'live.sqlite3', Registry())
    auth = LocalAuth(database, origins=routes.origins(), realms=routes.realms(), cookie_name='__Host-evlet_session')
    app = Application(database, auth=auth, routes=routes)
    app.initialize()
    return app


def on(host):
    return {b'host': host.encode()}


def test_with_hosts_adds_sub_hosts_and_skips_rows_it_cannot_serve(tmp_path):
    code = shop_routes(tmp_path, code_hosts=(Host('code.acme.com', root='shop.shop'),))
    table, problems = with_hosts(code, [
        ('www.acme.com', 'shop.shop'), ('portal.acme.com', 'shop.portal'), ('code.acme.com', 'shop.portal'),
        ('erp.acme.com', 'shop.shop'), ('desk.acme.com', 'shop.crm'), ('gone.acme.com', 'old.www'), ('Bad Host', 'shop.shop'),
        ('dev.localhost', 'shop.shop'), ('site.acme.com', 'mysite'), ('www.acme.com', 'shop.portal'), (7, None)])
    assert table.data_hosts() == ('www.acme.com', 'portal.acme.com')
    assert table.sub_hosts() == ('code.acme.com', 'www.acme.com', 'portal.acme.com')
    assert table.public('www.acme.com') and table.realm('portal.acme.com') == 'portal' and table.root('www.acme.com') == 'shop.shop'
    assert 'https://www.acme.com' in table.origins() and table.realms()['https://portal.acme.com'] == 'portal'
    assert [(row.host, row.path, row.source) for row in table.rows() if row.source == 'data'][:2] == [('www.acme.com', '/', 'data'), ('www.acme.com', '/api/*', 'data')]
    # The frontend keeps its path on the primary host: a data host never moves a path.
    assert table.match('erp.acme.com', '/shop') == code.match('erp.acme.com', '/shop')
    reasons = dict(problems)
    assert reasons['code.acme.com'] == 'code.acme.com is a sub-host in routes.py, which wins over data'
    assert reasons['erp.acme.com'] == 'erp.acme.com is the primary host'
    assert 'evlet frontend' in reasons['desk.acme.com'] and 'not a frontend of an installed app' in reasons['gone.acme.com']
    assert 'not a lowercase hostname' in reasons['Bad Host'] and '.localhost' in reasons['dev.localhost']
    assert reasons['site.acme.com'] == 'mysite is not a site of this instance'
    assert reasons['www.acme.com'] == 'www.acme.com is listed twice' and 7 in reasons
    # The code table is unchanged.
    assert code.data_hosts() == () and 'https://www.acme.com' not in code.origins()


def test_a_site_target_plugs_in_through_sites(tmp_path):
    site = Frontend('home', kind='static', dist='.', path='/home', auth=None)
    table, problems = with_hosts(shop_routes(tmp_path), [('home.acme.com', 'home')], {'home': (site, tmp_path / 'shop/dist')})
    assert not problems and table.root('home.acme.com') == 'home' and table.dist('home') == tmp_path / 'shop/dist'


def test_a_host_written_by_one_application_is_served_by_another_within_a_second(tmp_path):
    writer, reader = build(tmp_path), build(tmp_path)
    try:
        before = http(reader, 'GET', '/', headers=on('www.acme.com'))[0]
        assert before != 200
        assert http(reader, 'GET', '/_/tls-allowed?domain=www.acme.com')[0] == 404
        row = hosts.add_host(writer.business, 'www.acme.com', 'shop.shop', actor='admin')
        assert row['status'] == 'pending' and not row['served']
        # Pending: not served, and Caddy gets no certificate for it.
        time.sleep(1.05)
        assert http(reader, 'GET', '/_/tls-allowed?domain=www.acme.com')[0] == 404
        [checked] = hosts.check_hosts(writer.business, ['www.acme.com'], actor='admin', addresses=['203.0.113.7'], resolve=here)
        assert checked['status'] == 'active' and checked['records'] == '203.0.113.7' and checked['served']
        assert writer.routes.data_hosts() == ('www.acme.com',)
        started = time.monotonic()
        while http(reader, 'GET', '/', headers=on('www.acme.com'))[0] != 200:
            assert time.monotonic() - started < 2, 'the other application did not pick up the host'
            time.sleep(0.05)
        status, page, _ = http(reader, 'GET', '/', headers=on('www.acme.com'))
        assert status == 200 and b'Shop' in page
        assert http(reader, 'GET', '/_/tls-allowed?domain=www.acme.com')[:2] == (200, {'allowed': 'www.acme.com'})
        assert reader.business.auth.hosts >= {b'www.acme.com', b'erp.acme.com'}
        # Disabled: it stops being served by both, and the certificate check refuses it.
        hosts.set_host_status(writer.business, 'www.acme.com', 'disabled', actor='admin')
        time.sleep(1.05)
        assert http(reader, 'GET', '/_/tls-allowed?domain=www.acme.com')[0] == 404
        assert http(reader, 'GET', '/', headers=on('www.acme.com'))[0] != 200
        assert b'www.acme.com' not in reader.business.auth.hosts and reader.routes.data_hosts() == ()
        hosts.remove_host(writer.business, 'www.acme.com', actor='admin')
        assert hosts.list_hosts(reader.business) == []
        with writer.database._connection() as connection:
            audit = [tuple(row) for row in connection.execute("SELECT actor, target, detail FROM evlet_audit WHERE area = 'Hosts' ORDER BY id")]
        assert audit == [('admin', 'www.acme.com', 'Added, serving shop.shop'), ('admin', 'www.acme.com', 'Active: DNS points here'),
                         ('admin', 'www.acme.com', 'Disabled'), ('admin', 'www.acme.com', 'Removed')]
    finally:
        writer.database.close()
        reader.database.close()


def test_add_host_refuses_what_the_table_cannot_serve_and_checks_keep_an_active_host(tmp_path):
    app = build(tmp_path, code_hosts=(Host('code.acme.com', root='shop.shop'),))
    try:
        for host, target, error in (('code.acme.com', 'shop.portal', 'routes.py'), ('erp.acme.com', 'shop.shop', 'primary host'),
                                    ('www.acme.com', 'shop.crm', 'evlet frontend'), ('www.acme.com', 'site.www', 'not a frontend')):
            with pytest.raises(ValueError, match=error):
                hosts.add_host(app.business, host, target, actor='admin')
        hosts.add_host(app.business, 'portal.acme.com', 'shop.portal', actor='admin')
        with pytest.raises(ValueError, match='already a host'):
            hosts.add_host(app.business, 'portal.acme.com', 'shop.shop', actor='admin')
        [row] = hosts.check_hosts(app.business, actor='admin', addresses=['203.0.113.7'], resolve=nowhere)
        assert row['status'] == 'pending' and row['error'] == 'DNS: no A or AAAA record'
        hosts.check_hosts(app.business, actor='admin', addresses=['203.0.113.7'], resolve=here)
        # A failing check later keeps it active and records the error; only disable stops serving it.
        [row] = hosts.check_hosts(app.business, actor='admin', addresses=['203.0.113.7'], resolve=nowhere)
        assert row['status'] == 'active' and row['served'] and row['error'] == 'DNS: no A or AAAA record'
        assert app.routes.realm('portal.acme.com') == 'portal' and app.business.auth.realms[b'portal.acme.com'] == 'portal'
        with pytest.raises(ValueError, match='pending or disabled'):
            hosts.set_host_status(app.business, 'portal.acme.com', 'active', actor='admin')
    finally:
        app.database.close()


def test_the_check_job_makes_pending_hosts_active(tmp_path, monkeypatch):
    from evlet import JobQueue
    routes = shop_routes(tmp_path)
    database = Database(tmp_path / 'jobs.sqlite3', Registry())
    auth = LocalAuth(database, origins=routes.origins(), realms=routes.realms(), cookie_name='__Host-evlet_session')
    app = Application(database, auth=auth, routes=routes, jobs=JobQueue(database, []))
    app.initialize()
    try:
        with database._connection() as connection:
            assert connection.execute("SELECT name FROM evlet_schedules WHERE name = 'evlet_host_check'").fetchone() is None
        hosts.add_host(app.business, 'www.acme.com', 'shop.shop', actor='admin')
        with database._connection() as connection:
            assert connection.execute("SELECT interval FROM evlet_schedules WHERE name = 'evlet_host_check'").fetchone()[0] == 60
        monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **options: HERE)
        monkeypatch.setattr(hosts, 'server_addresses', lambda: {'203.0.113.7'})
        task = app.business.jobs.tasks[hosts.CHECK_TASK]
        assert task.external and task.handler(None, {}) == {'checked': 1}
        assert app.routes.data_hosts() == ('www.acme.com',)
        assert task.handler(None, {}) == {'checked': 0}
    finally:
        database.close()


def test_a_malformed_row_never_fails_the_boot_and_is_listed_under_needs_attention(tmp_path):
    app = build(tmp_path)
    with app.database.transaction() as tx:
        for host, target in (('www.acme.com', 'shop.shop'), ('gone.acme.com', 'removed.app'), ('NOT A HOST', 'shop.shop'), ('erp.acme.com', 'shop.shop')):
            tx.connection.execute('INSERT INTO evlet_hosts(host, target, status, records, created_at, created_by, changed_at) '
                                  "VALUES (?, ?, 'active', '', 0, 'test', 0)", (host, target))
        tx.connection.execute('UPDATE evlet_hosts_version SET version = version + 1')
    app.database.close()
    booted = build(tmp_path)
    try:
        assert booted.routes.data_hosts() == ('www.acme.com',)
        assert http(booted, 'GET', '/', headers=on('www.acme.com'))[0] == 200
        items = booted.live_routes.attention()
        assert [item['title'] for item in items] == ['NOT A HOST is not served', 'erp.acme.com is not served', 'gone.acme.com is not served']
        assert all(item['page'] == 'routing' for item in items)
        listed = {row['host']: row for row in hosts.list_hosts(booted.business)}
        assert listed['gone.acme.com']['problem'] == 'removed.app is not a frontend of an installed app' and not listed['gone.acme.com']['served']
    finally:
        booted.database.close()


def test_an_application_without_hosts_from_data_keeps_its_route_table(tmp_path):
    app = build(tmp_path)
    try:
        code, origins = app.live_routes.code, app.business.auth.origins
        assert app.live_routes.refresh(force=True) and app.routes is code and app.business.auth.origins is origins
        assert hosts.list_hosts(app.business) == [] and app.live_routes.attention() == []
    finally:
        app.database.close()


# ---------- evlet host --live ----------

@pytest.fixture
def web(project, monkeypatch):
    write_site(project)
    set_domain(project, 'acme', 'erp.acme.com')
    main(['install', 'notes', '--instance', 'acme', '--trust', '--no-host', '--project', str(project)])
    monkeypatch.setattr(proxy, 'check', functools.partial(proxy.check, resolve=lambda host, port, proto: HERE))
    return project


def test_evlet_host_live_adds_checks_lists_disables_and_removes_without_routes_py(web, capsys, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **options: [])
    main(['host', 'add', 'acme', 'www.acme.com', '--root', 'notes.www', '--live', '--ip', '203.0.113.7', '--project', str(web)])
    out = capsys.readouterr().out
    assert 'Added www.acme.com to the hosts of acme, served by notes.www. It is pending' in out
    assert 'checks pending hosts every minute' in out and not (web / 'instances/acme/routes.py').exists()
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **options: HERE)
    main(['host', 'check', 'acme', '--ip', '203.0.113.7', '--project', str(web)])
    out = capsys.readouterr().out
    assert out.splitlines()[1].split()[:3] == ['www.acme.com', 'notes.www', 'active'] and 'With Caddy on-demand TLS nothing else is needed' in out
    main(['host', 'list', 'acme', '--project', str(web)])
    listed = capsys.readouterr().out
    assert len(listed.split('\n\n')) == 2
    assert 'www.acme.com' not in listed.split('\n\n')[0] and 'www.acme.com   notes.www   active' in listed
    # evlet proxy writes the active host from data into the Caddy file, like a routes.py sub-host.
    main(['proxy', 'acme', '--caddy', '--project', str(web)])
    assert 'www.acme.com {' in capsys.readouterr().out
    app = runtime(web)
    try:
        assert app.routes.public('www.acme.com') and http(app, 'GET', '/about/', headers={b'host': b'www.acme.com'})[0] == 200
    finally:
        app.database.close()
    with pytest.raises(SystemExit):
        main(['host', 'add', 'acme', 'www.acme.com', '--root', 'notes.www', '--routes-only', '--project', str(web)])
    assert 'is a host from data' in capsys.readouterr().err
    main(['host', 'disable', 'acme', 'www.acme.com', '--project', str(web)])
    assert 'Disabled www.acme.com' in capsys.readouterr().out
    main(['host', 'enable', 'acme', 'www.acme.com', '--project', str(web)])
    assert 'pending again' in capsys.readouterr().out
    main(['host', 'remove', 'acme', 'www.acme.com', '--project', str(web)])
    assert 'Removed www.acme.com from the hosts of acme' in capsys.readouterr().out
    main(['host', 'check', 'acme', '--project', str(web)])
    assert 'has no hosts from data to check' in capsys.readouterr().out


# ---------- Controls, Routing ----------

def test_controls_adds_checks_and_removes_hosts_from_data_for_admins(tmp_path, monkeypatch, hashed):
    from examples.playground.app import build_app, playground_routes
    from examples.playground.migrate import configured_database
    from test_controls import add_user, on_host
    add_user.hashed = hashed
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'controls.sqlite3'))
    routes = playground_routes(8000)
    database = configured_database()
    app = build_app(database, LocalAuth(database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False), routes=routes)
    app.initialize()
    try:
        for name, role in (('admin', 'admin'), ('buyer', 'portal')):
            add_user(app, name, role)
        admin = on_host(app, 'admin', b'127.0.0.1:8000')
        assert admin('GET', '/api/_controls/routing')[1]['data_hosts'] == []
        status, body, _ = admin('POST', '/api/_controls/routing/hosts', {'host': 'site.localhost', 'target': 'playground.site'})
        assert status == 422 and 'routes.py' in body['fields']['host']
        status, body, _ = admin('POST', '/api/_controls/routing/hosts', {'host': 'Shop.localhost', 'target': 'playground.site'})
        assert status == 200 and [(row['host'], row['status']) for row in body['items']] == [('shop.localhost', 'pending')]
        monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **options: [(2, 1, 6, '', ('127.0.0.1', 0))])
        monkeypatch.setattr(hosts, 'server_addresses', lambda: {'127.0.0.1'})
        status, body, _ = admin('POST', '/api/_controls/routing/hosts/shop.localhost/check', {})
        assert status == 200 and body['items'][0]['status'] == 'active' and body['items'][0]['served']
        assert admin('POST', '/api/_controls/routing/hosts/shop.localhost/check', {})[0] == 429
        routing = admin('GET', '/api/_controls/routing')[1]
        assert {'host': 'shop.localhost', 'primary': False, 'sub': True, 'realm': 'public', 'data': True} in routing['hosts']
        assert ('shop.localhost', '/', 'playground.site', 'data') in [(row['host'], row['path'], row['serves'], row['source']) for row in routing['rows']]
        # The new host passes the Host check at once, without a restart.
        assert http(app, 'GET', '/', headers={b'host': b'shop.localhost:8000'})[0] != 400
        assert app.business.auth.hosts >= {b'shop.localhost:8000'}
        buyer = on_host(app, 'buyer', b'portal.localhost:8000')
        assert buyer('POST', '/api/_controls/routing/hosts', {'host': 'x.localhost', 'target': 'playground.site'})[0] == 404
        status, body, _ = admin('POST', '/api/_controls/routing/hosts/shop.localhost/status', {'status': 'disabled'})
        assert status == 200 and body['items'][0]['status'] == 'disabled'
        assert b'shop.localhost:8000' not in app.business.auth.hosts
        status, body, _ = admin('DELETE', '/api/_controls/routing/hosts/shop.localhost')
        assert status == 200 and body['items'] == []
        assert admin('DELETE', '/api/_controls/routing/hosts/shop.localhost')[0] == 422
        areas = [row['area'] for row in admin('GET', '/api/_controls/audit?area=Hosts')[1]['items']]
        assert areas == ['Hosts'] * 4
        # A row the table skips is listed under Needs attention on the Overview.
        with app.database.transaction() as tx:
            tx.connection.execute("INSERT INTO evlet_hosts(host, target, status, records, created_at, created_by, changed_at) "
                                  "VALUES ('gone.localhost', 'old.site', 'active', '', 0, 'test', 0)")
            tx.connection.execute('UPDATE evlet_hosts_version SET version = version + 1')
        app.live_routes.refresh(force=True)
        attention = admin('GET', '/api/_controls/state')[1]['attention']
        assert {'tone': 'amber', 'icon': 'globe', 'page': 'routing', 'title': 'gone.localhost is not served',
                'text': 'old.site is not a frontend of an installed app. Fix or remove it with evlet host remove, or in Controls, Routing.'} in attention
    finally:
        app.database.close()
