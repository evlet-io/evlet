"""Opt-in create retries commit once and reapply current read permissions on real SQLite."""

from concurrent.futures import ThreadPoolExecutor

from evlet import Policy, Rule, qb
from examples.playground.models import Customer, Project, ProjectTask, registry
from test_business import client, hashed, secured  # noqa: F401


KEY = {b'idempotency-key': b'create-test-001'}


def test_retry_after_lost_answer_returns_same_record_and_children(secured):
    person = client(secured, 'editor')
    values = {'title': 'One project', 'children': {'tasks': [{'title': 'Only task'}]}}
    original = person('POST', '/api/project', values, headers=KEY)
    assert original[0] == 201
    # The caller can lose the answer after commit, then send exactly the same request.
    retry = person('POST', '/api/project', values, headers=KEY)
    assert retry[0] == 201 and retry[1]['id'] == original[1]['id']
    assert retry[1]['children']['tasks'][0]['id'] == original[1]['children']['tasks'][0]['id']
    assert retry[2][b'idempotency-replayed'] == b'true'
    assert secured.database.count(Project) == secured.database.count(ProjectTask) == 1


def test_concurrent_plain_creates_with_one_key_commit_once(secured):
    person = client(secured, 'editor')
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(person, 'POST', '/api/customer', {'customer_name': 'Once'}, headers=KEY) for _ in range(2)]
        results = [request.result(timeout=10) for request in requests]
    assert [item[0] for item in results] == [201, 201]
    assert results[0][1]['id'] == results[1][1]['id'] and secured.database.count(Customer) == 1


def test_different_values_conflict_and_failed_validation_does_not_reserve_key(secured):
    person = client(secured, 'editor')
    assert person('POST', '/api/customer', {}, headers=KEY)[0] == 422
    first = person('POST', '/api/customer', {'customer_name': 'First'}, headers=KEY)
    assert first[0] == 201
    assert person('POST', '/api/customer', {'customer_name': 'Different'}, headers=KEY)[0] == 409
    assert secured.database.count(Customer) == 1


def test_replay_uses_current_field_and_row_permissions(secured):
    person = client(secured, 'editor')
    values = {'customer_name': 'Customer', 'notes': 'Confidential'}
    first = person('POST', '/api/customer', values, headers=KEY)
    assert first[0] == 201 and first[1]['notes'] == 'Confidential'
    secured.business.policy = Policy(registry, rules={('editor', Customer): Rule(('read', 'create'), read_fields=('customer_name',))})
    replay = person('POST', '/api/customer', values, headers=KEY)
    assert replay[0] == 201 and 'notes' not in replay[1]
    secured.business.policy = Policy(registry, rules={('editor', Customer): Rule(('read', 'create'), scope=lambda user: {'active': False})})
    assert person('POST', '/api/customer', values, headers=KEY)[0] == 404
    assert secured.database.count(Customer) == 1


def test_key_is_scoped_to_account_and_expiry_allows_a_new_create(secured):
    editor, admin = client(secured, 'editor'), client(secured)
    values = {'customer_name': 'Shared values'}
    first = editor('POST', '/api/customer', values, headers=KEY)
    second = admin('POST', '/api/customer', values, headers=KEY)
    assert first[0] == second[0] == 201 and first[1]['id'] != second[1]['id']
    with secured.database.transaction() as tx:
        tx.connection.run(qb.update('evlet_create_requests', expires=0))
    third = editor('POST', '/api/customer', values, headers=KEY)
    assert third[0] == 201 and third[1]['id'] not in (first[1]['id'], second[1]['id'])


def test_invalid_key_is_refused_before_create_and_deleted_records_are_not_recreated(secured):
    person = client(secured, 'editor')
    values = {'customer_name': 'Once'}
    for key in (b'', b'x'*129, b'has spaces', b'\xff'):
        assert person('POST', '/api/customer', values, headers={b'idempotency-key': key})[0] == 422
    first = person('POST', '/api/customer', values, headers=KEY)[1]
    assert person('DELETE', '/api/customer/' + first['id'])[0] == 204
    assert person('POST', '/api/customer', values, headers=KEY)[0] == 404
    assert secured.database.count(Customer) == 0
