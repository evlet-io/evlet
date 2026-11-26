"""Caddy through its admin API for a project endpoint (evlet/cli/caddy.py, docs/project-endpoint.md#caddy).

The admin API is a fake (tests/caddy_fake.py): a real HTTP server holding a config tree with Caddy's traversal rules.
The endpoint and its two instances are real processes over real SQLite: the instances answer /_/hosts and
/_/tls-allowed from their own route tables, and the endpoint pushes their hosts and answers the one TLS ask.
"""

import functools
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from caddy_fake import FakeCaddy, caddyfile_config
from evlet.cli import caddy, core, endpoint, proxy
from evlet.cli.instance import create_instance, set_domain
from evlet.cli.main import main
from evlet.cli.project import atomic_json
from evlet.instances import read_project
from support import http
from test_cli_hosts import write_site
from test_live_hosts import HERE
from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401  (fixtures)
from test_project_endpoint import free_port, until

ENDPOINT_HOST = 'project.example.com'


@pytest.fixture
def fake():
    server = FakeCaddy(caddyfile_config(ENDPOINT_HOST))
    yield server
    server.close()


@pytest.fixture
def sites(project, monkeypatch):
    """Two instances with public hosts: acme with a routes.py sub-host, beta with its primary host only."""
    write_site(project)
    set_domain(project, 'acme', 'erp.acme.com')
    core.install_app(project, 'acme', 'notes', trust=True)
    (project / 'instances/acme/routes.py').write_text('from evlet import Host\n\nhosts = (\n    Host("www.erp.acme.com", root="notes.www"),\n)\n')
    create_instance(project, 'beta')
    set_domain(project, 'beta', 'beta.example.com')
    core.install_app(project, 'beta', 'notes', trust=True)
    monkeypatch.setattr(proxy, 'check', functools.partial(proxy.check, resolve=lambda host, port, proto: HERE))
    return project


# ---------- the objects pushed ----------

def test_the_route_sends_each_instance_its_hosts_with_the_proxy_file_refusals():
    route, policy = caddy.objects('01hc1sd9vc1ye', {
        'beta': (8001, [{'host': 'beta.example.com', 'sub': False, 'public': False}]),
        'acme': (8000, [{'host': 'erp.acme.com', 'sub': False, 'public': False},
                        {'host': 'www.erp.acme.com', 'sub': True, 'public': True},
                        {'host': 'portal.acme.com', 'sub': True, 'public': False}]),
        'empty': (8002, []),
    })
    assert route['@id'] == 'evlet-01hc1sd9vc1ye' and policy == {
        '@id': 'evlet-01hc1sd9vc1ye-tls', 'on_demand': True,
        'subjects': ['beta.example.com', 'erp.acme.com', 'portal.acme.com', 'www.erp.acme.com']}
    assert route['match'] == [{'host': policy['subjects']}]
    acme, beta = route['handle'][0]['routes']
    assert acme['match'] == [{'host': ['erp.acme.com', 'www.erp.acme.com', 'portal.acme.com']}]
    inner = acme['handle'][0]['routes']
    assert inner[0]['handle'][0]['response']['set']['Strict-Transport-Security'] == ['max-age=31536000']
    # /_/ is refused but for the public framework paths, as in the evlet proxy Caddy file.
    assert inner[1]['match'][0]['path'] == ['/_/*'] and '/_/mcp' in inner[1]['match'][0]['not'][0]['path']
    # Controls and Docs only on the primary host; a public sub-host has nothing to refuse (Evlet 404s every path).
    assert inner[2]['match'][0]['host'] == ['portal.acme.com'] and 'controls|docs' in inner[2]['match'][0]['path_regexp']['pattern']
    assert inner[-1]['handle'] == [{'handler': 'reverse_proxy', 'upstreams': [{'dial': '127.0.0.1:8000'}]}]
    assert beta['handle'][0]['routes'][-1]['handle'][0]['upstreams'] == [{'dial': '127.0.0.1:8001'}]
    assert caddy.objects('x', {'empty': (8002, [])}) == (None, None)


@pytest.mark.parametrize('value', ['https://localhost:2019', 'http://10.0.0.5:2019', 'http://localhost:2019/config', 'unix//run/caddy.sock', 7])
def test_the_admin_api_must_be_on_loopback(value):
    with pytest.raises(ValueError, match='on this machine'):
        caddy.admin_address({'host': ENDPOINT_HOST, 'caddy_admin': value})


