import asyncio
import base64
import hashlib
import json
import sqlite3
from urllib.parse import urlencode

import pytest

from evlet import Action, Application, ChildTable, Database, LocalAuth, Policy, Rule, ValidationError
from evlet.auth import password_hash
from examples.playground.app import create_app
from examples.playground.models import Customer, Invoice, Project, ProjectTask, registry

PASSWORD = 'a sufficiently long test password'


def http(app, method, path, data=None, *, cookie='', csrf='', headers=None, client='127.0.0.1'):
    async def run():
        route, _, query = path.partition('?')
        raw = json.dumps({} if data is None else data).encode()
        events = [{'type': 'http.request', 'body': raw}]
        sent = []
        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}
        async def send(event):
            sent.append(event)
        request_headers = {b'host': b'127.0.0.1:8000', b'content-type': b'application/json', b'x-evlet-request': b'1', b'cookie': cookie.encode(), b'x-csrf-token': csrf.encode()}
        request_headers.update(headers or {})
        await app({'type': 'http', 'method': method, 'path': route, 'query_string': query.encode(), 'headers': list(request_headers.items()), 'client': (client, 1234)}, receive, send)
        status, response_headers, content = sent[0]['status'], dict(sent[0]['headers']), sent[1]['body']
        payload = json.loads(content) if content and response_headers.get(b'content-type', b'').startswith(b'application/json') else content
        return status, payload, response_headers
    return asyncio.run(run())


@pytest.fixture(scope='module')
def hashed():
    return password_hash(PASSWORD)


@pytest.fixture
def secured(tmp_path, monkeypatch, hashed):
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'business.sqlite3'))
    app = create_app()
    app.database.initialize()
    app.business.initialize()
    with app.database.transaction() as tx:
        for role in ('admin', 'editor', 'viewer'):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password', (role, hashed, role))
    return app


