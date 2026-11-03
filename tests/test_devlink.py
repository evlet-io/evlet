"""One-time development sign-in links: development servers only, loopback only, a normal session."""

import asyncio
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from evlet import DevSignIn
from evlet import devlink
from evlet.cli.main import main, parser
from evlet.devlink import issue

from evlet.cli.install import install
from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401

HOST = b'127.0.0.1:8000'


def call(app, method, path, *, cookie=None, headers=(), client='127.0.0.1', server='127.0.0.1', body=None):
    async def run():
        route, _, query = path.partition('?')
        sent = [(b'host', HOST), *headers]
        if cookie:
            sent.append((b'cookie', cookie.encode()))
        if body is not None:
            sent.append((b'content-type', b'application/json'))
        messages = []
        events = [{'type': 'http.request', 'body': json.dumps(body).encode() if body is not None else b''}]
        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}
        async def send(event):
            messages.append(event)
        await app({'type': 'http', 'method': method, 'path': route, 'query_string': query.encode(), 'headers': sent,
                   'client': (client, 50000), 'server': (server, 8000)}, receive, send)
        return messages[0]['status'], dict(messages[0]['headers']), messages[1]['body']
    return asyncio.run(run())


def session_cookie(headers):
    return headers[b'set-cookie'].decode().split(';')[0]


@pytest.fixture
def playground(tmp_path):
    from evlet.cli.playground import playground_app
    return playground_app(tmp_path / 'demo')


@pytest.fixture
def instance(project):  # noqa: F811
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    app.business.auth.create_user(username='owner', password='a long enough password', role='admin')
    return project, app


def users(app):
    with app.database._connection() as connection:
        return [row['username'] for row in connection.execute('SELECT username FROM evlet_users ORDER BY id')]


def test_link_signs_in_once_with_a_normal_session(playground):
    app = DevSignIn(playground)
    token, username = issue(playground.database, create_admin=True)
    assert username == 'demo-admin' and users(playground) == ['demo-admin']
    with playground.database._connection() as connection:
        stored = connection.execute('SELECT hash FROM evlet_dev_links').fetchone()['hash']
    assert token not in stored and stored == devlink._digest(token)

    status, headers, _ = call(app, 'GET', f'/_dev/signin?token={token}', headers=[(b'user-agent', b'Browser')])
    assert status == 302 and headers[b'location'] == b'/'
    cookie = headers[b'set-cookie'].decode()
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie and cookie.startswith('evlet_session=')
    status, _, body = call(app, 'GET', '/api/auth/session', cookie=session_cookie(headers))
    assert status == 200 and json.loads(body)['user']['username'] == 'demo-admin'
    with playground.database._connection() as connection:
        row = connection.execute('SELECT realm, agent, two_step FROM evlet_sessions').fetchone()
        audit = connection.execute("SELECT actor, detail FROM evlet_audit WHERE detail LIKE '%development link%' ORDER BY id").fetchall()
    assert row['realm'] == 'desk' and row['agent'] == 'Browser' and row['two_step'] is not None
    assert [tuple(item) for item in audit][-1] == ('demo-admin', 'Signed in with a development link')

    # Single use: the same link never signs in again.
    status, headers, body = call(app, 'GET', f'/_dev/signin?token={token}')
    assert status == 403 and b'set-cookie' not in headers and b'evlet dev-link' in body
    assert call(app, 'GET', '/_dev/signin')[0] == 403
    assert call(app, 'GET', '/_dev/signin?token=guess')[0] == 403


def test_link_expires(playground, monkeypatch):
    app = DevSignIn(playground)
    token, _ = issue(playground.database, create_admin=True)
    real = devlink.time.time
    monkeypatch.setattr(devlink, 'time', SimpleNamespace(time=lambda: real() + devlink.MINUTES * 60 + 1))
    status, headers, _ = call(app, 'GET', f'/_dev/signin?token={token}')
    assert status == 403 and b'set-cookie' not in headers
    with playground.database._connection() as connection:
        assert connection.execute('SELECT count(*) FROM evlet_dev_links').fetchone()[0] == 0


