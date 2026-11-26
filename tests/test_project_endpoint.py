"""The project endpoint (docs/project-endpoint.md): project.db, the operation log, the operate tools and the supervisor.

Real SQLite throughout. The supervisor tests start `evlet endpoint` as a real process, which runs the instance as a
real child process serving /health; they kill the child and see it come back, and see an operation that needs the
instance's exclusive lock stop and restart it.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

from evlet.cli import core, endpoint, oplog
from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.main import main
from evlet.cli.project import set_project_mode
from evlet.instances import read_project
from support import PASSWORD, client, http
from test_cli_lifecycle import environment_stub  # noqa: F401  (fixture)


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@pytest.fixture
def project(tmp_path, environment_stub):  # noqa: F811
    from evlet.cli.setup import setup
    root = setup(tmp_path / 'project').resolve()
    create_app(root, 'notes')
    create_instance(root, 'acme')
    core.install_app(root, 'acme', 'notes', trust=True)
    yield root
    for name in list(sys.modules):
        if name == 'notes' or name.startswith('notes.'):
            del sys.modules[name]


def endpoint_app(root, port=8000):
    """The endpoint Application in this process, answering on 127.0.0.1:8000 like the other ASGI tests."""
    endpoint.create(root, port=port)
    endpoint.set_owner_password(root, PASSWORD)
    _, runtime = endpoint.application(root)
    runtime.app.initialize()
    return runtime


def test_without_an_endpoint_nothing_changes(project, capsys):
    assert not oplog.exists(project) and not (project / '.evlet/endpoint.json').exists()
    assert core.backup_create(project, 'acme').ok
    assert not oplog.exists(project)
    main(['--project', str(project), 'project'])
    assert 'Endpoint: none\n' in capsys.readouterr().out


def test_project_db_holds_credentials_and_the_operation_log(project, capsys):
    assert endpoint.create(project) is True and endpoint.create(project) is False
    assert oplog.exists(project) and endpoint.read_settings(project)['port'] == endpoint.DEV_PORT
    assert endpoint.read_settings(project)['instances'] == {'acme': 8000}
    # The CLI's lifecycle functions append to the log once project.db exists.
    outcome = core.backup_create(project, 'acme')
    with pytest.raises(ValueError):
        core.backup_restore(project, 'acme', 'missing.sqlite3', yes=True)
    rows = oplog.operations(project)
    assert [(row['kind'], row['result'], row['origin']) for row in rows] == [('backup_restore', 'failed', 'cli'), ('backup_create', 'ok', 'cli')]
    assert rows[1]['instance'] == 'acme' and rows[1]['output'] == list(outcome.messages) and rows[1]['before'] == outcome.before
    assert rows[1]['arguments'] == {'instance': 'acme'}
    assert rows[0]['error'] and rows[0]['finished']
    # Append only: the database refuses to change a row.
    import sqlite3
    with sqlite3.connect(oplog.database_path(project)) as connection, pytest.raises(sqlite3.DatabaseError):
        connection.execute('UPDATE operation SET result = ? WHERE id = 1', ('ok',))
    main(['--project', str(project), 'project'])
    assert 'Endpoint: http://127.0.0.1:7999 (not running' in capsys.readouterr().out


def test_production_refuses_until_two_step_sign_in_is_set_up(project):
    endpoint.create(project)
    with pytest.raises(ValueError, match='no public host'):
        set_project_mode(project, 'production')
    endpoint.create(project, host='project.example.com')
    with pytest.raises(ValueError, match='two-step sign-in'):
        set_project_mode(project, 'production')
    assert read_project(project)['mode'] == 'development'
    from evlet import qb
    from evlet.writes import shell_transaction
    with shell_transaction(oplog.database_path(project)) as connection:
        owner = connection.run(qb.select('evlet_users', 'id').where(username='admin')).one()['id']
        connection.run(qb.update('evlet_user_profile', two_step_secret=b'sealed').where(user_id=owner))
    assert set_project_mode(project, 'production') and read_project(project)['mode'] == 'production'


def test_the_operate_tools_are_owner_only_services(project):
    runtime = endpoint_app(project)
    app = runtime.app
    status, data, _ = http(app, 'GET', '/api/_services/project_status')
    assert status == 401 and 'acme' not in json.dumps(data)
    owner = client(app)
    status, data, _ = owner('GET', '/api/_services/project_status')
    record = read_project(project)
    assert status == 200 and data['id'] == record['id'] and data['mode'] == 'development'
    assert data['mcp'] == 'http://127.0.0.1:8000/_/mcp'
    acme, = data['instances']
    assert acme['name'] == 'acme' and len(acme['id']) == 13 and acme['apps'] == {'notes': '0.1.0'} and acme['status'] == 'stopped'
    status, data, _ = owner('POST', '/api/_tools/execute', {'tool': 'list_services', 'arguments': {}})
    from evlet.cli.project_tools import TIERS
    assert {item['name'] for item in data['result']['services']} == set(TIERS) and set(TIERS.values()) == {'operate', 'develop'}
    # A destructive restore always asks first, and says what is lost.
    owner('POST', '/api/_services/backup_create', {'instance': 'acme'})
    runtime.wait()
    backups = owner('GET', '/api/_services/backup_list?instance=acme')[1]['backups']
    status, data, _ = owner('POST', '/api/_services/backup_restore', {'instance': 'acme', 'backup': backups[0]['name']})
    assert status == 422 and 'is lost' in data['fields']['confirm']
    status, data, _ = owner('POST', '/api/_services/backup_restore', {'instance': 'acme', 'backup': '../../secret.key', 'confirm': True})
    assert status == 422 and 'no backup named' in data['fields']['backup']
    status, data, _ = owner('POST', '/api/_services/backup_restore', {'instance': 'acme', 'backup': backups[0]['name'], 'confirm': True})
    assert status == 200 and data['result'] == 'started'
    runtime.wait()
    found = owner('GET', f'/api/_services/operation_status?operation={data["operation"]}')[1]
    assert found['kind'] == 'backup_restore' and found['result'] == 'ok' and found['origin'] == 'portal'
    assert any('restored from' in line for line in found['output'])
    log = owner('GET', '/api/_services/operation_log')[1]['operations']
    assert [row['kind'] for row in log] == ['backup_restore', 'backup_create']
    # In development an app may come from apps/; production takes only the sources in project.json.
    status, data, _ = owner('POST', '/api/_services/app_install', {'instance': 'acme', 'source': 'crm'})
    assert status == 422 and 'no app named crm' in data['fields']['source']
    record = read_project(project)
    (project / 'project.json').write_text(json.dumps({**record, 'mode': 'production', 'sources': ['https://git.example.com/acme/']}))
    status, data, _ = owner('POST', '/api/_services/app_install', {'instance': 'acme', 'source': 'https://github.com/someone/crm.git'})
    assert status == 422 and 'https://git.example.com/acme/' in data['fields']['source']


def test_portal_page_shows_no_ids_signed_out(project):
    runtime = endpoint_app(project)
    served = endpoint.Endpoint(runtime.app, runtime.supervisor)
    status, body, headers = http(served, 'GET', '/', headers={b'accept': b'text/html'})
    if status == 503:
        pytest.skip('The portal is built by the frontend production build')
    assert status == 200 and b"script-src 'self'" in headers[b'content-security-policy']
    assert read_project(project)['id'].encode() not in body
    assert http(served, 'GET', '/', headers={b'host': b'evil.example'})[0] in (400, 403, 404, 421)


# ---------- real processes ----------

class Endpoint:
    """`evlet endpoint` in a subprocess, with an HTTP client signed in as the owner."""

    def __init__(self, root):
        self.root, self.port, self.instance_port = root, free_port(), free_port()
        endpoint.create(root, port=self.port)
        settings = endpoint.read_settings(root)
        endpoint.save_settings(root, {**settings, 'instances': {'acme': self.instance_port}})
        endpoint.set_owner_password(root, PASSWORD)
        environment = {**os.environ, 'PYTHONPATH': os.pathsep.join(filter(None, (str(Path(__file__).resolve().parents[1]), os.environ.get('PYTHONPATH'))))}
        self.process = subprocess.Popen([sys.executable, '-m', 'evlet', '--project', str(root), 'endpoint', '--no-link'],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment, start_new_session=True)
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ''

    def request(self, method, path, body=None):
        headers = {'Content-Type': 'application/json', 'X-Evlet-Request': '1', 'X-CSRF-Token': self.csrf}
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', method=method, headers=headers,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read() or b'null')
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b'null')
        except (urllib.error.URLError, ConnectionError):
            assert self.process.poll() is None, self.stop()
            return 0, None

    def sign_in(self):
        status, data = self.request('POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
        assert status == 200, data
        self.csrf = data['csrf']

    def call(self, name, **arguments):
        if name in ('project_status', 'operation_status', 'instance_logs', 'operation_log', 'backup_list'):
            from urllib.parse import urlencode
            return self.request('GET', f'/api/_services/{name}?' + urlencode(arguments))
        return self.request('POST', f'/api/_services/{name}', arguments)

    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        return self.process.stdout.read().decode('utf-8', 'replace')


def until(check, seconds=60, every=0.25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(every)
    raise AssertionError('timed out')


def instance_health(port):
    from evlet.cli.restart import health
    return health(port, None)


def child_pid(served):
    status, data = served.call('project_status')
    acme = data['instances'][0] if status == 200 else {}
    return acme.get('status') == 'running' and acme.get('health') and acme


def test_the_supervisor_runs_restarts_and_pauses_the_instance(project):
    served = Endpoint(project)
    try:
        until(lambda: served.request('GET', '/health')[0] == 200)
        served.sign_in()
        until(lambda: instance_health(served.instance_port), seconds=90)
        acme = until(lambda: child_pid(served), seconds=30)
        assert acme['port'] == served.instance_port and acme['restarts'] == 0
        # A crash: kill the child, and the endpoint starts it again.
        pids = subprocess.run(['pgrep', '-f', f'dev acme --no-link --port {served.instance_port}'], capture_output=True, text=True).stdout.split()
        assert pids
        for pid in pids:
            os.kill(int(pid), signal.SIGKILL)
        until(lambda: not instance_health(served.instance_port), seconds=10, every=0.05)
        until(lambda: instance_health(served.instance_port), seconds=90)
        acme = until(lambda: child_pid(served), seconds=30)
        assert acme['restarts'] >= 1
        status, logs = served.call('instance_logs', instance='acme')
        assert status == 200 and any('exited with code' in line for line in logs['lines'])
        # A restore needs the instance's exclusive lock: the endpoint stops the child, restores and starts it again.
        status, started = served.call('backup_create', instance='acme')
        assert status == 200
        until(lambda: served.call('operation_status', operation=started['operation'])[1]['result'] == 'ok')
        backup = served.call('backup_list', instance='acme')[1]['backups'][0]['name']
        status, started = served.call('backup_restore', instance='acme', backup=backup, confirm=True)
        assert status == 200, started
        result = until(lambda: (found := served.call('operation_status', operation=started['operation'])[1])['result'] in ('ok', 'failed') and found)
        assert result['result'] == 'ok', result
        until(lambda: instance_health(served.instance_port), seconds=90)
        logs = served.call('instance_logs', instance='acme')[1]['lines']
        assert sum('[endpoint] starting' in line for line in logs) >= 3
        # instance_stop keeps it stopped, instance_start brings it back.
        status, started = served.call('instance_stop', instance='acme')
        until(lambda: served.call('operation_status', operation=started['operation'])[1]['result'] == 'ok')
        assert not instance_health(served.instance_port)
        time.sleep(1.5)
        assert served.call('project_status')[1]['instances'][0]['status'] == 'stopped'
        rows = oplog.operations(project)
        assert [row['origin'] for row in rows[:3]] == ['portal', 'portal', 'portal']
    finally:
        output = served.stop()
    assert not instance_health(served.instance_port), output


# ---------- app updates from Git ----------

def git(path, *arguments):
    return subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com', '-c', 'init.defaultBranch=main',
                           '-C', str(path), *arguments], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def from_git(tmp_path, environment_stub, monkeypatch):  # noqa: F811
    """A project whose notes app is a Git checkout of a bare origin, installed in acme; v2 is a tag on the origin."""
    from evlet.cli.setup import setup
    monkeypatch.setattr(core, 'GIT_PROTOCOLS', ())  # the test's origin is a local folder
    root = setup(tmp_path / 'project').resolve()
    app = create_app(root, 'notes')
    git(app, 'init', '--quiet')
    git(app, 'add', '-A')
    git(app, 'commit', '--quiet', '-m', 'v1')
    git(app, 'tag', 'v1')
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'clone', '--quiet', '--bare', str(app), str(origin)], check=True)
    git(app, 'remote', 'add', 'origin', str(origin))
    work = tmp_path / 'work'
    subprocess.run(['git', 'clone', '--quiet', str(origin), str(work)], check=True)
    readme = work / 'README.md'
    readme.write_text(readme.read_text() + '\nVersion two.\n')
    git(work, 'commit', '--quiet', '-am', 'v2')
    git(work, 'tag', 'v2')
    git(work, 'push', '--quiet', 'origin', 'HEAD:main', 'v2')
    create_instance(root, 'acme')
    core.install_app(root, 'acme', 'notes', trust=True)
    endpoint.create(root)
    yield root, git(work, 'rev-parse', 'HEAD')
    for name in list(sys.modules):
        if name == 'notes' or name.startswith('notes.'):
            del sys.modules[name]


def test_app_update_moves_an_app_to_a_tag_and_logs_it(from_git):
    root, v2 = from_git
    v1 = git(root / 'apps/notes', 'rev-parse', 'HEAD')
    plan = core.app_update_plan(root, 'acme', 'notes', 'v2', trust=True)
    assert plan.value['target'] == v2 and len(plan.value['commits']) == 1 and 'plan only' in '\n'.join(plan.messages)
    assert git(root / 'apps/notes', 'rev-parse', 'HEAD') == v1  # the plan leaves the checkout where it was
    events = []
    outcome = core.app_update(root, 'acme', 'notes', 'v2', trust=True, stop=lambda: events.append('stop'),
                              start=lambda: events.append('start'), healthy=lambda: True)
    assert outcome.ok and events == ['stop', 'start'] and outcome.backup
    assert outcome.after['apps']['notes']['commit'] == v2 and outcome.before['apps']['notes']['commit'] == v1
    assert [step for step in outcome.steps if step in ('plan', 'stop', 'migrate', 'start', 'health')] == ['plan', 'stop', 'migrate', 'start', 'health']
    kinds = [row['kind'] for row in oplog.operations(root)]
    assert kinds[:2] == ['app_update', 'app_update_plan'] and 'migrate' not in kinds  # one row pair for the whole update
    assert core.app_update(root, 'acme', 'notes', 'v2', trust=True).value['current'] == v2


def test_a_failed_app_update_brings_back_the_old_commit_and_the_backup(from_git):
    root, v2 = from_git
    v1 = git(root / 'apps/notes', 'rev-parse', 'HEAD')
    events = []
    outcome = core.app_update(root, 'acme', 'notes', 'v2', trust=True, stop=lambda: events.append('stop'),
                              start=lambda: events.append('start'), healthy=lambda: False)
    assert not outcome.ok and outcome.value['restored'] and events == ['stop', 'start', 'stop', 'start']
    assert git(root / 'apps/notes', 'rev-parse', 'HEAD') == v1
    assert outcome.after['apps']['notes']['commit'] == v1
    assert any('did not answer /health' in line for line in outcome.messages)
    assert oplog.operations(root)[0]['result'] == 'failed'


def test_app_update_refuses_what_it_cannot_update(from_git):
    root, _ = from_git
    with pytest.raises(ValueError, match='Name a tag'):
        core.app_update(root, 'acme', 'notes', '--upload-pack=x')
    (root / 'apps/notes/README.md').write_text('changed here')
    with pytest.raises(ValueError, match='local changes'):
        core.app_update(root, 'acme', 'notes', 'v2')


# ---------- evlet setup --endpoint ----------

@pytest.mark.parametrize('arguments, message', [
    (['--endpoint-host', 'project.example.com'], 'only works with --endpoint'),
    (['--endpoint', '--dev', '--instance', 'acme', '--app', 'notes'], 'leave out --dev and --host'),
    (['--endpoint', '--endpoint-host', 'http://project.example.com'], 'public lowercase hostname'),
    (['--endpoint', '--endpoint-host', 'project.localhost'], 'public lowercase hostname'),
])
def test_setup_refuses_unsafe_endpoint_options(tmp_path, arguments, message, capsys):
    with pytest.raises(SystemExit):
        main(['setup', str(tmp_path / 'new'), *arguments])
    assert message in capsys.readouterr().err and not (tmp_path / 'new').exists()


def test_setup_endpoint_host_prepares_the_one_unit_and_the_caddy_site(project, monkeypatch, capsys):
    from evlet.cli import site
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        monkeypatch.setattr(os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    main(['--project', str(project), 'setup', '--endpoint', '--endpoint-host', 'project.example.com', '--service-user', 'evlet'])
    out = capsys.readouterr().out
    assert 'Endpoint created' in out and 'Password set for the owner' in out and 'evlet project mode production' in out
    unit = (project / 'deploy/evlet-project.service').read_text()
    assert f'-m evlet endpoint --project {project}' in unit and 'User=evlet' in unit
    assert (project / 'deploy/evlet-project.caddy').read_text().startswith('project.example.com {')
    assert 'sudo systemctl enable --now evlet-project' in out
    assert endpoint.owner_has_password(project) and endpoint.read_settings(project)['host'] == 'project.example.com'
    assert endpoint.url(project) == 'https://project.example.com'
    # Run again: the steps are kept, project.db too.
    main(['--project', str(project), 'setup', '--endpoint', '--endpoint-host', 'project.example.com', '--service-user', 'evlet'])
    assert 'Endpoint exists' in capsys.readouterr().out


def test_mcp_clients_reach_the_operate_tools_as_the_owner(project):
    from test_assistants import approve, connect, tool
    runtime = endpoint_app(project)
    app = runtime.app
    _, tokens, owner = connect(app, role='admin')
    token = tokens['access_token']
    listed = tool(app, token, 'list_services', {})
    assert 'project_status' in json.dumps(listed) and 'backup_restore' in json.dumps(listed)
    status = json.loads(tool(app, token, 'call_service', {'service': 'project_status', 'arguments': {}})['content'][0]['text'])
    assert status['id'] == read_project(project)['id'] and status['instances'][0]['name'] == 'acme'
    # A tool that changes something waits for the owner's approval, then runs as an operation from mcp.
    waiting = tool(app, token, 'call_service', {'service': 'backup_create', 'arguments': {'instance': 'acme'}})
    assert waiting.get('isError') and 'approv' in json.dumps(waiting).lower()
    approve(owner)
    started = json.loads(tool(app, token, 'call_service', {'service': 'backup_create', 'arguments': {'instance': 'acme'}})['content'][0]['text'])
    runtime.wait()
    found = oplog.operation(project, started['operation'])
    assert found['origin'] == 'mcp' and found['result'] == 'ok' and found['kind'] == 'backup_create'
