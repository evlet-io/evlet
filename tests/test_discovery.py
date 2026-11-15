import json
from copy import deepcopy

import pytest

from evlet import Application, Database, Integer, Link, LocalAuth, Model, Policy, Registry, Rule, Text, identity
from tests.test_business import client, hashed, http, secured
from examples.playground.models import Customer, Project, ProjectTask


def test_discovery_requires_a_session_and_catalog_is_compact(secured):
    assert http(secured, 'GET', '/api/_discovery')[0] == 401
    c = client(secured)
    status, catalog, _ = c('GET', '/api/_discovery?limit=2')
    assert status == 200 and len(catalog['models']) == 2 and catalog['total'] == 7
    assert all('fields' not in model for model in catalog['models'])
    assert len(json.dumps(catalog)) < len(json.dumps(c('GET', '/api/schema')[1])) / 2
    assert c('GET', '/api/_discovery?q=project')[1]['models'][0]['name'] == 'project'
    assert c('GET', '/api/_discovery?offset=7')[1]['models'] == []
    for query in ('limit=0', 'limit=101', 'offset=-1', 'q=x&q=y', 'unknown=1'):
        assert c('GET', '/api/_discovery?' + query)[0] == 422
    assert c('GET', '/api/_discovery/missing')[0] == 404
    assert c('GET', '/api/_discovery/project?unknown=1')[0] == 422


def test_description_permissions_required_defaults_and_child_contract(secured):
    c = client(secured)
    item = c('GET', '/api/_discovery/project')[1]
    create, update = item['schemas']['create'], item['schemas']['update']
    assert 'title' in create['required'] and 'progress' not in create['required']
    assert update['required'] == [] and 'status' not in create['properties']
    assert create['properties']['progress']['default'] == 0
    child = create['properties']['children']['properties']['tasks']['items']['oneOf'][0]
    assert child['required'] == ['title'] and 'project' not in child['properties'] and 'id' not in child['properties']
    variants = update['properties']['children']['properties']['tasks']['items']['oneOf']
    assert variants[1]['required'] == ['id']
    assert c('POST', '/api/project', {'title': 'From discovery', 'children': {'tasks': [{'title': 'Task'}]}})[0] == 201
    viewer = client(secured, 'viewer')
    item = viewer('GET', '/api/_discovery/project')[1]
    assert item['operations'] == ['read'] and set(item['schemas']) == {'read'} and item['actions'] == []
    assert viewer('GET', '/api/_discovery')[1]['capabilities']['operations'] == ['read']


def test_discovery_does_not_leak_denied_models_fields_or_scoped_links(secured):
    secured.business.policy = Policy(secured.registry, rules={
        ('viewer', Customer): Rule(read_fields=('customer_name',), scope=lambda user: {'active': True}),
        ('viewer', Project): Rule(read_fields=('title', 'customer')),
    })
    c = client(secured, 'viewer')
    customer = c('GET', '/api/_discovery/customer')[1]
    assert [field['name'] for field in customer['fields']] == ['customer_name']
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
        full_name = Text(required=True)
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


