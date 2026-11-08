"""Verify the installed CLI over real HTTP, without Node.js or a source checkout."""

from contextlib import contextmanager
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from evlet import HTTPClient


def command(*args, check=True):
    return subprocess.run([sys.executable, '-I', '-m', 'evlet', *map(str, args)], check=check, capture_output=True, text=True)


@contextmanager
def server(*args):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryFile(mode='w+') as log:
        process = subprocess.Popen([sys.executable, '-I', '-m', 'evlet', *map(str, args), '--port', str(port)], stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        url = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                try:
                    with urlopen(url + ('/' if args[0] == 'docs' else '/health'), timeout=1) as response:
                        assert response.status == 200
                        break
                except OSError:
                    time.sleep(.1)
            else:
                log.seek(0)
                raise RuntimeError('Server was not ready: ' + log.read())
            yield url
        finally:
            if process.poll() is None:
                if os.name == 'nt':
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def enrol(url, username, password):
    """Instances require two-step sign-in for admins: set it up once as the browser does and keep the backup codes."""
    import base64
    from evlet.settings import totp
    client = HTTPClient(url)
    data, _ = client.request('POST', '/api/auth/login', {'username': username, 'password': password})
    if data.get('two_step') != 'setup':
        return []
    raw = base64.b32decode(data['secret'] + '=' * (-len(data['secret']) % 8))
    data, _ = client.request('POST', '/api/auth/login', {'ticket': data['ticket'], 'code': totp(raw, int(time.time() // 30))})
    return list(data['backup_codes'])


def main():
    password = 'temporary-package-check-password'
    with tempfile.TemporaryDirectory(prefix='evlet-cli-smoke-') as directory:
        root = Path(directory) / 'project'
        command('setup', root)
        command('app', 'notes', '--project', root)
        command('instance', 'acme', '--project', root)
        command('install', 'notes', '--instance', 'acme', '--trust', '--project', root)
        with server('serve', 'acme', '--project', root) as url:
            with urlopen(url) as response:
                assert b'<div id="evlet-app">' in response.read()
            client = HTTPClient(url)
            client.request('POST', '/api/auth/setup', {'username': 'owner', 'password': password})
            codes = enrol(url, 'owner', password)
            client = client.login('owner', password, code=codes.pop)
            client.request('POST', '/api/notesitem', {'title': 'Survives restart'})
            assert client.get('/api/notesitem')['items'][0]['title'] == 'Survives restart'
            busy = command('migrate', 'acme', '--trust', '--project', root, check=False)
            assert busy.returncode == 1 and 'in use' in busy.stderr
            stream = urlopen(Request(url + '/api/_events/stream', headers={'Cookie':client.cookie}), timeout=10)
            assert stream.readline().startswith(b'id:')
            shutdown_started = time.monotonic()
        assert time.monotonic() - shutdown_started < 5, 'Open event stream delayed shutdown'
        stream.close()
        with server('dev', '--instance', 'acme', '--project', root) as url:
            client = HTTPClient(url).login('owner', password, code=codes.pop)
            assert client.get('/api/notesitem')['items'][0]['title'] == 'Survives restart'
            stream = urlopen(Request(url + '/api/_events/stream', headers={'Cookie':client.cookie}), timeout=10)
            assert stream.readline().startswith(b'id:')
            reload_started = time.monotonic()
            model = root / 'apps/notes/models/item.py'
            model.write_text(model.read_text().replace("plural = 'Items'", "plural = 'Reloaded notes'"))
            for _ in range(80):
                try:
                    if client.get('/api/schema')['models'][0]['plural'] == 'Reloaded notes':
                        break
                except Exception:
                    pass
                time.sleep(.15)
            else:
                raise RuntimeError('Python development reload did not update the UI schema')
            assert time.monotonic() - reload_started < 5, 'Open event stream delayed reload'
            stream.close()
        command('migrate', 'acme', '--trust', '--project', root)
        with server('serve', 'acme', '--project', root) as url:
            client = HTTPClient(url).login('owner', password, code=codes.pop)
            assert client.get('/api/notesitem')['items'][0]['title'] == 'Survives restart'
        before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
        with server('playground', '--no-link') as url:
            client = HTTPClient(url)
            client.request('POST', '/api/auth/setup', {'username': 'owner', 'password': password})
            client = client.login('owner', password)
            assert client.get('/api/customer')['total'] == 1
        with server('playground') as url:
            port = url.rsplit(':', 1)[1]
            output = command('dev-link', '--playground', '--port', port).stdout
            assert 'Sign in as demo-admin' in output and 'password' not in output.lower()
            target = urlsplit(output.split()[-1])
            connection = HTTPConnection('127.0.0.1', int(port), timeout=10)
            connection.request('GET', target.path + '?' + target.query)
            response = connection.getresponse()
            assert response.status == 302 and response.getheader('Location') == '/'
            cookie = response.getheader('Set-Cookie').split(';')[0]
            connection.close()
            with urlopen(Request(url + '/api/auth/session', headers={'Cookie': cookie}), timeout=10) as session:
                assert json.load(session)['user']['username'] == 'demo-admin'
        with server('docs') as url:
            with urlopen(url + '/cli.md') as response:
                assert b'evlet setup myproject' in response.read()
        assert sorted(str(path.relative_to(root)) for path in root.rglob('*')) == before
    print('Installed CLI HTTP smoke passed: setup, login, records, restart, live Python reload, instance locking, Playground, its sign-in link and offline docs')


if __name__ == '__main__':
    main()