def client(app, role='admin'):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': role, 'password': PASSWORD})
    assert status == 200, data
    if 'two_step' in data:
        # Instances require two-step sign-in for admin: set it up with the given key, then use backup codes.
        import base64
        from evlet.settings import check_totp, totp
        import time
        codes = app.__dict__.setdefault('_test_codes', {})
        if data['two_step'] == 'setup':
            raw = base64.b32decode(data['secret'] + '=' * (-len(data['secret']) % 8))
            code = totp(raw, int(time.time() // 30))
            assert check_totp(raw, code) is not None
        else:
            code = codes[role].pop()
        status, data, headers = http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': code})
        assert status == 200, data
        codes.setdefault(role, data.get('backup_codes', []))
    cookie = headers[b'set-cookie'].decode().split(';')[0]
    def call(method, path, data=None, **kwargs):
        return http(app, method, path, data, cookie=cookie, csrf=data_csrf, **kwargs)
    data_csrf = data['csrf']
    call.cookie, call.csrf = cookie, data_csrf
    return call


def test_password_hash_has_random_salt_and_secure_cost(hashed):
    other = password_hash(PASSWORD)
    assert other != hashed and other.startswith('scrypt:')
    assert password_hash(PASSWORD, hashed.split(':')[1]) == hashed
    with pytest.raises(ValidationError):
        password_hash('short')


def test_authentication_cookie_csrf_logout_and_expiry(secured):
    assert http(secured, 'GET', '/api/schema')[0] == 401
    session = http(secured, 'GET', '/api/auth/session')[1]
    assert session['user'] is None and session['setup'] is False
    assert session['application']['playground'] is True
    c = client(secured)
    assert c('GET', '/api/auth/session')[1]['user']['role'] == 'admin'
    assert http(secured, 'POST', '/api/customer', {'customer_name': 'No token'}, cookie=c.cookie)[0] == 403
    assert c('POST', '/api/customer', {'customer_name': 'Blocked'}, headers={b'x-evlet-request': b''})[0] == 403
    assert c('POST', '/api/customer', {'customer_name': 'Allowed'})[0] == 201
    with secured.database._connection() as db:
        token = db.execute('SELECT token FROM evlet_sessions').fetchone()[0]
        assert token == hashlib.sha256(c.cookie.split('=')[1].encode()).hexdigest()
    # Writes are JSON only: a sign-out without the JSON type is refused and the session stays
    # (the browser client sends an empty object for writes without a body).
    assert c('POST', '/api/auth/logout', headers={b'content-type': b''})[0] == 415
    assert c('GET', '/api/schema')[0] == 200
    assert c('POST', '/api/auth/logout')[0] == 204
    assert c('GET', '/api/schema')[0] == 401
    c = client(secured)
    with secured.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_sessions SET expires=0')
    assert c('GET', '/api/schema')[0] == 401


@pytest.mark.parametrize('headers', [{b'host': b'evil.test'}, {b'origin': b'http://evil.test'}, {b'origin': b'null'}, {b'sec-fetch-site': b'cross-site'}])
def test_reject_cross_origin_and_dns_rebinding(secured, headers):
    assert http(secured, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD}, headers=headers)[0] == 403


def test_cookie_security_and_login_throttle(secured):
    secured.business.auth.secure_cookies = True
    status, _, headers = http(secured, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
    assert status == 200
    cookie = headers[b'set-cookie']
    assert all(part in cookie for part in (b'HttpOnly', b'SameSite=Strict', b'Secure', b'Path=/'))
    for i in range(5):
        assert http(secured, 'POST', '/api/auth/login', {'username': 'unknown', 'password': 'bad'})[0] == 401
    assert http(secured, 'POST', '/api/auth/login', {'username': 'unknown', 'password': 'bad'})[0] == 429
    assert http(secured, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})[0] == 200


def test_setup_loopback_once_and_no_account_role_override(tmp_path):
    db = Database(tmp_path / 'setup.sqlite3', registry)
    db.initialize()
    auth = LocalAuth(db, origins=['http://127.0.0.1:8000'], secure_cookies=False, allow_setup=True)
    app = Application(db, auth=auth); app.business.initialize()
    payload = {'username': 'owner', 'password': PASSWORD}
    assert http(app, 'POST', '/api/auth/setup', payload, client='192.0.2.1')[0] == 403
    assert http(app, 'POST', '/api/auth/setup', payload)[0] == 201
    assert http(app, 'POST', '/api/auth/setup', payload)[0] == 403
    assert auth.users()[0]['role'] == 'admin'
    assert http(app, 'GET', '/api/auth/session')[1]['setup'] is False


def test_account_management_revokes_sessions_and_preserves_last_admin(secured):
    admin, viewer = client(secured), client(secured, 'viewer')
    assert viewer('GET', '/api/auth/users')[0] == 403
    # The admin here is the built-in Administrator (account 1), which can't be disabled or lose its role; the viewer is 4 (the fixture's upsert of admin uses up id 2).
    assert admin('PATCH', '/api/auth/users/1', {'active': False})[0] == 409
    assert admin('PATCH', '/api/auth/users/1', {'role': 'editor'})[0] == 409
    assert admin('POST', '/api/auth/users', {'username': 'other', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    assert admin('PATCH', '/api/auth/users/4', {'role': 'editor'})[0] == 204
    assert viewer('GET', '/api/schema')[0] == 401
    assert all('password' not in row for row in admin('GET', '/api/auth/users')[1]['items'])


def test_project_tasks_save_the_full_lists_the_record_page_sends(secured):
    # The schema tells the browser that Project tasks take only full lists; change sets and _key stay refused.
    c = client(secured, 'editor')
    project = next(m for m in c('GET', '/api/schema')[1]['models'] if m['name'] == 'project')
    assert [(child['name'], child['row_changes']) for child in project['children']] == [('tasks', False)]
    status, created, _ = c('POST', '/api/project', {'title': 'Explore Evlet', 'children': {'tasks': [{'title': 'Read', 'done': False}]}})
    assert status == 201, created
    path, first = f"/api/project/{created['id']}", created['children']['tasks'][0]['id']
    assert c('PATCH', path, {'children': {'tasks': [{'id': first}, {'title': 'Try', 'done': False}]}})[0] == 200
    second = c('GET', path)[1]['children']['tasks'][1]['id']
    status, saved, _ = c('PATCH', path, {'children': {'tasks': [{'id': first, 'done': True}, {'id': second}, {'title': 'Ship', 'done': False}]}})
    assert status == 200, saved
    assert [(task['title'], task['done']) for task in saved['children']['tasks']] == [('Read', True), ('Try', False), ('Ship', False)]
    status, refused, _ = c('PATCH', path, {'children': {'tasks': {'update': [{'id': first, 'done': False}]}}})
    assert status == 422 and refused['fields']['children'].endswith('Use a known child collection with at most 100 rows')
    assert c('POST', '/api/project', {'title': 'Keyed', 'children': {'tasks': [{'_key': 'n1', 'title': 'Plan'}]}})[0] == 403
    assert len(c('GET', path)[1]['children']['tasks']) == 3


def test_deleting_a_project_removes_more_than_100_tasks(secured):
    with secured.database.transaction() as tx:
        project = tx.create(Project, title='Large')
        for index in range(150):
            tx.create(ProjectTask, project=project.id, title=f'Task {index}')
    status, _, _ = client(secured)('DELETE', f'/api/project/{project.id}')
    assert status == 204
    assert secured.database.count(ProjectTask, filters={'project': project.id}) == 0


def test_viewer_cannot_mutate_any_business_surface(secured):
    project = secured.database.create(Project, title='Private')
    c = client(secured, 'viewer')
    schema = c('GET', '/api/schema')[1]
    model = next(m for m in schema['models'] if m['name'] == 'project')
    assert not any(model['permissions'].values()) and not model['actions']
    for method, path, body in [('POST', '/api/project', {'title': 'No'}), ('PATCH', f'/api/project/{project.id}', {'title': 'No'}), ('DELETE', f'/api/project/{project.id}', {}), ('POST', f'/api/project/{project.id}/actions/start', {}), ('POST', f'/api/project/{project.id}/attachments', {'name': 'a', 'content': 'YQ=='})]:
        assert c(method, path, body)[0] == 403
    assert c('GET', f'/api/project/{project.id}')[0] == 200


def test_model_field_row_rules_apply_to_list_count_search_detail_writes_and_links(secured):
    db = secured.database
    visible = db.create(Customer, customer_name='Visible', notes='secretphrase', active=True)
    hidden = db.create(Customer, customer_name='Hidden', active=False)
    invoice = db.create(Invoice, customer=hidden.id, reference='Restricted link', total='5.00')
    rule = Rule(('read', 'create', 'update'), read_fields=('customer_name', 'active'), write_fields=('customer_name', 'active'), scope=lambda user: {'active': True})
    secured.business.policy = Policy(registry, rules={('editor', Customer): rule, ('editor', Project): Rule(())})
    c = client(secured, 'editor')
    page = c('GET', '/api/customer?limit=1')[1]
    assert page['total'] == 1 and page['items'] == [{'id': str(visible.id), 'customer_name': 'Visible', 'active': True}]
    assert c('GET', '/api/customer?q=secretphrase')[1]['total'] == 0
    assert c('GET', f'/api/customer/{hidden.id}')[0] == 404
    assert c('GET', '/api/customer?' + urlencode({'filters': json.dumps({'active': False})}))[1]['total'] == 0
    for query in ({'filters': json.dumps({'notes': 'secretphrase'})}, {'order_by': 'notes'}, {'filters': json.dumps({'customer__name': 'Hidden'})}):
        assert c('GET', '/api/customer?' + urlencode(query))[0] == 403
    assert c('GET', f'/api/invoice/{invoice.id}')[1]['customer'] is None
    assert c('POST', '/api/invoice', {'customer': str(hidden.id), 'reference': 'Forbidden', 'total': '1.00'})[0] == 404
    assert c('PATCH', f'/api/customer/{visible.id}', {'notes': 'override'})[0] == 403
    assert c('PATCH', f'/api/customer/{visible.id}', {'active': False})[0] == 404
    assert db.get(Customer, visible.id).active is True
    assert c('POST', '/api/customer', {'customer_name': 'Outside scope', 'active': False})[0] == 404
    assert db.count(Customer) == 2
    schema = c('GET', '/api/schema')[1]
    assert 'project' not in [m['name'] for m in schema['models']]
    assert [f['name'] for f in schema['models'][0]['fields']] == ['customer_name', 'active']


def test_atomic_children_ownership_replacement_and_action_workflow(secured):
    c = client(secured, 'editor')
    status, project, _ = c('POST', '/api/project', {'title': 'Work', 'children': {'tasks': [{'title': 'Build'}, {'title': 'Test'}]}})
    assert status == 201, project
    pid = project['id']; path = f'/api/project/{pid}'
    tasks = project['children']['tasks']
    assert len(tasks) == 2 and not tasks[0]['done']
    assert c('GET', '/api/projecttask')[0] == 404
    assert c('PATCH', path, {'status': 'done'})[0] == 403
    assert c('POST', path + '/actions/complete')[0] == 422
    assert c('POST', path + '/actions/start')[1]['status'] == 'active'
    assert c('POST', path + '/actions/start')[0] == 422
    assert c('POST', path + '/actions/complete')[0] == 422
    before = c('GET', path)[1]
    assert c('PATCH', path, {'title': 'Rollback', 'children': {'tasks': [{'title': ''}]}})[0] == 422
    assert c('GET', path)[1] == before
    other = c('POST', '/api/project', {'title': 'Other', 'children': {'tasks': [{'title': 'Other'}]}})[1]
    assert c('PATCH', path, {'children': {'tasks': [{'id': other['children']['tasks'][0]['id'], 'title': 'Hijack'}]}})[0] == 422
    assert c('PATCH', path, {'children': {'tasks': [{'title': 'Hijack', 'project': other['id']}]}})[0] == 403
    assert c('PATCH', path, {'children': {'tasks': [{**task, 'done': True} for task in tasks]}})[0] == 200
    result = c('POST', path + '/actions/complete')[1]
    assert result['status'] == 'done' and result['progress'] == 100
    assert c('DELETE', path)[0] == 204
    assert secured.database.count(ProjectTask, filters={'project': pid}) == 0


def test_action_transaction_rolls_back_all_writes(secured):
    def fail(tx, user, project):
        tx.update(Project, project.id, progress=99)
        tx.create(ProjectTask, project=project.id, title='Will roll back')
        raise ValidationError({'_form': 'Rejected'})
    secured.business.actions[Project, 'fail'] = Action(Project, 'fail', 'Fail', fail)
    project = secured.database.create(Project, title='Atomic')
    c = client(secured)
    assert c('POST', f'/api/project/{project.id}/actions/fail')[0] == 422
    assert secured.database.get(Project, project.id).progress == 0
    assert secured.database.count(ProjectTask) == 0


def test_private_attachment_authorization_download_cleanup_and_bounds(secured):
    c = client(secured)
    project = c('POST', '/api/project', {'title': 'Files'})[1]
    other = c('POST', '/api/project', {'title': 'Other'})[1]
    path = f'/api/project/{project["id"]}'
    raw = b'<script>this must download, never render</script>'
    content = base64.b64encode(raw).decode()
    status, file, _ = c('POST', path + '/attachments', {'name': 'notes.html', 'content': content})
    assert status == 201
    download = path + f'/attachments/{file["id"]}'
    assert http(secured, 'GET', download)[0] == 401
    status, data, headers = c('GET', download)
    assert status == 200 and data == raw
    assert headers[b'content-type'] == b'application/octet-stream' and b'attachment' in headers[b'content-disposition']
    assert c('GET', f'/api/project/{other["id"]}/attachments/{file["id"]}')[0] == 404
    viewer = client(secured, 'viewer')
    assert viewer('GET', download)[0] == 200
    assert viewer('DELETE', download)[0] == 403
    for name, data in [('../escape', content), ('line\nname', content), ('bad', '%%%'), ('empty', ''), ('large', base64.b64encode(b'x' * (1024*1024+1)).decode())]:
        assert c('POST', path + '/attachments', {'name': name, 'content': data})[0] == 422
    assert c('DELETE', path)[0] == 204
    assert c('GET', download)[0] == 404
    with secured.database._connection() as db:
        assert db.execute('SELECT count(*) FROM evlet_attachments').fetchone()[0] == 0


def test_rule_configuration_and_layout_validation(secured):
    with pytest.raises(ValueError):
        Policy(registry, rules={('editor', Customer): Rule(read_fields=('missing',))})
    with pytest.raises(ValueError):
        Application(secured.database, actions=(Action(Project, 'a', 'A', lambda *args: None),))
    with pytest.raises(ValueError):
        LocalAuth(secured.database, origins=['http://example.test'], secure_cookies=False)


def test_concurrent_completion_commits_once(secured):
    from concurrent.futures import ThreadPoolExecutor
    project = secured.database.create(Project, title='Concurrent', status='active')
    secured.database.create(ProjectTask, project=project.id, title='Done', done=True)
    c = client(secured, 'editor')
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: c('POST', f'/api/project/{project.id}/actions/complete')[0], range(2)))
    assert sorted(results) == [200, 422]
    assert secured.database.get(Project, project.id).progress == 100


def test_concurrent_admin_changes_keep_one_active_admin(secured, hashed):
    from concurrent.futures import ThreadPoolExecutor
    with secured.database.transaction() as tx:
        # Two ordinary admins, and the built-in Administrator without a password, so it does not count.
        tx.connection.execute("UPDATE evlet_users SET role='admin' WHERE id IN (3, 4)")
        tx.connection.execute("UPDATE evlet_users SET password='' WHERE id=1")
    def disable(user_id):
        from evlet.auth import AccessError
        try:
            secured.business.auth.update_user(user_id, {'active': False})
            return 204
        except AccessError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(disable, [3, 4]))
    assert sorted(results) == [204, 409]
    # Besides the built-in Administrator, which has no password yet and so does not count.
    assert sum(user['active'] and user['role'] == 'admin' and user['username'] != 'admin' for user in secured.business.auth.users()) == 1


