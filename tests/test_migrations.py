import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from evlet import (AddField, AddIndex, Boolean, CreateModel, Currency, Database, Decimal,
                   Link, Migration, Model, Registry, RenameField, SchemaError, Text)


def old_model():
    return type('Contact', (Model,), {'name': Text(required=True)})


def test_explicit_add_rename_index_preserves_ids_and_links(tmp_path):
    Old = old_model()
    class Invoice(Model):
        contact = Link(Old, required=True)
        total = Currency(required=True)
    path = tmp_path / 'migrations.sqlite3'
    db = Database(path, Registry(Old, Invoice))
    db.initialize()
    contact = db.create(Old, name="O'Reilly")
    invoice = db.create(Invoice, contact=contact.id, total='123.45')
    New = type('Contact', (Model,), {'title': Text(required=True, index=True), 'active': Boolean(required=True, default=True)})
    NewInvoice = type('Invoice', (Model,), {'contact': Link(New, required=True), 'total': Currency(required=True)})
    target = Database(path, Registry(New, NewInvoice))
    with pytest.raises(SchemaError):
        target.initialize()
    plan = [Migration('0001_contact', RenameField('contact', 'name', 'title'),
                      AddField('contact', 'active', Boolean(required=True, default=True)),
                      AddIndex('contact', 'title'))]
    assert target.migrate(plan) == ['0001_contact']
    assert target.migrate(plan) == []
    target.initialize()
    assert target.get(New, contact.id).title == "O'Reilly"
    assert target.get(New, contact.id).active is True
    assert target.related(target.get(NewInvoice, invoice.id), 'contact').id == contact.id
    assert target.get(NewInvoice, invoice.id).to_dict()['total'] == '123.45'
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_failed_migration_rolls_back_ddl_data_and_history(tmp_path):
    Old = old_model()
    path = tmp_path / 'rollback.sqlite3'
    db = Database(path, Registry(Old)); db.initialize()
    db.create(Old, name='One'); db.create(Old, name='Two')
    New = type('Contact', (Model,), {'name': Text(required=True), 'code': Text(required=True, unique=True)})
    target = Database(path, Registry(New))
    with pytest.raises(SchemaError, match='rolled back'):
        target.migrate([Migration('bad', AddField('contact', 'code', Text(required=True, unique=True), backfill='same'))])
    with sqlite3.connect(path) as connection:
        assert [row[1] for row in connection.execute('PRAGMA table_info(contact)')] == ['id', 'name']
        assert connection.execute('SELECT count(*) FROM evlet_migrations').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM contact').fetchone()[0] == 2
    db.initialize()


def test_changed_history_and_unplanned_type_changes_are_rejected(tmp_path):
    Old = old_model(); path = tmp_path / 'history.sqlite3'
    db = Database(path, Registry(Old)); db.initialize(); db.create(Old, name='Kept')
    New = type('Contact', (Model,), {'name': Text(required=True), 'active': Boolean(default=True)})
    target = Database(path, Registry(New))
    plan = [Migration('one', AddField('contact', 'active', Boolean(default=True)))]
    target.migrate(plan)
    for edited in ([], [Migration('one', AddField('contact', 'active', Boolean(default=False)))],
                   [Migration('other', AddField('contact', 'active', Boolean(default=True)))]):
        with pytest.raises(SchemaError):
            target.migrate(edited)
    Changed = type('Contact', (Model,), {'name': Text(required=True, max_length=1), 'active': Boolean(default=True)})
    with pytest.raises(SchemaError):
        Database(path, Registry(Changed)).migrate(plan)
    assert target.get(New, 1).name == 'Kept'


def test_legacy_baseline_and_fresh_database(tmp_path):
    Old = old_model(); path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE contact (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)')
        connection.execute("INSERT INTO contact(name) VALUES ('Legacy')")
    New = type('Contact', (Model,), {'name': Text(required=True), 'active': Boolean(default=True)})
    plan = [Migration('one', AddField('contact', 'active', Boolean(default=True)))]
    with pytest.raises(SchemaError, match='original models'):
        Database(path, Registry(New)).migrate(plan)
    Database(path, Registry(Old)).initialize()
    target = Database(path, Registry(New)); target.migrate(plan)
    assert target.get(New, 1).name == 'Legacy'
    for fresh_path in (tmp_path / 'fresh.sqlite3', tmp_path / 'initialized.sqlite3'):
        fresh = Database(fresh_path, Registry(New))
        if fresh_path.name.startswith('initialized'):
            fresh.initialize()
        assert fresh.migrate(plan) == ['one']
        assert fresh.migrate(plan) == []
        assert fresh.create(New, name='New').active is True


