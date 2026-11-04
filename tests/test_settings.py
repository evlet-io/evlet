"""Settings: personal pages, app settings and preferences, two-step sign-in, MultiSelect and suffix."""

import base64
import json
import re
import time

import pytest

from evlet import AppDefinition, AppSettings, Database, Decimal, Integer, LocalAuth, Model, MultiSelect, Registry, Text, UserPreferences
from evlet.auth import TWO_STEP_FIRST, User
from evlet.routing import ControlsAccess
from evlet.settings import totp
from examples.playground.app import build_app, playground_routes
from examples.playground.migrate import configured_database
from test_business import PASSWORD, client, hashed, http, secured  # noqa: F401

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 64


@pytest.fixture
def settled(secured, tmp_path):
    """The Playground with a secret key, so two-step sign-in is available (and required for admin)."""
    secured.business.secret_key = tmp_path / 'secret.key'
    return secured


def test_personal_pages_save_and_the_last_page_is_remembered(secured):
    c = client(secured, 'editor')
    me = c('GET', '/api/_settings/me')[1]
    assert me['pages'] == ['profile', 'appearance', 'security', 'notifications', 'email', 'ai', 'keys'] and not me['portal'] and not me['controls']
    assert me['prefs'] == {'theme': 'system', 'density': 'comfortable', 'text': 'default', 'start': '', 'counts': False, 'lang': 'en',
                           'tz': 'Asia/Dubai', 'date': 'dd-MM-yyyy', 'num': '1,234.56', 'week': 'mon', 'keep_unsaved': True}
    assert c('PATCH', '/api/_settings/me/profile', {'full_name': 'Sara Khan', 'short_name': 'Sara', 'title': 'Sales', 'phone': '+971 50 123 4567', 'signature': 'Sara\nAcme'})[0] == 200
    status, payload, _ = c('PATCH', '/api/_settings/me/profile', {'short_name': '  ', 'phone': 'call me'})
    assert status == 422 and payload['fields'] == {'short_name': "This can't be empty.", 'phone': 'Use digits, spaces, +, - and brackets only.'}
    changes = {'density': 'compact', 'text': 'large', 'start': 'project', 'counts': True, 'lang': 'ar', 'tz': 'Asia/Kolkata', 'date': 'd MMM yyyy',
               'num': '1.234,56', 'week': 'sun', 'notify.assigned': [False, True], 'notify.new_device': [False, False],
               'quiet': {'on': True, 'from': '21:00', 'to': '08:00'}, 'last_page': 'notifications'}
    assert c('PATCH', '/api/_settings/me/prefs', changes)[0] == 200
    status, payload, _ = c('PATCH', '/api/_settings/me/prefs', {'theme': 'sepia', 'tz': 'Mars/Base', 'date': 'yy', 'last_page': 'billing', 'other': 1})
    assert status == 422 and payload['fields'] == {'theme': 'Choose one of the options.', 'tz': 'Choose a time zone.',
                                                   'date': 'Choose one of the options.', 'last_page': 'Choose a settings page.', 'other': 'Choose a known preference.'}
    me = c('GET', '/api/_settings/me')[1]
    assert me['profile'] | {'photo': None} == {'full_name': 'Sara Khan', 'short_name': 'Sara', 'title': 'Sales', 'phone': '+971 50 123 4567',
                                               'email': '', 'signature': 'Sara\nAcme', 'photo': None}
    assert me['prefs']['date'] == 'd MMM yyyy' and me['prefs']['lang'] == 'ar' and me['last_page'] == 'notifications'
    channels = {item['key']: item['channels'] for group in me['notifications']['events'] for item in group['items']}
    # Security emails cannot be turned off.
    assert channels['assigned'] == [False, True] and channels['new_device'] == [False, True]
    assert me['notifications']['quiet'] == {'on': True, 'from': '21:00', 'to': '08:00'}
    status, _, headers = c('GET', '/settings')
    assert status == 302 and headers[b'location'] == b'/settings/notifications'
    assert c('GET', '/settings/appearance')[0] in (200, 404) and c('GET', '/settings/keys')[0] in (200, 404) and c('GET', '/settings/billing')[0] == 404
    status, _, headers = http(secured, 'GET', '/settings/profile')
    assert status == 302 and headers[b'location'] == b'/login?next=/settings/profile'
    # Other people never see someone else's settings.
    assert client(secured, 'viewer')('GET', '/api/_settings/me')[1]['profile']['full_name'] == ''


