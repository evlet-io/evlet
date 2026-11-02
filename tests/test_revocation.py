"""Every way of ending a person's access goes through LocalAuth.end_sessions, with one matrix of what ends."""
import pytest

from evlet.controls import grant, reset_two_step_from_shell
from test_api_keys import create, with_key
from test_business import PASSWORD, client, hashed, secured  # noqa: F401
from test_settings_security import settled  # noqa: F401
import test_assistants as A


def credentials(app, role='editor'):
    """One of each: a browser session, an assistant's access and refresh tokens, an API key and an unused authorization code."""
    A.settings(app, allow_writes=True)
    client_id, tokens, _ = A.connect(app, role)
    key = create(client(app), 'Script', user=role)[1]['token']
    return {'browser': client(app, role), 'client_id': client_id, 'tokens': tokens, 'key': key, 'code': A.code_for(app, role)}


def alive(app, held):
    """Which credentials still work. The refresh token is checked last, since using it rotates it."""
    refresh = {'grant_type': 'refresh_token', 'refresh_token': held['tokens']['refresh_token'], 'client_id': held['client_id']}
    return {
        'browser': held['browser']('GET', '/api/_settings/me')[0] == 200,
        'access': A.rpc(app, held['tokens']['access_token'], 'ping')[0] == 200,
        'key': with_key(app, held['key'], 'GET', '/api/customer')[0] == 200,
        'code': A.form(app, '/_/oauth/token', held['code'])[0] == 200,
        'refresh': A.form(app, '/_/oauth/token', refresh)[0] == 200,
    }


def user_id(app, username):
    return next(row['id'] for row in client(app)('GET', '/api/_controls/users')[1]['items'] if row['username'] == username)


ENDED = {'browser': False, 'access': False, 'code': False, 'refresh': False}


def test_everything_works_before_anything_ends(settled):
    assert alive(settled, credentials(settled)) == dict.fromkeys(('browser', 'access', 'key', 'code', 'refresh'), True)


def test_a_settings_password_change_ends_other_sessions_and_assistants_and_keeps_keys(settled):
    held = credentials(settled)
    person = client(settled, 'editor')
    assert person('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': PASSWORD + ' changed'})[0] == 200
    assert alive(settled, held) == {**ENDED, 'key': True}
    assert person('GET', '/api/_settings/me')[0] == 200


def test_controls_sign_out_everywhere_keeps_keys(settled):
    held = credentials(settled)
    assert client(settled)('PATCH', f"/api/_controls/users/{user_id(settled, 'editor')}", {'sessions': 'end', 'revoke_keys': False})[0] in (200, 204)
    assert alive(settled, held) == {**ENDED, 'key': True}


def test_a_settings_password_change_can_also_revoke_api_keys(settled):
    held = credentials(settled)
    person = client(settled, 'editor')
    assert person('GET', '/api/_settings/me')[1]['security']['api_keys'] == 1
    assert person('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': PASSWORD + ' changed', 'revoke_keys': 'yes'})[0] == 422
    status, body, _ = person('POST', '/api/_settings/me/password', {'current': PASSWORD, 'new': PASSWORD + ' changed', 'revoke_keys': True})
    assert (status, body) == (200, {'changed': True, 'revoked_keys': 1})
    assert alive(settled, held) == {**ENDED, 'key': False}
    assert person('GET', '/api/_settings/me')[1]['security']['api_keys'] == 0
    with settled.database._connection() as db:
        assert db.execute("SELECT detail FROM evlet_audit WHERE area='Users' ORDER BY id DESC").fetchone()[0].endswith('Revoked 1 API key')


def test_controls_sign_out_everywhere_can_also_revoke_api_keys(settled):
    held = credentials(settled)
    admin, editor = client(settled), user_id(settled, 'editor')
    row = next(item for item in admin('GET', '/api/_controls/users')[1]['items'] if item['id'] == editor)
    assert row['keys'] == 1
    # The choice goes with signing out, as an explicit true or false.
    assert admin('PATCH', f'/api/_controls/users/{editor}', {'revoke_keys': True})[0] == 422
    assert admin('PATCH', f'/api/_controls/users/{editor}', {'sessions': 'end', 'revoke_keys': 1})[0] == 422
    assert admin('PATCH', f'/api/_controls/users/{editor}', {'sessions': 'end', 'revoke_keys': True})[0] == 204
    assert alive(settled, held) == {**ENDED, 'key': False}
    assert next(item for item in admin('GET', '/api/_controls/users')[1]['items'] if item['id'] == editor)['keys'] == 0
    with settled.database._connection() as db:
        assert db.execute("SELECT detail FROM evlet_audit WHERE area='Users' ORDER BY id DESC").fetchone()[0] == 'Signed out everywhere · Revoked 1 API key'


@pytest.mark.parametrize('how', ['controls', 'shell'])
def test_a_two_step_reset_also_revokes_keys(settled, how):
    held = credentials(settled)
    if how == 'controls':
        assert client(settled)('PATCH', f"/api/_controls/users/{user_id(settled, 'editor')}", {'two_step': 'reset'})[0] in (200, 204)
    else:
        reset_two_step_from_shell(settled.database.path, 'editor')
    assert alive(settled, held) == {**ENDED, 'key': False}


def test_deactivation_revokes_keys_for_good(settled):
    held = credentials(settled)
    admin, editor = client(settled), user_id(settled, 'editor')
    assert admin('PATCH', f'/api/_controls/users/{editor}', {'active': False})[0] in (200, 204)
    assert admin('PATCH', f'/api/_controls/users/{editor}', {'active': True})[0] in (200, 204)
    assert alive(settled, held) == {**ENDED, 'key': False}


def test_break_glass_revokes_keys(settled):
    held = credentials(settled, 'viewer')
    grant(settled.database.path, 'viewer')
    assert alive(settled, held) == {**ENDED, 'key': False}
