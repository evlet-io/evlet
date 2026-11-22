"""Link filters as a list of groups (any one may match) and Refs that read a Table column or read through a Link.

    sale_unit = Link(Unit, filters=[{'id': Ref('unit')}, {'id__in': Ref('packs.unit')}])
    Line.unit = Link(Unit, filters=[{'id': Ref('item.unit')}, {'id__in': Ref('item.packs.unit')}])

The picker (GET /api/unit/search?link=...) offers only those units, the form's unsaved rows included, and the
server refuses any other on save. A Ref through a Link reads the linked record as the person choosing: a record,
Table or field they can't read gives nothing to match."""

import json
from urllib.parse import quote

import pytest

from evlet import Child, Database, Link, Model, Ref, Registry, Rule, Table, Text
from support import client
from test_rules_types import add_user, patch, start

ALL = ('read', 'create', 'update', 'delete')


def flour_only(user):
    return {'title': 'Flour'}


class Unit(Model):
    permissions = {'admin': Rule(ALL), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    title = Text(required=True)


class Item(Model):
    # The editor reads one item only; "viewer" reads items but neither their base unit nor their packs.
    permissions = {'admin': Rule(ALL), 'editor': Rule(('read',), scope=flour_only), 'viewer': Rule(('read',), read_fields=('title',))}
    title = Text(required=True)
    unit = Link(Unit)

    class Pack(Child):
        permissions = {'admin': Rule(ALL), 'editor': Rule(('read',))}
        unit = Link(Unit)
        title = Text(default='')

    packs = Table(Pack)
    # The base unit or one of the item's pack units, unsaved rows included.
    sale_unit = Link(Unit, filters=[{'id': Ref('unit')}, {'id__in': Ref('packs.unit')}])


class Order(Model):
    permissions = {'admin': Rule(ALL), 'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read', 'create', 'update'))}
    title = Text(default='Order')

    class Line(Child):
        permissions = {'admin': Rule(ALL), 'editor': Rule(ALL), 'viewer': Rule(ALL)}
        item = Link(Item)
        # The line's item's base unit or one of its pack units.
        unit = Link(Unit, filters=[{'id': Ref('item.unit')}, {'id__in': Ref('item.packs.unit')}])

    lines = Table(Line)


@pytest.fixture
def world(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Unit, Item, Order)))
    admin = client(app)
    units = {title: admin('POST', '/api/unit', {'title': title})[1]['id'] for title in ('kg', 'bag', 'box', 'litre')}
    items = {}
    for title, base, packs in (('Flour', 'kg', ('bag',)), ('Milk', 'litre', ('box',))):
        status, body, _ = admin('POST', '/api/item', {'title': title, 'unit': units[base],
                                                     'children': {'packs': [{'unit': units[pack]} for pack in packs]}})
        assert status == 201, body
        items[title] = body['id']
    yield app, admin, units, items
    app.database.close()


def offered(c, link, values=None):
    query = f'link={link}' + (f'&values={quote(json.dumps(values))}' if values is not None else '')
    status, body, _ = c('GET', f'/api/unit/search?{query}')
    assert status == 200, body
    return sorted(item['title'] for item in body['items'])


def test_the_item_picker_offers_its_base_unit_and_its_pack_units_unsaved_rows_included(world):
    _, admin, units, _ = world
    assert offered(admin, 'item.sale_unit', {'unit': units['kg']}) == ['kg']
    # Rows the form holds and has not saved yet feed Ref("packs.unit").
    assert offered(admin, 'item.sale_unit', {'unit': units['kg'], 'packs.unit': [units['bag'], units['box']]}) == ['bag', 'box', 'kg']
    assert offered(admin, 'item.sale_unit', {'packs.unit': [units['bag']]}) == ['bag']
    assert offered(admin, 'item.sale_unit', {}) == []
    # A Table column takes a list; one value is a 400.
    status, body, _ = admin('GET', f"/api/unit/search?link=item.sale_unit&values={quote(json.dumps({'packs.unit': units['bag']}))}")
    assert status == 422 and 'values' in body['fields'], body


