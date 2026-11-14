"""The /_/ routes the internet must reach pass the generated Caddy and nginx files; every other /_/ path stays refused."""
import asyncio
import json
import re
from pathlib import Path

import evlet
from evlet import AppDefinition, Frontend, Host
from evlet.app import MAIL_PATHS, MCP_PATHS, PUBLIC_PATHS, TLS_CHECK
from evlet.cli import proxy, serve, site
from evlet.routing import resolve_routes
from tests.test_business import secured, hashed  # noqa: F401

# A path Evlet does not serve, and near misses of the public ones: the proxy refuses all of them.
UNKNOWN = ('/_/', '/_/admin', '/_/mcp/extra', '/_/oauth', '/_/oauth/', '/_/mail/', '/_/mail/callback/x')

OLD_CADDY = '''erp.acme.com {
\t@internal path /_/*
\trespond @internal 404
\treverse_proxy 127.0.0.1:8000
}
'''


def served_paths():
    """Every /_/ path named as a string in the framework's code, so a new route can't hide from these tests."""
    found = set()
    for path in Path(evlet.__file__).parent.rglob('*.py'):
        found.update(re.findall(r'''['"](/_/[A-Za-z0-9_./-]+)['"]''', path.read_text(encoding='utf-8')))
    return found


def routes():
    shop = Frontend('shop', kind='static', dist='dist', path='/shop', auth=None)
    return resolve_routes([('shop', AppDefinition(frontends=(shop,)))], None,
                          {'primary': Host('erp.acme.com'), 'hosts': (Host('shop.acme.com', root=shop),)})


def nginx_refuses(text, paths):
    """nginx location choice for each server block: exact, then the longest prefix (^~ stops there), then regexes in order."""
    refused = set()
    for server in text.split('\nserver {')[1:]:
        if 'listen 443' not in server:
            continue
        locations = []
        lines = server.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r'    location (?:(=|\^~|~\*|~) )?(\S+) \{(.*)$', line)
            if not match:
                continue
            body = match[3]
            if not body.rstrip().endswith('}'):
                body = '\n'.join(lines[index + 1:lines.index('    }', index)])
            locations.append((match[1], match[2], 'return 404' in body, 'proxy_pass' in body))
        for path in paths:
            chosen = next((item for item in locations if item[0] == '=' and item[1] == path), None)
            if chosen is None:
                prefixes = sorted((item for item in locations if item[0] in (None, '^~') and path.startswith(item[1])), key=lambda item: len(item[1]))
                longest = prefixes[-1] if prefixes else None
                if longest is None or longest[0] != '^~':
                    chosen = next((item for item in locations if item[0] in ('~', '~*')
                                   and re.search(item[1], path, re.IGNORECASE if item[0] == '~*' else 0)), longest)
                else:
                    chosen = longest
            assert chosen is not None and chosen[2] != chosen[3], (path, chosen)
            if chosen[2]:
                refused.add(path)
    return sorted(refused)


def test_every_framework_route_under_underscore_is_public_or_the_tls_check():
    assert served_paths() == set(PUBLIC_PATHS) | {TLS_CHECK}
    assert PUBLIC_PATHS == MCP_PATHS + MAIL_PATHS and TLS_CHECK not in PUBLIC_PATHS


def test_generated_caddy_and_nginx_pass_exactly_the_public_routes():
    table = routes()
    probes = sorted(served_paths() | set(UNKNOWN))
    blocked = sorted(set(probes) - set(PUBLIC_PATHS))
    caddy = proxy.caddy(table)
    assert sorted(proxy.caddy_refuses(caddy, probes)) == blocked
    assert nginx_refuses(proxy.nginx(table), probes) == blocked
    # The site paths stay with Evlet. A public sub-host leaves /controls and /docs to Evlet, which answers
    # them with the site's own 404; a portal sub-host refuses them in the proxy too.
    assert proxy.caddy_refuses(caddy, ['/', '/app', '/controls', '/.well-known/oauth-protected-resource/_/mcp']) == []
    portal = Frontend('portal', kind='static', dist='dist', path='/portal', auth='portal')
    portal_table = resolve_routes([('shop', AppDefinition(frontends=(portal,)))], None,
                                  {'primary': Host('erp.acme.com'), 'hosts': (Host('portal.acme.com', root=portal),)})
    assert proxy.caddy_refuses(proxy.caddy(portal_table), ['/controls']) == ['/controls']
    # Caddy refuses a request whose Host differs from the TLS server name; nginx answers it with 421.
    assert caddy.index('servers :443 {\n\t\tstrict_sni_host on\n\t}') < caddy.index('erp.acme.com {')
    assert 'strict_sni_host' not in proxy.caddy(table, global_options=False)
    assert proxy.nginx(table).count('if ($ssl_server_name != $host) { return 421; }') == 2
    nginx = proxy.nginx(table, port=8010)
    assert nginx.count('location = /_/mcp {') == 2 and nginx.count('location ^~ /_/ { return 404; }') == 2
    assert nginx.index('location = /_/mail/continue.js {') < nginx.index('location ^~ /_/ { return 404; }')
    public = nginx[nginx.index('location = /_/mcp {'):nginx.index('location = /_/oauth/authorize {')]
    for line in ('proxy_pass http://127.0.0.1:8010;', 'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
                 'proxy_buffering off;', 'proxy_read_timeout 3600s;', 'client_max_body_size 128k;'):
        assert line in public


