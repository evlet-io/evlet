"""Public websites from app frontends: file types, pages routing and strict public hosts."""

import json

import pytest

from evlet import ApiScope, AppDefinition, Application, Database, Frontend, Host, LocalAuth, Policy, Rule
from evlet.routing import resolve_routes
from evlet.services import Endpoint
from examples.playground.models import Customer, registry
from test_business import http

PRIMARY, SITE = b'erp.acme.com', b'www.acme.com'
NAME = 'Acme Ledger'


def write_site(folder, not_found=True):
    pages = {
        'index.html': '<!doctype html><html><head><title>Home</title></head><body><a href="about/">About</a></body></html>',
        'about/index.html': '<html><head><link rel="stylesheet" href="../site.css"></head><body>About us</body></html>',
        'pricing.html': '<html><head></head><body>Pricing</body></html>',
        'docs/start/index.html': '<html><head></head><body>Start here</body></html>',
        'robots.txt': 'User-agent: *\nAllow: /\n',
        'sitemap.xml': '<?xml version="1.0"?><urlset></urlset>',
        'site.webmanifest': '{"name": "Acme"}',
        'site.css': 'body{}',
        'assets/app.js': 'console.log(1)',
        '.env': 'SECRET=1',
    }
    if not_found:
        pages['404.html'] = '<html><head><script id="evlet-config" type="application/json">{"x":1}</script></head><body>Lost?</body></html>'
    for name, text in pages.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
    for name, data in (('brochure.pdf', b'%PDF-1.4'), ('logo.gif', b'GIF89a'), ('hero.avif', b'\0\0\0 ftypavif'), ('font.woff', b'wOFF')):
        (folder / name).write_bytes(data)


def contact(context) -> dict:
    return {'ok': True}


@pytest.fixture
def site(tmp_path):
    write_site(tmp_path / 'web/dist')
    www = Frontend('www', kind='static', dist='dist', path='/www', auth=None, routing='pages', api=('web.public',), suggested_host='www')
    scope = ApiScope('public', endpoints=('web.contact', 'web.private'))
    definition = AppDefinition(frontends=(www,), api_scopes=(scope,))
    routes = resolve_routes([('web', definition)], None, {'primary': Host('erp.acme.com'), 'hosts': (Host('www.acme.com', root='web.www'),)},
                            roots={'web': tmp_path / 'web'})
    database = Database(tmp_path / 'site.sqlite3', registry)
    auth = LocalAuth(database, origins=routes.origins(), realms=routes.realms())
    apis = (Endpoint('web.contact', contact, 'GET', (Customer, 'read'), public=True),
            Endpoint('web.private', contact, 'GET', (Customer, 'read')))
    policy = Policy(registry, rules={('public', Customer): Rule(('read',))})
    app = Application(database, auth=auth, routes=routes, apis=apis, policy=policy, name=NAME)
    app.initialize()
    yield app
    database.close()


def get(app, path, host=SITE, method='GET'):
    return http(app, method, path, headers={b'host': host})


def test_static_frontends_serve_website_file_types(site):
    for path, kind in (('/robots.txt', b'text/plain; charset=utf-8'), ('/sitemap.xml', b'application/xml'),
                       ('/site.webmanifest', b'application/manifest+json'), ('/brochure.pdf', b'application/pdf'),
                       ('/logo.gif', b'image/gif'), ('/hero.avif', b'image/avif'), ('/font.woff', b'font/woff'),
                       ('/assets/app.js', b'text/javascript; charset=utf-8')):
        status, _, headers = get(site, path)
        assert (status, headers[b'content-type']) == (200, kind), path
    assert get(site, '/robots.txt')[1] == b'User-agent: *\nAllow: /\n'
    for path in ('/.env', '/../dist/robots.txt', '/a\\b'):
        assert get(site, path)[0] == 404, path


def test_pages_routing_serves_folders_and_html_files_and_redirects_one_way(site):
    status, page, _ = get(site, '/')
    assert status == 200 and b'<title>Home</title>' in page and b'<base' not in page
    status, page, _ = get(site, '/about/')
    assert status == 200 and b'About us' in page and b'<base' not in page
    status, page, _ = get(site, '/pricing')
    assert status == 200 and b'Pricing' in page
    assert get(site, '/docs/start/')[1].count(b'Start here') == 1
    # One address per page: a folder page ends in /, a .html page does not.
    status, _, headers = get(site, '/about')
    assert status == 301 and headers[b'location'] == b'/about/'
    status, _, headers = get(site, '/pricing/')
    assert status == 301 and headers[b'location'] == b'/pricing'
    status, _, headers = get(site, '/docs/start?x=1')
    assert status == 301 and headers[b'location'] == b'/docs/start/?x=1'


def test_pages_routing_answers_404_with_the_sites_own_page(site, tmp_path):
    for path in ('/missing', '/missing/', '/about/team', '/nope.js', '/.env'):
        status, page, headers = get(site, path)
        assert status == 404 and b'Lost?' in page and headers[b'content-type'] == b'text/html; charset=utf-8', path
        assert b'evlet-config' not in page
    (tmp_path / 'web/dist/404.html').unlink()
    status, page, headers = get(site, '/missing')
    assert (status, page, headers[b'content-type']) == (404, b'Not found\n', b'text/plain; charset=utf-8')


