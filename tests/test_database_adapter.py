"""Dialect conformance: the checklist every database dialect passes (docs/database-adapters.md).

Each test speaks to the query builder (evlet/qb.py), the connection's named methods (evlet/adapter.py) and the
Database's transactions, never to SQLite SQL. It runs on SQLite today. To check a new dialect, change
`adapter_database` to build that dialect's Database and run this module.

The last part opens a database written by Evlet 0.4.3 (tests/fixtures/sqlite-0.4.3) and checks that the
current release changes nothing in it: the schema, the stored rows and the migration checksums.
"""

from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import threading

import pytest

from evlet import (Boolean, Currency, Database, Date, DateTime, Decimal, Integer, Link, Model, Percent, Registry, Select, Text,
                   ValidationError)
from evlet import qb
from evlet.adapter import Column, Connection, IntegrityError, IntegrityFailure, TableDefinition, TableShape
from evlet.database import ConflictError
from evlet.migrations import SchemaError
from evlet import writes

import upgrade_fixture

MAX = 2**63 - 1


class Client(Model):
    full_name = Text(required=True, unique=True)
    visits = Integer(default=0)
    vip = Boolean(default=False)
    joined = Date()
    seen = DateTime()


class Bill(Model):
    client = Link(Client, required=True)
    total = Currency(required=True)
    rate = Decimal(scale=4, default=0)
    share = Percent(default=0)
    kind = Select(('cash', 'card'), default='cash')

    class Meta:
        indexes = [(('client', 'kind'), {'unique': True})]


class Entry(Model):
    """An append-only ledger."""
    bill = Link(Bill, required=True)
    amount = Currency(required=True)

    class Meta:
        ledger = True
        append_only = True


@pytest.fixture
def adapter_database(tmp_path):
    """The database under test. A new adapter builds its own Database here."""
    database = Database(tmp_path / 'adapter.sqlite3', Registry(Client, Bill, Entry))
    database.initialize()
    yield database
    database.close()


@pytest.fixture
def connection(adapter_database):
    """A connection inside a schema transaction that is rolled back afterwards."""
    with pytest.raises(_Undo):
        with adapter_database.schema_transaction() as connection:
            yield connection
            raise _Undo()


class _Undo(Exception):
    pass


def test_the_connection_implements_the_whole_interface(connection):
    assert isinstance(connection, Connection)
    names = [name for name, value in vars(Connection).items() if callable(value) and not name.startswith('_')]
    for name in names:
        assert getattr(type(connection), name) is not getattr(Connection, name), f'{name} is not implemented'


# ---------- values ----------

def test_values_round_trip_exactly(adapter_database):
    with adapter_database.transaction() as tx:
        client = tx.create(Client, full_name='Ünïcode “quotes” \' and "double"', visits=MAX, vip=True, joined=date(2026, 2, 28),
                           seen=datetime(2026, 11, 13, 6, 30, tzinfo=timezone.utc))
        low = tx.create(Client, full_name='Low', visits=-MAX - 1)
        bill = tx.create(Bill, client=client.id, total='9223372036854.77', rate='0.0001', share='12.5')
    stored = adapter_database.get(Client, client.id)
    assert (stored.full_name, stored.visits, stored.vip, stored.joined) == (client.full_name, MAX, True, date(2026, 2, 28))
    assert stored.seen == datetime(2026, 11, 13, 6, 30, tzinfo=timezone.utc)
    assert adapter_database.get(Client, low.id).visits == -MAX - 1
    saved = adapter_database.get(Bill, bill.id)
    assert (saved.total, saved.rate, saved.share) == (D('9223372036854.77'), D('0.0001'), D('12.5'))


def test_exact_amounts_add_up_without_rounding(adapter_database):
    with adapter_database.transaction() as tx:
        client = tx.create(Client, full_name='Sums')
        bill = tx.create(Bill, client=client.id, total='0.10')
        tx.insert_many(Entry, [{'bill': bill.id, 'amount': '0.10'} for _ in range(3)] + [{'bill': bill.id, 'amount': '-0.30'}])
    from evlet import Sum
    assert adapter_database.aggregate(Entry, measures={'total': Sum('amount')})[0]['total'] == D('0')


def test_bytes_and_empty_values_round_trip(connection):
    connection.create_table('adapter_blob', [Column('data', 'BLOB'), Column('note', 'TEXT')])
    for value in (b'', b'\x00\xff' * 100, None):
        row_id = connection.run(qb.insert('adapter_blob', data=value, note='').returning_id()).id
        assert connection.run(qb.select('adapter_blob', 'data').where(id=row_id)).value() == value


