"""SECH-14: the Controls audit log is hash chained, and a changed or removed row is reported."""

import sqlite3

from evlet import Database
from evlet.audit import LOG_HASH, add_log_hash, log_hash, log_head, verify_log
from evlet.controls import audit, ensure_schema
from evlet.framework import STEPS, upgrade
from examples.playground.models import registry
from support import client


def save_setting(admin, value):
    payload = {'changes': [{'kind': 'setting', 'key': 'security.lockout', 'value': value}]}
    assert admin('POST', '/api/_controls/overrides', payload)[0] == 200


def tamper(app, sql, *values):
    """What a root user can do: drop the append-only trigger, edit, and put the trigger back."""
    with app.database.transaction() as tx:
        connection = tx.connection
        connection.drop_append_only('evlet_audit')
        connection.execute(sql, values)
        connection.make_append_only('evlet_audit', 'The audit log is append-only')


def chain(app):
    with app.database._connection() as connection:
        return verify_log(connection)


def test_each_audit_row_is_chained_to_the_row_before_it(secured):
    admin = client(secured)
    for value in (5, 4, 3):
        save_setting(admin, value)
    with secured.database._connection() as connection:
        rows = connection.execute('SELECT id, at, actor, area, target, detail, before, after, hash FROM evlet_audit ORDER BY id').fetchall()
        head = log_head(connection)
    assert len(rows) >= 2 and all(row['hash'] for row in rows)
    previous = None
    for row in rows:
        assert row['hash'] == log_hash(row, previous)
        previous = row['hash']
    assert head == {'id': rows[-1]['id'], 'hash': rows[-1]['hash']}
    assert chain(secured) == (len(rows), None)
    state = admin('GET', '/api/_controls/state')[1]
    assert not [item for item in state['attention'] if item['page'] == 'audit']
    assert admin('GET', '/api/_controls/audit')[1]['head'] == head


def test_an_edited_row_is_the_first_break_and_shows_under_needs_attention(secured):
    admin = client(secured)
    for value in (5, 4, 3):
        save_setting(admin, value)
    with secured.database._connection() as connection:
        ids = [row[0] for row in connection.execute('SELECT id FROM evlet_audit ORDER BY id')]
    tamper(secured, 'UPDATE evlet_audit SET actor = ? WHERE id = ?', 'someone-else', ids[1])
    checked, broken = chain(secured)
    assert checked == 1 and broken['id'] == ids[1] and 'does not match' in broken['message']
    found = [item for item in admin('GET', '/api/_controls/state')[1]['attention'] if item['page'] == 'audit']
    assert len(found) == 1 and found[0]['tone'] == 'red' and str(ids[1]) in found[0]['title']


def test_a_removed_row_breaks_the_link_of_the_row_after_it(secured):
    admin = client(secured)
    for value in (5, 4, 3):
        save_setting(admin, value)
    with secured.database._connection() as connection:
        ids = [row[0] for row in connection.execute('SELECT id FROM evlet_audit ORDER BY id')]
    tamper(secured, 'DELETE FROM evlet_audit WHERE id = ?', ids[0])
    assert chain(secured)[1]['id'] == ids[1]


def test_an_unhashed_row_after_the_chain_started_is_a_break(secured):
    save_setting(client(secured), 4)
    with secured.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_audit(at, actor, area, target, detail) VALUES (1, 'x', 'Security', 't', 'sneaked in')")
        sneaked = tx.connection.execute('SELECT max(id) FROM evlet_audit').fetchone()[0]
    assert chain(secured)[1] == {'id': sneaked, 'message': 'this row has no hash, but earlier rows do'}


def test_rewriting_the_whole_chain_is_not_detected_on_the_host(secured):
    """The documented limit: whoever can write the file can recompute every hash; only an off-host head catches it."""
    admin = client(secured)
    save_setting(admin, 4)
    save_setting(admin, 3)
    with secured.database._connection() as connection:
        before = log_head(connection)
    with secured.database.transaction() as tx:
        connection = tx.connection
        connection.drop_append_only('evlet_audit')
        previous = None
        for row in connection.execute('SELECT * FROM evlet_audit ORDER BY id').fetchall():
            values = dict(row) | {'actor': 'rewritten'}
            previous = log_hash(values, previous)
            connection.execute('UPDATE evlet_audit SET actor = ?, hash = ? WHERE id = ?', ('rewritten', previous, row['id']))
        connection.make_append_only('evlet_audit', 'The audit log is append-only')
    assert chain(secured)[1] is None
    with secured.database._connection() as connection:
        after = log_head(connection)
    assert after['id'] == before['id'] and after['hash'] != before['hash']


def test_the_framework_step_adds_the_column_and_the_chain_starts_after_old_rows(tmp_path):
    path = tmp_path / 'old.sqlite3'
    database = Database(path, registry)
    database.initialize()
    upgrade(database)
    with database.transaction() as tx:
        connection = tx.connection
        ensure_schema(connection)
        # An audit log from before the chain: no hash column, two rows.
        connection.drop_append_only('evlet_audit')
        connection.execute('ALTER TABLE evlet_audit DROP COLUMN hash')
        connection.execute("INSERT INTO evlet_audit(at, actor, area, target, detail) VALUES (1, 'a', 'Users', 'u', 'old'), (2, 'a', 'Users', 'u', 'old')")
        connection.make_append_only('evlet_audit', 'The audit log is append-only')
        connection.execute("DELETE FROM evlet_framework_migrations WHERE name = '0018_audit_chain'")
    assert STEPS[-1].name == '0018_audit_chain' and upgrade(database) == ['0018_audit_chain']
    with database.transaction() as tx:
        connection = tx.connection
        assert LOG_HASH.name in connection.columns('evlet_audit')
        add_log_hash(connection)  # idempotent
        audit(connection, 'cli', 'Users', 'u', 'new')
        audit(connection, 'cli', 'Users', 'u', 'newer')
        rows = connection.execute('SELECT hash FROM evlet_audit ORDER BY id').fetchall()
        assert [row[0] is None for row in rows] == [True, True, False, False]
        assert verify_log(connection) == (2, None)
        try:
            connection.execute("UPDATE evlet_audit SET detail = 'x'")
            raise AssertionError('the audit log took an update')
        except sqlite3.DatabaseError as error:
            assert 'append-only' in str(error)
    database.close()
