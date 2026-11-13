"""evlet restart: read-only checks on a real project and SQLite instance, then a fake sudo systemctl restart."""

import json
import subprocess
import sys

import pytest

from evlet import framework
from evlet.cli import proxy, restart as restarting, site
from evlet.cli.install import install
from evlet.cli.instance import set_domain
from evlet.cli.main import main
from evlet.instances import read_lock
from test_cli_lifecycle import change_model, environment_stub, project  # noqa: F401
from test_framework_migrations import MOVE


class Server:
    """The fake systemd side: records every command and answers /health when told to."""

    def __init__(self, answers=True, journal='Nov 13 10:00:01 evlet[42]: ValueError: something broke\n'):
        self.ran, self.health, self.answers, self.journal = [], [], answers, journal

    def run(self, args, **values):
        self.ran.append(args)
        if args[0] == 'journalctl':
            return subprocess.CompletedProcess(args, 0, stdout=self.journal, stderr='')
        return subprocess.CompletedProcess(args, 0)

    def fetch(self, port, host):
        self.health.append((port, host))
        return self.answers


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def served(project, tmp_path):
    """An installed instance with the unit evlet setup --host --apply writes, on port 8010."""
    install(project, 'acme', app_name='notes', trust=True)
    units = tmp_path / 'systemd'
    units.mkdir()
    (units / 'evlet-acme.service').write_text(proxy.systemd_unit(project, 'acme', 8010, 'evlet'))
    return project, units


def call(root, units, server=None, lines=None, **values):
    server = server or Server()
    clock = Clock()
    lines = [] if lines is None else lines
    values = {'yes': True, **values}
    result = restarting.restart(root, 'acme', unit_dir=units, run=server.run, which=lambda name: '/usr/bin/' + name,
                                fetch=server.fetch, sleep=clock.sleep, clock=clock, out=lines.append, **values)
    return result, lines, server


def test_all_checks_pass_then_it_restarts_and_waits_for_health(served):
    root, units = served
    result, lines, server = call(root, units)
    assert result is True
    assert lines[:5] == ['Instance: ok', 'Apps: ok (notes 0.1.0)', 'Routes: ok (no domain)', 'Migrations: ok (none pending)',
                         f'Service: ok ({units}/evlet-acme.service, port 8010)']
    assert lines[5:] == ['$ sudo systemctl restart evlet-acme', 'acme is back up on 127.0.0.1:8010']
    assert server.ran == [['sudo', 'systemctl', 'restart', 'evlet-acme']]
    assert server.health == [(8010, None)]


def test_a_domain_is_sent_as_host_and_named_in_the_result(served):
    root, units = served
    set_domain(root, 'acme', 'erp.example.com')
    result, lines, server = call(root, units)
    assert result is True and 'Routes: ok (primary host erp.example.com)' in lines
    assert lines[-1] == 'acme is back up at https://erp.example.com'
    assert server.health == [(8010, 'erp.example.com')]


def test_pending_framework_migrations_refuse_and_restart_nothing(served, monkeypatch):
    root, units = served
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, MOVE))
    result, lines, server = call(root, units)
    assert result is False and server.ran == [] and server.health == []
    assert 'Migrations: pending: 0099_move_calls. Run evlet migrate acme first' in lines
    assert lines[-1] == 'Checks failed. evlet-acme was not restarted and keeps running as it was.'


def test_pending_app_migrations_refuse(served, monkeypatch):
    root, units = served
    from evlet.migrations import MigrationPlan

    class Waiting:
        name = 'app5_notes__0002_done'
    monkeypatch.setattr('evlet.database.Database.plan', lambda self, migrations: MigrationPlan('upgrade', (Waiting,), (), ''))
    result, lines, server = call(root, units)
    assert result is False and server.ran == []
    assert 'Migrations: pending: app5_notes__0002_done. Run evlet migrate acme first' in lines


def test_changed_app_source_refuses_until_migrated(served):
    root, units = served
    change_model(root)
    result, lines, server = call(root, units)
    assert result is False and server.ran == []
    assert 'Apps: notes differs from apps.lock. Run evlet migrate acme first' in lines
    assert 'Migrations: skipped until the apps load' in lines and 'Routes: skipped until the apps load' in lines
    # Nothing was migrated on its own.
    assert read_lock(root / 'instances/acme')['apps']['notes'] == json.loads((root / 'instances/acme/apps.lock').read_text())['apps']['notes']
    install(root, 'acme', trust=True)
    assert call(root, units)[0] is True