def test_rows_read_by_index_and_by_name(connection):
    connection.create_table('adapter_rows', [Column('one', 'INTEGER'), Column('two', 'TEXT')])
    connection.run(qb.insert('adapter_rows', one=1, two='2'))
    row = connection.run(qb.select('adapter_rows', 'one', 'two')).one()
    assert (row[0], row['two']) == (1, '2')


# ---------- ids ----------

def test_an_insert_returns_the_new_id_for_model_and_framework_tables(connection):
    connection.create_table('adapter_model', [Column('note', 'TEXT')])
    first = connection.run(qb.insert('adapter_model', note='a').returning_id()).id
    second = connection.run(qb.insert('adapter_model', note='b').returning_id()).id
    assert second > first > 0
    connection.ensure_table(TableDefinition('evlet_adapter_probe', (Column('note', 'TEXT', required=True),), numbered=True))
    probe = connection.run(qb.insert('evlet_adapter_probe', note='x').returning_id()).id
    assert connection.run(qb.select('evlet_adapter_probe', 'note').where(id=probe)).value() == 'x'


def test_insert_many_and_reserve_ids_give_ids_in_order(adapter_database):
    with adapter_database.transaction() as tx:
        bill = tx.create(Bill, client=tx.create(Client, full_name='Ids').id, total='1')
        reserved = tx.reserve_ids(Entry, 2)
        assert reserved == sorted(reserved) and len(set(reserved)) == 2
        ids = tx.insert_many(Entry, [{'bill': bill.id, 'amount': '1'}, {'id': reserved[1], 'bill': bill.id, 'amount': '2'},
                                     {'bill': bill.id, 'amount': '3'}])
        assert ids[1] == reserved[1] and ids[0] not in reserved and ids[2] not in reserved
        assert [tx.get(Entry, entry_id).amount for entry_id in ids] == [D('1'), D('2'), D('3')]
        more = tx.connection.insert_many('entry', ('bill', 'amount'), [(bill.id, 4), (bill.id, 5)])
        assert more == sorted(more) and more[0] > max(ids)


def test_ids_and_locks_need_a_write_transaction(adapter_database):
    with adapter_database._connection() as connection:
        with pytest.raises(ValueError, match='inside database.transaction'):
            connection.reserve_ids('entry', 1)
        with pytest.raises(ValueError, match='inside database.transaction'):
            connection.lock([1])
    with adapter_database.transaction() as tx:
        tx.lock(('bill', 1), ('bill', 2), timeout=1)


# ---------- reading the schema ----------

def test_tables_columns_and_inspect_give_the_neutral_shape(connection):
    assert {'client', 'bill', 'entry'} <= connection.tables()
    assert connection.table_exists('bill') and not connection.table_exists('nothing_here')
    assert not any(name.startswith('sqlite_') for name in connection.tables())
    assert connection.columns('client')[:2] == ['id', 'full_name']
    shape = connection.inspect('bill')
    assert isinstance(shape, TableShape)
    columns = {column.name: column for column in shape.columns}
    assert columns['id'] == Column('id', 'INTEGER', False, True)
    assert columns['client'] == Column('client', 'INTEGER', True, False, 'client')
    assert columns['total'].required and columns['kind'] == Column('kind', 'TEXT', False, False)
    unique = [index for index in shape.indexes if index.unique and not index.partial]
    assert ('client', 'kind') in [index.columns for index in unique]


def test_every_link_holds(connection):
    assert connection.broken_foreign_key() is None


# ---------- changing the schema ----------

def test_create_table_and_each_column_change_keep_the_rows(connection):
    connection.create_table('adapter_item', [Column('label', 'TEXT', required=True), Column('qty', 'INTEGER'),
                                             Column('client', 'INTEGER', references='client')])
    connection.run(qb.insert('adapter_item', label='a', qty=1))
    connection.add_column('adapter_item', Column('colour', 'TEXT'), default='blue')
    connection.add_column('adapter_item', Column('note', 'TEXT'))
    assert tuple(connection.run(qb.select('adapter_item', 'colour', 'note')).one()) == ('blue', None)
    connection.rename_column('adapter_item', 'qty', 'quantity')
    assert connection.run(qb.select('adapter_item', 'quantity')).value() == 1
    connection.set_required('adapter_item', 'quantity', True)
    assert {c.name: c.required for c in connection.inspect('adapter_item').columns}['quantity'] is True
    with pytest.raises(IntegrityError):
        connection.run(qb.insert('adapter_item', label='no quantity'))
    connection.set_required('adapter_item', 'label', False)
    connection.run(qb.insert('adapter_item', quantity=2))
    connection.drop_reference('adapter_item', 'client')
    assert {c.name: c.references for c in connection.inspect('adapter_item').columns}['client'] is None
    connection.run(qb.insert('adapter_item', quantity=3, client=12345))
    connection.drop_column('adapter_item', 'note')
    assert 'note' not in connection.columns('adapter_item')
    assert [row[0] for row in connection.run(qb.select('adapter_item', 'quantity').order_by('id'))] == [1, 2, 3]
    assert connection.broken_foreign_key() is None


