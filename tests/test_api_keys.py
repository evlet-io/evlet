"""API keys: shown once, hashed at rest, revocable, never more than their user, and the same checks as a session."""

import hashlib
import json
import logging
from urllib.parse import urlencode

import pytest

from evlet import Policy, Rule
from evlet import auth as auth_module
from examples.playground.models import Customer, Invoice, Project, registry
from test_business import PASSWORD, client, hashed, http, secured
from test_hosts_realms import portal  # noqa: F401


def with_key(app, token, method, path, data=None, client='127.0.0.1'):
    # No cookie, no CSRF token and no request header: the key alone authenticates.
    return http(app, method, path, data, headers={b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''}, client=client)


def create(c, name='Script', scope=None, user=None, personal=False):
    body = {'name': name, 'scope': scope or {'access': 'read', 'models': None}, **({'password': PASSWORD} if personal else {'user': user or 'admin'})}
    return c('POST', '/api/_settings/me/keys' if personal else '/api/_controls/keys', body)


def test_a_key_is_shown_once_and_only_its_hash_is_stored(secured, caplog):
    caplog.set_level(logging.DEBUG)
    admin = client(secured)
    status, created, _ = create(admin, 'Website lead form', {'access': 'write', 'models': ['Customer']}, user='editor')
    assert status == 201
    token, key = created['token'], created['key']
    assert token.startswith(key['prefix'] + '_') and key['username'] == 'editor' and key['scope'] == {'access': 'write', 'models': ['Customer']}
    listed = admin('GET', '/api/_controls/keys')[1]['items']
    assert [item['name'] for item in listed] == ['Website lead form'] and 'hash' not in listed[0]
    with secured.database._connection() as connection:
        row = dict(connection.execute('SELECT * FROM evlet_api_keys').fetchone())
        audit = json.dumps([dict(item) for item in connection.execute("SELECT * FROM evlet_audit WHERE area='API keys'")])
    assert row['hash'] == hashlib.sha256(token.encode()).hexdigest() and token not in json.dumps(row)
    secret_part = token.split('_', 2)[2]
    assert secret_part not in json.dumps(listed) + audit + caplog.text
    assert 'Created for editor' in audit
    assert with_key(secured, token, 'GET', '/api/customer')[0] == 200
    assert admin('GET', '/api/_controls/keys')[1]['items'][0]['last_used'] is not None


def test_revoked_keys_inactive_users_and_bad_keys_are_refused(secured):
    admin = client(secured)
    token = create(admin, user='viewer')[1]['token']
    assert with_key(secured, token, 'GET', '/api/customer')[0] == 200
    assert with_key(secured, token[:-1] + ('A' if token[-1] != 'A' else 'B'), 'GET', '/api/customer')[0] == 401
    viewer_id = next(item['id'] for item in admin('GET', '/api/_controls/users')[1]['items'] if item['username'] == 'viewer')
    assert admin('PATCH', f'/api/_controls/users/{viewer_id}', {'active': False})[0] == 204
    assert with_key(secured, token, 'GET', '/api/customer')[0] == 401
    assert admin('PATCH', f'/api/_controls/users/{viewer_id}', {'active': True})[0] == 204
    # Deactivation revoked the key for good: reactivating the person does not bring it back.
    assert admin('GET', '/api/_controls/keys')[1]['items'] == []
    assert with_key(secured, token, 'GET', '/api/customer')[:2] == (401, {'error': 'This API key is not valid. It may have been revoked.'})
    token = create(admin, user='viewer')[1]['token']
    key_id = admin('GET', '/api/_controls/keys')[1]['items'][0]['id']
    assert admin('DELETE', f'/api/_controls/keys/{key_id}')[1] == {'items': []}
    assert with_key(secured, token, 'GET', '/api/customer')[:2] == (401, {'error': 'This API key is not valid. It may have been revoked.'})
    assert admin('DELETE', f'/api/_controls/keys/{key_id}')[0] == 404
    with secured.database._connection() as connection:
        assert [row[0] for row in connection.execute("SELECT detail FROM evlet_audit WHERE area='API keys' ORDER BY id")] == ['Created for viewer', 'Created for viewer', 'Revoked']