def test_concurrent_migration_runs_apply_once(tmp_path):
    Old = old_model(); path = tmp_path / 'concurrent.sqlite3'
    db = Database(path, Registry(Old)); db.initialize()
    New = type('Contact', (Model,), {'name': Text(required=True), 'active': Boolean(default=True)})
    plan = [Migration('one', AddField('contact', 'active', Boolean(default=True)))]
    target = Database(path, Registry(New))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: target.migrate(plan), range(2)))
    assert sorted(len(result) for result in results) == [0, 1]


def test_create_model_and_atomic_batch(tmp_path):
    Old = old_model(); path = tmp_path / 'create.sqlite3'
    db = Database(path, Registry(Old)); db.initialize()
    record = db.create(Old, name='First')
    class Note(Model):
        contact = Link(Old)
        message = Text()
    target = Database(path, Registry(Old, Note))
    target.migrate([Migration('notes', CreateModel(Note))])
    assert target.create(Note, contact=record.id, message='Saved').contact == record.id


def test_currency_scale_change_cannot_silently_reinterpret_values(tmp_path):
    Old = type('Price', (Model,), {'amount': Currency()})
    path = tmp_path / 'currency.sqlite3'
    db = Database(path, Registry(Old)); db.initialize(); db.create(Old, amount='123.45')
    New = type('Price', (Model,), {'amount': Decimal(scale=4, index=True)})
    with pytest.raises(SchemaError, match="price.amount: type is 'currency' after the migrations and 'decimal' in the models"):
        Database(path, Registry(New)).migrate([Migration('index', AddIndex('price', 'amount'))])
    assert db.get(Old, 1).to_dict()['amount'] == '123.45'


def test_backfill_preserves_quotes_and_rejects_nul(tmp_path):
    Old = old_model(); path = tmp_path / 'quoted.sqlite3'
    db = Database(path, Registry(Old)); db.initialize(); db.create(Old, name='Keep')
    New = type('Contact', (Model,), {'name': Text(required=True), 'notes': Text()})
    value = "O'Reilly'); DROP TABLE contact;--"
    target = Database(path, Registry(New))
    with pytest.raises(SchemaError, match='NUL'):
        target.migrate([Migration('notes', AddField('contact', 'notes', Text(), backfill='a\x00b'))])
    target.migrate([Migration('notes', AddField('contact', 'notes', Text(), backfill=value))])
    assert target.get(New, 1).notes == value


def test_multiple_pending_migrations_roll_back_together(tmp_path):
    Old = old_model(); path = tmp_path / 'batch.sqlite3'
    db = Database(path, Registry(Old)); db.initialize()
    db.create(Old, name='Same'); db.create(Old, name='Same')
    New = type('Contact', (Model,), {'name': Text(required=True, unique=True), 'active': Boolean(default=True)})
    with pytest.raises(SchemaError):
        Database(path, Registry(New)).migrate([
            Migration('one', AddField('contact', 'active', Boolean(default=True))),
            Migration('two', AddIndex('contact', 'name', unique=True)),
        ])
    with sqlite3.connect(path) as c:
        assert [row[1] for row in c.execute('PRAGMA table_info(contact)')] == ['id', 'name']
        assert c.execute('SELECT count(*) FROM evlet_migrations').fetchone()[0] == 0
    db.initialize()


