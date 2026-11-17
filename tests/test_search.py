"""Record search for Link dropdowns, pickers and the search bar (GET /api/{model}/search, evlet/search.py)."""

import json
from urllib.parse import quote

import pytest

from evlet import Child, Count, Database, Decimal, Link, Model, Ref, Registry, Rule, Select, Sum, Table, Text, qb
from evlet.calc import F
from evlet.declarations import Option
from evlet.search import rank
from test_business import client
from test_rules_types import add_user, start

ALL = ('read', 'create', 'update', 'delete')


def visible_items(user):
    return {'hidden': ''}


class Group(Model):
    permissions = {'admin': Rule(ALL), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    title = Text(required=True)


class Item(Model):
    class Meta:
        title_field = 'title'
        search_fields = ('code', 'barcode')

    # Viewers read neither barcodes nor items marked hidden.
    permissions = {'admin': Rule(ALL), 'editor': Rule(('read', 'create')),
                   'viewer': Rule(('read',), read_fields=('title', 'code', 'group', 'stock_uom', 'hidden'), scope=visible_items)}
    title = Text(required=True)
    code = Text(default='')
    barcode = Text(default='')
    group = Link(Group)
    stock_uom = Select(['box', 'nos'], default='box')
    hidden = Text(default='')


class Warehouse(Model):
    # No Meta.search_fields: searched as the list's search box searches it.
    class Meta:
        option = Option(subtext=('City: ', F('city')))

    permissions = {'admin': Rule(ALL), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    title = Text(required=True)
    city = Text(default='')


class StockMove(Model):
    permissions = {'admin': Rule(ALL), 'editor': Rule(('read',))}
    item = Link(Item, required=True)
    warehouse = Link(Warehouse, required=True)
    qty = Decimal(scale=3, required=True)


def note_hook(context, options, values):
    for option in options:
        option['subtext'] = f"{option['record'].code} via hook"
    return options


class Order(Model):
    permissions = {'admin': Rule(ALL), 'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read', 'create', 'update'))}
    title = Text(default='Order')
    group = Link(Group)
    warehouse = Link(Warehouse)
    hooked = Link(Item, option=Option(hook=note_hook))

    class Line(Child):
        permissions = {'admin': Rule(ALL), 'editor': Rule(ALL), 'viewer': Rule(ALL)}
        # Items of the order's group, with their stock in the order's warehouse below each one.
        item = Link(Item, filters={'group': Ref('parent.group')},
                    option=Option(subtext=('In stock: ', Sum('qty', model=StockMove, link='item',
                                                             where={'warehouse': Ref('parent.warehouse')}), ' ', F('stock_uom'))))
        moves = Link(Item, option=Option(subtext=(Count(StockMove, link='item'), ' moves')))

    lines = Table(Line)


def ask(user, model, q='', **params):
    query = f'q={quote(q)}' + ''.join(f'&{key}={quote(json.dumps(value) if key == "values" else str(value))}' for key, value in params.items())
    return user('GET', f'/api/{model}/search?{query}')


def titles(response):
    status, body, _ = response
    assert status == 200, body
    return [item['title'] for item in body['items']]


@pytest.fixture
def world(tmp_path):
    registry = Registry(Group, Item, Warehouse, StockMove, Order)
    app = start(Database(tmp_path / 'db.sqlite3', registry))
    admin = client(app)
    ids = {}
    for title in ('Packing', 'Tools'):
        ids[title] = admin('POST', '/api/group', {'title': title})[1]['id']
    for title, code, barcode, group, hidden in (
            ('Box', 'B-1', '4006381333931', 'Packing', ''), ('Boxes', 'B-2', '', 'Packing', ''),
            ('Big box', 'B-3', '', 'Packing', ''), ('Inbox tray', 'T-1', '', 'Tools', ''),
            ('Black ox', 'T-2', '', 'Tools', ''), ('Cable', 'T-3', '', 'Tools', ''), ('Secret box', 'S-1', '', 'Packing', 'yes'),
            ('Under_score', 'U-1', '', 'Tools', '')):
        ids[title] = admin('POST', '/api/item', {'title': title, 'code': code, 'barcode': barcode, 'group': ids[group], 'hidden': hidden})[1]['id']
    for title, city in (('Dubai main', 'Dubai'), ('Jebel Ali', 'Dubai'), ('Riyadh', 'Riyadh')):
        ids[title] = admin('POST', '/api/warehouse', {'title': title, 'city': city})[1]['id']
    for item, warehouse, qty in (('Box', 'Dubai main', '10.5'), ('Box', 'Dubai main', '1.5'), ('Box', 'Riyadh', '4'), ('Boxes', 'Riyadh', '2')):
        assert admin('POST', '/api/stockmove', {'item': ids[item], 'warehouse': ids[warehouse], 'qty': qty})[0] == 201
    return app, admin, ids, registry