def test_link_refuses_other_clients_hosts_and_sites(playground):
    app = DevSignIn(playground)
    token, _ = issue(playground.database, create_admin=True)
    assert call(app, 'GET', f'/_dev/signin?token={token}', client='192.168.1.20')[0] == 404
    assert call(app, 'GET', f'/_dev/signin?token={token}', client='2001:db8::1')[0] == 404
    assert call(app, 'GET', f'/_dev/signin?token={token}', server='0.0.0.0')[0] == 404
    assert call(app, 'POST', f'/_dev/signin?token={token}', body={})[0] == 404
    assert call(app, 'GET', f'/_dev/signin?token={token}', headers=[(b'sec-fetch-site', b'cross-site')])[0] == 403
    assert call(app, 'GET', f'/_dev/signin?token={token}', headers=[(b'origin', b'https://evil.example')])[0] == 403
    # None of those used the link up.
    assert call(app, 'GET', f'/_dev/signin?token={token}', client='::1')[0] == 302


def test_admin_is_created_only_on_an_empty_playground(playground, tmp_path):
    from evlet.cli.playground import playground_app
    playground.business.auth.create_user(username='owner', password='a long enough password', role='admin')
    _, username = issue(playground.database, create_admin=True)
    assert username == 'owner' and users(playground) == ['owner']

    other = playground_app(tmp_path / 'other')
    with pytest.raises(ValueError, match='no users yet'):
        issue(other.database)
    assert users(other) == []
    issue(other.database, create_admin=True)
    issue(other.database, create_admin=True)
    assert users(other) == ['demo-admin']


def test_link_user_choice(playground):
    auth = playground.business.auth
    auth.create_user(username='owner', password='a long enough password', role='admin')
    auth.create_user(username='second', password='a long enough password', role='admin')
    auth.create_user(username='client', password='a long enough password', role='portal')
    with pytest.raises(ValueError, match='--user NAME'):
        issue(playground.database)
    assert issue(playground.database, 'Second')[1] == 'second'
    # Portal accounts sign in on portal hosts; desk links never name them.
    with pytest.raises(ValueError, match='No active desk user named client'):
        issue(playground.database, 'client')
    auth.update_user(2, {'active': False})
    assert issue(playground.database)[1] == 'owner'


def test_linked_session_keeps_every_permission_check(playground):
    app = DevSignIn(playground)
    playground.business.auth.create_user(username='reader', password='a long enough password', role='viewer')
    token, _ = issue(playground.database, 'reader')
    status, headers, _ = call(app, 'GET', f'/_dev/signin?token={token}')
    cookie = session_cookie(headers)
    status, _, body = call(app, 'GET', '/api/auth/session', cookie=cookie)
    csrf = json.loads(body)['csrf']
    assert call(app, 'GET', '/api/customer', cookie=cookie)[0] == 200
    protect = [(b'x-evlet-request', b'1')]
    assert call(app, 'POST', '/api/customer', cookie=cookie, headers=protect, body={'name': 'A'})[0] == 403
    assert call(app, 'POST', '/api/customer', cookie=cookie, headers=[*protect, (b'x-csrf-token', csrf.encode())], body={'name': 'A'})[0] == 403
    assert call(app, 'GET', '/api/_controls/settings', cookie=cookie)[0] == 404
    # Deactivating the account ends the session like any other.
    playground.business.auth.update_user(1, {'active': False})
    assert json.loads(call(app, 'GET', '/api/auth/session', cookie=cookie)[2])['user'] is None


