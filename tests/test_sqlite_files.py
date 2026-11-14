"""SQLite storage files and write turns (evlet/writes.py), all against real SQLite files and threads."""

from contextlib import closing
import json
import logging
import shutil
import sqlite3
import threading
import time

import pytest

from evlet import qb

from evlet import Database, JobQueue, Task, User
from evlet import framework, writes
from evlet.writes import STORES, Busy, SplitError, WriteLine, split_files
from examples.playground.models import Customer, registry
from test_business import secured, hashed, client  # noqa: F401
from test_eva import make


@pytest.fixture
def full(tmp_path, monkeypatch, hashed):
    """The Playground with Eva, so every side table exists."""
    return make(tmp_path, monkeypatch, hashed)


def schema(path):
    with closing(sqlite3.connect(path)) as connection:
        return {row[0]: row[1] for row in connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}


def test_side_tables_live_in_their_own_files_in_wal_mode(full):
    main, sessions, logs, eva = full.database.files()
    assert sessions.name == main.stem + '-sessions.sqlite3' and writes.data_files('x')[1].name == 'sessions.sqlite3'
    assert {'evlet_sessions', 'evlet_two_step_tickets', 'evlet_login_limits'} <= set(schema(sessions))
    assert {'evlet_tool_calls', 'evlet_forwarded_peers'} <= set(schema(logs))
    assert {name for name in STORES['eva']} <= set(schema(eva))
    kept = schema(main)
    assert not {table for tables in STORES.values() for table in tables} & set(kept)
    assert {'evlet_users', 'evlet_jobs', 'evlet_job_progress', 'evlet_events', 'evlet_audit'} <= set(kept)
    # References across files are dropped (Evlet checks them); references inside one file stay.
    assert 'REFERENCES' not in schema(sessions)['evlet_sessions']
    assert 'REFERENCES "evlet_eva_conversations"' in schema(eva)['evlet_eva_runs']
    for path in (main, sessions, logs, eva):
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    # Sign-in and the session lookup join across files as before.
    status, _, _ = client(full)('GET', '/api/customer')
    assert status == 200


def hold(database, store, seconds, started):
    with database.transaction(store=store) as tx:
        if store is None:
            tx.connection.execute('UPDATE evlet_users SET active=active WHERE id=1')
        started.set()
        time.sleep(seconds)


@pytest.mark.parametrize('held, other', [(None, 'sessions'), (None, 'logs'), (None, 'eva'), ('sessions', None), ('eva', None)])
def test_a_held_write_on_one_file_never_delays_another(full, held, other):
    database, started = full.database, threading.Event()
    holder = threading.Thread(target=hold, args=(database, held, 1.0, started))
    holder.start()
    started.wait()
    began = time.monotonic()
    table = {None: 'evlet_audit', 'sessions': 'evlet_login_limits', 'logs': 'evlet_tool_calls', 'eva': 'evlet_eva_lock'}[other]
    with database.transaction(store=other) as tx:
        if other is None:
            tx.connection.execute("INSERT INTO evlet_audit(at,actor,area,target,detail) VALUES (0,'test','Test','x','y')")
        elif other == 'sessions':
            tx.connection.execute("INSERT INTO evlet_login_limits VALUES ('x',1,0)")
        else:
            tx.connection.execute(f'DELETE FROM {table} WHERE 0')
    waited = time.monotonic() - began
    holder.join()
    assert waited < 0.2


def test_a_store_transaction_changes_only_its_own_file(full):
    with pytest.raises(sqlite3.OperationalError, match='A sessions transaction cannot change evlet_users'):
        with full.database.transaction(store='sessions') as tx:
            tx.connection.execute('SELECT count(*) FROM evlet_users').fetchone()  # reading any file is fine
            tx.connection.execute('UPDATE evlet_users SET active=1')
    with pytest.raises(sqlite3.OperationalError, match='cannot change evlet_eva_lock'):
        with full.database.transaction(store='logs') as tx:
            tx.connection.execute('UPDATE evlet_eva_lock SET n=n+1')
    with pytest.raises(ValueError):
        with full.database.transaction(store='records'):
            pass


