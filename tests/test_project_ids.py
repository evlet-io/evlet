"""Project ids (project.json), instance ids (evlet_instance) and the copied-folder check, on real projects and SQLite."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from evlet import identity
from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.cli.setup import setup
from evlet.framework import instance_identity as identity_of
from evlet.instances import check_identity, instance_identity, load_runtime, read_project
from evlet.writes import read_file


@pytest.fixture(autouse=True)
def environment_stub(monkeypatch):
    import importlib
    module = importlib.import_module('evlet.cli.setup')
    monkeypatch.setattr(module, 'create_environment', lambda root, framework: (root / '.venv').mkdir())


@pytest.fixture
def project(tmp_path):
    root = setup(tmp_path / 'project').resolve()
    create_app(root, 'notes')
    create_instance(root, 'acme')
    install(root, 'acme', app_name='notes', trust=True)
    yield root
    for name in list(sys.modules):
        if name == 'notes' or name.startswith('notes.'):
            del sys.modules[name]


def text_id(value):
    assert isinstance(value, str) and len(value) == 13 and value == value.lower()
    assert identity.encode(identity.decode(value)) == value
    return value


def copy_instance(root, source, target):
    import shutil
    shutil.copytree(root / 'instances' / source, root / 'instances' / target, ignore=shutil.ignore_patterns('.evlet.lock'))
    config = root / 'instances' / target / 'config.py'
    config.write_text(config.read_text().replace(f"name={source!r}", f"name={target!r}"))


def test_setup_writes_project_json(project):
    record = json.loads((project / 'project.json').read_text())
    assert set(record) == {'id', 'created', 'mode'} and record['mode'] == 'development'
    text_id(record['id'])
    assert read_project(project) == record
    # The creation date can be read from the id itself: its top 42 bits are milliseconds since 2026.
    assert record['created'].startswith('20')


def test_existing_project_gets_an_id_once(tmp_path, capsys):
    root = tmp_path / 'old'
    (root / 'apps').mkdir(parents=True)
    (root / 'instances').mkdir()
    main(['--project', str(root), 'instance', '--list'])
    first = capsys.readouterr()
    record = read_project(root)
    assert first.err.strip() == f'Project id: {record["id"]}'
    main(['--project', str(root), 'instance', '--list'])
    assert capsys.readouterr().err == ''
    assert read_project(root) == record


def test_unwritable_project_still_runs(tmp_path, capsys, monkeypatch):
    root = tmp_path / 'readonly'
    (root / 'apps').mkdir(parents=True)
    (root / 'instances').mkdir()
    import evlet.cli.project as module
    def refuse(path, value):
        raise PermissionError('read only')
    monkeypatch.setattr(module, 'atomic_json', refuse)
    main(['--project', str(root), 'instance', '--list'])
    assert capsys.readouterr().err == '' and read_project(root) is None


def test_invalid_project_json_is_refused(project, capsys):
    (project / 'project.json').write_text(json.dumps({'id': 'not an id', 'created': 'x', 'mode': 'development'}))
    with pytest.raises(SystemExit):
        main(['--project', str(project), 'project'])
    assert 'project.json must hold' in capsys.readouterr().err
    # Other commands still run, with a note.
    main(['--project', str(project), 'instance', '--list'])
    captured = capsys.readouterr()
    assert captured.out.startswith('acme\t') and 'project.json must hold' in captured.err


def test_instance_gets_an_id_named_for_its_folder(project, capsys):
    found = instance_identity(project / 'instances/acme')
    text_id(found['id'])
    assert found['name'] == 'acme' and found['created'] > 0
    main(['--project', str(project), 'instance', '--list'])
    assert capsys.readouterr().out.strip() == f'acme\tnotes\t{found["id"]}'
    # migrate keeps the id: it is made once per database.
    install(project, 'acme', trust=True)
    assert instance_identity(project / 'instances/acme') == found


def test_project_command_lists_ids(project, capsys):
    create_instance(project, 'empty')
    main(['--project', str(project), 'project'])
    out = capsys.readouterr().out
    record = read_project(project)
    acme = instance_identity(project / 'instances/acme')['id']
    assert f'Project id: {record["id"]}\n' in out and 'Mode: development\n' in out and 'Endpoint: none\n' in out
    assert f'  acme   {acme}\n' in out
    assert 'empty  no id yet' in out


def test_project_new_id_and_mode(project, capsys):
    before = read_project(project)
    main(['--project', str(project), 'project', 'mode', 'production'])
    assert read_project(project)['mode'] == 'production' and read_project(project)['id'] == before['id']
    main(['--project', str(project), 'project', 'new-id'])
    after = read_project(project)
    assert after['id'] != before['id'] and after['mode'] == 'production'
    assert f'(was {before["id"]})' in capsys.readouterr().out
    # Instances keep their own ids.
    assert instance_identity(project / 'instances/acme')['name'] == 'acme'


def test_copied_instance_refuses_to_serve_until_new_id(project, capsys):
    copy_instance(project, 'acme', 'copy')
    original = instance_identity(project / 'instances/acme')
    assert instance_identity(project / 'instances/copy') == original
    check_identity(project, 'acme')  # the original keeps serving
    with pytest.raises(ValueError, match='evlet instance new-id copy'):
        load_runtime(project, 'copy', serving=True)
    # Other commands on the copy still work, so it can be inspected and fixed.
    load_runtime(project, 'copy').database.close()
    main(['--project', str(project), 'project'])
    assert 'a copied folder; run evlet instance new-id copy' in capsys.readouterr().out
    main(['--project', str(project), 'instance', 'new-id', 'copy'])
    assert f'(was {original["id"]})' in capsys.readouterr().out
    copied = instance_identity(project / 'instances/copy')
    assert copied['id'] != original['id'] and copied['name'] == 'copy'
    assert instance_identity(project / 'instances/acme') == original
    load_runtime(project, 'copy', serving=True).database.close()


def test_instance_new_id_refuses_while_serving(project):
    from evlet.cli.project import instance_guard
    with instance_guard(project / 'instances/acme', serving=True):
        with pytest.raises(SystemExit):
            main(['--project', str(project), 'instance', 'new-id', 'acme'])


def test_instance_new_id_needs_a_database(project, capsys):
    create_instance(project, 'empty')
    with pytest.raises(SystemExit):
        main(['--project', str(project), 'instance', 'new-id', 'empty'])
    assert 'no database yet' in capsys.readouterr().err


def test_backup_and_restore_keep_the_id(project):
    from evlet.cli.backup import backup, restore
    found = instance_identity(project / 'instances/acme')
    archive = backup(project, 'acme')[0]
    restore(project, 'acme', archive)
    assert instance_identity(project / 'instances/acme') == found


def test_existing_database_gets_an_id_when_it_migrates(project):
    database = project / 'instances/acme/data/database.sqlite3'
    connection = sqlite3.connect(database)
    connection.execute('DROP TABLE evlet_instance')
    connection.execute("DELETE FROM evlet_framework_migrations WHERE name IN ('0018_instance_id', '0019_hosts', '0021_sites', '0022_manage_sites')")
    connection.commit()
    connection.close()
    lock = json.loads((project / 'instances/acme/apps.lock').read_text())
    lock['evlet']['schema'] = '0017_prompt_tickets'
    for path in (project / 'instances/acme/apps.lock',):
        path.write_text(json.dumps(lock))
    connection = sqlite3.connect(database)
    connection.execute('UPDATE evlet_installed_apps SET manifest = ?', (json.dumps(lock),))
    connection.commit()
    connection.close()
    assert instance_identity(project / 'instances/acme') is None
    with pytest.raises(ValueError, match='evlet migrate acme'):
        load_runtime(project, 'acme')
    result = install(project, 'acme', trust=True)
    assert result['framework'] == ['0018_instance_id', '0019_hosts', '0021_sites', '0022_manage_sites']
    found = instance_identity(project / 'instances/acme')
    assert text_id(found['id']) and found['name'] == 'acme'


def test_application_outside_a_project_has_an_unnamed_id(tmp_path):
    from evlet import Application, Database, Model, Registry, Text

    class Note(Model):
        title = Text()

    database = Database(tmp_path / 'app.sqlite3', Registry(Note))
    Application(database).initialize()
    with database.transaction() as tx:
        found = identity_of(tx.connection)
    assert text_id(found['id']) and found['name'] is None


def test_controls_shows_the_ids_to_admins(project):
    from support import http
    from evlet import User
    app = load_runtime(project, 'acme')
    try:
        app.initialize()
        admin = User(1, 'admin', 'admin')
        state = app.business.controls.state(admin, {'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})['instance']
        assert state['id'] == instance_identity(project / 'instances/acme')['id']
        assert state['project'] == read_project(project)['id']
        # Signed out, and on the pages anyone can open: never.
        for path in ('/api/_controls/state', '/health', '/api/auth/me', '/'):
            _, payload, _ = http(app, 'GET', path)
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            assert state['id'].encode() not in body and state['project'].encode() not in body
    finally:
        app.database.close()


def test_project_command_in_a_real_process(project):
    result = subprocess.run([sys.executable, '-m', 'evlet', '--project', str(project), 'project'], capture_output=True, text=True,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f'Project id: {read_project(project)["id"]}\n')