def test_the_admin_address_defaults_to_localhost_with_a_public_host_only():
    assert caddy.admin_address({'host': ENDPOINT_HOST}) == 'http://localhost:2019'
    assert caddy.admin_address({'host': None}) is None
    assert caddy.admin_address({'host': ENDPOINT_HOST, 'caddy_admin': None}) is None
    assert caddy.admin_address({'host': None, 'caddy_admin': 'http://127.0.0.1:2020/'}) == 'http://127.0.0.1:2020'


def test_push_touches_only_its_own_objects(fake):
    before = json.loads(json.dumps(fake.config))
    route, policy = caddy.objects('p1', {'acme': (8000, [{'host': 'erp.acme.com', 'sub': False, 'public': False}])})
    admin = caddy.Admin(fake.url)
    caddy.push(admin, 'p1', route, policy)
    servers = fake.config['apps']['http']['servers']['srv0']['routes']
    assert servers[0]['@id'] == 'evlet-p1' and servers[1:] == before['apps']['http']['servers']['srv0']['routes']
    assert fake.config['apps']['tls']['automation']['policies'] == [policy]
    assert fake.config['apps']['tls']['automation']['on_demand'] == before['apps']['tls']['automation']['on_demand']
    # Again with a host less: replaced in place, through its @id.
    route2, policy2 = caddy.objects('p1', {'acme': (8000, [{'host': 'erp2.acme.com', 'sub': False, 'public': False}])})
    caddy.push(admin, 'p1', route2, policy2, previous=policy)
    assert [request[:2] for request in fake.writes()[-3:]] == [('PATCH', '/id/evlet-p1-tls'), ('PATCH', '/id/evlet-p1'), ('PATCH', '/id/evlet-p1-tls')]
    # While the route moves, the policy covers the old and the new hosts, so neither is managed outside the ask.
    assert fake.writes()[-3][2]['subjects'] == ['erp.acme.com', 'erp2.acme.com']
    assert fake.object('evlet-p1') == route2 and fake.object('evlet-p1-tls') == policy2
    # No hosts: both objects go, the rest stays.
    caddy.push(admin, 'p1', None, None)
    assert fake.object('evlet-p1') is None and fake.object('evlet-p1-tls') is None
    assert fake.config['apps']['http']['servers']['srv0']['routes'] == before['apps']['http']['servers']['srv0']['routes']
    assert all(path.startswith('/id/evlet-p1') or path.startswith(('/config/apps/tls/automation', '/config/apps/http/servers'))
               for _, path, _ in fake.requests)


def test_push_says_what_to_do_when_caddy_has_no_https_server():
    server = FakeCaddy({'apps': {}})
    try:
        route, policy = caddy.objects('p1', {'acme': (8000, [{'host': 'erp.acme.com', 'sub': False, 'public': False}])})
        with pytest.raises(caddy.AdminError, match='no HTTP server. Load the base config first'):
            caddy.push(caddy.Admin(server.url), 'p1', route, policy)
    finally:
        server.close()


def test_the_admin_api_not_answering_is_an_admin_error():
    with pytest.raises(caddy.AdminError, match='does not answer'):
        caddy.Admin(f'http://127.0.0.1:{free_port()}', timeout=1).get('/config/')


# ---------- the base config ----------

def test_evlet_proxy_endpoint_prints_the_one_time_global_options(sites, capsys):
    project = sites
    with pytest.raises(SystemExit):
        main(['--project', str(project), 'proxy', '--endpoint', '--caddy'])
    assert 'This project has no endpoint' in capsys.readouterr().err
    endpoint.create(project, host=ENDPOINT_HOST, port=7999)
    main(['--project', str(project), 'proxy', '--endpoint', '--caddy'])
    text = capsys.readouterr().out
    assert '\tadmin localhost:2019\n' in text and 'ask http://127.0.0.1:7999/_/tls-allowed' in text and 'strict_sni_host on' in text
    # An instance's own file still prints, with a note that the endpoint routes it.
    main(['--project', str(project), 'proxy', 'acme', '--caddy'])
    captured = capsys.readouterr()
    assert 'evlet proxy --endpoint --caddy' in captured.err
    # The global options file of evlet setup --host and evlet host add --apply is the same base config.
    assert caddy.global_options(project) == text