def test_indexes_get_free_names_and_partial_ones_cover_set_values(connection):
    connection.create_table('adapter_coded', [Column('code', 'TEXT'), Column('scope', 'TEXT')])
    first = connection.create_index('adapter_coded', 'evlet_adapter_coded_code_unique', ('scope', 'code'), unique=True, partial='code')
    second = connection.create_index('adapter_coded', 'evlet_adapter_coded_code_unique', ('code',))
    assert first == 'evlet_adapter_coded_code_unique' and second != first and second.startswith(first)
    found = {index.name: index for index in connection.inspect('adapter_coded').indexes}
    assert found[first].unique and found[first].partial and found[first].columns == ('scope', 'code')
    for code in (None, None, '1'):
        connection.run(qb.insert('adapter_coded', code=code, scope='a'))
    with pytest.raises(IntegrityError):
        connection.run(qb.insert('adapter_coded', code='1', scope='a'))
    connection.drop_index(first)
    assert first not in {index.name for index in connection.inspect('adapter_coded').indexes}


def test_append_only_tables_refuse_changes_until_dropped(connection):
    connection.create_table('adapter_log', [Column('note', 'TEXT')])
    row_id = connection.run(qb.insert('adapter_log', note='kept').returning_id()).id
    assert connection.is_append_only('adapter_log') is False
    connection.make_append_only('adapter_log', 'adapter_log is append only')
    connection.make_append_only('adapter_log', 'adapter_log is append only')
    assert connection.is_append_only('adapter_log') is True
    for statement in (qb.update('adapter_log', note='changed'), qb.delete('adapter_log'),
                      qb.insert('adapter_log', id=row_id, note='replaced').on_conflict('id').do_update(note='replaced')):
        with pytest.raises(IntegrityError) as caught:
            connection.run(statement)
        assert connection.integrity_failure(caught.value).message.endswith('adapter_log is append only')
    connection.run(qb.insert('adapter_log', note='appended'))
    connection.drop_append_only('adapter_log')
    assert connection.is_append_only('adapter_log') is False
    connection.run(qb.delete('adapter_log'))


def test_the_id_guard_refuses_a_changed_id(connection):
    connection.create_table('adapter_guarded', [Column('note', 'TEXT')])
    row_id = connection.run(qb.insert('adapter_guarded', note='a').returning_id()).id
    assert not connection.has_id_guard('adapter_guarded')
    connection.guard_id('adapter_guarded', 'adapter_guarded: ids never change')
    assert connection.has_id_guard('adapter_guarded')
    with pytest.raises(IntegrityError) as caught:
        connection.run(qb.update('adapter_guarded', id=qb.col('id') + 1))
    assert connection.integrity_failure(caught.value).message.endswith('ids never change')
    connection.run(qb.update('adapter_guarded', note='b').where(id=row_id))


def test_a_failed_schema_transaction_rolls_back_every_change(adapter_database):
    with pytest.raises(RuntimeError):
        with adapter_database.schema_transaction() as connection:
            connection.create_table('adapter_gone', [Column('note', 'TEXT')])
            connection.add_column('client', Column('adapter_extra', 'TEXT'))
            raise RuntimeError('stop')
    with adapter_database._connection() as connection:
        assert not connection.table_exists('adapter_gone')
        assert 'adapter_extra' not in connection.columns('client')


def test_cannot_drop_columns_says_why_or_nothing(connection):
    reason = connection.cannot_drop_columns()
    assert reason is None or isinstance(reason, str) and reason


# ---------- errors ----------

def test_a_unique_index_names_the_fields_and_a_unique_field_conflicts(adapter_database):
    with adapter_database.transaction() as tx:
        client = tx.create(Client, full_name='Twice')
        tx.create(Bill, client=client.id, total='1', kind='card')
    with pytest.raises(ValidationError) as caught:
        with adapter_database.transaction() as tx:
            tx.create(Bill, client=client.id, total='2', kind='card')
    assert set(caught.value.errors) == {'client', 'kind'}
    with pytest.raises(ConflictError):
        with adapter_database.transaction() as tx:
            tx.create(Client, full_name='Twice')