def test_read_schema_validates_annotated_records_without_loosening_inputs(secured):
    validator = pytest.importorskip('jsonschema').Draft202012Validator
    c = client(secured)
    schemas = c('GET', '/api/_discovery/project')[1]['schemas']
    for schema in schemas.values():
        validator.check_schema(schema)
    read = validator(schemas['read'])
    status, created, _ = c('POST', '/api/project', {
        'title': 'Annotated project', 'children': {'tasks': [{'title': 'Task'}]},
    })
    assert status == 201, created
    assert {'_audit', '_moves', '_actions', '_locked', '_journal', '_row_ids'} <= created.keys()
    assert next(move for move in created['_moves'] if move['key'] == 'start')['primary'] is True
    read.validate(created)
    url = f"/api/project/{created['id']}"
    status, fetched, headers = c('GET', url)
    assert status == 200
    read.validate(fetched)
    status, updated, _ = c('PATCH', url, {'title': 'Updated'}, headers={b'if-match': headers[b'etag']})
    assert status == 200, updated
    read.validate(updated)

    invalid = []
    for path, value in (
        (('unknown_business_field',), True),
        (('_moves', 0, 'primary'), 'yes'),
        (('_moves', 0, 'unexpected'), True),
        (('_journal', 'count'), 'one'),
        (('_row_ids',), {'temporary-key': 'invalid-id'}),
        (('children_pages',), {'hidden_collection': {'total': 1, 'limit': 100, 'partial': True}}),
        (('children', 'tasks', 0, 'unexpected'), True),
    ):
        candidate = deepcopy(created)
        target = candidate
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        invalid.append(candidate)
    assert all(not read.is_valid(value) for value in invalid)
    for operation in ('create', 'update'):
        schema = validator(schemas[operation])
        assert not schema.is_valid({'title': 'Injected', '_moves': []})
        assert not schema.is_valid({'title': 'Invalid', 'progress': 101})
    secured.business.policy = Policy(secured.registry, rules={('viewer', ProjectTask): Rule(operations=())})
    restricted = client(secured, 'viewer')('GET', '/api/_discovery/project')[1]['schemas']['read']
    assert 'children' not in restricted['properties'] and 'children_pages' not in restricted['properties']


def test_read_schema_covers_approval_release_restore_and_action_metadata(tmp_path):
    validator = pytest.importorskip('jsonschema').Draft202012Validator
    from evlet import Child, State, Table, action
    from evlet.calc import F
    from tests.finite_fixtures import FiniteMeta
    from tests.test_rules_types import start

    class ReportLine(Child):
        label = Text(default='')

    class Report(Model):
        Meta = FiniteMeta
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
        title = Text(default='Report')
        amount = Integer(default=2)
        total = Integer(compute=F('amount') * 2)
        lines = Table(ReportLine, max_rows=101)
        status = State(State.Step('draft'), State.Step('issued', release=True), rules=(
            State.Rule('draft', 'issued', 'Issue', key='issue', primary=True, ask=('title',),
                       remark='optional', self_approve=True,
                       levels=(State.Level(by=('admin',), pending='Waiting for approval'),)),
            State.Rule('issued', 'draft', 'Reopen', key='reopen'),
        ))

        @action
        def summarize(self):
            return [self.title, self.total]

    app = start(Database(tmp_path / 'report.sqlite3', Registry(Report)))
    c = client(app)
    schema = c('GET', '/api/_discovery/report')[1]['schemas']['read']
    validator.check_schema(schema)
    read = validator(schema)
    status, paged, _ = c('POST', '/api/report', {'children': {'lines': [{} for _ in range(101)]}})
    assert status == 201, paged
    assert paged['children_pages']['lines'] == {'total': 101, 'limit': 100, 'partial': True}
    read.validate(paged)
    read.validate(c('GET', f"/api/report/{paged['id']}")[1])
    status, created, _ = c('POST', '/api/report', {})
    assert status == 201, created
    assert created['_calculation']['language'] == 'finite-v1'
    assert created['_version'] == {'number': None, 'released': False, 'state': None}
    assert created['_moves'][0]['remark'] == {'mode': 'optional', 'label': None, 'required': False}
    assert created['_moves'][0]['asks'] == ['title']
    assert created['_moves'][0]['level'] == 1 and created['_moves'][0]['can_approve'] is True
    read.validate(created)
    url = f"/api/report/{created['id']}"

    def post(suffix, values):
        headers = {b'if-match': c('GET', url)[2][b'etag']}
        status, record, _ = c('POST', url + suffix, values, headers=headers)
        assert status == 200, record
        read.validate(record)
        return record

    result = post('/actions/summarize', {})
    assert result['_outcome'] == ['Report', 4]
    issued = post('/moves/issue', {'title': 'Issued report', 'remark': 'Approved'})
    assert issued['_version'] == {'number': 0, 'released': True, 'state': 'issued'}
    post('/moves/reopen', {})
    restored = post('/restore', {'version': 0, 'remark': 'Restore release'})
    assert restored['_restore'] == {'version': 0, 'dropped': [], 'kept': []}
    malformed = deepcopy(restored)
    malformed['_version']['number'] = 'zero'
    assert not read.is_valid(malformed)
