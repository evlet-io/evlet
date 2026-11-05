"""Owned tables (Table): row limits, row changes, paging and bounded SQL, on real SQLite and PostgreSQL."""

from contextlib import contextmanager

import pytest

from evlet import (Application, Database, PostgreSQLDatabase, LocalAuth, Registry, Model, Child, Table, Decimal, Currency,
                   Integer, Text, Link, Rule, ValidationError)
from evlet.business import ChildTable
from evlet.calc import CalcError, Document, check_plan, fingerprint, round
from test_business import http, client, PASSWORD
from test_postgres import pg_dsn  # noqa: F401  (fixture)
from finite_fixtures import FiniteMeta

FULL = Rule(('read', 'create', 'update', 'delete'))
ALL = {'admin': FULL, 'editor': FULL, 'viewer': Rule(('read',))}


class Product(Model):
    permissions = ALL
    title = Text(default='product')


class Part(Child):
    hours = Decimal(default=0, minimum='0')


class Line(Child):
    product = Link(Product)
    qty = Decimal(default=1, minimum='0')
    rate = Currency(default=0)
    amount = Currency(compute=round(qty * rate, 2))
    parts = Table(Part, max_rows=9)
    hours = Decimal(compute=parts.sum('hours'))

    def validate(self):
        if self.qty == 13:
            raise ValidationError({'qty': 'Thirteen is not allowed'})


class Order(Model):
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='order')
    lines = Table(Line, min_rows=1, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))
    line_count = Integer(compute=lines.count())


class Pair(Model):
    Meta = FiniteMeta
    permissions = ALL
    rows = Table(Part, min_rows=2)


class Counting(Database):
    """Real SQLite that counts the statements each connection runs."""
    statements = 0

    @contextmanager
    def _connection(self):
        with super()._connection() as connection:
            connection.set_trace_callback(self._count)
            yield connection

    def _count(self, statement):
        type(self).statements += 1


def build(database, **options):
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True), **options)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


@pytest.fixture(params=['sqlite', 'postgresql'])
def make_app(request, tmp_path):
    def make(*models, counting=False, **options):
        if request.param == 'sqlite':
            return build((Counting if counting else Database)(tmp_path / 'lines.sqlite3', Registry(*models)), **options)
        return build(PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), Registry(*models)), **options)
    make.backend = request.param
    return make


def counted(c, *args, **kwargs):
    Counting.statements = 0
    result = c(*args, **kwargs)
    return result, Counting.statements


def lines(count, start=0, **values):
    return [{'_key': f'l{index}', 'qty': '1', 'rate': '2.50', **values} for index in range(start, start + count)]


def edit(c, url, body):
    headers = {b'if-match': c('GET', url)[2][b'etag']}
    return c('PATCH', url, body, headers=headers)


def page(c, url, name='lines', **query):
    suffix = '&'.join(f'{key}={value}' for key, value in query.items())
    return c('GET', f'{url}/lines/{name}' + ('?' + suffix if suffix else ''))


def test_table_declares_row_limits_and_rejects_conflicts():
    assert (Table(Part).min_rows, Table(Part).max_rows, Table(Part).required) == (0, 100, False)
    assert (Table(Part, required=True).min_rows, Table(Part, required=True, min_rows=3).min_rows) == (1, 3)
    assert Table(Part, min_rows=2, max_rows=1000).required
    for options in ({'max_rows': 1001}, {'max_rows': 0}, {'min_rows': -1}, {'min_rows': 5, 'max_rows': 4},
                    {'required': True, 'min_rows': 0}, {'max_rows': True}, {'min_rows': '1'}):
        with pytest.raises(ValueError):
            Table(Part, **options)

    class Plain(Model):
        permissions = ALL
        rows = Table(Part, max_rows=101)
    with pytest.raises(ValueError, match='finite-v1 root'):
        Registry(Plain)

    class Small(Model):
        permissions = ALL
        rows = Table(Part, max_rows=40, min_rows=2)
    Registry(Small)  # Legacy roots may lower the limit.

    plan = Registry(Order, Product).plans[Order]
    assert (plan['limits']['collection_rows'], plan['limits']['document_rows']) == (1000, 5000)
    assert {key: (entry.get('min_rows'), entry.get('max_rows')) for key, entry in plan['tables'].items()} == \
        {'$root': (None, None), 'lines[*]': (1, 1000), 'lines[*].parts[*]': (0, 9)}
    for value in (1001, -1, 2.0, None):
        broken = {**plan, 'tables': {**plan['tables'], 'lines[*]': {**plan['tables']['lines[*]'], 'max_rows': value}}}
        broken['model_schema_hash'] = fingerprint(broken)
        with pytest.raises(CalcError, match='row limits'):
            check_plan(broken)
    with pytest.raises(CalcError) as error:
        Document(plan, {'children': {'lines': [{'children': {'parts': [{} for _ in range(10)]}}]}})
    assert error.value.code == 'CALCULATION_LIMIT_EXCEEDED'


