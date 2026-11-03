"""Sub-hosts, sign-in realms, API scopes, static and server frontends, public endpoints and evlet proxy."""

import json
import re
import threading

import pytest

from evlet import (ApiScope, AppDefinition, Application, Database, Docs, Frontend, Host, LocalAuth, Policy, Request, Response, Rule,
                   own_customer)
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.cli import proxy
from evlet.routing import resolve_routes
from evlet.services import Endpoint
from evlet.ui.serving import STATIC
from examples.playground.app import build_app, playground_routes
from examples.playground.migrate import configured_database
from examples.playground.models import Customer, Invoice, Project, registry
from test_business import PASSWORD, hashed, http
from test_cli_lifecycle import environment_stub, project, runtime

DESK, PORTAL = b'127.0.0.1:8000', b'portal.localhost:8000'
built = pytest.mark.skipif(not (STATIC / 'index.html').is_file(), reason='Build browser assets before checking the desk shell')


def config(page):
    found = re.search(rb'<script id="evlet-config" type="application/json">(.*?)</script>', page)
    return json.loads(found[1])


@pytest.fixture
def portal(tmp_path, monkeypatch, hashed):
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'portal.sqlite3'))
    database = configured_database()
    routes = playground_routes(8000)
    auth = LocalAuth(database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False)
    app = build_app(database, auth, routes=routes)
    app.initialize()
    with app.database.transaction() as tx:
        for name, role in (('admin', 'admin'), ('viewer', 'viewer'), ('buyer', 'portal')):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (name, hashed, role))
    yield app
    app.database.close()


def sign_in(app, username, host):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': username, 'password': PASSWORD}, headers={b'host': host})
    if status != 200:
        return status, data, None
    cookie = headers[b'set-cookie'].decode()
    def call(method, path, data=None, **kwargs):
        return http(app, method, path, data, cookie=cookie.split(';')[0], csrf=call.csrf, headers={b'host': host}, **kwargs)
    call.csrf, call.cookie = data['csrf'], cookie
    return status, data, call


def test_a_sub_host_serves_its_root_frontend_with_the_config_block(portal):
    status, page, headers = http(portal, 'GET', '/', headers={b'host': PORTAL})
    assert status == 200 and headers[b'content-type'] == b'text/html; charset=utf-8'
    assert config(page) == {'frontend': 'playground.portal', 'base': '/', 'api': '/api', 'realm': 'portal',
                            'host': 'portal.localhost', 'docs': None, 'theme': 'system', 'version': config(page)['version']}
    assert b'<base href="/">' in page and b"frame-ancestors 'none'" in headers[b'content-security-policy']
    assert http(portal, 'GET', '/invoices/12', headers={b'host': PORTAL})[1] == page
    status, script, headers = http(portal, 'GET', '/portal.js', headers={b'host': PORTAL})
    assert status == 200 and headers[b'content-type'] == b'text/javascript; charset=utf-8' and b'evlet-config' in script
    status, page, _ = http(portal, 'GET', '/portal/invoices', headers={b'host': DESK})
    assert status == 200 and b'<base href="/portal/">' in page and config(page)['base'] == '/portal'
    # The primary host is a desk host, so the portal page there reports the desk realm.
    assert config(page)['realm'] == 'desk'


@pytest.mark.parametrize('path', ['/controls', '/controls/users', '/CONTROLS', '/docs', '/docs/routes', '/app/customer', '/settings/accounts', '/_/mcp', '/reports/x'])
def test_framework_pages_answer_404_on_a_sub_host(portal, path):
    assert http(portal, 'GET', path, headers={b'host': PORTAL})[0] == 404


@pytest.mark.parametrize('method,path', [('GET', '/api/project'), ('GET', '/api/schema'), ('GET', '/api/auth/users'), ('POST', '/api/auth/setup'),
                                         ('GET', '/api/ui'), ('GET', '/api/_events/stream'), ('GET', '/api/_discovery'), ('POST', '/api/_draft/project'),
                                         ('POST', '/api/invoice/1/actions/send'), ('GET', '/api/_services/playground.anything'), ('GET', '/api')])
def test_the_api_outside_the_scope_is_404_on_a_sub_host(portal, method, path):
    _, _, buyer = sign_in(portal, 'buyer', PORTAL)
    assert buyer(method, path)[0] == 404
    assert http(portal, method, path, headers={b'host': PORTAL})[0] == 404


def test_scoped_models_and_auth_endpoints_stay_available_on_a_sub_host(portal):
    assert http(portal, 'GET', '/api/auth/session', headers={b'host': PORTAL})[1]['user'] is None
    assert http(portal, 'GET', '/api/customer', headers={b'host': PORTAL})[0] == 401
    assert http(portal, 'GET', '/api/project')[0] == 401


