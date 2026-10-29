"""Exercise two disposable tenants over real HTTP, optionally through Vite/Caddy."""

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evlet import TenantRegistry
from evlet.deployment import render_deployment
from examples.playground.manage import provision


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


class TLSConnection(http.client.HTTPSConnection):
    def __init__(self, port, context, hostname):
        super().__init__('127.0.0.1', port, timeout=3, context=context)
        self.tenant_hostname = hostname

    def connect(self):
        http.client.HTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.tenant_hostname)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', action='store_true')
    parser.add_argument('--caddy', action='store_true')
    args = parser.parse_args()
    if args.frontend and args.caddy:
        parser.error('Choose Vite or Caddy for this run')
    if args.caddy and not shutil.which('caddy'):
        parser.error('Install Caddy before running this check')
    processes = []
    with tempfile.TemporaryDirectory(prefix='evlet-tenants-') as temporary:
        work = Path(temporary).resolve()
        backend_port, public_port = free_port(), free_port()
        while public_port == backend_port:
            public_port = free_port()
        production = args.caddy
        catalog = TenantRegistry.create(work / 'state', managed_domains=('example.test',) if production else (), development=not production, ports=(backend_port, public_port))
        password = secrets.token_urlsafe(32)
        suffix = '.example.test' if production else '.localhost'
        for slug in ('alpha', 'beta'):
            catalog.create_tenant(slug, slug.title(), slug + suffix)
            provision(catalog, slug, 'smoke', password)
        environment = {**os.environ, 'EVLET_TENANT_ROOT': str(catalog.root), 'EVLET_BACKEND_PORT': str(backend_port)}
        context = None
        target_port = public_port if args.frontend or args.caddy else backend_port
        log = (work / 'servers.log').open('w+')
        def start(command, env=environment):
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            return process
        def request(slug, path, method='GET', data=None, auth=None, headers=None):
            host = slug + suffix
            authority = host if production else f'{host}:{target_port}'
            values = {'Host': authority, 'Origin': ('https://' if production else 'http://') + authority, 'X-Evlet-Request': '1', 'Content-Type': 'application/json', **(headers or {})}
            if auth:
                values.update({'Cookie': auth['cookie'], 'X-CSRF-Token': auth['csrf']})
            connection = TLSConnection(target_port, context, host) if production else http.client.HTTPConnection('127.0.0.1', target_port, timeout=3)
            try:
                connection.request(method, path, None if data is None else json.dumps(data), values)
                response = connection.getresponse()
                raw = response.read()
                payload = json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw
                return response.status, payload, response.getheader('Set-Cookie')
            finally:
                connection.close()
        try:
            server = [sys.executable, '-m', 'uvicorn', 'examples.playground.tenants:create_app', '--factory', '--host', '127.0.0.1', '--port', str(backend_port)]
            server += ['--proxy-headers', '--forwarded-allow-ips', '127.0.0.1'] if production else ['--no-proxy-headers']
            backend = start(server)
            if args.frontend:
                start(['node', 'frontend/node_modules/vite/bin/vite.js', 'frontend', '--host', '127.0.0.1', '--port', str(public_port), '--strictPort'])
            if args.caddy:
                config = render_deployment(catalog)['Caddyfile'].replace('127.0.0.1:8000', f'127.0.0.1:{backend_port}').replace('/opt/evlet/frontend/dist', str(ROOT / 'frontend/dist'))
                # Local TLS uses an isolated CA, never the system trust store or ACME.
                config = config.replace('root * ' + str(ROOT / 'frontend/dist'), 'root * "' + str(ROOT / 'frontend/dist') + '"')
                for slug in ('alpha', 'beta'):
                    config = config.replace(slug + suffix + ' {', f'https://{slug}{suffix}:{public_port} {{\n    bind 127.0.0.1\n    tls internal')
                config = '{\n    admin off\n    auto_https disable_redirects\n    skip_install_trust\n    persist_config off\n}\n' + config
                config_path = work / 'Caddyfile'; config_path.write_text(config)
                start(['caddy', 'run', '--config', str(config_path), '--adapter', 'caddyfile'], {**environment, 'XDG_DATA_HOME': str(work / 'caddy-data'), 'XDG_CONFIG_HOME': str(work / 'caddy-config')})
            for attempt in range(100):
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError('A smoke server exited')
                try:
                    if production and context is None:
                        context = ssl.create_default_context(cafile=str(work / 'caddy-data/caddy/pki/authorities/local/root.crt'))
                    if request('alpha', '/health')[0] == 200:
                        break
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError('Smoke servers did not become ready')
            credentials = {}
            for slug in ('alpha', 'beta'):
                status, session, _ = request(slug, '/api/auth/session')
                assert status == 200 and session['tenant']['slug'] == slug and not session['setup']
                status, login, cookie = request(slug, '/api/auth/login', 'POST', {'username': 'smoke', 'password': password})
                assert status == 200
                if production: assert cookie.startswith('__Host-evlet_session=') and '; Secure' in cookie
                auth = credentials[slug] = {'cookie': cookie.split(';')[0], 'csrf': login['csrf']}
                status, project, _ = request(slug, '/api/project', 'POST', {'name': slug, 'children': {'tasks': [{'title': slug, 'done': True}]}}, auth)
                assert status == 201 and project['id'] == 1
                assert request(slug, '/api/project/1/actions/start', 'POST', {}, auth)[0] == 200
                assert request(slug, '/api/project/1/attachments', 'POST', {'name': 'private.txt', 'content': base64.b64encode(slug.encode()).decode()}, auth)[0] == 201
            for slug, other in (('alpha', 'beta'), ('beta', 'alpha')):
                auth = credentials[slug]
                assert request(slug, '/api/project', auth=auth)[1]['items'][0]['name'] == slug
                assert request(slug, '/api/project/1/attachments/1', auth=auth)[1] == slug.encode()
                assert request(slug, '/api/project/1/attachments/1', auth=credentials[other])[0] == 401
                assert request(slug, '/api/auth/session', auth=auth, headers={'X-Forwarded-Host': other + suffix, 'X-Forwarded-For': '203.0.113.12'})[1]['tenant']['slug'] == slug
            if not production:
                assert request('unknown', '/health')[0] == 421
            elif (ROOT / 'frontend/dist/index.html').exists():
                assert b'<html' in request('alpha', '/')[1]
            backend.terminate(); backend.wait(timeout=10)
            processes.remove(backend)
            start(server)
            for attempt in range(100):
                try:
                    if request('alpha', '/api/project/1/attachments/1', auth=credentials['alpha'])[1] == b'alpha': break
                except (OSError, http.client.HTTPException, ValueError): pass
                time.sleep(0.1)
            else: raise AssertionError('Tenant session/file did not survive restart')
            catalog.set_active('alpha', False)
            assert request('alpha', '/api/auth/session', auth=credentials['alpha'])[0] == 421
            assert request('beta', '/api/project/1', auth=credentials['beta'])[0] == 200
            print('Two-tenant smoke passed: sign-in, records, children, actions, files, replay rejection, restart, and revocation' + (' through local Caddy HTTPS.' if production else ' through Vite.' if args.frontend else '.'))
        except BaseException:
            log.flush(); log.seek(0); print(log.read(), file=sys.stderr)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None: process.terminate()
            for process in processes:
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            log.close()


if __name__ == '__main__':
    main()
