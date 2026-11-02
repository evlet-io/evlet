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
    assert client(settled)('PATCH', f"/api/_controls/users/{user_id(settled, 'editor')}", {'sessions': 'end'})[0] in (200, 204)
    assert alive(settled, held) == {**ENDED, 'key': True}


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
