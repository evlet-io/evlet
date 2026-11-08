"""Ledger models (Meta.ledger = True) and named transaction locks (tx.lock), on real SQLite and PostgreSQL."""

from datetime import date, datetime, timezone
from decimal import Decimal as D
import sqlite3
import threading

import pytest

from evlet import (AddField, AddIndex, Application, Boolean, Column, ConflictError, CreateModel, Currency, Database, Date,
                   DateTime, Decimal, Integer, Journal, Link, LockTimeout, LongText, Migration, Model, MultiSelect, Percent,
                   Reference, Registry, RenameField, Report, RetireField, RichText, Rule, SchemaError, Select, SetAppendOnly,
                   State, Sum, Table, Tags, Text, ValidationError, action, AlterField, Count, Child, Email, Duration)
from evlet.auth import LocalAuth, User
from evlet.database import Session
from evlet.ledger import describe, lock_hash, table as ledger_table
from evlet.periods import set_lock
from evlet.postgres import PostgreSQLDatabase
from evlet.sequences import Sequence
from test_business import PASSWORD, client, http
from test_postgres import pg_dsn  # noqa: F401 - fixture
from test_rules_types import ALL

FULL = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'))}
ADMIN = User(1, 'admin', 'admin')


class Member(Model):
    permissions = FULL
    full_name = Text(required=True)
    team = Text(default='north')
    # A normal model may point at a ledger row: callers see the plain integer id, read only.
    last_move = Link('pointmove')


class PointMove(Model):
    """An append-only movement ledger."""
    member = Link(Member, required=True)
    on_date = Date(required=True)
    reason = Text(default='')
    points = Decimal(scale=2, required=True)
    team = Text(default='north')

    class Meta:
        ledger = True
        append_only = True
        journal = Journal(tamper_evident=True)
        timestamps = 'created'
        indexes = [('member', 'on_date')]
        period = 'on_date'

    permissions = {**FULL, 'viewer': Rule(('read',), scope=lambda user: {'team': 'north'})}


class PointBalance(Model):
    """A mutable balance cache the application keeps up to date."""
    member = Link(Member, required=True)
    points = Decimal(scale=2, required=True)
    last_move = Link(PointMove)

    class Meta:
        ledger = True
        timestamps = 'modified'
        indexes = [(('member',), {'unique': True})]


class Reading(Model):
    """Every allowed type, no options."""
    label = Text(default='')
    count = Integer(default=0)
    amount = Currency(currency='AED', default=0)
    rate = Percent(default=0)
    done = Boolean(default=False)
    at = DateTime()
    kind = Select(('a', 'b'), default='a')

    class Meta:
        ledger = True


MODELS = (Member, PointMove, PointBalance, Reading)


class Points(Report):
    """A report over a ledger model: the person's row scope on PointMove applies to it."""
    title = 'Points'
    roles = ('admin', 'viewer')
    columns = (Column('team'), Column('points', type='decimal', total=True, unit='points'))

    def run(self):
        groups = self.context.aggregate(PointMove, measures={'points': Sum('points')}, group_by=('team',))
        rows = [{'team': row['team'], 'points': row['points']} for row in groups]
        return {'rows': rows, 'total': len(rows)}


def build(database):
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      reports=(Points,))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    assert admin('POST', '/api/auth/users', {'username': 'viewer', 'password': PASSWORD, 'role': 'viewer'})[0] == 201
    return app, admin


@pytest.fixture(params=['sqlite', 'postgresql'])
def points(request, tmp_path):
    registry = Registry(*MODELS)
    if request.param == 'sqlite':
        database = Database(tmp_path / 'points.sqlite3', registry)
    else:
        database = PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry)
    return build(database)


def postgres(app):
    return isinstance(app.database, PostgreSQLDatabase)


def member(app, name='Aisha', team='north'):
    with app.database.transaction() as tx:
        return tx.create(Member, full_name=name, team=team).id