def test_the_caddy_reader_sees_what_older_files_refused():
    assert proxy.caddy_refuses(OLD_CADDY, PUBLIC_PATHS) == list(PUBLIC_PATHS)
    assert proxy.caddy_refuses(OLD_CADDY, ['/app']) == []
    # Matchers it does not know never count as refusing, and case does not matter, as in Caddy.
    assert proxy.caddy_refuses('a {\n\t@x header Foo bar\n\trespond @x 404\n}\n', ['/_/mcp']) == []
    assert proxy.caddy_refuses(proxy.caddy(routes()), ['/_/TLS-allowed', '/_/MCP']) == ['/_/TLS-allowed']


def send(app, method, path, body=b'', headers=(), address='127.0.0.1', host=b'127.0.0.1:8000'):
    async def run():
        events = [{'type': 'http.request', 'body': body}]
        sent = []

        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}

        async def push(event):
            sent.append(event)
        route, _, query = path.partition('?')
        await app({'type': 'http', 'method': method, 'path': route, 'query_string': query.encode(), 'scheme': 'http',
                   'headers': [(b'host', host), *headers], 'client': (address, 1234)}, receive, push)
        return sent[0]['status'], dict(sent[0]['headers']), sent[1]['body']
    return asyncio.run(run())


def test_each_public_route_is_answered_by_evlet(secured):
    # Not the generic 404: each route reaches its handler, which answers with its own status.
    answers = {path: send(secured, 'POST' if path in ('/_/mcp', '/_/oauth/register', '/_/oauth/token', '/_/oauth/revoke') else 'GET', path,
                          b'{}', [(b'content-type', b'application/json')]) for path in PUBLIC_PATHS}
    assert answers['/_/mcp'][0] == 401 and answers['/_/mcp'][1][b'www-authenticate'].startswith(b'Bearer resource_metadata=')
    assert answers['/_/oauth/register'][0] == 400 and answers['/_/oauth/token'][0] == 400 and answers['/_/oauth/revoke'][0] == 400
    assert answers['/_/oauth/authorize'][0] in (200, 404) and b'Route not found' not in answers['/_/oauth/authorize'][2]
    assert answers['/_/mail/callback'][0] == 200 and answers['/_/mail/continue.js'][0] == 200
    assert send(secured, 'GET', '/_/unknown')[0] == 404


def test_tls_check_reaches_its_handler_on_an_instance_with_accounts(secured, monkeypatch):
    # It used to fall into the MCP routes and answer 404 for every host once the instance had sign-in.
    table, actual = routes(), secured.routes

    class Named:
        """The instance's own route table, with the hosts of a domain."""
        primary = table.primary

        def hosts(self):
            return table.hosts()

        def __getattr__(self, name):
            return getattr(actual, name)
    monkeypatch.setattr(secured, 'routes', Named())
    assert send(secured, 'GET', '/_/tls-allowed?domain=shop.acme.com')[0] == 200
    assert send(secured, 'GET', '/_/tls-allowed?domain=evil.example')[0] == 404
    assert send(secured, 'GET', '/_/tls-allowed?domain=shop.acme.com', address='203.0.113.9')[0] == 404


def test_start_up_warns_once_when_the_installed_caddy_site_blocks_public_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(site, 'CADDY_DIR', str(tmp_path))
    assert serve.proxy_warning(tmp_path, 'acme', 'erp.acme.com') is None  # nothing installed
    (tmp_path / 'evlet-acme.caddy').write_text(OLD_CADDY)
    warning = serve.proxy_warning(tmp_path, 'acme', 'erp.acme.com', port=8010)
    assert warning.count('\n') == 0 and '/_/mcp' in warning and '/_/tls-allowed' not in warning
    assert f'evlet setup {tmp_path} --instance acme --app APP --host erp.acme.com --port 8010 --apply' in warning
    (tmp_path / 'evlet-acme.caddy').write_text(proxy.caddy(routes()))
    assert serve.proxy_warning(tmp_path, 'acme', 'erp.acme.com') is None
    (tmp_path / 'evlet-acme.caddy').chmod(0)
    try:
        assert serve.proxy_warning(tmp_path, 'acme', 'erp.acme.com') is None  # unreadable: say nothing
    finally:
        (tmp_path / 'evlet-acme.caddy').chmod(0o644)


