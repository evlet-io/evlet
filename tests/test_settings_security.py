"""Settings review findings: the secret key, key loss, verified sessions, resets, re-authentication and limits."""

import base64
import logging
import os
import time

import pytest

from evlet import LocalAuth, LocalFileStorage
from evlet import secrets as vault
from evlet.auth import TWO_STEP_FIRST
from evlet.controls import TWO_STEP, reset_two_step_from_shell
from evlet.routing import ControlsAccess
from evlet.settings import KEY_PROBLEM, NO_EMAIL, TOO_MANY_CODES, VERIFY, totp
from examples.playground.app import build_app
from examples.playground.migrate import configured_database
from test_business import PASSWORD, client, hashed, http, secured  # noqa: F401

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 64


@pytest.fixture
def settled(secured, tmp_path):
    secured.business.secret_key = tmp_path / 'secret.key'
    return secured


def b32(text):
    return base64.b32decode(text + '=' * (-len(text) % 8))


def enrol(app, role):
    c = client(app, role)
    started = c('POST', '/api/_settings/me/two-step', {'password': PASSWORD})[1]
    raw = b32(started['secret'])
    codes = c('POST', '/api/_settings/me/two-step/confirm', {'code': totp(raw, int(time.time() // 30))})[1]['codes']
    return c, raw, codes


def sign_in(app, username, code, **kwargs):
    data = http(app, 'POST', '/api/auth/login', {'username': username, 'password': PASSWORD}, **kwargs)[1]
    return http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': code}, **kwargs)


def test_the_secret_key_uses_one_file_format_and_strict_checks(tmp_path, monkeypatch):
    path = tmp_path / 'secret.key'
    sealed = vault.seal(path, b'value', 'two_step:1', create=True)
    assert vault.open(path, sealed, 'two_step:1') == b'value'
    with pytest.raises(vault.SecretKeyError):
        vault.open(path, sealed, 'two_step:2')
    # URL-safe base64, one key per line, mode 0600: the same file the connectors read.
    assert len(base64.urlsafe_b64decode(path.read_text().strip() + '=')) == 32 and path.stat().st_mode & 0o777 == 0o600
    # Rotation: a new first key seals, the old one still opens.
    key = vault.SecretKey(path)
    key.write([os.urandom(32), *key.keys()])
    assert vault.open(path, sealed, 'two_step:1') == b'value' and vault.seal(path, b'x', 'p')[1:5] != sealed[1:5]
    os.chmod(path, 0o644)
    assert not vault.available(path)
    with pytest.raises(vault.SecretKeyError, match='chmod 600'):
        vault.open(path, sealed, 'two_step:1')
    os.chmod(path, 0o600)
    link = tmp_path / 'link.key'
    link.symlink_to(path)
    assert not vault.available(link)
    # Without a file, nothing is created unless asked; available(existing=True) needs the key there.
    missing = tmp_path / 'none.key'
    assert vault.available(missing) and not vault.available(missing, existing=True)
    with pytest.raises(vault.SecretKeyError, match='missing'):
        vault.seal(missing, b'x', 'p')
    assert not missing.exists()
    monkeypatch.setenv(vault.KEY_ENV, base64.urlsafe_b64encode(os.urandom(32)).decode())
    assert vault.available(None) and vault.open(None, vault.seal(None, b'env', 'p'), 'p') == b'env'


def test_a_lost_key_is_never_replaced_and_sign_in_answers_503(settled, tmp_path):
    app = settled
    _, raw, _ = enrol(app, 'editor')
    (tmp_path / 'secret.key').unlink()
    status, body, _ = http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})
    assert (status, body) == (503, {'error': KEY_PROBLEM})
    assert not (tmp_path / 'secret.key').exists()
    viewer = client(app, 'viewer')
    assert viewer('GET', '/api/_settings/me')[1]['security']['two_step']['available'] is False
    assert viewer('POST', '/api/_settings/me/two-step', {'password': PASSWORD})[:2] == (503, {'error': KEY_PROBLEM})
    assert not (tmp_path / 'secret.key').exists()
    # A required role can't slip past with a password while the key is gone.
    assert http(app, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})[:2] == (503, {'error': KEY_PROBLEM})


def test_controls_needs_a_session_that_finished_two_step(settled):
    app = settled
    app.routes = app.business.routes = app.business.controls.routes = type(app.routes)(**{**app.routes.__dict__, 'controls': ControlsAccess(require_two_step=True)})
    admin = client(app)
    assert admin('GET', '/api/_controls/state')[0] == 200
    editor, raw, _ = enrol(app, 'editor')
    # Framework roles can't open Controls from the page; set it directly for this check.
    with app.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_roles SET opens_controls=1 WHERE id='editor'")
        tx.connection.execute("UPDATE evlet_config SET version=version+1")
    # Enrolling from a password-only session does not verify that session.
    assert editor('GET', '/api/_controls/state')[:2] == (403, {'error': TWO_STEP})
    status, data, headers = sign_in(app, 'editor', totp(raw, int(time.time() // 30) + 1))
    assert status == 200
    cookie = headers[b'set-cookie'].decode().split(';')[0]
    assert http(app, 'GET', '/api/_controls/state', cookie=cookie)[0] == 200
    assert http(app, 'GET', '/api/_settings/me', cookie=cookie)[1]['security']['two_step']['verified'] is True


def test_required_roles_need_a_verified_session_for_controls(settled):
    app = settled
    admin = client(app)
    assert admin('GET', '/api/_controls/state')[0] == 200
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_sessions SET two_step=NULL')
    # A required role's session without two-step uses nothing at all, not only Controls.
    assert admin('GET', '/api/_controls/state')[:2] == (401, {'error': TWO_STEP_FIRST})


def test_enrolment_is_audited_shown_and_can_be_reset_by_admins_and_the_shell(settled, tmp_path):
    app = settled
    editor, raw, _ = enrol(app, 'editor')
    admin = client(app)
    users = {item['username']: item for item in admin('GET', '/api/_controls/users')[1]['items']}
    assert users['editor']['two_step']['agent'] == 'an unknown device' and users['viewer']['two_step'] is None
    with app.database._connection() as db:
        details = [row[0] for row in db.execute("SELECT detail FROM evlet_audit WHERE area='Users' ORDER BY id")]
    assert 'Two-step sign-in set up from an unknown device' in details
    assert admin('PATCH', f"/api/_controls/users/{users['editor']['id']}", {'two_step': 'reset', 'role': 'viewer'})[0] == 422
    assert admin('PATCH', f"/api/_controls/users/{users['editor']['id']}", {'two_step': 'reset'})[0] == 204
    assert editor('GET', '/api/_settings/me')[0] == 401
    assert http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[1]['user']['username'] == 'editor'
    with app.database._connection() as db:
        assert db.execute("SELECT actor, detail FROM evlet_audit WHERE area='Users' ORDER BY id DESC LIMIT 1").fetchone()[0] == 'admin'
    # From the server shell, audited as cli: an admin enrols again at the next sign-in.
    database = app.database.path
    reset_two_step_from_shell(database, 'admin')
    assert http(app, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})[1]['two_step'] == 'setup'
    with app.database._connection() as db:
        assert tuple(db.execute("SELECT actor, target FROM evlet_audit ORDER BY id DESC LIMIT 1").fetchone()) == ('cli', 'admin')
    with pytest.raises(ValueError, match='No user'):
        reset_two_step_from_shell(database, 'nobody')


def test_sensitive_changes_ask_only_a_code_with_two_step_on_and_the_password_without(settled):
    app = settled
    editor, raw, codes = enrol(app, 'editor')
    enter_code = {'code': 'Enter a current code from your app, or a backup code.'}
    for path in ('/api/_settings/me/two-step/codes', '/api/_settings/me/two-step/off'):
        assert editor('POST', path, {})[1]['fields'] == enter_code
        # The password is not asked, and a right password does not stand in for the code.
        assert editor('POST', path, {'password': PASSWORD})[1]['fields'] == enter_code
        assert editor('POST', path, {'code': '000000'})[1]['fields'] == enter_code
    fresh = editor('POST', '/api/_settings/me/two-step/codes', {'code': codes[0]})[1]['codes']
    assert len(fresh) == 8
    # The old codes no longer work.
    assert editor('POST', '/api/_settings/me/two-step/off', {'code': codes[1]})[0] == 422
    assert editor('POST', '/api/_settings/me/email', {'email': 'e@example.com', 'password': PASSWORD})[1]['fields'] == enter_code
    assert editor('POST', '/api/_settings/me/email', {'email': 'e@example.com', 'code': fresh[0]})[0] == 200
    assert editor('POST', '/api/_settings/me/two-step/off', {'code': totp(raw, int(time.time() // 30) + 1)})[1] == {'on': False}
    # With two-step sign-in off, the password is asked instead.
    assert editor('POST', '/api/_settings/me/two-step', {})[1]['fields'] == {'password': 'Enter your password.'}
    assert editor('POST', '/api/_settings/me/two-step', {'password': 'not the password at all'})[1]['fields'] == {'password': 'Your password is not right.'}
    assert editor('POST', '/api/_settings/me/email', {'email': 'e@example.com', 'code': fresh[1]})[1]['fields'] == {'password': 'Enter your password.'}
    assert editor('POST', '/api/_settings/me/email', {'email': 'e@example.com', 'password': PASSWORD})[0] == 200
    assert editor('POST', '/api/_settings/me/two-step', {'password': PASSWORD})[0] == 200


def test_wrong_codes_are_limited_per_account_across_networks(settled):
    app = settled
    _, raw, _ = enrol(app, 'editor')
    for attempt in range(10):
        address = f'10.0.{attempt}.1'
        assert sign_in(app, 'editor', '000000' if totp(raw, int(time.time() // 30)) != '000000' else '111111', client=address)[0] == 401
    status, body, _ = sign_in(app, 'editor', totp(raw, int(time.time() // 30)), client='10.9.9.9')
    assert (status, body) == (429, {'error': TOO_MANY_CODES})


def test_email_change_is_off_on_production_instances(secured, caplog):
    routes = secured.business.routes
    object.__setattr__(routes, 'primary', 'erp.example.com')
    try:
        c = client(secured, 'editor')
        with caplog.at_level('INFO', logger='evlet.settings'):
            assert c('POST', '/api/_settings/me/email', {'email': 'x@example.com', 'password': PASSWORD}, headers={b'host': b'127.0.0.1:8000'})[:2] in ((409, {'error': NO_EMAIL}), (403, {'error': 'Unrecognized host'}))
        assert 'confirm=' not in caplog.text
        with secured.database._connection() as db:
            assert db.execute('SELECT count(*) FROM evlet_email_tokens').fetchone()[0] == 0
    finally:
        object.__setattr__(routes, 'primary', None)


def test_the_start_page_must_be_a_model_the_person_can_read(secured):
    admin = client(secured)
    assert admin('POST', '/api/_controls/roles', {'id': 'clerk', 'base': 'viewer'})[0] == 201
    change = {'kind': 'perm', 'key': 'clerk|Project', 'value': {'ops': [], 'read': None, 'write': None, 'scope': None}}
    assert admin('POST', '/api/_controls/overrides', {'changes': [change]})[0] == 200
    with secured.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) SELECT 'clerk', password, 'clerk' FROM evlet_users WHERE username='viewer'")
    clerk = client(secured, 'clerk')
    assert clerk('PATCH', '/api/_settings/me/prefs', {'start': 'project'})[1]['fields'] == {'start': 'Choose a page you can open.'}
    assert clerk('PATCH', '/api/_settings/me/prefs', {'start': 'customer'})[0] == 200


def test_a_replaced_photo_removes_its_old_file(tmp_path, monkeypatch, hashed):
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'files.sqlite3'))
    database = configured_database()
    app = build_app(database, LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False), storage=LocalFileStorage(tmp_path / 'private'))
    app.initialize()
    with database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', ('editor', hashed, 'editor'))
    c = client(app, 'editor')
    files = lambda: sorted(path.read_bytes() for path in (tmp_path / 'private').iterdir() if not path.name.startswith('.'))
    for content in (PNG + b'1', PNG + b'2'):
        assert c('POST', '/api/_settings/me/photo', {'type': 'image/png', 'data': base64.b64encode(content).decode()})[0] == 200
    assert files() == [PNG + b'2']
    assert c('DELETE', '/api/_settings/me/photo')[0] == 200 and files() == []
    database.close()


def test_start_warns_when_required_roles_cannot_use_two_step(secured, tmp_path, monkeypatch, caplog):
    secured.business.secret_key = tmp_path / 'secret.key'
    monkeypatch.setattr(vault, 'cryptography_installed', lambda: False)
    with caplog.at_level(logging.WARNING, logger='evlet.settings'):
        secured.business.settings.initialize()
    assert 'need two-step sign-in, but the cryptography package is not installed' in caplog.text
    assert http(secured, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})[1]['user']['username'] == 'admin'


def test_controls_lists_required_roles_that_cannot_use_two_step(secured, tmp_path, monkeypatch):
    from test_controls import save
    admin = client(secured)
    unavailable = lambda: [item for item in admin('GET', '/api/_controls/state')[1]['attention'] if item['page'] == 'security']
    # An application without a secret key has no two-step sign-in by design, so nothing is listed.
    assert unavailable() == []
    secured.business.secret_key = tmp_path / 'secret.key'
    monkeypatch.setattr(vault, 'cryptography_installed', lambda: False)
    assert save(admin, ('setting', 'security.two_step_roles', ['admin', 'editor']))[0] == 200
    [item] = unavailable()
    assert item['title'] == 'Two-step sign-in is unavailable for admin, editor' and 'cryptography package is not installed' in item['text']
    # With the package, admin needs a two-step session to open Controls, so the list is read directly.
    monkeypatch.setattr(vault, 'cryptography_installed', lambda: True)
    listed = lambda: [item for item in secured.business.controls.system.attention([], 0) if item['page'] == 'security']
    (tmp_path / 'secret.key').write_bytes(base64.urlsafe_b64encode(os.urandom(32)) + b'\n')
    os.chmod(tmp_path / 'secret.key', 0o644)
    [item] = listed()
    assert 'can be read by other users' in item['text']
    os.chmod(tmp_path / 'secret.key', 0o600)
    assert listed() == []
    # Admin's session predates the requirement taking effect, so it signs in again and sets two-step up.
    assert admin('GET', '/api/_controls/state')[:2] == (401, {'error': TWO_STEP_FIRST})


def test_one_key_and_one_rotation_cover_connectors_two_step_and_backup_codes(settled, tmp_path, monkeypatch):
    from evlet.auth import User
    from evlet.secrets import SecretKey, rotate
    monkeypatch.delenv(vault.KEY_ENV, raising=False)
    app = settled
    _, raw, codes = enrol(app, 'editor')
    body = {'settings': {'address': 'help@example.test', 'host': 'imap.example.test', 'port': 993, 'security': 'ssl'}, 'secrets': {'password': 'mail secret'}}
    app.business.connectors.save(User(1, 'admin', 'admin'), 'imap:support', body)
    # Connectors seal with the same key file as two-step sign-in.
    assert app.business.connectors.key.path == tmp_path / 'secret.key' and len((tmp_path / 'secret.key').read_text().split()) == 1
    before = (tmp_path / 'secret.key').read_text()
    assert rotate(app.database.path, SecretKey(tmp_path / 'secret.key')) == (3, [])
    assert (tmp_path / 'secret.key').read_text() != before and len((tmp_path / 'secret.key').read_text().split()) == 1
    assert app.business.connectors.values('imap:support')['password'] == 'mail secret'
    assert sign_in(app, 'editor', codes[0])[0] == 200
    # The enrolment used the current code; the next one opens the rotated secret.
    assert sign_in(app, 'editor', totp(raw, int(time.time() // 30) + 1))[0] == 200


def user_id(app, username):
    with app.database._connection() as db:
        return db.execute('SELECT id FROM evlet_users WHERE username=?', (username,)).fetchone()[0]


def test_a_role_that_requires_two_step_uses_nothing_until_it_is_set_up(settled):
    from test_controls import save
    app = settled
    editor = client(app, 'editor')
    _, key = app.business.auth.create_key(user_id(app, 'editor'), 'Script', {'access': 'write', 'models': None})
    bearer = {b'authorization': f'Bearer {key}'.encode(), b'x-evlet-request': b''}
    assert editor('GET', '/api/customer')[0] == 200
    assert save(client(app), ('setting', 'security.two_step_roles', ['admin', 'editor']))[0] == 200
    # Every surface of a browser session: records, writes, Settings, Controls-adjacent routes and the pages.
    for method, path, body in (('GET', '/api/customer', None), ('POST', '/api/customer', {'name': 'x'}), ('GET', '/api/schema', None),
                               ('GET', '/api/_settings/me', None), ('POST', '/api/_settings/me/two-step', {'password': PASSWORD}),
                               ('GET', '/api/_settings/me/keys', None)):
        assert editor(method, path, body)[:2] == (401, {'error': TWO_STEP_FIRST}), (method, path)
    assert editor('GET', '/api/auth/session')[1]['user'] is None
    assert editor('GET', '/settings/profile')[0] == 302
    # API keys are not sessions: they keep working.
    assert http(app, 'GET', '/api/customer', headers=bearer)[0] == 200
    # Signing in again sets it up first.
    assert http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[1]['two_step'] == 'setup'
    assert client(app, 'editor')('GET', '/api/customer')[0] == 200


def test_controls_changes_ask_for_a_code_at_most_every_ten_minutes(settled):
    app = settled
    admin = client(app)
    codes = app._test_codes['admin']
    change = lambda value: admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'security.password_min', 'value': value}]})
    # Signing in with a code counts as a fresh check.
    assert change(20)[0] == 200
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_sessions SET two_step=two_step-601')
    # Reading never asks; every change does, including an admin setting someone else's password.
    assert admin('GET', '/api/_controls/state')[0] == 200 and admin('GET', '/api/_controls/users')[0] == 200
    assert change(21)[:2] == (403, {'error': VERIFY})
    editor = user_id(app, 'editor')
    assert admin('PATCH', f'/api/_controls/users/{editor}', {'password': PASSWORD + ' reset'})[:2] == (403, {'error': VERIFY})
    assert admin('PATCH', f'/api/_controls/users/{editor}', {'sessions': 'end'})[:2] == (403, {'error': VERIFY})
    assert admin('POST', '/api/_controls/roles', {'id': 'ops', 'base': 'editor'})[:2] == (403, {'error': VERIFY})
    # The older account routes are Controls changes too.
    assert admin('GET', '/api/auth/users')[0] == 200
    for method, path, body in (('PATCH', f'/api/auth/users/{editor}', {'role': 'admin'}), ('PATCH', f'/api/auth/users/{editor}', {'password': PASSWORD + ' reset'}),
                               ('PATCH', f'/api/auth/users/{editor}', {'two_step': 'reset'}), ('POST', '/api/auth/users', {'username': 'newbie', 'password': PASSWORD, 'role': 'viewer'})):
        assert admin(method, path, body)[:2] == (403, {'error': VERIFY}), (method, path, body)
    with app.database._connection() as db:
        assert db.execute("SELECT role FROM evlet_users WHERE username='editor'").fetchone()[0] == 'editor'
        assert db.execute("SELECT count(*) FROM evlet_users WHERE username='newbie'").fetchone()[0] == 0
    assert admin('POST', '/api/_settings/me/two-step/verify', {'code': '00000000'})[1]['fields'] == {'code': 'Enter a current code from your app, or a backup code.'}
    status, body, _ = admin('POST', '/api/_settings/me/two-step/verify', {'code': codes.pop()})
    assert status == 200 and body['until'] - body['verified'] == 600
    assert change(21)[0] == 200
    assert admin('PATCH', f'/api/_controls/users/{editor}', {'password': PASSWORD + ' reset'})[0] == 204
    assert admin('POST', '/api/auth/users', {'username': 'newbie', 'password': PASSWORD, 'role': 'viewer'})[0] == 201
    with app.database._connection() as db:
        assert db.execute("SELECT password FROM evlet_users WHERE username='editor'").fetchone()[0] != db.execute(
            "SELECT password FROM evlet_users WHERE username='viewer'").fetchone()[0]


def test_controls_without_two_step_on_asks_no_code(secured):
    # No secret key: two-step sign-in is unavailable, and Controls changes need no code.
    admin = client(secured)
    with secured.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_sessions SET two_step=NULL')
    assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'security.password_min', 'value': 20}]})[0] == 200


def test_changing_your_own_password_asks_a_code_when_two_step_is_on(settled):
    app = settled
    editor, raw, codes = enrol(app, 'editor')
    new = PASSWORD + ' changed'
    assert editor('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': new})[1]['fields'] == {'code': 'Enter a current code from your app, or a backup code.'}
    assert editor('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': new, 'code': '000000'})[0] == 422
    # The current password is still needed with a right code.
    assert editor('POST', '/api/_settings/me/password', {'current': 'not the password at all', 'new': new, 'code': codes[0]})[1]['fields'] == {
        'current': 'Your current password is not right.'}
    assert editor('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': new, 'code': codes[0]})[:2] == (200, {'changed': True, 'revoked_keys': 0})
    # Without two-step sign-in the current password is enough.
    viewer = client(app, 'viewer')
    assert viewer('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': new})[0] == 200


def test_a_code_cannot_be_used_twice_by_requests_that_race(settled):
    app = settled
    _, raw, _ = enrol(app, 'editor')
    editor_id, settings = user_id(app, 'editor'), app.business.settings
    with app.database._connection() as db:
        stale = dict(db.execute('SELECT * FROM evlet_user_profile WHERE user_id=?', (editor_id,)).fetchone())
    code = totp(raw, int(time.time() // 30) + 1)
    assert sign_in(app, 'editor', code)[0] == 200
    # A second request that read the profile before the first one wrote it still loses.
    with app.database.transaction() as tx:
        assert settings._use_code(tx.connection, editor_id, stale, code) is False
