"""Record ids are made by the framework on every create path and never change.

A caller-supplied `id` is refused with one clear message on every create and update path (HTTP, Context,
transaction sessions, action handlers, moves), and the database refuses a raw id UPDATE. Ledger rows keep
database-assigned ids, with an explicit id only when this transaction reserved it. Real SQLite.
"""

import sqlite3

import pytest

from evlet import (Application, Child, ConflictError, Database, Integer, Link, Model, Registry, Rule, State, Table,
                   Text, ValidationError, action)
from evlet.auth import LocalAuth, User
from evlet.framework import STEPS, upgrade
from evlet.identity import RecordId, check_ids, has_id_guard
from evlet.models import ASSIGNED_ID
from test_business import PASSWORD, client, http

FULL = {'admin': Rule(('read', 'create', 'update', 'delete'))}
ADMIN = User(1, 'admin', 'admin')


class Venture(Model):
    class Step(Child):
        title = Text(required=True, max_length=40)

    permissions = FULL
    title = Text(required=True, max_length=40)
    stage = State(
        State.Step('draft'),
        State.Step('active'),
        rules=(
            State.Rule('draft', 'active', 'Start', key='active'),
        ), default='draft')
    steps = Table(Step)

    @action('Take id')
    def take_id(self):
        self.id = RecordId(42)

    @action('Rename')
    def rename(self):
        self.title = 'Renamed'


class Note(Model):
    permissions = FULL
    body = Text(max_length=40)
    venture = Link(Venture)


class Tally(Model):
    """A mutable ledger: rows may change, but never their id."""
    amount = Integer(required=True)

    class Meta:
        ledger = True


def build(database):
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app, client(app)


@pytest.fixture
def app(tmp_path):
    registry = Registry(Venture, Note, Tally)
    return build(Database(tmp_path / 'ids.sqlite3', registry))


def refused(error):
    return error.value.errors == {'id': ASSIGNED_ID}


def guarded(error):
    """The id guard's refusal, as a ConflictError."""
    return isinstance(error.value, ConflictError) and str(error.value).endswith(': record ids never change')


TEXT_ID, NUMBER_ID = '0000000000042', 42


# Create paths

def test_http_create_refuses_an_id_and_generates_one(app):
    app, admin = app
    for value in (TEXT_ID, NUMBER_ID, None):
        status, body, _ = admin('POST', '/api/note', {'body': 'x', 'id': value})
        assert status == 422 and body['fields'] == {'id': ASSIGNED_ID}, body
    status, body, _ = admin('POST', '/api/note', {'body': 'x'})
    assert status == 201 and len(body['id']) == 13 and RecordId(body['id']) > 2**41
    assert app.database.count(Note) == 1


def test_http_table_rows_take_no_id_on_create(app):
    app, admin = app
    status, body, _ = admin('POST', '/api/venture', {'title': 'A', 'children': {'steps': [{'id': TEXT_ID, 'title': 'one'}]}})
    assert status == 422 and 'steps' in body['fields'], body
    assert app.database.count(Venture) == 0
    status, body, _ = admin('POST', '/api/venture', {'title': 'A', 'children': {'steps': [{'title': 'one'}]}})
    assert status == 201, body
    row = body['children']['steps'][0]['id']
    assert len(row) == 13 and row != body['id']


def test_python_create_paths_refuse_an_id_and_generate_one(app):
    app, _ = app
    database = app.database
    with pytest.raises(ValidationError) as error:
        database.create(Note, body='x', id=NUMBER_ID)
    assert refused(error)
    for maintenance in (False, True):
        with pytest.raises(ValidationError) as error:
            with database.transaction() as tx:
                if maintenance:
                    with tx.maintenance():
                        tx.create(Note, body='x', id=NUMBER_ID)
                else:
                    tx.create(Note, body='x', id=NUMBER_ID)
        assert refused(error)
    with pytest.raises(ValidationError) as error:
        Note(body='x', id=NUMBER_ID)
    assert refused(error)
    with app.business.context(ADMIN) as context:
        with pytest.raises(ValidationError) as error:
            context.create(Venture, title='A', id=TEXT_ID)
        assert refused(error)
        with pytest.raises(ValidationError):
            context.create(Venture, title='A', children={'steps': [{'id': TEXT_ID, 'title': 'one'}]})
        made = context.create(Venture, title='A', children={'steps': [{'title': 'one'}]})
    assert RecordId(made['id']) > 2**41
    with database.transaction() as tx:
        assert tx.create(Note, body='y').id > 2**41
    assert database.count(Note) == 1 and database.count(Venture) == 1


# Update paths

def test_http_update_refuses_an_id_change(app):
    app, admin = app
    _, note, _ = admin('POST', '/api/note', {'body': 'x'})
    status, read, headers = admin('GET', f"/api/note/{note['id']}")
    for value in (TEXT_ID, note['id']):
        status, body, _ = admin('PATCH', f"/api/note/{note['id']}", {'id': value, 'body': 'y'},
                                headers={b'if-match': headers[b'etag']})
        assert status == 422 and body['fields'] == {'id': ASSIGNED_ID}, body
    assert app.database.get(Note, note['id']).body == 'x'


