"""When and by whom records were created and last changed (evlet/audit.py): every write path, `_audit` in
details with permission-safe names, and the framework step that adds and fills the columns, on real SQLite."""

import sqlite3

import pytest

from evlet import Database, Model, Registry, Rule, Text
from evlet import audit
from evlet.auth import User
from evlet.framework import upgrade
from evlet.identity import RecordId
from test_business import client
from test_rules_types import ALL, add_user, start
import test_effects as books_module
import test_journal as journal_module


class Note(Model):
    """A plain model: no State, no journal, written by raw Session or the plain record routes."""
    permissions = {**ALL, 'portal': Rule(('read',))}
    title = Text(default='')


MODELS = (Note, *books_module.MODELS)


@pytest.fixture
def clock(monkeypatch):
    """Each stamp is one second after the last, so the order of writes shows in the times."""
    ticks = (f'2026-11-04T08:{minute:02d}:{second:02d}.000Z' for minute in range(60) for second in range(60))
    monkeypatch.setattr(audit, 'now', lambda: next(ticks))


@pytest.fixture
def app(tmp_path, clock):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(*MODELS)))
    add_user(app, 'editor')
    return app


def stamps(app, table, record_id):
    with app.database.transaction() as tx:
        row = tx.connection.execute(f'SELECT {", ".join(audit.COLUMNS)} FROM {table} WHERE id=?', (RecordId(record_id),)).fetchone()
    return tuple(row)


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


def test_plain_routes_record_who_and_when_and_details_show_it(app):
    admin, editor = client(app), client(app, 'editor')
    status, note, _ = admin('POST', '/api/note', {'title': 'First'})
    assert status == 201
    url = f"/api/note/{note['id']}"
    assert stamps(app, 'note', note['id']) == ('2026-11-04T08:00:00.000Z', 1, '2026-11-04T08:00:00.000Z', 1)
    assert note['_audit'] == {'created_at': '2026-11-04T08:00:00.000Z', 'created_by': {'id': 1, 'name': 'admin'},
                              'modified_at': '2026-11-04T08:00:00.000Z', 'modified_by': {'id': 1, 'name': 'admin'}}
    assert editor('PATCH', url, {'title': 'Second'}, headers=etag(editor, url))[0] == 200
    created_at, created_by, modified_at, modified_by = stamps(app, 'note', note['id'])
    assert (created_at, created_by, modified_by) == ('2026-11-04T08:00:00.000Z', 1, 2) and modified_at > created_at
    # The display name is the short name, then the full name, then the username.
    assert editor('PATCH', '/api/_settings/me/profile', {'full_name': 'Priya Sharma', 'short_name': 'Priya S.'})[0] == 200
    before = editor('GET', url)
    assert before[1]['_audit']['modified_by'] == {'id': 2, 'name': 'Priya S.'}
    assert before[1]['_audit']['created_by'] == {'id': 1, 'name': 'admin'}
    # Times and names are outside the ETag: a new display name does not make open records stale.
    assert editor('PATCH', '/api/_settings/me/profile', {'short_name': 'P. Sharma'})[0] == 200
    after = editor('GET', url)
    assert after[1]['_audit']['modified_by']['name'] == 'P. Sharma' and after[2][b'etag'] == before[2][b'etag']
    # Lists do not carry them.
    assert '_audit' not in admin('GET', '/api/note')[1]['items'][0]


def test_discovery_describes_audit_so_tool_clients_validate_it(app):
    """MCP and the connector check record output against Discovery's read schema, which allows no other keys."""
    c = client(app)
    note = c('POST', '/api/note', {'title': 'x'})[1]
    schema = c('GET', '/api/_discovery/note')[1]['schemas']['read']
    assert schema['properties']['_audit']['required'] == ['created_at', 'created_by', 'modified_at', 'modified_by']
    pytest.importorskip('jsonschema').validate(c('GET', f"/api/note/{note['id']}")[1], schema)


def test_the_columns_are_not_fields_and_cannot_be_written(app):
    c = client(app)
    assert all(column not in Note.__fields__ for column in audit.COLUMNS)
    assert c('POST', '/api/note', {'title': 'x', 'evlet_created_by': 7})[:2] == (403, {'error': 'One or more fields cannot be written'})
    with pytest.raises(Exception, match='Unknown field'):
        with app.database.transaction() as tx:
            tx.create(Note, title='x', evlet_created_by=7)
    schema = c('GET', '/api/schema')[1]
    fields = next(model for model in schema['models'] if model['name'] == 'note')['fields']
    assert not any(field['name'].startswith('evlet_') for field in fields)


def test_the_column_names_are_reserved():
    clash = type('Clash', (Model,), {'evlet_modified_at': Text()})
    with pytest.raises(ValueError, match='evlet_modified_at is reserved'):
        Registry(clash)


