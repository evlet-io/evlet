"""The project MCP (docs/project-endpoint.md#tiers): the operate and develop tiers, the mode rule, approvals, the
project files the develop tools reach, tests_run, and the management channel with its route walk.

Real SQLite throughout; tests_run runs pytest as a real process, and the last test runs `evlet endpoint` with the
instance as a real child process and reaches it through the management channel.
"""

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evlet.cli import core, endpoint, oplog
from evlet.cli.project_tools import ALWAYS_ASK, PRODUCTION_REFUSAL, TIERS
from evlet.instances import load_runtime, read_project
from support import client, http
from test_assistants import approve, connect, tool
from test_cli_lifecycle import environment_stub  # noqa: F401  (fixture)
from test_project_endpoint import Endpoint, endpoint_app, project, until  # noqa: F401  (fixtures)

DEVELOP = {'files_list', 'file_read', 'file_write', 'file_delete', 'app_create', 'app_fetch', 'makemigration', 'migrate',
           'build', 'tests_run', 'instance_create', 'instance_delete', 'controls_override_set', 'settings_set',
           'site_files_write', 'site_publish', 'site_rollback'}


def set_mode(root, mode, **extra):
    """Write project.json's mode as `evlet project mode` does once its checks pass (they are tested in
    test_project_endpoint.py), so these tests need no public host or two-step sign-in."""
    record = read_project(root)
    (root / 'project.json').write_text(json.dumps({**record, 'mode': mode, **extra}))


def services(app, token):
    listed = tool(app, token, 'list_services', {})
    return {item['name'] for item in listed['structuredContent']['services']}


def result(answer):
    return json.loads(answer['content'][0]['text'])


def test_each_tool_has_a_tier_and_there_is_no_shell_or_mode_tool():
    from evlet.cli.project_tools import endpoints
    endpoints(SimpleNamespace(root=Path(".")))  # the tools are made, with their tiers, for an endpoint
    assert {name for name, tier in TIERS.items() if tier == 'develop'} == DEVELOP
    assert {'host_add', 'host_remove', 'host_check', 'instance_restart', 'app_update', 'app_install'} <= \
        {name for name, tier in TIERS.items() if tier == 'operate'}
    assert not [name for name in TIERS if re.search(r'mode|shell|exec|command|run_|records?$', name)]


def test_the_develop_tier_follows_project_json_on_every_call(project):
    runtime = endpoint_app(project)
    app = runtime.app
    _, tokens, owner = connect(app, role='admin')
    token = tokens['access_token']
    assert DEVELOP <= services(app, token)
    read = result(tool(app, token, 'call_service', {'service': 'file_read', 'arguments': {'path': 'apps/notes/pyproject.toml'}}))
    assert 'name = "notes"' in read['text']
    assert set(owner('GET', '/api/_services/project_status')[1]['tools']) == set(TIERS)
    # The shell switches the mode; the next call follows it. The client has no say.
    set_mode(project, 'production')
    listed = services(app, token)
    assert not DEVELOP & listed and {'project_status', 'app_update', 'host_add'} <= listed
    refused = tool(app, token, 'call_service', {'service': 'file_read', 'arguments': {'path': 'apps/notes/pyproject.toml'}})
    assert refused['isError'] and PRODUCTION_REFUSAL in json.dumps(refused) and '403' in json.dumps(refused)
    status, data, _ = owner('GET', '/api/_services/file_read?path=apps/notes/pyproject.toml')
    assert status == 403 and data['error'] == PRODUCTION_REFUSAL
    status, data, _ = owner('POST', '/api/_services/file_write', {'path': 'apps/notes/x.py', 'text': ''})
    assert status == 403 and not (project / 'apps/notes/x.py').exists()
    assert not DEVELOP & set(owner('GET', '/api/_services/project_status')[1]['tools'])
    # Back in development, the develop tools are back.
    set_mode(project, 'development')
    assert DEVELOP <= services(app, token)


def pending(owner):
    return [item for item in owner('GET', '/api/_tools/approvals')[1]['items'] if item['status'] == 'pending']