def test_foreign_keys_are_enforced_and_append_only_rows_conflict(adapter_database):
    with pytest.raises(ConflictError):
        with adapter_database.transaction() as tx:
            tx.connection.run(qb.insert('bill', id=1, client=424242, total=1))
    with adapter_database.transaction() as tx:
        bill = tx.create(Bill, client=tx.create(Client, full_name='Append').id, total='1')
        entry = tx.insert_many(Entry, [{'bill': bill.id, 'amount': '1'}])[0]
    with pytest.raises(ConflictError, match='append only'):
        with adapter_database.transaction() as tx:
            tx.connection.run(qb.update('entry', amount=2).where(id=entry))


def test_integrity_failure_reads_only_integrity_errors(connection):
    with pytest.raises(IntegrityError) as caught:
        connection.run(qb.insert('client', id=1, full_name=None))
    failure = connection.integrity_failure(caught.value)
    assert isinstance(failure, IntegrityFailure) and failure.message
    assert connection.integrity_failure(ValueError('not a database error')) is None
    connection.run(qb.insert('client', id=1, full_name='One'))
    with pytest.raises(IntegrityError) as caught:
        connection.run(qb.insert('client', id=2, full_name='One'))
    failure = connection.integrity_failure(caught.value)
    assert (failure.table, failure.columns) == ('client', ('full_name',))


def test_a_failed_transaction_rolls_back(adapter_database):
    with pytest.raises(RuntimeError):
        with adapter_database.transaction() as tx:
            tx.create(Client, full_name='Never saved')
            raise RuntimeError('stop')
    assert adapter_database.count(Client) == 0


def test_a_writer_that_waits_too_long_is_told_the_database_is_busy(adapter_database):
    adapter_database.write_wait = 0.2
    holding, release = threading.Event(), threading.Event()

    def hold():
        with adapter_database.transaction() as tx:
            tx.create(Client, full_name='Holder')
            holding.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert holding.wait(5)
        with pytest.raises(writes.Busy):
            with adapter_database.transaction() as tx:
                tx.create(Client, full_name='Waiter')
    finally:
        release.set()
        thread.join()
    assert adapter_database.count(Client) == 1


def test_a_snapshot_reads_one_moment(adapter_database):
    with adapter_database._connection() as connection:
        connection.snapshot()
        before = connection.run(qb.select('client', qb.count())).value()
        with adapter_database.transaction() as tx:
            tx.create(Client, full_name='Later')
        assert connection.run(qb.select('client', qb.count())).value() == before
        connection.rollback()


# ---------- the query builder ----------

@pytest.fixture
def people(connection):
    """A small table of people and teams to query, in the rolled-back connection."""
    connection.create_table('adapter_team', [Column('label', 'TEXT', required=True), Column('lead', 'INTEGER')])
    connection.create_table('adapter_person', [Column('given', 'TEXT', required=True), Column('team', 'INTEGER', references='adapter_team'),
                                               Column('age', 'INTEGER'), Column('tags', 'TEXT')])
    north = connection.run(qb.insert('adapter_team', label='North').returning_id()).id
    south = connection.run(qb.insert('adapter_team', label='South', lead=north).returning_id()).id
    for given, team, age, tags in (('Aisha', north, 31, '["a","b"]'), ('Bilal', north, None, None), ('Chen', south, 45, '["b"]'),
                                   ('dana', None, 22, None)):
        connection.run(qb.insert('adapter_person', given=given, team=team, age=age, tags=tags))
    return connection, north, south


def names(connection, query):
    return [row[0] for row in connection.run(query)]


def test_conditions_select_the_same_rows_on_every_dialect(people):
    connection, north, south = people
    person = qb.select('adapter_person', 'given').order_by('id')
    assert names(connection, person.where(team=north)) == ['Aisha', 'Bilal']
    assert names(connection, person.where(team=None)) == ['dana']
    assert names(connection, person.where(qb.ne('team', north))) == ['Chen']
    assert names(connection, person.where(qb.gt('age', 30), qb.le('age', 45))) == ['Aisha', 'Chen']
    assert names(connection, person.where(qb.between('age', 22, 31))) == ['Aisha', 'dana']
    assert names(connection, person.where(qb.in_('given', ['Chen', 'dana']))) == ['Chen', 'dana']
    assert names(connection, person.where(qb.in_('given', []))) == []
    assert names(connection, person.where(qb.not_in('given', []))) == ['Aisha', 'Bilal', 'Chen', 'dana']
    assert names(connection, person.where(qb.or_(qb.is_null('age'), qb.lt('age', 25)))) == ['Bilal', 'dana']
    assert names(connection, person.where(qb.not_(qb.eq('given', 'Aisha')))) == ['Bilal', 'Chen', 'dana']
    assert names(connection, person.where(qb.FALSE)) == [] and len(names(connection, person.where(qb.TRUE))) == 4


