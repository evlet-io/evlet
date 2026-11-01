"""The route table: reserved paths, clashes, routes.py, dispatch and the evlet routes command."""

import dataclasses
import re

import pytest

from evlet import AppDefinition, ControlsAccess, Docs, Frontend, Host, Instance
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.instances import load_runtime
from evlet.routing import RESERVED, host_name, reserved, resolve_routes
from evlet.ui import Column, PageView, Text, ViewRegistry
from evlet.ui.registry import route_pattern
from evlet.ui.serving import STATIC, static_response
from test_business import http, secured, hashed
from test_cli_lifecycle import create_app, environment_stub, project, runtime


def legacy_served(app, path):
    """The browser paths Application served before the route table, kept as the reference."""
    builtin = path in ('/', '/login', '/docs', '/settings/accounts') or path.startswith('/docs/') or path.startswith('/assets/')
    generated = re.fullmatch(r'/app/([a-z][a-z0-9_]*)(?:/(new|[1-9][0-9]{0,15})(/view)?)?', path)
    known_report = path.startswith('/reports/') and path.removeprefix('/reports/') in app.business.forms.reports
    custom = any(view.route and route_pattern(view.route).fullmatch(path) for view in app.ui.views.values())
    allowed = builtin or (generated and generated[1] in app.registry.models) or known_report or custom
    return bool(allowed) and static_response(path) is not None


PATHS = (
    '/', '/login', '/docs', '/docs/', '/docs/components', '/settings/accounts', '/demo/projects', '/demo/layouts',
    '/app/customer', '/app/customer/new', '/app/customer/12', '/app/customer/12/view', '/app/project/3',
    '/reports/tickets', '/assets/evlet.svg',
    '/missing', '/app', '/app/', '/app/unknown', '/app/customer/0', '/app/customer/abc', '/app/customer/12/edit',
    '/APP/customer', '/Login', '/login/x', '/Docs', '/docsx', '/settings', '/settings/other', '/controls',
    '/controls/users', '/assets', '/assets/missing.js', '/assets/../app.py', '/reports', '/reports/unknown',
    '/files/x', '/_/mcp', '/demo', '/demo/projects/1', '/apis', '/report/x', '/kb',
)


@pytest.mark.skipif(not (STATIC / 'index.html').is_file(), reason='Build browser assets before checking static serving')
def test_without_routes_py_every_browser_path_is_served_as_before(secured):
    secured.business.forms.reports['tickets'] = object()
    assert {path for path in PATHS if legacy_served(secured, path)} >= {'/', '/docs/components', '/app/customer/12', '/reports/tickets'}
    for path in PATHS:
        status, payload, headers = http(secured, 'GET', path)
        assert (status == 200) == legacy_served(secured, path), path
        if status == 200 and not path.startswith('/assets/'):
            assert b'<div id="evlet-app">' in payload
            assert b"frame-ancestors 'none'" in headers[b'content-security-policy']
    assert http(secured, 'GET', '/health')[0] == 200
    assert http(secured, 'GET', '/api/customer')[0] == 401
    assert http(secured, 'GET', '/docs', headers={b'host': b'evil.example'})[0] == 403
    # Unknown browser paths get the app's not-found page; unknown assets stay plain 404s.
    status, payload, headers = http(secured, 'GET', '/missing')
    assert status == 404 and b'<div id="evlet-app">' in payload and b"frame-ancestors 'none'" in headers[b'content-security-policy']
    status, payload, _ = http(secured, 'GET', '/assets/missing.js')
    assert status == 404 and isinstance(payload, dict)


def test_session_tells_the_browser_whether_docs_are_on(secured):
    assert http(secured, 'GET', '/api/auth/session')[1]['application']['docs'] == '/docs'


def app(name, *frontends, views=()):
    return name, AppDefinition(frontends=frontends, views=views)


