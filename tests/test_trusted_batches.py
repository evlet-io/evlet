"""Set-based work in trusted sessions: paged aggregates and long `in` lists, on real SQLite and PostgreSQL.
The HTTP and permission-aware limits (100 values per `in`, 500 groups per aggregate) stay as they were."""

from datetime import date, timedelta
import json
import tracemalloc
from urllib.parse import quote

import pytest

from evlet import Boolean, Count, Database, Date, Integer, Link, Model, Month, Registry, Rule, Select, Sum, Text, ValidationError, Year
from evlet.auth import User
from evlet.postgres import PostgreSQLDatabase
from test_indexes import build
from test_postgres import pg_dsn  # noqa: F401 - fixture

FULL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class Account(Model):
    permissions = FULL
    code = Text(max_length=20, required=True)


class Entry(Model):
    """A ledger of postings: many rows per account and day."""
    account = Link(Account)
    on_date = Date(required=True)
    kind = Select(('credit', 'debit', 'fee'))
    cleared = Boolean(default=False)
    amount = Integer(default=0)
    bucket = Integer(default=0)

    class Meta:
        ledger = True


@pytest.fixture(params=['sqlite', 'postgresql'])
def app(request, tmp_path):
    registry = Registry(Account, Entry)
    if request.param == 'sqlite':
        return build(Database(tmp_path / 'batches.sqlite3', registry))
    return build(PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry))


def accounts(database, count):
    with database.transaction() as tx:
        return [tx.create(Account, code=f'A{index:04d}').id for index in range(count)]


def walk(tx, limit, **query):
    """Every group of a paged aggregate, page by page, and the size of each page."""
    groups, sizes, after = [], [], None
    while page := tx.aggregate(Entry, limit=limit, after=after, **query):
        groups.extend(page)
        sizes.append(len(page))
        after = page[-1]
    return groups, sizes


def ordered(rows, keys):
    """The paged order: each key ascending, empty values first."""
    return sorted(rows, key=lambda row: tuple((row[key] is not None, row[key]) for key in keys))


# Paged aggregates

def test_paging_returns_every_group_exactly_once_in_a_stable_order(app):
    app, _ = app
    database = app.database
    ids = accounts(database, 3)
    start = date(2026, 1, 1)
    rows = []
    # One busy account has 700 days on its own, so no filter on the account keeps a page under 500 groups.
    for day in range(700):
        rows.append({'account': ids[0], 'on_date': start + timedelta(days=day), 'kind': ('credit', 'debit', None)[day % 3], 'amount': day})
        rows.append({'account': ids[0], 'on_date': start + timedelta(days=day), 'kind': ('credit', 'debit', None)[day % 3], 'amount': 1})
    for day in range(40):
        rows.append({'account': ids[1], 'on_date': start + timedelta(days=day), 'kind': 'fee', 'amount': 5})
        rows.append({'account': None, 'on_date': start + timedelta(days=day), 'kind': None, 'amount': 2})
    with database.transaction() as tx:
        tx.insert_many(Entry, rows)
    expected = {}
    for row in rows:
        key = (row['kind'], row['account'], row['on_date'])
        total, count = expected.get(key, (0, 0))
        expected[key] = (total + row['amount'], count + 1)
    query = {'measures': {'total': Sum('amount'), 'rows': Count()}, 'group_by': ('kind', 'account', 'on_date')}
    with database.transaction() as tx:
        with pytest.raises(ValidationError, match='more than 500 groups'):
            tx.aggregate(Entry, **query)
        groups, sizes = walk(tx, 97, **query)
        again, _ = walk(tx, 250, **query)
    assert len(expected) > 500 and len(groups) == len(expected)
    assert {(item['kind'], item['account'], item['on_date']): (item['total'], item['rows']) for item in groups} == expected
    assert max(sizes) == 97 and sum(sizes) == len(expected)
    # Stable: the same order whatever the page size, keys ascending with empty values first.
    assert groups == again == ordered(groups, ('kind', 'account', 'on_date'))
    assert groups[0]['kind'] is None and groups[0]['account'] is None


def test_paging_by_date_parts_booleans_and_filters(app):
    app, _ = app
    database = app.database
    [account] = accounts(database, 1)
    with database.transaction() as tx:
        tx.insert_many(Entry, [{'account': account, 'on_date': date(2020 + index % 5, 1 + index % 12, 1), 'cleared': index % 2 == 0,
                                'amount': index} for index in range(240)])
        query = {'measures': {'rows': Count()}, 'group_by': (Year('on_date'), Month('on_date'), 'cleared'),
                 'filters': {'account': account}}
        whole = tx.aggregate(Entry, **query)
        groups, sizes = walk(tx, 1, **query)
    # Database.aggregate pages the same way.
    first = database.aggregate(Entry, limit=7, **query)
    assert groups == whole and set(sizes) == {1}
    assert first == whole[:7]
    assert isinstance(groups[0]['on_date__year'], int)


def test_paging_arguments_are_checked(app):
    app, _ = app
    with app.database.transaction() as tx:
        query = {'measures': {'rows': Count()}, 'group_by': 'account'}
        with pytest.raises(ValidationError, match='after together with limit'):
            tx.aggregate(Entry, after={'account': None}, **query)
        for limit in (0, 10_001, '5', True):
            with pytest.raises(ValidationError, match='between 1 and 10000'):
                tx.aggregate(Entry, limit=limit, **query)
        with pytest.raises(ValidationError, match='Paging needs group_by'):
            tx.aggregate(Entry, measures={'rows': Count()}, limit=10)
        for after in ({}, {'other': 1}, [1], {'account': 'not an id'}):
            with pytest.raises(ValidationError) as error:
                tx.aggregate(Entry, limit=10, after=after, **query)
            assert 'after' in error.value.errors
        for after in ({'on_date__year': '2026'}, {'on_date__year': 0}):
            with pytest.raises(ValidationError, match='year'):
                tx.aggregate(Entry, measures={'rows': Count()}, group_by=Year('on_date'), limit=5, after=after)
        with pytest.raises(ValidationError, match='YYYY-MM'):
            tx.aggregate(Entry, measures={'rows': Count()}, group_by=Month('on_date'), limit=5, after={'on_date__month': "2026-01' OR 1=1"})


