"""Sign-in rules in config.py (Security) and Controls, and the built-in Administrator."""

import sqlite3

import pytest

from evlet import Application, Database, LocalAuth, Policy, Rule, Security
from evlet.auth import ADMINISTRATOR, PROTECTED, password_hash
from evlet.controls import set_email_from_shell, set_password_from_shell
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

def test_every_new_instance_has_the_administrator_without_a_password(tmp_path):
    secured = plain_app(tmp_path)
    secured.business.auth.create_user(username='owner', password=PASSWORD, role='admin')
    with secured.database._connection() as connection:
        row = connection.execute('SELECT u.id, u.role, u.active, u.password, p.full_name FROM evlet_users u '
                                 'LEFT JOIN evlet_user_profile p ON p.user_id=u.id WHERE u.username=?', (ADMINISTRATOR,)).fetchone()
    assert tuple(row) == (1, 'admin', 1, '', 'System Administrator')
    # No password: no way in, the same answer as a wrong one.
    status, payload, _ = http(secured, 'POST', '/api/auth/login', {'username': ADMINISTRATOR, 'password': ''})
    assert status == 401
    status, payload, _ = http(secured, 'POST', '/api/auth/login', {'username': ADMINISTRATOR, 'password': 'anything at all here'})
    assert status == 401 and payload['error'] == 'Invalid username or password'
    users = client(secured, 'owner')('GET', '/api/_controls/users')[1]['items']
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
        connection.execute("DELETE FROM evlet_framework_migrations WHERE name IN ('0008_administrator', '0009_admin_username', '0010_split_files', '0011_state_manager')")
    database = Database(path, registry)
    assert upgrade(database) == ['0008_administrator', '0009_admin_username', '0010_split_files', '0011_state_manager']
    with database._connection() as connection:
        rows = [tuple(row) for row in connection.execute('SELECT username, role, active, password FROM evlet_users ORDER BY id')]
    assert rows[0][0] == 'owner' and rows[1] == (ADMINISTRATOR, 'admin', 1, '')
    database.close()


def older_database(tmp_path, *statements):
    """A database whose users table holds only what statements add, with steps 0008 and 0009 not yet recorded."""
    app = plain_app(tmp_path)
    path = tmp_path / 'rules.sqlite3'
    app.database.close()
    with sqlite3.connect(path) as connection:
        connection.execute('DELETE FROM evlet_user_profile')
        connection.execute('DELETE FROM evlet_users')
        for statement in statements:
            connection.execute(statement)
        connection.execute("DELETE FROM evlet_framework_migrations WHERE name IN ('0008_administrator', '0009_admin_username', '0010_split_files', '0011_state_manager')")
    return Database(path, registry)


def accounts(database):
    with database._connection() as connection:
        return [tuple(row) for row in connection.execute('SELECT username, role, active, builtin FROM evlet_users ORDER BY id')]


def test_migrating_refuses_to_take_over_an_admin_account_that_is_not_an_active_admin(tmp_path):
    for role, active in (('editor', 1), ('admin', 0)):
        database = older_database(tmp_path / f'{role}{active}', f"INSERT INTO evlet_users(username,password,role,active) VALUES ('admin', 'x', '{role}', {active})")
        with pytest.raises(SchemaError, match='account named admin exists that is not an active administrator'):
            upgrade(database)
        assert [row[:3] for row in accounts(database)] == [('admin', role, active)]
        database.close()


def test_migrating_makes_an_older_active_admin_named_admin_the_administrator(tmp_path):
    # Before 0.4.2 an instance's first admin was often named admin: it becomes the Administrator and keeps its password.
    database = older_database(tmp_path, "INSERT INTO evlet_users(username,password,role) VALUES ('admin', 'kept', 'admin')")
    upgrade(database)
    assert accounts(database) == [('admin', 'admin', 1, 1)]
    with database._connection() as connection:
        assert connection.execute("SELECT password FROM evlet_users WHERE username='admin'").fetchone()[0] == 'kept'
    database.close()


def test_migrating_from_0_4_2_renames_administrator_to_admin(tmp_path):
    app = plain_app(tmp_path)
    give_password(app, ADMINISTRATOR)
    with app.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_users SET username='administrator' WHERE username=?", (ADMINISTRATOR,))
        tx.connection.execute("DELETE FROM evlet_framework_migrations WHERE name IN ('0009_admin_username', '0010_split_files', '0011_state_manager')")
    app.database.close()
    database = Database(tmp_path / 'rules.sqlite3', registry)
    assert upgrade(database) == ['0009_admin_username', '0010_split_files', '0011_state_manager']
    assert accounts(database) == [('admin', 'admin', 1, 1)]
    database.close()
    app = plain_app(tmp_path)
    assert http(app, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})[0] == 200
    assert http(app, 'POST', '/api/auth/login', {'username': 'administrator', 'password': PASSWORD})[0] == 401