def test_two_apps_with_the_same_default_path_fail_naming_both():
    with pytest.raises(ValueError, match=r'^crm\.portal and sales\.portal both want /portal on erp\.acme\.com\. Move one in instances/acme/routes\.py\.$'):
        resolve_routes([app('crm', Frontend('portal', path='/portal')), app('sales', Frontend('portal', path='/portal'))],
                       Instance('acme', domain='erp.acme.com'))


@pytest.mark.parametrize('path', ['/app', '/api', '/docs', '/controls', '/settings', '/reports', '/report', '/kb', '/files', '/ws'])
def test_frontend_on_a_reserved_path_fails(path):
    with pytest.raises(ValueError, match=f'^crm.crm wants {re.escape(path)} on localhost, which is reserved for the framework. Move it in instances/acme/routes.py.$'):
        resolve_routes([app('crm', Frontend('crm', path=path))], Instance('acme'))
    for invalid in ('/_internal', '/.well-known', '/API'):
        with pytest.raises(ValueError, match='one-segment default path'):
            Frontend('crm', path=invalid)


def test_reserved_paths_match_per_segment_and_ignore_case():
    assert reserved('/API/x') == reserved('/api') == 'locked'
    assert reserved('/Docs/topic') == 'framework' and reserved('/search') == 'reserved'
    assert reserved('/_/mcp') == reserved('/_anything') == 'locked'
    assert reserved('/apis') is None and reserved('/applications') is None and reserved('/crm') is None
    assert 'controls' in RESERVED and 'reports' in RESERVED
    with pytest.raises(TypeError):
        RESERVED['crm'] = 'locked'
    resolve_routes([app('crm', Frontend('apis', path='/apis'))])
    resolve_routes([app('crm', views=(PageView('feed', route='/Apis/feed'),))])


@pytest.mark.parametrize('route', ['/API/feed', '/Docs', '/settings/mine', '/_/x', '/Reports/q', '/print/{id:int}'])
def test_page_view_on_a_reserved_path_fails_naming_the_app(route):
    with pytest.raises(ValueError, match=f'^notes page feed wants {re.escape(route)} on localhost, which is reserved for the framework. Change the route in app notes.$'):
        resolve_routes([('notes', AppDefinition(views=(PageView('feed', route=route),)))])
    with pytest.raises(ValueError, match='Reserved route namespace'):
        ViewRegistry([PageView('feed', route=route)])


def test_pages_share_segments_but_not_with_frontends():
    pages = (PageView('board', route='/board'), PageView('card', route='/board/{id:int}'))
    resolve_routes([app('notes', views=pages), app('crm', views=(PageView('other', route='/board/all'),))])
    with pytest.raises(ValueError, match='notes.board and crm page other both want /board'):
        resolve_routes([app('notes', Frontend('board', path='/board')), app('crm', views=(PageView('other', route='/Board/all'),))])
    with pytest.raises(ValueError, match='must start with a fixed segment'):
        resolve_routes([app('notes', views=(PageView('any', route='/{slug}'),))])
    with pytest.raises(ValueError, match='fixed segment'):
        ViewRegistry([PageView('any', route='/{slug}')])


def test_routes_py_moves_frontends_and_redirects_the_root():
    crm, portal = Frontend('crm', path='/crm'), Frontend('portal', path='/portal', auth='portal')
    table = resolve_routes([app('crm', crm, portal)], Instance('acme'), {'primary': Host('erp.acme.com', root=crm), 'paths': {crm: '/sales'}})
    assert [(row.path, row.serves, row.source) for row in table.rows()[-3:]] == [
        ('/sales', 'crm.crm', 'routes.py'), ('/portal', 'crm.portal', 'app crm'), ('/', '-> /sales', 'routes.py')]
    assert table.match(b'erp.acme.com', '/sales/deals') == ('crm.crm', '/sales', '/deals')
    assert table.match(b'erp.acme.com', '/crm') == ('evlet.desk', '/', '/crm')
    assert table.match(b'erp.acme.com:443', '/crm') is None and table.authorities() == ('erp.acme.com',)
    assert table.match(b'portal.acme.com', '/sales') is None
    assert table.redirect(b'erp.acme.com', '/') == '/sales' and table.redirect(b'erp.acme.com', '/x') is None
    assert table.origins() == ('https://erp.acme.com',) and table.hosts() == ('erp.acme.com',)
    with pytest.raises(ValueError, match='both want /portal'):
        resolve_routes([app('crm', crm, portal)], None, {'paths': {crm: '/portal'}})
    with pytest.raises(ValueError, match='crm.crm wants /app on localhost, which is reserved'):
        resolve_routes([app('crm', crm)], None, {'paths': {crm: '/app'}})
    with pytest.raises(ValueError, match='no installed app registers'):
        resolve_routes([app('crm', crm)], None, {'paths': {Frontend('other', path='/other'): '/x'}})
    with pytest.raises(ValueError, match='must be a .localhost name in development'):
        resolve_routes([app('crm', crm, portal)], None, {'hosts': (Host('portal.acme.com', root=portal),)})