def test_setup_endpoint_host_installs_the_base_config_first_in_the_caddyfile(project, monkeypatch, capsys):
    from evlet.cli import site
    from test_project_endpoint import PASSWORD
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        monkeypatch.setattr(os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    main(['--project', str(project), 'setup', '--endpoint', '--endpoint-host', ENDPOINT_HOST, '--service-user', 'evlet'])
    out = capsys.readouterr().out
    options = (project / 'deploy' / site.GLOBAL_FILE).read_text()
    assert 'on_demand_tls' in options and 'ask http://127.0.0.1:7999/_/tls-allowed' in options and 'admin localhost:2019' in options
    assert 'one on-demand TLS ask' in out and f'sudo sed -i \'1i import {site.CADDY_DIR}/{site.GLOBAL_FILE}\'' in out
    # A Caddyfile with a global block of its own gets the lines to add, instead of a second block.
    caddyfile = project / 'Caddyfile'
    caddyfile.write_text('{\n\temail ops@example.com\n\tservers :443 {\n\t\tstrict_sni_host on\n\t}\n}\n')
    monkeypatch.setattr(site, 'CADDYFILE', str(caddyfile))
    with pytest.raises(ValueError, match='has its own global options block') as raised:
        site.run_privileged([('import-first', '/etc/caddy/evlet-global.caddy', options)], run=lambda *a, **k: None)
    assert 'ask http://127.0.0.1:7999/_/tls-allowed' in str(raised.value)


# ---------- without an endpoint ----------

def test_without_an_endpoint_nothing_talks_to_the_admin_api(sites, fake, monkeypatch, capsys):
    monkeypatch.setattr(caddy, 'DEFAULT_ADMIN', fake.url)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **options: HERE)
    main(['host', 'add', 'acme', 'shop.acme.com', '--root', 'notes.www', '--live', '--ip', '203.0.113.7', '--project', str(sites)])
    main(['proxy', 'acme', '--caddy', '--project', str(sites)])
    out = capsys.readouterr()
    assert 'shop.acme.com {' in out.out and 'admin' not in out.out and 'evlet proxy --endpoint' not in out.err
    assert caddy.global_options(sites) == proxy.GLOBAL_OPTIONS
    app = runtime(sites)
    try:
        assert http(app, 'GET', '/_/hosts')[0] == 200
        assert http(app, 'GET', '/_/tls-allowed?domain=shop.acme.com')[0] == 200
    finally:
        app.database.close()
    assert fake.requests == []


def test_an_instance_lists_its_hosts_on_loopback_only(sites, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **options: HERE)
    main(['host', 'add', 'acme', 'shop.acme.com', '--root', 'notes.www', '--live', '--ip', '203.0.113.7', '--project', str(sites)])
    app = runtime(sites)
    try:
        status, body, _ = http(app, 'GET', '/_/hosts')
        assert status == 200 and body['hosts'] == [{'host': 'erp.acme.com', 'sub': False, 'public': False},
                                                   {'host': 'www.erp.acme.com', 'sub': True, 'public': True},
                                                   {'host': 'shop.acme.com', 'sub': True, 'public': True}]
        assert http(app, 'GET', '/_/hosts', client='203.0.113.9')[0] == 404
        assert http(app, 'POST', '/_/hosts')[0] in (404, 405)
    finally:
        app.database.close()


def test_the_ask_answers_the_endpoint_host_without_asking_an_instance():
    class Nobody:
        children = {}
    assert caddy.allowed(Nobody, ENDPOINT_HOST, ENDPOINT_HOST)
    assert not caddy.allowed(Nobody, ENDPOINT_HOST, 'other.example.com') and not caddy.allowed(Nobody, ENDPOINT_HOST, '')


# ---------- the endpoint, two instances and Caddy: real processes ----------