def test_portal_and_desk_users_sign_in_only_on_their_own_hosts(portal):
    wrong = {'error': 'Invalid username or password'}
    assert sign_in(portal, 'buyer', DESK)[:2] == (401, wrong)
    assert sign_in(portal, 'admin', PORTAL)[:2] == (401, wrong)
    assert sign_in(portal, 'viewer', PORTAL)[:2] == (401, wrong)
    status, data, buyer = sign_in(portal, 'buyer', PORTAL)
    assert status == 200 and data['user']['role'] == 'portal'
    assert sign_in(portal, 'admin', DESK)[0] == 200
    with portal.database._connection() as connection:
        assert {row[0] for row in connection.execute('SELECT realm FROM evlet_sessions')} == {'desk', 'portal'}


def test_portal_and_desk_cookies_are_host_only_and_separate(portal):
    _, _, buyer = sign_in(portal, 'buyer', PORTAL)
    _, _, admin = sign_in(portal, 'admin', DESK)
    assert buyer.cookie.startswith('evlet_portal_session=') and admin.cookie.startswith('evlet_session=')
    for cookie in (buyer.cookie, admin.cookie):
        assert 'domain' not in cookie.lower() and 'Path=/' in cookie and 'HttpOnly' in cookie
    portal_token, desk_token = buyer.cookie.split(';')[0].split('=')[1], admin.cookie.split(';')[0].split('=')[1]
    assert http(portal, 'GET', '/api/auth/session', cookie=buyer.cookie.split(';')[0], headers={b'host': DESK})[1]['user'] is None
    assert http(portal, 'GET', '/api/auth/session', cookie=admin.cookie.split(';')[0], headers={b'host': PORTAL})[1]['user'] is None
    # A session token moved to the other realm's cookie is still refused.
    assert http(portal, 'GET', '/api/auth/session', cookie='evlet_portal_session=' + desk_token, headers={b'host': PORTAL})[1]['user'] is None
    assert http(portal, 'GET', '/api/auth/session', cookie='evlet_session=' + portal_token, headers={b'host': DESK})[1]['user'] is None
    assert buyer('GET', '/api/auth/session')[1]['user']['username'] == 'buyer'
    assert admin('GET', '/api/auth/session')[1]['user']['username'] == 'admin'
    status, _, headers = buyer('POST', '/api/auth/logout')
    assert status == 204 and headers[b'set-cookie'].startswith(b'evlet_portal_session=;')
    assert admin('GET', '/api/auth/session')[1]['user']['username'] == 'admin'


def test_own_customer_scope_reads_evlet_user_links(portal):
    acme = portal.database.create(Customer, name='Acme', email='buyer@acme.test')
    other = portal.database.create(Customer, name='Other')
    mine = portal.database.create(Invoice, customer=acme.id, reference='INV-1', total='10.00')
    theirs = portal.database.create(Invoice, customer=other.id, reference='INV-2', total='20.00')
    auth = portal.business.auth
    buyer_id = next(user['id'] for user in auth.users() if user['username'] == 'buyer')
    _, _, buyer = sign_in(portal, 'buyer', PORTAL)
    assert buyer('GET', '/api/invoice')[1]['items'] == []
    auth.link_user(buyer_id, Customer, acme.id)
    auth.link_user(buyer_id, 'customer', acme.id)
    assert auth.user_links(buyer_id) == (('customer', acme.id),)
    assert [item['reference'] for item in buyer('GET', '/api/invoice')[1]['items']] == ['INV-1']
    assert [item['name'] for item in buyer('GET', '/api/customer')[1]['items']] == ['Acme']
    assert buyer('GET', f'/api/invoice/{mine.id}')[1]['customer'] == acme.id
    assert buyer('GET', f'/api/invoice/{theirs.id}')[0] == 404
    assert buyer('POST', '/api/customer', {'name': 'Mine now'})[0] == 403
    assert set(buyer('GET', f'/api/customer/{acme.id}')[1]) == {'id', 'name', 'email'}
    auth.unlink_user(buyer_id, Customer, acme.id)
    assert buyer('GET', '/api/invoice')[1]['items'] == []
    from evlet.auth import AccessError
    with pytest.raises(AccessError):
        auth.link_user(buyer_id, Customer, 999)
    with pytest.raises(AccessError):
        auth.link_user(999, Customer, acme.id)
    with pytest.raises(ValueError):
        auth.link_user(buyer_id, 'nothing', acme.id)
    assert own_customer()(type('U', (), {'links': (('customer', 3), ('project', 4))})()) == {'customer__in': [3]}


def test_portal_role_sees_nothing_without_an_app_rule(portal):
    project = portal.database.create(Project, name='Hidden')
    policy = portal.business.policy
    from evlet import User
    assert not policy.allows(User(1, 'buyer', 'portal'), Project, 'read')
    assert not policy.allows(User(0, 'public', 'public'), Project, 'read')
    assert policy.allows(User(1, 'viewer', 'viewer'), Project, 'read') and project.id