def test_python_sessions_record_the_engine_or_the_actor_they_are_given(app):
    with app.database.transaction() as tx:
        engine = tx.create(Note, title='Engine')
        tx.actor = 2
        mine = tx.create(Note, title='Mine')
        tx.update(Note, engine.id, title='Touched')
    assert stamps(app, 'note', engine.id) == ('2026-11-04T08:00:00.000Z', None, '2026-11-04T08:00:02.000Z', 2)
    assert stamps(app, 'note', mine.id)[1] == 2
    # A Python session read shows the engine as no one.
    with app.database.transaction() as tx:
        detail = app.business.detail(tx, User(1, 'admin', 'admin'), tx.get(Note, engine.id))
    assert detail['_audit']['created_by'] is None and detail['_audit']['modified_by'] == {'id': 2, 'name': 'editor'}
    # An update that changes nothing writes nothing, so it keeps the times.
    with app.database.transaction() as tx:
        tx.update(Note, mine.id)
    assert stamps(app, 'note', mine.id)[2] == '2026-11-04T08:00:01.000Z'


def test_document_writes_actions_moves_effects_and_void_record_the_person(app):
    c, editor = client(app), client(app, 'editor')
    account = c('POST', '/api/account', {'title': 'Sales'})[1]['id']
    url = books_module.invoice(editor, account)
    record_id = url.rsplit('/', 1)[1]
    created = stamps(app, 'invoice', record_id)
    assert created[1] == created[3] == 2
    # A row change is a change of the document.
    rows = editor('GET', url)[1]['children']['lines']
    status, _, _ = editor('PATCH', url, {'children': {'lines': {'update': [{'id': rows[0]['id'], 'qty': '3'}]}}}, headers=etag(editor, url))
    assert status == 200
    changed = stamps(app, 'invoice', record_id)
    assert changed[:2] == created[:2] and changed[2] > created[2] and changed[3] == 2
    # A move by admin posts effect rows created by admin, in the same write.
    assert books_module.move(c, url, 'issued')[0] == 200
    moved = stamps(app, 'invoice', record_id)
    assert moved[3] == 1 and moved[2] > changed[2]
    entries = books_module.entries(app)
    assert entries and all(entry['evlet_created_by'] == 1 and entry['evlet_created_at'] > changed[2] for entry in entries)
    # A void changes only the state, and still counts as a change by whoever voided.
    assert editor('POST', f'{url}/void', {'reason': 'Wrong'}, headers=etag(editor, url))[0] == 200
    voided = stamps(app, 'invoice', record_id)
    assert voided[3] == 2 and voided[2] > moved[2] and voided[:2] == created[:2]


def test_contexts_and_actions_in_python_record_their_user(tmp_path, clock):
    app = journal_module.seed(start(Database(tmp_path / 'db.sqlite3', Registry(*journal_module.MODELS))))
    editor = User(2, 'editor', 'editor')
    with app.business.context(editor) as context:
        memo = context.create(journal_module.Memo, title='From Python')
    assert stamps(app, 'memo', memo['id'])[1] == 2
    admin = User(1, 'admin', 'admin')
    c = client(app)
    record, url = journal_module.new_invoice(c)
    with app.business.context(editor) as context:
        context.action(journal_module.Invoice, record['id'], 'rename', title='Renamed')
    assert stamps(app, 'invoice', record['id'])[3] == 2
    # Two contexts in one transaction: each write is recorded under its own context's user.
    with app.database.transaction() as tx:
        from evlet.documents import Context
        first = Context(app.business, tx, admin).create(journal_module.Memo, title='a')
        second = Context(app.business, tx, editor).create(journal_module.Memo, title='b')
        Context(app.business, tx, admin).update(journal_module.Memo, second['id'], title='c')
    assert stamps(app, 'memo', first['id'])[1] == 1
    assert stamps(app, 'memo', second['id'])[1::2] == (2, 1)


def test_portal_roles_see_times_but_not_who(app):
    with app.database.transaction() as tx:
        tx.actor = 1
        note = tx.create(Note, title='Shared')
        app.business.controls.roles.setdefault('portal', {'realm': 'portal'})
        detail = app.business.detail(tx, User(9, 'customer', 'portal'), note)
    assert detail['_audit']['created_at'] and detail['_audit']['created_by'] is None and detail['_audit']['modified_by'] is None