def test_child_permissions_rollback_parent_and_do_not_delete_hidden_rows(secured):
    c = client(secured, 'editor')
    project = c('POST', '/api/project', {'title': 'Original', 'children': {'tasks': [{'title': 'Hidden'}]}})[1]
    secured.business.policy = Policy(registry, rules={('editor', ProjectTask): Rule(('read', 'create', 'update', 'delete'), scope=lambda user: {'done': True})})
    path = f'/api/project/{project["id"]}'
    assert c('GET', path)[1]['children']['tasks'] == []
    assert c('PATCH', path, {'title': 'Attempt', 'children': {'tasks': []}})[0] == 200
    assert secured.database.get(Project, project['id']).title == 'Attempt'
    assert secured.database.count(ProjectTask) == 1
    assert c('DELETE', path)[0] == 404


def test_scope_applies_to_actions_and_attachment_downloads(secured):
    c = client(secured, 'editor')
    project = c('POST', '/api/project', {'title': 'Scoped'})[1]
    path = f'/api/project/{project["id"]}'
    file = c('POST', path + '/attachments', {'name': 'a.txt', 'content': 'YQ=='})[1]
    secured.business.policy = Policy(registry, rules={('editor', Project): Rule(('read', 'create', 'update', 'delete'), scope=lambda user: {'title': 'Different'})})
    assert c('POST', path + '/actions/start')[0] == 404
    assert c('GET', path + '/attachments')[0] == 404
    assert c('GET', path + f'/attachments/{file["id"]}')[0] == 404
    assert c('DELETE', path + f'/attachments/{file["id"]}')[0] == 404