def test_the_theme_is_saved_per_person_and_painted_before_any_script_runs(secured, tmp_path, monkeypatch):
    # A stand-in for the built index.html, so this runs without the frontend build.
    (tmp_path / 'index.html').write_text('<!doctype html><html lang="en"><head><title>Evlet</title></head><body></body></html>')
    monkeypatch.setattr('evlet.ui.serving.STATIC', tmp_path)
    c = client(secured, 'editor')

    def painted(response):
        status, page, _ = response
        assert status == 200
        found = re.search(rb'<html[^>]*>', page)[0]
        theme = re.search(rb'data-theme="([a-z]+)"', found)
        return (theme[1].decode() if theme else None), json.loads(re.search(rb'<script id="evlet-config" type="application/json">(.*?)</script>', page)[1])['theme']

    assert painted(c('GET', '/docs')) == (None, 'system')
    for theme in ('light', 'dark', 'system'):
        assert c('PATCH', '/api/_settings/me/prefs', {'theme': theme})[0] == 200
        assert c('GET', '/api/_settings/me')[1]['prefs']['theme'] == theme
        # Docs, a model screen and Settings all open in the chosen theme; "system" is left to the stylesheet.
        for path in ('/docs', '/app/customer', '/settings/appearance'):
            assert painted(c('GET', path)) == (None if theme == 'system' else theme, theme)
    assert c('PATCH', '/api/_settings/me/prefs', {'theme': 'dark'})[0] == 200
    # Each person has their own theme.
    assert client(secured, 'viewer')('GET', '/api/_settings/me')[1]['prefs']['theme'] == 'system'
    assert c('PATCH', '/api/_settings/me/prefs', {'theme': 'Dark'})[0] == 422
    # Signed out, the sign-in page follows the theme this browser last showed, from the app's cookie.
    assert painted(http(secured, 'GET', '/login', cookie='evlet_theme=dark')) == ('dark', 'dark')
    assert painted(http(secured, 'GET', '/login', cookie='evlet_theme=light')) == ('light', 'light')
    for cookie in ('', 'evlet_theme=sepia', 'evlet_theme="><script>', 'evlet_theme=system'):
        assert painted(http(secured, 'GET', '/login', cookie=cookie)) == (None, 'system')
    # The signed-in person's choice wins over the cookie.
    assert painted(http(secured, 'GET', '/docs', cookie=c.cookie + '; evlet_theme=light')) == ('dark', 'dark')


