"""Controls reaches late accounts and live credentials through bounded HTTP pages."""

import json

from evlet import qb
from tests.test_business import client, hashed, http, secured


def accounts(app, hashed, count):
    with app.database.transaction() as tx:
        return [tx.connection.run(qb.insert('evlet_users', username=f'person{index:03}', password=hashed,
                                            role='editor' if index % 2 else 'viewer', active=int(index != 149)).returning_id()).id
                for index in range(count)]


def test_users_filter_and_page_before_rendering_with_correct_counts(secured, hashed):
    ids = accounts(secured, hashed, 151)
    c = client(secured)
    first = c('GET', '/api/_controls/users')[1]
    assert len(first['items']) == 100 and first['total'] >= 151
    final = c('GET', '/api/_controls/users?q=person&offset=150')[1]
    assert [row['username'] for row in final['items']] == ['person150']
    assert final['total'] == 151 and final['limit'] == 100
    match = c('GET', '/api/_controls/users?q=PERSON14&role=editor&active=false')[1]
    assert [row['username'] for row in match['items']] == ['person149']
    assert match['total'] == 1 and match['counts'] == {'all': 5, 'active': 4, 'inactive': 1}
    assert c('PATCH', f'/api/_controls/users/{ids[149]}', {'role': 'viewer', 'active': True})[0] == 204
    changed = c('GET', '/api/_controls/users?q=person149&role=viewer&active=true')[1]
    assert changed['total'] == 1 and changed['items'][0]['active'] is True
    status, key, _ = c('POST', '/api/_controls/keys', {'user': 'person150', 'name': 'Last account', 'scope': {'access': 'read', 'models': None}})
    assert status == 201 and key['key']['user_id'] == ids[150], key
    assert c('GET', '/api/_controls/keys?user=person150')[1]['total'] == 1


def test_key_cursors_reach_and_revoke_the_oldest_live_key_during_insertions(secured, hashed):
    ids = accounts(secured, hashed, 11)
    c = client(secured)
    status, oldest, _ = c('POST', '/api/_controls/keys', {'user': 'person000', 'name': 'Oldest credential', 'scope': {'access': 'read', 'models': None}})
    assert status == 201, oldest
    with secured.database.transaction() as tx:
        for index in range(1, 201):
            tx.connection.run(qb.insert('evlet_api_keys', user_id=ids[index // 20], name=f'Credential {index:03}',
                                        prefix=f'evk_{index:08}', hash='not-used', scope=json.dumps({'access': 'read', 'models': None}), created_at=index))
    first = c('GET', '/api/_controls/keys')[1]
    assert len(first['items']) == 100 and first['total'] == 201
    assert oldest['key']['id'] not in {row['id'] for row in first['items']}
    status, new, _ = c('POST', '/api/_controls/keys', {'user': 'person010', 'name': 'Concurrent new key', 'scope': {'access': 'read', 'models': None}})
    assert status == 201, new
    found, page = list(first['items']), first
    while page['next_before'] is not None:
        status, page, _ = c('GET', f"/api/_controls/keys?before={page['next_before']}")
        assert status == 200 and len(page['items']) <= 100
        found.extend(page['items'])
    assert len(found) == len({row['id'] for row in found}) == 201
    assert found[-1]['id'] == oldest['key']['id'] and new['key']['id'] not in {row['id'] for row in found}
    match = c('GET', '/api/_controls/keys?q=OLDEST&user=PERSON000')[1]
    assert match['total'] == 1 and match['items'][0]['id'] == oldest['key']['id']
    bearer = {b'authorization': ('Bearer ' + oldest['token']).encode()}
    assert http(secured, 'GET', '/api/schema', headers=bearer)[0] == 200
    assert c('DELETE', f"/api/_controls/keys/{oldest['key']['id']}")[0] == 200
    assert c('GET', '/api/_controls/keys?q=oldest')[1]['total'] == 0
    assert c('GET', '/api/_controls/keys')[1]['total'] == 201
    assert http(secured, 'GET', '/api/schema', headers=bearer)[0] == 401


def test_controls_pages_reject_unbounded_queries_and_keep_access_checks(secured):
    c = client(secured)
    for path in (
        '/users?limit=201', '/users?offset=-1', '/users?offset=bad', '/users?active=1',
        '/users?role=admin&role=viewer', '/users?q=' + 'x' * 201, '/users?unknown=1',
        '/keys?limit=201', '/keys?before=-1', '/keys?before=0', '/keys?before=bad',
        '/keys?user=bad%20name', '/keys?q=' + 'x' * 201, '/keys?user=admin&user=viewer', '/keys?unknown=1',
    ):
        assert c('GET', '/api/_controls' + path)[0] == 422, path
    viewer = client(secured, 'viewer')
    assert viewer('GET', '/api/_controls/users?q=admin')[0] == 404
    assert viewer('GET', '/api/_controls/keys?q=admin')[0] == 404
