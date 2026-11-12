"""evlet dev signs in the built-in Administrator with a one-time link; it chooses its password (and two-step) in the browser.
evlet serve never prints links; it says how to set the password from the shell. users set-password."""

import asyncio
import base64
import getpass
import json
import time

import pytest

from evlet import DevSignIn
from evlet.cli import serve as serving
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.devlink import issue
from evlet.settings import totp

from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401
from test_devlink import call, session_cookie

GOOD = 'a long enough password'
ORIGIN = [(b'origin', b'http://127.0.0.1:8000'), (b'x-evlet-request', b'1')]


@pytest.fixture
def empty(project):  # noqa: F811
    install(project, 'acme', app_name='notes', trust=True)
    return project, runtime(project)


def start(app, capsys, announce=True, **options):
    async def run():
        events = [{'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}]
        async def receive():
            return events.pop(0)
        async def send(message):
            pass
        await DevSignIn(app, announce=announce, **options)({'type': 'lifespan'}, receive, send)
    asyncio.run(run())
    captured = capsys.readouterr()
    return captured.out, captured.err


def accounts(app):
    with app.database._connection() as connection:
        return [tuple(row) for row in connection.execute('SELECT username, role, active FROM evlet_users ORDER BY id')]


def audit_rows(app):
    with app.database._connection() as connection:
        return [tuple(row) for row in connection.execute("SELECT actor, target, detail FROM evlet_audit WHERE area='Users' ORDER BY id")]


def post(app, path, body, cookie, csrf):
    return call(app, 'POST', path, body=body, cookie=cookie, headers=[*ORIGIN, (b'x-csrf-token', csrf.encode())])


def test_dev_prints_a_link_for_the_administrator_who_then_sets_a_password_and_two_step(empty, capsys):
    _, app = empty
    out, err = start(app, capsys)
    # Nothing new is created: the instance already has its Administrator, without a password.
    assert accounts(app) == [('admin', 'admin', 1)]
    assert 'The admin account has no password yet. Open this link to sign in and choose one (works once, for 10 minutes):' in out
    token = out.split('token=')[1].split()[0]
    status, headers, _ = call(DevSignIn(app), 'GET', f'/_dev/signin?token={token}')
    assert status == 302
    cookie = session_cookie(headers)
    session = json.loads(call(app, 'GET', '/api/auth/session', cookie=cookie)[2])
    assert session['user']['username'] == 'admin' and session['password_needed'] is True
    csrf = session['csrf']
    # Admin accounts follow config.py's minimum, 15 by default.
    status, _, body = post(app, '/api/auth/password', {'password': 'short'}, cookie, csrf)
    assert status == 422 and b'at least 15' in body
    # Admins need two-step sign-in: the password is saved, this session ends and the answer is the set-up.
    status, headers, body = post(app, '/api/auth/password', {'password': GOOD}, cookie, csrf)
    answer = json.loads(body)
    assert status == 200 and answer['two_step'] == 'setup' and answer['ticket'] and answer['secret']
    assert b'Max-Age=0' in headers[b'set-cookie']
    assert json.loads(call(app, 'GET', '/api/auth/session', cookie=cookie)[2])['user'] is None
    raw = base64.b32decode(answer['secret'] + '=' * (-len(answer['secret']) % 8))
    status, headers, body = call(app, 'POST', '/api/auth/login', body={'ticket': answer['ticket'], 'code': totp(raw, int(time.time() // 30))}, headers=ORIGIN)
    assert status == 200 and json.loads(body)['backup_codes']
    session = json.loads(call(app, 'GET', '/api/auth/session', cookie=session_cookie(headers))[2])
    assert session['user']['username'] == 'admin' and 'password_needed' not in session
    # Only a first password: setting another goes through Settings with the current one.
    status, _, body = post(app, '/api/auth/password', {'password': 'yet another long password'}, session_cookie(headers), session['csrf'])
    assert status == 409 and b'already has a password' in body
    assert ('admin', 'admin', 'First password set') in audit_rows(app)
    # A second start prints a plain link.
    out, _ = start(app, capsys)
    assert 'Sign in as admin (works once' in out and 'no password' not in out


def test_without_required_two_step_the_first_password_keeps_the_session(empty, capsys):
    _, app = empty
    app.business.controls.two_step_required = lambda role: False
    token, username = issue(app.database)
    assert username == 'admin'
    status, headers, _ = call(DevSignIn(app), 'GET', f'/_dev/signin?token={token}')
    cookie = session_cookie(headers)
    csrf = json.loads(call(app, 'GET', '/api/auth/session', cookie=cookie)[2])['csrf']
    status, headers, body = post(app, '/api/auth/password', {'password': GOOD}, cookie, csrf)
    assert status == 200 and json.loads(body) == {'password': True} and b'set-cookie' not in headers
    session = json.loads(call(app, 'GET', '/api/auth/session', cookie=cookie)[2])
    assert session['user']['username'] == 'admin' and 'password_needed' not in session


def test_dev_user_names_another_account(empty, capsys):
    _, app = empty
    app.business.auth.create_user(username='boss', password=GOOD, role='editor')
    out, _ = start(app, capsys, user='Boss')
    assert 'Sign in as boss (works once' in out
    out, _ = start(app, capsys, user='nobody')
    assert 'No sign-in link: No active desk user named nobody' in out


def test_no_link_prints_nothing_and_setup_stays_in_the_browser(empty, monkeypatch, capsys):
    project, app = empty
    captured = []
    monkeypatch.setattr(serving, 'run_server', lambda application, **options: captured.append(application))
    serving.serve(project, 'acme', development=True, link=False)
    for key, value in (('PROJECT', str(project)), ('INSTANCE', 'acme'), ('PORT', '8000'), ('LINK', '0')):
        monkeypatch.setenv('EVLET_CLI_' + key, value)
    factory = serving.development_factory()
    assert factory.announce is False
    assert start(app, capsys, announce=False)[0] == '' and accounts(app) == [('admin', 'admin', 1)]
    assert app.business.auth.setup_needed()


def test_serve_never_prints_a_link_and_says_how_to_set_the_password(empty, monkeypatch, capsys):
    project, app = empty
    monkeypatch.setenv('EVLET_CLI_PROJECT', str(project))
    monkeypatch.setenv('EVLET_CLI_INSTANCE', 'acme')
    monkeypatch.setenv('EVLET_CLI_PORT', '8000')
    monkeypatch.setenv('EVLET_CLI_LINK', '1')
    monkeypatch.delenv('EVLET_CLI_LINK_USER', raising=False)
    assert serving.development_factory().user is None
    captured = []
    monkeypatch.setattr(serving, 'run_server', lambda application, **options: captured.append(application))
    serving.serve(project, 'acme')
    target = captured[-1]
    while target is not None:
        assert not isinstance(target, DevSignIn)
        target = getattr(target, 'app', None)
    out = capsys.readouterr().out
    assert 'The built-in Administrator has no password yet. Set one with: evlet users set-password acme admin' in out
    assert '_dev/signin' not in out
    # Once it has one, serve says nothing about it.
    monkeypatch.setattr(getpass, 'getpass', lambda prompt='': GOOD)
    main(['users', 'set-password', 'acme', 'admin', '--project', str(project)])
    capsys.readouterr()
    serving.serve(project, 'acme')
    assert 'no password' not in capsys.readouterr().out


def test_set_password_enforces_minimum_ends_sessions_and_audits(empty, monkeypatch, capsys):
    project, app = empty
    app.business.auth.create_user(username='owner', password=GOOD, role='admin')
    token, _ = issue(app.database, 'owner')
    status, headers, _ = call(DevSignIn(app), 'GET', f'/_dev/signin?token={token}')
    cookie = session_cookie(headers)
    assert b'"owner"' in call(app, 'GET', '/api/auth/session', cookie=cookie)[2]

    def answers(*values):
        queue = list(values)
        monkeypatch.setattr(getpass, 'getpass', lambda prompt='': queue.pop(0))

    answers('short', 'short')
    with pytest.raises(SystemExit):
        main(['users', 'set-password', 'acme', 'owner', '--project', str(project)])
    assert 'Use between 15 and 128 characters' in capsys.readouterr().err
    assert b'"owner"' in call(app, 'GET', '/api/auth/session', cookie=cookie)[2]

    answers('another long password', 'different long password')
    with pytest.raises(SystemExit):
        main(['users', 'set-password', 'acme', 'owner', '--project', str(project)])
    assert 'differ' in capsys.readouterr().err

    answers('another long password', 'another long password')
    main(['users', 'set-password', 'acme', 'owner', '--project', str(project)])
    out = capsys.readouterr()
    assert 'another long password' not in out.out + out.err
    assert b'"user":null' in call(app, 'GET', '/api/auth/session', cookie=cookie)[2].replace(b' ', b'')
    login = call(app, 'POST', '/api/auth/login', body={'username': 'owner', 'password': 'another long password'}, headers=ORIGIN)
    assert login[0] == 200
    assert call(app, 'POST', '/api/auth/login', body={'username': 'owner', 'password': GOOD}, headers=ORIGIN)[0] == 401
    assert audit_rows(app)[-1][:2] == ('cli', 'owner') and 'Password set' in audit_rows(app)[-1][2]


def test_set_password_unknown_user(empty, monkeypatch, capsys):
    project, _ = empty
    monkeypatch.setattr(getpass, 'getpass', lambda prompt='': GOOD)
    with pytest.raises(SystemExit):
        main(['users', 'set-password', 'acme', 'nobody', '--project', str(project)])
    assert 'No user' in capsys.readouterr().err


def test_set_email_command_sets_the_administrators_email(empty, capsys):
    project, app = empty
    main(['users', 'set-email', 'acme', 'admin', 'ops@example.test', '--project', str(project)])
    assert capsys.readouterr().out == 'Email for admin is now ops@example.test.\n'
    with app.database._connection() as connection:
        assert connection.execute("SELECT p.email FROM evlet_user_profile p JOIN evlet_users u ON u.id=p.user_id "
                                  "WHERE u.username='admin'").fetchone()[0] == 'ops@example.test'
    assert audit_rows(app)[-1] == ('cli', 'admin', 'Email set from the server shell')
    with pytest.raises(SystemExit):
        main(['users', 'set-email', 'acme', 'admin', 'not-an-email', '--project', str(project)])
    assert 'not a valid email address' in capsys.readouterr().err
