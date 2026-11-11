"""Sign-in rules in config.py (Security) and Controls, and the built-in Administrator."""

import sqlite3

import pytest

from evlet import Application, Database, LocalAuth, Policy, Rule, Security
from evlet.auth import ADMINISTRATOR, PROTECTED, password_hash
from evlet.controls import set_password_from_shell
from evlet.framework import upgrade
from evlet.migrations import SchemaError
from evlet.settings import FIXED_PROFILE
from examples.playground.models import Customer, registry
from test_business import PASSWORD, client, hashed, http, secured  # noqa: F401


def save(c, *changes):
    return c('POST', '/api/_controls/overrides', {'changes': [{'kind': kind, 'key': key, 'value': value} for kind, key, value in changes]})


def user_id(app, username):
    with app.database._connection() as connection:
        return connection.execute('SELECT id FROM evlet_users WHERE username=?', (username,)).fetchone()[0]


def give_password(app, username, value=PASSWORD):
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_users SET password=? WHERE username=?', (password_hash(value), username))


def plain_app(tmp_path, security=None, rules=None):
    database = Database(tmp_path / 'rules.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True, security=security)
    app = Application(database, auth=auth, policy=Policy(registry, rules=rules or {}))
    app.initialize()
    return app


# ---------- Security(...) ----------

def test_security_takes_one_to_64_characters_and_true_or_false():
    assert Security(password_min=1).password_min == 1 and Security(password_min=64).password_min == 64
    for value in (0, 65, '15', True):
        with pytest.raises(ValueError, match='password_min takes 1 to 64'):
            Security(password_min=value)
    for name in ('admin_two_step', 'two_step', 'admin_sign_in'):
        with pytest.raises(ValueError, match=f'{name} takes True or False'):
            Security(**{name: 1})
    assert (Security().admin_two_step, Security().two_step, Security().admin_sign_in) == (True, False, False)


def test_password_min_1_accepts_1_as_a_password(tmp_path):
    app = plain_app(tmp_path, Security(password_min=1))
    app.business.auth.create_user(username='owner', password='1', role='admin')
    app.business.auth.create_user(username='clerk', password='2', role='editor')
    for username, password in (('owner', '1'), ('clerk', '2')):
        status, payload, _ = http(app, 'POST', '/api/auth/login', {'username': username, 'password': password})
        assert status == 200 and payload['user']['username'] == username
    with pytest.raises(Exception, match='between 1 and 128'):
        app.business.auth.create_user(username='empty', password='', role='editor')


# ---------- Controls and the admin rules ----------

def test_controls_lowers_and_raises_the_password_minimum_for_users_only(secured):
    admin = client(secured)
    assert save(admin, ('setting', 'security.password_min', 4))[0] == 200
    # Everyone else: 4 characters will do.
    editor = client(secured, 'editor')
    status, payload, _ = editor('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': 'abcd'})
    assert status == 200, payload
    assert editor('GET', '/api/_settings/me')[1]['security']['password_min'] == 4
    # Admins keep config.py's minimum, whatever Controls says.
    assert admin('GET', '/api/_settings/me')[1]['security']['password_min'] == 15
    status, payload, _ = admin('POST', '/api/_controls/users', {'username': 'boss', 'password': 'abcd', 'role': 'admin'})
    assert status == 422 and 'between 15 and 128' in payload['fields']['password']
    assert admin('POST', '/api/_controls/users', {'username': 'temp', 'password': 'abcd', 'role': 'viewer'})[0] == 201
    # Making someone an admin with a short password set at the same time is refused too.
    status, _, _ = admin('PATCH', f"/api/_controls/users/{user_id(secured, 'temp')}", {'role': 'admin', 'password': 'efgh'})
    assert status == 422
    # Raising it works as before.
    assert save(admin, ('setting', 'security.password_min', 40))[0] == 200
    status, payload, _ = editor('POST', '/api/_settings/me/password', {'current': 'abcd', 'new': 'x' * 39})
    assert status == 422 and 'at least 40' in payload['fields']['new']


def test_the_shell_uses_config_for_admins_and_controls_for_everyone_else(secured, tmp_path):
    admin = client(secured)
    assert save(admin, ('setting', 'security.password_min', 3))[0] == 200
    path = tmp_path / 'business.sqlite3'
    set_password_from_shell(path, 'editor', 'abc', 15)
    with pytest.raises(ValueError, match='between 15 and 128'):
        set_password_from_shell(path, 'admin', 'abc', 15)
    set_password_from_shell(path, ADMINISTRATOR, 'a long administrator password', 15)
    assert http(secured, 'POST', '/api/auth/login', {'username': 'editor', 'password': 'abc'})[0] == 200