def test_min_and_max_rows_are_enforced_on_save(make_app):
    app = make_app(Order, Product, Pair)
    c = client(app)
    status, error, _ = c('POST', '/api/order', {'children': {'lines': []}})
    assert status == 422 and error['fields'] == {'lines': 'Add at least one row'}
    status, error, _ = c('POST', '/api/order', {'children': {'lines': [{'children': {'parts': [{} for _ in range(10)]}}]}})
    assert status == 422 and list(error['fields'].values()) == ['Use at most 9 rows'] and next(iter(error['fields'])).endswith('.parts')
    status, error, _ = c('POST', '/api/order', {'children': {'lines': lines(1001)}})
    assert status == 422 and error['fields'] == {'lines': 'Use at most 1000 rows'}
    status, order, _ = c('POST', '/api/order', {'children': {'lines': lines(1)}})
    assert status == 201, order
    url = f"/api/order/{order['id']}"
    only = order['children']['lines'][0]['id']
    status, error, _ = edit(c, url, {'children': {'lines': {'delete': [only]}}})
    assert status == 422 and error['fields'] == {'lines': 'Add at least one row'}
    status, error, _ = c('POST', '/api/pair', {'children': {'rows': [{}]}})
    assert status == 422 and error['fields'] == {'rows': 'Add at least 2 rows'}
    assert c('POST', '/api/pair', {'children': {'rows': [{}, {}]}})[0] == 201


def test_thousand_row_document_pages_and_loads_with_bounded_statements(make_app):
    app = make_app(Order, Product, counting=True)
    c = client(app)
    product = c('POST', '/api/product', {})[1]['id']
    # A thousand rows with a 13-character Link id each pass the 64 KiB body limit, so they go in two
    # requests, as docs/finite-calculations.md advises for large collections.
    status, order, _ = c('POST', '/api/order', {'title': 'big', 'children': {'lines': lines(500, product=product)}})
    assert status == 201, order
    first = order['_row_ids']
    status, order, _ = edit(c, f"/api/order/{order['id']}", {'children': {'lines': {'create': lines(500, 500, product=product)}}})
    assert status == 200, order
    order['_row_ids'] = {**first, **order['_row_ids']}
    assert (order['total'], order['line_count']) == ('2500.00', 1000)
    assert len(order['_row_ids']) == 1000
    assert len(order['children']['lines']) == 100
    assert order['children_pages'] == {'lines': {'total': 1000, 'limit': 100, 'partial': True}}
    url = f"/api/order/{order['id']}"

    (status, detail, headers), statements = counted(c, 'GET', url)
    assert status == 200 and len(detail['children']['lines']) == 100 and detail['children']['lines'][0]['product'] == product
    assert detail['children_pages']['lines']['total'] == 1000
    if make_app.backend == 'sqlite':
        assert statements <= 12, statements  # 607 for 100 rows before batching; one reads the config version, one names who wrote it.

    status, rows, _ = page(c, url, limit=100, offset=900)
    assert status == 200 and rows['total'] == 1000 and (rows['limit'], rows['offset']) == (100, 900)
    ids = [row['id'] for row in rows['items']]
    assert ids == [order['_row_ids'][f'l{index}'] for index in range(900, 1000)]
    assert 'evlet_owner' not in rows['items'][0] and 'evlet_position' not in rows['items'][0] and rows['items'][0]['children'] == {'parts': []}

    (status, saved, _), statements = counted(c, 'PATCH', url, {'title': 'renamed'}, headers={b'if-match': headers[b'etag']})
    assert status == 200 and saved['title'] == 'renamed' and saved['total'] == '2500.00'
    if make_app.backend == 'sqlite':
        # 2,021 for 100 rows before batching; two of these open and release the operation savepoint,
        # one names who created and changed the record, and one removes the saver's unsaved changes
        # (evlet/unsaved.py).
        assert statements <= 36, statements

    last = order['_row_ids']['l999']
    headers = {b'if-match': c('GET', url)[2][b'etag']}
    (status, saved, _), statements = counted(c, 'PATCH', url, {'children': {'lines': {'update': [{'id': last, 'qty': '3'}]}}}, headers=headers)
    assert status == 200 and saved['total'] == '2505.00' and saved['line_count'] == 1000
    if make_app.backend == 'sqlite':
        assert statements <= 38, statements
    assert page(c, url, limit=1, offset=999)[1]['items'][0]['qty'] == '3.0000'

    status, error, _ = edit(c, url, {'children': {'lines': {'create': [{'qty': '1'}]}}})
    assert status == 422 and error['fields'] == {'lines': 'Use at most 1000 rows'}


