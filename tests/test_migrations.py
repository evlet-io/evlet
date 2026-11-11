import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from evlet.audit import COLUMNS as AUDIT_COLUMNS

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
        assert [row[1] for row in connection.execute('PRAGMA table_info(contact)')] == ['id', 'name', *AUDIT_COLUMNS]
        assert connection.execute('SELECT count(*) FROM evlet_migrations').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM contact').fetchone()[0] == 2
    db.initialize()


def test_changed_history_and_unplanned_type_changes_are_rejected(tmp_path):
    Old = old_model(); path = tmp_path / 'history.sqlite3'
    db = Database(path, Registry(Old)); db.initialize(); kept = db.create(Old, name='Kept')
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
    assert target.get(New, kept.id).name == 'Kept'


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
    db = Database(path, Registry(Old)); db.initialize(); price = db.create(Old, amount='123.45')
    New = type('Price', (Model,), {'amount': Decimal(scale=4, index=True)})
    with pytest.raises(SchemaError, match="price.amount: type is 'currency' after the migrations and 'decimal' in the models"):
        Database(path, Registry(New)).migrate([Migration('index', AddIndex('price', 'amount'))])
    assert db.get(Old, price.id).to_dict()['amount'] == '123.45'


def test_backfill_preserves_quotes_and_rejects_nul(tmp_path):
    Old = old_model(); path = tmp_path / 'quoted.sqlite3'
    db = Database(path, Registry(Old)); db.initialize(); kept = db.create(Old, name='Keep')
    New = type('Contact', (Model,), {'name': Text(required=True), 'notes': Text()})
    value = "O'Reilly'); DROP TABLE contact;--"
    target = Database(path, Registry(New))
    with pytest.raises(SchemaError, match='NUL'):
        target.migrate([Migration('notes', AddField('contact', 'notes', Text(), backfill='a\x00b'))])
    target.migrate([Migration('notes', AddField('contact', 'notes', Text(), backfill=value))])
    assert target.get(New, kept.id).notes == value


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
        assert [row[1] for row in c.execute('PRAGMA table_info(contact)')] == ['id', 'name', *AUDIT_COLUMNS]
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


def adopting(tmp_path, name='adopt.sqlite3'):
    """A database made before the app adopted migrations: Contact exists, with a row; the app now adds
    Contact.active and a Note model, and its first migration creates Contact as it was."""
    Old = old_model()
    path = tmp_path / name
    Database(path, Registry(Old)).initialize()
    kept = Database(path, Registry(Old)).create(Old, name='Kept').id
    New = type('Contact', (Model,), {'name': Text(required=True), 'active': Boolean(default=True)})
    Note = type('Note', (Model,), {'text': Text(default='')})
    return path, kept, New, Note


def test_create_model_if_missing_adopts_a_table_made_before_migrations(tmp_path):
    path, kept, New, Note = adopting(tmp_path)
    plain = [Migration('0001_contact', CreateModel(old_model())), Migration('0002_active', AddField('contact', 'active', Boolean(default=True))),
             Migration('0003_note', CreateModel(Note))]
    # A plain CreateModel of a table that is already there fails, pointing at the option.
    with pytest.raises(SchemaError, match=r'contact already exists in the schema; .*if_missing=True'):
        Database(path, Registry(New, Note)).migrate(plain)
    history = [Migration('0001_contact', CreateModel(old_model(), if_missing=True)), *plain[1:]]
    target = Database(path, Registry(New, Note), migrations=history)
    assert target.migrate(history) == ['0001_contact', '0002_active', '0003_note']
    target.initialize()
    assert target.get(New, kept).name == 'Kept' and target.get(New, kept).active is True
    assert target.create(Note, text='new').text == 'new'
    # On a fresh database the same history creates the table.
    fresh = Database(tmp_path / 'fresh.sqlite3', Registry(New, Note), migrations=history)
    assert fresh.migrate(history) == ['0001_contact', '0002_active', '0003_note']
    assert fresh.create(New, name='New').active is True
    # The option is part of the checksum only when it is set, so existing histories keep theirs.
    assert 'if_missing' not in CreateModel(old_model()).signature()
    assert CreateModel(old_model(), if_missing=True).signature()['if_missing'] is True
    with pytest.raises(SchemaError, match='True or False'):
        CreateModel(old_model(), if_missing='yes')