def test_app_settings_respect_editors_audit_and_the_config_version(secured):
    app = secured
    editor, viewer = client(app, 'editor'), client(app, 'viewer')
    state = viewer('GET', '/api/_settings/app/projects')[1]
    assert not state['can_edit'] and state['editors'] == ['editor'] and [section['title'] for section in state['sections']] == ['Work week', 'Progress', 'Money']
    hours = state['sections'][0]['fields'][1]
    assert (hours['name'], hours['value'], hours['default'], hours['suffix'], hours['label']) == ('hours', 8, 8, 'hours', 'Hours per day')
    assert viewer('PATCH', '/api/_settings/app/projects', {'values': {'hours': 7}})[0] == 403
    status, payload, _ = editor('PATCH', '/api/_settings/app/projects', {'values': {'hours': 30, 'days': ['mon', 'xmas'], 'vat': '5.001'}})
    assert status == 422 and set(payload['fields']) == {'hours', 'days', 'vat'}
    before = app.business.controls.version
    saved = editor('PATCH', '/api/_settings/app/projects', {'values': {'hours': 7, 'days': ['fri', 'mon'], 'vat': '5'}})[1]
    assert saved['saved'] == 2 and app.business.controls.version == before + 1
    fields = {field['name']: field for section in saved['sections'] for field in section['fields']}
    assert fields['days']['value'] == ['mon', 'fri'] and fields['vat']['value'] == '5.00'
    # Server code reads the values, cached per config version; a reset goes back to the default.
    assert app.business.settings.values.get('projects', 'hours') == 7
    assert app.business.settings.values.get('projects', 'days') == ('mon', 'fri')
    assert editor('PATCH', '/api/_settings/app/projects', {'values': {'hours': None}})[1]['saved'] == 1
    assert app.business.settings.values.get('projects', 'hours') == 8
    with app.database._connection() as db:
        rows = [dict(row) for row in db.execute("SELECT actor, area, target, detail, before, after FROM evlet_audit WHERE area='Settings' ORDER BY id")]
    assert [(row['actor'], row['target'], row['detail']) for row in rows] == [('editor', 'projects.hours', 'Changed'), ('editor', 'projects.days', 'Changed'),
                                                                             ('editor', 'projects.hours', 'Reset to the default')]
    assert json.loads(rows[1]['after']) == ['mon', 'fri']
    # Just for you: saved at once, per person.
    assert viewer('PATCH', '/api/_settings/me/app/projects', {'group': 'due', 'cc_me': True})[0] == 200
    assert viewer('PATCH', '/api/_settings/me/app/projects', {'group': 'week'})[0] == 422
    prefs = {field['name']: field['value'] for field in viewer('GET', '/api/_settings/app/projects')[1]['preferences']}
    assert prefs == {'group': 'due', 'cc_me': True}
    viewer_id = next(user['id'] for user in app.business.auth.users() if user['username'] == 'viewer')
    assert app.business.settings.preferences(User(viewer_id, 'viewer', 'viewer')).get('projects', 'group') == 'due'
    assert app.business.settings.preferences(User(1, 'admin', 'admin')).get('projects', 'group') == 'status'
    labels = viewer('GET', '/api/_settings/me')[1]['apps'][0]['labels']
    assert labels[:2] == ['Working days', 'Hours per day'] and 'Group my board by' in labels


def test_app_settings_are_visible_only_to_people_who_read_one_of_its_models(secured):
    admin = client(secured)
    assert admin('POST', '/api/_controls/roles', {'id': 'clerk', 'base': 'viewer'})[0] == 201
    change = {'kind': 'perm', 'key': 'clerk|Project', 'value': {'ops': [], 'read': None, 'write': None, 'scope': None}}
    assert admin('POST', '/api/_controls/overrides', {'changes': [change]})[0] == 200
    with secured.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) SELECT 'clerk', password, 'clerk' FROM evlet_users WHERE username='viewer'")
    clerk = client(secured, 'clerk')
    assert clerk('GET', '/api/_settings/me')[1]['apps'] == []
    assert clerk('GET', '/api/_settings/app/projects')[0] == 404 and clerk('GET', '/settings/app/projects')[0] == 404