def test_instance_domain_and_routes_primary_must_agree():
    with pytest.raises(ValueError, match='config.py sets domain erp.acme.com but instances/acme/routes.py sets primary www.acme.com'):
        resolve_routes((), Instance('acme', domain='erp.acme.com'), {'primary': Host('www.acme.com')})
    assert resolve_routes((), Instance('acme', domain='erp.acme.com'), {'primary': Host('erp.acme.com')}).primary == 'erp.acme.com'
    assert resolve_routes((), None, {'primary': Host('erp.acme.com')}).origins() == ('https://erp.acme.com',)


def test_docs_are_on_in_development_and_off_with_a_domain_unless_routes_py_says_so():
    assert resolve_routes().docs and resolve_routes((), Instance('acme')).docs
    assert not resolve_routes((), Instance('acme', domain='erp.acme.com')).docs
    assert not resolve_routes((), None, {'primary': Host('erp.acme.com')}).docs
    assert resolve_routes((), Instance('acme', domain='erp.acme.com'), {'docs': Docs(enabled=True)}).docs
    assert not resolve_routes((), Instance('acme'), {'docs': Docs(enabled=False)}).docs
    assert resolve_routes((), Instance('acme', domain='a.b')).match(b'a.b', '/docs') == ('evlet.desk', '/', '/docs')
    with pytest.raises(ValueError, match='one free segment'):
        Docs(path='/settings')


def test_route_table_is_frozen():
    table = resolve_routes([app('crm', Frontend('crm', path='/crm'))])
    with pytest.raises(dataclasses.FrozenInstanceError):
        table.primary = 'evil.example'
    with pytest.raises(dataclasses.FrozenInstanceError):
        table.rows()[0].path = '/x'
    with pytest.raises(TypeError):
        table._segments['/x'] = 'crm.crm'
    assert isinstance(table.rows(), tuple) and isinstance(table.origins(), tuple)


def test_declarations_validate_their_fields():
    for kwargs in ({'path': '/'}, {'path': '/a/b'}, {'path': 'crm'}, {'path': '/Crm'}, {}, {'path': '/x', 'kind': 'vue'},
                   {'path': '/x', 'auth': 'staff'}, {'path': '/x', 'kind': 'static'}, {'path': '/x', 'kind': 'static', 'dist': '../dist'},
                   {'path': '/x', 'kind': 'server'}, {'path': '/x', 'dist': 'dist'}, {'path': '/x', 'csp': {'connect-src': ('https://a.b',)}},
                   {'path': '/x', 'kind': 'static', 'dist': 'dist', 'csp': {'script-src': ('https://a.b',)}}):
        with pytest.raises(ValueError):
            Frontend('crm', **kwargs)
    Frontend('portal', kind='static', dist='portal/dist', path='/portal', auth='portal', api=('crm.portal',), csp={'connect-src': ('https://maps.example.com',)})
    Frontend('site', kind='server', handler=lambda request: None, path='/site', auth=None)
    with pytest.raises(ValueError):
        Host('https://erp.acme.com')
    with pytest.raises(ValueError):
        ControlsAccess(allow_ips=('not an ip',))
    assert ControlsAccess(allow_ips=('10.0.0.1/8',)).allow_ips == ('10.0.0.0/8',)
    with pytest.raises(ValueError, match='Register Frontend'):
        AppDefinition(frontends=('crm',))