class Public:
    """Stands in for the network: what this site's public address answers."""

    def __init__(self, mcp=(401, {'www-authenticate': 'Bearer resource_metadata="https://erp.acme.com/.well-known/oauth-protected-resource/_/mcp"'})):
        self.mcp, self.calls = mcp, []

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url))
        if isinstance(self.mcp, Exception):
            raise self.mcp
        if url.endswith('/.well-known/oauth-protected-resource/_/mcp'):
            return 200, {}, json.dumps({'resource': 'https://erp.acme.com/_/mcp'}).encode()
        return self.mcp[0], self.mcp[1], b''


def test_reachability_is_checked_on_demand_for_admins_on_a_public_origin(secured, monkeypatch):
    from tests.test_business import client
    admin, editor = client(secured, 'admin'), client(secured, 'editor')
    assistants = secured.business.assistants
    # On loopback there is nothing to check.
    status, data, _ = admin('POST', '/api/_controls/mcp/reach', {})
    assert status == 200 and data == {'checked': False, 'reachable': None, 'reason': 'Only on a site with a domain.'}
    assert editor('POST', '/api/_controls/mcp/reach', {})[0] in (403, 404)
    monkeypatch.setattr(type(assistants), 'origin', lambda self, scope: 'https://erp.acme.com')
    public = Public()
    monkeypatch.setattr(assistants, 'fetch', public)
    data = admin('POST', '/api/_controls/mcp/reach', {})[1]
    assert data['checked'] and data['reachable'] is True and data['reason'] == 'Reachable from the internet.'
    assert public.calls == [('GET', 'https://erp.acme.com/.well-known/oauth-protected-resource/_/mcp'), ('POST', 'https://erp.acme.com/_/mcp')]
    # Kept briefly: a second click does not call out again.
    admin('POST', '/api/_controls/mcp/reach', {})
    assert len(public.calls) == 2
    assistants.reached = {}
    monkeypatch.setattr(assistants, 'fetch', Public(mcp=(404, {})))
    data = admin('POST', '/api/_controls/mcp/reach', {})[1]
    assert data['reachable'] is False and data['reason'].startswith('Blocked by the proxy') and '--apply' in data['reason']
    assistants.reached = {}
    monkeypatch.setattr(assistants, 'fetch', Public(mcp=OSError('timed out')))
    data = admin('POST', '/api/_controls/mcp/reach', {})[1]
    assert data['reachable'] is False and data['reason'] == 'Could not connect to https://erp.acme.com: timed out'
    # A plain read of the MCP page never calls out.
    calls = Public()
    monkeypatch.setattr(assistants, 'fetch', calls)
    assert admin('GET', '/api/_controls/mcp')[0] == 200 and calls.calls == []


def test_setup_remembers_the_public_address_after_a_passing_check(tmp_path):
    root = tmp_path
    (root / 'deploy').mkdir()
    assert site.saved_addresses(root) is None
    site.save_addresses(root, ['203.0.113.7', '203.0.113.7'])
    assert json.loads((root / 'deploy/server.json').read_text()) == {'ip': ['203.0.113.7']}
    assert site.saved_addresses(root) == ['203.0.113.7']
    (root / 'deploy/server.json').write_text('{"ip": ["not an address"]}')
    assert site.saved_addresses(root) is None


def test_nginx_upload_envelopes_cover_application_limits():
    from evlet.app import request_body_limit
    from evlet.controls import IMAGE_LIMITS
    text = proxy.nginx(routes())
    maximum = IMAGE_LIMITS['files.image_max_kb'][1] * 1024
    for match, path in (
        ('= /api/_settings/me/photo', '/api/_settings/me/photo'),
        ('= /api/_eva/files', '/api/_eva/files'),
        ('~ ^/api/.*/(attachments|_images)$', '/api/model/_images'),
        ('~ ^/api/_controls/prints', '/api/_controls/prints'),
        ('~ ^/api/_unsaved/', '/api/_unsaved/item'),
        ('/', '/api/model'),
    ):
        block = text.split(f'location {match} {{', 1)[1].split('    }', 1)[0]
        limit = int(re.search(r'client_max_body_size (\d+);', block)[1])
        assert limit == request_body_limit(path, maximum)
    # A full 8 MiB image is base64-expanded, with room for filename and JSON syntax.
    assert request_body_limit('/api/item/_images', maximum) >= 4 * ((maximum + 2) // 3) + 1024
    assert request_body_limit('/api/_settings/me/photo', maximum) > 4 * ((2 * 1024 * 1024 + 2) // 3)