def test_a_main_transaction_writes_side_tables_too(secured):
    # end_sessions during an account change: one transaction, the sessions file's turn taken on the way.
    with secured.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_sessions(token,user_id,csrf,expires) VALUES ('t',1,'c',?)", (int(time.time()) + 60,))
        tx.connection.execute('UPDATE evlet_users SET active=active WHERE id=1')
    with secured.database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM evlet_sessions WHERE token='t'").fetchone()[0] == 1


# ---------- The line-up ----------

def test_requests_go_first_and_a_waiting_job_gets_its_turn_within_the_limit():
    line, order = WriteLine(job_wait=0.2), []
    line.acquire()

    def wait(kind, name):
        line.acquire(kind)
        order.append(name)
        time.sleep(0.01)
        line.release()

    threads = [threading.Thread(target=wait, args=('job', 'job'))]
    threads[0].start()
    time.sleep(0.02)
    for index in range(3):
        threads.append(threading.Thread(target=wait, args=('interactive', f'request {index}')))
        threads[-1].start()
        time.sleep(0.02)
    line.release()
    for thread in threads:
        thread.join()
    # The job had waited less than job_wait when the turn came free: the requests went first, in order.
    assert order == ['request 0', 'request 1', 'request 2', 'job']


def test_a_job_is_never_starved_by_a_stream_of_requests():
    line, stop, job_waits = WriteLine(job_wait=0.1), threading.Event(), []

    def requests():
        while not stop.is_set():
            line.acquire()
            time.sleep(0.005)
            line.release()

    workers = [threading.Thread(target=requests) for _ in range(4)]
    for worker in workers:
        worker.start()
    time.sleep(0.05)
    for _ in range(10):
        job_waits.append(line.acquire('job'))
        line.release()
    stop.set()
    for worker in workers:
        worker.join()
    # Each job turn came within job_wait plus about one request.
    assert max(job_waits) < 0.1 + 0.08


def test_after_a_step_that_made_requests_wait_a_job_rests_as_long_as_the_step():
    line = WriteLine(job_wait=1.0, job_share=1.0)
    line.acquire('job')
    request = threading.Thread(target=lambda: (line.acquire(), line.release()))
    request.start()
    time.sleep(0.15)  # the request waits on the job's step
    line.release()
    request.join()
    began = time.monotonic()
    line.acquire('job')  # nobody waiting, but the job rests about as long as its step held the file
    assert 0.12 <= time.monotonic() - began < 0.3
    line.release()
    # Nobody waited on that step: no rest.
    began = time.monotonic()
    line.acquire('job')
    assert time.monotonic() - began < 0.05
    line.release()


def test_the_wait_limit_gives_the_usual_error():
    line = WriteLine()
    taken = threading.Event()
    release = threading.Event()

    def holder():
        line.acquire()
        taken.set()
        release.wait()
        line.release()

    thread = threading.Thread(target=holder)
    thread.start()
    taken.wait()
    began = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match='database is locked'):
        line.acquire(timeout=0.2)
    assert 0.2 <= time.monotonic() - began < 0.5
    release.set()
    thread.join()
    assert line.acquire(timeout=0.2) == pytest.approx(0, abs=0.05)
    line.release()


STEP = 0.1


def stepper(steps):
    def handler(tx, context, payload):
        done = context.checkpoint or 0
        if done == steps:
            return None
        tx.create(Customer, customer_name=f'Imported {done}')
        time.sleep(STEP)  # a write-heavy step: it holds the main file this long
        return done + 1
    return handler


