"""Meta.indexes on normal models, `id` in ledger indexes, tx.reserve_ids and the append-only refusal error,
on real SQLite and PostgreSQL."""

from datetime import date
import sqlite3

import pytest

from evlet import (AddIndex, Application, Child, ConflictError, CreateModel, Database, Date, Integer, Link, Migration, Model,
                   Registry, Rule, SchemaError, Select, Table, Text, ValidationError)
from evlet.auth import LocalAuth
from evlet.postgres import PostgreSQLDatabase
from test_business import PASSWORD, client, http
from test_postgres import pg_dsn  # noqa: F401 - fixture

FULL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class Station(Model):
    permissions = FULL
    code = Text(max_length=20, required=True)


class Sample(Model):
    """One sample per station, day and kind."""
    permissions = FULL
    station = Link(Station, required=True)
    on_date = Date(required=True)
    kind = Select(('spot', 'average'), default='spot')
    value = Integer(default=0)

    class Meta:
        indexes = [(('station', 'on_date', 'kind'), {'unique': True}), ('on_date',)]


class Part(Child):
    code = Text(max_length=20, required=True)

    class Meta:
        # A code once per kit: the row's owner field is evlet_owner.
        indexes = [(('evlet_owner', 'code'), {'unique': True})]


class Kit(Model):
    permissions = FULL
    label = Text(default='')
    parts = Table(Part)


class Step(Model):
    """A ledger whose rows link to each other in one batch."""
    serial = Text(max_length=20, required=True)
    previous = Link('step')
    root = Link('step')

    class Meta:
        ledger = True
        append_only = True
        indexes = [('serial', 'id')]


MODELS = (Station, Sample, Kit, Step)


def build(database):
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app, client(app)


@pytest.fixture(params=['sqlite', 'postgresql'])
def app(request, tmp_path):
    registry = Registry(*MODELS)
    if request.param == 'sqlite':
        return build(Database(tmp_path / 'indexes.sqlite3', registry))
    return build(PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry))


def postgres(database):
    return isinstance(database, PostgreSQLDatabase)


def station(database, code='north'):
    with database.transaction() as tx:
        return tx.create(Station, code=code).id


# Declaration

def test_meta_indexes_normalize_on_a_normal_model():
    assert Sample.__indexes__ == ((('station', 'on_date', 'kind'), True), (('on_date',), False))
    plain = type('Plain', (Model,), {'amount': Integer()})
    assert plain.__indexes__ == ()


@pytest.mark.parametrize('indexes, message', [
    ([('missing',)], 'names missing, which is not a field'),
    ([('amount', 'amount')], 'names a field twice'),
    ([('amount',), ('amount',)], 'lists .* twice'),
    ([(('amount',), {'unique': 1})], 'options are'),
    ([(('amount',), {'sparse': True})], 'options are'),
    ('amount', 'is a list'),
    ([()], 'names one or more fields'),
])
def test_meta_indexes_are_checked_on_a_normal_model(indexes, message):
    with pytest.raises(ValueError, match=message):
        type('Plain', (Model,), {'amount': Integer(), 'Meta': type('Meta', (), {'indexes': indexes})})


def test_meta_indexes_refuse_fields_without_one_stored_column():
    from evlet import Reference, RichText
    for field in (Reference(), RichText()):
        with pytest.raises(ValueError, match='has no single stored column to index'):
            type('Plain', (Model,), {'other': field, 'amount': Integer(),
                                     'Meta': type('Meta', (), {'indexes': [('amount', 'other')]})})


def test_a_child_row_names_its_owner_field_once_a_table_registers_it():
    registry = Registry(Kit)
    row = next(item.model for item in registry.children if item.name == 'parts')
    assert row.__indexes__ == ((('evlet_owner', 'code'), True),)
    wrong = type('Wrong', (Child,), {'code': Text(), 'Meta': type('Meta', (), {'indexes': [('owner', 'code')]})})
    with pytest.raises(ValueError, match='names owner, which is not a field'):
        Registry(type('Box', (Model,), {'parts': Table(wrong)}))


