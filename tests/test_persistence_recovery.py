"""Complete SQLite generations, online snapshots and replay after interrupted publication."""

from contextlib import closing
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from evlet import Database, Registry, writes
from evlet.cli.backup import restore
from evlet.cli.project import atomic_json, instance_guard
from evlet.instances import require_consistent

installer = importlib.import_module('evlet.cli.install')


def generation(main, number):
    main.parent.mkdir(parents=True, exist_ok=True)
    state = {'apps': {}, 'evlet': {'version': str(number), 'schema': 'test'}}
    for path in writes.files(main):
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE marker(n)')
            connection.execute('INSERT INTO marker VALUES (?)', (number,))
            if path == main:
                connection.execute('CREATE TABLE evlet_installed_apps(id INTEGER PRIMARY KEY, manifest TEXT)')
                connection.execute('INSERT INTO evlet_installed_apps VALUES (1,?)', (json.dumps(state),))
            connection.commit()
    return state


def numbers(main):
    result = []
    for path in writes.files(main):
        with closing(sqlite3.connect(path)) as connection:
            result.append(connection.execute('SELECT n FROM marker').fetchone()[0])
    return result


def instance(tmp_path):
    path = tmp_path / 'instances' / 'acme'
    main = path / 'data' / 'database.sqlite3'
    state = generation(main, 2)
    (path / 'config.py').touch()
    atomic_json(path / 'apps.lock', state)
    source = tmp_path / 'saved' / 'before.sqlite3'
    generation(source, 1)
    return path, main, source


def test_controls_database_backup_contains_and_restores_every_store(tmp_path):
    path, main, _ = instance(tmp_path)
    database = Database(main, Registry())
    saved = main.parent / 'backups' / database.backup()
    assert numbers(saved) == [2] * 4
    assert writes.manifest_path(saved).exists()
    for member in writes.files(main):
        with closing(sqlite3.connect(member)) as connection:
            connection.execute('UPDATE marker SET n=3')
            connection.commit()
    restore(tmp_path, 'acme', saved)
    assert numbers(main) == [2] * 4
    assert not (path / '.install-journal.json').exists()


@pytest.mark.parametrize('bad', ['missing', 'corrupt', 'mixed'])
def test_invalid_modern_set_is_rejected_without_touching_live_data(tmp_path, bad):
    path, main, source = instance(tmp_path)
    saved = source.with_name('checked.sqlite3')
    writes.copy_set(source, saved)
    side = writes.files(saved)[1]
    if bad == 'missing':
        side.unlink()
    elif bad == 'corrupt':
        side.write_bytes(b'not sqlite')
    else:
        with closing(sqlite3.connect(side)) as connection:
            connection.execute('UPDATE marker SET n=9')
            connection.commit()
    with pytest.raises(ValueError, match='backup|Backup'):
        restore(tmp_path, 'acme', saved)
    assert numbers(main) == [2] * 4
    assert not (path / '.install-journal.json').exists()
    assert not (main.parent / 'backups').exists()


@pytest.mark.parametrize('position', range(4))
@pytest.mark.parametrize('after', [False, True])
def test_restore_recovers_every_interrupted_live_file_replace(tmp_path, monkeypatch, position, after):
    path, main, source = instance(tmp_path)
    original = installer.os.replace
    targets = (*writes.files(main)[1:], main)
    hit = False

    def fail(old, new):
        nonlocal hit
        if Path(new) == targets[position] and not hit:
            hit = True
            if after:
                original(old, new)
            raise OSError('interrupted publication')
        return original(old, new)

    monkeypatch.setattr(installer.os, 'replace', fail)
    with pytest.raises(OSError, match='interrupted'):
        restore(tmp_path, 'acme', source)
    assert hit
    with pytest.raises(ValueError, match='Interrupted'):
        require_consistent(path)
    monkeypatch.setattr(installer.os, 'replace', original)
    with instance_guard(path):
        installer.recover(path)
    assert numbers(main) == [1] * 4
    assert require_consistent(path)['evlet']['version'] == '1'
    assert not list(main.parent.glob('.publish-*'))


def test_recovery_can_itself_be_interrupted_and_replayed(tmp_path, monkeypatch):
    path, main, source = instance(tmp_path)
    original = installer.atomic_json

    def fail_lock(target, value):
        if target.name == 'apps.lock':
            raise OSError('lock interrupted')
        original(target, value)

    monkeypatch.setattr(installer, 'atomic_json', fail_lock)
    with pytest.raises(OSError, match='lock interrupted'):
        restore(tmp_path, 'acme', source)
    with pytest.raises(OSError, match='lock interrupted'), instance_guard(path):
        installer.recover(path)
    monkeypatch.setattr(installer, 'atomic_json', original)
    with instance_guard(path):
        installer.recover(path)
    assert numbers(main) == [1] * 4
    assert require_consistent(path)['evlet']['version'] == '1'


@pytest.mark.parametrize('committed', [False, True])
def test_legacy_journal_cannot_accept_mixed_files_but_explicit_restore_recovers(tmp_path, committed):
    path, main, source = instance(tmp_path)
    before = json.loads((path / 'apps.lock').read_text())
    other = {'apps': {}, 'evlet': {'version': '3'}}
    atomic_json(path / '.install-journal.json', {'before': other if committed else before, 'after': before if committed else other})
    with pytest.raises(ValueError, match='legacy installation'):
        installer.recover(path)
    restore(tmp_path, 'acme', source)
    assert numbers(main) == [1] * 4
    assert require_consistent(path)['evlet']['version'] == '1'


