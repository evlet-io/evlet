"""Framework schema versioning: evlet migrate runs ordered framework steps after a backup."""

from contextlib import closing
import json
import sqlite3
import time

import pytest

from evlet import Application, Database, LocalAuth, SchemaError
from evlet import framework, writes
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.instances import database_state, read_lock
from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401


def move_calls(connection):
    # The shape of a real non-additive change: copy rows to a new table, then drop the old one.
    connection.execute('CREATE TABLE IF NOT EXISTS evlet_moved_calls (id INTEGER PRIMARY KEY, tool TEXT NOT NULL)')
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_legacy_calls'").fetchone():
        connection.execute('INSERT INTO evlet_moved_calls (id, tool) SELECT id, tool FROM evlet_legacy_calls')
        connection.execute('DROP TABLE evlet_legacy_calls')


def broken_move(connection):
    connection.execute('DROP TABLE evlet_legacy_calls')
    raise RuntimeError('simulated framework step failure')


# Recent enough to outlive the call log's 90-day retention.
NOW = int(time.time()) - 60
MOVE = framework.FrameworkStep('0099_move_calls', 'Move legacy call rows to evlet_moved_calls.', move_calls)


def database(project):
    return project / 'instances/acme/data/database.sqlite3'


def as_release_0_2_0(project):
    """Rewrite an installed instance into what Evlet 0.2.0 left: no ledger and no evlet lock entry."""
    path = project / 'instances/acme'
    lock = json.loads((path / 'apps.lock').read_text())
    del lock['evlet']
    (path / 'apps.lock').write_text(json.dumps(lock))
    with closing(writes.connect(database(project))) as connection:
        connection.execute('DROP TABLE evlet_framework_migrations')
        connection.execute('DROP TABLE evlet_tool_calls')
        connection.execute('UPDATE evlet_installed_apps SET manifest=? WHERE id=1', (json.dumps(lock),))
        connection.execute('CREATE TABLE evlet_legacy_calls (id INTEGER PRIMARY KEY, tool TEXT NOT NULL)')
        connection.execute("INSERT INTO evlet_legacy_calls VALUES (7, 'describe')")
        # Evlet 0.2.0's MCP call log, which step 0002_tool_calls moves into evlet_tool_calls.
        connection.execute("CREATE TABLE evlet_mcp_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL REFERENCES evlet_mcp_clients(id) ON DELETE CASCADE, "
                           "at INTEGER NOT NULL, tool TEXT NOT NULL, model TEXT, kind TEXT NOT NULL CHECK(kind IN ('read','write')), outcome TEXT NOT NULL CHECK(outcome IN ('ok','denied','failed')))")
        # 0.2.0 had no built-in Administrator; step 0008_administrator adds it.
        connection.execute("DELETE FROM evlet_user_profile")
        connection.execute("DELETE FROM evlet_users")
        connection.execute("INSERT INTO evlet_users (id, username, password, role) VALUES (1, 'owner', 'x', 'admin')")
        connection.execute("INSERT INTO evlet_oauth_clients (id, name, redirect_uris, created_at) VALUES ('oc', 'Desktop client', '[]', 0)")
        connection.execute("INSERT INTO evlet_mcp_clients (id, user_id, oauth_client, client_name, created_at) VALUES (3, 1, 'oc', 'Desktop client', 0)")
        connection.execute("INSERT INTO evlet_mcp_calls (client_id, at, tool, model, kind, outcome) VALUES (3, ?, 'list_records', 'notesitem', 'read', 'ok')", (NOW,))
        connection.commit()


def tables(path):
    """The tables of a database file and of its sessions, logs and Eva files (evlet/writes.py)."""
    with closing(writes.connect(path)) as connection:
        return writes.tables(connection)


