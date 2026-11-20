"""Link filters on the linked record's own fields, with an operator: `filters={'kind__ne': 'transit'}`.

The picker (GET /api/{model}/search?link=...) leaves those records out, and the server refuses one on every save and
every move, not only in the browser."""

import pytest

from evlet import Database, Link, Model, Ref, Registry, Rule, Select, State, Text
from test_business import client
from test_rules_types import start

ALL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class Location(Model):
    permissions = ALL
    title = Text(required=True)
    kind = Select(('store', 'shop', 'transit'), default='store')
    zone = Text(default='')


class Transfer(Model):
    permissions = ALL
    title = Text(default='Transfer')
    zone = Text(default='')
    # Never a transit location; and, from the form, one in the transfer's zone or none at all.
    origin = Link(Location, filters={'kind__ne': 'transit'})
    target = Link(Location, filters={'kind__in': ['store', 'shop'], 'zone': Ref('zone')})
    docstate = State(State.Step('draft'), State.Step('sent'), rules=(State.Rule('draft', 'sent', 'Send', key='send'),))


@pytest.fixture
def world(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Location, Transfer)))
    c = client(app)
    ids = {title: c('POST', '/api/location', {'title': title, 'kind': kind, 'zone': 'north'})[1]['id']
           for title, kind in (('Main store', 'store'), ('Corner shop', 'shop'), ('Truck 4', 'transit'))}
    yield app, c, ids
    app.database.close()


def titles(c, link, values=None):
    import json
    from urllib.parse import quote
    query = f'link={link}' + (f'&values={quote(json.dumps(values))}' if values else '')
    status, body, _ = c('GET', f'/api/location/search?{query}')
    assert status == 200, body
    return sorted(item['title'] for item in body['items'])


def test_the_picker_leaves_out_records_the_filter_refuses(world):
    _, c, _ = world
    assert titles(c, 'transfer.origin') == ['Corner shop', 'Main store']
    assert titles(c, 'transfer.target', {'zone': 'north'}) == ['Corner shop', 'Main store']


def test_a_save_naming_a_refused_record_gets_a_field_error(world):
    _, c, ids = world
    status, body, _ = c('POST', '/api/transfer', {'origin': ids['Truck 4']})
    assert status == 422 and 'origin' in body['fields'], body
    status, body, _ = c('POST', '/api/transfer', {'zone': 'north', 'target': ids['Truck 4']})
    assert status == 422 and 'target' in body['fields'], body
    status, created, headers = c('POST', '/api/transfer', {'zone': 'north', 'origin': ids['Main store'], 'target': ids['Corner shop']})
    assert status == 201, created
    url = f"/api/transfer/{created['id']}"
    status, body, _ = c('PATCH', url, {'origin': ids['Truck 4']}, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert status == 422 and 'origin' in body['fields'], body


def test_a_move_checks_the_filter_again(world):
    _, c, ids = world
    created = c('POST', '/api/transfer', {'origin': ids['Main store']})[1]
    url = f"/api/transfer/{created['id']}"
    # The location became a transit one after it was chosen: the next move is refused, as a save would be.
    location = f"/api/location/{ids['Main store']}"
    assert c('PATCH', location, {'kind': 'transit'}, headers={b'if-match': c('GET', location)[2][b'etag']})[0] == 200
    status, body, _ = c('POST', f'{url}/moves/send', {}, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert status == 422 and 'origin' in body['fields'], body


def test_an_unknown_operator_or_field_fails_when_the_registry_is_built():
    class Bad(Model):
        place = Link(Location, filters={'kind__near': 'x'})
    with pytest.raises(ValueError):
        Registry(Location, Bad)

    class Missing(Model):
        place = Link(Location, filters={'colour__ne': 'red'})
    with pytest.raises(ValueError, match='Link filters must name stored target fields'):
        Registry(Location, Missing)