def test_nested_rows_load_with_a_bounded_statement_count(make_app):
    app = make_app(Order, Product, counting=True)
    c = client(app)
    body = [{'_key': f'l{index}', 'qty': '1', 'rate': '1', 'children': {'parts': [{'hours': '1'} for _ in range(9)]}} for index in range(100)]
    status, order, _ = c('POST', '/api/order', {'children': {'lines': body}})
    assert status == 201, order
    url = f"/api/order/{order['id']}"
    (status, detail, headers), statements = counted(c, 'GET', url)
    assert status == 200 and len(detail['children']['lines']) == 100 and 'children_pages' not in detail
    assert all(len(line['children']['parts']) == 9 and line['hours'] == '9.0000' for line in detail['children']['lines'])
    if make_app.backend == 'sqlite':
        assert statements <= 10, statements  # 2,407 before batching.
    (status, saved, _), statements = counted(c, 'PATCH', url, {'title': 'nested'}, headers={b'if-match': headers[b'etag']})
    assert status == 200, saved
    if make_app.backend == 'sqlite':
        assert statements <= 32, statements  # 9,221 before batching.
    first = detail['children']['lines'][0]
    status, parts, _ = page(c, url, f"lines/{first['id']}/parts", limit=5)
    assert status == 200 and parts['total'] == 9 and [part['id'] for part in parts['items']] == [part['id'] for part in first['children']['parts'][:5]]