def test_child_and_attachment_limits_are_enforced(secured):
    c = client(secured, 'editor')
    assert c('POST', '/api/project', {'title': 'Too many', 'children': {'tasks': [{'title': str(i)} for i in range(101)]}})[0] == 422
    assert secured.database.count(Project) == 0
    project = c('POST', '/api/project', {'title': 'Files'})[1]
    path = f'/api/project/{project["id"]}/attachments'
    for i in range(10):
        assert c('POST', path, {'name': f'{i}.txt', 'content': 'YQ=='})[0] == 201
    assert c('POST', path, {'name': 'eleventh.txt', 'content': 'YQ=='})[0] == 422


def test_schema_fixtures_match_python_metadata():
    from pathlib import Path
    from evlet import User
    assert json.loads(Path('tests/fixtures/test-schema.json').read_text()) == registry.ui_schema()
    assert json.loads(Path('tests/fixtures/test-business-schema.json').read_text()) == create_app().business.schema(User(1, 'editor', 'editor'))


def test_malformed_csrf_token_is_rejected_without_server_error(secured):
    c = client(secured)
    assert c('POST', '/api/customer', {'customer_name': 'No'}, headers={b'x-csrf-token': b'\xff'})[0] == 403


def test_account_listing_pages_without_exposing_passwords(secured, hashed):
    with secured.database.transaction() as tx:
        tx.connection.executemany("INSERT INTO evlet_users(username,password,role) VALUES (?,?,'viewer')", [(f'user{i}', hashed) for i in range(102)])
    c = client(secured)
    first = c('GET', '/api/auth/users')[1]
    second = c('GET', '/api/auth/users?offset=100')[1]
    # 102 viewers and the three accounts the fixture sets up (its admin is the built-in Administrator).
    assert len(first['items']) == 100 and len(second['items']) == 5 and second['total'] == 105
    assert first['items'][-1]['id'] < second['items'][0]['id']
    assert all(set(row) == {'id', 'username', 'role', 'active'} for row in second['items'])
    for query in ('offset=-1', 'offset=x', 'offset=1&offset=2', 'other=x'):
        assert c('GET', '/api/auth/users?' + query)[0] == 422