def test_install_records_framework_version_in_lock_manifest_and_ledger(project):
    install(project, 'acme', app_name='notes', trust=True)
    lock = read_lock(project / 'instances/acme')
    assert lock['evlet'] == framework.state() == {'version': framework.version(), 'schema': '0010_split_files'}
    assert database_state(database(project)) == lock
    with closing(sqlite3.connect(database(project))) as connection:
        rows = connection.execute('SELECT position, name, checksum, evlet_version FROM evlet_framework_migrations').fetchall()
    assert rows == [(i + 1, step.name, step.checksum(), framework.version()) for i, step in enumerate(framework.STEPS)]


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
    assert 'Framework migrations: 0001_startup_tables, 0002_tool_calls, 0003_record_names, 0004_record_times, 0005_unsaved_changes, 0006_fixed_ids, 0007_money_scale, 0008_administrator, 0009_admin_username, 0010_split_files, 0099_move_calls' in out
    backup = out.split('Backup: ')[1].strip()
    # The backup is the instance exactly as 0.2.0 left it.
    assert {'evlet_legacy_calls', 'evlet_mcp_calls'} <= tables(backup) and 'evlet_framework_migrations' not in tables(backup)
    assert {'evlet_moved_calls', 'evlet_tool_calls', 'evlet_framework_migrations'} <= tables(database(project))
    assert not {'evlet_legacy_calls', 'evlet_mcp_calls'} & tables(database(project))
    with closing(writes.connect(database(project))) as connection:
        assert connection.execute('SELECT id, tool FROM evlet_moved_calls').fetchall() == [(7, 'describe')]
        assert connection.execute('SELECT at, user_id, origin, client, client_id, tool, model, kind, outcome FROM evlet_tool_calls').fetchall() == \
            [(NOW, 1, 'mcp', 'Desktop client', 3, 'list_records', 'notesitem', 'read', 'ok')]
        assert [row[0] for row in connection.execute('SELECT name FROM evlet_framework_migrations ORDER BY position')] == \
            ['0001_startup_tables', '0002_tool_calls', '0003_record_names', '0004_record_times', '0005_unsaved_changes', '0006_fixed_ids', '0007_money_scale', '0008_administrator', '0009_admin_username', '0010_split_files', '0099_move_calls']
    assert read_lock(project / 'instances/acme')['evlet'] == {'version': framework.version(), 'schema': '0099_move_calls'}
    app = runtime(project)
    assert [record.title for record in app.database.list(app.registry.models['notesitem'])] == ['Keep this note']
    app.database.close()


def test_failed_framework_step_leaves_database_and_lock_untouched(project, monkeypatch):
    install(project, 'acme', app_name='notes', trust=True)
    as_release_0_2_0(project)
    lock = (project / 'instances/acme/apps.lock').read_bytes()
    released = framework.STEPS
    monkeypatch.setattr(framework, 'STEPS', (*released, framework.FrameworkStep('0099_broken', 'Fails halfway.', broken_move)))
    with pytest.raises(RuntimeError, match='simulated framework step failure'):
        install(project, 'acme')
    assert (project / 'instances/acme/apps.lock').read_bytes() == lock
    assert not (project / 'instances/acme/.install-journal.json').exists()
    assert {'evlet_legacy_calls', 'evlet_mcp_calls'} <= tables(database(project))
    assert not {'evlet_framework_migrations', 'evlet_tool_calls'} & tables(database(project))
    # A fixed release then upgrades the same instance.
    monkeypatch.setattr(framework, 'STEPS', (*released, MOVE))
    assert install(project, 'acme')['framework'] == ['0001_startup_tables', '0002_tool_calls', '0003_record_names', '0004_record_times', '0005_unsaved_changes', '0006_fixed_ids', '0007_money_scale', '0008_administrator', '0009_admin_username', '0010_split_files', '0099_move_calls']


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
    with pytest.raises(SchemaError, match='framework migrations are pending \\(0099_move_calls\\); stop serving and run evlet migrate'):
        app.initialize()
    app.database.close()
    assert 'evlet_moved_calls' not in tables(database(project))


def test_database_from_a_newer_or_different_evlet_is_refused(project, monkeypatch):
    install(project, 'acme', app_name='notes', trust=True)
    with closing(sqlite3.connect(database(project))) as connection:
        connection.execute("INSERT INTO evlet_framework_migrations VALUES (99, '0099_future', 'x', '9.0.0', 0)")
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
    monkeypatch.setattr(framework, 'STEPS', (framework.FrameworkStep('0001_startup_tables', 'Edited after release.'), *framework.STEPS[1:]))
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
        assert [row[0] for row in connection.execute('SELECT name FROM evlet_framework_migrations ORDER BY position')] == \
            ['0001_startup_tables', '0002_tool_calls', '0003_record_names', '0004_record_times', '0005_unsaved_changes', '0006_fixed_ids', '0007_money_scale', '0008_administrator', '0009_admin_username', '0010_split_files', '0099_move_calls']