def test_password_change_needs_the_current_password_and_signs_out_other_sessions(secured):
    first, second = client(secured, 'editor'), client(secured, 'editor')
    assert first('POST', '/api/_settings/me/password', {'current': 'wrong password here', 'new': 'another long enough password'})[1]['fields'] == {'current': 'Your current password is not right.'}
    assert first('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': 'short'})[1]['fields'] == {'new': 'Your workspace needs at least 15 characters.'}
    assert first('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': PASSWORD})[1]['fields'] == {'new': "Choose a password you haven't used here before."}
    assert len(first('GET', '/api/_settings/me')[1]['security']['sessions']) == 2
    assert first('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': 'a brand new long password'})[0] == 200
    assert first('GET', '/api/_settings/me')[0] == 200 and second('GET', '/api/_settings/me')[0] == 401
    assert http(secured, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[0] == 401
    assert http(secured, 'POST', '/api/auth/login', {'username': 'editor', 'password': 'a brand new long password'})[0] == 200
    sessions = first('GET', '/api/_settings/me')[1]['security']['sessions']
    other = next(item for item in sessions if not item['current'])
    assert first('DELETE', f"/api/_settings/me/sessions/{other['id']}")[1] == {'ended': 1}
    assert first('DELETE', '/api/_settings/me/sessions')[1] == {'ended': 0}


def test_photo_type_size_and_private_storage(secured):
    c = client(secured, 'editor')
    send = lambda content, kind='image/png': c('POST', '/api/_settings/me/photo', {'type': kind, 'data': base64.b64encode(content).decode()})
    assert send(b'GIF89a' + b'\0' * 10, 'image/gif')[1]['fields'] == {'photo': 'Use a PNG, JPG or WebP image.'}
    assert send(b'not really a png', 'image/png')[1]['fields'] == {'photo': 'Use a PNG, JPG or WebP image.'}
    assert send(PNG + b'\0' * (2 * 1024 * 1024))[1]['fields'] == {'photo': 'That photo is over 2 MB. Choose a smaller one.'}
    assert send(PNG)[1] == {'photo': True}
    status, content, headers = c('GET', '/api/_settings/me/photo')
    assert status == 200 and content == PNG and headers[b'content-type'] == b'image/png'
    assert http(secured, 'GET', '/api/_settings/me/photo')[0] == 401
    assert client(secured, 'viewer')('GET', '/api/_settings/me/photo')[0] == 404
    webp = b'RIFF\0\0\0\0WEBPVP8 ' + b'\0' * 32
    assert send(webp, 'image/webp')[1] == {'photo': True}
    with secured.database._connection() as db:
        assert db.execute("SELECT count(*) FROM evlet_attachments WHERE model='evlet_user_photo'").fetchone()[0] == 1
    assert c('DELETE', '/api/_settings/me/photo')[1] == {'photo': False} and c('GET', '/api/_settings/me/photo')[0] == 404


def test_photo_is_kept_in_private_file_storage(tmp_path, monkeypatch, hashed):
    from evlet import LocalFileStorage
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'files.sqlite3'))
    database = configured_database()
    app = build_app(database, LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False), storage=LocalFileStorage(tmp_path / 'private'))
    app.initialize()
    with database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', ('editor', hashed, 'editor'))
    c = client(app, 'editor')
    content = PNG + b'\1' * (1024 * 1024 + 10)
    assert c('POST', '/api/_settings/me/photo', {'type': 'image/png', 'data': base64.b64encode(content).decode()})[0] == 200
    assert [path.stat().st_size for path in (tmp_path / 'private').iterdir() if not path.name.startswith('.')] == [len(content)]
    assert c('GET', '/api/_settings/me/photo')[1] == content
    database.close()


def test_email_change_goes_through_a_one_time_link(secured, caplog):
    c = client(secured, 'editor')
    assert c('POST', '/api/_settings/me/email', {'email': 'nope'})[1]['fields'] == {'email': 'Enter a valid email address.'}
    assert c('POST', '/api/_settings/me/email', {'email': 'sara@example.com'})[1]['fields'] == {'password': 'Enter your password.'}
    with caplog.at_level('INFO', logger='evlet.settings'):
        sent = c('POST', '/api/_settings/me/email', {'email': 'sara@example.com', 'password': PASSWORD})[1]
    assert sent['sent'] == 'sara@example.com' and sent['link'].startswith('http://127.0.0.1:8000/settings/profile?confirm=')
    assert sent['link'] in caplog.text
    token = sent['link'].split('confirm=')[1]
    with secured.database._connection() as db:
        stored = db.execute('SELECT token_hash, new_email FROM evlet_email_tokens').fetchall()
    assert len(stored) == 1 and stored[0]['token_hash'] != token and stored[0]['new_email'] == 'sara@example.com'
    assert client(secured, 'viewer')('POST', '/api/_settings/me/email/confirm', {'token': token})[0] == 422
    assert c('GET', '/api/_settings/me')[1]['profile']['email'] == ''
    assert c('POST', '/api/_settings/me/email/confirm', {'token': token})[1] == {'email': 'sara@example.com'}
    assert c('GET', '/api/_settings/me')[1]['profile']['email'] == 'sara@example.com'
    assert c('POST', '/api/_settings/me/email/confirm', {'token': token})[1]['fields'] == {'token': 'This link has expired or was already used. Ask for a new one.'}