def static_app(tmp_path, *, routes_module=None, frontends=(), roots=True):
    dist = tmp_path / 'shop/dist'
    dist.mkdir(parents=True)
    (dist / 'index.html').write_text('<!doctype html><html><head><title>Shop</title><script id="evlet-config" type="application/json">{"base":"/old"}</script></head><body></body></html>')
    (dist / 'app.js').write_text('console.log(1)')
    (dist / 'logo.png').write_bytes(b'\x89PNG')
    (dist / 'notes.txt').write_text('private')
    (dist / '.env').write_text('SECRET=1')
    (tmp_path / 'shop/secret.js').write_text('outside')
    (dist / 'link.js').symlink_to(tmp_path / 'shop/secret.js')
    shop = Frontend('shop', kind='static', dist='dist', path='/shop', auth=None, csp={'connect-src': ('https://maps.example.com',)})
    definition = AppDefinition(frontends=(shop, *frontends))
    routes = resolve_routes([('shop', definition)], None, routes_module, roots={'shop': tmp_path / 'shop'} if roots else None)
    return Application(Database(tmp_path / 'static.sqlite3', registry), routes=routes)


def test_static_frontend_files_types_traversal_and_fallback(tmp_path):
    app = static_app(tmp_path)
    status, page, headers = http(app, 'GET', '/shop/orders/12')
    assert status == 200 and page.count(b'evlet-config') == 1 and config(page)['base'] == '/shop' and b'<base href="/shop/">' in page
    assert headers[b'content-security-policy'].count(b"connect-src 'self' https://maps.example.com;") == 1
    assert http(app, 'GET', '/shop')[1] == page
    assert http(app, 'GET', '/shop/app.js')[2][b'content-type'] == b'text/javascript; charset=utf-8'
    assert http(app, 'GET', '/shop/logo.png')[2][b'content-type'] == b'image/png'
    for path in ('/shop/notes.txt', '/shop/.env', '/shop/../shop/dist/app.js', '/shop/../secret.js', '/shop/link.js',
                 '/shop/missing.js', '/shop/a\\b', '/shop/%2e%2e/secret.js', '/shop/app.py'):
        assert http(app, 'GET', path)[0] == 404, path
    assert http(static_app(tmp_path / 'unbuilt', roots=False), 'GET', '/shop')[0] == 404


def test_server_frontend_handlers_run_in_a_worker_thread(tmp_path):
    seen = []
    def render(request):
        seen.append((request, threading.current_thread() is threading.main_thread()))
        return Response('<html><head></head><body>Hello</body></html>') if request.path != '/text' else 'plain <b>text</b>'
    site = Frontend('site', kind='server', handler=render, path='/site', auth=None)
    app = static_app(tmp_path, frontends=(site,))
    status, page, headers = http(app, 'GET', '/site/hello?x=1&y=')
    assert status == 200 and b'Hello' in page and config(page)['frontend'] == 'shop.site'
    request, on_main = seen[0]
    assert request == Request('/hello', 'x=1&y=', '127.0.0.1', None, {'x': '1', 'y': ''}) and not on_main
    assert http(app, 'GET', '/site/text')[1].startswith(b'<script id="evlet-config"')
    with pytest.raises(ValueError):
        Response(None)


def test_server_frontend_with_a_desk_realm_redirects_to_sign_in(portal, tmp_path):
    def render(request):
        return f'<p>{request.user.username}</p>'
    staff = Frontend('staff', kind='server', handler=render, path='/staff')
    routes = resolve_routes([('ops', AppDefinition(frontends=(staff,)))])
    auth = LocalAuth(portal.database, origins=routes.origins(), secure_cookies=False)
    app = Application(portal.database, auth=auth, routes=routes)
    status, _, headers = http(app, 'GET', '/staff/x')
    assert status == 302 and headers[b'location'] == b'/login?next=/staff/x'
    _, _, admin = sign_in(app, 'admin', DESK)
    status, page, _ = admin('GET', '/staff/x')
    assert status == 200 and b'<p>admin</p>' in page


