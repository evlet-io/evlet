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
from tests.test_filters import (  # noqa: F401  (the items fixture and filter tests, run here on PostgreSQL)
    items,
    test_every_operator_by_field_type,
    test_link_filters_and_operators_a_type_does_not_take,
    test_child_groups_match_one_row_without_repeating_parents,
    test_many_conditions_and_the_limit,
    test_hidden_fields_and_tables_are_refused_not_answered,
    test_row_scope_still_applies_to_filtered_lists,
    test_injection_attempts_are_values_or_refused,
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


def test_postgres_void_revise_permissions_and_rollback(pg_dsn, monkeypatch):
    from tests import test_corrections as corrections
    from tests.test_rules_types import start
    app = corrections.seed(start(PostgreSQLDatabase(pg_dsn, Registry(*corrections.MODELS))))
    corrections.check_void(app)
    corrections.check_revise(app)
    corrections.check_rollback(app, monkeypatch)
    corrections.check_permissions(app)
    with app[0].database.transaction() as tx:
        tx.connection.execute('SET LOCAL enable_seqscan = off')
        plan = ' '.join(row[0] for row in tx.connection.execute(
            'EXPLAIN SELECT id FROM "quote" WHERE "revises"=? ORDER BY id LIMIT 1', (1,)).fetchall())
    assert 'Index' in plan and 'revises' in plan, plan


def test_postgres_revise_is_refused_up_front_outside_the_callers_scope(pg_dsn):
    from tests import test_corrections as corrections
    from tests.test_rules_types import start
    app = start(PostgreSQLDatabase(pg_dsn, Registry(corrections.Vendor, corrections.Bill)))
    corrections.check_revise_is_predicted((app, client(app)))


def test_postgres_revisable_needs_an_add_field_migration(pg_dsn):
    from tests.test_corrections import check_revises_migration

    def make(registry, migrations, migrate=False):
        database = PostgreSQLDatabase(pg_dsn, registry, migrations=migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    check_revises_migration(make)


@pytest.mark.parametrize('check', ['check_numbers_follow_moves_scopes_and_periods', 'check_assigned_numbers_fix_their_scope_and_period',
                                   'check_failures_do_not_consume_numbers', 'check_only_the_engine_writes_a_sequence',
                                   'check_assigned_numbers_are_unique_per_scope', 'check_a_planted_number_is_skipped',
                                   'check_roles_that_cannot_read_the_number_learn_nothing',
                                   'check_raw_session_writes_number_and_keep_scope', 'check_void_and_revise_keep_and_renew_numbers',
                                   'check_a_caught_revise_failure_leaves_no_number'])
def test_postgres_sequences_assign_scope_reset_and_stay_engine_written(pg_dsn, check):
    from tests import test_sequences as sequences
    from tests.test_rules_types import start
    getattr(sequences, check)(sequences.seed(start(PostgreSQLDatabase(pg_dsn, Registry(*sequences.MODELS)))))


def test_postgres_parallel_sessions_get_distinct_gapless_numbers(pg_dsn):
    from tests import test_sequences as sequences
    from tests.test_rules_types import start
    app = start(PostgreSQLDatabase(pg_dsn, Registry(*sequences.MODELS)))
    sequences.check_parallel_writers_get_distinct_gapless_numbers(app, writers=8, each=9)
    with app.database.transaction() as tx:
        index = tx.connection.execute("SELECT indexdef FROM pg_indexes WHERE tablename='invoice' AND indexdef LIKE '%IS NOT NULL%'").fetchone()[0]
    assert 'UNIQUE' in index and '(company, number)' in index


def test_postgres_sequence_can_be_added_by_migration(pg_dsn):
    from tests.test_sequences import check_a_sequence_can_be_added_to_an_existing_table

    def make(registry, migrations, migrate=False):
        database = PostgreSQLDatabase(pg_dsn, registry, migrations=migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    check_a_sequence_can_be_added_to_an_existing_table(make)


def test_postgres_a_caught_failure_leaves_no_trace(pg_dsn):
    from tests import test_sequences as sequences
    from tests.test_rules_types import start
    sequences.check_a_caught_failure_leaves_no_trace(start(PostgreSQLDatabase(pg_dsn, Registry(*sequences.MODELS))))


def test_postgres_sequence_scope_field_can_be_renamed(pg_dsn):
    from tests.test_sequences import check_a_scope_field_can_be_renamed

    def make(registry, migrations, migrate=False):
        database = PostgreSQLDatabase(pg_dsn, registry, migrations=migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    check_a_scope_field_can_be_renamed(make)


def test_postgres_related_records_follow_row_scope(pg_dsn):
    from tests import test_meaning as meaning
    meaning.check_related(meaning.serve(PostgreSQLDatabase(pg_dsn, Registry(*meaning.MODELS))))


def test_postgres_attachment_limits_from_meta(pg_dsn):
    from tests import test_meaning as meaning
    meaning.check_attachment_limits(meaning.serve(PostgreSQLDatabase(pg_dsn, Registry(*meaning.MODELS))))


def effect_books(pg_dsn):
    from tests import test_effects as effects
    from tests.test_rules_types import start
    return effects, effects.seed(start(PostgreSQLDatabase(pg_dsn, Registry(*effects.MODELS))))


@pytest.mark.parametrize('name', ['test_a_move_posts_balanced_rows_atomically', 'test_unbalanced_rows_roll_the_move_back',
                                  'test_an_invalid_row_rolls_the_move_back'])
def test_postgres_effects_post_balance_and_roll_back(pg_dsn, name):
    effects, books = effect_books(pg_dsn)
    getattr(effects, name)(books)


def test_postgres_effect_rows_are_read_only(pg_dsn):
    effects, books = effect_books(pg_dsn)
    effects.test_effect_rows_are_read_only_on_every_write_path(books)


def test_postgres_effects_are_reversed_on_void_and_revise(pg_dsn):
    effects, books = effect_books(pg_dsn)
    effects.test_void_writes_mirror_rows_and_keeps_originals(books)
    effects.test_revise_reverses_the_original_and_the_copy_posts_its_own(books)


def test_postgres_effect_permissions(pg_dsn):
    effects, books = effect_books(pg_dsn)
    effects.test_roles_that_cannot_read_a_target_see_nothing_about_it(books)


def test_postgres_effects_check(pg_dsn):
    effects, books = effect_books(pg_dsn)
    effects.test_check_reports_changed_missing_extra_and_unbalanced_rows(books)


def test_postgres_effects_check_command(pg_dsn, tmp_path, monkeypatch, capsys):
    effects, books = effect_books(pg_dsn)
    effects.test_check_command_exit_codes(books, tmp_path, monkeypatch, capsys)


def effect_branches(pg_dsn):
    from tests import test_effects as effects
    from tests.test_rules_types import add_user, start
    app = start(PostgreSQLDatabase(pg_dsn, Registry(effects.Branch, effects.BranchEntry, effects.PlainEntry, effects.BranchOrder)))
    return effects, (app, add_user(app, 'editor'))


@pytest.mark.parametrize('name', ['test_rows_are_validated_without_any_role', 'test_links_to_missing_records_are_field_errors'])
def test_postgres_effect_rows_are_validated_without_any_role(pg_dsn, name):
    effects, branches = effect_branches(pg_dsn)
    getattr(effects, name)(branches)


def test_postgres_effect_failures_roll_back(pg_dsn, monkeypatch, caplog):
    effects, books = effect_books(pg_dsn)
    effects.test_a_failing_rows_method_is_a_field_error_and_an_invalid_check(books, monkeypatch, caplog)


def test_postgres_void_checks_the_mirror_balance(pg_dsn, monkeypatch):
    effects, books = effect_books(pg_dsn)
    effects.test_void_checks_the_balance_of_the_mirror_rows(books, monkeypatch)


def test_postgres_effects_check_reads_a_snapshot_and_uses_the_source_index(pg_dsn):
    import psycopg
    effects, books = effect_books(pg_dsn)
    app, c, account = books
    url = effects.invoice(c, account)
    effects.move(c, url, 'issued')
    with psycopg.connect(pg_dsn) as writer:
        writer.execute('UPDATE stockmove SET qty = 0')
        # An uncommitted writer neither blocks the check nor shows in its snapshot.
        assert effects.effects.check(app) == []
        writer.rollback()
    with app.database.transaction() as tx:
        tx.connection.execute('SET LOCAL enable_seqscan = off')
        plan = ' '.join(row[0] for row in tx.connection.execute(
            'EXPLAIN SELECT * FROM "ledgerentry" WHERE "source_model"=? AND "source_id" IN (?) ORDER BY id', ('invoice', 1)).fetchall())
    assert 'Index' in plan and 'source' in plan, plan


def test_postgres_reference_index_migrations(pg_dsn):
    from evlet import Boolean, CreateModel, Reference
    def entry_model(name, indexed):
        return type('Entry', (Model,), {'item': Text(default=''), name: Reference(index=indexed), 'reversal': Boolean(default=False)})
    old, new, renamed = entry_model('source', False), entry_model('source', True), entry_model('origin', True)
    first = (Migration('0001', CreateModel(old)),)
    PostgreSQLDatabase(pg_dsn, Registry(old), migrations=first).migrate(first)
    with pytest.raises(SchemaError):
        PostgreSQLDatabase(pg_dsn, Registry(new), migrations=first).initialize()
    second = first + (Migration('0002', AddIndex('entry', 'source')),)
    PostgreSQLDatabase(pg_dsn, Registry(new), migrations=second).migrate(second)
    PostgreSQLDatabase(pg_dsn, Registry(new), migrations=second).initialize()
    third = second + (Migration('0003', RenameField('entry', 'source', 'origin')),)
    PostgreSQLDatabase(pg_dsn, Registry(renamed), migrations=third).migrate(third)
    PostgreSQLDatabase(pg_dsn, Registry(renamed), migrations=third).initialize()


@pytest.mark.parametrize('check', ['check_a_caught_move_failure_leaves_no_rows_or_number', 'check_numbers_and_rows_on_one_move_and_on_revise',
                                   'check_measures_over_a_target_follow_row_scope', 'check_an_effect_source_is_not_a_relation'])
def test_postgres_effects_beside_numbers_measures_and_relations(pg_dsn, check):
    from tests import test_effects as effects
    getattr(effects, check)(effects.stacked_books(PostgreSQLDatabase(pg_dsn, Registry(*effects.STACKED))))


@pytest.mark.parametrize('check', ['check_engine', 'check_reading', 'check_user_entries', 'check_append_only_and_chain', 'check_rollback'])
def test_postgres_journal(pg_dsn, monkeypatch, check):
    from tests import test_journal as journal
    from tests.test_rules_types import start
    app = journal.seed(start(PostgreSQLDatabase(pg_dsn, Registry(*journal.MODELS))))
    # The table, its index and its triggers are created once and kept on a restart.
    app.initialize()
    getattr(journal, check)(*((app, monkeypatch) if check == 'check_rollback' else (app,)))


def test_postgres_journal_records_effects(pg_dsn):
    effects, books = effect_books(pg_dsn)
    effects.check_the_journal_records_postings_and_reversals(books)


def test_postgres_rolled_back_move_leaves_no_effects_entry(pg_dsn, monkeypatch):
    from tests import test_effects as effects
    effects.check_a_rolled_back_move_leaves_no_effects_entry(effects.stacked_books(PostgreSQLDatabase(pg_dsn, Registry(*effects.STACKED))),
                                                             monkeypatch)


@pytest.fixture
def settled(secured, tmp_path):
    secured.business.secret_key = tmp_path / 'secret.key'
    return secured


from tests.test_settings import (  # noqa: E402
    test_personal_pages_save_and_the_last_page_is_remembered as test_postgres_settings_personal_pages,
    test_app_settings_respect_editors_audit_and_the_config_version as test_postgres_app_settings,
    test_password_change_needs_the_current_password_and_signs_out_other_sessions as test_postgres_settings_password,
    test_photo_type_size_and_private_storage as test_postgres_settings_photo,
    test_email_change_goes_through_a_one_time_link as test_postgres_settings_email,
    test_two_step_enrollment_login_with_codes_and_backup_codes_once as test_postgres_two_step,
    test_a_role_that_requires_two_step_sets_it_up_at_sign_in_and_cannot_turn_it_off as test_postgres_two_step_required,
)


from tests.test_settings_security import (  # noqa: E402
    test_a_lost_key_is_never_replaced_and_sign_in_answers_503 as test_postgres_lost_key,
    test_required_roles_need_a_verified_session_for_controls as test_postgres_verified_sessions,
    test_sensitive_changes_ask_only_a_code_with_two_step_on_and_the_password_without as test_postgres_reauthentication,
    test_wrong_codes_are_limited_per_account_across_networks as test_postgres_code_limit,
    test_a_role_that_requires_two_step_uses_nothing_until_it_is_set_up as test_postgres_two_step_first,
    test_controls_changes_ask_for_a_code_at_most_every_ten_minutes as test_postgres_controls_verification,
    test_changing_your_own_password_asks_a_code_when_two_step_is_on as test_postgres_password_code,
    test_a_code_cannot_be_used_twice_by_requests_that_race as test_postgres_code_race,
)
from tests.test_revocation import (  # noqa: E402
    test_a_settings_password_change_can_also_revoke_api_keys as test_postgres_password_revokes_keys,
    test_controls_sign_out_everywhere_can_also_revoke_api_keys as test_postgres_sign_out_revokes_keys,
)


def test_postgres_multiselect_storage_and_filters(pg_dsn):
    from evlet import MultiSelect
    shift = type('Shift', (Model,), {'name': Text(required=True), 'days': MultiSelect(['mon', 'tue', 'wed'], default=['mon']),
                                     'hours': Integer(default=8, suffix='hours')})
    db = PostgreSQLDatabase(pg_dsn, Registry(shift))
    db.initialize()
    db.create(shift, name='Early', days=['wed', 'mon'])
    db.create(shift, name='Late', days=['tue'])
    assert [item.days for item in db.list(shift, filters={'days__contains': 'mon'})] == [('mon', 'wed')]
def test_postgres_connectors_secrets_and_api_keys(secured, monkeypatch):
    import base64, hashlib, json
    monkeypatch.setenv('EVLET_SECRET_KEY', base64.urlsafe_b64encode(os.urandom(32)).decode())
    admin = client(secured)
    body = {'settings': {'address': 'noreply@example.test', 'host': '127.0.0.1', 'port': 2525, 'security': 'none'}, 'secrets': {'password': 'pg-secret-value'}}
    status, saved, _ = admin('PUT', '/api/_controls/connectors/smtp:notifications', body)
    assert status == 200 and saved['default'] and saved['secrets'] == {'password': {'saved': True}}
    assert admin('PUT', '/api/_controls/connectors/smtp:accounts', body)[1]['default'] is False
    assert admin('POST', '/api/_controls/connectors/smtp:accounts/default')[1]['default']
    assert secured.business.connectors.values('smtp:notifications')['password'] == 'pg-secret-value'
    listing = admin('GET', '/api/_controls/connectors')[1]
    assert 'pg-secret-value' not in json.dumps(listing) and len(listing['items']) == 2
    assert admin('DELETE', '/api/_controls/connectors/smtp:notifications')[0] == 204
    status, created, _ = admin('POST', '/api/_controls/keys', {'name': 'Script', 'user': 'editor', 'scope': {'access': 'write', 'models': ['Project']}})
    assert status == 201
    token = created['token']
    bearer = {b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''}
    assert http(secured, 'POST', '/api/project', {'name': 'By key'}, headers=bearer)[0] == 201
    assert http(secured, 'GET', '/api/customer', headers=bearer)[0] in (403, 404)
    with secured.database._connection() as connection:
        assert connection.execute('SELECT hash FROM evlet_api_keys').fetchone()[0] == hashlib.sha256(token.encode()).hexdigest()
        audit = json.dumps([list(row) for row in connection.execute('SELECT area, target, detail, after FROM evlet_audit ORDER BY id')])
    assert admin('DELETE', f"/api/_controls/keys/{created['key']['id']}")[1] == {'items': []}
    assert http(secured, 'GET', '/api/project', headers=bearer)[0] == 401
    assert 'pg-secret-value' not in audit and token.split('_', 2)[2] not in audit and 'Created for editor' in audit


def test_postgres_remote_mcp_oauth_and_assistant_tables(secured):
    from tests.test_assistants import connect, form, rpc, settings, tool
    client_id, tokens, person = connect(secured, 'editor')
    assert tool(secured, tokens['access_token'], 'discover_models', {})['structuredContent']['total'] >= 1
    settings(secured, allow_writes=True)
    assert tool(secured, tokens['access_token'], 'create_record', {'model': 'project', 'values': {'name': 'From an assistant'}})['structuredContent']['name'] == 'From an assistant'
    status, fresh, _ = form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': client_id})
    assert status == 200
    page = person('GET', '/api/_settings/me/ai')[1]
    assert page['clients'][0]['calls'] == 2 and [item['tool'] for item in page['requests']] == ['create_record', 'discover_models']
    overview = client(secured)('GET', '/api/_controls/mcp')[1]
    assert overview['totals'] == {'reads': 1, 'writes': 1, 'denied': 0} and overview['clients'][0]['username'] == 'editor'
    assert form(secured, '/_/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': client_id})[1]['error'] == 'invalid_grant'
    assert rpc(secured, fresh['access_token'], 'ping')[0] == 401


def test_dev_signin_link_on_postgresql(pg_dsn):
    from evlet import DevSignIn
    from evlet.devlink import issue
    from tests.test_devlink import call, session_cookie
    db = PostgreSQLDatabase(pg_dsn, registry)
    app = build_app(db, LocalAuth(db, origins=['http://127.0.0.1:8000'], secure_cookies=False, allow_setup=True))
    app.initialize()
    token, username = issue(db, create_admin=True)
    assert username == 'demo-admin'
    wrapped = DevSignIn(app)
    status, headers, _ = call(wrapped, 'GET', f'/_dev/signin?token={token}')
    assert status == 302
    status, _, body = call(wrapped, 'GET', '/api/auth/session', cookie=session_cookie(headers))
    assert b'demo-admin' in body
    assert call(wrapped, 'GET', f'/_dev/signin?token={token}')[0] == 403


def test_framework_ledger_and_steps_match_sqlite(pg_dsn, monkeypatch):
    from evlet import Application, framework
    seen = []

    def move(connection, postgres):
        seen.append(postgres)
        connection.execute('CREATE TABLE evlet_moved_calls (id INTEGER PRIMARY KEY, tool TEXT NOT NULL)')
        connection.execute('INSERT INTO evlet_moved_calls (id, tool) SELECT id, username FROM evlet_users')
        connection.execute('DROP TABLE evlet_legacy_calls')

    db = PostgreSQLDatabase(pg_dsn, registry)
    app = build_app(db, LocalAuth(db, origins=['http://127.0.0.1:8000'], secure_cookies=False))
    app.initialize()
    with db.transaction() as tx:
        assert [tuple(row) for row in tx.connection.execute('SELECT position, name, checksum FROM evlet_framework_migrations')] == \
            [(1, '0001_startup_tables', framework.STEPS[0].checksum())]
        tx.connection.execute('CREATE TABLE evlet_legacy_calls (id INTEGER PRIMARY KEY)')
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('owner','x','admin')")
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, framework.FrameworkStep('0002_move', 'Move rows, drop a table.', move)))
    app.managed_framework = True
    with pytest.raises(SchemaError, match='framework migrations are pending'):
        app.initialize()
    app.managed_framework = False
    assert framework.upgrade(db) == ['0002_move'] and seen == [True]
    with db.transaction() as tx:
        assert [row[0] for row in tx.connection.execute('SELECT tool FROM evlet_moved_calls')] == ['owner']
        assert tx.connection.execute("SELECT to_regclass('evlet_legacy_calls')").fetchone()[0] is None
    # A failing step rolls back its own changes and leaves the ledger as it was.
    def fail(connection, postgres):
        connection.execute('DROP TABLE evlet_moved_calls')
        raise RuntimeError('simulated framework step failure')
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, framework.FrameworkStep('0003_fail', 'Fails.', fail)))
    with pytest.raises(RuntimeError):
        framework.upgrade(db)
    with db.transaction() as tx:
        assert tx.connection.execute('SELECT count(*) FROM evlet_framework_migrations').fetchone()[0] == 2
        assert tx.connection.execute("SELECT to_regclass('evlet_moved_calls')").fetchone()[0] is not None
def test_postgres_plan_and_add_table_with_confirmed_documents(pg_dsn):
    from tests.test_migration_plan import check_plan_and_add_table

    def make(registry, migrations, migrate=False, plan=False):
        database = PostgreSQLDatabase(pg_dsn, registry, migrations=migrations)
        if plan:
            return database.plan(migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    check_plan_and_add_table(make)