def test_text_matching_and_case(people):
    connection, _, _ = people
    person = qb.select('adapter_person', 'given').order_by('id')
    assert names(connection, person.where(qb.contains('given', 'i'))) == ['Aisha', 'Bilal']
    assert names(connection, person.where(qb.contains_ci('given', 'D'))) == ['dana']
    assert names(connection, person.where(qb.excludes_ci('tags', 'A'))) == ['Bilal', 'Chen', 'dana']
    assert names(connection, person.where(qb.starts_with('given', 'Ch'))) == ['Chen']
    assert names(connection, person.where(qb.starts_with('given', 'ch'))) == []
    assert names(connection, person.where(qb.starts_with('given', '_'))) == []
    assert names(connection, person.where(qb.contains('given', "%_'"))) == []


def test_joins_subqueries_and_exists(people):
    connection, north, _ = people
    joined = (qb.select(qb.table('adapter_person', 'p'), 'p.given', 't.label')
              .join(qb.table('adapter_team', 't'), qb.eq('t.id', qb.col('p.team')), left=True).order_by('p.id'))
    assert [tuple(row) for row in connection.run(joined)] == [('Aisha', 'North'), ('Bilal', 'North'), ('Chen', 'South'), ('dana', None)]
    inner = (qb.select(qb.table('adapter_person', 'p'), 'p.given', 't.label')
             .join(qb.table('adapter_team', 't'), qb.eq('t.id', qb.col('p.team'))).order_by('p.id'))
    assert [tuple(row) for row in connection.run(inner)] == [('Aisha', 'North'), ('Bilal', 'North'), ('Chen', 'South')]
    led = qb.select('adapter_team', 'id').where(qb.not_null('lead'))
    assert names(connection, qb.select('adapter_person', 'given').where(qb.in_('team', led))) == ['Chen']
    members = qb.select(qb.table('adapter_person', 'p'), 'p.id').where(qb.eq('p.team', qb.col('t.id')))
    teams = qb.select(qb.table('adapter_team', 't'), 't.label').order_by('t.id')
    assert names(connection, teams.where(qb.exists(members))) == ['North', 'South']
    assert names(connection, teams.where(qb.not_exists(members.where(qb.gt('p.age', 40))))) == ['North']
    scalar = qb.select(qb.table('adapter_team', 't'), 't.label').where(qb.eq('t.id', qb.col('p.team')))
    assert connection.run(qb.select(qb.table('adapter_person', 'p'), scalar).where(qb.eq('p.given', 'Chen'))).value() == 'South'


def test_aggregates_groups_order_and_pages(people):
    connection, north, south = people
    groups = qb.select('adapter_person', 'team', qb.named(qb.count(), 'people'), qb.named(qb.sum('age'), 'years'),
                       qb.named(qb.min('age'), 'youngest'), qb.named(qb.max('age'), 'eldest')).group_by('team')
    rows = {row['team']: tuple(row)[1:] for row in connection.run(groups)}
    assert rows == {north: (2, 31, 31, 31), south: (1, 45, 45, 45), None: (1, 22, 22, 22)}
    assert connection.run(qb.select('adapter_person', qb.count('age'))).value() == 3
    assert connection.run(qb.select('adapter_person', qb.coalesce(qb.sum('age'), 0)).where(qb.FALSE)).value() == 0
    ordered = qb.select('adapter_person', 'given')
    assert names(connection, ordered.order_by(qb.asc('age', nulls='first'), 'id')) == ['Bilal', 'dana', 'Aisha', 'Chen']
    assert names(connection, ordered.order_by(qb.desc('age', nulls='last'), 'id')) == ['Chen', 'Aisha', 'dana', 'Bilal']
    assert names(connection, ordered.order_by('-id').limit(2)) == ['dana', 'Chen']
    assert names(connection, ordered.order_by('id').limit(2).offset(1)) == ['Bilal', 'Chen']
    assert names(connection, ordered.order_by('id').offset(3)) == ['dana']
    assert names(connection, qb.select('adapter_person', qb.lower('given')).order_by('id').limit(1)) == ['aisha']


def test_windows_derived_tables_case_and_arithmetic(people):
    connection, north, south = people
    ranked = qb.select(qb.table('adapter_person', 'p'), qb.all_columns('p'),
                       qb.named(qb.over(qb.row_number(), partition_by=['p.team'], order_by=['-p.id']), 'rank'),
                       qb.named(qb.over(qb.count(), partition_by=['p.team']), 'size')).where(qb.not_null('p.team'))
    first = qb.select(qb.from_query(ranked, 'r')).where(qb.eq('r.rank', 1)).order_by('r.team')
    assert [(row['given'], row['size']) for row in connection.run(first)] == [('Bilal', 2), ('Chen', 1)]
    labels = qb.select('adapter_team', qb.case('id', {north: 'N', south: 'S'})).order_by('id')
    assert names(connection, labels) == ['N', 'S']
    connection.run(qb.update('adapter_person', age=qb.col('age') * 2 + 1 - 1).where(given='Aisha'))
    assert connection.run(qb.select('adapter_person', 'age').where(given='Aisha')).value() == 62