def test_create_model_if_missing_refuses_a_table_that_differs(tmp_path):
    path, kept, New, Note = adopting(tmp_path)
    Other = type('Contact', (Model,), {'name': Text(required=True, max_length=10)})
    history = [Migration('0001_contact', CreateModel(Other, if_missing=True)),
               Migration('0002_active', AddField('contact', 'active', Boolean(default=True)))]
    Target = type('Contact', (Model,), {'name': Text(required=True, max_length=10), 'active': Boolean(default=True)})
    with pytest.raises(SchemaError, match=r'CreateModel\(Contact, if_missing=True\): the existing contact differs .*\n  contact\.name'):
        Database(path, Registry(Target)).migrate(history)
    # The saved schema can agree while the table itself does not: the table is checked too, and nothing is written.
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE contact ADD COLUMN stray TEXT')
    history = [Migration('0001_contact', CreateModel(old_model(), if_missing=True)),
               Migration('0002_active', AddField('contact', 'active', Boolean(default=True)))]
    with pytest.raises(SchemaError, match=r'if_missing=True\): the existing table cannot be adopted: contact: database columns differ'):
        Database(path, Registry(New)).migrate(history)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT count(*) FROM evlet_migrations').fetchone()[0] == 0
        assert 'active' not in [row[1] for row in connection.execute('PRAGMA table_info(contact)')]


def check_required_changes(make):
    """AlterField changes `required` on a table with rows. make(registry, migrations=None) opens the database."""
    from evlet import AlterField, Integer
    Contact = type('Contact', (Model,), {'name': Text(required=True), 'phone': Text(), 'visits': Integer()})
    database = make(Registry(Contact))
    database.initialize()
    empty = database.create(Contact, name='No phone').id
    database.create(Contact, name='Has phone', phone='+971 4 000 0000', visits=2)
    Required = type('Contact', (Model,), {'name': Text(required=True), 'phone': Text(required=True), 'visits': Integer()})
    # Rows with no value refuse the change, naming how many and the first; nothing is written.
    plain = [Migration('0001_phone', AlterField('contact', 'phone', Text(required=True)))]
    with pytest.raises(SchemaError, match=rf'contact.phone cannot become required: 1 record has no value, the first #{empty}\. Give AlterField a fill='):
        make(Registry(Required)).migrate(plain)
    database = make(Registry(Contact))
    database.initialize()
    assert database.get(Contact, empty).phone is None
    # A fill value goes into the rows with none, then the column refuses empty values.
    filled = [Migration('0001_phone', AlterField('contact', 'phone', Text(required=True), fill='unknown'))]
    target = make(Registry(Required), filled)
    assert target.migrate(filled) == ['0001_phone']
    target.initialize()
    assert sorted(record.phone for record in target.list(Required)) == ['+971 4 000 0000', 'unknown']
    with pytest.raises(Exception, match='(?i)not null|null value|constraint was violated'):
        with target.transaction() as tx:
            tx.connection.execute('UPDATE contact SET phone = NULL')
    # Making a field optional always works, here together with making another required.
    Optional = type('Contact', (Model,), {'name': Text(), 'phone': Text(required=True), 'visits': Integer(required=True)})
    history = [*filled, Migration('0002_optional', AlterField('contact', 'name', Text()),
                                  AlterField('contact', 'visits', Integer(required=True), fill=0))]
    target = make(Registry(Optional), history)
    assert target.migrate(history) == ['0002_optional']
    target.initialize()
    created = target.create(Optional, phone='1', visits=1)
    assert created.name is None and target.get(Optional, empty).visits == 0
    # A fill must suit the field and only goes with required=True.
    for operation, message in ((AlterField('contact', 'phone', Text(required=True, max_length=3), fill='too long'), 'fill= is not valid'),
                               (AlterField('contact', 'phone', Text(), fill='x'), 'fill= is for making a field required')):
        with pytest.raises(SchemaError, match=message):
            operation.signature()
    # Without a fill the checksum is the one AlterField had before the option existed.
    assert 'fill' not in AlterField('contact', 'phone', Text(required=True)).signature()


def test_alter_field_changes_required_on_existing_rows(tmp_path):
    path = tmp_path / 'required.sqlite3'
    check_required_changes(lambda registry, migrations=None: Database(path, registry, migrations=migrations))
    # The constraint changes are made in place and leave a consistent file.
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert {row[1]: row[3] for row in connection.execute('PRAGMA table_info(contact)')}.items() >= {'name': 0, 'phone': 1, 'visits': 1}.items()


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
    preserved = old.create(old_registry.models['customer'], name='Preserved')
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
    assert Database(backup, old_registry).get(old_registry.models['customer'], preserved.id).name == 'Preserved'
    app = create_app(); app.initialize()
    assert app.database.get(Customer, preserved.id).customer_name == 'Preserved'
