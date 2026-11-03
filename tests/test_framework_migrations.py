"""Framework schema versioning: evlet migrate runs ordered framework steps after a backup."""

from contextlib import closing
import json
import sqlite3

import pytest

from evlet import Application, Database, LocalAuth, SchemaError
from evlet import framework
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.instances import database_state, read_lock
from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401


def move_calls(connection, postgres):
    # The shape of a real non-additive change: copy rows to a new table, then drop the old one.
    connection.execute('CREATE TABLE IF NOT EXISTS evlet_moved_calls (id INTEGER PRIMARY KEY, tool TEXT NOT NULL)')
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_legacy_calls'").fetchone():
        connection.execute('INSERT INTO evlet_moved_calls (id, tool) SELECT id, tool FROM evlet_legacy_calls')
        connection.execute('DROP TABLE evlet_legacy_calls')


def broken_move(connection, postgres):
    connection.execute('DROP TABLE evlet_legacy_calls')
    raise RuntimeError('simulated framework step failure')


MOVE = framework.FrameworkStep('0002_move_calls', 'Move legacy call rows to evlet_moved_calls.', move_calls)


def database(project):
    return project / 'instances/acme/data/database.sqlite3'


def as_release_0_2_0(project):
    """Rewrite an installed instance into what Evlet 0.2.0 left: no ledger and no evlet lock entry."""
    path = project / 'instances/acme'
    lock = json.loads((path / 'apps.lock').read_text())
    del lock['evlet']
    (path / 'apps.lock').write_text(json.dumps(lock))
    with closing(sqlite3.connect(database(project))) as connection:
        connection.execute('DROP TABLE evlet_framework_migrations')
        connection.execute('UPDATE evlet_installed_apps SET manifest=? WHERE id=1', (json.dumps(lock),))
        connection.execute('CREATE TABLE evlet_legacy_calls (id INTEGER PRIMARY KEY, tool TEXT NOT NULL)')
        connection.execute("INSERT INTO evlet_legacy_calls VALUES (7, 'describe')")
        connection.commit()


def tables(path):
    with closing(sqlite3.connect(path)) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_install_records_framework_version_in_lock_manifest_and_ledger(project):
    install(project, 'acme', app_name='notes', trust=True)
    lock = read_lock(project / 'instances/acme')
    assert lock['evlet'] == framework.state() == {'version': framework.version(), 'schema': '0001_startup_tables'}
    assert database_state(database(project)) == lock
    with closing(sqlite3.connect(database(project))) as connection:
        rows = connection.execute('SELECT position, name, checksum, evlet_version FROM evlet_framework_migrations').fetchall()
    assert rows == [(1, '0001_startup_tables', framework.STEPS[0].checksum(), framework.version())]


def test_upgrade_from_0_2_0_instance_backs_up_then_applies_non_additive_step(project, monkeypatch, capsys):
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    app.database.create(app.registry.models['notesitem'], title='Keep this note')
    app.database.close()
    as_release_0_2_0(project)
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, MOVE))
    with pytest.raises(ValueError, match='migrated with Evlet a release before framework versioning; stop serving and run evlet migrate acme'):
        runtime(project)

    main(['migrate', 'acme', '--project', str(project)])
    out = capsys.readouterr().out
    assert 'Framework migrations: 0001_startup_tables, 0002_move_calls' in out
    backup = out.split('Backup: ')[1].strip()
    # The backup is the instance exactly as 0.2.0 left it.
    assert 'evlet_legacy_calls' in tables(backup) and 'evlet_framework_migrations' not in tables(backup)
    assert {'evlet_moved_calls', 'evlet_framework_migrations'} <= tables(database(project))
    assert 'evlet_legacy_calls' not in tables(database(project))
    with closing(sqlite3.connect(database(project))) as connection:
        assert connection.execute('SELECT id, tool FROM evlet_moved_calls').fetchall() == [(7, 'describe')]
        assert [row[0] for row in connection.execute('SELECT name FROM evlet_framework_migrations ORDER BY position')] == ['0001_startup_tables', '0002_move_calls']
    assert read_lock(project / 'instances/acme')['evlet'] == {'version': framework.version(), 'schema': '0002_move_calls'}
    app = runtime(project)
    assert [record.title for record in app.database.list(app.registry.models['notesitem'])] == ['Keep this note']
    app.database.close()