def test_upgrade_adds_and_fills_the_columns_from_the_journal_and_the_id(tmp_path):
    path = tmp_path / 'old.sqlite3'
    old = Database(path, Registry(Note))
    old.initialize()
    upgrade(old)
    from evlet.identity import new_id
    recent = int(new_id())
    with sqlite3.connect(path) as connection:
        # A database from before this release: no time columns, a legacy id, a time-ordered id, and one with a journal.
        for column in audit.COLUMNS:
            connection.execute(f'ALTER TABLE note DROP COLUMN {column}')
        # Roll the framework ledger back to before 0004, so the later steps run again too.
        connection.execute("DELETE FROM evlet_framework_migrations WHERE position >= (SELECT position FROM evlet_framework_migrations WHERE name='0004_record_times')")
        connection.execute("INSERT INTO note (id, title) VALUES (42, 'Legacy')")
        connection.execute("INSERT INTO note (id, title) VALUES (?, 'Recent')", (recent,))
        connection.execute("INSERT INTO note (id, title) VALUES (?, 'Journaled')", (recent + 1,))
        connection.execute('CREATE TABLE evlet_journal (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, record_id INTEGER NOT NULL, '
                           'kind TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL, prev_hash TEXT, hash TEXT)')
        for kind, actor, at in (('created', '3', '2026-10-01T10:00:00.000Z'), ('note', '5', '2026-10-02T10:00:00.000Z'),
                                ('changed', 'engine', '2026-10-03T10:00:00.000Z'), ('moved', '4', '2026-10-04T10:00:00.000Z'),
                                ('file', '6', '2026-10-05T10:00:00.000Z')):
            connection.execute("INSERT INTO evlet_journal (model, record_id, kind, actor, at, data) VALUES ('note', ?, ?, ?, ?, '{}')",
                               (recent + 1, kind, actor, at))
    database = Database(path, Registry(Note))
    database.initialize()  # A table without the time columns is accepted until the framework step runs.
    assert upgrade(database) == ['0004_record_times', '0005_unsaved_changes']
    with sqlite3.connect(path) as connection:
        rows = {row[0]: row[1:] for row in connection.execute(f'SELECT id, {", ".join(audit.COLUMNS)} FROM note')}
    assert rows[42] == (None, None, None, None)
    at = audit.id_time(recent)
    assert rows[recent] == (at, None, at, None) and at.startswith('2026-')
    assert rows[recent + 1] == ('2026-10-01T10:00:00.000Z', 3, '2026-10-04T10:00:00.000Z', 4)
    assert upgrade(database) == []
    # New writes fill them.
    assert database.create(Note, title='New')._audit[0] is not None


def test_some_but_not_all_columns_is_a_schema_error(tmp_path):
    path = tmp_path / 'db.sqlite3'
    database = Database(path, Registry(Note))
    database.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE note DROP COLUMN evlet_modified_by')
    with pytest.raises(Exception, match='record time columns are incomplete'):
        database.initialize()


def test_id_time_reads_the_time_in_an_id():
    assert audit.id_time(42) is None
    assert audit.id_time(1 << 21) == '2026-01-01T00:00:00.001Z'
    assert audit.id_time(RecordId('01hc1sd9vc1ye')).endswith('Z')


# PostgreSQL

from test_postgres import pg_dsn  # noqa: E402,F401  (skips without EVLET_TEST_POSTGRES_DSN)


def test_postgresql_records_times_and_backfills_them(pg_dsn, clock):
    from evlet import PostgreSQLDatabase
    app = start(PostgreSQLDatabase(pg_dsn, Registry(*MODELS)))
    add_user(app, 'editor')
    admin, editor = client(app), client(app, 'editor')
    note = admin('POST', '/api/note', {'title': 'First'})[1]
    url = f"/api/note/{note['id']}"
    assert editor('PATCH', url, {'title': 'Second'}, headers=etag(editor, url))[0] == 200
    created_at, created_by, modified_at, modified_by = stamps(app, 'note', note['id'])
    assert (created_by, modified_by) == (1, 2) and modified_at > created_at
    assert editor('GET', url)[1]['_audit']['modified_by'] == {'id': 2, 'name': 'editor'}
    account = admin('POST', '/api/account', {'title': 'Sales'})[1]['id']
    invoice = books_module.invoice(editor, account)
    assert books_module.move(admin, invoice, 'issued')[0] == 200
    assert stamps(app, 'invoice', invoice.rsplit('/', 1)[1])[1::2] == (2, 1)
    assert {entry['evlet_created_by'] for entry in books_module.entries(app)} == {1}
    # An older database: the columns go, the framework step adds them back and fills them from the journal.
    with app.database.transaction() as tx:
        for column in audit.COLUMNS:
            tx.connection.execute(f'ALTER TABLE note DROP COLUMN {column}')
        tx.connection.execute("DELETE FROM evlet_framework_migrations WHERE position >= "
                              "(SELECT position FROM evlet_framework_migrations WHERE name='0004_record_times')")
        tx.connection.execute("INSERT INTO evlet_journal (model, record_id, kind, actor, at, data) VALUES ('note', ?, 'created', '2', "
                              "'2026-10-01T10:00:00.000Z', '{}')", (RecordId(note['id']),))
    app.database.initialize()
    assert upgrade(app.database) == ['0004_record_times', '0005_unsaved_changes']
    assert stamps(app, 'note', note['id']) == ('2026-10-01T10:00:00.000Z', 2, '2026-10-01T10:00:00.000Z', 2)
    assert upgrade(app.database) == []
