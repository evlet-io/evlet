"""List pages count up to a cap (PERF-12) and a search scans once for a short page (PERF-09)."""

from contextlib import contextmanager

import pytest

from evlet import Database, Model, Registry, Rule, Text
from evlet.database import LIST_COUNT_CAP, list_count_cap, page_count
from evlet import qb
from test_business import client
from test_rules_types import start


class Item(Model):
    class Meta:
        search_fields = ('title',)
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    title = Text(required=True)


@pytest.fixture
def listing(tmp_path):
    db = Database(tmp_path / 'db.sqlite3', Registry(Item))
    app = start(db)
    with db.transaction() as tx:
        for index in range(30):
            tx.create(Item, title=f'{"box" if index % 3 == 0 else "tape"} {index}')
    db.count_cap = 12
    return db, client(app)


def get(admin, url):
    status, body, _ = admin('GET', url)
    assert status == 200, body
    return body


def test_a_list_past_the_cap_reads_the_cap_and_says_so(listing):
    _, admin = listing
    body = get(admin, '/api/item?limit=5')
    assert (body['total'], body['total_capped'], len(body['items'])) == (12, True, 5)


def test_a_page_past_the_cap_counts_at_least_to_its_end(listing):
    _, admin = listing
    assert (lambda body: (body['total'], body['total_capped']))(get(admin, '/api/item?limit=5&offset=15')) == (20, True)
    # The last full page: nothing follows it, so the total is exact again.
    assert (lambda body: (body['total'], body['total_capped']))(get(admin, '/api/item?limit=5&offset=25')) == (30, False)


def test_a_count_under_the_cap_is_exact(listing):
    _, admin = listing
    body = get(admin, '/api/item?limit=5&q=box')
    assert (body['total'], body['total_capped']) == (10, False)


def test_a_cap_of_zero_counts_exactly(listing):
    db, admin = listing
    db.count_cap = 0
    body = get(admin, '/api/item?limit=5')
    assert (body['total'], body['total_capped']) == (30, False)


def test_a_short_page_is_its_own_total_without_a_count(listing):
    db, admin = listing
    statements = []
    original = db._connection

    @contextmanager
    def traced():  # every statement the list route runs, from SQLite itself
        with original() as connection:
            connection.set_trace_callback(statements.append)
            yield connection
    db._connection = traced
    body = get(admin, '/api/item?q=box')
    db._connection = original
    assert (body['total'], body['total_capped']) == (10, False)
    searched = [sql for sql in statements if 'instr(' in sql]
    assert len(searched) == 1, searched  # the page itself: no second scan for the count


def test_page_count_bounds_the_scan(listing):
    db, _ = listing
    query = qb.select(qb.table('item', 'record'), qb.col('record.id'))
    with db._connection() as connection:
        assert page_count(connection, query, limit=5, offset=0, shown=5, cap=12) == (12, True)
        assert page_count(connection, query, limit=5, offset=0, shown=5, cap=30) == (30, False)
        assert page_count(connection, query, limit=5, offset=0, shown=5, cap=None) == (30, False)
        assert page_count(connection, query, limit=50, offset=0, shown=30, cap=12) == (30, False)


def test_the_cap_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv('EVLET_LIST_COUNT_CAP', raising=False)
    assert list_count_cap() == LIST_COUNT_CAP == 10_000
    monkeypatch.setenv('EVLET_LIST_COUNT_CAP', '0')
    assert list_count_cap() == 0
    monkeypatch.setenv('EVLET_LIST_COUNT_CAP', ' 500 ')
    assert Database(tmp_path / 'db.sqlite3', Registry(Item)).count_cap == 500
    for bad in ('-1', 'ten', '1e3', '1000000000'):
        monkeypatch.setenv('EVLET_LIST_COUNT_CAP', bad)
        with pytest.raises(ValueError, match='EVLET_LIST_COUNT_CAP'):
            list_count_cap()