def test_public_endpoints_are_opt_in_and_rate_limited_per_address(portal):
    def count(context) -> dict:
        return {'customers': len(context.list(Customer)['rows']), 'user': context.user.role}
    public = Endpoint('playground.count', count, 'GET', (Customer, 'read'), public=True)
    private = Endpoint('playground.private', count, 'GET', (Customer, 'read'))
    policy = Policy(registry, rules={('public', Customer): Rule(('read',), read_fields=('name',))})
    auth = LocalAuth(portal.database, origins=portal.routes.origins(), secure_cookies=False)
    app = Application(portal.database, auth=auth, policy=policy, apis=(public, private))
    app.initialize()
    portal.database.create(Customer, name='Acme')
    assert http(app, 'GET', '/api/_services/playground.private')[0] == 401
    for _ in range(30):
        status, body, _ = http(app, 'GET', '/api/_services/playground.count')
        assert status == 200 and body == {'customers': 1, 'user': 'public'}
    assert http(app, 'GET', '/api/_services/playground.count')[:2] == (429, {'error': 'Too many requests. Try again later.'})
    assert http(app, 'GET', '/api/_services/playground.count', client='10.0.0.2')[0] == 200
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_public_limits SET calls=0 WHERE period=60')
        tx.connection.execute('UPDATE evlet_public_limits SET calls=300 WHERE period=3600')
    assert http(app, 'GET', '/api/_services/playground.count')[0] == 429
    _, _, admin = sign_in(app, 'admin', DESK)
    assert admin('GET', '/api/_services/playground.count')[0] == 429
    with pytest.raises(ValueError):
        from evlet import api
        api(permission=(Customer, 'read'), public='yes')


def test_desk_shell_reads_its_base_from_the_config_block(portal):
    if not (STATIC / 'index.html').is_file():
        pytest.skip('Build browser assets before checking the desk shell')
    status, page, _ = http(portal, 'GET', '/app/customer')
    assert status == 200 and config(page) == {'frontend': 'evlet.desk', 'base': '/app', 'api': '/api', 'realm': 'desk',
                                               'host': '127.0.0.1', 'docs': '/docs', 'theme': 'system', 'version': config(page)['version']}


@built
def test_docs_move_and_limit_to_roles(portal):
    routes = resolve_routes((), None, {'docs': Docs(path='/help', roles=('admin',))})
    auth = LocalAuth(portal.database, origins=routes.origins(), secure_cookies=False)
    app = build_app(portal.database, auth, routes=routes)
    assert [row.path for row in routes.rows() if row.serves == 'evlet.docs'] == ['/help']
    status, _, headers = http(app, 'GET', '/help/routes')
    assert status == 302 and headers[b'location'] == b'/login?next=/help/routes'
    assert sign_in(app, 'viewer', DESK)[2]('GET', '/help')[0] == 404
    status, page, _ = sign_in(app, 'admin', DESK)[2]('GET', '/help')
    assert status == 200 and config(page)['docs'] == '/help'
    assert http(app, 'GET', '/docs')[0] == 404
    for path in ('/app', '/api', '/_x'):
        with pytest.raises(ValueError):
            Docs(path=path)
    with pytest.raises(ValueError, match='crm.help and evlet.docs both want /help|evlet.docs and crm.help both want /help'):
        resolve_routes([('crm', AppDefinition(frontends=(Frontend('help', path='/help'),)))], None, {'docs': Docs(path='/help')})


def test_view_registry_resolves_generated_routes_under_a_frontend_base(portal):
    from evlet import User
    schema = portal.business.schema(User(1, 'admin', 'admin'))
    assert portal.ui.resolve('/app/customer/3', schema)['params'] == {'id': '3'}
    assert portal.ui.resolve('/crm/customer/new', schema, frontend='crm.crm', base='/crm')['view']['kind'] == 'form'
    with pytest.raises(LookupError):
        portal.ui.resolve('/app/customer', schema, frontend='crm.crm', base='/crm')


def test_sub_host_declarations_are_checked():
    shop = Frontend('shop', kind='static', dist='dist', path='/shop', auth='portal', api=('shop.orders',))
    desk = Frontend('desk', path='/desk')
    definition = AppDefinition(frontends=(shop, desk), api_scopes=(ApiScope('orders', models=(Customer,)),))
    def resolve(hosts, primary=None, definition=definition):
        return resolve_routes([('shop', definition)], None, {'hosts': hosts, **({'primary': primary} if primary else {})})
    table = resolve((Host('shop.acme.com', root=shop),), Host('erp.acme.com'))
    assert table.origins() == ('https://erp.acme.com', 'https://shop.acme.com') and table.realms() == {'https://shop.acme.com': 'portal'}
    assert table.match(b'shop.acme.com', '/assets/x.js') == ('shop.shop', '/', '/assets/x.js')
    assert table.match(b'erp.acme.com', '/shop') == ('evlet.desk', '/', '/shop')
    assert table.redirect(b'shop.acme.com', '/') is None and table.scopes(b'erp.acme.com') is None
    assert resolve((Host('shop.acme.com', root=shop, also_on_primary=True),), Host('erp.acme.com')).match(b'erp.acme.com', '/shop/x') == ('shop.shop', '/shop', '/x')
    for hosts, message in (((Host('shop.localhost'),), 'needs root='), ((Host('desk.localhost', root=desk),), 'evlet frontends stay on the primary host'),
                           ((Host('shop.localhost', root=shop), Host('shop.localhost', root=shop)), 'listed twice')):
        with pytest.raises(ValueError, match=message):
            resolve(hosts)
    with pytest.raises(ValueError, match='uses API scope shop.orders, which no installed app defines'):
        resolve_routes([('shop', AppDefinition(frontends=(shop,)))])
    with pytest.raises(ValueError, match='pairs of its own models'):
        ApiScope('orders', models=(Customer,), actions=((Invoice, 'send'),))
    with pytest.raises(ValueError, match='Register ApiScope'):
        AppDefinition(api_scopes=('orders',))