@pytest.mark.parametrize('installation', ['upgraded', 'fresh', 'adopted', 'migrate'])
def test_next_migration_preserves_records_on_every_installation_path(tmp_path, installation):
    Old = old_model()
    Current = type('Contact', (Model,), {'name': Text(required=True), 'active': Boolean(default=True)})
    Next = type('Contact', (Model,), {
        'name': Text(required=True), 'active': Boolean(default=True), 'notes': Text(),
    })
    first = Migration('one', AddField('contact', 'active', Boolean(default=True)))
    second = Migration('two', AddField('contact', 'notes', Text()))
    path = tmp_path / 'next.sqlite3'
    if installation == 'upgraded':
        original = Database(path, Registry(Old))
        original.initialize()
        record = original.create(Old, name='Keep this record')
        Database(path, Registry(Current)).migrate([first])
    else:
        if installation == 'adopted':
            original = Database(path, Registry(Current))
            original.initialize()
            record = original.create(Current, name='Keep this record', active=False)
        current = Database(path, Registry(Current), migrations=[first])
        if installation == 'migrate':
            current.migrate([first])
        else:
            current.initialize()
        if installation != 'adopted':
            record = current.create(Current, name='Keep this record', active=False)
        current.initialize()

    target = Database(path, Registry(Next), migrations=[first, second])
    with pytest.raises(SchemaError, match='explicit migrations'):
        target.initialize()
    assert target.migrate([first, second]) == ['two']
    target.initialize()
    assert target.migrate([first, second]) == []
    saved = target.get(Next, record.id)
    assert saved.name == 'Keep this record'
    assert saved.active is (installation == 'upgraded')
    assert saved.notes is None
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            'SELECT name, checksum FROM evlet_migrations ORDER BY position'
        ).fetchall() == [('one', first.checksum()), ('two', second.checksum())]


def test_startup_rejects_changed_or_incomplete_declared_history(tmp_path):
    Current = type('Contact', (Model,), {'name': Text(required=True), 'active': Boolean(default=True)})
    path = tmp_path / 'declared.sqlite3'
    first = Migration('one', AddField('contact', 'active', Boolean(default=True)))
    database = Database(path, Registry(Current), migrations=[first])
    database.initialize()
    record = database.create(Current, name='Keep')
    for plan in (
        [],
        [Migration('one', AddField('contact', 'active', Boolean(default=False)))],
        [Migration('renamed', AddField('contact', 'active', Boolean(default=True)))],
        [first, first],
        [first, Migration('two', AddIndex('contact', 'name'))],
    ):
        with pytest.raises(SchemaError):
            Database(path, Registry(Current), migrations=plan).initialize()
    database.initialize()
    assert database.get(Current, record.id).name == 'Keep'
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT name FROM evlet_migrations').fetchall() == [('one',)]


def test_baseline_adoption_validates_existing_rows_before_recording_history(tmp_path):
    Current = type('Contact', (Model,), {'name': Text(required=True), 'active': Boolean(default=True)})
    path = tmp_path / 'invalid.sqlite3'
    original = Database(path, Registry(Current))
    original.initialize()
    record = original.create(Current, name='Keep')
    with sqlite3.connect(path) as connection:
        connection.execute('UPDATE contact SET active = 2 WHERE id = ?', (record.id,))
    first = Migration('one', AddField('contact', 'active', Boolean(default=True)))
    with pytest.raises(SchemaError, match='stored data fails validation'):
        Database(path, Registry(Current), migrations=[first]).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT name FROM evlet_migrations').fetchall() == []
        assert connection.execute('SELECT active FROM contact').fetchone() == (2,)


@pytest.mark.parametrize('dsn', [None, ''])
def test_playground_migration_cli_keeps_sqlite_selection_and_backup(tmp_path, monkeypatch, dsn):
    import subprocess
    import sys
    from examples.playground.migrate import legacy_registry
    from examples.playground.app import create_app
    from examples.playground.models import Customer
    path = tmp_path / 'playground.sqlite3'
    old_registry = legacy_registry()
    old = Database(path, old_registry); old.initialize()
    old.create(old_registry.models['customer'], name='Preserved')
    monkeypatch.setenv('EVLET_DATABASE', str(path))
    if dsn is None:
        monkeypatch.delenv('EVLET_POSTGRES_DSN', raising=False)
    else:
        monkeypatch.setenv('EVLET_POSTGRES_DSN', dsn)
    result = subprocess.run([sys.executable, '-m', 'examples.playground.migrate'],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'Backup:' in result.stdout and 'Applied:' in result.stdout
    backup, = tmp_path.glob('*.backup')
    assert Database(backup, old_registry).get(old_registry.models['customer'], 1).name == 'Preserved'
    app = create_app(); app.initialize()
    assert app.database.get(Customer, 1).name == 'Preserved'