def test_two_step_for_admins_comes_from_config_and_for_users_from_controls(secured):
    controls = secured.business.controls
    admin = client(secured)
    assert controls.two_step_required('admin') and not controls.two_step_required('editor')
    # Turning two-step on for everyone covers every other role, never admin's rule.
    assert save(admin, ('setting', 'security.two_step', True))[0] == 200
    assert controls.two_step_required('editor') and controls.two_step_required('viewer')
    assert save(admin, ('setting', 'security.two_step', None))[0] == 200
    # A role list that names admin (as saved before 0.4.2) keeps only the other roles.
    status, _, _ = save(admin, ('setting', 'security.two_step_roles', ['admin', 'editor']))
    assert status == 200 and controls.settings['security.two_step_roles'] == ['editor']
    assert controls.two_step_required('editor') and not controls.two_step_required('viewer')
    state = admin('GET', '/api/_controls/state')[1]
    assert state['admin_rules'] == {'password_min': 15, 'two_step': True, 'sign_in': False}
    assert state['settings']['security.two_step'] == {'file': False, 'value': False}


def test_admin_two_step_false_turns_it_off_for_admins_only(tmp_path):
    app = plain_app(tmp_path, Security(admin_two_step=False, two_step=True))
    controls = app.business.controls
    assert not controls.two_step_required('admin') and controls.two_step_required('editor')
    assert controls.settings['security.two_step'] is True
    # Controls can turn the users' rule off.
    app.business.auth.create_user(username='owner', password=PASSWORD, role='admin')
    owner = client(app, 'owner')
    assert save(owner, ('setting', 'security.two_step', False))[0] == 200
    assert not controls.two_step_required('editor')


def test_admin_sign_in_fills_in_the_username_on_the_sign_in_page(tmp_path):
    plain = plain_app(tmp_path)
    assert 'sign_in' not in http(plain, 'GET', '/api/auth/session')[1]
    app = plain_app(tmp_path / 'other', Security(admin_sign_in=True))
    session = http(app, 'GET', '/api/auth/session')[1]
    assert session['user'] is None and session['sign_in'] == {'username': ADMINISTRATOR}


# ---------- the built-in Administrator ----------

def test_every_new_instance_has_the_administrator_without_a_password(secured):
    with secured.database._connection() as connection:
        row = connection.execute('SELECT u.id, u.role, u.active, u.password, p.full_name FROM evlet_users u '
                                 'LEFT JOIN evlet_user_profile p ON p.user_id=u.id WHERE u.username=?', (ADMINISTRATOR,)).fetchone()
    assert tuple(row) == (1, 'admin', 1, '', 'System Administrator')
    # No password: no way in, the same answer as a wrong one.
    status, payload, _ = http(secured, 'POST', '/api/auth/login', {'username': ADMINISTRATOR, 'password': ''})
    assert status == 401
    status, payload, _ = http(secured, 'POST', '/api/auth/login', {'username': ADMINISTRATOR, 'password': 'anything at all here'})
    assert status == 401 and payload['error'] == 'Invalid username or password'
    users = client(secured)('GET', '/api/_controls/users')[1]['items']
    builtin = [item for item in users if item['builtin']]
    assert [(item['username'], item['password_set']) for item in builtin] == [(ADMINISTRATOR, False)]


def test_setup_in_the_browser_can_set_the_administrators_password(tmp_path):
    app = plain_app(tmp_path)
    assert http(app, 'GET', '/api/auth/session')[1]['setup'] is True
    assert http(app, 'POST', '/api/auth/setup', {'username': ADMINISTRATOR, 'password': PASSWORD})[0] == 201
    assert http(app, 'GET', '/api/auth/session')[1]['setup'] is False
    assert http(app, 'POST', '/api/auth/login', {'username': ADMINISTRATOR, 'password': PASSWORD})[0] == 200
    assert http(app, 'POST', '/api/auth/setup', {'username': 'second', 'password': PASSWORD})[0] == 403


def test_migrating_an_older_database_adds_the_administrator(tmp_path):
    app = plain_app(tmp_path)
    path = tmp_path / 'rules.sqlite3'
    app.database.close()
    # A database from 0.4.1: an owner, no Administrator, the step not recorded.
    with sqlite3.connect(path) as connection:
        connection.execute('DELETE FROM evlet_user_profile')
        connection.execute('DELETE FROM evlet_users')
        connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('owner', ?, 'admin')", (password_hash(PASSWORD),))
        connection.execute("DELETE FROM evlet_framework_migrations WHERE name='0008_administrator'")
    database = Database(path, registry)
    assert upgrade(database) == ['0008_administrator']
    with database._connection() as connection:
        rows = [tuple(row) for row in connection.execute('SELECT username, role, active, password FROM evlet_users ORDER BY id')]
    assert rows[0][0] == 'owner' and rows[1] == (ADMINISTRATOR, 'admin', 1, '')
    database.close()