def test_tls_allowed_answers_loopback_callers_for_this_instance_hosts(tmp_path):
    shop = Frontend('shop', kind='static', dist='dist', path='/shop', auth=None)
    routes = resolve_routes([('shop', AppDefinition(frontends=(shop,)))], None, {'primary': Host('erp.acme.com'), 'hosts': (Host('shop.acme.com', root=shop),)})
    app = Application(Database(tmp_path / 'tls.sqlite3', registry), routes=routes)
    assert http(app, 'GET', '/_/tls-allowed?domain=shop.acme.com')[:2] == (200, {'allowed': 'shop.acme.com'})
    assert http(app, 'GET', '/_/tls-allowed?domain=erp.acme.com')[0] == 200
    assert http(app, 'GET', '/_/tls-allowed?domain=evil.example')[0] == 404
    assert http(app, 'GET', '/_/tls-allowed?domain=shop.acme.com', client='203.0.113.9')[0] == 404


ROUTES = '''HOST              PATH        SERVES                 SIGN-IN   SOURCE
erp.acme.com      /controls   evlet.controls         control   locked
erp.acme.com      /settings   evlet.settings         desk      locked
erp.acme.com      /app        evlet.desk             desk      locked
erp.acme.com      /reports    evlet.reports          desk      locked
erp.acme.com      /login      evlet.login            public    locked
erp.acme.com      /assets     evlet.assets           public    locked
erp.acme.com      /health     evlet.health           public    locked
erp.acme.com      /api/*      evlet.api              desk      locked
erp.acme.com      /board      notes.board            desk      app notes
erp.acme.com      /           -> /board              desk      routes.py
portal.acme.com   /           notes.portal           portal    routes.py
portal.acme.com   /api/*      notes.items (scoped)   portal    routes.py
'''

CADDY = '''# Evlet reads client addresses from X-Forwarded-For only from trusted proxies. Add this line to Instance(...)
# in instances/acme/config.py, then restart Evlet:
#     trusted_proxies=("127.0.0.1", "::1"),

erp.acme.com {
\t@internal path /_/*
\trespond @internal 404
\treverse_proxy 127.0.0.1:8000
}

portal.acme.com {
\t@internal path /_/*
\trespond @internal 404
\t@primary_only path_regexp (?i)^/(controls|docs)(/|$)
\trespond @primary_only 404
\treverse_proxy 127.0.0.1:8000
}
'''


def write_portal(project):
    package = project / 'apps/notes/notes'
    (package / 'frontends.py').write_text('from evlet import Frontend\n\nboard = Frontend("board", path="/board", title="Board")\n'
                                          'portal = Frontend("portal", kind="static", dist="portal/dist", path="/portal", auth="portal")\n')
    hooks = package / 'hooks.py'
    hooks.write_text(hooks.read_text().replace('from .migrations import migrations', 'from .migrations import migrations\nfrom .frontends import board, portal')
                     .replace('migrations=migrations)', 'migrations=migrations, frontends=(board, portal))'))
    (package / 'portal/dist').mkdir(parents=True)
    (package / 'portal/dist/index.html').write_text('<html><head></head><body>Notes portal</body></html>')
    frontends = package / 'frontends.py'
    frontends.write_text(frontends.read_text().replace('auth="portal")', 'auth="portal", api=("notes.items",))'))
    hooks = package / 'hooks.py'
    hooks.write_text('from evlet import ApiScope\n' + hooks.read_text().replace('frontends=(board, portal))', 'frontends=(board, portal), api_scopes=(ApiScope("items", models=models),))'))
    (project / 'instances/acme/routes.py').write_text('from evlet import Host\nfrom notes.frontends import board, portal\n\n'
                                                       'primary = Host("erp.acme.com", root=board)\nhosts = (Host("portal.acme.com", root=portal),)\n')