def test_distinct_having_searched_case_division_and_text_functions(people):
    connection, north, south = people
    assert names(connection, qb.select('adapter_person', 'team').distinct().where(qb.not_null('team')).order_by('team')) == [north, south]
    busy = qb.select('adapter_person', 'team').where(qb.not_null('team')).group_by('team').having(qb.gt(qb.count(), 1))
    assert names(connection, busy) == [north]
    with pytest.raises(ValueError):
        connection.run(qb.select('adapter_person', 'team').having(qb.gt(qb.count(), 1)))
    totals = connection.run(qb.select('adapter_person', qb.named(qb.count_distinct('team'), 'teams'),
                                      qb.named(qb.sum(qb.when((qb.ge('age', 30), 1), otherwise=0)), 'older'),
                                      qb.named(qb.sum(qb.when((qb.is_null('age'), 0), (qb.lt('age', 30), 1))), 'younger'))).one()
    assert (totals['teams'], totals['older'], totals['younger']) == (2, 2, 1)
    assert connection.run(qb.select('adapter_person', qb.col('age') / 2).where(given='Chen')).value() == 22
    assert connection.run(qb.select('adapter_person', qb.col('age') / -2).where(given='Chen')).value() == -22
    assert connection.run(qb.select('adapter_person', qb.col('age') % 7, (qb.value(0) - qb.col('age')) % 7).where(given='Chen')).one()[:] == (3, -3)
    assert names(connection, qb.select('adapter_person', 'given').where(qb.ends_with('given', 'en')).order_by('id')) == ['Chen']
    assert names(connection, qb.select('adapter_person', 'given').where(qb.ends_with('given', 'EN'))) == []
    assert names(connection, qb.select('adapter_person', 'given').where(qb.ends_with('given', 'xxAisha'))) == []
    shown = connection.run(qb.select('adapter_person', qb.substr('given', 1, 2), -qb.col('age')).where(given='dana')).one()
    assert tuple(shown) == ('da', -22)
    # A column default and a cascading reference in a framework table.
    connection.ensure_table(TableDefinition('evlet_adapter_parent', (Column('label', 'TEXT', required=True, default='none'),), numbered=True))
    connection.ensure_table(TableDefinition('evlet_adapter_child', (
        Column('parent', 'INTEGER', required=True, references='evlet_adapter_parent', on_delete='cascade'),
        Column('state', 'INTEGER', required=True, default=1)), numbered=True))
    parent = connection.run(qb.insert('evlet_adapter_parent').returning_id()).id
    connection.run(qb.insert('evlet_adapter_child', parent=parent))
    assert connection.run(qb.select('evlet_adapter_parent', 'label')).value() == 'none'
    assert connection.run(qb.select('evlet_adapter_child', 'state')).value() == 1
    connection.ensure_table(TableDefinition('evlet_adapter_note', (
        Column('parent', 'INTEGER', references='evlet_adapter_parent', on_delete='set null'),), numbered=True))
    connection.run(qb.insert('evlet_adapter_note', parent=parent))
    connection.run(qb.delete('evlet_adapter_parent').where(id=parent))
    assert connection.run(qb.select('evlet_adapter_child', qb.count())).value() == 0
    assert connection.run(qb.select('evlet_adapter_note', 'parent')).all()[0][0] is None
    connection.drop_table('evlet_adapter_note')
    # A reference to a text key other than "id" (the tenant catalog's domains point at tenants by slug).
    connection.ensure_table(TableDefinition('evlet_adapter_slug', (Column('slug', 'TEXT', primary=True),)))
    connection.ensure_table(TableDefinition('evlet_adapter_alias', (
        Column('slug', 'TEXT', required=True, references='evlet_adapter_slug', referenced_column='slug'),), numbered=True))
    connection.run(qb.insert('evlet_adapter_slug', slug='north'))
    connection.run(qb.insert('evlet_adapter_alias', slug='north'))
    with pytest.raises(IntegrityError):
        connection.run(qb.insert('evlet_adapter_alias', slug='south'))
    connection.drop_table('evlet_adapter_alias')
    connection.drop_table('evlet_adapter_slug')
    # Rebuilding a framework table: copy, drop, rename.
    connection.run(qb.insert('evlet_adapter_parent', label='kept'))
    connection.ensure_table(TableDefinition('evlet_adapter_parent_new', (Column('label', 'TEXT', required=True),), numbered=True))
    connection.run(qb.insert_rows_from('evlet_adapter_parent_new', ('id', 'label'), qb.select('evlet_adapter_parent', 'id', 'label')))
    connection.drop_table('evlet_adapter_child')
    connection.drop_table('evlet_adapter_parent')
    connection.rename_table('evlet_adapter_parent_new', 'evlet_adapter_parent')
    assert not connection.table_exists('evlet_adapter_parent_new') and not connection.table_exists('evlet_adapter_child')
    assert names(connection, qb.select('evlet_adapter_parent', 'label')) == ['kept']

