import pytest

from test_business import secured, hashed, client
from evlet import Policy, Rule
from examples.playground.models import Customer, Invoice, Project, ProjectTask, registry


@pytest.mark.parametrize('value', ['not-an-id', 0, True, 2**63])
def test_authenticated_invalid_link_returns_field_validation(secured, value):
    call = client(secured, 'editor')
    status, data, _ = call('POST', '/api/invoice', {
        'customer': value, 'reference': 'REVIEW', 'total': '1.00',
    })
    assert status == 422, (status, data)
    assert 'customer' in data['fields']


@pytest.mark.parametrize('value', ['invalid', 0, True, 2**63])
def test_authenticated_invalid_link_patch_is_validation_and_atomic(secured, value):
    customer = secured.database.create(Customer, customer_name='Review')
    invoice = secured.database.create(Invoice, customer=customer.id, reference='ORIGINAL', total='1.00')
    call = client(secured, 'editor')
    url = f'/api/invoice/{invoice.id}'
    # The invoice has a calculated payment status, so an update sends its ETag.
    status, data, _ = call('PATCH', url, {
        'customer': value, 'reference': 'CHANGED',
    }, headers={b'if-match': call('GET', url)[2][b'etag']})
    assert secured.database.get(Invoice, invoice.id).reference == 'ORIGINAL'
    assert status == 422, (status, data)
    assert 'customer' in data['fields']
    assert secured.database.get(Invoice, invoice.id).customer == customer.id


def test_child_create_without_update_is_supported_by_backend(secured):
    secured.business.policy = Policy(registry, rules={
        ('editor', ProjectTask): Rule(('read', 'create')),
    })
    call = client(secured, 'editor')
    child_schema = next(model for model in call('GET', '/api/schema')[1]['models']
                        if model['name'] == 'projecttask')
    assert child_schema['permissions'] == {'create': True, 'update': False, 'delete': False}
    status, data, _ = call('POST', '/api/project', {
        'title': 'Can create', 'children': {'tasks': [{'title': 'Allowed new task'}]},
    })
    assert status == 201, data
    assert data['children']['tasks'][0]['title'] == 'Allowed new task'


def test_child_delete_without_update_is_supported_by_backend(secured):
    call = client(secured, 'editor')
    status, project, _ = call('POST', '/api/project', {
        'title': 'Can delete', 'children': {'tasks': [{'title': 'Remove me'}]},
    })
    assert status == 201
    secured.business.policy = Policy(registry, rules={
        ('editor', ProjectTask): Rule(('read', 'delete')),
    })
    status, data, _ = call('PATCH', f'/api/project/{project["id"]}', {'children': {'tasks': []}})
    assert status == 200, data
    assert data['children']['tasks'] == []


@pytest.mark.parametrize('operation', ['create', 'delete'])
def test_child_collection_can_retain_rows_without_update_permission(secured, operation):
    project = secured.database.create(Project, title='Keep parent')
    first = secured.database.create(ProjectTask, project=project.id, title='Keep first')
    second = secured.database.create(ProjectTask, project=project.id, title='Keep second')
    secured.business.policy = Policy(registry, rules={
        ('editor', ProjectTask): Rule(('read', operation)),
    })
    call = client(secured, 'editor')
    rows = [{'id': first.id}, {'id': second.id}, {'title': 'New child'}] if operation == 'create' else [{'id': second.id}]
    status, data, _ = call('PATCH', f'/api/project/{project.id}', {'children': {'tasks': rows}})
    assert status == 200, data
    assert [row['title'] for row in data['children']['tasks']] == (['Keep first', 'Keep second', 'New child'] if operation == 'create' else ['Keep second'])
    assert secured.database.get(ProjectTask, second.id).title == 'Keep second'
    retained = [{'id': row['id']} for row in data['children']['tasks']]
    retained[0]['title'] = 'Forbidden update'
    assert call('PATCH', f'/api/project/{project.id}', {'title': 'Rollback parent', 'children': {'tasks': retained}})[0] == 403
    assert secured.database.get(Project, project.id).title == 'Keep parent'
    assert call('GET', f'/api/project/{project.id}')[1] == data


def test_child_retention_checks_parent_ownership_and_row_scope(secured):
    project = secured.database.create(Project, title='Owned')
    other = secured.database.create(Project, title='Other')
    hidden = secured.database.create(ProjectTask, project=project.id, title='Hidden', done=False)
    foreign = secured.database.create(ProjectTask, project=other.id, title='Foreign', done=True)
    secured.business.policy = Policy(registry, rules={
        ('editor', ProjectTask): Rule(('read', 'create'), scope=lambda user: {'done': True}),
    })
    call = client(secured, 'editor')
    path = f'/api/project/{project.id}'
    assert call('PATCH', path, {'children': {'tasks': [{'id': hidden.id}]}})[0] == 404
    assert call('PATCH', path, {'children': {'tasks': [{'id': foreign.id}]}})[0] == 422
    assert secured.database.count(ProjectTask) == 2