def test_routes_proxy_and_link_user_commands(project, capsys, tmp_path, hashed):
    write_portal(project)
    install(project, 'acme', app_name='notes', trust=True)
    main(['routes', 'acme', '--project', str(project)])
    assert capsys.readouterr().out == ROUTES
    main(['proxy', 'acme', '--caddy', '--project', str(project)])
    assert capsys.readouterr().out == CADDY
    main(['proxy', 'acme', '--nginx', '--project', str(project)])
    nginx = capsys.readouterr().out
    assert nginx.count('location ~* ^/(controls|docs)(/|$) { return 404; }') == 1 and 'server_name erp.acme.com portal.acme.com;' in nginx
    assert nginx.count('location ^~ /_/ { return 404; }') == 2 and 'proxy_http_version 1.1;' in nginx and 'proxy_read_timeout 3600s;' in nginx
    assert 'trusted_proxies' in nginx
    target = tmp_path / 'Caddyfile'
    main(['proxy', 'acme', '--caddy', '--write', str(target), '--project', str(project)])
    out = capsys.readouterr().out
    assert target.read_text() == CADDY and 'Reload the proxy yourself' in out
    assert 'Add this line to Instance(...) in instances/acme/config.py, then restart Evlet:\n    trusted_proxies=("127.0.0.1", "::1"),' in out
    assert target.stat().st_mode & 0o777 == 0o644
    with pytest.raises(SystemExit):
        main(['proxy', 'acme', '--caddy', '--port', '70000', '--project', str(project)])
    assert 'port between 1 and 65535' in capsys.readouterr().err
    application = runtime(project)
    try:
        report, ok = proxy.check(application.routes, ['203.0.113.5'], resolve=lambda host, port, proto: [(0, 0, 0, '', ('203.0.113.5' if host == 'erp.acme.com' else '198.51.100.7', 0))])
        assert not ok and re.search(r'erp\.acme\.com +203\.0\.113\.5 +points here', report) and 'portal.acme.com   198.51.100.7' in report
        assert proxy.check(application.routes, ['203.0.113.5'], resolve=lambda *args, **kwargs: [(0, 0, 0, '', ('203.0.113.5', 0))])[1]
        status, page, _ = http(application, 'GET', '/anything', headers={b'host': b'portal.acme.com'})
        assert status == 200 and b'Notes portal' in page and config(page)['realm'] == 'portal'
        assert http(application, 'GET', '/controls', headers={b'host': b'portal.acme.com'})[0] == 404
        primary = {b'host': b'erp.acme.com'}
        status, _, headers = http(application, 'GET', '/', headers=primary)
        assert status == 302 and headers[b'location'] == b'/board'
        assert http(application, 'GET', '/board/unknown', headers=primary)[0] == 404
        assert http(application, 'GET', '/portal', headers=primary)[0] == 404
        if (STATIC / 'index.html').is_file():
            status, page, _ = http(application, 'GET', '/board/notesitem/new', headers=primary)
            assert status == 200 and config(page)['frontend'] == 'notes.board' and config(page)['base'] == '/board'
        from notes.models import NotesItem
        item = application.database.create(NotesItem, title='First')
        with application.database.transaction() as tx:
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', ('buyer', hashed, 'portal'))
    finally:
        application.database.close()
    main(['link-user', 'acme', 'buyer', 'notesitem', str(item.id), '--project', str(project)])
    assert capsys.readouterr().out == f'Linked buyer to notesitem {item.id}\n'
    with pytest.raises(SystemExit):
        main(['link-user', 'acme', 'nobody', 'notesitem', str(item.id), '--project', str(project)])
    assert 'No account named nobody' in capsys.readouterr().err
    application = runtime(project)
    try:
        assert application.business.auth.user_links(1) == (('notesitem', item.id),)
    finally:
        application.database.close()
    main(['link-user', 'acme', 'buyer', 'notesitem', str(item.id), '--remove', '--project', str(project)])
    assert capsys.readouterr().out.startswith('Unlinked buyer')


def ops_app(portal, *frontends, trusted_proxies=(), apis=(), policy=None, routes_module=None):
    routes = resolve_routes([('ops', AppDefinition(frontends=frontends))], None, routes_module)
    auth = LocalAuth(portal.database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False, trusted_proxies=trusted_proxies)
    app = Application(portal.database, auth=auth, routes=routes, apis=apis, policy=policy)
    app.initialize()
    return app


def test_frontends_need_the_matching_realm_and_role(portal):
    def render(request):
        return f'<p>{request.user.username if request.user else "-"}</p>'
    shop = Frontend('shop', kind='server', handler=render, path='/shop', auth='portal')
    staff = Frontend('staff', kind='server', handler=render, path='/staff', roles=('admin',))
    crm = Frontend('crm', path='/crm', roles=('admin',))
    app = ops_app(portal, shop, staff, crm)
    _, _, viewer = sign_in(app, 'viewer', DESK)
    _, _, admin = sign_in(app, 'admin', DESK)
    # A desk user counts as signed out on a portal frontend, whatever their role.
    assert viewer('GET', '/shop/x')[0] == 401 and admin('GET', '/shop/x')[0] == 401
    assert viewer('GET', '/staff/x')[0] == 404
    status, page, _ = admin('GET', '/staff/x')
    assert status == 200 and b'<p>admin</p>' in page
    status, _, headers = http(app, 'GET', '/crm/customer')
    assert status == 302 and headers[b'location'] == b'/login?next=/crm/customer'
    assert viewer('GET', '/crm/customer')[0] == 404
    if (STATIC / 'index.html').is_file():
        assert admin('GET', '/crm/customer')[0] == 200
    with pytest.raises(ValueError, match='roles need a sign-in realm'):
        Frontend('site', kind='server', handler=render, path='/site', auth=None, roles=('admin',))


