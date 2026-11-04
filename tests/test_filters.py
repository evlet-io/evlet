"""List filters: operators by field type, child table EXISTS groups, permissions and hostile input.

The Playground's Item has about 200 fields and two child tables (prices, barcodes). Every test runs
through the authenticated HTTP list route; tests/test_postgres.py runs them on PostgreSQL too."""
import copy
import json
from urllib.parse import quote

import pytest

from evlet import Policy, Rule, ValidationError
from evlet.query import MAX_CONDITIONS, SqlQuery, conditions
from examples.playground.items import Item
from tests.test_business import client, hashed, secured  # noqa: F401  (fixtures)


def price(key, price_list, rate, min_qty=0, **values):
    return {'_key': key, 'price_list': price_list, 'rate': rate, 'min_qty': min_qty, **values}


ITEMS = {
    'A-100': {'item_name': 'Steel bolt', 'item_group': 'spare part', 'standard_rate': '12.50', 'disabled': False,
              'introduced_on': '2026-01-15', 'brand': 'Atlas', 'weight_kg': '0.250',
              'prices': [price('a1', 'wholesale', '60.00', 10), price('a2', 'export', '20.00', 1)], 'barcodes': [{'_key': 'b', 'barcode': '6291041500213'}]},
    'B-200': {'item_name': 'Copper wire', 'item_group': 'raw material', 'standard_rate': '80.00', 'disabled': False,
              'introduced_on': '2026-03-01', 'brand': None, 'weight_kg': '5.000',
              'prices': [price('b1', 'wholesale', '40.00', 20)], 'barcodes': []},
    'C-300': {'item_name': 'Site survey', 'item_group': 'service', 'standard_rate': '450.00', 'disabled': True,
              'introduced_on': None, 'brand': 'atlas works', 'weight_kg': None, 'prices': [], 'barcodes': []},
}


@pytest.fixture
def items(secured):
    admin = client(secured)
    status, customer, _ = admin('POST', '/api/customer', {'customer_name': 'Gulf Traders', 'currency': 'AED'})
    assert status == 201, customer
    ids = {}
    for code, values in ITEMS.items():
        values = copy.deepcopy(values)
        children = {'prices': values.pop('prices'), 'barcodes': values.pop('barcodes')}
        if code == 'B-200':
            children['prices'][0]['customer'] = customer['id']
        status, created, _ = admin('POST', '/api/item', {'item_code': code, **values, 'children': children})
        assert status == 201, created
        ids[code] = created['id']
    secured.customer_id = customer['id']
    return admin


def listed(call, filters, model='item'):
    status, data, _ = call('GET', f'/api/{model}?order_by=item_code&filters=' + quote(json.dumps(filters)))
    if status != 200:
        return status, data
    return status, [row['item_code'] for row in data['items']]


def codes(call, filters):
    status, result = listed(call, filters)
    assert status == 200, result
    return result


def test_every_operator_by_field_type(items):
    cases = [
        # text
        ([['brand', 'eq', 'Atlas']], ['A-100']),
        ([['item_name', 'ne', 'Steel bolt']], ['B-200', 'C-300']),
        ([['brand', 'icontains', 'ATLAS']], ['A-100', 'C-300']),
        ([['brand', 'contains', 'atlas']], ['C-300']),
        # Not like and not in also match empty values, as Frappe's do.
        ([['brand', 'nicontains', 'works']], ['A-100', 'B-200']),
        ([['brand', 'nin', ['Atlas']]], ['B-200', 'C-300']),
        ([['brand', 'in', ['Atlas', 'atlas works']]], ['A-100', 'C-300']),
        ([['item_code', 'startswith', 'B-']], ['B-200']),
        ([['brand', 'isnull', True]], ['B-200']),
        ([['brand', 'isnull', False]], ['A-100', 'C-300']),
        # select
        ([['item_group', 'in', ['spare part', 'service']]], ['A-100', 'C-300']),
        ([['item_group', 'nin', ['spare part', 'service']]], ['B-200']),
        # currency and decimal
        ([['standard_rate', 'gt', '12.50']], ['B-200', 'C-300']),
        ([['standard_rate', 'gte', '12.50']], ['A-100', 'B-200', 'C-300']),
        ([['standard_rate', 'lt', '80']], ['A-100']),
        ([['standard_rate', 'lte', '80']], ['A-100', 'B-200']),
        ([['standard_rate', 'between', ['12.50', '80.00']]], ['A-100', 'B-200']),
        ([['weight_kg', 'ne', '5']], ['A-100']),
        # date
        ([['introduced_on', 'eq', '2026-03-01']], ['B-200']),
        ([['introduced_on', 'lt', '2026-03-01']], ['A-100']),
        ([['introduced_on', 'gt', '2026-01-15']], ['B-200']),
        ([['introduced_on', 'between', ['2026-01-01', '2026-01-31']]], ['A-100']),
        ([['introduced_on', 'isnull', True]], ['C-300']),
        # check
        ([['disabled', 'eq', True]], ['C-300']),
        ([['disabled', 'eq', False]], ['A-100', 'B-200']),
        # several at once are combined with AND
        ([['disabled', 'eq', False], ['standard_rate', 'gt', '20'], ['item_group', 'in', ['raw material']]], ['B-200']),
    ]
    for filters, expected in cases:
        assert codes(items, filters) == expected, filters
    # The object form keeps working: "field__operator": value.
    assert codes(items, {'standard_rate__between': ['12.50', '80.00'], 'disabled': False}) == ['A-100', 'B-200']