def test_requests_wait_about_one_step_behind_a_resumable_job_and_none_fails(secured):
    """The measured case in the PR: a resumable import holds the main file STEP seconds per step while
    four people save records. Before evlet/writes.py the waiters retried at random inside SQLite."""
    jobs = JobQueue(secured.database, [Task('import', stepper(12), resumable=True)], events=secured.business.events)
    jobs.initialize()
    with secured.database.transaction() as tx:
        jobs.enqueue(tx, 'import', {}, system_role='editor')
    errors, waits, stop = [], [], threading.Event()

    def person(index):
        count = 0
        while not stop.is_set():
            began = time.monotonic()
            try:
                secured.database.create(Customer, customer_name=f'Person {index} {count}')
            except Exception as exc:  # any error fails the test below
                errors.append(exc)
            waits.append(time.monotonic() - began)
            count += 1
            time.sleep(0.02)

    people = [threading.Thread(target=person, args=(index,)) for index in range(4)]
    for thread in people:
        thread.start()
    began = time.monotonic()
    assert jobs.run_one()
    took = time.monotonic() - began
    stop.set()
    for thread in people:
        thread.join()
    assert errors == []
    assert max(waits) < STEP + 0.15, max(waits)
    # The job was not starved either: with people waiting it rests as long as each step, so about twice its own time.
    assert took < 2 * 13 * STEP + 1.0


def test_a_slow_write_logs_a_warning_naming_the_job(secured, caplog):
    jobs = JobQueue(secured.database, [Task('slow', lambda tx, context, payload: time.sleep(0.15))])
    jobs.initialize()
    with secured.database.transaction() as tx:
        receipt = jobs.enqueue(tx, 'slow', {}, system_role='editor')
    secured.database.slow_write = 0.1
    try:
        with caplog.at_level(logging.WARNING, logger='evlet.writes'):
            assert jobs.run_one()
    finally:
        del secured.database.slow_write
    assert any(f'job #{receipt.id} slow' in record.getMessage() and 'database.sqlite3' in record.getMessage() for record in caplog.records)


# ---------- Batched log writes ----------

def test_batched_writes_save_within_a_moment_and_on_close(secured):
    database = secured.database
    database.logs.add('logs', qb.insert('evlet_forwarded_peers', peer='10.0.0.1', last_seen=1))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with database._connection() as connection:
            if connection.execute('SELECT count(*) FROM evlet_forwarded_peers').fetchone()[0]:
                break
        time.sleep(0.05)
    else:
        pytest.fail('The batch was not saved')
    database.logs.add(None, qb.update('evlet_users', active=qb.col('active')).where(id=1))
    database.logs.add('logs', qb.insert('evlet_forwarded_peers', peer='10.0.0.2', last_seen=1))
    database.close()
    with database._connection() as connection:
        assert connection.execute('SELECT count(*) FROM evlet_forwarded_peers').fetchone()[0] == 2


def test_adding_to_the_batch_never_waits_on_the_database(secured):
    started = threading.Event()
    holder = threading.Thread(target=hold, args=(secured.database, 'logs', 0.5, started))
    holder.start()
    started.wait()
    began = time.monotonic()
    for index in range(100):
        secured.database.logs.add('logs', qb.insert('evlet_forwarded_peers', peer=f'10.1.0.{index}', last_seen=1))
    assert time.monotonic() - began < 0.05
    holder.join()
    secured.database.close()


# ---------- Delayed follow-up jobs ----------

def test_a_job_queues_its_next_run_with_its_own_identity(secured):
    runs = []

    def handler(tx, context, payload):
        runs.append((payload['n'], context.user.role if context.user else None))
        if payload['n'] < 2:
            context.follow_up(tx, {'n': payload['n'] + 1}, run_at=int(time.time()) + 3600)

    jobs = JobQueue(secured.database, [Task('poll', handler)], events=secured.business.events)
    jobs.initialize()
    with secured.database.transaction() as tx:
        first = jobs.enqueue(tx, 'poll', {'n': 1}, system_role='viewer')
    assert jobs.run_one() and runs == [(1, 'viewer')]
    with secured.database._connection() as connection:
        follow = connection.execute('SELECT * FROM evlet_jobs WHERE id<>? ORDER BY id DESC', (first.id,)).fetchone()
        role = connection.execute('SELECT role FROM evlet_job_identities WHERE job_id=?', (follow['id'],)).fetchone()[0]
    assert follow['state'] == 'queued' and follow['run_at'] > time.time() + 3000 and follow['dedupe'].startswith(f'follow:{first.id}:')
    assert role == 'viewer' and follow['actor'] is None  # the same system role, never the Administrator
    assert not jobs.run_one()  # not due yet
    assert jobs.run_one(now=follow['run_at']) and runs[-1] == (2, 'viewer')


