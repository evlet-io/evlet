"""Verify the installed CLI over real HTTP, without Node.js or a source checkout."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
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
                assert b'<div id="root">' in response.read()
            client = HTTPClient(url)
            client.request('POST', '/api/auth/setup', {'username': 'owner', 'password': password})
            client = client.login('owner', password)
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
            client = HTTPClient(url).login('owner', password)
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
            client = HTTPClient(url).login('owner', password)
            assert client.get('/api/notesitem')['items'][0]['title'] == 'Survives restart'
        before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
        with server('playground') as url:
            client = HTTPClient(url)
            client.request('POST', '/api/auth/setup', {'username': 'owner', 'password': password})
            client = client.login('owner', password)
            assert client.get('/api/customer')['total'] == 1
            assert any(view['name'] == 'projects_demo' for view in client.get('/api/ui')['views'])
        with server('docs') as url:
            with urlopen(url + '/cli.md') as response:
                assert b'evlet setup myproject' in response.read()
        assert sorted(str(path.relative_to(root)) for path in root.rglob('*')) == before
    print('Installed CLI HTTP smoke passed: setup, login, records, restart, live Python reload, instance locking, Playground and offline docs')


if __name__ == '__main__':
    main()