def test_migrating_refuses_to_take_over_someone_elses_administrator_account(tmp_path):
    app = plain_app(tmp_path)
    path = tmp_path / 'rules.sqlite3'
    app.database.close()
    with sqlite3.connect(path) as connection:
        connection.execute('DELETE FROM evlet_user_profile')
        connection.execute('DELETE FROM evlet_users')
        connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('administrator', 'x', 'editor')")
        connection.execute("DELETE FROM evlet_framework_migrations WHERE name='0008_administrator'")
    database = Database(path, registry)
    with pytest.raises(SchemaError, match='account named administrator already exists'):
        upgrade(database)
    with database._connection() as connection:
        assert [tuple(row) for row in connection.execute('SELECT username, role FROM evlet_users')] == [('administrator', 'editor')]
    database.close()


def test_the_administrator_cannot_be_changed_on_any_surface(secured, tmp_path):
    give_password(secured, ADMINISTRATOR)
    admin = client(secured)
    target = user_id(secured, ADMINISTRATOR)
    for body in ({'role': 'editor'}, {'active': False}, {'role': 'viewer', 'active': False}):
        for path in (f'/api/_controls/users/{target}', f'/api/auth/users/{target}'):
            status, payload, _ = admin('PATCH', path, body)
            assert status == 409 and payload['error'] == PROTECTED, (path, body)
    # The username stays its own: no one else can take it.
    status, payload, _ = admin('POST', '/api/_controls/users', {'username': 'Administrator', 'password': PASSWORD, 'role': 'viewer'})
    assert status == 422 and 'already uses' in payload['fields']['username']
    # Its own profile: name, photo and the rest stay as they are; preferences and the email address can change.
    me = client(secured, ADMINISTRATOR)
    assert me('GET', '/api/_settings/me')[1]['builtin'] is True
    for path, body in (('/api/_settings/me/profile', {'full_name': 'Someone'}), ('/api/_settings/me/profile', {'phone': '123'}),
                       ('/api/_settings/me/photo', {'type': 'image/png', 'data': 'iVBORw0KGgo='})):
        status, payload, _ = me('PATCH' if path.endswith('profile') else 'POST', path, body)
        assert status == 409 and payload['error'] == FIXED_PROFILE
    assert me('PATCH', '/api/_settings/me/prefs', {'theme': 'dark'})[0] == 200
    profile = me('GET', '/api/_settings/me')[1]['profile']
    assert profile['full_name'] == 'System Administrator'
    sent = me('POST', '/api/_settings/me/email', {'email': 'ops@example.test', 'password': PASSWORD})[1]
    assert me('POST', '/api/_settings/me/email/confirm', {'token': sent['link'].split('confirm=')[1]})[0] in (200, 204)
    assert me('GET', '/api/_settings/me')[1]['profile']['email'] == 'ops@example.test'
    # Its password, sessions and two-step can be managed like anyone's.
    assert admin('PATCH', f'/api/_controls/users/{target}', {'sessions': 'end'})[0] == 204
    assert admin('PATCH', f'/api/_controls/users/{target}', {'password': 'another long password here'})[0] == 204
    # Direct changes are put back at the next start.
    with secured.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_users SET role='viewer', active=0 WHERE username=?", (ADMINISTRATOR,))
    secured.business.auth.initialize()
    with secured.database._connection() as connection:
        assert tuple(connection.execute('SELECT role, active FROM evlet_users WHERE username=?', (ADMINISTRATOR,)).fetchone()) == ('admin', 1)


def test_the_administrator_passes_every_role_permission_check(tmp_path):
    rules = {('admin', Customer): Rule(('read',), read_fields=('customer_name',), scope=lambda user: {'customer_name': 'nobody'}),
             ('editor', Customer): Rule(('read',))}
    app = plain_app(tmp_path, rules=rules)
    auth = app.business.auth
    auth.create_user(username='owner', password=PASSWORD, role='admin')
    give_password(app, ADMINISTRATOR)
    owner, builtin = client(app, 'owner'), client(app, ADMINISTRATOR)
    assert owner('POST', '/api/customer', {'customer_name': 'Acme'})[0] == 403
    status, created, _ = builtin('POST', '/api/customer', {'customer_name': 'Acme', 'email': 'a@example.test'})
    assert status == 201, created
    # Every row and field: the owner's row scope and field list do not apply.
    assert owner('GET', '/api/customer')[1]['total'] == 0
    listing = builtin('GET', '/api/customer')[1]
    assert listing['total'] == 1 and listing['items'][0]['email'] == 'a@example.test'
    assert builtin('PATCH', f"/api/customer/{created['id']}", {'email': 'b@example.test'})[0] == 200
    # Data rules still apply.
    status, _, _ = builtin('PATCH', f"/api/customer/{created['id']}", {'customer_name': ''})
    assert status == 422
    assert builtin('DELETE', f"/api/customer/{created['id']}")[0] == 204
    # It opens Controls like any admin.
    assert builtin('GET', '/api/_controls/state')[0] == 200
