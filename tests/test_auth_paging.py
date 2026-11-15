"""Account and live-key inventories filter before paging, on a real SQLite database."""

import json

import pytest

from evlet import Database, LocalAuth, Registry, ValidationError, qb


@pytest.fixture
def auth(tmp_path):
    database = Database(tmp_path / 'accounts.sqlite3', Registry())
    database.initialize()
    auth = LocalAuth(database, origins=('http://localhost:8000',), secure_cookies=False)
    auth.initialize()
    with database.transaction() as tx:
        for index in range(205):
            user = tx.connection.run(qb.insert('evlet_users', username=f'person{index:03}', password='',
                                               role='viewer', active=int(index % 2 == 0)).returning_id()).id
            tx.connection.run(qb.insert('evlet_api_keys', user_id=user, name=f'Key {index:03}', prefix=f'prefix{index:03}',
                                        hash='unused', scope=json.dumps({'access': 'read', 'models': None}), created_at=index))
    yield auth
    database.close()


def test_user_filters_find_accounts_beyond_the_first_page(auth):
    found = auth.user_page(q='PERSON20', active=False, role='viewer')
    assert [row['username'] for row in found['items']] == ['person201', 'person203']
    assert found['total'] == 2 and found['counts'] == {'all': 5, 'active': 3, 'inactive': 2}
    assert auth.user_page(q='person', offset=200)['total'] == 205
    assert len(auth.user_page(q='person', offset=200)['items']) == 5
    assert len(auth.users()) == 100
    assert auth.user_page(q='%')['items'] == []


def test_key_cursor_visits_old_keys_and_search_is_before_limit(auth):
    first = auth.key_page()
    assert len(first['items']) == 100 and first['total'] == 205
    second = auth.key_page(before=first['next_before'])
    third = auth.key_page(before=second['next_before'])
    ids = [row['id'] for page in (first, second, third) for row in page['items']]
    assert len(set(ids)) == 205 and ids == sorted(ids, reverse=True)
    assert third['next_before'] is None
    assert auth.key_page(q='KEY 000')['items'][0]['username'] == 'person000'
    assert auth.key_page(q='PERSON000')['total'] == 1
    assert auth.key_page(user='PERSON000')['total'] == 1
    assert auth.key_page(q='%')['items'] == []
    with auth.database.transaction() as tx:
        tx.connection.run(qb.update('evlet_api_keys', revoked_at=1).where(id=ids[-1]))
    assert auth.key_page(user='person000')['total'] == 0


@pytest.mark.parametrize('arguments', [{'limit': 0}, {'limit': True}, {'q': None}, {'q': 'x'*201}, {'active': 1}, {'offset': -1}])
def test_user_page_refuses_invalid_bounds(auth, arguments):
    with pytest.raises(ValidationError):
        auth.user_page(**arguments)


@pytest.mark.parametrize('arguments', [{'before': 0}, {'before': True}, {'owner': -1}, {'limit': 201}])
def test_key_page_refuses_invalid_bounds(auth, arguments):
    with pytest.raises(ValidationError):
        auth.key_page(**arguments)