@pytest.mark.parametrize('breakage, label, message', [
    (lambda path: (path / 'config.py').write_text('raise ValueError("config.py is broken")\n'), 'Instance', 'config.py is broken'),
    (lambda path: (path / 'routes.py').write_text('raise ValueError("routes.py is broken")\n'), 'Instance', 'routes.py is broken'),
    (lambda path: (path / 'routes.py').write_text('unknown = 1\n'), 'Routes', "unknown name 'unknown'"),
    (lambda path: (path / 'apps.lock').write_text('{"apps": {}}'), 'Apps', 'differs from apps.lock'),
    (lambda path: (path / '.install-journal.json').write_text('{}'), 'Apps', 'Interrupted installation'),
])
def test_each_failing_check_leaves_the_service_alone(served, breakage, label, message):
    root, units = served
    breakage(root / 'instances/acme')
    result, lines, server = call(root, units)
    assert result is False and server.ran == [] and server.health == []
    line = next(item for item in lines if item.startswith(label + ':'))
    assert line != f'{label}: ok' and message in line
    if label == 'Instance':
        assert 'Apps: skipped until the instance loads' in lines


def test_an_evlet_upgrade_without_migrate_refuses(served, monkeypatch):
    root, units = served
    monkeypatch.setattr(framework, 'version', lambda: '9.9.9')
    result, lines, server = call(root, units)
    assert result is False and server.ran == []
    assert any(line.startswith('Migrations: Evlet 9.9.9 is installed but acme was migrated with Evlet ')
               and line.endswith('Run evlet migrate acme first') for line in lines)


def test_broken_app_import_is_reported(served):
    root, units = served
    models = root / 'apps/notes/notes/models/__init__.py'
    models.write_text('raise ImportError("notes cannot import")\n')
    result, lines, server = call(root, units)
    assert result is False and server.ran == []
    assert any(line.startswith('Apps: ') and line != 'Apps: ok' for line in lines)


def test_a_missing_unit_says_how_to_restart_by_hand(served):
    root, units = served
    (units / 'evlet-acme.service').unlink()
    result, lines, server = call(root, units)
    assert result is False and server.ran == []
    service = next(line for line in lines if line.startswith('Service:'))
    assert f'{units}/evlet-acme.service is not installed' in service
    assert 'evlet serve acme' in service and 'evlet dev reloads by itself' in service


def test_no_systemd_says_how_to_restart_by_hand(served):
    root, units = served
    lines = []
    result = restarting.restart(root, 'acme', unit_dir=units, which=lambda name: None, yes=True, out=lines.append,
                                run=lambda *args, **values: pytest.fail('ran a command'))
    assert result is False
    assert 'Service: this machine has no systemd. Restart it by hand instead' in '\n'.join(lines)


@pytest.mark.parametrize('text, message', [
    ('[Service]\nExecStart=/usr/bin/gunicorn app:app\n', 'not an Evlet unit'),
    ('[Service]\nExecStart=/srv/.venv/bin/python -m evlet serve other --port 8020\n', 'serves instance other, not acme'),
    ('[Service]\nExecStart=/srv/.venv/bin/python -m evlet serve acme --project /elsewhere --port 8020\n', 'serves the project in /elsewhere'),
])
def test_a_unit_that_is_not_this_instance_is_refused(served, text, message):
    root, units = served
    (units / 'evlet-acme.service').write_text(text)
    result, lines, server = call(root, units)
    assert result is False and server.ran == []
    assert message in next(line for line in lines if line.startswith('Service:'))


def test_the_port_comes_from_the_unit(served):
    root, units = served
    assert restarting.unit_options(proxy.systemd_unit(root, 'acme', 8010, 'evlet')) == ('acme', str(root), 8010)
    assert restarting.unit_options('[Service]\nExecStart=/x/python -m evlet serve demo --jobs\n') == ('demo', None, 8000)
    assert restarting.unit_options('ExecStart=-/x/evlet serve demo --port=9001\n') == ('demo', None, 9001)
    assert restarting.unit_options('ExecStart=/x/python -m evlet serve demo --port many\n') is None
    (units / 'evlet-acme.service').write_text(proxy.systemd_unit(root, 'acme', 8123, 'evlet'))
    result, lines, server = call(root, units)
    assert result is True and server.health == [(8123, None)] and lines[-1] == 'acme is back up on 127.0.0.1:8123'


def test_service_option_picks_another_unit(served):
    root, units = served
    (units / 'evlet-acme.service').rename(units / 'erp.service')
    result, lines, server = call(root, units, service='erp.service')
    assert result is True and server.ran == [['sudo', 'systemctl', 'restart', 'erp']]
    with pytest.raises(ValueError, match='--service takes'):
        call(root, units, service='erp; rm -rf /')