def test_a_hierarchy_below_its_roots(people):
    connection, north, south = people
    third = connection.run(qb.insert('adapter_team', label='East', lead=south).returning_id()).id
    teams = qb.select('adapter_team', 'label').order_by('id')
    assert names(connection, teams.where(qb.in_tree('id', 'adapter_team', 'lead', [north], include_roots=True))) == ['North', 'South', 'East']
    assert names(connection, teams.where(qb.in_tree('id', 'adapter_team', 'lead', [south], include_roots=False))) == ['East']
    assert names(connection, teams.where(qb.in_tree('id', 'adapter_team', 'lead', [], include_roots=True))) == []
    connection.run(qb.update('adapter_team', lead=third).where(id=north))  # a cycle stops instead of looping
    assert len(names(connection, teams.where(qb.in_tree('id', 'adapter_team', 'lead', [north], include_roots=True)))) == 3


def test_writes_report_their_rows_and_upserts_take_one_path(people):
    connection, north, _ = people
    assert connection.run(qb.update('adapter_person', age=1).where(team=north)).rowcount == 2
    assert connection.run(qb.delete('adapter_person').where(given='nobody')).rowcount == 0
    connection.ensure_table(TableDefinition('evlet_adapter_counter', (Column('key', 'TEXT', primary=True),
                                                                      Column('value', 'INTEGER', required=True))))
    bump = qb.insert('evlet_adapter_counter', key='k', value=1).on_conflict('key')
    assert [connection.run(bump.do_update(value=qb.col('evlet_adapter_counter.value') + 1).returning('value')).id for _ in range(3)] == [1, 2, 3]
    ignored = connection.run(bump.do_nothing().returning('value'))
    assert (ignored.id, ignored.rowcount) == (None, 0)
    connection.run(qb.insert('evlet_adapter_counter', key='x', value=5).on_conflict('key').do_update(value=qb.col('excluded.value')))
    copied = qb.insert_rows_from('evlet_adapter_counter', ('key', 'value'), qb.select('adapter_person', 'given', 'age').where(given='Chen'))
    connection.run(copied)
    assert dict(connection.run(qb.select('evlet_adapter_counter', 'key', 'value')).all()) == {'k': 3, 'x': 5, 'Chen': 45}
    assert connection.run(qb.insert('adapter_team', label='Default').returning_id()).id > 0
    # A conflict on any unique key is skipped when no columns are named.
    assert connection.run(qb.insert('evlet_adapter_counter', key='k', value=9).on_conflict().do_nothing()).rowcount == 0
    assert connection.run(qb.select('evlet_adapter_counter', 'value').where(key='k')).value() == 3


def test_framework_table_definitions_hold_their_rules(connection):
    definition = TableDefinition('evlet_adapter_rules', (Column('scope', 'TEXT', required=True), Column('kind', 'TEXT', required=True),
                                                         Column('step', 'INTEGER', required=True), Column('note', 'TEXT')),
                                 primary_key=('scope', 'kind'), unique=(('note',),),
                                 checks=(qb.in_('kind', ('a', 'b')), qb.ge('step', 0)))
    connection.ensure_table(definition)
    connection.ensure_table(definition)
    connection.ensure_index('evlet_adapter_rules_step', 'evlet_adapter_rules', ('step',))
    connection.ensure_index('evlet_adapter_rules_step', 'evlet_adapter_rules', ('step',))
    connection.run(qb.insert('evlet_adapter_rules', scope='s', kind='a', step=0, note="it's"))
    for values in ({'scope': 's', 'kind': 'a', 'step': 1}, {'scope': 't', 'kind': 'c', 'step': 1}, {'scope': 't', 'kind': 'a', 'step': -1},
                   {'scope': 't', 'kind': 'b', 'step': 1, 'note': "it's"}):
        with pytest.raises(IntegrityError):
            connection.run(qb.insert('evlet_adapter_rules', **values))
    assert {index.columns for index in connection.inspect('evlet_adapter_rules').indexes} >= {('step',)}