def test_a_key_follows_its_scope_and_reads_only_unless_it_may_write(secured):
    admin = client(secured)
    read = create(admin, 'Reader', {'access': 'read', 'models': ['Customer']})[1]['token']
    write = create(admin, 'Writer', {'access': 'write', 'models': ['Customer']})[1]['token']
    assert with_key(secured, read, 'POST', '/api/customer', {'customer_name': 'From a script'})[:2] == (403, {'error': 'This key can only read.'})
    status, created, _ = with_key(secured, write, 'POST', '/api/customer', {'customer_name': 'From a script'})
    assert status == 201 and created['customer_name'] == 'From a script'
    assert with_key(secured, write, 'GET', '/api/project')[0] in (403, 404)
    assert with_key(secured, write, 'POST', '/api/project', {'title': 'Not in scope'})[0] in (403, 404)
    assert secured.database.count(Project) == 0
    names = [model['name'] for model in with_key(secured, read, 'GET', '/api/schema')[1]['models']]
    assert names == ['customer']
    # Keys never open Controls, account routes or the key routes themselves.
    assert with_key(secured, write, 'GET', '/api/_controls/state')[:2] == (404, {'error': 'Route not found'})
    assert with_key(secured, write, 'GET', '/api/auth/users')[0] == 403
    assert with_key(secured, write, 'POST', '/api/_settings/me/keys', {'name': 'x'})[0] == 403


def test_key_requests_keep_row_scopes_and_field_rules(secured):
    db = secured.database
    visible = db.create(Customer, customer_name='Visible', notes='secretphrase', active=True)
    hidden = db.create(Customer, customer_name='Hidden', active=False)
    rule = Rule(('read', 'create', 'update'), read_fields=('customer_name', 'active'), write_fields=('customer_name', 'active'), scope=lambda user: {'active': True})
    secured.business.policy = Policy(registry, rules={('editor', Customer): rule})
    token = create(client(secured), 'Editor key', {'access': 'write', 'models': None}, user='editor')[1]['token']
    page = with_key(secured, token, 'GET', '/api/customer')[1]
    assert page['total'] == 1 and page['items'] == [{'id': str(visible.id), 'customer_name': 'Visible', 'active': True}]
    assert with_key(secured, token, 'GET', f'/api/customer/{hidden.id}')[0] == 404
    assert with_key(secured, token, 'GET', '/api/customer?' + urlencode({'filters': json.dumps({'notes': 'secretphrase'})}))[0] == 403
    assert with_key(secured, token, 'PATCH', f'/api/customer/{visible.id}', {'notes': 'changed'})[0] == 403
    assert with_key(secured, token, 'DELETE', f'/api/customer/{visible.id}')[0] == 403


def test_a_personal_key_cannot_exceed_the_users_own_access(secured):
    secured.business.policy = Policy(registry, rules={('editor', Project): Rule(())})
    editor, viewer = client(secured, 'editor'), client(secured, 'viewer')
    status, payload, _ = create(editor, scope={'access': 'read', 'models': ['Project']}, personal=True)
    assert status == 422 and payload['fields']['scope'] == "editor can't read Project. A key can never do more than the user it acts as."
    status, payload, _ = create(viewer, scope={'access': 'write', 'models': None}, personal=True)
    assert status == 422 and payload['fields']['scope'] == 'viewer can only read these, so the key can only read.'
    assert create(viewer, ' ', personal=True)[1]['fields'] == {'name': 'Give the key a name so you can recognise it later.'}
    status, created, _ = create(editor, 'Mine', {'access': 'write', 'models': ['Customer', 'Invoice']}, personal=True)
    assert status == 201 and created['key']['username'] == 'editor'
    assert editor('POST', '/api/_settings/me/keys', {'name': 'Other', 'user': 'admin', 'password': PASSWORD})[:2] == (422, {'error': 'Validation failed', 'fields': {'_form': 'Use name, scope'}})
    # A new key asks who you are: the password here, since two-step sign-in is not on.
    assert editor('POST', '/api/_settings/me/keys', {'name': 'Other'})[1]['fields'] == {'password': 'Enter your password.'}
    assert editor('POST', '/api/_settings/me/keys', {'name': 'Other', 'password': 'not the password at all'})[1]['fields'] == {'password': 'Your password is not right.'}
    assert [item['name'] for item in editor('GET', '/api/_settings/me/keys')[1]['items']] == ['Mine']
    assert viewer('GET', '/api/_settings/me/keys')[1]['items'] == []
    assert viewer('DELETE', f"/api/_settings/me/keys/{created['key']['id']}")[0] == 404
    # The key follows its user: when the user loses write access, so does the key.
    with secured.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_users SET role='viewer' WHERE username='editor'")
    assert with_key(secured, created['token'], 'POST', '/api/customer', {'customer_name': 'After demotion'})[0] == 403
    assert with_key(secured, created['token'], 'GET', '/api/customer')[0] == 200
    assert editor('DELETE', f"/api/_settings/me/keys/{created['key']['id']}")[1] == {'items': []}
    assert with_key(secured, created['token'], 'GET', '/api/customer')[0] == 401