def test_plan_lists_pending_framework_steps_without_writing(project, capsys):
    install(project, 'acme', app_name='notes', trust=True)
    as_release_0_2_0(project)
    before = database(project).read_bytes()
    main(['migrate', 'acme', '--plan', '--project', str(project)])
    out = capsys.readouterr().out
    assert 'Pending framework migrations (run after a backup):\n  0001_startup_tables' in out
    assert '  0002_tool_calls\n    Move the MCP call log into evlet_tool_calls and drop evlet_mcp_calls.' in out
    assert database(project).read_bytes() == before
    install(project, 'acme')
    main(['migrate', 'acme', '--plan', '--project', str(project)])
    assert 'Pending framework migrations' not in capsys.readouterr().out


def test_tool_calls_step_copes_with_missing_mcp_tables(tmp_path):
    from evlet import Model, Registry, Text

    class Note(Model):
        title = Text()

    path = tmp_path / 'app.sqlite3'
    with closing(sqlite3.connect(path)) as connection:
        # A call log whose clients table is gone: nothing can be attributed, so the log is dropped.
        connection.execute('CREATE TABLE evlet_mcp_calls (id INTEGER PRIMARY KEY, client_id INTEGER, at INTEGER, tool TEXT, model TEXT, kind TEXT, outcome TEXT)')
        connection.execute("INSERT INTO evlet_mcp_calls VALUES (1, 9, 0, 'describe', NULL, 'read', 'ok')")
        connection.commit()
    Application(Database(path, Registry(Note))).initialize()
    assert 'evlet_mcp_calls' not in tables(path) and 'evlet_tool_calls' in tables(path)
    with closing(writes.connect(path)) as connection:
        assert connection.execute('SELECT count(*) FROM evlet_tool_calls').fetchone()[0] == 0


def slow_step(connection):
    time.sleep(0.3)


def test_concurrent_starts_apply_steps_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from evlet import Model, Registry, Text

    class Note(Model):
        title = Text()

    registry = Registry(Note)
    Database(tmp_path / 'app.sqlite3', registry).initialize()
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, framework.FrameworkStep('0099_slow', 'Wait.', slow_step)))
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(framework.upgrade, [Database(tmp_path / 'app.sqlite3', registry) for _ in range(4)]))
    assert sorted(results, key=len) == [[], [], [], [step.name for step in framework.STEPS]]


# Existing databases store these in evlet_framework_migrations and startup refuses a database whose
# stored checksums differ, so they must survive moving evlet/framework.py into a layer package.
RELEASED_CHECKSUMS = {
    '0001_startup_tables': 'fcf94383a319e164059c5bae99e98b355b9ddbfc88aa7c25a861595083774fb5',
    '0002_tool_calls': '6a74154ddfb44cf78f1b2104f350c47205c6af6543c8ac3467d0f24f2c614726',
    '0003_record_names': 'cbb8c4161abe35023adc77f3f2f57b1ccb2a57fbf8f10825b5299da1bf77c726',
    '0004_record_times': '0f53a6cf4b12965ec44643521eb6fd1cb8e1ba5ecbfd53aed99002658d640ec9',
    '0005_unsaved_changes': '8865d2f82ad60c9b3c0e98056196fbea3a7d1912558d572d6905900fcc49ee43',
    '0006_fixed_ids': '58dc4f13eee9729cd65ce9ca36db181a90101d0db60d9f21d393e73a79c6cc1b',
    '0007_money_scale': 'acd90b0531d10785b9c3c07dbb78455473e2da563b3eeba16325eb05b203314d',
    '0008_administrator': '2216c1887131fbdb40f77fc043d0223e79a6b92dd5793bc5f455bc017ba8f65b',
    '0009_admin_username': '93214ff2f9ded75e9ade48ef3a41159e9681fb95bd7ef5fe27ac64c270e117af',
    '0010_split_files': '025f9ac009fd65d7b0230fa3fb3694aeae047c50e959ba01b04a795cc1c4f545',
}


def test_released_step_checksums_never_change():
    assert {step.name: step.checksum() for step in framework.STEPS[:len(RELEASED_CHECKSUMS)]} == RELEASED_CHECKSUMS


def test_step_checksum_does_not_depend_on_the_module_path(monkeypatch):
    step = framework.STEPS[1]
    before = step.checksum()
    monkeypatch.setattr(step.run, '__module__', 'evlet.web.framework')
    assert step.checksum() == before