def test_a_savepoint_undoes_only_its_own_changes(connection):
    connection.create_table('adapter_saved', [Column('note', 'TEXT')])
    connection.run(qb.insert('adapter_saved', note='kept'))
    with pytest.raises(RuntimeError):
        with connection.savepoint('adapter_inner'):
            connection.run(qb.insert('adapter_saved', note='undone'))
            raise RuntimeError('stop')
    with connection.savepoint('adapter_inner'):
        connection.run(qb.insert('adapter_saved', note='also kept'))
    assert names(connection, qb.select('adapter_saved', 'note').order_by('id')) == ['kept', 'also kept']


def test_names_are_checked_and_values_never_become_sql(connection):
    for bad in ('drop table x', 'a"b', 'sqlite_master', '1abc'):
        with pytest.raises(ValueError):
            qb.select(bad)
        with pytest.raises(ValueError):
            qb.col(bad)
    with pytest.raises(TypeError):
        qb.select('client').where('"id" = 1')
    hostile = "'); DROP TABLE client; --"
    assert connection.run(qb.select('client', 'id').where(full_name=hostile)).one() is None
    assert connection.table_exists('client')


# ---------- a database written by Evlet 0.4.3 ----------

FIXTURE = Path(__file__).parent / 'fixtures' / 'sqlite-0.4.3'


def restore(folder):
    """The fixture's four files, restored from their SQL dumps."""
    for name in upgrade_fixture.FILES:
        with sqlite3.connect(folder / (name + '.sqlite3')) as connection:
            connection.executescript((FIXTURE / (name + '.sql')).read_text())
    return folder / 'database.sqlite3'


def contents(path):
    """Every file's schema text and stored rows, to compare before and after."""
    result = {}
    for name in upgrade_fixture.FILES:
        with sqlite3.connect(path.with_name(name + '.sqlite3')) as connection:
            schema = sorted(connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall(), key=repr)
            rows = {table: sorted(connection.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
                    for _, table, _, _ in schema if _ == 'table'}
            result[name] = (schema, rows)
    return result


def test_a_0_4_3_database_opens_unchanged(tmp_path):
    from evlet.framework import upgrade
    path = restore(tmp_path)
    # 0.4.6 adds the state manager's journal columns (framework step 0011); after that the file stays as it is.
    upgraded = Database(path, upgrade_fixture.registry(), migrations=upgrade_fixture.MIGRATIONS)
    assert upgrade(upgraded) == ['0011_state_manager']
    upgraded.close()
    before = contents(path)
    database = Database(path, upgrade_fixture.registry(), migrations=upgrade_fixture.MIGRATIONS)
    with database._connection() as connection:
        stored = connection.run(qb.select('evlet_migrations', 'name', 'checksum').order_by('position')).all()
    assert [tuple(row) for row in stored] == [(item.name, item.checksum()) for item in upgrade_fixture.MIGRATIONS]
    assert database.plan(upgrade_fixture.MIGRATIONS).pending == ()
    app = upgrade_fixture.application(database)
    app.initialize()
    assert database.migrate(upgrade_fixture.MIGRATIONS) == []
    customers = {record.full_name: record for record in database.list(upgrade_fixture.Customer, limit=10)}
    assert customers['Blue Dhow Foods'].phone == 'none'
    assert database.get(upgrade_fixture.Invoice, next(iter(database.list(upgrade_fixture.Invoice))).id).amount == D('1250.75')
    database.close()
    assert contents(path) == before


def test_a_0_4_3_database_keeps_its_guards(tmp_path):
    database = Database(restore(tmp_path), upgrade_fixture.registry(), migrations=upgrade_fixture.MIGRATIONS)
    database.initialize()
    with database._connection() as connection:
        assert connection.is_append_only('move') and connection.has_id_guard('customer')
        assert {c.name: c.required for c in connection.inspect('customer').columns}['phone'] is True
    move = next(iter(database.list(upgrade_fixture.Move)))
    with pytest.raises(ConflictError, match='append only'):
        with database.transaction() as tx:
            tx.connection.run(qb.delete('move').where(id=move.id))
    with pytest.raises((SchemaError, ValidationError, ValueError)):
        database.update(upgrade_fixture.Customer, next(iter(database.list(upgrade_fixture.Customer))).id, phone=None)
    database.close()


@pytest.mark.parametrize('dsn', ['postgresql://evlet@localhost/app', 'postgres://localhost/app', 'host=127.0.0.1 dbname=app'])
def test_a_postgresql_setup_is_told_which_version_still_supports_it(dsn):
    with pytest.raises(ValueError, match='0.4.4 is the last version that supports it'):
        Database(dsn, Registry())
    with pytest.raises(ImportError, match='removed in Evlet 0.4.5'):
        from evlet import PostgreSQLDatabase  # noqa: F401
