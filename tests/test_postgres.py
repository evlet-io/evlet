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
