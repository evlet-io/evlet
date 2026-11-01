"""Real PostgreSQL parity checks. CI must configure EVLET_TEST_POSTGRES_DSN."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from evlet import (Database, PostgreSQLDatabase, Model, Registry, Text, Integer,
                   Currency, Link, Migration, AddField, RenameField, AddIndex,
                   SchemaError, ConflictError, LocalAuth)
from examples.playground.app import build_app
from examples.playground.models import registry, Project
from tests.test_business import hashed, PASSWORD, client, http
from tests.test_business import (
    test_authentication_cookie_csrf_logout_and_expiry,
    test_reject_cross_origin_and_dns_rebinding,
    test_cookie_security_and_login_throttle,
    test_account_management_revokes_sessions_and_preserves_last_admin,
    test_viewer_cannot_mutate_any_business_surface,
    test_model_field_row_rules_apply_to_list_count_search_detail_writes_and_links,
)


@pytest.fixture
def pg_dsn(monkeypatch):
    databases = []
    original_init = PostgreSQLDatabase.__init__
    def track(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        databases.append(self)
    monkeypatch.setattr(PostgreSQLDatabase, '__init__', track)
    dsn = os.environ.get('EVLET_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Set EVLET_TEST_POSTGRES_DSN to a disposable PostgreSQL cluster')
    import psycopg
    from psycopg.conninfo import make_conninfo
    name = 'evlet_test_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
    try:
        yield make_conninfo(dsn, dbname=name)
    finally:
        for database in databases: database.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(psycopg.sql.Identifier(name)))


@pytest.fixture
def secured(pg_dsn, hashed):
    db = PostgreSQLDatabase(pg_dsn, registry)
    app = build_app(db, LocalAuth(db, origins=['http://127.0.0.1:8000'], secure_cookies=False))
    app.initialize()
    with db.transaction() as tx:
        for role in ('admin', 'editor', 'viewer'):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (role, hashed, role))
    return app


class Account(Model):
    name = Text(required=True, unique=True)
    amount = Currency(default=0)
    revision = Integer(default=0)


def test_declarative_documents_preview_revision_and_child_rollback(pg_dsn):
    from tests.test_documents import Order
    from evlet import Application
    db = PostgreSQLDatabase(pg_dsn, Registry(Order))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    c = client(app)
    payload = {'title': 'PostgreSQL', 'children': {'items': [{'_key': 'pg-row', 'qty': '3', 'rate': '0.10'}]}}
    assert c('POST', '/api/_draft/order', {'values': payload})[1]['values']['total'] == '0.30'
    status, record, headers = c('POST', '/api/order', payload)
    assert status == 201, record
    assert record['total'] == '0.30'
    assert c('PATCH', '/api/order/1', {'title': 'no revision'})[0] == 428
    payload['children']['items'][0]['qty'] = '0'
    assert c('PATCH', '/api/order/1', payload, headers={b'if-match': headers[b'etag']})[0] == 422
    assert c('GET', '/api/order/1')[1]['children']['items'][0]['qty'] == '3.0000'


class Entry(Model):
    account = Link(Account, required=True, index=True)
    amount = Currency(required=True)


def test_postgres_crud_constraints_queries_and_concurrent_transactions(pg_dsn):
    db = PostgreSQLDatabase(pg_dsn, Registry(Account, Entry))
    db.initialize(); db.initialize()
    account = db.create(Account, name='100% ? Mixed', amount='123456789.01')
    entry = db.create(Entry, account=account.id, amount='-1.01')
    assert db.related(entry, 'account').amount == account.amount
    assert db.list(Account, filters={'name__contains': '% ?'})[0].id == account.id
    assert db.list(Account, filters={'name__startswith': '100%'})[0].id == account.id
    assert db.list(Entry, filters={'account__name__icontains': 'mixed'})[0].id == entry.id
    assert db.list(Account, filters={'id__in': []}) == []
    assert db.count(Account, search='mixed') == 1
    with pytest.raises(ConflictError): db.create(Account, name=account.name)
    with pytest.raises(ConflictError): db.create(Entry, account=9999, amount=1)
    with pytest.raises(ConflictError): db.delete(Account, account.id)
    with pytest.raises(RuntimeError):
        with db.transaction() as tx:
            tx.update(Account, account.id, amount=0)
            raise RuntimeError('rollback')
    assert db.get(Account, account.id).amount == account.amount
    def increment(_):
        with db.transaction() as tx:
            current = tx.get(Account, account.id)
            tx.update(Account, current.id, revision=current.revision + 1)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(increment, range(100)))
    assert db.get(Account, account.id).revision == 100
    with db._connection() as connection:
        connection.execute('BEGIN')
        assert connection.execute('SELECT count(*) FROM account').fetchone()[0] == 1
        db.create(Account, name='After snapshot')
        assert connection.execute('SELECT count(*) FROM account').fetchone()[0] == 1
    assert db.count(Account) == 2


def test_postgres_migrations_history_and_rollback(pg_dsn):
    class Item(Model):
        name = Text(required=True)
    old = PostgreSQLDatabase(pg_dsn, Registry(Item))
    old.initialize(); old.create(Item, name='Keep')
    class Item(Model):
        title = Text(required=True, index=True)
        note = Text(required=True)
    plan = [Migration('rename', RenameField('item', 'name', 'title'), AddIndex('item', 'title'),
                      AddField('item', 'note', Text(required=True), backfill="50% ? 'quote' INTEGER PRIMARY KEY AUTOINCREMENT X''"))]
    db = PostgreSQLDatabase(pg_dsn, Registry(Item), migrations=plan)
    with pytest.raises(SchemaError): db.initialize()
    assert db.migrate(plan) == ['rename']
    db.initialize()
    assert db.get(Item, 1).note == "50% ? 'quote' INTEGER PRIMARY KEY AUTOINCREMENT X''"
    with pytest.raises(SchemaError): db.migrate([Migration('changed', AddIndex('item', 'title'))])
    assert db.get(Item, 1).title == 'Keep'


def test_postgres_actions_children_attachments_jobs_events_and_revocation(secured):
    c = client(secured)
    status, project, _ = c('POST', '/api/project', {'name': 'PG project'})
    assert status == 201, project
    path = f'/api/project/{project["id"]}'
    assert c('PATCH', path, {'children': {'tasks': [{'title': 'One', 'done': True}]}})[0] == 200
    status, attachment, _ = c('POST', path+'/attachments', {'name': 'private.txt', 'content': 'aGVsbG8='})
    assert status == 201, attachment
    assert c('GET', path+f'/attachments/{attachment["id"]}')[1] == b'hello'
    assert c('POST', path+'/actions/start')[0] == 200
    assert c('POST', path+'/actions/refresh_progress')[0] == 202
    assert secured.business.jobs.run_one()
    assert c('GET', path)[1]['progress'] == 100
    assert c('GET', '/api/_events?cursor=0')[0] == 200
    assert c('GET', '/api/_discovery/project')[0] == 200
    assert c('POST', '/api/auth/logout')[0] == 204
    assert c('GET', path)[0] == 401


def test_private_storage_and_events_reject_foreign_transactions(pg_dsn, tmp_path):
    from evlet import LocalFileStorage, EventStore, StorageError
    db = PostgreSQLDatabase(pg_dsn, Registry(Account))
    db.initialize()
    files = LocalFileStorage(tmp_path / 'files'); files.initialize(db)
    events = EventStore(db); events.initialize()
    other = Database(tmp_path / 'other.sqlite3', Registry(Account)); other.initialize()
    with other.transaction() as tx:
        with pytest.raises(StorageError): files.add(tx, 'account', 1, 'private.txt', b'private')
        with pytest.raises(ValueError): events.publish(tx, Account, 1)
    with db.transaction() as tx:
        record = tx.create(Account, name='Private')
        attachment = files.add(tx, 'account', record.id, 'private.txt', b'private')
        row = tx.connection.execute('SELECT * FROM evlet_attachments WHERE id=?', (attachment,)).fetchone()
        assert files.read(row) == b'private'
        events.publish(tx, Account, record.id)
    assert files.collect() == 0


def test_migration_failure_rolls_back_data_and_history(pg_dsn):
    class Item(Model):
        value = Text()
    original = PostgreSQLDatabase(pg_dsn, Registry(Item)); original.initialize()
    original.create(Item, value='same'); original.create(Item, value='same')
    class Item(Model):
        value = Text(unique=True)
        note = Text()
    db = PostgreSQLDatabase(pg_dsn, Registry(Item))
    plan = [Migration('bad', AddField('item','note',Text()), AddIndex('item','value',unique=True))]
    with pytest.raises(SchemaError): db.migrate(plan)
    original.initialize()
    assert original.count(next(iter(original.registry.models.values()))) == 2
    with original._connection() as c:
        assert 'note' not in original.columns(c, 'item')
        assert c.execute('SELECT count(*) FROM evlet_migrations').fetchone()[0] == 0


def test_nullable_ordering_parameter_literals_and_big_integers(pg_dsn):
    class Sample(Model):
        label = Text()
        number = Integer()
    db = PostgreSQLDatabase(pg_dsn, Registry(Sample)); db.initialize()
    first = db.create(Sample, number=2**62)
    second = db.create(Sample, label="instr ? 100% X'' INTEGER BLOB")
    assert db.get(Sample, first.id).number == 2**62
    assert db.list(Sample, order_by='label')[0].id == first.id
    assert db.list(Sample, order_by='-label')[0].id == second.id
    with db._connection() as c:
        assert c.execute("SELECT 'literal ? 50%' AS value, ? AS parameter", ('untrusted ? 80%',)).fetchone()[0] == 'literal ? 50%'



def test_postgres_rejects_nul_text_as_validation_error(pg_dsn):
    from evlet import ValidationError
    db = PostgreSQLDatabase(pg_dsn, Registry(Account)); db.initialize()
    with pytest.raises(ValidationError): db.create(Account, name='bad\x00text')
    assert db.count(Account) == 0



def test_pool_bounds_reuse_and_resets_session_state(pg_dsn):
    db = PostgreSQLDatabase(pg_dsn, Registry(Account), max_connections=2)
    db.initialize()
    with db._connection() as c:
        c.execute("SET search_path TO pg_catalog")
        c.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
        c.execute("INSERT INTO public.account(name,amount,revision) VALUES(?,?,?)", ('uncommitted', 0, 0))
    assert db.count(Account) == 0
    def read(_):
        with db._connection() as c:
            assert c.execute('SHOW search_path').fetchone()[0] == 'public,pg_catalog'
            return c.execute('SELECT pg_backend_pid()').fetchone()[0]
    with ThreadPoolExecutor(max_workers=20) as pool:
        ids = set(pool.map(read, range(100)))
    assert len(ids) <= 2
    db.close()
    db.check()


def test_pool_checkout_timeout_is_bounded(pg_dsn):
    import sqlite3
    import time
    db = PostgreSQLDatabase(pg_dsn, Registry(), timeout=1, max_connections=1)
    with db._connection():
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(sqlite3.OperationalError): pool.submit(db.check).result(timeout=3)
        assert time.monotonic()-started < 3


@pytest.mark.parametrize('unique', [False, True])
def test_reusing_indexed_field_names_preserves_existing_indexes(pg_dsn, unique):
    def field():
        return Text(index=not unique, unique=unique)

    Item = type('Item', (Model,), {'code': field()})
    original = PostgreSQLDatabase(pg_dsn, Registry(Item))
    original.initialize()
    original.create(Item, code='existing')
    plan = []
    for generation in (1, 2):
        Item = type('Item', (Model,), {
            'code': field(), **{f'code_{i}': field() for i in range(1, generation + 1)},
        })
        plan.append(Migration(f'replace_code_{generation}',
                              RenameField('item', 'code', f'code_{generation}'),
                              AddField('item', 'code', field())))
        updated = PostgreSQLDatabase(pg_dsn, Registry(Item), migrations=plan)
        assert updated.migrate(plan) == [f'replace_code_{generation}']
        updated.initialize()
        assert updated.get(Item, 1).code_1 == 'existing'
        assert updated.get(Item, 1).code is None
        assert updated.migrate(plan) == []

    assert updated.get(Item, 1).code_2 is None
    updated.create(Item, code='replacement', code_1='old', code_2='middle')
    if unique:
        for values in ({'code': 'replacement'}, {'code_1': 'existing'}, {'code_2': 'middle'}):
            with pytest.raises(ConflictError):
                updated.create(Item, **values)
    else:
        updated.create(Item, code='replacement', code_1='existing', code_2='middle')


@pytest.mark.parametrize('unique', [False, True])
@pytest.mark.parametrize('field_type', ['text', 'long_text'])
def test_indexed_text_requires_a_bounded_definition(pg_dsn, unique, field_type):
    from evlet import LongText
    field = Text if field_type == 'text' else LongText
    class Note(Model):
        value = field(max_length=501, index=not unique, unique=unique)
    with pytest.raises(SchemaError, match=r'note.value:.*max_length <= 500'):
        PostgreSQLDatabase(pg_dsn, Registry(Note))
    # Plain long text remains supported; only indexed declarations are restricted.
    class Note(Model):
        value = LongText()
    db = PostgreSQLDatabase(pg_dsn, Registry(Note)); db.initialize()
    value = 'long text ' * 500
    assert db.create(Note, value=value).value == value


@pytest.mark.parametrize('unique', [False, True])
def test_indexed_unicode_text_boundary_and_http_validation(pg_dsn, unique):
    import random
    from evlet import Application, LongText
    from tests.test_api import request
    class Note(Model):
        value = LongText(max_length=500, index=not unique, unique=unique)
    db = PostgreSQLDatabase(pg_dsn, Registry(Note)); db.initialize()
    rng = random.Random(123)
    value = ''.join(chr(rng.randrange(0x10000, 0x10FFFF)) for _ in range(500))
    assert len(value.encode('utf-8')) == 2000
    app = Application(db)
    status, record, _ = request(app, 'POST', '/api/note', {'value': value})
    assert status == 201 and record['value'] == value
    assert request(app, 'POST', '/api/note', {'value': value})[0] == (409 if unique else 201)
    for method, path in [('POST', '/api/note'), ('PATCH', f'/api/note/{record["id"]}')]:
        status, error, _ = request(app, method, path, {'value': value + 'x'})
        assert status == 422 and 'value' in error['fields']
    assert db.get(Note, record['id']).value == value
    assert db.list(Note, filters={'value': value})[0].value == value


@pytest.mark.parametrize('suffix', ['_id_seq', '_pkey'])
@pytest.mark.parametrize('reverse', [False, True])
def test_model_names_do_not_collide_with_identity_objects(pg_dsn, suffix, reverse):
    class Item(Model):
        name = Text()
    Other = type('Item' + suffix, (Model,), {'name': Text()})
    models = [Item, Other]
    db = PostgreSQLDatabase(pg_dsn, Registry(*(models[::-1] if reverse else models)))
    db.initialize(); db.initialize()
    assert db.create(Item, name='first').id == 1
    assert db.create(Other, name='second').id == 1


@pytest.mark.parametrize('suffix', ['_id_seq', '_pkey'])
def test_migration_moves_legacy_identity_names_without_resetting_ids(pg_dsn, suffix):
    from evlet import CreateModel
    from evlet.models import identifier
    class Item(Model):
        name = Text()
    original = PostgreSQLDatabase(pg_dsn, Registry(Item)); original.initialize()
    assert original.create(Item, name='preserved').id == 1
    with original.transaction() as tx:
        sequence = tx.connection.execute("SELECT relname FROM pg_class WHERE oid=pg_get_serial_sequence('item','id')::regclass").fetchone()[0]
        primary = tx.connection.execute("SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid='item'::regclass AND indisprimary").fetchone()[0]
        tx.connection.execute(f'ALTER SEQUENCE {identifier(sequence)} RENAME TO item_id_seq')
        tx.connection.execute(f'ALTER INDEX {identifier(primary)} RENAME TO item_pkey')
    Other = type('Item' + suffix, (Model,), {'name': Text()})
    target = PostgreSQLDatabase(pg_dsn, Registry(Item, Other))
    history = [Migration('other', CreateModel(Other))]
    assert target.migrate(history) == ['other']
    assert target.migrate(history) == []
    target.initialize()
    assert target.get(Item, 1).name == 'preserved'
    assert target.create(Item, name='next').id == 2
    assert target.create(Other, name='independent').id == 1


def test_playground_migration_cli_uses_postgres_and_preserves_sqlite(pg_dsn, tmp_path, monkeypatch):
    import subprocess
    import sys
    from examples.playground.migrate import legacy_registry
    from examples.playground.app import create_app
    from examples.playground.models import Customer
    old_registry = legacy_registry()
    old = PostgreSQLDatabase(pg_dsn, old_registry); old.initialize()
    old.create(old_registry.models['customer'], name='Keep PostgreSQL record')
    sqlite_path = tmp_path / 'unselected.sqlite3'
    sqlite_db = Database(sqlite_path, old_registry); sqlite_db.initialize()
    sqlite_db.create(old_registry.models['customer'], name='Keep SQLite record')
    before = sqlite_path.read_bytes()
    monkeypatch.setenv('EVLET_POSTGRES_DSN', pg_dsn)
    monkeypatch.setenv('EVLET_DATABASE', str(sqlite_path))
    for expected in ('Applied:', 'Schema is current.'):
        result = subprocess.run([sys.executable, '-m', 'examples.playground.migrate'],
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout
    app = create_app(); app.initialize()
    assert app.database.get(Customer, 1).name == 'Keep PostgreSQL record'
    assert sqlite_path.read_bytes() == before
    assert not list(tmp_path.glob('*.backup'))


def test_migration_has_its_own_timeout_and_pool_restores_request_limit(pg_dsn, monkeypatch):
    from evlet import Boolean
    import evlet.postgres_schema as schema
    class Item(Model):
        title = Text()
    old = PostgreSQLDatabase(pg_dsn, Registry(Item), timeout=1)
    old.initialize(); old.create(Item, title='Keep')
    class Item(Model):
        title = Text()
        done = Boolean(default=False)
    db = PostgreSQLDatabase(pg_dsn, Registry(Item), timeout=1, migration_timeout=3)
    original = schema.apply
    def slower(connection, operation):
        assert connection.execute("SELECT current_setting('statement_timeout')").fetchone()[0] == '3s'
        connection.execute('SELECT pg_sleep(1.1)')
        original(connection, operation)
    monkeypatch.setattr(schema, 'apply', slower)
    assert db.migrate([Migration('add_done', AddField('item', 'done', Boolean(default=False)))]) == ['add_done']
    assert db.get(Item, 1).title == 'Keep'
    with db._connection() as connection:
        assert connection.execute("SELECT current_setting('statement_timeout')").fetchone()[0] == '1s'


def test_postgres_new_field_types_store_filter_and_migrate(pg_dsn):
    from tests.test_rules_types import Contact, check_types_storage_and_filters
    db = PostgreSQLDatabase(pg_dsn, Registry(Contact))
    db.initialize()
    check_types_storage_and_filters(db)
    with db._connection() as connection:
        assert {'about_model', 'about_id'} <= db.columns(connection, 'contact')


def test_postgres_new_field_types_can_be_added_by_migration(pg_dsn):
    from tests.test_rules_types import add_types

    def make(registry, migrations, migrate=False):
        database = PostgreSQLDatabase(pg_dsn, registry, migrations=migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    add_types(make)


def test_postgres_initial_suggestions_rules_and_locks_over_http(pg_dsn):
    from tests import test_rules_types as rules
    app = rules.start(PostgreSQLDatabase(pg_dsn, Registry(rules.Client, rules.Product, rules.Deal)))
    rules.test_initial_suggestions_follow_their_inputs_and_keep_user_edits(rules.seed_deals(app))
    rules.test_applies_when_clears_and_skips_validation_and_required_when_is_enforced(rules.seed_deals(app))
    rules.test_locked_fields_reject_changes_over_http_actions_and_context(rules.seed_deals(app))


def test_postgres_recompute_updates_and_rolls_back(pg_dsn):
    from tests.test_rules_types import BASE, bill_models, check_recompute, start
    check_recompute(start(PostgreSQLDatabase(pg_dsn, Registry(bill_models('1')), migrations=[BASE])),
                    lambda registry, migrations: PostgreSQLDatabase(pg_dsn, registry, migrations=migrations))


def test_postgres_recompute_clears_values_that_do_not_apply(pg_dsn):
    from tests.test_rules_types import check_recompute_clears
    check_recompute_clears(lambda registry, migrations: PostgreSQLDatabase(pg_dsn, registry, migrations=migrations))


def test_postgres_renaming_a_reference_renames_both_columns(pg_dsn):
    from tests.test_rules_types import check_reference_rename
    check_reference_rename(lambda registry, migrations: PostgreSQLDatabase(pg_dsn, registry, migrations=migrations))


def test_postgres_states_moves_locks_and_actions_over_http(pg_dsn):
    from tests import test_states as states
    from tests.test_rules_types import start
    app = states.seed(start(PostgreSQLDatabase(pg_dsn, Registry(*states.MODELS))))
    states.check_ordinary_writes_cannot_set_the_state(app)
    states.check_moves_check_state_role_guard_and_revision(app)
    states.check_a_failing_run_rolls_the_move_back(app)
    states.check_locked_records_refuse_ordinary_writes_at_every_depth(app)
    states.check_actions_validate_inputs_and_availability(app)