def test_link_filters_and_operators_a_type_does_not_take(items, secured):
    customer = secured.customer_id
    assert codes(items, [{'child': 'prices', 'where': [['customer', 'eq', customer]]}]) == ['B-200']
    assert codes(items, [['default_customer', 'isnull', True]]) == ['A-100', 'B-200', 'C-300']
    for filters in ([['disabled', 'gt', True]], [['item_group', 'icontains', 'part']], [['standard_rate', 'icontains', '1']],
                    [['introduced_on', 'in', ['2026-03-01']]], [['standard_rate', 'between', ['1']]], [['brand', 'eq', None, 1]],
                    [['brand', 'unknown', 'x']], [['standard_rate', 'gt', 'many']], [['introduced_on', 'eq', '01-03-2026']]):
        status, result = listed(items, filters)
        assert status == 422, (filters, result)


def test_child_groups_match_one_row_without_repeating_parents(items):
    # One row must meet every condition: A's wholesale row has rate 60; B's wholesale row has rate 40.
    assert codes(items, [{'child': 'prices', 'match': 'any', 'where': [['price_list', 'eq', 'wholesale'], ['rate', 'gt', '50']]}]) == ['A-100']
    # A has a wholesale row and an export row of 20, but no single row is both.
    assert codes(items, [{'child': 'prices', 'where': [['price_list', 'eq', 'wholesale'], ['rate', 'lt', '30']]}]) == []
    assert codes(items, [{'child': 'prices', 'where': [['min_qty', 'gte', 10], ['rate', 'between', ['30', '70']]]}]) == ['A-100', 'B-200']
    # No row matches: items without a wholesale price, including items without prices.
    assert codes(items, [{'child': 'prices', 'match': 'none', 'where': [['price_list', 'eq', 'wholesale']]}]) == ['C-300']
    assert codes(items, [{'child': 'barcodes', 'match': 'none', 'where': [['barcode', 'isnull', False]]}]) == ['B-200', 'C-300']
    # Shorthand: "table.field" is a group of one condition any row meets.
    assert codes(items, [['prices.price_list', 'eq', 'export']]) == ['A-100']
    # Parent and child conditions together, and two groups on different tables.
    assert codes(items, [['disabled', 'eq', False], ['prices.rate', 'gt', '0'], {'child': 'barcodes', 'match': 'none', 'where': [['barcode_type', 'eq', 'UPC']]}]) == ['A-100', 'B-200']
    # Every price matches rate > 0 for A (two rows) and B: each parent is listed and counted once.
    status, data, _ = items('GET', '/api/item?filters=' + quote(json.dumps([['prices.rate', 'gt', '0']])))
    assert status == 200 and data['total'] == 2 and len(data['items']) == 2
    # Measures and the aggregate route take the same filters.
    status, data, _ = items('GET', '/api/item/aggregate?measures=n:count&filters=' + quote(json.dumps([['prices.price_list', 'eq', 'wholesale']])))
    assert status == 200 and data['items'] == [{'n': 2}], data
    for bad in ([{'child': 'prices', 'where': []}], [{'child': 'prices', 'match': 'all', 'where': [['rate', 'gt', '1']]}],
                [{'child': 'prices', 'where': [['rate', 'gt', '1']], 'extra': 1}], [{'child': 'prices', 'where': [['rate', 'icontains', '1']]}]):
        status, result = listed(items, bad)
        assert status == 422, (bad, result)


def test_many_conditions_and_the_limit(items):
    many = [['standard_rate', 'gte', '0']] * 20 + [['brand', 'nicontains', f'zz{index}'] for index in range(20)] \
        + [{'child': 'prices', 'where': [['rate', 'gte', '0']] * 9}]
    assert len(many) == 41 and codes(items, many) == ['A-100', 'B-200']
    status, result = listed(items, [['standard_rate', 'gte', '0']] * (MAX_CONDITIONS + 1))
    assert status == 422 and 'at most' in json.dumps(result)
    status, result = listed(items, [['standard_rate', 'gte', '0']] * 30 + [{'child': 'prices', 'where': [['rate', 'gte', '0']] * 21}])
    assert status == 422