def columns(app, table):
    with app.database.transaction() as tx:
        if postgres(app):
            return [row[0] for row in tx.connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=? ORDER BY ordinal_position", (table,))]
        return [row[1] for row in tx.connection.execute(f'PRAGMA table_info({table})')]


def move(**values):
    return {'on_date': date(2026, 11, 1), 'points': '1.00', **values}


# Class creation

def ledger_class(fields=None, **meta):
    return type('Lean', (Model,), {'Meta': type('Meta', (), {'ledger': True, **meta}), **(fields or {'amount': Integer()})})


def test_a_ledger_model_takes_stored_fields_and_ledger_options():
    assert PointMove.__ledger__ and PointMove.__append_only__ and PointMove.__timestamps__ == 'created'
    assert PointMove.__indexes__ == ((('member', 'on_date'), False),)
    assert PointBalance.__indexes__ == ((('member',), True),)
    assert list(PointMove.__app_fields__) == ['member', 'on_date', 'reason', 'points', 'team', 'created_at']
    assert 'name' not in Reading.__app_fields__ and 'title' not in Reading.__app_fields__
    assert not Member.__ledger__ and 'name' in Member.__app_fields__
    # Field checks still apply: required, scale, choices.
    with pytest.raises(ValidationError) as error:
        PointMove(member=1, on_date='2026-11-01', points='1.234')
    assert 'points' in error.value.errors
    with pytest.raises(ValidationError):
        Reading(kind='c')


@pytest.mark.parametrize('field, message', [
    (LongText(), 'stores Text, Integer, Decimal, Currency, Percent, Boolean, Date, DateTime, Select, Link fields only, not LongText'),
    (RichText(), 'not RichText'),
    (State(('a', 'b')), 'not State'),
    (Sequence('{n:05}'), 'not Sequence'),
    (Tags(), 'not Tags'),
    (MultiSelect(('a',)), 'not MultiSelect'),
    (Reference(), 'not Reference'),
    (Email(), 'not Email'),
    (Duration(), 'not Duration'),
    (Integer(compute=lambda record: 1), 'computed fields'),
    (Integer(stored=False), 'virtual fields'),
    (Text(help_text='Shown under the input'), 'screen text'),
    (Text(visible_if=lambda record: True), 'screen rules'),
    (Text(hidden=True), 'screen options'),
    (Text(read_only=True), 'screen options'),
    (Select(('a',), colors={'a': 'blue'}), 'Select colors are a screen option'),
])
def test_fields_and_options_a_ledger_model_refuses(field, message):
    with pytest.raises(ValueError, match=message):
        ledger_class({'value': field})


@pytest.mark.parametrize('namespace, meta, message', [
    ({'lines': Table(type('Line', (Child,), {'qty': Integer()}))}, {}, 'no child tables'),
    ({'run': action(lambda self: None)}, {}, 'no actions, moves, lifecycle handlers or reactions'),
    ({'confirmable': True}, {}, 'no lifecycle; confirmable'),
    ({}, {'api': 'read'}, 'no public surface; Meta.api is None'),
    ({}, {'api': 'full'}, 'no public surface'),
    ({}, {'layout': (('Details', ('amount',)),)}, 'Meta.layout is not available'),
    ({}, {'status': 'amount'}, 'Meta.status is not available'),
    ({}, {'revisable': True}, 'Meta.revisable is not available'),
    ({}, {'timestamps': 'always'}, 'Meta.timestamps is None, "created", "modified" or "both"'),
    ({}, {'append_only': 'yes'}, 'Meta.append_only is True or False'),
    ({}, {'indexes': [('missing',)]}, 'Meta.indexes names missing'),
    ({}, {'indexes': [(('amount',), {'sparse': True})]}, 'Meta.indexes options'),
    ({'created_at': DateTime()}, {}, 'created_at is kept for Meta.timestamps'),
])
def test_declarations_a_ledger_model_refuses(namespace, meta, message):
    with pytest.raises(ValueError, match=message):
        ledger_class({'amount': Integer(), **namespace}, **meta)


@pytest.mark.parametrize('option', ['append_only', 'timestamps', 'indexes'])
def test_ledger_options_need_meta_ledger(option):
    value = {'append_only': True, 'timestamps': 'created', 'indexes': [('amount',)]}[option]
    with pytest.raises(ValueError, match=f'Meta.{option} is for ledger models; set Meta.ledger = True as well'):
        type('Plain', (Model,), {'Meta': type('Meta', (), {option: value}), 'amount': Integer()})


def test_api_none_is_accepted_and_ledger_is_a_boolean():
    assert ledger_class(api=None).__ledger__
    with pytest.raises(ValueError, match='Meta.ledger is True or False'):
        type('Odd', (Model,), {'Meta': type('Meta', (), {'ledger': 1}), 'amount': Integer()})


# Table shape and basic access

def test_tables_hold_the_id_and_declared_columns_only(points):
    app, _ = points
    assert columns(app, 'pointmove') == ['id', 'member', 'on_date', 'reason', 'points', 'team', 'created_at']
    assert columns(app, 'pointbalance') == ['id', 'member', 'points', 'last_move', 'modified_at']
    assert columns(app, 'reading') == ['id', 'label', 'count', 'amount', 'rate', 'done', 'at', 'kind']
    assert {'name', 'title', 'evlet_created_at'} <= set(columns(app, 'member'))


def test_insert_get_select_update_and_delete(points):
    app, _ = points
    aisha, omar = member(app), member(app, 'Omar')
    with app.database.transaction() as tx:
        first = tx.create(PointMove, **move(member=aisha, points='10.00', reason='joined'))
        ids = tx.insert_many(PointMove, [move(member=aisha, points='-2.50'), move(member=omar, points='4.00'), move(member=aisha, points='0.25')])
        assert type(first.id) is int and ids == [first.id + 1, first.id + 2, first.id + 3]
        assert tx.insert_many(PointMove, []) == []
        assert tx.get(PointMove, ids[1]).points == D('4.00') and tx.get(PointMove, ids[1]).member == omar
        assert first.to_dict()['id'] == first.id
        assert [row.points for row in tx.list(PointMove, filters={'member': aisha}, order_by=('id',))] == [D('10.00'), D('-2.50'), D('0.25')]
        assert tx.count(PointMove, filters={'points__lt': '1'}) == 2
        balance = tx.create(PointBalance, member=aisha, points='7.75', last_move=ids[2])
        changed = tx.update(PointBalance, balance.id, points='8.00')
        assert changed.points == D('8.00') and changed.last_move == ids[2]
        tx.delete(PointBalance, balance.id)
        assert tx.count(PointBalance) == 0
    # Ids keep increasing in insert order and are never reused.
    with app.database.transaction() as tx:
        again = tx.create(PointBalance, member=omar, points='1.00')
        assert again.id == balance.id + 1
    with pytest.raises(ValidationError, match='integer id'):
        with app.database.transaction() as tx:
            tx.get(PointMove, 'abc')
    with pytest.raises(ValidationError):
        with app.database.transaction() as tx:
            tx.insert_many(PointMove, [move(member=aisha), move(member=aisha, points='x')])
    assert app.database.count(PointMove) == 4


def test_insert_many_is_for_ledger_models_and_refuses_bad_rows(points):
    app, _ = points
    with pytest.raises(ValueError, match='insert_many writes ledger models'):
        with app.database.transaction() as tx:
            tx.insert_many(Member, [{'full_name': 'x'}])
    with pytest.raises(ValidationError, match='rows'):
        with app.database.transaction() as tx:
            tx.insert_many(PointMove, {'points': '1'})


# Append only

def test_the_session_refuses_to_change_an_append_only_ledger(points):
    app, _ = points
    aisha = member(app)
    with app.database.transaction() as tx:
        row = tx.create(PointMove, **move(member=aisha)).id
    with pytest.raises(ValueError, match='PointMove is append only: its rows are never changed'):
        with app.database.transaction() as tx:
            tx.update(PointMove, row, points='2.00')
    with pytest.raises(ValueError, match='never deleted'):
        with app.database.transaction() as tx:
            tx.delete(PointMove, row)
    assert app.database.get(PointMove, row).points == D('1.00')


def test_triggers_refuse_raw_update_delete_and_replace(points):
    app, _ = points
    aisha = member(app)
    with app.database.transaction() as tx:
        row = tx.create(PointMove, **move(member=aisha)).id
    statements = [('UPDATE pointmove SET points = 5', ()), ('DELETE FROM pointmove', ())]
    if postgres(app):
        statements.append(("INSERT INTO pointmove (id, member, on_date, reason, points, team) VALUES (?, ?, '2026-11-02', 'x', 5, 'n') "
                           'ON CONFLICT (id) DO UPDATE SET points = 5', (row, aisha)))
        statements.append(('TRUNCATE pointmove', ()))
    else:
        statements.append(("INSERT OR REPLACE INTO pointmove (id, member, on_date, reason, points, team) VALUES (?, ?, '2026-11-02', 'x', 5, 'n')",
                           (row, aisha)))
        statements.append(("INSERT INTO pointmove (id, member, on_date, reason, points, team) VALUES (?, ?, '2026-11-02', 'x', 5, 'n') "
                           'ON CONFLICT (id) DO UPDATE SET points = 5', (row, aisha)))
    for sql, values in statements:
        with pytest.raises(Exception) as error:
            with app.database.transaction() as tx:
                tx.connection.execute(sql, values)
        assert 'append only' in str(error.value) or isinstance(error.value, (ConflictError, sqlite3.OperationalError)), sql
    if not postgres(app):
        # A connection that is not Evlet's (no recursive triggers) is refused too.
        with sqlite3.connect(app.database.path) as connection:
            for sql, values in statements:
                with pytest.raises(sqlite3.IntegrityError, match='pointmove is append only'):
                    connection.execute(sql, values)
    assert [(item.id, item.points) for item in app.database.list(PointMove)] == [(row, D('1.00'))]


# Rollback, foreign keys, journal, timestamps

def test_a_rollback_leaves_no_rows_and_no_journal_entries(points):
    app, _ = points
    aisha = member(app)
    with pytest.raises(RuntimeError):
        with app.database.transaction() as tx:
            tx.insert_many(PointMove, [move(member=aisha), move(member=aisha)])
            tx.create(PointBalance, member=aisha, points='2.00')
            raise RuntimeError('stop')
    assert app.database.count(PointMove) == 0 and app.database.count(PointBalance) == 0
    with app.database.transaction() as tx:
        assert tx.connection.execute("SELECT count(*) FROM evlet_journal WHERE model = 'pointmove'").fetchone()[0] == 0


def test_foreign_keys_hold_between_ledger_and_normal_models(points):
    app, _ = points
    aisha = member(app)
    with pytest.raises(ConflictError):
        with app.database.transaction() as tx:
            tx.create(PointMove, **move(member=999999))
    with app.database.transaction() as tx:
        row = tx.create(PointMove, **move(member=aisha)).id
        tx.create(PointBalance, member=aisha, points='1.00', last_move=row)
    # A ledger row keeps the member it points at, and a balance row keeps its ledger row.
    with pytest.raises(ConflictError):
        with app.database.transaction() as tx:
            tx.delete(Member, aisha)
    with pytest.raises(ConflictError):
        with app.database.transaction() as tx:
            tx.create(PointBalance, member=aisha, points='1.00', last_move=row + 100)


def test_the_journal_records_ledger_rows_and_detects_tampering(points):
    from evlet import journal
    app, _ = points
    aisha = member(app)
    with app.database.transaction() as tx:
        tx.actor = 1
        first, second = tx.insert_many(PointMove, [move(member=aisha), move(member=aisha, points='3.00')])
    with app.database.transaction() as tx:
        rows = tx.connection.execute("SELECT record_id, kind, actor, data FROM evlet_journal WHERE model='pointmove' ORDER BY id").fetchall()
    assert [(row[0], row[1], row[2]) for row in rows] == [(first, 'created', '1'), (second, 'created', '1')]
    assert '"points":{"new":"3.00","old":null}' in rows[1][3]
    assert journal.verify(app.database) == (2, [])
    if postgres(app):
        return
    with sqlite3.connect(app.database.path) as connection:
        connection.execute('DROP TRIGGER evlet_journal_no_update')
        connection.execute("UPDATE evlet_journal SET data = replace(data, '3.00', '30.00') WHERE record_id = ?", (second,))
    checked, breaks = journal.verify(app.database)
    assert checked == 2 and [item[3] for item in breaks] == ['the hash does not match the entry']


def test_a_mutable_journaled_ledger_records_changes_and_deletes(tmp_path):
    journaled = type('Tally', (Model,), {'Meta': type('Meta', (), {'ledger': True, 'journal': Journal()}), 'amount': Integer()})
    database = Database(tmp_path / 'tally.sqlite3', Registry(journaled))
    database.initialize()
    with database.transaction() as tx:
        row = tx.create(journaled, amount=1).id
        tx.update(journaled, row, amount=2)
        tx.delete(journaled, row)
    with database.transaction() as tx:
        kinds = [item[0] for item in tx.connection.execute("SELECT kind FROM evlet_journal WHERE model='tally' ORDER BY id")]
    assert kinds == ['created', 'changed', 'deleted']


def test_timestamps_are_filled_on_insert_and_update_and_absent_by_default(points):
    app, _ = points
    aisha = member(app)
    before = datetime.now(timezone.utc)
    with app.database.transaction() as tx:
        row = tx.create(PointMove, **move(member=aisha))
        balance = tx.create(PointBalance, member=aisha, points='1.00')
    assert before <= row.created_at <= datetime.now(timezone.utc) and before <= balance.modified_at
    with app.database.transaction() as tx:
        changed = tx.update(PointBalance, balance.id, points='2.00')
    assert changed.modified_at > balance.modified_at
    assert 'created_at' not in Reading.__fields__ and 'modified_at' not in PointMove.__fields__
    with pytest.raises(ValidationError, match='filled by the framework'):
        with app.database.transaction() as tx:
            tx.create(PointMove, **move(member=aisha), created_at=before)


def test_closed_periods_refuse_dated_ledger_writes_with_the_trusted_override(points):
    app, _ = points
    aisha = member(app)
    with app.database.transaction() as tx:
        set_lock(tx.connection, '', date(2026, 10, 31), ADMIN)
    with pytest.raises(ValidationError, match='closed period'):
        with app.database.transaction() as tx:
            tx.insert_many(PointMove, [move(member=aisha), move(member=aisha, on_date=date(2026, 10, 31))])
    with app.database.transaction() as tx, tx.unlocked_periods('Load October from the old system'):
        tx.create(PointMove, **move(member=aisha, on_date=date(2026, 10, 15)))
    assert app.database.count(PointMove) == 1


# Measures and reports

def test_measures_and_a_report_are_exact_and_follow_the_row_scope(points):
    app, admin = points
    aisha, omar = member(app), member(app, 'Omar', 'south')
    with app.database.transaction() as tx:
        tx.insert_many(PointMove, [move(member=aisha, points='0.10'), move(member=aisha, points='0.20'),
                                   move(member=omar, points='0.30', team='south')])
    [total] = app.database.aggregate(PointMove, measures={'points': Sum('points'), 'rows': Count()})
    assert total == {'points': D('0.60'), 'rows': 3}
    status, result, _ = admin('POST', '/api/_reports/Points', {'filters': {}})
    assert status == 200, result
    assert sorted((row['team'], D(row['points'])) for row in result['rows']) == [('north', D('0.30')), ('south', D('0.30'))]
    viewer = client(app, 'viewer')
    status, result, _ = viewer('POST', '/api/_reports/Points', {'filters': {}})
    assert status == 200, result
    assert [(row['team'], D(row['points'])) for row in result['rows']] == [('north', D('0.30'))]


# No public surface

def test_a_ledger_model_has_no_public_surface(points):
    app, admin = points
    aisha = member(app)
    with app.database.transaction() as tx:
        row = tx.create(PointMove, **move(member=aisha)).id
        tx.update(Member, aisha, last_move=row)
    for name in ('pointmove', 'pointbalance', 'reading'):
        for method, path, body in (('GET', f'/api/{name}', None), ('POST', f'/api/{name}', {}), ('GET', f'/api/{name}/{row}', None),
                                   ('PATCH', f'/api/{name}/{row}', {}), ('DELETE', f'/api/{name}/{row}', None),
                                   ('GET', f'/api/{name}/aggregate?measures=count:count', None), ('GET', f'/api/_discovery/{name}', None),
                                   ('POST', f'/api/_draft/{name}', {'values': {}}), ('GET', f'/app/{name}', None)):
            assert admin(method, path, body)[0] == 404, (method, path)
        assert admin('POST', '/api/_tools/execute', {'tool': 'list_records', 'arguments': {'model': name}})[0] == 404
    schema = admin('GET', '/api/schema')[1]
    names = {entry['name'] for entry in schema['models']}
    assert names.isdisjoint({'pointmove', 'pointbalance', 'reading'}) and 'member' in names
    assert names.isdisjoint(entry['name'] for entry in admin('GET', '/api/_discovery')[1]['models']) is False
    assert {'pointmove', 'pointbalance', 'reading'}.isdisjoint(entry['name'] for entry in admin('GET', '/api/_discovery')[1]['models'])
    assert {'PointMove', 'PointBalance', 'Reading'}.isdisjoint(model['name'] for model in admin('GET', '/api/_controls/state')[1]['models'])
    assert {'pointmove', 'pointbalance', 'reading'}.isdisjoint(entry['name'] for entry in app.business.registry.ui_schema()['models'])
    assert 'PointMove' not in str(admin('GET', '/api/ui')[1])
    search = admin('GET', '/api/_search?q=Aisha')
    assert search[0] in (200, 404) and 'pointmove' not in str(search[1])
    # A normal model's Link to a ledger row: the plain integer id, read only, with no target to follow.
    [field] = [item for item in next(entry for entry in schema['models'] if entry['name'] == 'member')['fields'] if item['name'] == 'last_move']
    assert field['type'] == 'integer' and field['read_only'] and 'target' not in field
    detail = admin('GET', f'/api/member/{aisha}')[1]
    assert detail['last_move'] == row
    status, payload, _ = admin('PATCH', f'/api/member/{aisha}', {'last_move': row}, headers={b'if-match': admin('GET', f'/api/member/{aisha}')[2][b'etag']})
    assert status in (403, 422), payload


# Migrations

def lean(name, fields, **meta):
    return type(name, (Model,), {'storage_name': name.lower(), 'Meta': type('Meta', (), {'ledger': True, **meta}), **fields})


def test_create_add_field_and_add_index_migrations(tmp_path):
    path = tmp_path / 'migrated.sqlite3'
    first = lean('Meter', {'reading': Integer(required=True)}, append_only=True)
    Database(path, Registry(first), migrations=[Migration('0001_meter', CreateModel(first))]).migrate([Migration('0001_meter', CreateModel(first))])
    with Database(path, Registry(first)).transaction() as tx:
        tx.insert_many(first, [{'reading': 1}, {'reading': 2}])
    second = lean('Meter', {'reading': Integer(required=True), 'site': Text(default='main')}, append_only=True,
                  timestamps='created', indexes=[('site', 'reading')])
    migrations = [Migration('0001_meter', CreateModel(first)),
                  Migration('0002_site', AddField('meter', 'site', Text(default='main')), AddField('meter', 'created_at', DateTime()),
                            AddIndex('meter', ('site', 'reading')))]
    database = Database(path, Registry(second), migrations=migrations)
    assert database.migrate(migrations) == ['0002_site']
    database.initialize()
    assert [(row.reading, row.site, row.created_at) for row in database.list(second, order_by=('id',))] == [(1, 'main', None), (2, 'main', None)]
    with sqlite3.connect(path) as connection:
        indexes = [row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='meter'")]
        assert any('("site", "reading")' in sql for sql in indexes)
        with pytest.raises(sqlite3.IntegrityError, match='append only'):
            connection.execute('UPDATE meter SET reading = 3')


@pytest.mark.parametrize('operation', [RenameField('meter', 'reading', 'value'), RetireField('meter', 'reading'),
                                       AlterField('meter', 'reading', Decimal(scale=2))])
def test_append_only_tables_refuse_operations_that_rewrite_rows(tmp_path, operation):
    path = tmp_path / 'refused.sqlite3'
    model = lean('Meter', {'reading': Integer()}, append_only=True)
    Database(path, Registry(model)).migrate([Migration('0001_meter', CreateModel(model))])
    with pytest.raises(SchemaError, match='meter is append only, so .* cannot change its stored rows; turn it off first with SetAppendOnly'):
        Database(path, Registry(model)).migrate([Migration('0001_meter', CreateModel(model)), Migration('0002', operation)])


def test_append_only_turns_on_for_a_table_with_rows_and_off_again(tmp_path):
    path = tmp_path / 'toggle.sqlite3'
    mutable = lean('Meter', {'reading': Integer()})
    history = [Migration('0001_meter', CreateModel(mutable))]
    Database(path, Registry(mutable)).migrate(history)
    with Database(path, Registry(mutable)).transaction() as tx:
        row = tx.create(mutable, reading=1).id
        tx.update(mutable, row, reading=2)
    locked = lean('Meter', {'reading': Integer()}, append_only=True)
    # The models alone are not a migration.
    with pytest.raises(SchemaError, match='append_only is False after the migrations and True in the models'):
        Database(path, Registry(locked)).migrate([*history, Migration('0002_noop', AddField('meter', 'note', Text(default='')))])
    plan = Database(path, Registry(locked)).plan(history)
    assert "SetAppendOnly('meter', True)" in plan.text()
    history.append(Migration('0002_lock', SetAppendOnly('meter', True)))
    Database(path, Registry(locked)).migrate(history)
    with pytest.raises(ValueError, match='append only'):
        with Database(path, Registry(locked)).transaction() as tx:
            tx.update(locked, row, reading=3)
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute('DELETE FROM meter')
    history.append(Migration('0003_unlock', SetAppendOnly('meter', False)))
    Database(path, Registry(mutable)).migrate(history)
    with Database(path, Registry(mutable)).transaction() as tx:
        assert tx.update(mutable, row, reading=4).reading == 4


def test_switching_ledger_mode_is_refused(tmp_path):
    path = tmp_path / 'switch.sqlite3'
    plain = type('Meter', (Model,), {'storage_name': 'meter', 'reading': Integer()})
    Database(path, Registry(plain)).initialize()
    with pytest.raises(SchemaError, match='switching an existing model to Meta.ledger = True, or back, is not supported'):
        Database(path, Registry(lean('Meter', {'reading': Integer()}))).initialize()
    other = tmp_path / 'back.sqlite3'
    Database(other, Registry(lean('Meter', {'reading': Integer()}))).initialize()
    with pytest.raises(SchemaError, match='switching an existing model'):
        Database(other, Registry(plain)).migrate([Migration('0001', AddField('meter', 'note', Text(default='')))])


def test_the_schema_check_finds_missing_triggers_and_indexes(tmp_path):
    path = tmp_path / 'checked.sqlite3'
    registry = Registry(*MODELS)
    Database(path, registry).initialize()
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TRIGGER evlet_pointmove_no_update')
    with pytest.raises(SchemaError, match='append-only triggers are incomplete'):
        Database(path, registry).initialize()


def test_postgres_migrations_create_add_and_toggle(pg_dsn):
    first = lean('Meter', {'reading': Integer(required=True)}, append_only=True)
    history = [Migration('0001_meter', CreateModel(first))]
    PostgreSQLDatabase(pg_dsn, Registry(first)).migrate(history)
    database = PostgreSQLDatabase(pg_dsn, Registry(first))
    with database.transaction() as tx:
        assert tx.insert_many(first, [{'reading': 1}, {'reading': 2}]) == [1, 2]
    second = lean('Meter', {'reading': Integer(required=True), 'site': Text(default='main')}, indexes=[(('site', 'reading'), {'unique': True})])
    history.append(Migration('0002', AddField('meter', 'site', Text(default='main')), AddIndex('meter', ('site', 'reading'), unique=True),
                             SetAppendOnly('meter', False)))
    database = PostgreSQLDatabase(pg_dsn, Registry(second), migrations=history)
    database.migrate(history)
    database.initialize()
    with database.transaction() as tx:
        assert tx.update(second, 1, reading=5).reading == 5
    with pytest.raises(ConflictError):
        with database.transaction() as tx:
            tx.create(second, reading=5)
    with pytest.raises(SchemaError, match='append only'):
        third = lean('Meter', {'value': Integer(required=True), 'site': Text(default='main')}, append_only=True,
                     indexes=[(('site', 'value'), {'unique': True})])
        PostgreSQLDatabase(pg_dsn, Registry(third)).migrate(
            [*history, Migration('0003', SetAppendOnly('meter', True)), Migration('0004', RenameField('meter', 'reading', 'value'))])


def test_plans_suggest_frozen_ledger_classes(tmp_path):
    from evlet.migrations import suggest
    registry = Registry(*MODELS)
    text = suggest(registry, {'version': 1, 'models': [m for m in registry.schema()['models'] if m['name'] == 'member']})
    assert 'class PointMove(Model):' in text and "        timestamps = 'created'" in text and '        append_only = True' in text
    assert "        indexes = [('member', 'on_date')]" in text and 'created_at =' not in text
    namespace = {}
    exec(text.replace('Link(\'member\'', 'Link(\'member\''), namespace)  # The suggestion is valid Python.
    assert namespace['PointMove'].__append_only__


# CLI

def test_ledger_list_prints_each_ledger_model(points, tmp_path, monkeypatch, capsys):
    from evlet.cli import ledger as command
    from evlet.cli.main import main
    app, _ = points
    aisha = member(app)
    with app.database.transaction() as tx:
        tx.insert_many(PointMove, [move(member=aisha)] * 3)
    root = tmp_path / 'project'
    for name in ('apps', 'instances/acme'):
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(command, 'load_runtime', lambda project, name: app if (project, name) == (root, 'acme') else None)
    main(['ledger', 'list', '--instance', 'acme', '--project', str(root)])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ['Model', 'Table', 'Rows', 'Append', 'only', 'Journal', 'Timestamps', 'Period']
    assert [line.split() for line in lines[1:]] == [
        ['PointMove', 'pointmove', '3', 'yes', 'tamper', 'evident', 'created', 'on_date'],
        ['PointBalance', 'pointbalance', '0', 'no', 'off', 'modified', 'off'],
        ['Reading', 'reading', '0', 'no', 'off', 'off', 'off']]
    assert describe(app.database)[0] == {'model': 'PointMove', 'table': 'pointmove', 'rows': 3, 'append_only': True,
                                         'journal': 'tamper evident', 'timestamps': 'created', 'period': 'on_date'}
    assert ledger_table([]) == 'No ledger models'


# tx.lock

@pytest.mark.parametrize('key', [(), ['stock', 1], ('stock', 1.5), ('stock', True), ('',), ('x' * 201,), ('a\x00b',), ('n', 2**63), 'stock'])
def test_lock_keys_are_validated(tmp_path, key):
    database = Database(tmp_path / 'keys.sqlite3', Registry(Reading))
    database.initialize()
    with pytest.raises(ValueError, match='lock key'):
        with database.transaction() as tx:
            tx.lock(key)


def test_sqlite_locks_check_keys_and_need_a_write_transaction(tmp_path):
    database = Database(tmp_path / 'locks.sqlite3', Registry(Reading))
    database.initialize()
    with database.transaction() as tx:
        tx.lock(('stock', 1, 2), ('open_item', 42), timeout=5)
        tx.create(Reading, count=1)
    with pytest.raises(ValueError, match='at least one key'):
        with database.transaction() as tx:
            tx.lock()
    with pytest.raises(ValueError, match='timeout'):
        with database.transaction() as tx:
            tx.lock(('a',), timeout=0)
    with database._connection() as connection, pytest.raises(ValueError, match='inside database.transaction'):
        Session(connection, database.registry).lock(('stock', 1))


def test_lock_hashes_are_stable_signed_64_bit_numbers():
    assert lock_hash(('stock', 1, 2)) == lock_hash(('stock', 1, 2))
    assert lock_hash(('stock', 1)) != lock_hash(('stock', '1'))
    assert -2**63 <= lock_hash(('x',)) < 2**63
    # Pinned so a release can never change which number a key locks.
    assert lock_hash(('stock', 1, 2)) == 3951967802950191666


def decrement_concurrently(database, balance_model, member_id, workers=6):
    """Each worker takes the lock, reads the balance, checks it covers 1.00 and writes it back less 1.00."""
    refused, errors = [], []
    start = threading.Barrier(workers)

    def work():
        start.wait()
        try:
            with database.transaction() as tx:
                tx.lock(('points', member_id))
                [balance] = tx.list(balance_model, filters={'member': member_id})
                if balance.points < 1:
                    refused.append(1)
                    return
                tx.update(balance_model, balance.id, points=balance.points - 1)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
    threads = [threading.Thread(target=work) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return refused, errors


def test_check_then_write_under_a_lock_never_overdraws(points):
    app, _ = points
    aisha = member(app)
    with app.database.transaction() as tx:
        tx.create(PointBalance, member=aisha, points='3.00')
    refused, errors = decrement_concurrently(app.database, PointBalance, aisha)
    assert errors == [] and len(refused) == 3
    assert app.database.list(PointBalance)[0].points == D('0.00')


# tx.lock on PostgreSQL, against a second real connection

@pytest.fixture
def locks(pg_dsn):
    import psycopg
    database = PostgreSQLDatabase(pg_dsn, Registry(Reading))
    database.initialize()
    other = psycopg.connect(pg_dsn)
    try:
        yield database, other
    finally:
        other.rollback()
        other.close()


def hold(other, *keys):
    """Take the keys' locks on another connection's open transaction, as a second writer would."""
    for key in keys:
        other.execute('SELECT pg_advisory_xact_lock(%s)', (lock_hash(key),))


def test_postgres_locks_wait_for_commit_and_rollback(locks):
    database, other = locks
    for finish in (other.commit, other.rollback):
        hold(other, ('stock', 1))
        acquired = threading.Event()

        def take():
            with database.transaction() as tx:
                tx.lock(('stock', 1), timeout=10)
                acquired.set()
        thread = threading.Thread(target=take)
        thread.start()
        assert not acquired.wait(0.5)
        finish()
        assert acquired.wait(5)
        thread.join()


def test_postgres_locks_on_other_keys_do_not_wait(locks):
    database, other = locks
    hold(other, ('stock', 1))
    with database.transaction() as tx:
        tx.lock(('stock', 2), ('open_item', 1), timeout=1)


def test_postgres_locks_in_opposite_orders_do_not_deadlock(locks, pg_dsn):
    database, other = locks
    database.close()
    results, barrier = [], threading.Barrier(2)

    def run(keys):
        # Two sessions that are not serialized by Evlet's write lock: each locks through Session.lock.
        import psycopg
        from evlet.postgres import Connection, _row_factory
        with psycopg.connect(pg_dsn, row_factory=_row_factory) as raw:
            raw.execute('BEGIN')
            session = Session(Connection(raw, database), database.registry)
            barrier.wait()
            session.lock(*keys, timeout=5)
            raw.execute('SELECT pg_sleep(0.2)')
            results.append(keys)
            raw.rollback()
    threads = [threading.Thread(target=run, args=(keys,)) for keys in ((('a',), ('b',)), (('b',), ('a',)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2


def test_postgres_lock_timeout_raises_and_rolls_back(locks):
    database, other = locks
    hold(other, ('stock', 1))
    with pytest.raises(LockTimeout, match='busy with the same records; try again'):
        with database.transaction() as tx:
            tx.create(Reading, count=7)
            tx.lock(('stock', 1), timeout=0.2)
    assert database.count(Reading) == 0
    other.rollback()
    # The timeout is restored: a later wait in the same transaction uses the database's own limit again.
    with database.transaction() as tx:
        tx.lock(('stock', 1), timeout=0.2)
        assert tx.connection.execute("SELECT current_setting('lock_timeout')").fetchone()[0] == '5s'


def test_postgres_locks_validate_keys_and_need_a_write_transaction(locks):
    database, _ = locks
    with pytest.raises(ValueError, match='lock key'):
        with database.transaction() as tx:
            tx.lock(('stock', None))
    with database._connection() as connection:
        with pytest.raises(ValueError, match='inside database.transaction'):
            Session(connection, database.registry).lock(('stock', 1))
        connection.execute('BEGIN')
        with pytest.raises(ValueError, match='inside database.transaction'):
            Session(connection, database.registry).lock(('stock', 1))


# Playground

def test_the_playground_awards_points_into_a_ledger_and_its_balance_cache(tmp_path, monkeypatch):
    from examples.playground.app import create_app
    from examples.playground.models import PointBalance as Balance, PointMove as Movement
    from evlet.auth import password_hash
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'playground.sqlite3'))
    app = create_app()
    app.database.initialize()
    app.business.initialize()
    with app.database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', ('admin', password_hash(PASSWORD), 'admin'))
    admin = client(app)
    status, customer, _ = admin('POST', '/api/customer', {'customer_name': 'Acme'})
    assert status == 201, customer
    for _ in range(2):
        status, payload, _ = admin('POST', f"/api/customer/{customer['id']}/actions/award_points", {})
        assert status == 200, payload
    assert [(row.points, row.reason) for row in app.database.list(Movement)] == [(10, 'Awarded from the customer page')] * 2
    assert [row.points for row in app.database.list(Balance)] == [20]
    assert admin('GET', '/api/pointmove')[0] == 404


def test_applications_refuse_attachments_and_actions_on_ledger_models(tmp_path):
    from evlet import Action
    database = Database(tmp_path / 'refused.sqlite3', Registry(*MODELS))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    with pytest.raises(ValueError, match='PointMove is a ledger model: it has no attachments'):
        Application(database, auth=auth, attachments=(PointMove,))
    with pytest.raises(ValueError, match='PointMove is a ledger model: it has no actions'):
        Application(database, auth=auth, actions=(Action(PointMove, 'touch', 'Touch', lambda tx, user, row: None),))