def test_visitors_from_other_sites_reach_a_public_host(site):
    # A link from a search engine or another site is a cross-site navigation.
    away = {b'host': SITE, b'sec-fetch-site': b'cross-site', b'sec-fetch-mode': b'navigate'}
    assert http(site, 'GET', '/pricing', headers=away)[0] == 200
    assert http(site, 'GET', '/robots.txt', headers=away)[0] == 200
    # The primary host keeps refusing cross-site requests.
    assert http(site, 'GET', '/', headers={**away, b'host': PRIMARY})[0] == 403


def test_head_requests_get_headers_without_a_body(site):
    status, page, headers = get(site, '/pricing', method='HEAD')
    assert status == 200 and page == b'' and int(headers[b'content-length']) > 0


def test_a_public_host_serves_no_evlet_paths(site):
    for path in ('/login', '/logout', '/controls', '/settings', '/app/customer', '/health', '/api', '/api/auth/session',
                 '/api/schema', '/api/customer', '/api/_services/web.private', '/api/_events/stream', '/_/mcp',
                 '/_/oauth/authorize', '/.well-known/oauth-protected-resource', '/.well-known/security.txt', '/assets/missing.js'):
        status, page, _ = get(site, path)
        assert status == 404 and b'Lost?' in page, path
    for method in ('POST', 'PUT', 'DELETE'):
        assert get(site, '/api/auth/login', method=method)[0] == 404
        assert get(site, '/', method=method)[0] == 404
    # The public endpoint its scope lists still works; the primary host keeps everything.
    assert get(site, '/api/_services/web.contact')[:2] == (200, {'ok': True})
    assert get(site, '/api/auth/session', host=PRIMARY)[0] == 200
    assert get(site, '/health', host=PRIMARY)[0] == 200


def test_a_public_host_never_names_the_primary_host_or_the_application(site):
    paths = ('/', '/about/', '/pricing', '/missing', '/robots.txt', '/login', '/api/auth/session', '/api/auth/login',
             '/assets/index.js', '/.well-known/oauth-authorization-server', '/.well-known/oauth-protected-resource/_/mcp',
             '/_/mcp', '/_/oauth/authorize', '/controls', '/docs', '/health', '/api/_services/web.contact')
    for path in paths:
        for method in ('GET', 'POST', 'HEAD'):
            status, page, headers = get(site, path, method=method)
            raw = (page if isinstance(page, bytes) else json.dumps(page).encode()).lower()
            seen = b'\n'.join(key + b': ' + value for key, value in headers.items()).lower()
            for secret in (PRIMARY, NAME.lower().encode(), b'evlet', b'version'):
                assert secret not in raw and secret not in seen, (method, path, secret)
            assert b'set-cookie' not in headers, (method, path)


def test_public_host_routes_and_declarations(site):
    assert site.routes.public(SITE) and not site.routes.public(PRIMARY) and site.routes.root(SITE) == 'web.www'
    assert ('www.acme.com', '/api/*', 'web.public (public endpoints)') in [(row.host, row.path, row.serves) for row in site.routes.rows()]
    with pytest.raises(ValueError, match='routing must be'):
        Frontend('x', path='/x', routing='spa')
    with pytest.raises(ValueError, match='for static frontends'):
        Frontend('x', path='/x', routing='pages')
    for label in ('www.acme', 'WWW', '-www', '', 'a' * 64):
        with pytest.raises(ValueError, match='one lowercase label'):
            Frontend('x', kind='static', dist='d', path='/x', suggested_host=label)
    with pytest.raises(ValueError, match='static and server'):
        Frontend('x', path='/x', suggested_host='www')
    with pytest.raises(ValueError, match='its id'):
        Host('www.acme.com', root='nodot')
    with pytest.raises(ValueError, match='no installed app registers'):
        resolve_routes([], None, {'primary': Host('erp.acme.com'), 'hosts': (Host('www.acme.com', root='web.www'),)})


def test_the_playground_website_is_a_public_pages_site(tmp_path, monkeypatch):
    from examples.playground.app import build_app, playground_routes
    from examples.playground.migrate import configured_database
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'playground.sqlite3'))
    database = configured_database()
    routes = playground_routes(8000)
    app = build_app(database, LocalAuth(database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False), routes=routes)
    app.initialize()
    try:
        host = b'site.localhost:8000'
        assert get(app, '/about/', host)[0] == 200 and b'evlet-config' not in get(app, '/', host)[1]
        assert get(app, '/about', host)[2][b'location'] == b'/about/'
        assert get(app, '/robots.txt', host)[2][b'content-type'] == b'text/plain; charset=utf-8'
        status, page, _ = get(app, '/login', host)
        assert status == 404 and b'Page not found' in page
        assert get(app, '/api/auth/session', host)[0] == 404
        assert get(app, '/site', b'127.0.0.1:8000')[0] == 404
    finally:
        database.close()
