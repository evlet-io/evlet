"""Bounded idle resources, drain semantics and serialized secret rotation against real SQLite."""

from contextlib import closing, contextmanager
import os
import gc
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import weakref

import pytest

from evlet import Database, Registry, qb, writes
from evlet.secrets import SecretKey, rotate


def database(tmp_path):
    result = Database(tmp_path / 'database.sqlite3', Registry())
    with result.schema_transaction() as connection:
        connection.execute('CREATE TABLE marker(n)')
        connection.execute('CREATE TABLE logs.evlet_test_log(n)')
    return result


def test_idle_pool_bounds_all_instances_and_reuses_across_threads(tmp_path, monkeypatch):
    writes.release_idle()
    monkeypatch.setattr(writes, 'IDLE_LIMIT', 3)
    databases = [database(tmp_path / str(index)) for index in range(12)]
    try:
        assert len(writes.IDLE) <= 3
        assert all(len(item._idle) <= item._IDLE for item in databases)
        last = databases[-1]
        with last._connection() as initial:
            pass
        reused = []
        def use():
            with last._connection() as connection:
                reused.append(connection is initial)
        worker = threading.Thread(target=use)
        worker.start()
        worker.join(timeout=5)
        assert reused == [True]
        monkeypatch.setattr(writes, 'IDLE_SECONDS', -1)
        with last._connection() as connection:
            assert connection is not initial
        assert not writes.IDLE
    finally:
        for item in databases:
            item.close()


def test_returning_connection_cannot_repopulate_cache_after_close(tmp_path):
    db = database(tmp_path)
    with db._connection() as connection:
        db.close()
        assert connection.execute('SELECT 1').fetchone()[0] == 1
    assert not db._idle
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute('SELECT 1')
    db.check()  # close remains reusable, as the existing adapter contract allows.
    db.close()


def test_release_idle_also_closes_connections_whose_owner_was_collected(tmp_path):
    db = database(tmp_path)
    with db._connection() as connection:
        pass
    owner = weakref.ref(db)
    del db
    gc.collect()
    assert owner() is None
    writes.release_idle()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute('SELECT 1')