def test_partially_split_database_backs_up_moved_and_unmoved_tables(tmp_path):
    path, main, source = instance(tmp_path)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute('CREATE TABLE evlet_tool_calls(note TEXT)')
        connection.execute("INSERT INTO evlet_tool_calls VALUES ('not moved yet')")
        connection.commit()
    with closing(sqlite3.connect(writes.side_path(source, 'sessions'))) as connection:
        connection.execute('CREATE TABLE evlet_sessions(token TEXT)')
        connection.execute("INSERT INTO evlet_sessions VALUES ('already moved')")
        connection.commit()
    target = source.with_name('partial.sqlite3')
    assert len(writes.copy_set(source, target)) == 4
    assert not writes.backup_members(target)[1]
    _, pending = restore(tmp_path, 'acme', target)
    assert pending and numbers(main) == [1] * 4
    with closing(writes.connect(main)) as connection:
        assert connection.execute('SELECT note FROM evlet_tool_calls').fetchone()[0] == 'not moved yet'
        assert connection.execute('SELECT token FROM evlet_sessions').fetchone()[0] == 'already moved'


def test_failed_journal_unlink_fsync_retains_generation_for_power_loss_replay(tmp_path, monkeypatch):
    path, main, source = instance(tmp_path)
    original = installer.sync_directory
    journal = path / '.install-journal.json'
    recorded = None

    def fail(directory):
        nonlocal recorded
        if journal.exists():
            recorded = json.loads(journal.read_text())
        if directory == path and not journal.exists():
            raise OSError('journal unlink not durable')
        original(directory)

    monkeypatch.setattr(installer, 'sync_directory', fail)
    with pytest.raises(OSError, match='not durable'):
        restore(tmp_path, 'acme', source)
    assert recorded is not None
    assert (main.parent / recorded['directory']).is_dir()
    # A power failure can make the not-yet-durable journal unlink disappear.
    atomic_json(journal, recorded)
    monkeypatch.setattr(installer, 'sync_directory', original)
    with instance_guard(path):
        installer.recover(path)
    assert numbers(main) == [1] * 4
    assert require_consistent(path)['evlet']['version'] == '1'


def test_online_snapshot_excludes_another_process_until_all_snapshots_are_pinned(tmp_path, monkeypatch):
    main, target = tmp_path / 'database.sqlite3', tmp_path / 'saved.sqlite3'
    generation(main, 0)
    # The child waits for the first read snapshot, then attempts one transaction changing all four files.
    child = subprocess.Popen([sys.executable, '-c', '''
import sqlite3, sys
from pathlib import Path
main = Path(sys.argv[1])
c = sqlite3.connect(main, isolation_level=None)
for store in ('sessions', 'logs', 'eva'):
    c.execute('ATTACH DATABASE ? AS ' + store, (str(main.with_name(store + '.sqlite3')),))
print('ready', flush=True)
sys.stdin.readline()
print('attempting', flush=True)
c.execute('BEGIN IMMEDIATE')
for store in ('main', 'sessions', 'logs', 'eva'):
    c.execute('UPDATE ' + store + '.marker SET n=1')
c.commit()
print('committed', flush=True)
''', str(main)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        original = writes._set_connection
        calls = 0

        def traced(members):
            nonlocal calls
            connection = original(members)
            calls += 1
            if calls == 2:
                def on_read(sql):
                    if sql == 'SELECT count(*) FROM main.sqlite_master':
                        child.stdin.write('go\n')
                        child.stdin.flush()
                        assert child.stdout.readline().strip() == 'attempting'
                connection.set_trace_callback(on_read)
            return connection

        monkeypatch.setattr(writes, '_set_connection', traced)
        writes.copy_set(main, target)
        assert child.wait(timeout=10) == 0
        assert numbers(target) == [0] * 4
        assert numbers(main) == [1] * 4
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@pytest.mark.parametrize('store', ['sessions', 'logs', 'eva'])
def test_snapshot_barrier_fences_a_writer_attached_only_to_each_side(tmp_path, monkeypatch, store):
    main, target = tmp_path / 'database.sqlite3', tmp_path / 'saved.sqlite3'
    generation(main, 0)
    child = subprocess.Popen([sys.executable, '-c', '''
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None, timeout=0.05)
print('ready', flush=True)
sys.stdin.readline()
try:
    c.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError:
    print('blocked', flush=True)
    sys.stdin.readline()
    c.execute('BEGIN IMMEDIATE')
else:
    c.execute('UPDATE marker SET n=1')
    c.commit()
    print('acquired', flush=True)
    sys.exit()
c.execute('UPDATE marker SET n=1')
c.commit()
''', str(writes.side_path(main, store))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        original, calls, observations = writes._set_connection, 0, []
        def traced(members):
            nonlocal calls
            connection = original(members)
            calls += 1
            if calls == 2:
                def read(sql):
                    if sql == 'SELECT count(*) FROM main.sqlite_master':
                        child.stdin.write('attempt\n')
                        child.stdin.flush()
                        observations.append(child.stdout.readline().strip())
                connection.set_trace_callback(read)
            return connection
        monkeypatch.setattr(writes, '_set_connection', traced)
        writes.copy_set(main, target)
        if observations == ['blocked']:
            child.stdin.write('continue\n')
            child.stdin.flush()
        assert child.wait(timeout=5) == 0
        assert observations == ['blocked']
        assert numbers(target) == [0] * 4
        assert sum(numbers(main)) == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
