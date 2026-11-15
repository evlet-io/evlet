"""Discovery shapes follow readable child collections recursively, including section rows."""

import json
from copy import deepcopy

import pytest

from evlet import Child, Database, Model, Policy, Registry, Rule, Table, Text
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import add_user, start


def test_nested_sections_validate_on_read_create_update_and_keep_permissions(tmp_path):
    validator = pytest.importorskip('jsonschema').Draft202012Validator
    class Part(Child):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'viewer': Rule(('read',), read_fields=('text',))}
        text = Text(default='part')
        secret = Text(default='hidden note')
    class Line(Child):
        text = Text(default='line')
        parts = Table(Part, sections=True)
    class Report(Model):
        Meta = FiniteMeta
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'viewer': Rule(('read',))}
        lines = Table(Line, sections=True)

    app = start(Database(tmp_path / 'nested.sqlite3', Registry(Report)))
    admin = client(app)
    description = admin('GET', '/api/_discovery/report')[1]
    schemas = description['schemas']
    for schema in schemas.values():
        validator.check_schema(schema)
    values = {'children': {'lines': [{'section': 'Group'}, {'_key': 'line', 'text': 'One', 'children': {
        'parts': [{'section': 'Parts'}, {'_key': 'part', 'text': 'Nested'}]}}]}}
    validator(schemas['create']).validate(values)
    status, created, _ = admin('POST', '/api/report', values)
    assert status == 201, created
    validator(schemas['read']).validate(created)
    url = '/api/report/' + created['id']
    response = admin('GET', url)
    validator(schemas['read']).validate(response[1])
    line = created['children']['lines'][1]
    changes = {'children': {'lines': {'update': [{'id': line['id'], 'children': {'parts': {
        'create': [{'_key': 'next', 'section': 'Extra'}, {'text': 'New part'}],
        'update': [{'id': line['children']['parts'][1]['id'], 'text': 'Changed'}]}}}]}}}
    validator(schemas['update']).validate(changes)
    result = admin('PATCH', url, changes, headers={b'if-match': response[2][b'etag']})
    assert result[0] == 200, result[1]
    validator(schemas['read']).validate(result[1])
    line_name = description['children'][0]['model']
    line_description = admin('GET', '/api/_discovery/' + line_name)[1]
    part_name = line_description['children'][0]['model']
    assert admin('GET', '/api/_discovery/' + part_name)[0] == 200
    malformed = deepcopy(created)
    malformed['children']['lines'][0]['text'] = 'Section rows have no row fields'
    assert not validator(schemas['read']).is_valid(malformed)
    viewer = add_user(app, 'viewer')
    hidden = viewer('GET', '/api/_discovery/report')[1]
    assert 'secret' not in json.dumps(hidden) and set(hidden['schemas']) == {'read'}
    validator(hidden['schemas']['read']).validate(viewer('GET', url)[1])
    app.business.policy = Policy(app.registry, rules={**app.business.policy.file_rules, ('viewer', app.registry.models[part_name]): Rule(())})
    hidden = viewer('GET', '/api/_discovery/report')[1]
    assert 'parts' not in json.dumps(hidden['schemas'])
    assert viewer('GET', '/api/_discovery/' + part_name)[0] == 404


def test_readonly_intermediate_row_does_not_advertise_writable_descendants(tmp_path):
    validator = pytest.importorskip('jsonschema').Draft202012Validator
    class Part(Child):
        permissions = {'editor': Rule(('read', 'create', 'update', 'delete'))}
        text = Text(default='part')
    class Line(Child):
        permissions = {'editor': Rule(('read',))}
        text = Text(default='line')
        parts = Table(Part)
    class Report(Model):
        Meta = FiniteMeta
        permissions = {'editor': Rule(('read', 'update'))}
        lines = Table(Line)
    app = start(Database(tmp_path / 'permissions.sqlite3', Registry(Report)))
    admin = client(app)
    created = admin('POST', '/api/report', {'children': {'lines': [{'children': {'parts': [{'text': 'Before'}]}}]}})
    assert created[0] == 201, created[1]
    editor = add_user(app, 'editor')
    url = '/api/report/' + created[1]['id']
    current = editor('GET', url)
    line = current[1]['children']['lines'][0]
    change = {'children': {'lines': {'update': [{'id': line['id'], 'children': {'parts': {'update': [
        {'id': line['children']['parts'][0]['id'], 'text': 'After'}]}}}]}}}
    schema = validator(editor('GET', '/api/_discovery/report')[1]['schemas']['update'])
    assert not schema.is_valid(change)
    assert editor('PATCH', url, change, headers={b'if-match': current[2][b'etag']})[0] == 403
    assert admin('GET', url)[1]['children']['lines'][0]['children']['parts'][0]['text'] == 'Before'
    echo = {'children': {'lines': [{'id': line['id']}]}}
    schema.validate(echo)
    assert editor('PATCH', url, echo, headers={b'if-match': current[2][b'etag']})[0] == 200