def test_the_saved_schema_records_normal_model_indexes_and_omits_none():
    entries = {entry['name']: entry for entry in Registry(*MODELS).schema()['models']}
    assert entries['sample']['indexes'] == [{'fields': ['station', 'on_date', 'kind'], 'unique': True},
                                            {'fields': ['on_date'], 'unique': False}]
    assert entries['kit_parts']['indexes'] == [{'fields': ['evlet_owner', 'code'], 'unique': True}]
    assert 'indexes' not in entries['station'] and 'indexes' not in entries['kit']
    assert entries['step']['ledger']['indexes'] == [{'fields': ['serial', 'id'], 'unique': False}]


# Composite unique indexes on normal models

def test_a_composite_unique_index_is_enforced_with_a_validation_error(app):
    app, admin = app
    database = app.database
    north, south = station(database), station(database, 'south')
    with database.transaction() as tx:
        tx.create(Sample, station=north, on_date=date(2026, 11, 1), kind='spot')
        # Other values in any one field are fine.
        tx.create(Sample, station=north, on_date=date(2026, 11, 1), kind='average')
        tx.create(Sample, station=south, on_date=date(2026, 11, 1), kind='spot')
        other = tx.create(Sample, station=north, on_date=date(2026, 11, 2), kind='spot').id
    with pytest.raises(ValidationError) as error:
        with database.transaction() as tx:
            tx.create(Sample, station=north, on_date=date(2026, 11, 1), kind='spot', value=5)
    message = 'Another record already has the same station, on date and kind'
    assert error.value.errors == {'station': message, 'on_date': message, 'kind': message}
    with pytest.raises(ValidationError, match='same station, on date and kind'):
        with database.transaction() as tx:
            tx.update(Sample, other, on_date=date(2026, 11, 1))
    assert database.count(Sample) == 4
    # HTTP: the usual 422 with field errors.
    body = {'station': str(north), 'on_date': '2026-11-01', 'kind': 'spot'}
    status, payload, _ = admin('POST', '/api/sample', body)
    assert status == 422, payload
    assert payload == {'error': 'Validation failed', 'fields': {'station': message, 'on_date': message, 'kind': message}}
    status, payload, _ = admin('POST', '/api/sample', {**body, 'on_date': '2026-11-03'})
    assert status == 201, payload
    # A plain unique field keeps its conflict error.
    with pytest.raises(ConflictError):
        with database.transaction() as tx:
            tx.connection.execute('INSERT INTO station (id, code) VALUES (?, ?)', (north, 'x'))


def test_a_child_code_is_unique_per_parent_record(app):
    app, admin = app
    status, first, _ = admin('POST', '/api/kit', {'label': 'a', 'children': {'parts': [{'code': 'P1'}, {'code': 'P2'}]}})
    assert status == 201, first
    # The same code in another kit is fine.
    status, payload, _ = admin('POST', '/api/kit', {'label': 'b', 'children': {'parts': [{'code': 'P1'}]}})
    assert status == 201, payload
    status, payload, _ = admin('POST', '/api/kit', {'label': 'c', 'children': {'parts': [{'code': 'P3'}, {'code': 'P3'}]}})
    assert status == 422, payload
    assert payload == {'error': 'Validation failed', 'fields': {'code': 'Another row of this record already has the same code'}}
    assert app.database.count(Kit) == 2


def test_the_schema_check_needs_the_composite_unique_index(tmp_path):
    path = tmp_path / 'checked.sqlite3'
    Database(path, Registry(*MODELS)).initialize()
    with sqlite3.connect(path) as connection:
        [name] = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sample' AND sql LIKE 'CREATE UNIQUE%' AND sql LIKE '%kind%'")]
        connection.execute(f'DROP INDEX "{name}"')
    with pytest.raises(SchemaError, match='sample: unique constraints differ from models'):
        Database(path, Registry(*MODELS)).initialize()