def test_auth_realms_must_match_the_route_table(portal):
    with pytest.raises(ValueError, match='LocalAuth realms must match the route table'):
        Application(portal.database, auth=LocalAuth(portal.database, origins=portal.routes.origins(), secure_cookies=False), routes=portal.routes)


def test_client_address_trusts_only_configured_proxies():
    from evlet import Instance
    from evlet.auth import client_address
    def scope(peer, *forwarded):
        return {'client': (peer, 1), 'headers': [(b'x-forwarded-for', value.encode()) for value in forwarded]}
    trusted = ('127.0.0.1', '::1', '10.0.0.0/8')
    assert client_address(scope('203.0.113.9', '198.51.100.7')) == '203.0.113.9'
    assert client_address(scope('127.0.0.1', '198.51.100.7')) == '127.0.0.1'
    assert client_address(scope('127.0.0.1', '198.51.100.7'), trusted) == '198.51.100.7'
    assert client_address(scope('127.0.0.1', '6.6.6.6, 198.51.100.7'), trusted) == '198.51.100.7'
    assert client_address(scope('127.0.0.1', '198.51.100.7, 10.1.2.3'), trusted) == '198.51.100.7'
    assert client_address(scope('127.0.0.1', '6.6.6.6', '198.51.100.7'), trusted) == '198.51.100.7'
    assert client_address(scope('127.0.0.1', '6.6.6.6, garbage'), trusted) == '127.0.0.1'
    assert client_address(scope('::1'), trusted) == '::1'
    assert client_address(scope('203.0.113.9', '127.0.0.1'), trusted) == '203.0.113.9'
    assert Instance('acme', trusted_proxies=('127.0.0.1', '10.0.0.0/8')).trusted_proxies == ('127.0.0.1/32', '10.0.0.0/8')
    with pytest.raises(ValueError, match='trusted_proxies'):
        Instance('acme', trusted_proxies=('proxy.example',))


def test_public_limit_counts_each_endpoint_and_client_behind_a_trusted_proxy(portal):
    def count(context) -> dict:
        return {'ok': True}
    endpoints = (Endpoint('ops.one', count, 'GET', (Customer, 'read'), public=True), Endpoint('ops.two', count, 'GET', (Customer, 'read'), public=True))
    policy = Policy(registry, rules={('public', Customer): Rule(('read',))})
    app = ops_app(portal, trusted_proxies=('127.0.0.1',), apis=endpoints, policy=policy)
    forwarded = {b'x-forwarded-for': b'198.51.100.7'}
    for _ in range(30):
        assert http(app, 'GET', '/api/_services/ops.one', headers=forwarded)[0] == 200
    status, _, headers = http(app, 'GET', '/api/_services/ops.one', headers=forwarded)
    assert status == 429 and 0 < int(headers[b'retry-after']) <= 60
    # A spoofed address on the left is ignored; the proxy's own entry decides.
    assert http(app, 'GET', '/api/_services/ops.one', headers={b'x-forwarded-for': b'6.6.6.6, 198.51.100.7'})[0] == 429
    assert http(app, 'GET', '/api/_services/ops.two', headers=forwarded)[0] == 200
    assert http(app, 'GET', '/api/_services/ops.one', headers={b'x-forwarded-for': b'198.51.100.8'})[0] == 200
    direct = ops_app(portal, apis=endpoints, policy=policy)
    with direct.database.transaction() as tx:
        tx.connection.execute('DELETE FROM evlet_public_limits')
    for index in range(30):
        assert http(direct, 'GET', '/api/_services/ops.one', headers={b'x-forwarded-for': f'198.51.100.{index}'.encode()})[0] == 200
    assert http(direct, 'GET', '/api/_services/ops.one', headers={b'x-forwarded-for': b'203.0.113.1'})[0] == 429


def test_login_throttling_uses_the_forwarded_client(portal):
    app = ops_app(portal, trusted_proxies=('127.0.0.1',))
    def attempt(address):
        return http(app, 'POST', '/api/auth/login', {'username': 'admin', 'password': 'wrong password for this account'},
                    headers={b'x-forwarded-for': address.encode()})[0]
    assert [attempt('198.51.100.7') for _ in range(5)] == [401] * 5
    assert attempt('198.51.100.7') == 429
    assert attempt('198.51.100.8') == 401