def test_failed_framework_step_leaves_database_and_lock_untouched(project, monkeypatch):
    install(project, 'acme', app_name='notes', trust=True)
    as_release_0_2_0(project)
    lock = (project / 'instances/acme/apps.lock').read_bytes()
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, framework.FrameworkStep('0002_broken', 'Fails halfway.', broken_move)))
    with pytest.raises(RuntimeError, match='simulated framework step failure'):
        install(project, 'acme')
    assert (project / 'instances/acme/apps.lock').read_bytes() == lock
    assert not (project / 'instances/acme/.install-journal.json').exists()
    assert 'evlet_legacy_calls' in tables(database(project))
    assert 'evlet_framework_migrations' not in tables(database(project))
    # A fixed release then upgrades the same instance.
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS[:1], MOVE))
    assert install(project, 'acme')['framework'] == ['0001_startup_tables', '0002_move_calls']


def test_serve_refuses_a_newer_evlet_until_migrate(project, monkeypatch):
    install(project, 'acme', app_name='notes', trust=True)
    monkeypatch.setattr(framework, 'version', lambda: '9.9.9')
    with pytest.raises(ValueError, match='Evlet 9.9.9 is installed but instance acme was migrated with Evlet 0.*run evlet migrate acme'):
        runtime(project)
    result = install(project, 'acme')
    assert result['backup'] and result['framework'] == []
    assert read_lock(project / 'instances/acme')['evlet']['version'] == '9.9.9'
    runtime(project).database.close()


def test_instance_runtime_never_applies_pending_framework_steps(project, monkeypatch):
    install(project, 'acme', app_name='notes', trust=True)
    from evlet.instances import load_runtime
    app = load_runtime(project, 'acme')
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, MOVE))
    with pytest.raises(SchemaError, match='framework migrations are pending \\(0002_move_calls\\); stop serving and run evlet migrate'):
        app.initialize()
    app.database.close()
    assert 'evlet_moved_calls' not in tables(database(project))


def test_database_from_a_newer_or_different_evlet_is_refused(project, monkeypatch):
    install(project, 'acme', app_name='notes', trust=True)
    with closing(sqlite3.connect(database(project))) as connection:
        connection.execute("INSERT INTO evlet_framework_migrations VALUES (2, '0002_future', 'x', '9.0.0', 0)")
        connection.commit()
    monkeypatch.setattr(framework, 'version', lambda: '0.1.9')
    with pytest.raises(SchemaError, match='migrated by a different or newer Evlet release'):
        install(project, 'acme')


def test_changed_released_step_is_refused(tmp_path, monkeypatch):
    from evlet import Model, Registry, Text

    class Note(Model):
        title = Text()

    db = Database(tmp_path / 'app.sqlite3', Registry(Note))
    Application(db).initialize()
    monkeypatch.setattr(framework, 'STEPS', (framework.FrameworkStep('0001_startup_tables', 'Edited after release.'),))
    with pytest.raises(SchemaError, match='different or newer Evlet'):
        Application(db).initialize()


def test_development_applications_bootstrap_and_apply_steps_automatically(tmp_path, monkeypatch):
    from evlet import Model, Registry, Text

    class Note(Model):
        title = Text()

    db = Database(tmp_path / 'app.sqlite3', Registry(Note))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    app.initialize()
    app.initialize()
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, MOVE))
    app.initialize()
    with closing(sqlite3.connect(tmp_path / 'app.sqlite3')) as connection:
        assert [row[0] for row in connection.execute('SELECT name FROM evlet_framework_migrations ORDER BY position')] == ['0001_startup_tables', '0002_move_calls']