def test_health_timeout_prints_the_journal(served):
    root, units = served
    result, lines, server = call(root, units, server=Server(answers=False), timeout=3)
    assert result is False
    assert server.ran == [['sudo', 'systemctl', 'restart', 'evlet-acme'],
                          ['journalctl', '-u', 'evlet-acme', '-n', '20', '--no-pager']]
    assert len(server.health) == 7  # every half second for 3 seconds, then once more at the deadline
    text = '\n'.join(lines)
    assert 'evlet-acme did not answer http://127.0.0.1:8010/health within 3 seconds.' in text
    assert 'Last 20 lines of its journal:' in text and 'ValueError: something broke' in text
    assert 'is back up' not in text


def test_a_failed_systemctl_prints_the_journal(served):
    root, units = served
    server = Server()

    def run(args, **values):
        if args[0] == 'sudo':
            server.ran.append(args)
            raise subprocess.CalledProcessError(1, args)
        return server.run(args, **values)
    lines = []
    result = restarting.restart(root, 'acme', unit_dir=units, run=run, which=lambda name: '/usr/bin/' + name, yes=True,
                                fetch=server.fetch, out=lines.append)
    assert result is False and server.health == []
    assert any('sudo systemctl restart evlet-acme failed' in line for line in lines) and 'ValueError: something broke' in lines[-1]


def test_check_runs_the_checks_only(served):
    root, units = served
    result, lines, server = call(root, units, check_only=True, yes=False)
    assert result is True and server.ran == [] and server.health == []
    assert lines[-1] == 'All checks passed. Run evlet restart acme to restart evlet-acme.'


def test_without_a_terminal_it_needs_yes(served):
    root, units = served
    result, lines, server = call(root, units, yes=False, interactive=False)
    assert result is False and server.ran == []
    assert lines[-1] == 'No terminal to confirm the restart. Run evlet restart acme --yes to restart without asking.'


def test_it_asks_in_a_terminal(served):
    root, units = served
    asked = []
    result, lines, server = call(root, units, yes=False, interactive=True, ask=lambda text: asked.append(text) or 'n')
    assert result is False and server.ran == [] and asked == ['Restart evlet-acme now? [y/N] ']
    assert lines[-1] == 'Nothing was restarted.'
    result, lines, server = call(root, units, yes=False, interactive=True, ask=lambda text: 'y')
    assert result is True and server.ran == [['sudo', 'systemctl', 'restart', 'evlet-acme']]


def test_command_line(served, monkeypatch, capsys):
    root, units = served
    monkeypatch.setattr(site, 'SYSTEMD_DIR', str(units))
    monkeypatch.setattr('shutil.which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
    main(['restart', 'acme', '--check', '--project', str(root)])
    out = capsys.readouterr().out
    assert 'Migrations: ok (none pending)' in out and 'All checks passed' in out
    with pytest.raises(SystemExit) as stopped:
        main(['restart', 'acme', '--project', str(root)])
    assert stopped.value.code == 1
    assert 'Run evlet restart acme --yes' in capsys.readouterr().out
    monkeypatch.setattr(framework, 'STEPS', (*framework.STEPS, MOVE))
    with pytest.raises(SystemExit):
        main(['restart', 'acme', '--yes', '--project', str(root)])
    assert 'Run evlet migrate acme first' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(['restart', 'acme', '--timeout', '0', '--project', str(root)])
    assert '--timeout between 1 and 3600' in capsys.readouterr().err


def test_a_running_migrate_stops_the_checks(served):
    root, units = served
    from evlet.cli.project import instance_guard
    with instance_guard(root / 'instances/acme'):
        result, lines, server = call(root, units)
    assert result is False and server.ran == []
    assert lines[0] == 'Instance: another command, such as evlet migrate acme, is changing it. Wait for it to finish, then try again'


def test_health_asks_loopback_with_the_host_header():
    import http.server
    import threading
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers['Host']))
            body = b'{"status": "ok"}' if self.headers['Host'] == 'erp.example.com' else b'{}'
            self.send_response(200 if body != b'{}' else 404)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        assert restarting.health(port, 'erp.example.com') is True
        assert restarting.health(port, None) is False
    finally:
        server.shutdown()
        server.server_close()
    assert seen == [('/health', 'erp.example.com'), ('/health', f'127.0.0.1:{port}')]
    assert restarting.health(port, None) is False  # nothing listens any more