def test_production_always_asks_for_the_locked_tools_and_never_ask_for_the_others(project):
    runtime = endpoint_app(project)
    app = runtime.app
    _, tokens, owner = connect(app, role='admin')
    token = tokens['access_token']
    settings = endpoint.read_settings(project)
    endpoint.save_settings(project, {**settings, 'never_ask': ['backup_create', 'app_update', 'instance_restart', 'file_write']})
    # Development: never_ask is honoured, except for the tools that always ask.
    started = result(tool(app, token, 'call_service', {'service': 'file_write', 'arguments': {'path': 'apps/notes/extra.py', 'text': 'X = 1\n'}}))
    assert started['result'] == 'ok' and (project / 'apps/notes/extra.py').read_text() == 'X = 1\n' and not pending(owner)
    asked = tool(app, token, 'call_service', {'service': 'instance_delete', 'arguments': {'instance': 'acme'}})
    assert asked['isError'] and pending(owner)[0]['summary']['warning'].startswith('Deleting acme')
    approve(owner, False)
    assert (project / 'instances/acme/config.py').is_file()
    set_mode(project, 'production')
    # Production: never_ask still holds for backup_create...
    started = result(tool(app, token, 'call_service', {'service': 'backup_create', 'arguments': {'instance': 'acme'}}))
    runtime.wait()
    assert started['result'] == 'started' and not pending(owner)
    # ...but app_update always asks, though endpoint.json says never.
    asked = tool(app, token, 'call_service', {'service': 'app_update', 'arguments': {'instance': 'acme', 'app': 'notes', 'ref': 'v2'}})
    assert asked['isError'] and 'approv' in json.dumps(asked).lower()
    waiting, = pending(owner)
    assert waiting['tool'] == 'call_service' and waiting['summary']['service'] == 'app_update' and 'always asks' in waiting['summary']['warning']
    approve(owner)
    ran = result(tool(app, token, 'call_service', {'service': 'app_update', 'arguments': {'instance': 'acme', 'app': 'notes', 'ref': 'v2'}}))
    runtime.wait()
    assert oplog.operation(project, ran['operation'])['result'] == 'failed'  # notes has no Git source; the approval was used
    # backup_restore asks with what is lost.
    backup = owner('GET', '/api/_services/backup_list?instance=acme')[1]['backups'][0]['name']
    asked = tool(app, token, 'call_service', {'service': 'backup_restore', 'arguments': {'instance': 'acme', 'backup': backup, 'confirm': True}})
    assert asked['isError'] and 'is lost' in pending(owner)[0]['summary']['warning']
    assert ALWAYS_ASK == {'app_install', 'app_update', 'evlet_upgrade', 'backup_restore', 'instance_delete'}


@pytest.mark.parametrize('path', [
    '.venv/bin/python', '.evlet/project.sqlite3', '.evlet/control/acme.token', 'instances/acme/data/database.sqlite3',
    'instances/acme/secret.key', 'instances/acme/control.key', 'instances/acme/apps.lock', 'project.json', '../outside.py',
    '/etc/passwd', 'apps/../project.json', 'apps/notes/.git/config', 'apps/notes/.env', 'apps\\notes\\x.py', 'apps/notes//x.py',
    'instances/other/config.py', 'apps/linked/x.py',
])
def test_the_file_tools_reach_only_apps_and_the_instance_files(project, path):
    runtime = endpoint_app(project)
    owner = client(runtime.app)
    (project / 'apps/linked').symlink_to(project / 'instances')
    status, data, _ = owner('GET', '/api/_services/file_read?path=' + path)
    assert status == 422, data
    status, data, _ = owner('POST', '/api/_services/file_write', {'path': path, 'text': 'x'})
    assert status == 422, data
    assert oplog.operations(project) == []  # a refused path writes nothing, not even a log row


def test_the_file_tools_write_read_list_and_delete(project):
    runtime = endpoint_app(project)
    owner = client(runtime.app)
    status, data, _ = owner('POST', '/api/_services/file_write', {'path': 'apps/notes/tests/test_more.py', 'text': 'def test_more():\n    assert True\n'})
    assert status == 200 and data['result'] == 'ok' and data['bytes'] == 33
    assert owner('GET', '/api/_services/file_read?path=apps/notes/tests/test_more.py')[1]['text'].startswith('def test_more')
    listed = owner('GET', '/api/_services/files_list?path=apps/notes')[1]
    assert 'apps/notes/tests/test_more.py' in listed['files'] and not [item for item in listed['files'] if '/.' in item]
    assert owner('GET', '/api/_services/files_list?path=instances')[1]['files'] == ['instances/acme/config.py']
    config = owner('GET', '/api/_services/file_read?path=instances/acme/config.py')[1]['text']
    assert 'name' in config
    status, data, _ = owner('POST', '/api/_services/file_write', {'path': 'instances/acme/routes.py', 'text': 'routes = {}\n'})
    assert status == 200 and (project / 'instances/acme/routes.py').read_text() == 'routes = {}\n'
    status, data, _ = owner('POST', '/api/_services/file_delete', {'path': 'instances/acme/config.py'})
    assert data['result'] == 'failed' and 'keeps its config.py' in data['error']
    assert owner('POST', '/api/_services/file_delete', {'path': 'apps/notes/tests/test_more.py'})[1]['result'] == 'ok'
    kinds = [(row['kind'], row['result'], row['origin']) for row in oplog.operations(project)]
    assert kinds == [('file_delete', 'ok', 'portal'), ('file_delete', 'failed', 'portal'), ('file_write', 'ok', 'portal'), ('file_write', 'ok', 'portal')]