def test_two_step_enrollment_login_with_codes_and_backup_codes_once(settled):
    app = settled
    editor = client(app, 'editor')
    assert editor('GET', '/api/_settings/me')[1]['security']['two_step'] == {'on': False, 'since': None, 'agent': '', 'codes': 0, 'required': False, 'available': True, 'verified': False}
    assert editor('POST', '/api/_settings/me/two-step', {})[1]['fields'] == {'password': 'Enter your password.'}
    started = editor('POST', '/api/_settings/me/two-step', {'password': PASSWORD})[1]
    raw = base64.b32decode(started['secret'] + '=' * (-len(started['secret']) % 8))
    assert started['uri'].startswith('otpauth://totp/') and len(raw) == 20
    assert editor('POST', '/api/_settings/me/two-step/confirm', {'code': '000000' if totp(raw, int(time.time() // 30)) != '000000' else '111111'})[0] == 422
    codes = editor('POST', '/api/_settings/me/two-step/confirm', {'code': totp(raw, int(time.time() // 30))})[1]['codes']
    assert len(codes) == 8 and len(set(codes)) == 8
    with app.database._connection() as db:
        row = db.execute("SELECT two_step_secret, backup_codes FROM evlet_user_profile p JOIN evlet_users u ON u.id=p.user_id WHERE u.username='editor'").fetchone()
    # The secret is sealed and the backup codes are hashed, then sealed: neither is stored as given.
    assert raw not in bytes(row['two_step_secret']) and not any(code in row['backup_codes'] for code in codes)
    # A password alone no longer signs in; a code does, and a code is used only once.
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})
    assert status == 200 and data['two_step'] == 'code' and b'set-cookie' not in headers and 'csrf' not in data
    step = int(time.time() // 30) + 1
    assert http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': '12345'})[1] == {'error': 'That code is not right. Try again.'}
    status, payload, headers = http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': totp(raw, step)})
    assert status == 200 and payload['user']['username'] == 'editor' and b'set-cookie' in headers
    assert http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': totp(raw, step)})[0] == 401
    replay = http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[1]
    assert http(app, 'POST', '/api/auth/login', {'ticket': replay['ticket'], 'code': totp(raw, step)})[1] == {'error': 'That code is not right. Try again.'}
    for _ in range(3):
        http(app, 'POST', '/api/auth/login', {'ticket': replay['ticket'], 'code': '999999'})
    assert http(app, 'POST', '/api/auth/login', {'ticket': replay['ticket'], 'code': '999998'})[1] == {'error': 'That code is not right. Sign in again.'}
    # Backup codes: each works once.
    for code in (codes[0], codes[0].replace('-', '').upper()):
        ticket = http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[1]['ticket']
        assert http(app, 'POST', '/api/auth/login', {'ticket': ticket, 'code': code})[0] == (200 if code == codes[0] else 401)
    assert editor('GET', '/api/_settings/me')[0] == 200
    fresh = http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[1]
    status, data, headers = http(app, 'POST', '/api/auth/login', {'ticket': fresh['ticket'], 'code': codes[1]})
    assert status == 200
    cookie = headers[b'set-cookie'].decode().split(';')[0]
    me = http(app, 'GET', '/api/_settings/me', cookie=cookie)[1]
    assert me['security']['two_step']['codes'] == 6 and me['security']['two_step']['on']
    # Not required for editor: it can be turned off.
    off = {'password': PASSWORD, 'code': codes[2]}
    assert http(app, 'POST', '/api/_settings/me/two-step/off', {'password': PASSWORD}, cookie=cookie, csrf=data['csrf'])[1]['fields'] == {'code': 'Enter a current code from your app, or a backup code.'}
    assert http(app, 'POST', '/api/_settings/me/two-step/off', off, cookie=cookie, csrf=data['csrf'])[1] == {'on': False}
    assert http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[1]['user']['username'] == 'editor'


def test_a_role_that_requires_two_step_sets_it_up_at_sign_in_and_cannot_turn_it_off(settled):
    app = settled
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
    assert status == 200 and data['two_step'] == 'setup' and data['secret'] and b'set-cookie' not in headers
    raw = base64.b32decode(data['secret'] + '=' * (-len(data['secret']) % 8))
    assert http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': 'abcdef'})[0] == 401
    status, payload, headers = http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': totp(raw, int(time.time() // 30))})
    assert status == 200 and len(payload['backup_codes']) == 8
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], payload['csrf']
    me = http(app, 'GET', '/api/_settings/me', cookie=cookie)[1]
    assert me['security']['two_step'] | {'since': None} == {'on': True, 'since': None, 'agent': 'an unknown device', 'codes': 8, 'required': True, 'available': True, 'verified': True}
    status, body, _ = http(app, 'POST', '/api/_settings/me/two-step/off', {'password': PASSWORD, 'code': payload['backup_codes'][0]}, cookie=cookie, csrf=csrf)
    assert (status, body) == (409, {'error': 'Required for your role'})
    # Controls' two_step_roles decides who must use it.
    admin = lambda method, path, data=None: http(app, method, path, data, cookie=cookie, csrf=csrf)
    assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'security.two_step_roles', 'value': ['admin', 'editor']}]})[0] == 200
    assert http(app, 'POST', '/api/auth/login', {'username': 'editor', 'password': PASSWORD})[1]['two_step'] == 'setup'
    assert admin('GET', '/api/_controls/state')[1]['two_step'] is True


def test_without_a_secret_key_two_step_is_unavailable_and_sign_in_is_unchanged(secured):
    c = client(secured)
    assert c('GET', '/api/_settings/me')[1]['security']['two_step'] == {'on': False, 'since': None, 'agent': '', 'codes': 0, 'required': False, 'available': False, 'verified': False}
    status, payload, _ = c('POST', '/api/_settings/me/two-step', {'password': PASSWORD})
    assert status == 409 and 'cryptography package and a secret key' in payload['error']


def test_controls_require_two_step_lets_in_only_people_who_use_it(settled):
    app = settled
    app.routes = app.business.routes = app.business.controls.routes = type(app.routes)(**{**app.routes.__dict__, 'controls': ControlsAccess(require_two_step=True)})
    admin = client(app)
    assert admin('GET', '/api/_controls/state')[0] == 200
    # The session, not only the account, must have finished two-step sign-in.
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_sessions SET two_step=NULL')
    # Admin requires two-step sign-in, so that session is no longer accepted anywhere.
    assert admin('GET', '/api/_controls/state')[:2] == (401, {'error': TWO_STEP_FIRST})


def test_portal_hosts_serve_the_portal_subset_of_settings(tmp_path, monkeypatch, hashed):
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'portal.sqlite3'))
    routes = playground_routes(8000)
    database = configured_database()
    app = build_app(database, LocalAuth(database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False), routes=routes)
    app.initialize()
    with database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', ('buyer', hashed, 'portal'))
    host = {b'host': b'portal.localhost:8000'}
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': 'buyer', 'password': PASSWORD}, headers=host)
    assert status == 200
    buyer = lambda method, path, body=None: http(app, method, path, body, cookie=headers[b'set-cookie'].decode().split(';')[0], csrf=data['csrf'], headers=host)
    me = buyer('GET', '/api/_settings/me')[1]
    assert me['portal'] and me['pages'] == ['profile', 'security', 'notifications'] and me['apps'] == [] and not me['controls']
    assert [group['group'] for group in me['notifications']['events']] == ['Account']
    assert buyer('PATCH', '/api/_settings/me/profile', {'full_name': 'Buyer One'})[0] == 200
    assert buyer('PATCH', '/api/_settings/me/prefs', {'notify.new_device': [False, True], 'last_page': 'security'})[0] == 200
    assert buyer('PATCH', '/api/_settings/me/prefs', {'date': 'yyyy-MM-dd'})[0] == 422
    assert buyer('PATCH', '/api/_settings/me/prefs', {'last_page': 'appearance'})[0] == 422
    for method, path in (('POST', '/api/_settings/me/two-step'), ('GET', '/api/_settings/app/projects'), ('PATCH', '/api/_settings/app/projects')):
        assert buyer(method, path, {})[0] == 404, path
    assert buyer('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': 'a new portal password'})[0] == 200
    status, _, location = buyer('GET', '/settings')
    assert status == 302 and location[b'location'] == b'/settings/security'
    for path, expected in (('/settings/profile', 200), ('/settings/notifications', 200), ('/settings/appearance', 404), ('/settings/app/projects', 404)):
        assert buyer('GET', path)[0] in ((expected, 404) if expected == 200 else (404,)), path
    status, _, location = http(app, 'GET', '/settings/profile', headers=host)
    assert status == 302 and location[b'location'] == b'/'
    database.close()