def test_mixed_row_changes_in_one_request_keep_order_totals_and_row_ids(make_app):
    app = make_app(Order, Product)
    c = client(app)
    status, order, _ = c('POST', '/api/order', {'children': {'lines': lines(5)}})
    assert status == 201 and order['total'] == '12.50'
    url, ids = f"/api/order/{order['id']}", order['_row_ids']
    a, b, c_, d, e = (ids[f'l{index}'] for index in range(5))

    body = {'children': {'lines': {'create': [{'_key': 'n1', 'qty': '2', 'rate': '1'}, {'_key': 'n2', 'qty': '4', 'rate': '1'}],
                                   'update': [{'id': b, 'qty': '3'}], 'delete': [d],
                                   'order': ['n1', e, c_, a]}}}
    status, saved, _ = edit(c, url, body)
    assert status == 200, saved
    n1, n2 = saved['_row_ids']['n1'], saved['_row_ids']['n2']
    # Listed rows (n1, e, c, a) take the places they held among a, c, e, n1; b keeps its place and n2 appends.
    assert [row['id'] for row in saved['children']['lines']] == [n1, b, e, c_, a, n2]
    assert (saved['total'], saved['line_count']) == ('21.00', 6)
    assert [row['id'] for row in page(c, url)[1]['items']] == [n1, b, e, c_, a, n2]

    # Unmentioned rows keep their values; a reorder alone changes no values.
    status, saved, _ = edit(c, url, {'children': {'lines': {'order': [n2, n1]}}})
    assert status == 200 and [row['id'] for row in saved['children']['lines']] == [n2, b, e, c_, a, n1]
    assert saved['total'] == '21.00'
    status, saved, _ = edit(c, url, {'children': {'lines': {'delete': [n2, n1]}}})
    assert status == 200 and (saved['total'], saved['line_count']) == ('15.00', 4)
    status, saved, _ = edit(c, url, {'title': 'kept', 'children': {'lines': {'create': [{'qty': '1', 'rate': '1'}]}}})
    assert status == 200 and saved['children']['lines'][-1]['qty'] == '1.0000' and saved['total'] == '16.00'
    assert len(saved['_row_ids']) == 1 and saved['title'] == 'kept'

    # The full replacement list is still accepted at the same time.
    current = [row['id'] for row in saved['children']['lines']]
    status, saved, _ = edit(c, url, {'children': {'lines': [{'id': current[-1]}, {'id': current[0], 'qty': '2'}]}})
    assert status == 200 and [row['id'] for row in saved['children']['lines']] == [current[-1], current[0]]
    assert (saved['total'], saved['line_count']) == ('6.00', 2)


def test_nested_row_changes_and_parent_totals(make_app):
    app = make_app(Order, Product)
    c = client(app)
    body = {'children': {'lines': [{'_key': 'a', 'qty': '1', 'rate': '1', 'children': {'parts': [{'_key': 'p1', 'hours': '1'}, {'_key': 'p2', 'hours': '2'}]}}]}}
    status, order, _ = c('POST', '/api/order', body)
    assert status == 201 and order['children']['lines'][0]['hours'] == '3.0000'
    url, ids = f"/api/order/{order['id']}", order['_row_ids']
    change = {'children': {'lines': {
        'update': [{'id': ids['a'], 'children': {'parts': {'create': [{'_key': 'p3', 'hours': '5'}], 'delete': [ids['p1']], 'order': ['p3', ids['p2']]}}}],
        'create': [{'_key': 'b', 'qty': '2', 'rate': '1', 'children': {'parts': {'create': [{'_key': 'q1', 'hours': '4'}]}}},
                   {'_key': 'c', 'qty': '1', 'rate': '3', 'children': {'parts': [{'_key': 'r1', 'hours': '6'}]}}]}}}
    status, saved, _ = edit(c, url, change)
    assert status == 200, saved
    first, second, third = saved['children']['lines']
    assert [part['id'] for part in first['children']['parts']] == [saved['_row_ids']['p3'], ids['p2']]
    assert (first['hours'], second['hours'], third['hours']) == ('7.0000', '4.0000', '6.0000')
    assert (saved['total'], saved['line_count']) == ('6.00', 3)
    status, saved, _ = edit(c, url, {'children': {'lines': {'update': [{'id': ids['a'], 'qty': '4', 'children': {'parts': {'update': [{'id': ids['p2'], 'hours': '1'}]}}}]}}})
    assert status == 200 and saved['children']['lines'][0]['hours'] == '6.0000' and saved['total'] == '9.00'