class Served:
    """`evlet endpoint` in production mode in a subprocess, pushing to the fake admin API."""

    def __init__(self, root, admin):
        self.root, self.port = root, free_port()
        self.ports = {'acme': free_port(), 'beta': free_port()}
        endpoint.create(root, host=ENDPOINT_HOST, port=self.port)
        settings = endpoint.read_settings(root)
        endpoint.save_settings(root, {**settings, 'instances': self.ports, 'caddy_admin': admin})
        # Production: the children are evlet serve, which serves instances with a domain (evlet dev refuses them).
        atomic_json(root / 'project.json', {**read_project(root), 'mode': 'production'})
        environment = {**os.environ, 'PYTHONPATH': os.pathsep.join(filter(None, (str(Path(__file__).resolve().parents[1]), os.environ.get('PYTHONPATH'))))}
        self.process = subprocess.Popen([sys.executable, '-m', 'evlet', '--project', str(root), 'endpoint'],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment, start_new_session=True)

    def ask(self, domain):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f'http://127.0.0.1:{self.port}/_/tls-allowed?domain={domain}', timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code
        except (urllib.error.URLError, ConnectionError):
            return 0

    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        return self.process.stdout.read().decode('utf-8', 'replace')


def routed(fake, project_id):
    route = fake.object(f'evlet-{project_id}')
    return route and {item['handle'][0]['routes'][-1]['handle'][0]['upstreams'][0]['dial']: item['match'][0]['host']
                      for item in route['handle'][0]['routes']}


def test_the_endpoint_pushes_both_instances_follows_a_host_and_answers_the_one_ask(sites, fake, monkeypatch, capsys):
    project_id = read_project(sites)['id']
    # A host from data, pending: not served, so neither routed nor allowed.
    with monkeypatch.context() as dns:
        dns.setattr(socket, 'getaddrinfo', lambda host, port, *options, **named: [])
        main(['host', 'add', 'acme', 'shop.acme.com', '--root', 'notes.www', '--live', '--ip', '203.0.113.7', '--project', str(sites)])
    served = Served(sites, fake.url)
    acme, beta = (f'127.0.0.1:{served.ports[name]}' for name in ('acme', 'beta'))
    try:
        hosts = until(lambda: (found := routed(fake, project_id)) and len(found) == 2 and found, seconds=120)
        assert hosts == {acme: ['erp.acme.com', 'www.erp.acme.com'], beta: ['beta.example.com']}
        policy = fake.object(f'evlet-{project_id}-tls')
        assert policy['on_demand'] and policy['subjects'] == ['beta.example.com', 'erp.acme.com', 'www.erp.acme.com']
        # Caddy's own route for the endpoint's site is still there, after Evlet's.
        assert fake.config['apps']['http']['servers']['srv0']['routes'][1]['match'] == [{'host': [ENDPOINT_HOST]}]
        # The one ask: the endpoint's host and every host of every instance; nothing else, nor the pending host.
        for domain in (ENDPOINT_HOST, 'erp.acme.com', 'www.erp.acme.com', 'beta.example.com'):
            assert served.ask(domain) == 200, domain
        for domain in ('shop.acme.com', 'evil.example.com', ''):
            assert served.ask(domain) == 404, domain
        # The host becomes active: the running instance serves it, the endpoint routes it and allows it.
        with monkeypatch.context() as dns:
            dns.setattr(socket, 'getaddrinfo', lambda host, port, *options, **named: HERE)
            main(['host', 'check', 'acme', '--ip', '203.0.113.7', '--project', str(sites)])
        assert 'active' in capsys.readouterr().out
        until(lambda: 'shop.acme.com' in (routed(fake, project_id) or {}).get(acme, ()), seconds=20)
        assert served.ask('shop.acme.com') == 200
        assert 'shop.acme.com' in fake.object(f'evlet-{project_id}-tls')['subjects']
        # Caddy reloads its Caddyfile and drops the route: the endpoint puts it back.
        with fake.lock:
            fake.config['apps']['http']['servers']['srv0']['routes'].pop(0)
        until(lambda: routed(fake, project_id), seconds=30)
        # Caddy refuses loads: the endpoint keeps running, says so and tries again with backoff.
        fake.refusing, before = True, len(fake.writes())
        main(['host', 'disable', 'acme', 'shop.acme.com', '--project', str(sites)])
        until(lambda: len(fake.writes()) > before, seconds=20)
        refused = len(fake.writes())
        time.sleep(4)
        assert len(fake.writes()) > refused, 'no retry'
        assert served.process.poll() is None and served.ask('erp.acme.com') == 200
        assert 'shop.acme.com' in routed(fake, project_id)[acme]  # the last good config stays
        fake.refusing = False
        until(lambda: 'shop.acme.com' not in routed(fake, project_id)[acme], seconds=60)
    finally:
        output = served.stop()
    assert served.process.returncode is not None
    assert 'Caddy refused' in output and 'trying again in' in output, output