def test_multiselect_and_suffix_fields(tmp_path):
    class Shift(Model):
        name = Text(required=True)
        days = MultiSelect(['mon', 'tue', 'wed'], default=['mon'], label='Days')
        rate = Decimal(scale=2, default='5', suffix='%')
        hours = Integer(default=8, suffix='hours')

    database = Database(tmp_path / 'fields.sqlite3', Registry(Shift))
    database.initialize()
    first = database.create(Shift, name='Early', days=['wed', 'mon'])
    database.create(Shift, name='Late', days=['tue'])
    assert first.days == ('mon', 'wed') and first.to_dict()['days'] == ['mon', 'wed']
    assert database.create(Shift, name='Default').days == ('mon',)
    for wrong in (['fri'], ['mon', 'mon'], 'mon,fri', 7):
        with pytest.raises(Exception):
            database.create(Shift, name='Bad', days=wrong)
    assert sorted(item.name for item in database.list(Shift, filters={'days__contains': 'mon'})) == ['Default', 'Early']
    schema = {field['name']: field for field in database.registry.ui_schema()['models'][0]['fields']}
    assert schema['days']['type'] == 'multiselect' and schema['days']['choices'] == ['mon', 'tue', 'wed']
    assert schema['rate']['suffix'] == '%' and schema['hours']['suffix'] == 'hours' and 'suffix' not in schema['name']
    with pytest.raises(ValueError):
        Integer(suffix='x' * 21)
    with pytest.raises(ValueError):
        MultiSelect(['a', 'a'])
    database.close()


def test_settings_declarations_are_checked():
    with pytest.raises(ValueError, match='sections'):
        type('Broken', (AppSettings,), {'sections': {'A': ('x',)}, 'y': Integer(default=1)})
    with pytest.raises(ValueError, match='editors'):
        type('Broken', (AppSettings,), {'editors': 'admin'})
    with pytest.raises(ValueError, match='settings take'):
        type('Broken', (UserPreferences,), {'total': Integer(compute=None, stored=False)})
    with pytest.raises(ValueError, match='preferences takes'):
        AppDefinition(preferences=AppSettings)
    assert AppDefinition(settings=type('Fine', (AppSettings,), {'x': Integer(default=1)})).settings.__sections__ == {'General': ('x',)}