def test_migrating_from_0_4_2_beside_an_admin_account_keeps_that_account_as_the_administrator(tmp_path):
    # A 0.4.2 instance that also has an ordinary admin named admin: that account becomes the Administrator, and the
    # 0.4.2 one an ordinary account, disabled when it never had a password.
    for password, active in (('', 0), ('set', 1)):
        database = older_database(tmp_path / str(active),
                                  f"INSERT INTO evlet_users(username,password,role,builtin) VALUES ('administrator', '{password}', 'admin', 1)",
                                  "INSERT INTO evlet_users(username,password,role) VALUES ('admin', 'x', 'admin')")
        upgrade(database)
        assert accounts(database) == [('administrator', 'admin', active, 0), ('admin', 'admin', 1, 1)]
        database.close()


def test_the_administrator_cannot_be_changed_on_any_surface(secured, tmp_path):
    give_password(secured, ADMINISTRATOR)
    secured.business.auth.create_user(username='owner', password=PASSWORD, role='admin')
    admin = client(secured, 'owner')
    target = user_id(secured, ADMINISTRATOR)
    for body in ({'role': 'editor'}, {'active': False}, {'role': 'viewer', 'active': False}):
        for path in (f'/api/_controls/users/{target}', f'/api/auth/users/{target}'):
            status, payload, _ = admin('PATCH', path, body)
            assert status == 409 and payload['error'] == PROTECTED, (path, body)
    # The username stays its own: no one else can take it.
    status, payload, _ = admin('POST', '/api/_controls/users', {'username': 'Admin', 'password': PASSWORD, 'role': 'viewer'})
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
    assert me('POST', '/api/_settings/me/email', {'email': 'ops@example.test', 'password': PASSWORD})[1] == {'email': 'ops@example.test'}
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


def test_the_administrator_sets_its_email_on_any_instance_with_its_password(secured, caplog):
    give_password(secured, ADMINISTRATOR)
    me = client(secured, ADMINISTRATOR)
    routes = secured.business.routes
    # A served instance with a domain: other people still can't change their email here.
    object.__setattr__(routes, 'primary', '127.0.0.1')
    try:
        status, payload, _ = client(secured, 'editor')('POST', '/api/_settings/me/email', {'email': 'x@example.test', 'password': PASSWORD})
        assert status == 409
        email = lambda body: me('POST', '/api/_settings/me/email', body)
        for body, field in (({'email': 'not an address', 'password': PASSWORD}, 'email'), ({'email': 'ops@example.test'}, 'password'),
                            ({'email': 'ops@example.test', 'password': 'wrong password entirely'}, 'password')):
            status, payload, _ = email(body)
            assert status == 422 and field in payload['fields'], (body, payload)
        with caplog.at_level('INFO', logger='evlet.settings'):
            status, payload, _ = email({'email': ' ops@example.test ', 'password': PASSWORD})
            assert status == 200 and payload == {'email': 'ops@example.test'}
        assert 'confirm=' not in caplog.text
    finally:
        object.__setattr__(routes, 'primary', None)
    assert me('GET', '/api/_settings/me')[1]['profile']['email'].lower() == 'ops@example.test'
    with secured.database._connection() as connection:
        row = connection.execute("SELECT actor, detail, before, after FROM evlet_audit WHERE detail LIKE 'Email for notifications%' ORDER BY id DESC").fetchone()
    assert row['actor'] == ADMINISTRATOR and row['before'] == '{"email": null}' and 'ops@example.test' in row['after'].lower()


def test_set_email_from_the_shell(secured, tmp_path):
    path = tmp_path / 'business.sqlite3'
    assert set_email_from_shell(path, ADMINISTRATOR, 'ops@example.test') == 'ops@example.test'
    set_email_from_shell(path, 'editor', 'priya@example.test')
    with pytest.raises(ValueError, match='not a valid email'):
        set_email_from_shell(path, 'editor', 'nope')
    with pytest.raises(ValueError, match='No user'):
        set_email_from_shell(path, 'nobody', 'a@example.test')
    with secured.database._connection() as connection:
        emails = dict(connection.execute('SELECT u.username, p.email FROM evlet_users u JOIN evlet_user_profile p ON p.user_id=u.id').fetchall())
        audited = [tuple(row) for row in connection.execute("SELECT actor, target, detail FROM evlet_audit WHERE detail='Email set from the server shell'")]
    assert emails[ADMINISTRATOR] == 'ops@example.test' and emails['editor'] == 'priya@example.test'
    assert audited == [('cli', ADMINISTRATOR, 'Email set from the server shell'), ('cli', 'editor', 'Email set from the server shell')]