def test_an_item_save_refuses_a_unit_that_is_not_its_own(world):
    _, admin, units, items = world
    status, body, _ = admin('POST', '/api/item', {'title': 'Rice', 'unit': units['kg'], 'sale_unit': units['box']})
    assert status == 422 and 'sale_unit' in body['fields'], body
    # The pack is in the same save: its unit counts.
    status, body, _ = admin('POST', '/api/item', {'title': 'Rice', 'unit': units['kg'], 'sale_unit': units['box'],
                                                 'children': {'packs': [{'unit': units['box']}]}})
    assert status == 201, body
    assert patch(admin, f"/api/item/{items['Flour']}", {'sale_unit': units['bag']})[0] == 200
    status, body, _ = patch(admin, f"/api/item/{items['Flour']}", {'sale_unit': units['litre']})
    assert status == 422 and 'sale_unit' in body['fields'], body


def test_the_line_picker_reads_the_items_units_through_the_link(world):
    _, admin, units, items = world
    assert offered(admin, 'order_lines.unit', {'item': items['Flour']}) == ['bag', 'kg']
    assert offered(admin, 'order_lines.unit', {'item': items['Milk']}) == ['box', 'litre']
    assert offered(admin, 'order_lines.unit', {}) == []
    # Only the Link's value is a form value: the rest is read on the server.
    status, body, _ = admin('GET', f"/api/unit/search?link=order_lines.unit&values={quote(json.dumps({'item.unit': units['kg']}))}")
    assert status == 422 and 'values' in body['fields'], body


def test_a_line_save_refuses_a_unit_the_item_does_not_have(world):
    _, admin, units, items = world
    line = lambda item, unit: {'children': {'lines': [{'item': items[item], 'unit': units[unit]}]}}
    for item, unit in (('Flour', 'kg'), ('Flour', 'bag'), ('Milk', 'box')):
        status, body, _ = admin('POST', '/api/order', line(item, unit))
        assert status == 201, body
    status, body, _ = admin('POST', '/api/order', line('Flour', 'box'))
    assert status == 422, body
    created = admin('POST', '/api/order', line('Milk', 'litre'))[1]
    status, body, _ = patch(admin, f"/api/order/{created['id']}", line('Milk', 'kg'))
    assert status == 422, body


def test_a_ref_through_a_link_reads_only_what_the_person_may_read(world):
    app, _, units, items = world
    editor, viewer = add_user(app, 'editor'), add_user(app, 'viewer')
    # The editor reads Flour, not Milk: Milk's units are never offered, and a line naming one is refused.
    assert offered(editor, 'order_lines.unit', {'item': items['Flour']}) == ['bag', 'kg']
    assert offered(editor, 'order_lines.unit', {'item': items['Milk']}) == []
    assert editor('POST', '/api/order', {'children': {'lines': [{'item': items['Flour'], 'unit': units['bag']}]}})[0] == 201
    # "viewer" reads the item's title only: neither its base unit nor its packs reach the picker or the save.
    assert offered(viewer, 'order_lines.unit', {'item': items['Flour']}) == []
    status, body, _ = viewer('POST', '/api/order', {'children': {'lines': [{'item': items['Flour'], 'unit': units['kg']}]}})
    assert status == 422, body


def test_the_schema_sends_the_groups_as_declared(world):
    _, admin, _, _ = world
    models = {model['name']: model for model in admin('GET', '/api/schema')[1]['models']}
    ref = lambda path: {'$expr': 'ref', 'args': [path]}
    assert models['item']['behavior']['sale_unit']['filters'] == [{'id': ref('unit')}, {'id__in': ref('packs.unit')}]
    assert models['order_lines']['behavior']['unit']['filters'] == [{'id': ref('item.unit')}, {'id__in': ref('item.packs.unit')}]


def test_wrong_declarations_fail_when_the_registry_is_built():
    class Plain(Model):
        unit = Link(Unit)
        packs = Table(Item.Pack)
        bad = Link(Unit, filters=[{'id': Ref('packs.unit')}])
    with pytest.raises(ValueError, match='compare it with id__in'):
        Registry(Unit, Item, Plain)

    class Through(Model):
        item = Link(Item)
        bad = Link(Unit, filters={'id': Ref('item.colour')})
    with pytest.raises(ValueError, match='has no stored field colour'):
        Registry(Unit, Item, Through)

    with pytest.raises(ValueError, match='list of non-empty dicts'):
        Link(Unit, filters=[{}])