def test_api_scope_lists_each_route_family(portal):
    from evlet.app import HTTPError
    def allowed(app, path, method='GET'):
        try:
            app._api_scope({'path': path, 'method': method, 'headers': [(b'host', PORTAL)]})
            return True
        except HTTPError:
            return False
    paths = ('/api/invoice', '/api/invoice/1', '/api/invoice/1/void', '/api/invoice/1/revise', '/api/invoice/1/unpost', '/api/invoice/1/repost',
             '/api/invoice/1/actions/send',
             '/api/invoice/1/moves/send', '/api/invoice/measures', '/api/invoice/aggregate', '/api/invoice/1/measures',
             '/api/invoice/1/related/payment', '/api/invoice/1/lines/rows', '/api/invoice/1/attachments', '/api/_draft/invoice', '/api/invoice/1/other')
    assert [path for path in paths if allowed(portal, path)] == ['/api/invoice', '/api/invoice/1']
    shop = Frontend('shop', kind='static', dist='dist', path='/shop', auth='portal', api=('ops.full',))
    scope = ApiScope('full', models=(Invoice,), actions=((Invoice, 'void'), (Invoice, 'unpost'), (Invoice, 'send')),
                     extras=('measures', 'related', 'lines', 'attachments', 'drafts'))
    routes = resolve_routes([('ops', AppDefinition(frontends=(shop,), api_scopes=(scope,)))], None, {'hosts': (Host('portal.localhost', root=shop),)})
    auth = LocalAuth(portal.database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False)
    app = Application(portal.database, auth=auth, routes=routes)
    # Each correction is listed on its own: unpost does not open repost.
    assert [path for path in paths if not allowed(app, path)] == ['/api/invoice/1/revise', '/api/invoice/1/repost', '/api/invoice/1/other']
    with pytest.raises(ValueError, match='extras'):
        ApiScope('bad', models=(Invoice,), extras=('everything',))


def test_session_reports_docs_only_to_docs_roles_and_never_on_sub_hosts(portal):
    routes = resolve_routes([], None, {'docs': Docs(enabled=True, roles=('admin',))})
    app = Application(portal.database, auth=LocalAuth(portal.database, origins=routes.origins(), secure_cookies=False), routes=routes)
    assert http(app, 'GET', '/api/auth/session')[1]['application']['docs'] is None
    assert sign_in(app, 'viewer', DESK)[2]('GET', '/api/auth/session')[1]['application']['docs'] is None
    assert sign_in(app, 'admin', DESK)[2]('GET', '/api/auth/session')[1]['application']['docs'] == '/docs'
    assert http(portal, 'GET', '/api/auth/session')[1]['application']['docs'] == '/docs'
    assert http(portal, 'GET', '/api/auth/session', headers={b'host': PORTAL})[1]['application']['docs'] is None


def test_old_sessions_gain_the_desk_realm(tmp_path):
    database = Database(tmp_path / 'old.sqlite3', registry)
    with database.transaction() as tx:
        tx.connection.execute('CREATE TABLE evlet_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
        tx.connection.execute('CREATE TABLE evlet_sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires INTEGER NOT NULL)')
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('a','x','admin')")
        tx.connection.execute("INSERT INTO evlet_sessions VALUES ('t',1,'c',9999999999)")
    LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False).initialize()
    with database._connection() as connection:
        assert [tuple(row) for row in connection.execute('SELECT token, realm FROM evlet_sessions')] == [('t', 'desk')]
    database.close()


def test_portal_users_filter_on_records_they_can_read(portal):
    from urllib.parse import quote
    acme = portal.database.create(Customer, name='Acme')
    other = portal.database.create(Customer, name='Other')
    portal.database.create(Invoice, customer=acme.id, reference='INV-1', total='10.00')
    portal.database.create(Invoice, customer=other.id, reference='INV-2', total='20.00')
    auth = portal.business.auth
    auth.link_user(next(user['id'] for user in auth.users() if user['username'] == 'buyer'), Customer, acme.id)
    _, _, buyer = sign_in(portal, 'buyer', PORTAL)
    def listed(filters):
        status, body, _ = buyer('GET', '/api/invoice?filters=' + quote(json.dumps(filters)))
        return status, [item['reference'] for item in body['items']] if status == 200 else body
    assert listed({'customer': acme.id}) == (200, ['INV-1'])
    assert listed({'customer__in': [acme.id]}) == (200, ['INV-1'])
    assert listed({'customer': other.id})[0] == 403
    assert listed({'customer__in': [acme.id, other.id]})[0] == 403
    assert listed({'customer__ne': other.id})[0] == 403