def test_the_postgres_schema_check_needs_the_composite_unique_index(pg_dsn):
    PostgreSQLDatabase(pg_dsn, Registry(*MODELS)).initialize()
    database = PostgreSQLDatabase(pg_dsn, Registry(*MODELS))
    with database.transaction() as tx:
        [name] = [row[0] for row in tx.connection.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='sample' AND indexdef LIKE 'CREATE UNIQUE%' AND indexdef LIKE '%kind%'")]
        tx.connection.execute(f'DROP INDEX "{name}"')
    with pytest.raises(SchemaError, match='sample: unique constraints differ from models'):
        PostgreSQLDatabase(pg_dsn, Registry(*MODELS)).initialize()


def test_postgres_refuses_long_indexed_text_in_meta_indexes(pg_dsn):
    wide = type('Wide', (Model,), {'note': Text(max_length=600), 'Meta': type('Meta', (), {'indexes': [('note',)]})})
    with pytest.raises(SchemaError, match='wide.note: PostgreSQL indexed text requires max_length <= 500'):
        PostgreSQLDatabase(pg_dsn, Registry(wide))


def plain(name, indexes=()):
    return type(name, (Model,), {'storage_name': name.lower(), 'unit': Text(max_length=10, required=True),
                                 'on_date': Date(required=True), 'Meta': type('Meta', (), {'indexes': list(indexes)})})


@pytest.mark.parametrize('engine', ['sqlite', 'postgresql'])
def test_add_index_adds_a_composite_unique_index_to_a_normal_model(engine, tmp_path, request):
    def open_(registry, **options):
        if engine == 'sqlite':
            return Database(tmp_path / 'migrated.sqlite3', registry, **options)
        return PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry, **options)
    first = plain('Entry')
    history = [Migration('0001_entry', CreateModel(first))]
    open_(Registry(first)).migrate(history)
    with open_(Registry(first)).transaction() as tx:
        tx.create(first, unit='a', on_date=date(2026, 11, 1))
    second = plain('Entry', [(('unit', 'on_date'), {'unique': True})])
    # The models alone are not a migration, and the plan suggests the operation.
    with pytest.raises(SchemaError, match=r"Meta.indexes differ: \[\] after the migrations"):
        open_(Registry(second)).migrate([*history, Migration('0002_noop', AddIndex('entry', 'unit'))])
    assert "AddIndex('entry', ('unit', 'on_date'), unique=True)" in open_(Registry(second)).plan(history).text()
    history.append(Migration('0002_unique', AddIndex('entry', ('unit', 'on_date'), unique=True)))
    database = open_(Registry(second), migrations=history)
    assert database.migrate(history) == ['0002_unique']
    database.initialize()
    with pytest.raises(ValidationError, match='same unit and on date'):
        with database.transaction() as tx:
            tx.create(second, unit='a', on_date=date(2026, 11, 1))
    with database.transaction() as tx:
        tx.create(second, unit='a', on_date=date(2026, 11, 2))
    # A second AddIndex for the same entry is refused.
    with pytest.raises(SchemaError, match='already exists'):
        open_(Registry(second)).migrate([*history, Migration('0003', AddIndex('entry', ('unit', 'on_date')))])


def test_a_frozen_class_keeps_normal_model_indexes():
    from evlet.migrations import suggest
    text = suggest(Registry(Station, Sample), {'version': 1, 'models': [
        entry for entry in Registry(Station).schema()['models'] if entry['name'] == 'station']})
    assert 'class Sample(Model):' in text
    assert "        indexes = [(('station', 'on_date', 'kind'), {\"unique\": True}), ('on_date',)]" in text


def test_create_model_keeps_its_checksum_without_indexes():
    """Migrations written before normal models had Meta.indexes keep their checksums."""
    signature = CreateModel(Station).signature()
    assert 'indexes' not in signature and 'ledger' not in signature


# Ledger indexes naming id