def test_only_admins_create_keys_that_act_as_admins(secured):
    admin = client(secured)
    assert admin('POST', '/api/_controls/roles', {'id': 'ops', 'base': 'editor'})[0] == 201
    assert admin('PATCH', '/api/_controls/roles/ops', {'opens_controls': True})[0] == 200
    with secured.database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role) SELECT ?, password, ? FROM evlet_users WHERE username=?', ('opsuser', 'ops', 'admin'))
    ops = client(secured, 'opsuser')
    assert create(ops, user='admin')[0] == 403
    assert create(ops, user='editor')[0] == 201
    admin_key = create(admin, 'Admin key', user='admin')[1]['key']['id']
    assert ops('DELETE', f'/api/_controls/keys/{admin_key}')[0] == 403


def test_key_requests_are_rate_limited(secured, monkeypatch):
    monkeypatch.setattr(auth_module, 'KEY_RATE', 3)
    token = create(client(secured))[1]['token']
    assert [with_key(secured, token, 'GET', '/api/customer')[0] for _ in range(4)] == [200, 200, 200, 429]
    monkeypatch.setattr(auth_module, 'KEY_FAILURES', 2)
    bad = token[:-2] + 'zz'
    assert [with_key(secured, bad, 'GET', '/api/customer', client='10.9.9.9')[0] for _ in range(3)] == [401, 401, 429]


def test_a_valid_key_is_never_throttled_by_other_keys_failures(secured, monkeypatch):
    monkeypatch.setattr(auth_module, 'KEY_FAILURES', 3)
    token = create(client(secured))[1]['token']
    prefix = token.split('_')[1]
    wrong = f'evk_{prefix}_' + 'A' * 43
    assert [with_key(secured, wrong, 'GET', '/api/customer', client='10.1.1.1')[0] for _ in range(4)] == [401, 401, 401, 429]
    assert with_key(secured, token, 'GET', '/api/customer', client='10.1.1.1')[0] == 200
    # Failures count per network and prefix: another prefix from the same network is still answered.
    assert with_key(secured, 'evk_zzzzzzzz_' + 'A' * 43, 'GET', '/api/customer', client='10.1.1.1')[0] == 401
    # The trusted proxy reading of the client address is used when the auth has one.
    secured.business.auth.client_address = lambda scope: '10.1.1.1'
    assert with_key(secured, wrong, 'GET', '/api/customer', client='10.7.7.7')[0] == 429


def test_only_a_well_formed_key_skips_the_request_header(secured):
    status, payload, _ = http(secured, 'POST', '/api/customer', {'customer_name': 'x'}, headers={b'authorization': b'Bearer junk', b'x-evlet-request': b''})
    assert (status, payload) == (403, {'error': 'Missing request protection header'})