def test_tests_run_runs_pytest_in_the_project_environment_with_a_time_limit(project):
    python = project / '.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    python.chmod(0o755)
    tests = project / 'apps/notes/tests'
    tests.mkdir(exist_ok=True)
    (tests / 'test_project.py').write_text('def test_passes():\n    assert 1 + 1 == 2\n')
    outcome = core.tests_run(project, 'notes/tests/test_project.py')
    assert outcome.ok and outcome.value == 0 and any('1 passed' in line for line in outcome.messages)
    (tests / 'test_project.py').write_text('def test_fails():\n    assert 1 + 1 == 3\n')
    outcome = core.tests_run(project, 'notes/tests/test_project.py')
    assert not outcome.ok and outcome.value == 1 and any('1 failed' in line for line in outcome.messages)
    (tests / 'test_project.py').write_text('import time\n\ndef test_sleeps():\n    time.sleep(30)\n')
    outcome = core.tests_run(project, 'notes/tests/test_project.py', seconds=2)
    assert not outcome.ok and outcome.value is None and 'was stopped' in outcome.messages[-1]
    with pytest.raises(ValueError, match='relative path'):
        core.tests_run(project, '../..')


def test_instance_create_and_instance_delete(project):
    runtime = endpoint_app(project)
    owner = client(runtime.app)
    started = owner('POST', '/api/_services/instance_create', {'name': 'beta'})[1]
    runtime.wait()
    assert oplog.operation(project, started['operation'])['result'] == 'ok' and 'beta' in runtime.supervisor.children
    assert 'beta' in endpoint.read_settings(project)['instances']
    started = owner('POST', '/api/_services/instance_delete', {'instance': 'beta'})[1]
    runtime.wait()
    assert oplog.operation(project, started['operation'])['result'] == 'ok'
    assert not (project / 'instances/beta').exists() and [path.name[:5] for path in (project / '.evlet/deleted').iterdir()] == ['beta-']
    assert 'beta' not in runtime.supervisor.children and 'beta' not in endpoint.read_settings(project)['instances']


# ---------- the management channel ----------

def instance_app(root):
    app = load_runtime(root, 'acme')
    app.initialize()
    return app


def literal_routes():
    """Every route path written in Evlet's source, as the route walk's list: /api/..., /_/..., /.well-known/..."""
    source = Path(__file__).resolve().parents[1] / 'evlet'
    found = set()
    for path in source.rglob('*.py'):
        found |= set(re.findall(r"""['"](/(?:api|_|\.well-known)/[A-Za-z0-9_./-]*)['"]""", path.read_text(encoding='utf-8')))
    return sorted(found | {'/', '/app', '/controls', '/settings', '/docs', '/health', '/api', '/api/notesitem', '/api/notesitem/1'})


def test_the_management_token_authenticates_only_the_management_routes(project):
    from evlet.cli import channel
    from evlet.manage import ROUTES
    token = channel.token(project, 'acme')
    assert oct((project / 'instances/acme/control.key').stat().st_mode & 0o777) == '0o600'
    assert token not in (project / 'instances/acme/control.key').read_text()  # the instance keeps the hash only
    app = instance_app(project)
    bearer = {b'authorization': f'Bearer {token}'.encode()}
    managed = {'/_/manage/' + route for _, route in ROUTES}
    walked = 0
    for path in literal_routes():
        if path.startswith('/_/manage/'):
            continue
        for method in ('GET', 'POST'):
            plain = http(app, method, path)
            # The token reads as a wrong API key: forget those failures, so no 429 hides what the route answers.
            app.business.auth._key_counts.clear()
            sent = http(app, method, path, headers=bearer)
            # The token opens nothing: a route either refuses it or answers exactly as it does without it.
            assert sent[0] in (401, 404, 405, 415) or sent[0] == plain[0] and sent[1] == plain[1], (method, path, plain[0], sent[0])
            walked += 1
    assert walked > 150
    app.business.auth._key_counts.clear()
    for method, route in ROUTES:
        path = '/_/manage/' + route
        assert http(app, method, path)[0] == 404, path  # no token: as if it were not there
        assert http(app, method, path, headers={b'authorization': b'Bearer ' + b'x' * 43})[0] == 404, path
        assert http(app, method, path, headers=bearer, client='10.0.0.7')[0] == 404, path
        assert http(app, method, path, headers={**bearer, b'x-forwarded-for': b'203.0.113.9'})[0] == 404, path
        assert http(app, method, path, headers=bearer)[0] != 404 or route.startswith('sites/') and method == 'GET', path
    assert managed and http(app, 'GET', '/_/manage/other', headers=bearer)[0] == 404
    # The token is no session or key: /api, /_/mcp and Controls do not take it.
    assert http(app, 'GET', '/api/notesitem', headers=bearer)[0] == 401
    assert http(app, 'POST', '/_/mcp', headers=bearer)[0] == 401
    assert http(app, 'GET', '/api/_controls/overrides', headers=bearer)[0] == 401