def test_a_ledger_index_may_name_id(app):
    app, _ = app
    database = app.database
    with database.transaction() as tx:
        tx.insert_many(Step, [{'serial': 'A'}, {'serial': 'B'}, {'serial': 'A'}])
        if postgres(database):
            definitions = [row[0] for row in tx.connection.execute("SELECT indexdef FROM pg_indexes WHERE tablename='step'")]
            assert any('(serial, id)' in item for item in definitions), definitions
        else:
            definitions = [row[0] for row in tx.connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='step'")]
            assert any('("serial", "id")' in (item or '') for item in definitions), definitions
        latest = tx.list(Step, filters={'serial': 'A'}, order_by=('-id',), limit=1)[0]
    assert latest.id == 3
    # The schema check accepts it.
    type(database)(*((database.path,) if not postgres(database) else (database._dsn,)), Registry(*MODELS)).initialize()


def test_add_index_on_a_ledger_may_name_id(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    def step(indexes):
        return type('Step', (Model,), {'storage_name': 'step', 'serial': Text(required=True),
                                       'Meta': type('Meta', (), {'ledger': True, 'indexes': indexes})})
    history = [Migration('0001', CreateModel(step([])))]
    Database(path, Registry(step([]))).migrate(history)
    history.append(Migration('0002', AddIndex('step', ('serial', 'id'))))
    Database(path, Registry(step([('serial', 'id')]))).migrate(history)
    Database(path, Registry(step([('serial', 'id')])), migrations=history).initialize()


# Id reservation

def test_reserved_ids_let_rows_of_one_batch_link_to_each_other(app):
    app, _ = app
    database = app.database
    with database.transaction() as tx:
        before = tx.insert_many(Step, [{'serial': 'X'}])[0]
        first, second, third = tx.reserve_ids(Step, 3)
        assert before < first < second < third
        # A row may point at its own id, and at rows earlier in the batch.
        ids = tx.insert_many(Step, [
            {'id': first, 'serial': 'S', 'root': first},
            {'id': second, 'serial': 'S', 'previous': first, 'root': first},
            {'serial': 'T'},
            {'id': third, 'serial': 'S', 'previous': second, 'root': first},
        ])
        assert ids[0] == first and ids[1] == second and ids[3] == third and ids[2] > third
        assert tx.get(Step, third).previous == second and tx.get(Step, first).root == first
        # Rows inserted later without an id never take a reserved one.
        after = tx.insert_many(Step, [{'serial': 'U'}, {'serial': 'V'}])
        assert min(after) > ids[2]
        # tx.create takes a reserved id too.
        [own] = tx.reserve_ids(Step, 1)
        assert tx.create(Step, id=own, serial='W', root=own).id == own
    with database.transaction() as tx:
        assert tx.insert_many(Step, [{'serial': 'Y'}])[0] > own
    assert database.count(Step) == 9


def test_insert_many_refuses_ids_that_were_not_reserved(app):
    app, _ = app
    database = app.database
    with database.transaction() as tx:
        top = tx.insert_many(Step, [{'serial': 'A'}])[0]
    for rows in ([{'id': top + 50, 'serial': 'B'}], [{'id': top, 'serial': 'B'}], [{'id': 'x', 'serial': 'B'}]):
        with pytest.raises(ValidationError) as error:
            with database.transaction() as tx:
                tx.insert_many(Step, rows)
        assert 'id' in error.value.errors
    # A reserved id is used once, and only in the transaction that reserved it.
    with pytest.raises(ValidationError, match='used twice'):
        with database.transaction() as tx:
            [one] = tx.reserve_ids(Step, 1)
            tx.insert_many(Step, [{'id': one, 'serial': 'B'}, {'id': one, 'serial': 'C'}])
    with database.transaction() as tx:
        [one] = tx.reserve_ids(Step, 1)
        tx.insert_many(Step, [{'id': one, 'serial': 'B'}])
        with pytest.raises(ValidationError, match='not reserved'):
            tx.insert_many(Step, [{'id': one, 'serial': 'C'}])
    with pytest.raises(ValidationError, match='not reserved'):
        with database.transaction() as tx:
            tx.insert_many(Step, [{'id': one + 1, 'serial': 'C'}])


def test_reserve_ids_checks_its_arguments(app):
    app, _ = app
    with app.database.transaction() as tx:
        for count in (0, -1, 1.5, True, 100_001):
            with pytest.raises(ValidationError, match='count'):
                tx.reserve_ids(Step, count)
        with pytest.raises(ValueError, match='reserve_ids writes ledger models'):
            tx.reserve_ids(Station, 1)


def test_sqlite_reserve_ids_on_rollback_and_outside_a_transaction(tmp_path):
    database = Database(tmp_path / 'reserve.sqlite3', Registry(*MODELS))
    database.initialize()
    with pytest.raises(RuntimeError):
        with database.transaction() as tx:
            reserved = tx.reserve_ids(Step, 5)
            raise RuntimeError('stop')
    # A rollback hands the ids back on SQLite.
    with database.transaction() as tx:
        assert tx.reserve_ids(Step, 5) == reserved == [1, 2, 3, 4, 5]
    from evlet.database import Session
    with sqlite3.connect(database.path) as connection:
        connection.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match='works inside database.transaction'):
            Session(connection, database.registry).reserve_ids(Step, 1)


def test_postgres_reserve_ids_leave_a_gap_on_rollback_and_need_a_write_transaction(pg_dsn):
    database = PostgreSQLDatabase(pg_dsn, Registry(*MODELS))
    database.initialize()
    with pytest.raises(RuntimeError):
        with database.transaction() as tx:
            reserved = tx.reserve_ids(Step, 5)
            raise RuntimeError('stop')
    with database.transaction() as tx:
        assert tx.reserve_ids(Step, 1)[0] > max(reserved)
        assert tx.insert_many(Step, [{'serial': 'A'}])[0] > max(reserved) + 1
    from evlet.database import Session
    with database._connection() as connection:
        with pytest.raises(ValueError, match='works inside database.transaction'):
            Session(connection, database.registry).reserve_ids(Step, 1)
        connection.execute('BEGIN')
        with pytest.raises(ValueError, match='works inside database.transaction'):
            Session(connection, database.registry).reserve_ids(Step, 1)
        connection.rollback()


# The append-only refusal

def test_raw_updates_and_deletes_of_an_append_only_table_raise_conflict_error(app):
    app, _ = app
    database = app.database
    with database.transaction() as tx:
        tx.insert_many(Step, [{'serial': 'A'}])
    for sql in ('UPDATE step SET serial = ?', 'DELETE FROM step WHERE serial = ?'):
        with pytest.raises(ConflictError, match='^step is append only: its rows are never changed or deleted$'):
            with database.transaction() as tx:
                tx.connection.execute(sql, ('A',) if 'DELETE' in sql else ('B',))
    assert [row.serial for row in database.list(Step)] == ['A']


def test_postgres_triggers_from_before_this_version_raise_conflict_error_too(pg_dsn):
    database = PostgreSQLDatabase(pg_dsn, Registry(*MODELS))
    database.initialize()
    with database.transaction() as tx:
        tx.insert_many(Step, [{'serial': 'A'}])
        # The function as earlier versions created it: a plain RAISE EXCEPTION.
        tx.connection.execute("CREATE OR REPLACE FUNCTION evlet_ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS "
                              "$$ BEGIN RAISE EXCEPTION '% is append only', TG_TABLE_NAME; END $$")
    with pytest.raises(ConflictError, match='^step is append only'):
        with database.transaction() as tx:
            tx.connection.execute('UPDATE step SET serial = ?', ('B',))
    # Any other raised database error stays a generic failure.
    with pytest.raises(sqlite3.OperationalError, match='PostgreSQL operation failed'):
        with database.transaction() as tx:
            tx.connection.execute("DO $$ BEGIN RAISE EXCEPTION 'something else'; END $$")


# Aggregates in a transaction

def test_tx_aggregate_matches_database_aggregate_and_sees_its_own_writes(app):
    from evlet import Count, Month, Sum
    app, _ = app
    database = app.database
    north = station(database)
    with database.transaction() as tx:
        tx.create(Sample, station=north, on_date=date(2026, 11, 1), value=3)
        tx.create(Sample, station=north, on_date=date(2026, 12, 1), value=4)
        tx.insert_many(Step, [{'serial': 'A'}, {'serial': 'A'}, {'serial': 'B'}])
        # Uncommitted rows count inside the transaction.
        inside = tx.aggregate(Sample, measures={'total': Sum('value'), 'rows': Count()}, group_by=(Month('on_date'),),
                              filters={'station': north})
        steps = tx.aggregate(Step, measures={'rows': Count()}, group_by='serial')
    assert inside == [{'on_date__month': '2026-11', 'total': 3, 'rows': 1}, {'on_date__month': '2026-12', 'total': 4, 'rows': 1}]
    assert steps == [{'serial': 'A', 'rows': 2}, {'serial': 'B', 'rows': 1}]
    assert inside == database.aggregate(Sample, measures={'total': Sum('value'), 'rows': Count()}, group_by=(Month('on_date'),),
                                        filters={'station': north})
    # The same limits: at most four group fields.
    with database.transaction() as tx:
        with pytest.raises(ValidationError, match='at most 4 group fields'):
            tx.aggregate(Sample, measures={'rows': Count()}, group_by=('station', 'on_date', 'kind', 'value', 'id'))