def test_a_follow_up_rolls_back_with_its_step_and_is_queued_once(secured):
    def handler(tx, context, payload):
        context.follow_up(tx, run_at=2_000_000_000)
        context.follow_up(tx, run_at=2_000_000_000)  # the same key: still one job
        if payload.get('fail'):
            raise ValueError('no')

    jobs = JobQueue(secured.database, [Task('poll', handler, max_attempts=1)], events=secured.business.events)
    jobs.initialize()
    with secured.database.transaction() as tx:
        jobs.enqueue(tx, 'poll', {'fail': True}, system_role='editor')
        jobs.enqueue(tx, 'poll', {}, system_role='editor')
    jobs.run_one()
    jobs.run_one()
    with secured.database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM evlet_jobs WHERE dedupe LIKE 'follow:%'").fetchone()[0] == 1


def test_context_enqueue_takes_a_run_time(secured):
    jobs = JobQueue(secured.database, [Task('later', lambda tx, context, payload: None)], events=secured.business.events)
    jobs.initialize()
    secured.business.jobs = jobs
    from evlet.documents import Context
    with secured.database.transaction() as tx:
        receipt = Context(secured.business, tx, User(0, 'system', 'editor', system='Test')).enqueue('later', {}, run_at=2_000_000_000)
    with secured.database._connection() as connection:
        assert connection.execute('SELECT run_at FROM evlet_jobs WHERE id=?', (receipt.id,)).fetchone()[0] == 2_000_000_000


# ---------- Migration of an instance from 0.4.2 ----------

