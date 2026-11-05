"""evlet dev creates the first admin on an empty instance; evlet serve never does; users set-password."""

import asyncio
import getpass
import sqlite3

import pytest

from evlet import DevSignIn
from evlet.cli import serve as serving
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.devlink import issue

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


def test_dev_creates_one_admin_and_a_working_link(empty, capsys):
    _, app = empty
    out, err = start(app, capsys, create_admin='admin')
    assert accounts(app) == [('admin', 'admin', 1)]
    assert 'No accounts yet. Created admin "admin" (no password set). Sign in (works once, for 10 minutes):' in out
    token = out.split('token=')[1].split()[0]
    status, headers, _ = call(DevSignIn(app), 'GET', f'/_dev/signin?token={token}')
    assert status == 302
    me = call(DevSignIn(app), 'GET', '/api/auth/session', cookie=session_cookie(headers))[2]
    assert b'"admin"' in me
    with app.database._connection() as connection:
        stored = connection.execute('SELECT password FROM evlet_users').fetchone()[0]
    # The password is random: neither printed nor guessable, and never in the output.
    assert stored not in out + err and stored.startswith('scrypt:')
    assert audit_rows(app)[0][:2] == ('cli', 'admin') and 'Random password' in audit_rows(app)[0][2]
    # A second start finds the account and creates nothing.
    out, _ = start(app, capsys, create_admin='admin')
    assert accounts(app) == [('admin', 'admin', 1)] and 'Sign in as admin (works once' in out and 'Created' not in out


def test_dev_user_names_the_admin(empty, capsys):
    _, app = empty
    out, _ = start(app, capsys, user='Boss', create_admin='boss')
    assert accounts(app) == [('boss', 'admin', 1)] and 'Created admin "boss"' in out


def test_dev_rejects_a_bad_name_and_creates_nothing(empty, capsys):
    _, app = empty
    out, _ = start(app, capsys, create_admin='no')
    assert accounts(app) == [] and 'Cannot create the first admin' in out and 'Use 3 to 64' in out


def test_no_link_creates_nothing_and_setup_stays_in_the_browser(empty, monkeypatch, capsys):
    project, app = empty
    captured = []
    monkeypatch.setattr(serving, 'run_server', lambda application, **options: captured.append(application))
    serving.serve(project, 'acme', development=True, link=False)
    for key, value in (('PROJECT', str(project)), ('INSTANCE', 'acme'), ('PORT', '8000'), ('LINK', '0')):
        monkeypatch.setenv('EVLET_CLI_' + key, value)
    factory = serving.development_factory()
    assert factory.announce is False and factory.create_admin is False
    assert start(app, capsys, announce=False)[0] == '' and accounts(app) == []
    assert app.business.auth.setup_needed()


def test_dev_defaults_to_admin_and_serve_never_creates(empty, monkeypatch):
    project, app = empty
    monkeypatch.setenv('EVLET_CLI_PROJECT', str(project))
    monkeypatch.setenv('EVLET_CLI_INSTANCE', 'acme')
    monkeypatch.setenv('EVLET_CLI_PORT', '8000')
    monkeypatch.setenv('EVLET_CLI_LINK', '1')
    monkeypatch.delenv('EVLET_CLI_LINK_USER', raising=False)
    assert serving.development_factory().create_admin == 'admin'
    captured = []
    monkeypatch.setattr(serving, 'run_server', lambda application, **options: captured.append(application))
    serving.serve(project, 'acme')
    target = captured[-1]
    while target is not None:
        assert not isinstance(target, DevSignIn)
        target = getattr(target, 'app', None)
    assert accounts(app) == []
    # Even asking the link code directly never creates an account without create_admin.
    with pytest.raises(ValueError, match='evlet dev'):
        issue(app.database)
    assert accounts(app) == []


def test_dev_link_command_points_to_evlet_dev(empty, capsys):
    project, app = empty
    with pytest.raises(SystemExit):
        main(['dev-link', '--project', str(project)])
    assert 'evlet dev' in capsys.readouterr().err and accounts(app) == []


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