def test_jobs_queued_with_a_key_keep_its_scope_and_stop_when_it_is_revoked(secured, monkeypatch):
    from evlet import Task
    seen = []
    jobs = secured.business.jobs
    monkeypatch.setitem(jobs.tasks, 'project_progress', Task('project_progress', lambda tx, job, payload: seen.append(job.user)))
    admin = client(secured)
    project = admin('POST', '/api/project', {'title': 'Keyed'})[1]
    created = create(admin, 'Worker', {'access': 'write', 'models': ['Project']})[1]
    token = created['token']
    assert with_key(secured, token, 'POST', f"/api/project/{project['id']}/actions/refresh_progress")[0] == 202
    assert jobs.run_one()
    assert seen[0].access is not None and seen[0].access.write and {model.__name__ for model in seen[0].access.models} >= {'Project'}
    assert not secured.business.policy.allows(seen[0], Customer, 'read')
    assert with_key(secured, token, 'POST', f"/api/project/{project['id']}/actions/refresh_progress")[0] == 202
    assert admin('DELETE', f"/api/_controls/keys/{created['key']['id']}")[0] == 200
    assert jobs.run_one() and len(seen) == 1
    with secured.database._connection() as connection:
        row = connection.execute('SELECT state, error FROM evlet_jobs ORDER BY id DESC LIMIT 1').fetchone()
    assert tuple(row) == ('failed', 'The API key that queued this job was revoked.')


def test_guard_checks_are_cached_per_key_scope(secured):
    from types import SimpleNamespace
    from evlet.auth import KeyAccess, User
    from evlet.states import readable
    guard = SimpleNamespace(reads=(('self', Customer, 'customer_name'),))
    policy = secured.business.policy
    assert readable(policy, User(2, 'editor', 'editor'), guard)
    assert not readable(policy, User(2, 'editor', 'editor', access=KeyAccess(True, frozenset({Project}))), guard)


# ---------- realms, Controls overrides and custom roles ----------

def test_keys_are_for_desk_users_on_desk_hosts(portal):
    from test_hosts_realms import DESK, PORTAL, sign_in
    _, _, admin = sign_in(portal, 'admin', DESK)
    status, payload, _ = admin('POST', '/api/_controls/keys', {'name': 'Shop', 'user': 'buyer', 'scope': {'access': 'read', 'models': None}})
    assert status == 422 and 'API keys are for desk users only' in payload['fields']['user']
    token = admin('POST', '/api/_controls/keys', {'name': 'Desk', 'user': 'viewer', 'scope': {'access': 'read', 'models': None}})[1]['token']
    bearer = {b'authorization': f'Bearer {token}'.encode(), b'x-evlet-request': b''}
    assert http(portal, 'GET', '/api/customer', headers={**bearer, b'host': DESK})[0] == 200
    assert http(portal, 'GET', '/api/customer', headers={**bearer, b'host': PORTAL})[:2] == (403, {'error': 'API keys work on desk hosts only.'})
    # A key whose user now has a portal role stops working.
    with portal.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_users SET role='portal' WHERE username='viewer'")
    assert http(portal, 'GET', '/api/customer', headers={**bearer, b'host': DESK})[0] == 401
    _, _, buyer = sign_in(portal, 'buyer', PORTAL)
    assert buyer('GET', '/api/_settings/me/keys')[0] == 404
    # Personal assistants and mailboxes are desk only too.
    assert buyer('GET', '/api/_settings/me/mailbox')[0] == 404
    assert buyer('GET', '/api/_settings/me/ai')[0] == 404


def test_a_key_follows_controls_overrides_and_custom_roles(secured):
    admin = client(secured)
    assert admin('POST', '/api/_controls/roles', {'id': 'clerk', 'base': 'editor'})[0] == 201
    assert admin('POST', '/api/_controls/users', {'username': 'casey', 'password': 'a long enough password', 'role': 'clerk'})[0] in (200, 201)
    token = create(admin, 'Clerk script', {'access': 'write', 'models': None}, user='casey')[1]['token']
    assert with_key(secured, token, 'GET', '/api/customer')[0] == 200
    # Controls takes read away from the role: the key loses it at once, without being changed.
    change = {'kind': 'perm', 'key': 'clerk|Customer', 'value': {'ops': [], 'read': None, 'write': None, 'scope': None}}
    assert admin('POST', '/api/_controls/overrides', {'changes': [change]})[0] == 200
    assert with_key(secured, token, 'GET', '/api/customer')[0] in (403, 404)
    assert with_key(secured, token, 'GET', '/api/project')[0] == 200
    # A new key can't name what the role can no longer read.
    status, payload, _ = create(admin, 'Too much', {'access': 'read', 'models': ['Customer']}, user='casey')
    assert status == 422 and "can't read Customer" in payload['fields']['scope']