def as_release_0_4_2(database):
    """Put every side table back into the main file, as Evlet 0.4.2 kept them, with the step unrecorded."""
    with closing(writes.connect(database.path, isolation_level=None)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        for store, tables in STORES.items():
            for table in tables:
                row = connection.execute(f"SELECT sql FROM {store}.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                if row is None:
                    continue
                connection.execute(row[0])  # unqualified: the main file
                connection.execute(f'INSERT INTO main.{table} SELECT * FROM {store}.{table}')
                counter = connection.execute(f'SELECT seq FROM {store}.sqlite_sequence WHERE name=?', (table,)).fetchone() \
                    if connection.execute(f"SELECT 1 FROM {store}.sqlite_master WHERE name='sqlite_sequence'").fetchone() else None
                if counter is not None:
                    connection.execute('UPDATE main.sqlite_sequence SET seq=? WHERE name=?', (counter[0], table))
                connection.execute(f'DROP TABLE {store}.{table}')
        connection.execute("DELETE FROM evlet_framework_migrations WHERE name IN ('0010_split_files', '0011_state_manager')")
        connection.execute('COMMIT')
    for side in database.files()[1:]:
        for path in (side, side.with_name(side.name + '-wal'), side.with_name(side.name + '-shm')):
            path.unlink(missing_ok=True)


def fill_every_moved_table(database):
    now = int(time.time())
    with database.transaction() as tx:
        c = tx.connection
        c.execute("INSERT INTO evlet_sessions(token,user_id,csrf,expires) VALUES ('s1',1,'c',?)", (now + 3600,))
        c.execute("INSERT INTO evlet_two_step_tickets(token_hash,user_id,realm,expires) VALUES ('h',1,'desk',?)", (now + 60,))
        c.execute("INSERT INTO evlet_login_limits VALUES ('client',2,?)", (now + 900,))
        c.execute('CREATE TABLE IF NOT EXISTS evlet_public_limits (client TEXT NOT NULL, period INTEGER NOT NULL, calls INTEGER NOT NULL, resets INTEGER NOT NULL, PRIMARY KEY (client, period))')
        c.execute("INSERT INTO evlet_public_limits VALUES ('p',60,1,?)", (now + 60,))
        c.execute("INSERT INTO evlet_forwarded_peers VALUES ('10.0.0.9',?)", (now,))
        for index in range(30):
            c.execute("INSERT INTO evlet_tool_calls(at,user_id,origin,tool,kind,outcome) VALUES (?,1,'api','list_records','read','ok')", (now,))
        c.execute("INSERT INTO evlet_eva_conversations(id,user_id,title,created_at,updated_at) VALUES (5,1,'Chat',0,0)")
        c.execute("INSERT INTO evlet_eva_runs(id,conversation_id,user_id,origin,model,status,started_at,updated_at) VALUES (8,5,1,'eva','m','completed',0,0)")
        c.execute("INSERT INTO evlet_eva_messages(conversation_id,run_id,role,content,created_at) VALUES (5,8,'user','Hi',0)")
        c.execute("INSERT INTO evlet_eva_events(run_id,at,type,data) VALUES (8,0,'run.completed','{}')")
        c.execute("INSERT INTO evlet_eva_files(user_id,conversation_id,name,kind,size,content,created_at) VALUES (1,5,'a.txt','upload',1,x'41',0)")
        c.execute("INSERT INTO evlet_eva_usage(at,user_id,origin,provider,model,input_tokens,cached_tokens,output_tokens,reasoning_tokens,total_tokens,requests,cost_micro) "
                  "VALUES (0,1,'eva','x','m',1,0,1,0,2,1,5)")
        c.execute("INSERT INTO evlet_eva_reservations(user_id,origin,cost_micro,tokens,requests,expires_at) VALUES (1,'eva',1,1,1,0)")
        c.execute("INSERT INTO evlet_eva_tasks(id,user_id,name,instructions,cron,timezone,created_at) VALUES (3,1,'Daily','Sum up','0 9 * * *','Asia/Dubai',0)")
        c.execute("INSERT INTO evlet_eva_task_runs(task_id,at,status) VALUES (3,0,'done')")
        # A gap in the ids: the AUTOINCREMENT counter must move with the table.
        c.execute('DELETE FROM evlet_tool_calls WHERE id=(SELECT max(id) FROM evlet_tool_calls)')


def counts(database):
    with database._connection() as connection:
        present = writes.tables(connection)
        return {table: connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in writes.STORE_OF if table in present}


def test_migrating_a_0_4_2_database_moves_every_table_and_survives_a_crash_halfway(full):
    database = full.database
    fill_every_moved_table(database)
    before = counts(database)
    # Optional integrations (for example OIDC) need not have created their registered side tables.
    moved_tables = [table for table in writes.STORE_OF if table in before]
    assert all(before[table] for table in ('evlet_sessions', 'evlet_tool_calls', 'evlet_eva_files', 'evlet_eva_task_runs'))
    as_release_0_4_2(database)
    assert set(moved_tables) <= set(schema(database.path))
    assert counts(database) == before
    # The app's SQL still finds every table in the old single file, before the move.
    with database._connection() as connection:
        assert connection.execute("SELECT csrf FROM evlet_sessions WHERE token='s1'").fetchone()[0] == 'c'

    # A crash after the fourth table's copy, before it leaves the main file.
    copied = []

    def crash(table):
        copied.append(table)
        if len(copied) == 4:
            raise RuntimeError('power cut')

    with pytest.raises(SplitError, match=f'Could not move {moved_tables[3]}'):
        split_files(database, after_copy=crash)
    halfway = schema(database.path)
    assert not set(copied[:3]) & set(halfway) and copied[3] in halfway  # three moved, the fourth still in the main file
    assert counts(database) == before

    # The re-run copies the fourth table again from the main file and finishes; the step is recorded.
    assert framework.upgrade(database) == ['0010_split_files', '0011_state_manager']
    assert not {table for tables in STORES.values() for table in tables} & set(schema(database.path))
    assert counts(database) == before
    with database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM eva.evlet_eva_runs WHERE conversation_id=5").fetchone()[0] == 1
        assert connection.execute("SELECT seq FROM logs.sqlite_sequence WHERE name='evlet_tool_calls'").fetchone()[0] == 30
    # Ids carry on after the old counter, and a second migrate changes nothing.
    with database.transaction(store='logs') as tx:
        assert tx.connection.execute("INSERT INTO evlet_tool_calls(at,user_id,origin,tool,kind,outcome) VALUES (0,1,'api','x','read','ok')").lastrowid == 31
    assert framework.upgrade(database) == [] and split_files(database) == []
    assert client(full)('GET', '/api/customer')[0] == 200


def test_a_short_disk_stops_the_move_before_anything_changes(full, monkeypatch):
    database = full.database
    fill_every_moved_table(database)
    as_release_0_4_2(database)
    before = schema(database.path)
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: shutil._ntuple_diskusage(10**9, 10**9 - 1024, 1024))
    with pytest.raises(SplitError, match='Not enough free disk space'):
        framework.upgrade(database)
    assert schema(database.path) == before


def test_split_is_a_no_op_on_a_new_database(tmp_path):
    database = Database(tmp_path / 'fresh.sqlite3', registry)
    database.initialize()
    assert split_files(database) == []


def test_a_kept_connection_is_used_again_only_for_the_same_files(tmp_path):
    database = Database(tmp_path / 'database.sqlite3', registry)
    database.initialize()
    with database._connection() as first:
        pass
    with database._connection() as again:
        assert again is first
        with database._connection() as nested:  # in use: a nested operation opens its own
            assert nested is not first
    # A file replaced on disk, as a restore does: the kept connection is not used again.
    sessions = database.files()[1]
    for path in (sessions, sessions.with_name(sessions.name + '-wal'), sessions.with_name(sessions.name + '-shm')):
        path.unlink(missing_ok=True)
    with database._connection() as fresh:
        assert fresh is not first
        assert fresh.execute("SELECT count(*) FROM sessions.sqlite_master WHERE name='evlet_turn'").fetchone()[0] == 1
    # A transaction left open by a read snapshot (Database.page) is rolled back before the connection is kept.
    with database._connection() as connection:
        connection.execute('BEGIN')
        connection.execute('SELECT 1').fetchone()
    with database._connection() as connection:
        assert not connection.in_transaction
    database.close()


def test_two_databases_in_one_folder_keep_their_own_side_files(tmp_path):
    first, second = Database(tmp_path / 'a.sqlite3', registry), Database(tmp_path / 'b.sqlite3', registry)
    assert set(first.files()).isdisjoint(second.files())
    with pytest.raises(Busy):
        raise Busy()


# ---------- evlet migrate, evlet backup and evlet restore on an instance ----------

from test_cli_lifecycle import environment_stub, project, runtime  # noqa: E402,F401


def instance_set(project):
    return writes.data_files(project / 'instances/acme')


def test_migrate_backs_up_and_splits_an_instance_then_backup_and_restore_move_the_set(project, capsys):
    from evlet.cli.install import install
    from evlet.cli.main import main
    from evlet.models import Registry
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    app.database.create(app.registry.models['notesitem'], title='Keep this note')
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_sessions(token,user_id,csrf,expires) VALUES ('kept',1,'c',?)", (int(time.time()) + 3600,))
    app.database.close()
    main_file = instance_set(project)[0]
    # The instance as 0.4.2 left it: one file, the step not recorded.
    as_release_0_4_2(Database(main_file, Registry()))
    lock_file = project / 'instances/acme/apps.lock'
    lock = json.loads(lock_file.read_text())
    lock['evlet'] = {'version': '0.4.2', 'schema': '0008_administrator'}
    lock_file.write_text(json.dumps(lock))
    with closing(sqlite3.connect(main_file)) as connection:
        connection.execute('UPDATE evlet_installed_apps SET manifest=? WHERE id=1', (json.dumps(lock),))
        connection.commit()
    with pytest.raises(ValueError, match='run evlet migrate acme'):
        runtime(project)
    main(['migrate', 'acme', '--project', str(project)])
    out = capsys.readouterr().out
    assert 'Framework migrations: 0010_split_files' in out
    backup = out.split('Backup: ')[1].split()[0]
    assert 'evlet_sessions' in schema(backup)  # the backup is the single file before the move
    assert all(path.exists() for path in instance_set(project))
    assert 'evlet_sessions' not in schema(main_file) and 'evlet_sessions' in schema(instance_set(project)[1])

    # A backup of the four files as one set, while nothing is running.
    main(['backup', '--instance', 'acme', '--project', str(project)])
    out = capsys.readouterr().out
    saved = out.split('Backup: ')[1].split()[0]
    assert all(path.exists() for path in writes.files(saved))
    app = runtime(project)
    model = app.registry.models['notesitem']
    app.database.create(model, title='Made after the backup')
    with app.database.transaction(store='sessions') as tx:
        tx.connection.execute("DELETE FROM evlet_sessions")
    app.database.close()

    # Restore asks for the name typed back; --yes must name the instance.
    with pytest.raises(SystemExit):
        main(['restore', saved, '--instance', 'acme', '--yes', 'other', '--project', str(project)])
    main(['restore', saved, '--instance', 'acme', '--yes', 'acme', '--project', str(project)])
    out = capsys.readouterr().out
    assert 'restored from' in out and 'before-restore' in out
    app = runtime(project)
    assert [record.title for record in app.database.list(model)] == ['Keep this note']
    with app.database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM evlet_sessions WHERE token='kept'").fetchone()[0] == 1
    app.database.close()

    # An old single-file backup restores too; the next migrate moves its tables out again.
    main(['restore', backup, '--instance', 'acme', '--yes', 'acme', '--project', str(project)])
    out = capsys.readouterr().out
    assert 'Run evlet migrate acme' in out
    assert not any(path.exists() for path in instance_set(project)[1:])
    main(['migrate', 'acme', '--project', str(project)])
    capsys.readouterr()
    app = runtime(project)
    assert [record.title for record in app.database.list(model)] == ['Keep this note']
    with app.database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM sessions.evlet_sessions WHERE token='kept'").fetchone()[0] == 1
    app.database.close()


def test_a_busy_job_does_not_affect_people_or_the_side_files(full):
    """The owner's acceptance check, shortened (scripts/busy_queue_benchmark.py is the full run): a job writes
    in back-to-back 0.1 s transactions while people read and save, and sign-in, the tool log and Eva write."""
    from evlet.writes import job_writes
    database, stop, results, rows = full.database, threading.Event(), [], []

    def timed(kind, action):
        began = time.monotonic()
        try:
            action()
            results.append((kind, time.monotonic() - began, None))
        except Exception as exc:  # every failure fails the test below
            results.append((kind, time.monotonic() - began, exc))

    def person(index):
        count = 0
        while not stop.is_set():
            timed('read', lambda: database.list(Customer, limit=50))
            timed('save', lambda: database.create(Customer, customer_name=f'Person {index}-{count}'))
            count += 1
            time.sleep(0.05)

    def side():
        def write(store, sql):
            with database.transaction(store=store) as tx:
                tx.connection.execute(sql)
        while not stop.is_set():
            timed('side', lambda: write('sessions', "INSERT INTO evlet_login_limits VALUES ('c',1,0) ON CONFLICT(client) DO UPDATE SET attempts=attempts+1"))
            timed('side', lambda: write('logs', "INSERT INTO evlet_tool_calls(at,user_id,origin,tool,kind,outcome) VALUES (0,1,'api','x','read','ok')"))
            timed('side', lambda: write('eva', 'UPDATE evlet_eva_lock SET n=n+1 WHERE id=1'))
            time.sleep(0.02)

    def job():
        written = 0
        with job_writes('job #1 generator'):
            while not stop.is_set():
                with database.transaction() as tx:
                    until = time.monotonic() + 0.1
                    while time.monotonic() < until:
                        tx.create(Customer, customer_name=f'Generated {written}')
                        written += 1
        rows.append(written)

    threads = [threading.Thread(target=person, args=(index,)) for index in range(4)] + [threading.Thread(target=side), threading.Thread(target=job)]
    for thread in threads:
        thread.start()
    time.sleep(3)
    stop.set()
    for thread in threads:
        thread.join()
    assert [item for item in results if item[2] is not None] == []

    def p95(kind):
        waits = sorted(item[1] for item in results if item[0] == kind)
        return waits[int(0.95 * len(waits))]

    assert p95('save') < 0.25 and p95('side') < 0.05, (p95('save'), p95('side'))
    assert rows[0] > 100  # the job kept going