def test_walking_many_groups_keeps_memory_bounded(app):
    app, _ = app
    database = app.database
    with database.transaction() as tx:
        tx.connection.execute('INSERT INTO "entry" ("on_date", "cleared", "amount", "bucket") '
                              'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 20000) '
                              "SELECT '2026-01-01', 0, 1, i FROM n")
    query = {'measures': {'total': Sum('amount')}, 'group_by': 'bucket'}
    with database.transaction() as tx:
        tx.aggregate(Entry, limit=10, **query)  # warm up statement caches and imports
        tracemalloc.start()
        seen, after, largest = 0, None, 0
        try:
            while page := tx.aggregate(Entry, limit=500, after=after, **query):
                seen += len(page)
                largest = max(largest, len(page))
                after = page[-1]
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    assert seen == 20_000 and largest == 500
    # Twenty thousand groups held at once would take several megabytes; a page of 500 stays well under one.
    assert peak < 1_500_000, peak


# Long `in` lists

def test_trusted_sessions_take_an_in_list_of_10000_values(app):
    app, _ = app
    database = app.database
    ids = accounts(database, 3)
    with database.transaction() as tx:
        tx.insert_many(Entry, [{'account': ids[index % 3], 'on_date': date(2026, 1, 1), 'bucket': index} for index in range(10_000)])
        entry_ids = [row['id'] for row in tx.connection.execute('SELECT "id" FROM "entry" ORDER BY "id"').fetchall()]
        buckets = list(range(0, 20_000, 2))
        assert len(entry_ids) == len(buckets) == 10_000
        assert tx.count(Entry, filters={'id__in': entry_ids}) == 10_000
        assert tx.count(Entry, filters={'bucket__in': buckets}) == 5_000
        assert tx.count(Entry, filters={'bucket__nin': buckets}) == 5_000
        page = tx.list(Entry, filters={'id__in': entry_ids[5_000:]}, order_by=('id',), limit=100)
        assert [row.id for row in page] == entry_ids[5_000:5_100]
        # A Link `in` with thousands of ids, most of them unknown, in an aggregate.
        [row] = tx.aggregate(Entry, measures={'rows': Count()}, filters={'account__in': [*ids[:2], *range(10**6, 10**6 + 9_998)]})
        assert row == {'rows': 6_667}
        groups = tx.aggregate(Entry, measures={'rows': Count()}, group_by='account', filters={'id__in': entry_ids}, limit=2)
        assert [group['rows'] for group in groups] == [3_334, 3_333]
    assert database.count(Entry, filters={'id__in': entry_ids}) == 10_000
    with database.transaction() as tx:
        # Values are still validated and bound as parameters, one at a time.
        with pytest.raises(ValidationError, match='Use isnull'):
            tx.count(Entry, filters={'bucket__in': [*buckets[:500], None]})
        with pytest.raises(ValidationError):
            tx.count(Entry, filters={'bucket__in': [*buckets[:500], "1) OR (1=1"]})
        # Past 30,000 values in one query, split the list (itertools.batched).
        with pytest.raises(ValidationError, match='at most 30000'):
            tx.count(Entry, filters={'bucket__in': list(range(20_000)), 'id__nin': list(range(10_001))})
        assert tx.count(Entry, filters={'bucket__in': list(range(20_000)),
                                        'id__nin': [*entry_ids[:5_000], *range(10**7, 10**7 + 5_000)]}) == 5_000


def test_http_and_context_keep_the_public_limits(app):
    app, session = app
    database = app.database
    ids = accounts(database, 501)
    ten_thousand = [*ids, *range(10**6, 10**6 + 10_000 - len(ids))]
    for values in (ids[:101], ten_thousand):
        filters = quote(json.dumps({'id__in': values}))
        status, payload = session('GET', f'/api/account?filters={filters}')[:2]
        assert status == 422, payload
        status, payload = session('GET', f'/api/account/aggregate?measures=n:count&filters={filters}')[:2]
        assert status == 422, payload
    filters = quote(json.dumps({'id__in': ids[:101]}))
    assert 'at most 100 values' in json.dumps(session('GET', f'/api/account?filters={filters}')[1])
    assert session('GET', f'/api/account?filters={quote(json.dumps({"id__in": ids[:100]}))}')[0] == 200
    # More than 500 groups is still an error over HTTP, and the route takes no paging parameters.
    status, payload = session('GET', '/api/account/aggregate?measures=n:count&group_by=code')[:2]
    assert status == 422 and 'more than 500 groups' in json.dumps(payload)
    assert session('GET', '/api/account/aggregate?measures=n:count&group_by=code&limit=10')[0] == 422
    with app.business.context(User(1, 'admin', 'admin')) as context:
        with pytest.raises(ValidationError, match='at most 100 values'):
            context.list(Account, filters={'id__in': ids[:101]})
        with pytest.raises(ValidationError, match='at most 100 values'):
            context.aggregate(Account, measures={'n': Count()}, filters={'id__in': ten_thousand})
        with pytest.raises(ValidationError, match='more than 500 groups'):
            context.aggregate(Account, measures={'n': Count()}, group_by='code')
        with pytest.raises(TypeError):
            context.aggregate(Account, measures={'n': Count()}, group_by='code', limit=10)