def test_management_reads_and_writes_are_audited_as_project_and_production_refuses_the_development_writes(project):
    from evlet.cli import channel
    token = channel.token(project, 'acme')
    app = instance_app(project)
    bearer = {b'authorization': f'Bearer {token}'.encode()}
    status, data, _ = http(app, 'GET', '/_/manage/id', headers=bearer)
    assert status == 200 and data['project'] == read_project(project)['id'] and len(data['id']) == 13
    assert http(app, 'GET', '/_/manage/lock', headers=bearer)[1]['apps']['notes']['version'] == '0.1.0'
    assert http(app, 'GET', '/_/manage/version', headers=bearer)[1]['schema']
    change = {'changes': [{'kind': 'setting', 'key': 'mcp.allow_writes', 'value': True}]}
    status, data, _ = http(app, 'POST', '/_/manage/overrides', change, headers=bearer)
    assert status == 200, data
    status, data, _ = http(app, 'POST', '/_/manage/hosts/add', {'host': 'shop.example.com', 'target': 'notes.web'}, headers=bearer)
    assert status in (200, 422), data
    set_mode(project, 'production')
    status, data, _ = http(app, 'POST', '/_/manage/overrides', change, headers=bearer)
    assert status == 403 and data['error'] == PRODUCTION_REFUSAL
    assert http(app, 'POST', '/_/manage/sites/publish', {'site': 'www'}, headers=bearer)[0] == 403
    assert http(app, 'GET', '/_/manage/overrides', headers=bearer)[0] == 200  # reads stay
    from evlet import qb
    with app.database._connection() as connection:
        actors = {row['actor'] for row in connection.run(qb.select('evlet_audit', 'actor'))}
    assert 'project' in actors
    # Never records: there is no route for them.
    assert http(app, 'GET', '/_/manage/records', headers=bearer)[0] == 404


def test_the_endpoint_reaches_a_running_instance_only_through_the_management_channel(project):
    served = Endpoint(project)
    try:
        until(lambda: served.request('GET', '/health')[0] == 200)
        served.sign_in()
        from test_project_endpoint import instance_health
        until(lambda: instance_health(served.instance_port), seconds=90)
        until(lambda: served.call('project_status')[1]['instances'][0]['status'] == 'running', seconds=30)
        status, data = served.call('controls_override_set', instance='acme', kind='setting', key='mcp.allow_writes', value='true')
        assert status == 200 and data['result'] == 'ok', data
        status, data = served.call('host_check', instance='acme')
        assert status == 200 and data['result'] == 'ok' and data['hosts'] == [], data
        app = instance_app(project)
        from evlet import qb
        with app.database._connection() as connection:
            row = connection.run(qb.select('evlet_audit', 'actor', 'target').where(actor='project')).one()
        assert row['target'] == 'mcp.allow_writes'
        rows = oplog.operations(project)
        assert [(row['kind'], row['result']) for row in rows[:2]] == [('host_check', 'ok'), ('controls_override_set', 'ok')]
        # evlet restart goes through the endpoint while it runs: no systemd, logged from the CLI.
        import subprocess
        environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        restarted = subprocess.run([sys.executable, '-m', 'evlet', '--project', str(project), 'restart', 'acme'],
                                   capture_output=True, text=True, env=environment, timeout=120)
        assert restarted.returncode == 0 and 'The project endpoint runs operation' in restarted.stdout, restarted.stdout + restarted.stderr
        assert oplog.operations(project)[0]['kind'] == 'instance_restart' and oplog.operations(project)[0]['origin'] == 'cli'
    finally:
        served.stop()