@pytest.mark.parametrize('value', ['erp.acme.com', b'127.0.0.1:8000', 'localhost:1', '[::1]:8000', 'a-b.example:65535'])
def test_host_headers_are_normalized_strictly(value):
    assert host_name(value) is not None


@pytest.mark.parametrize('value', ['', 'ERP.acme.com', 'user@erp.acme.com', 'erp.acme.com/x', 'erp.acme.com?x', 'erp.acme.com#x',
                                   'erp.acme.com:', 'erp.acme.com:0', 'erp.acme.com:65536', 'erp.acme.com:80x', 'erp..acme.com',
                                   'erp acme', '[::1', '[nothost]:80', '-erp.com', None, b'\xff'])
def test_invalid_host_headers_match_nothing(value):
    assert host_name(value) is None
    assert resolve_routes().match(value, '/app/x') is None


@pytest.mark.parametrize('names, message', [
    ({'primay': Host('erp.acme.com')}, "unknown name 'primay'"),
    ({'doc': Docs(enabled=False)}, "unknown name 'doc'"),
    ({'control': ControlsAccess()}, "unknown name 'control'"),
    ({'path': {}}, "unknown name 'path'"),
    ({'hosts': None}, 'hosts must be a tuple of Host'),
    ({'hosts': ('portal.acme.com',)}, 'hosts must be a tuple of Host'),
    ({'controls': {}}, 'controls must be ControlsAccess'),
    ({'paths': None}, 'paths must be a dict'),
    ({'paths': []}, 'paths must be a dict'),
    ({'primary': 'erp.acme.com'}, 'primary must be Host'),
    ({'docs': False}, 'docs must be Docs'),
])
def test_routes_py_rejects_unknown_names_and_wrong_types(names, message):
    with pytest.raises(ValueError, match=re.escape(f'instances/acme/routes.py: {message}')):
        resolve_routes((), Instance('acme'), names)


def test_routes_py_skips_imports_and_private_names():
    import os
    namespace = {'__builtins__': {}, '_helper': 1, 'os': os, 'Host': Host, 'crm': Frontend('crm', path='/crm'),
                 'reserved': reserved, 'docs': Docs(enabled=False)}
    assert not resolve_routes((), Instance('acme'), namespace).docs


TABLE = '''HOST           PATH        SERVES           SIGN-IN   SOURCE
erp.acme.com   /controls   evlet.controls   control   locked
erp.acme.com   /settings   evlet.settings   desk      locked
erp.acme.com   /app        evlet.desk       desk      locked
erp.acme.com   /reports    evlet.reports    desk      locked
erp.acme.com   /login      evlet.login      public    locked
erp.acme.com   /assets     evlet.assets     public    locked
erp.acme.com   /health     evlet.health     public    locked
erp.acme.com   /api/*      evlet.api        desk      locked
erp.acme.com   /sales      crm.crm          desk      routes.py
erp.acme.com   /portal     crm.portal       portal    app crm
erp.acme.com   /           -> /sales        desk      routes.py'''


def test_frontends_and_root_resolve_into_rows():
    crm, portal = Frontend('crm', path='/crm'), Frontend('portal', kind='static', dist='portal/dist', path='/portal', auth='portal')
    table = resolve_routes([app('crm', crm, portal)], Instance('acme'), {'primary': Host('erp.acme.com', root=crm), 'paths': {crm: '/sales'}})
    assert table.text() == TABLE


ROUTES = '''HOST           PATH        SERVES           SIGN-IN   SOURCE
erp.acme.com   /controls   evlet.controls   control   locked
erp.acme.com   /settings   evlet.settings   desk      locked
erp.acme.com   /app        evlet.desk       desk      locked
erp.acme.com   /reports    evlet.reports    desk      locked
erp.acme.com   /login      evlet.login      public    locked
erp.acme.com   /assets     evlet.assets     public    locked
erp.acme.com   /health     evlet.health     public    locked
erp.acme.com   /api/*      evlet.api        desk      locked
erp.acme.com   /           evlet.desk       desk      framework
'''