def restricted(secured):
    """Viewers read items without standard_rate and price rows without rate; editors see only services."""
    registry = secured.registry
    prices = next(child.model for child in registry.children if child.parent is Item and child.name == 'prices')
    readable = tuple(name for name in Item.__fields__ if name != 'standard_rate')
    secured.business.policy = Policy(registry, rules={
        ('viewer', Item): Rule(('read',), read_fields=readable),
        ('viewer', prices): Rule(('read',), read_fields=('price_list', 'currency', 'min_qty', 'valid_from', 'customer')),
        ('editor', Item): Rule(('read', 'create', 'update'), scope=lambda user: {'item_group': 'service'}),
    })


def test_hidden_fields_and_tables_are_refused_not_answered(items, secured):
    restricted(secured)
    viewer = client(secured, 'viewer')
    unknown = listed(viewer, [['no_such_field', 'eq', 'x']])
    assert unknown[0] == 403
    # A hidden field gives exactly what an unknown one does, whatever the value would match.
    for filters in ([['standard_rate', 'gt', '0']], [['standard_rate', 'gt', '100000']], {'standard_rate__gt': '0'},
                    [['prices.rate', 'gt', '0']], [{'child': 'prices', 'match': 'none', 'where': [['rate', 'gt', '0']]}],
                    [['no_table.rate', 'gt', '0']], [['prices.no_such_field', 'eq', 'x']], [['default_customer__item_name', 'eq', 'x']]):
        assert listed(viewer, filters) == unknown, filters
    status, data, _ = viewer('GET', '/api/item/aggregate?measures=n:count&filters=' + quote(json.dumps([['prices.rate', 'gt', '0']])))
    assert status == 403
    # Readable fields of the same tables still work.
    assert codes(viewer, [['prices.price_list', 'eq', 'wholesale'], ['prices.min_qty', 'gte', 15]]) == ['B-200']
    # The schema leaves hidden fields out and says what each readable field takes.
    status, schema, _ = viewer('GET', '/api/schema')
    models = {entry['name']: entry for entry in schema['models']}
    fields = {field['name']: field for field in models['item']['fields']}
    assert 'standard_rate' not in fields and fields['brand']['operators'][:3] == ['eq', 'ne', 'in']
    assert fields['disabled']['operators'] == ['eq', 'ne', 'isnull'] and 'between' in fields['introduced_on']['operators']
    tables = {entry['name']: entry for entry in models['item']['children']}
    assert tables['prices']['filterable'] is True
    assert 'rate' not in {field['name'] for field in models[tables['prices']['model']]['fields']}


def test_row_scope_still_applies_to_filtered_lists(items, secured):
    restricted(secured)
    editor = client(secured, 'editor')
    assert codes(editor, [['standard_rate', 'gte', '0']]) == ['C-300']
    assert codes(editor, [{'child': 'prices', 'match': 'none', 'where': [['rate', 'gte', '0']]}]) == ['C-300']
    assert codes(editor, [['prices.rate', 'gte', '0']]) == []


def test_injection_attempts_are_values_or_refused(items, secured):
    payloads = ["x' OR '1'='1", 'x"; DROP TABLE item; --', '%', '_', "') OR 1=1 --", '\\', 'atlas%']
    for text in payloads:
        assert codes(items, [['brand', 'eq', text]]) == []
        assert codes(items, [['brand', 'icontains', text]]) == []
        assert codes(items, [['barcodes.barcode', 'in', [text]]]) == []
        assert listed(items, [['prices.price_list', 'in', [text]]])[0] == 422
    for filters in ([['brand" = brand OR "1', 'eq', 'x']], [['brand', 'eq) OR (1=1', 'x']], [['prices"; --.rate', 'gt', '0']],
                    [{'child': 'prices WHERE 1=1 --', 'where': [['rate', 'gt', '0']]}], [{'child': 'prices', 'where': [['rate" OR 1=1 --', 'gt', '0']]}],
                    [['prices.rate', 'gt', "0 OR 1=1"]], [['brand', 'icontains', 'a\x00b']], [['item_code', 'in', list(range(101))]]):
        status, _ = listed(items, filters)
        assert status in (403, 422), filters
    status, data, _ = items('GET', '/api/item')
    assert status == 200 and data['total'] == 3


def test_python_queries_take_the_same_filters(items, secured):
    rows = secured.database.list(Item, filters=[{'child': 'prices', 'where': [['price_list', 'eq', 'wholesale'], ['rate', 'gt', '50']]}])
    assert [row.item_code for row in rows] == ['A-100']
    with pytest.raises(ValidationError):
        SqlQuery(secured.registry, Item).where([{'child': 'nothing', 'where': [['rate', 'gt', '1']]}])
    assert [type(item).__name__ for item in conditions([['a', 'eq', 1], ['t.b', 'eq', 2]])] == ['Condition', 'ChildGroup']