def test_close_waits_for_an_inflight_log_batch_and_drains_later_additions(tmp_path, monkeypatch):
    db = database(tmp_path)
    entered, closed = threading.Event(), threading.Event()
    original = db.transaction

    @contextmanager
    def observed(**values):
        entered.set()
        with original(**values) as tx:
            yield tx

    monkeypatch.setattr(db, 'transaction', observed)
    with closing(sqlite3.connect(writes.side_path(db.path, 'logs'), isolation_level=None)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        db.logs.add('logs', qb.insert('evlet_test_log', n=1))
        assert entered.wait(timeout=5)
        closer = threading.Thread(target=lambda: (db.close(), closed.set()))
        closer.start()
        assert not closed.wait(timeout=0.1)
        db.logs.add('logs', qb.insert('evlet_test_log', n=2))
        blocker.rollback()
        closer.join(timeout=5)
    assert closed.is_set() and not db._idle
    assert db.logs._thread is None and not db.logs._pending
    with closing(sqlite3.connect(writes.side_path(db.path, 'logs'))) as reader:
        assert reader.execute('SELECT n FROM evlet_test_log ORDER BY n').fetchall() == [(1,), (2,)]


def test_failed_close_reports_and_retains_unsaved_log_writes(tmp_path, monkeypatch):
    db = database(tmp_path)
    monkeypatch.setattr(writes, 'FLUSH_EVERY', 60)
    db.write_wait = 0.05
    db.close()  # connection timeouts are set when opened.
    with closing(sqlite3.connect(writes.side_path(db.path, 'logs'), isolation_level=None)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        db.logs.add('logs', qb.insert('evlet_test_log', n=3))
        with pytest.raises(RuntimeError, match='could not be drained'):
            db.close()
        assert len(db.logs._pending) == 1
        blocker.rollback()
    db.close()
    with closing(sqlite3.connect(writes.side_path(db.path, 'logs'))) as reader:
        assert reader.execute('SELECT n FROM evlet_test_log').fetchall() == [(3,)]


def test_log_backlog_stays_bounded_and_reports_drops_during_storage_failure(tmp_path, monkeypatch, caplog):
    db = database(tmp_path)
    monkeypatch.setattr(writes, 'FLUSH_EVERY', 60)
    monkeypatch.setattr(writes, 'LOG_LIMIT', 3)
    db.write_wait = 0.02
    db.close()
    with closing(sqlite3.connect(writes.side_path(db.path, 'logs'), isolation_level=None)) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        for number in range(10):
            db.logs.add('logs', qb.insert('evlet_test_log', n=number))
            if number % 2:
                db.logs.flush()
        assert len(db.logs._pending) == 3
        assert db.logs.dropped == 7
        assert 'capacity reached' in caplog.text
        blocker.rollback()
    db.close()
    with closing(sqlite3.connect(writes.side_path(db.path, 'logs'))) as reader:
        assert reader.execute('SELECT n FROM evlet_test_log ORDER BY n').fetchall() == [(7,), (8,), (9,)]


@pytest.mark.skipif(os.name == 'nt', reason='RLIMIT_NOFILE is POSIX')
def test_many_idle_tenants_fit_under_256_file_descriptors(tmp_path):
    probe = subprocess.run([sys.executable, '-c', '''
import resource, sys
from pathlib import Path
from evlet import Database, Registry
from evlet import writes
resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
databases=[]
for i in range(40):
    db=Database(Path(sys.argv[1])/str(i)/'database.sqlite3', Registry())
    with db.schema_transaction() as c:
        c.execute('CREATE TABLE marker(n)')
    databases.append(db)
assert len(writes.IDLE) <= writes.IDLE_LIMIT
for db in databases:
    db.check()
    db.close()
assert not writes.IDLE
''', str(tmp_path)], text=True, capture_output=True, timeout=30)
    assert probe.returncode == 0, probe.stderr


def sealed_database(tmp_path):
    db = database(tmp_path)
    key = SecretKey(tmp_path / 'secret.key')
    with db.schema_transaction() as connection:
        connection.execute('CREATE TABLE evlet_connectors(id TEXT PRIMARY KEY, secrets BLOB)')
        connection.execute('CREATE TABLE sessions.evlet_two_step_tickets(token_hash TEXT PRIMARY KEY, user_id INTEGER, secret BLOB)')
        connection.run(qb.insert('evlet_connectors', id='one', secrets=key.encrypt(b'connector', b'evlet-connector:one')))
        connection.run(qb.insert('evlet_two_step_tickets', token_hash='ticket', user_id=1,
                                 secret=key.encrypt(b'ticket', b'two_step_ticket:1')))
    db.close()
    return db, key


def test_rotation_refuses_another_process_before_changing_keyring(tmp_path):
    db, key = sealed_database(tmp_path)
    before = key.path.read_bytes()
    lock = key.path.with_name('.' + key.path.name + '.rotate.lock')
    child = subprocess.Popen([sys.executable, '-c', '''
import sys
from evlet.writes import file_lock
with file_lock(sys.argv[1]):
    print('locked', flush=True)
    sys.stdin.readline()
''', str(lock)], text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(ValueError, match='in use'):
            rotate(db.path, SecretKey(key.path))
        assert key.path.read_bytes() == before
    finally:
        child.stdin.write('done\n')
        child.stdin.flush()
        assert child.wait(timeout=5) == 0
    assert rotate(db.path, SecretKey(key.path)) == (2, [])
    assert len(key.keys()) == 1
    with db._connection() as connection:
        assert key.decrypt(connection.run(qb.select('evlet_connectors', 'secrets')).value(), b'evlet-connector:one') == b'connector'
        assert key.decrypt(connection.run(qb.select('evlet_two_step_tickets', 'secret')).value(), b'two_step_ticket:1') == b'ticket'
    db.close()


def test_rotation_failure_after_publication_keeps_old_data_readable(tmp_path, monkeypatch):
    db, key = sealed_database(tmp_path)
    original = SecretKey.encrypt
    def fail(*args, **values):
        raise RuntimeError('interrupted rotation')
    monkeypatch.setattr(SecretKey, 'encrypt', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        rotate(db.path, SecretKey(key.path))
    assert len(key.keys()) == 2
    with db._connection() as connection:
        assert key.decrypt(connection.run(qb.select('evlet_connectors', 'secrets')).value(), b'evlet-connector:one') == b'connector'
    monkeypatch.setattr(SecretKey, 'encrypt', original)
    assert rotate(db.path, SecretKey(key.path)) == (2, [])
    assert len(key.keys()) == 1
    db.close()
