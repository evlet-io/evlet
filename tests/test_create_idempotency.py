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


def test_plain_replay_answers_exactly_as_the_first_create(secured):
    person = client(secured, 'editor')
    values = {'customer_name': 'Same answer'}
    first = person('POST', '/api/customer', values, headers=KEY)
    replay = person('POST', '/api/customer', values, headers=KEY)
    assert first[0] == replay[0] == 201 and replay[1] == first[1]
    # One answer: the first create carries the ETag a replay carries, so a client can save without reloading.
    assert first[2][b'etag'] == replay[2][b'etag'] and first[2][b'location'] == replay[2][b'location']
    assert b'idempotency-replayed' not in first[2]
    assert person('PATCH', '/api/customer/' + first[1]['id'], {'customer_name': 'Renamed'},
                  headers={b'if-match': first[2][b'etag']})[0] == 200


def test_document_replay_answers_as_the_first_create_without_write_reports(tmp_path):
    from test_nested_documents import Plan, application, stages
    app = application(tmp_path, Plan)
    person = client(app)
    values = {'children': {'stages': stages()}}
    first = person('POST', '/api/plan', values, headers=KEY)
    replay = person('POST', '/api/plan', values, headers=KEY)
    assert first[0] == replay[0] == 201
    # Only the write itself reports the keys of new rows; the ETag never covers them.
    assert set(first[1]['_row_ids']) == {'A', 'B', 'a1', 'a2', 'b1'} and '_row_ids' not in replay[1]
    assert {key: value for key, value in first[1].items() if key != '_row_ids'} == replay[1]
    assert first[2][b'etag'] == replay[2][b'etag'] and replay[2][b'idempotency-replayed'] == b'true'
    assert replay[1]['_calculation']['model_schema_hash'] == app.registry.plans[Plan]['model_schema_hash']
    # Virtual (stored=False) results are part of both answers.
    assert [stage['hourly_rate'] for stage in replay[1]['children']['stages']] == ['100.00', '100.00']
    assert replay[2][b'location'] == f"/api/plan/{first[1]['id']}".encode()