def test_python_update_paths_refuse_an_id_change(app):
    app, _ = app
    database = app.database
    note = database.create(Note, body='x')
    with pytest.raises(ValidationError) as error:
        database.update(Note, note.id, id=NUMBER_ID)
    assert refused(error)
    with pytest.raises(ValidationError) as error:
        with database.transaction() as tx, tx.maintenance():
            tx.update(Note, note.id, id=NUMBER_ID)
    assert refused(error)
    with pytest.raises(ValidationError):
        note.id = RecordId(NUMBER_ID)
    with app.business.context(ADMIN) as context:
        venture = context.create(Venture, title='A')['id']
        with pytest.raises(ValidationError) as error:
            context.update(Venture, venture, id=TEXT_ID)
        assert refused(error)
        with pytest.raises(ValidationError) as error:
            context.move(Venture, venture, 'active', id=TEXT_ID)
        assert refused(error)
        # An action handler cannot assign the record's id either.
        with pytest.raises(ValidationError) as error:
            context.action(Venture, venture, 'take_id')
        assert refused(error)
        assert context.action(Venture, venture, 'rename')
    assert database.get(Note, note.id).id == note.id
    assert database.get(Venture, venture).title == 'Renamed'


def test_the_database_refuses_a_raw_id_update(app):
    app, _ = app
    database = app.database
    note = database.create(Note, body='x')
    for table in ('note', 'venture', 'venture_steps', 'tally'):
        with database.transaction() as tx:
            assert has_id_guard(tx.connection, table)
    with pytest.raises(Exception) as error:
        with database.transaction() as tx:
            tx.connection.execute('UPDATE note SET id = ? WHERE id = ?', (NUMBER_ID, note.id))
    assert guarded(error)
    # Other columns, and an UPDATE that keeps the id, still work.
    with database.transaction() as tx:
        tx.connection.execute('UPDATE note SET body = ?, id = id WHERE id = ?', ('raw', note.id))
    assert database.get(Note, note.id).body == 'raw'


# Ledger models

def test_ledger_ids_come_from_the_database_or_this_transactions_reservation(app):
    app, _ = app
    database = app.database
    with database.transaction() as tx:
        first = tx.create(Tally, amount=1).id
        [reserved] = tx.reserve_ids(Tally, 1)
        assert tx.create(Tally, id=reserved, amount=2).id == reserved
        with pytest.raises(ValidationError, match='not reserved'):
            tx.create(Tally, id=reserved + 100, amount=3)
    with pytest.raises(ValidationError) as error:
        with database.transaction() as tx:
            tx.update(Tally, first, id=reserved + 50)
    assert refused(error)
    with pytest.raises(Exception) as error:
        with database.transaction() as tx:
            tx.connection.execute('UPDATE tally SET id = ? WHERE id = ?', (reserved + 50, first))
    assert guarded(error)
    with database.transaction() as tx:
        assert tx.update(Tally, first, amount=5).amount == 5


# Existing data

def test_check_ids_reports_impossible_ids_and_a_missing_guard(tmp_path):
    path = tmp_path / 'old.sqlite3'
    registry = Registry(Venture, Note, Tally)
    database = Database(path, registry)
    database.initialize()
    upgrade(database)
    with database.transaction() as tx:
        assert check_ids(tx.connection, registry) == []
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO note (id, body) VALUES (-3, ?)', ('raw',))
        connection.execute('DROP TRIGGER "evlet_venture_fixed_id"')
    with database.transaction() as tx:
        assert check_ids(tx.connection, registry) == [
            'note: 1 row(s) with an id that is empty, zero or negative',
            'venture: the database does not refuse id changes; run evlet migrate']


def test_the_framework_step_adds_the_guard_to_existing_tables(tmp_path):
    path = tmp_path / 'before.sqlite3'
    registry = Registry(Venture, Note, Tally)
    database = Database(path, registry)
    database.initialize()
    upgrade(database)
    # An instance from before 0006_fixed_ids: no guards, and the step not yet recorded.
    with sqlite3.connect(path) as connection:
        for table in registry.models:
            connection.execute(f'DROP TRIGGER "evlet_{table}_fixed_id"')
        connection.execute("DELETE FROM evlet_framework_migrations WHERE name IN ('0006_fixed_ids', '0007_money_scale', '0008_administrator', '0009_admin_username', '0010_split_files', '0011_state_manager')")
    assert STEPS[5].name == '0006_fixed_ids'
    assert upgrade(database) == ['0006_fixed_ids', '0007_money_scale', '0008_administrator', '0009_admin_username', '0010_split_files', '0011_state_manager']
    with database.transaction() as tx:
        assert check_ids(tx.connection, registry) == []


def test_evlet_ids_check_prints_problems_and_exits_with_one(app, tmp_path, monkeypatch, capsys):
    from evlet.cli import ids as command
    from evlet.cli.main import main
    app, _ = app
    root = tmp_path / 'project'
    for name in ('apps', 'instances/acme'):
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(command, 'load_runtime', lambda project, name: app if (project, name) == (root, 'acme') else None)
    main(['ids', 'check', 'acme', '--project', str(root)])
    assert capsys.readouterr().out == 'Every model row has a record id, and the database refuses id changes\n'
    with app.database.transaction() as tx:
        tx.connection.execute('INSERT INTO note (id, body) VALUES (0, ?)', ('raw',))
    with pytest.raises(SystemExit) as exit:
        main(['ids', 'check', 'acme', '--project', str(root)])
    assert exit.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == 'note: 1 row(s) with an id that is empty, zero or negative\n' and captured.err == '1 problem\n'