def test_any_row_error_rolls_back_the_whole_change(make_app):
    app = make_app(Order, Product)
    c = client(app)
    status, order, _ = c('POST', '/api/order', {'children': {'lines': lines(3)}})
    url, ids = f"/api/order/{order['id']}", order['_row_ids']
    before = c('GET', url)[1]
    for change, field in [
            ({'create': [{'_key': 'ok', 'qty': '1'}, {'_key': 'bad', 'qty': '13'}], 'delete': [ids['l0']]}, 'lines.bad.qty'),
            ({'update': [{'id': ids['l1'], 'qty': '-1'}], 'delete': [ids['l2']], 'order': [ids['l1'], ids['l0']]}, f"lines.{ids['l1']}.qty"),
            ({'update': [{'id': ids['l0'], 'children': {'parts': {'create': [{'_key': 'p', 'hours': '-2'}]}}}], 'delete': [ids['l2']]},
             f"lines.{ids['l0']}.parts.p.hours")]:
        status, error, _ = edit(c, url, {'title': 'changed', 'children': {'lines': change}})
        assert status == 422 and field in error['fields'], error
        assert c('GET', url)[1] == before

    for change, message in [
            ({'insert': []}, 'only create'), ({'create': {}}, 'lists'), ({'create': [{'id': ids['l0']}]}, 'take no id'),
            ({'update': [{'id': 999999}]}, 'belong'), ({'delete': [ids['l0'], ids['l0']]}, 'belong'),
            ({'update': [{'id': ids['l0']}], 'delete': [ids['l0']]}, 'belong'), ({'order': [ids['l0'], ids['l0']]}, 'once'),
            ({'order': ['missing']}, 'Order lists'), ({'delete': [ids['l0']], 'order': [ids['l0']]}, 'deleted'),
            ({'update': ['x']}, 'object'), ({'delete': ['1']}, 'belong')]:
        status, error, _ = edit(c, url, {'children': {'lines': change}})
        assert status == 422 and message in error['fields']['lines'], (change, error)
    assert c('GET', url)[1] == before