def test_ranks_whole_value_prefix_word_inside_then_in_order(world):
    _, admin, _, _ = world
    assert titles(ask(admin, 'item', 'box')) == ['Box', 'Boxes', 'Big box', 'Secret box', 'Inbox tray', 'Black ox']
    # Characters in order, tightest first; case does not matter.
    assert titles(ask(admin, 'item', 'BLX')) == ['Black ox']
    assert titles(ask(admin, 'item', 'box', limit=2)) == ['Box', 'Boxes']


def test_rank_tiers():
    assert rank('Box', 'box', 'box') == (0, 0)
    assert rank('Boxes', 'box', 'box')[0] == 1
    assert rank('Big box', 'box', 'box')[0] == 2
    assert rank('Inbox', 'box', 'box')[0] == 3
    assert rank('Black ox', 'bx', 'bx') == (4, 8)
    assert rank('Cable', 'box', 'box') is None


def test_searches_declared_fields_and_shows_the_match(world):
    _, admin, ids, _ = world
    status, body, _ = ask(admin, 'item', '40063813')
    assert status == 200 and body['items'] == [{'id': ids['Box'], 'title': 'Box', 'subtext': '4006381333931'}]
    assert titles(ask(admin, 'item', 't-3')) == ['Cable']
    # An empty search lists records in the list's order.
    assert len(titles(ask(admin, 'item'))) == 8


def test_permissions_hide_fields_and_rows(world):
    app, _, _, _ = world
    viewer = add_user(app, 'viewer')
    # The barcode is not readable, so it is not searched; the hidden item is outside the row scope.
    assert titles(ask(viewer, 'item', '40063813')) == []
    assert 'Secret box' not in titles(ask(viewer, 'item', 'box'))
    # A model the role can't read has no search.
    assert ask(viewer, 'stockmove', '')[0] == 403


def test_a_model_without_search_fields_searches_as_the_list_does(world):
    _, admin, _, _ = world
    assert titles(ask(admin, 'warehouse', 'jebel')) == ['Jebel Ali']
    # No fuzzy matching: the text must appear as typed.
    assert titles(ask(admin, 'warehouse', 'jbl')) == []
    # Meta.option applies to every Link to the model.
    status, body, _ = ask(admin, 'warehouse', 'riyadh')
    assert body['items'][0]['subtext'] == 'City: Riyadh'


def test_link_filters_take_form_values(world):
    _, admin, ids, registry = world
    line = 'order_lines'
    link = f'{line}.item'
    assert titles(ask(admin, 'item', 'box', link=link, values={'parent.group': ids['Tools']})) == ['Inbox tray', 'Black ox']
    # A Ref the form left empty matches nothing.
    assert titles(ask(admin, 'item', 'box', link=link)) == []
    # Only the paths the Link reads, and only Links to this model.
    assert ask(admin, 'item', 'box', link=link, values={'title': 'x'})[0] == 422
    assert ask(admin, 'item', 'box', link='order.warehouse')[0] == 422
    assert ask(admin, 'item', 'box', link='order.nothing')[0] == 422
    assert ask(admin, 'item', 'box', link='nothing.item')[0] == 422
    assert ask(admin, 'item', 'box', values={'parent.group': ids['Tools']})[0] == 422
    # Values are checked as the filtered field's values.
    assert ask(admin, 'item', 'box', link=link, values={'parent.group': 'not an id'})[0] == 422