def test_instance_routes_py_resolves_on_start_and_prints_with_evlet_routes(project, capsys):
    install(project, 'acme', app_name='notes', trust=True)
    instance = project / 'instances/acme'
    main(['routes', 'acme', '--project', str(project)])
    assert '/docs       evlet.docs       desk      framework' in capsys.readouterr().out
    (instance / 'routes.py').write_text('from evlet import Host, ControlsAccess\n\n'
                                        'primary = Host("erp.acme.com")\ncontrols = ControlsAccess(allow_ips=("10.0.0.0/8",))\n')
    main(['routes', 'acme', '--project', str(project)])
    assert capsys.readouterr().out == ROUTES
    application = runtime(project)
    try:
        auth = application.business.auth
        assert auth.origins == set(application.routes.origins()) == {'https://erp.acme.com'} and auth.secure_cookies
        assert auth.hosts == {value.encode() for value in application.routes.authorities()}
        assert application.routes.controls.allow_ips == ('10.0.0.0/8',)
        host = {b'host': b'erp.acme.com', b'origin': b'https://erp.acme.com'}
        assert http(application, 'GET', '/api/auth/session', headers=host)[1]['application']['docs'] is None
        assert http(application, 'GET', '/docs', headers=host)[0] == 404
        assert http(application, 'GET', '/', headers={b'host': b'127.0.0.1:8000'})[0] == 403
    finally:
        application.database.close()
    with pytest.raises(ValueError, match='Development serving requires a local instance'):
        load_runtime(project, 'acme', development=True)
    with pytest.raises(SystemExit) as exit:
        main(['dev', '--instance', 'acme', '--project', str(project)])
    assert exit.value.code == 1 and 'Development serving requires a local instance' in capsys.readouterr().err
    (instance / 'routes.py').unlink()
    with pytest.raises(ValueError, match='Configure an HTTPS domain'):
        load_runtime(project, 'acme', public=True)
    with pytest.raises(SystemExit):
        main(['serve', 'acme', '--host', '0.0.0.0', '--project', str(project)])
    assert 'Configure an HTTPS domain' in capsys.readouterr().err
    (instance / 'routes.py').write_text('from evlet import Host\nprimary = Host("erp.acme.com")\n')
    config = instance / 'config.py'
    config.write_text(config.read_text().replace('domain=None', 'domain="www.acme.com"'))
    with pytest.raises(ValueError, match='sets domain www.acme.com but instances/acme/routes.py sets primary erp.acme.com'):
        runtime(project)


def test_routes_py_typo_fails_boot(project):
    install(project, 'acme', app_name='notes', trust=True)
    (project / 'instances/acme/routes.py').write_text('from evlet import Host\nprimay = Host("erp.acme.com")\n')
    with pytest.raises(ValueError, match="instances/acme/routes.py: unknown name 'primay'"):
        runtime(project)


def test_routes_py_must_be_a_regular_file_inside_the_instance(project, tmp_path):
    install(project, 'acme', app_name='notes', trust=True)
    outside = tmp_path / 'routes.py'
    outside.write_text('paths = {}\n')
    (project / 'instances/acme/routes.py').symlink_to(outside)
    with pytest.raises(ValueError, match='symbolic link: routes.py'):
        runtime(project)


def test_instance_app_frontend_on_a_reserved_path_fails_boot(project):
    package = project / 'apps/notes/notes'
    hooks = package / 'hooks.py'
    hooks.write_text('from evlet import Frontend\n' + hooks.read_text().replace('migrations=migrations)', 'migrations=migrations, frontends=(Frontend("settings", path="/settings"),))'))
    with pytest.raises(ValueError, match='^notes.settings wants /settings on localhost, which is reserved for the framework. Move it in instances/acme/routes.py.$'):
        install(project, 'acme', app_name='notes', trust=True)