def test_row_scope_hidden_rows_and_field_rules_on_changes_and_pages(make_app):
    class Note(Child):
        permissions = {'admin': FULL, 'editor': Rule(('read', 'create', 'update'))}
        text = Text(default='')

    class Area(Child):
        permissions = {'admin': FULL, 'editor': Rule(('read', 'create', 'update', 'delete'), read_fields=('region', 'amount'),
                                                     scope=lambda user: {'region': 'east'})}
        region = Text(default='east')
        amount = Currency(default=0)
        secret = Text(default='hidden value')
        notes = Table(Note)

    class Territory(Model):
        Meta = FiniteMeta
        permissions = {'admin': FULL, 'editor': FULL}
        title = Text(default='t')
        areas = Table(Area, max_rows=300)

    app = make_app(Territory)
    admin = client(app)
    assert admin('POST', '/api/auth/users', {'username': 'editor', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    editor = client(app, 'editor')
    rows = [{'_key': f'r{index}', 'region': 'west' if index % 5 == 0 else 'east', 'amount': str(index)} for index in range(150)]
    rows[1]['children'] = {'notes': [{'text': 'kept'}]}
    status, territory, _ = admin('POST', '/api/territory', {'children': {'areas': rows}})
    assert status == 201, territory
    url, ids = f"/api/territory/{territory['id']}", territory['_row_ids']
    west = [ids[f'r{index}'] for index in range(0, 150, 5)]
    east = [ids[f'r{index}'] for index in range(150) if index % 5]

    status, detail, _ = editor('GET', url)
    assert [row['id'] for row in detail['children']['areas']] == east[:100]
    assert detail['children_pages'] == {'areas': {'total': 120, 'limit': 100, 'partial': True}}
    assert set(detail['children']['areas'][0]) == {'id', 'region', 'amount', 'children'}
    status, result, _ = page(editor, url, 'areas', offset=100)
    assert status == 200 and result['total'] == 120 and [row['id'] for row in result['items']] == east[100:]
    assert 'secret' not in result['items'][0]
    assert page(admin, url, 'areas', limit=1)[1]['total'] == 150
    status, notes, _ = page(editor, url, f'areas/{ids["r1"]}/notes')
    assert status == 200 and notes['total'] == 1
    assert page(editor, url, f'areas/{west[0]}/notes')[0] == 404
    assert page(editor, url, f'areas/{east[0]}/missing')[0] == 404

    # Hidden rows cannot be targeted; a delete needs delete permission on the whole subtree.
    headers = {b'if-match': editor('GET', url)[2][b'etag']}
    for change in ({'update': [{'id': west[0], 'amount': '1'}]}, {'delete': [west[1]]}, {'order': [west[2], east[0]]}):
        status, error, _ = editor('PATCH', url, {'children': {'areas': change}}, headers=headers)
        assert status == 422 and 'belong' in error['fields']['areas'], error
    status, _, _ = editor('PATCH', url, {'children': {'areas': {'delete': [ids['r1']]}}}, headers=headers)
    assert status == 403
    status, error, _ = editor('PATCH', url, {'children': {'areas': {'create': [{'region': 'west'}]}}}, headers=headers)
    assert status == 404  # A created row must stay inside the editor's row scope.

    status, saved, _ = editor('PATCH', url, {'children': {'areas': {
        'create': [{'_key': 'new', 'amount': '9'}], 'delete': [ids['r2']], 'order': [east[2], 'new', east[0]]}}}, headers=headers)
    assert status == 200, saved
    _, full, _ = admin('GET', url)
    order = [row['id'] for row in full['children']['areas']] + [row['id'] for row in page(admin, url, 'areas', offset=100)[1]['items']]
    # Listed rows take the places they held (the new row first appends); hidden west rows stay put.
    new = saved['_row_ids']['new']
    expected = [ids[f'r{index}'] for index in range(150) if index != 2] + [new]
    listed = [east[2], new, east[0]]
    for slot, row in zip(sorted(expected.index(row) for row in listed), listed):
        expected[slot] = row
    assert order == expected
    assert all(row['secret'] == 'hidden value' for row in full['children']['areas'])


def test_legacy_child_table_keeps_working_with_row_changes_and_paging(make_app):
    class Memo(Model):
        permissions = ALL
        title = Text(default='memo')

    class MemoItem(Model):
        permissions = ALL
        memo = Link(Memo, required=True)
        body = Text(default='')

    app = make_app(Memo, MemoItem, children=(ChildTable(Memo, 'items', MemoItem, 'memo', 'Items'),))
    c = client(app)
    status, memo, _ = c('POST', '/api/memo', {'children': {'items': [{'body': 'one'}, {'body': 'two'}]}})
    assert status == 201, memo
    url = f"/api/memo/{memo['id']}"
    first, second = (row['id'] for row in memo['children']['items'])
    status, saved, _ = edit(c, url, {'children': {'items': {'create': [{'body': 'three'}], 'update': [{'id': first, 'body': 'uno'}], 'delete': [second]}}})
    assert status == 200 and [row['body'] for row in saved['children']['items']] == ['uno', 'three']
    status, error, _ = edit(c, url, {'children': {'items': {'order': [first]}}})
    assert status == 422 and 'no stored order' in error['fields']['items']
    status, rows, _ = page(c, url, 'items')
    assert status == 200 and [row['body'] for row in rows['items']] == ['uno', 'three'] and 'memo' not in rows['items'][0]


def test_paging_endpoint_validates_its_route_and_query(make_app):
    app = make_app(Order, Product)
    c = client(app)
    status, order, _ = c('POST', '/api/order', {'children': {'lines': lines(3)}})
    url = f"/api/order/{order['id']}"
    for query in ('limit=0', 'limit=101', 'offset=-1', 'limit=x', 'other=1', 'limit=1&limit=2'):
        assert c('GET', f'{url}/lines/lines?{query}')[0] == 422, query
    assert c('GET', f'{url}/lines/missing')[0] == 404
    assert c('GET', f'{url}/lines/lines/{order["_row_ids"]["l0"]}')[0] == 404
    assert c('GET', f'{url}/lines/lines/0/parts')[0] == 404
    assert c('GET', f'/api/order/999999/lines/lines')[0] == 404
    assert c('POST', f'{url}/lines/lines', {})[0] == 405
    status, other, _ = c('POST', '/api/order', {'children': {'lines': lines(1)}})
    assert c('GET', f"{url}/lines/lines/{other['_row_ids']['l0']}/parts")[0] == 404
    assert page(c, url, limit=2)[1]['items'][1]['id'] == order['_row_ids']['l1']


def test_recompute_reads_every_row_of_a_large_table(tmp_path):
    from evlet import AddIndex, Migration, Recompute
    from evlet.calc import D, F

    def models(factor):
        class BigLine(Child):
            qty = Decimal(default=1)
            amount = Currency(compute=F('qty') * D(factor))

        class Big(Model):
            Meta = FiniteMeta
            storage_name = 'big'
            permissions = ALL
            title = Text(default='', index=True)
            lines = Table(BigLine, max_rows=300)
            total = Currency(compute=lines.sum('amount'))
        return Big

    base = Migration('0000_base', AddIndex('big', 'title'))
    app = build(Database(tmp_path / 'big.sqlite3', Registry(models('1')), migrations=[base]))
    status, big, _ = client(app)('POST', '/api/big', {'children': {'lines': [{'qty': '1'} for _ in range(150)]}})
    assert status == 201 and big['total'] == '150.00'
    steps = [base, Migration('0001_recompute', Recompute('big_lines', ('amount',)), Recompute('big', ('total',)))]
    database = Database(tmp_path / 'big.sqlite3', Registry(models('2')), migrations=steps)
    assert database.migrate(steps) == ['0001_recompute']
    upgraded = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    upgraded.initialize()
    c = client(upgraded)
    url = f"/api/big/{big['id']}"
    assert c('GET', url)[1]['total'] == '300.00'
    assert {row['amount'] for row in page(c, url, offset=100)[1]['items']} == {'2.00'}


@pytest.mark.parametrize('change', ['update', 'order', 'nested'])
def test_etag_covers_rows_beyond_the_first_page(make_app, change):
    app = make_app(Order, Product)
    c = client(app)
    rows = lines(150)
    rows[140]['children'] = {'parts': [{'_key': 'deep', 'hours': '1'}]}
    status, order, _ = c('POST', '/api/order', {'children': {'lines': rows}})
    assert status == 201, order
    url, ids = f"/api/order/{order['id']}", order['_row_ids']
    stale = c('GET', url)[2][b'etag']
    body = {'update': {'update': [{'id': ids['l140'], 'qty': '2', 'rate': '1.25'}]},  # Same amount, so totals do not change.
            'order': {'order': [ids['l149'], ids['l120']]},
            'nested': {'update': [{'id': ids['l140'], 'children': {'parts': {'update': [{'id': ids['deep'], 'hours': '1'}], 'create': [{'hours': '0'}]}}}]}}[change]
    status, saved, headers = c('PATCH', url, {'children': {'lines': body}}, headers={b'if-match': stale})
    assert status == 200, saved
    assert headers[b'etag'] != stale and headers[b'etag'] == c('GET', url)[2][b'etag']
    assert len(saved['children']['lines']) == 100 and saved['children_pages']['lines']['total'] == 150
    status, _, _ = c('PATCH', url, {'children': {'lines': {'update': [{'id': ids['l140'], 'qty': '5'}]}}}, headers={b'if-match': stale})
    assert status == 409


def test_full_list_is_refused_when_the_detail_holds_only_the_first_page(make_app):
    app = make_app(Order, Product)
    c = client(app)
    status, order, _ = c('POST', '/api/order', {'children': {'lines': lines(100)}})
    url = f"/api/order/{order['id']}"
    detail = c('GET', url)[1]
    assert 'children_pages' not in detail
    # At 100 rows the whole collection is embedded, so the form's full-list save keeps working.
    rows = [{'id': row['id'], 'qty': row['qty']} for row in detail['children']['lines']]
    rows[0]['qty'] = '2'
    status, saved, _ = edit(c, url, {'children': {'lines': rows + [{'_key': 'extra'}]}})
    assert status == 200 and saved['line_count'] == 101 and saved['children_pages']['lines']['total'] == 101
    detail = c('GET', url)[1]
    status, error, _ = edit(c, url, {'children': {'lines': [{'id': row['id']} for row in detail['children']['lines']]}})
    assert status == 422 and 'change set' in error['fields']['lines']
    assert page(c, url, limit=1)[1]['total'] == 101
    status, saved, _ = edit(c, url, {'children': {'lines': {'delete': [saved['_row_ids']['extra']]}}})
    assert status == 200 and saved['line_count'] == 100


def test_hidden_rows_keep_their_stored_positions(make_app):
    class Zone(Child):
        permissions = {'admin': FULL, 'editor': Rule(('read', 'create', 'update', 'delete'), scope=lambda user: {'region': 'east'})}
        region = Text(default='east')

    class Map(Model):
        Meta = FiniteMeta
        permissions = {'admin': FULL, 'editor': FULL}
        zones = Table(Zone)

    app = make_app(Map)
    admin = client(app)
    assert admin('POST', '/api/auth/users', {'username': 'editor', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    editor = client(app, 'editor')
    keys = ['w0', 'e1', 'e2', 'w3', 'e4']
    status, saved, _ = admin('POST', '/api/map', {'children': {'zones': [{'_key': key, 'region': 'west' if key[0] == 'w' else 'east'} for key in keys]}})
    url, ids = f"/api/map/{saved['id']}", saved['_row_ids']

    def stored():
        zone = next(child.model for child in app.registry.children if child.name == 'zones')
        with app.database.transaction() as tx:
            return {str(row.id): row.evlet_position for row in tx.list(zone, limit=100)}
    before = stored()
    order = lambda: [row['id'] for row in admin('GET', url)[1]['children']['zones']]
    status, result, _ = edit(editor, url, {'children': {'zones': [{'id': ids['e4']}, {'id': ids['e2']}, {'id': ids['e1']}]}})
    assert status == 200, result
    assert order() == [ids['w0'], ids['e4'], ids['e2'], ids['w3'], ids['e1']]
    assert stored()[ids['w0']] == before[ids['w0']] and stored()[ids['w3']] == before[ids['w3']]
    status, result, _ = edit(editor, url, {'children': {'zones': {'delete': [ids['e4']], 'create': [{'_key': 'n'}]}}})
    assert status == 200, result
    assert order() == [ids['w0'], ids['e2'], ids['w3'], ids['e1'], result['_row_ids']['n']]
    assert stored()[ids['w0']] == before[ids['w0']] and stored()[ids['w3']] == before[ids['w3']]
    # A full list fills the visible places in order (the row n left out is removed); hidden rows stay put.
    status, result, _ = edit(editor, url, {'children': {'zones': [{'id': ids['e1']}, {'_key': 'm'}, {'id': ids['e2']}]}})
    assert status == 200, result
    assert order() == [ids['w0'], ids['e1'], ids['w3'], result['_row_ids']['m'], ids['e2']]
    assert stored()[ids['w0']] == before[ids['w0']] and stored()[ids['w3']] == before[ids['w3']]


def test_client_keys_never_collide_with_saved_row_ids(make_app):
    app = make_app(Order, Product)
    c = client(app)
    status, order, _ = c('POST', '/api/order', {'children': {'lines': lines(3)}})
    url, first = f"/api/order/{order['id']}", order['_row_ids']['l0']
    status, saved, _ = edit(c, url, {'children': {'lines': [{'id': first}, {'_key': str(first), 'qty': '2'}]}})
    assert status == 200, saved
    assert saved['_row_ids'] == {str(first): saved['children']['lines'][1]['id']} and saved['line_count'] == 2
    status, saved, _ = edit(c, url, {'children': {'lines': {'create': [{'_key': str(first)}, {'_key': '999'}]}}})
    assert status == 200 and saved['line_count'] == 4, saved


def test_plans_reject_min_rows_above_max_rows():
    plan = Registry(Order, Product).plans[Order]
    broken = {**plan, 'tables': {**plan['tables'], 'lines[*]': {**plan['tables']['lines[*]'], 'min_rows': 5, 'max_rows': 4}}}
    broken['model_schema_hash'] = fingerprint(broken)
    with pytest.raises(CalcError, match='row limits'):
        check_plan(broken)