def test_subtext_joins_an_aggregate_keyed_by_a_form_value(world):
    _, admin, ids, registry = world
    link = f'order_lines.item'
    values = {'parent.group': ids['Packing'], 'parent.warehouse': ids['Dubai main']}
    status, body, _ = ask(admin, 'item', 'box', link=link, values=values)
    shown = {item['title']: item.get('subtext') for item in body['items']}
    assert shown['Box'] == 'In stock: 12 box' and shown['Boxes'] == 'In stock: 0 box'
    values['parent.warehouse'] = ids['Riyadh']
    shown = {item['title']: item.get('subtext') for item in ask(admin, 'item', 'box', link=link, values=values)[1]['items']}
    assert shown['Box'] == 'In stock: 4 box' and shown['Boxes'] == 'In stock: 2 box'
    # Without the warehouse there is nothing to show below the title.
    del values['parent.warehouse']
    assert all('subtext' not in item for item in ask(admin, 'item', 'box', link=link, values=values)[1]['items'])
    # A count needs no form value.
    status, body, _ = ask(admin, 'item', 'box', link=f'order_lines.moves')
    assert {item['title']: item['subtext'] for item in body['items']}['Box'] == '3 moves'


def test_subtext_leaves_out_what_the_role_cannot_read(world):
    app, _, ids, registry = world
    viewer = add_user(app, 'viewer')
    # Viewers can't read stock moves, so no stock line shows; the options still do.
    status, body, _ = ask(viewer, 'item', 'box', link=f'order_lines.item',
                          values={'parent.group': ids['Packing'], 'parent.warehouse': ids['Dubai main']})
    assert status == 200 and [item['title'] for item in body['items']] == ['Box', 'Boxes', 'Big box']
    assert all('subtext' not in item for item in body['items'])


def test_hook_is_trusted_code(world):
    _, admin, _, _ = world
    status, body, _ = ask(admin, 'item', 'cable', link='order.hooked')
    assert body['items'][0]['subtext'] == 'T-3 via hook'


def test_search_text_is_a_bound_value(world):
    _, admin, _, _ = world
    # LIKE wildcards in the text match only themselves; SQL in the text is just text.
    assert titles(ask(admin, 'item', '_')) == ['Under_score']
    assert titles(ask(admin, 'item', '%')) == []
    assert titles(ask(admin, 'item', "x' OR '1'='1")) == []
    assert ask(admin, 'item', 'x' * 101)[0] == 422
    assert ask(admin, 'item', 'box', limit=21)[0] == 422
    assert admin('GET', '/api/item/search?q=box&order_by=title')[0] == 422


def test_subsequence_condition_on_sqlite(tmp_path):
    db = Database(tmp_path / 'db.sqlite3', Registry(Group))
    db.initialize()
    with db.transaction() as tx:
        for title in ('Packing', 'Tools', '50%_off', 'PACKED'):
            tx.create(Group, title=title)
        names = lambda text: [row[0] for row in tx.connection.run(  # noqa: E731
            qb.select('group', 'title').where(qb.subsequence_ci('title', text)).order_by('id')).all()]
        assert names('pck') == ['Packing', 'PACKED']
        assert names('%_') == ['50%_off']
        assert names('ols') == ['Tools']


def test_declarations_are_checked():
    with pytest.raises(ValueError, match='search_fields'):
        class Bad(Model):
            title = Text()

            class Meta:
                search_fields = ('nothing',)
    with pytest.raises(ValueError, match='Ref'):
        class Form(Model):
            title = Text()

            class Meta:
                option = Option(subtext=(Count('StockMove', link='item', where={'warehouse': Ref('warehouse')}),))
    with pytest.raises(ValueError, match='F'):
        Option(subtext=(F('a') + 1,))
    with pytest.raises(ValueError, match='Option'):
        Link(Item, option='code')
    with pytest.raises(ValueError, match='no field'):
        class Odd(Model):
            item = Link(Item, option=Option(subtext=F('colour')))
        Registry(Group, Item, Odd)
