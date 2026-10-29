import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from evlet import ConnectorError, HTTPClient
from evlet.cli import main, scaffold
from tests.test_connector import live_connector, signed, wire_server
from tests.test_business import hashed, PASSWORD


def test_scaffold_runs_an_authenticated_persistent_application(tmp_path):
    target = scaffold(tmp_path / 'starter')
    assert sorted(path.name for path in target.iterdir()) == ['.gitignore', 'README.md', 'app.py', 'models.py']
    with pytest.raises(FileExistsError): scaffold(target)
    root = str(Path(__file__).resolve().parents[1])
    code = '''from app import create_app
from models import Item
app = create_app(); app.initialize()
assert app.business.auth.setup_needed()
app.database.create(Item, title='Persistent')
restarted = create_app(); restarted.initialize()
assert restarted.database.get(Item, 1).title == 'Persistent'
assert restarted.business.auth.allow_setup
'''
    subprocess.run([sys.executable, '-c', code], cwd=target, env={**os.environ, 'PYTHONPATH': root}, check=True)
    assert (target / '.data' / 'application.sqlite3').is_file()


def test_cli_login_describe_and_logout_keep_credentials_private(live_connector, tmp_path, monkeypatch, capsys):
    file = tmp_path / 'session.json'
    monkeypatch.setattr('getpass.getpass', lambda prompt: PASSWORD)
    main(['login', '--url', f'http://alpha.localhost:{live_connector[2]}', '--username', 'admin', '--session', str(file)])
    output = capsys.readouterr().out
    client = HTTPClient.load(file)
    assert PASSWORD not in output and client.cookie not in output and client.csrf not in output
    with pytest.raises(SystemExit): main(['login', '--url', client.url, '--username', 'admin', '--session', str(file)])
    capsys.readouterr()
    main(['describe', '--session', str(file), '--model', 'project'])
    assert json.loads(capsys.readouterr().out)['name'] == 'project'
    main(['logout', '--session', str(file)])
    assert not file.exists()
    with pytest.raises(ConnectorError) as failure: client.get('/api/project/1')
    assert failure.value.status == 401


@pytest.mark.parametrize('invalidate', ['expiry', 'revocation'])
def test_logout_clears_ended_session_and_allows_sign_in_at_same_path(live_connector, tmp_path, monkeypatch, capsys, invalidate):
    client = signed(live_connector)
    file = tmp_path / 'session.json'; client.save(file)
    if invalidate == 'expiry':
        with live_connector[1]['alpha'].database.transaction() as tx:
            tx.connection.execute('UPDATE evlet_sessions SET expires=0')
    else:
        client.request('POST', '/api/auth/logout', {})
    main(['logout', '--session', str(file)])
    assert not file.exists()
    assert not capsys.readouterr().err
    monkeypatch.setattr('getpass.getpass', lambda prompt: PASSWORD)
    main(['login', '--url', client.url, '--username', 'admin', '--session', str(file)])
    assert HTTPClient.load(file).get('/api/_discovery')['tenant']['slug'] == 'alpha'
    assert file.stat().st_mode & 0o777 == 0o600
    main(['logout', '--session', str(file)])


def test_logout_retains_session_after_permission_denial(live_connector, tmp_path):
    client = signed(live_connector)
    file = tmp_path / 'session.json'
    HTTPClient(client.url, cookie=client.cookie, csrf='0' * 64).save(file)
    original = file.read_bytes()
    with pytest.raises(SystemExit) as failure:
        main(['logout', '--session', str(file)])
    assert failure.value.code == 1 and file.read_bytes() == original
    assert client.get('/api/_discovery')['tenant']['slug'] == 'alpha'


def test_logout_retains_session_after_server_failure(live_connector, tmp_path, monkeypatch):
    client = signed(live_connector)
    file = tmp_path / 'session.json'; client.save(file)
    original = file.read_bytes()
    def unavailable(self, method, path, body=None):
        assert (method, path) == ('POST', '/api/auth/logout')
        raise ConnectorError(500, 'Application request failed')
    with monkeypatch.context() as patch:
        patch.setattr(HTTPClient, 'request', unavailable)
        with pytest.raises(SystemExit) as failure:
            main(['logout', '--session', str(file)])
    assert failure.value.code == 1 and file.read_bytes() == original
    main(['logout', '--session', str(file)])
    assert not file.exists()


def test_logout_retains_session_when_connection_drops(wire_server, tmp_path):
    client, requests = wire_server
    file = tmp_path / 'session.json'; client.save(file)
    original = file.read_bytes()
    with pytest.raises(SystemExit) as failure:
        main(['logout', '--session', str(file)])
    assert failure.value.code == 1 and file.read_bytes() == original
    assert [path for path, cookie in requests] == ['/api/auth/logout']