def test_serve_never_registers_the_link_and_dev_does(instance, monkeypatch):
    from evlet.cli import serve as serving
    project, app = instance
    token, _ = issue(app.database, 'owner')
    # The served application has no such route.
    assert call(app, 'GET', f'/_dev/signin?token={token}')[0] == 404
    captured = []
    monkeypatch.setattr(serving, 'run_server', lambda application, **options: captured.append(application))
    serving.serve(project, 'acme')
    target = captured[-1]
    while target is not None:
        assert not isinstance(target, DevSignIn)
        target = getattr(target, 'app', None)

    serving.serve(project, 'acme', development=True, user='owner')
    assert captured[-1] == 'evlet.cli.serve:development_factory'
    monkeypatch.setenv('EVLET_CLI_PROJECT', str(project))
    monkeypatch.setenv('EVLET_CLI_INSTANCE', 'acme')
    monkeypatch.setenv('EVLET_CLI_PORT', '8000')
    monkeypatch.setenv('EVLET_CLI_LINK', '1')
    monkeypatch.setenv('EVLET_CLI_LINK_USER', 'owner')
    factory = serving.development_factory()
    assert isinstance(factory, DevSignIn) and factory.announce and factory.user == 'owner' and not factory.create_admin
    monkeypatch.setenv('EVLET_CLI_LINK', '0')
    assert not serving.development_factory().announce

    # The link works on the development application.
    status, headers, _ = call(DevSignIn(app), 'GET', f'/_dev/signin?token={token}')
    assert status == 302 and b'set-cookie' in headers


def test_dev_link_command_for_an_instance(instance, capsys):
    project, app = instance
    main(['dev-link', '--project', str(project)])
    output = capsys.readouterr().out
    assert 'Sign in as owner' in output and 'http://127.0.0.1:8000/_dev/signin?token=' in output
    token = output.split('token=')[1].split()[0]
    status, headers, _ = call(DevSignIn(app), 'GET', f'/_dev/signin?token={token}')
    assert status == 302
    main(['dev-link', '--project', str(project), '--port', '8123'])
    assert 'http://127.0.0.1:8123/_dev/signin?token=' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(['dev-link', '--project', str(project), '--user', 'nobody'])
    assert 'No active desk user named nobody' in capsys.readouterr().err


def test_dev_link_finds_the_running_playground(tmp_path, monkeypatch, capsys):
    from evlet.cli.playground import playground_app
    monkeypatch.setattr(devlink.tempfile, 'gettempdir', lambda: str(tmp_path))
    monkeypatch.chdir(tmp_path)
    live = tmp_path / 'evlet-playground-live'
    live.mkdir(mode=0o700)
    playground = playground_app(live)
    issue(playground.database, create_admin=True)
    with pytest.raises(SystemExit):
        main(['dev-link'])
    assert 'No evlet playground is running' in capsys.readouterr().err
    devlink.write_marker(live, 8000)
    # A folder anyone can write and a stopped server are ignored.
    shared = tmp_path / 'evlet-playground-shared'
    shared.mkdir()
    shared.chmod(0o777)
    (shared / 'server.json').write_text(json.dumps({'port': 9000, 'pid': os.getpid()}))
    stopped = tmp_path / 'evlet-playground-stopped'
    stopped.mkdir(mode=0o700)
    (stopped / 'server.json').write_text(json.dumps({'port': 9001, 'pid': 2**22 + 12345}))
    assert devlink.running_playgrounds() == [(8000, live)]
    main(['dev-link'])
    output = capsys.readouterr().out
    assert 'Sign in as demo-admin' in output and 'http://127.0.0.1:8000/_dev/signin?token=' in output
    token = output.split('token=')[1].split()[0]
    assert call(DevSignIn(playground), 'GET', f'/_dev/signin?token={token}')[0] == 302


def test_startup_announces_a_link(playground, capsys):
    async def run():
        events = [{'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}]
        sent = []
        async def receive():
            return events.pop(0)
        async def send(message):
            sent.append(message['type'])
        await DevSignIn(playground, announce=True, create_admin=True)({'type': 'lifespan'}, receive, send)
        return sent
    assert asyncio.run(run())[0] == 'lifespan.startup.complete'
    output = capsys.readouterr().out
    assert 'Sign in as demo-admin (works once, for 10 minutes):' in output and '/_dev/signin?token=' in output
    assert 'password' not in output.lower()


def test_link_options_parse():
    args = parser().parse_args(['playground', '--no-link', '--user', 'owner'])
    assert args.no_link and args.user == 'owner'
    args = parser().parse_args(['dev', '--user', 'owner'])
    assert not args.no_link and args.user == 'owner'
    with pytest.raises(SystemExit):
        parser().parse_args(['serve', 'acme', '--user', 'owner'])
