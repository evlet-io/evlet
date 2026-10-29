import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from evlet import ConnectorError, HTTPClient
from evlet.cli import main, scaffold
from tests.test_connector import live_connector, signed
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
