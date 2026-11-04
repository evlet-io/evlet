import json

from evlet import Application, Database, Integer, Link, LocalAuth, Model, Policy, Registry, Rule, Text, identity
from tests.test_business import client, hashed, http, secured
from examples.playground.models import Customer, Project, ProjectTask


def test_discovery_requires_a_session_and_catalog_is_compact(secured):
    assert http(secured, 'GET', '/api/_discovery')[0] == 401
    c = client(secured)
    status, catalog, _ = c('GET', '/api/_discovery?limit=2')
    assert status == 200 and len(catalog['models']) == 2 and catalog['total'] == 6
    assert all('fields' not in model for model in catalog['models'])
    assert len(json.dumps(catalog)) < len(json.dumps(c('GET', '/api/schema')[1])) / 2
    assert c('GET', '/api/_discovery?q=project')[1]['models'][0]['name'] == 'project'
    assert c('GET', '/api/_discovery?offset=6')[1]['models'] == []
    for query in ('limit=0', 'limit=101', 'offset=-1', 'q=x&q=y', 'unknown=1'):
        assert c('GET', '/api/_discovery?' + query)[0] == 422
    assert c('GET', '/api/_discovery/missing')[0] == 404
    assert c('GET', '/api/_discovery/project?unknown=1')[0] == 422


def test_description_permissions_required_defaults_and_child_contract(secured):
    c = client(secured)
    item = c('GET', '/api/_discovery/project')[1]
    create, update = item['schemas']['create'], item['schemas']['update']
    assert 'name' in create['required'] and 'progress' not in create['required']
    assert update['required'] == [] and 'status' not in create['properties']
    assert create['properties']['progress']['default'] == 0
    child = create['properties']['children']['properties']['tasks']['items']['oneOf'][0]
    assert child['required'] == ['title'] and 'project' not in child['properties'] and 'id' not in child['properties']
    variants = update['properties']['children']['properties']['tasks']['items']['oneOf']
    assert variants[1]['required'] == ['id']
    assert c('POST', '/api/project', {'name': 'From discovery', 'children': {'tasks': [{'title': 'Task'}]}})[0] == 201
    viewer = client(secured, 'viewer')
    item = viewer('GET', '/api/_discovery/project')[1]
    assert item['operations'] == ['read'] and set(item['schemas']) == {'read'} and item['actions'] == []
    assert viewer('GET', '/api/_discovery')[1]['capabilities']['operations'] == ['read']


def test_discovery_does_not_leak_denied_models_fields_or_scoped_links(secured):
    secured.business.policy = Policy(secured.registry, rules={
        ('viewer', Customer): Rule(read_fields=('name',), scope=lambda user: {'active': True}),
        ('viewer', Project): Rule(read_fields=('name', 'customer')),
    })
    c = client(secured, 'viewer')
    customer = c('GET', '/api/_discovery/customer')[1]
    assert [field['name'] for field in customer['fields']] == ['name']
    assert 'email' not in json.dumps(customer)
    project = c('GET', '/api/_discovery/project')[1]
    assert 'customer' not in project['query']['fields']
    secured.business.policy = Policy(secured.registry, rules={('viewer', Project): Rule(operations=())})
    assert c('GET', '/api/_discovery/project')[0] == 404
    assert c('GET', '/api/_discovery/projecttask')[0] == 404
    assert 'project' not in [row['name'] for row in c('GET', '/api/_discovery')[1]['models']]


def test_child_schema_allows_only_the_callers_permitted_row_operations(secured):
    secured.business.policy = Policy(secured.registry, rules={('editor', ProjectTask): Rule(operations=('read', 'create'))})
    c = client(secured, 'editor')
    item = c('GET', '/api/_discovery/project')[1]
    variants = item['schemas']['update']['properties']['children']['properties']['tasks']['items']['oneOf']
    assert 'title' in variants[0]['properties']
    assert variants[1]['properties'] == {'id': {'type': 'string', 'pattern': identity.PATTERN}}
    secured.business.policy = Policy(secured.registry, rules={('editor', ProjectTask): Rule(operations=('read', 'update'))})
    item = c('GET', '/api/_discovery/project')[1]
    assert 'children' not in item['schemas']['create']['properties']
    variants = item['schemas']['update']['properties']['children']['properties']['tasks']['items']['oneOf']
    assert len(variants) == 1 and variants[0]['required'] == ['id']


def test_describing_does_not_evaluate_callable_defaults_or_reveal_link_default(tmp_path):
    calls = []
    class Person(Model):
        name = Text(required=True)
    class Note(Model):
        sequence = Integer(default=lambda: calls.append(1) or 9)
        person = Link(Person, default=42)
    db = Database(tmp_path / 'discovery.sqlite3', Registry(Person, Note))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    app = Application(db, auth=auth); app.initialize()
    from evlet import User
    from evlet.discovery import Discovery
    description = Discovery(app.business, User(1, 'test', 'viewer')).describe('note')
    assert calls == [] and '42' not in json.dumps(description)
    assert description['schemas']['read']['properties']['person']['anyOf'][1] == {'type': 'null'}
