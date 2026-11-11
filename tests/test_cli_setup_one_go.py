"""evlet setup in one go, with real projects, apps, instances and SQLite databases."""

from argparse import Namespace
import functools
import os
import sqlite3
import subprocess
import sys

import pytest

from evlet.cli import proxy, site
from evlet.cli.app import create_app
from evlet.cli.main import main
from evlet.cli.setup import setup
from evlet.instances import load_runtime, read_instance, read_lock

PASSWORD = 'correct horse battery staple 42'


@pytest.fixture(autouse=True)
def environment_stub(monkeypatch):
    import importlib
    module = importlib.import_module('evlet.cli.setup')
    monkeypatch.setattr(module, 'create_environment', lambda root, framework: (root / '.venv').mkdir())
    yield
    for name in list(sys.modules):
        if name.split('.')[0] in ('notes', 'crm', 'tasks'):
            del sys.modules[name]


def options(**values):
    defaults = dict(app=None, get=None, instance=None, trust=False, dev=False, host=None, port=8000, ip=None, apply=False)
    return Namespace(**{**defaults, **values})


def users(root, name='acme'):
    with sqlite3.connect(root / 'instances' / name / 'data/database.sqlite3') as connection:
        return connection.execute('SELECT username, role FROM evlet_users').fetchall()


def test_comma_lists_keep_order_and_drop_repeats():
    assert site.split_list(['notes,crm', ' tasks , notes']) == ['notes', 'crm', 'tasks']
    assert site.split_list(None) == []
    with pytest.raises(ValueError, match='empty item'):
        site.split_list(['notes,,crm'])
    with pytest.raises(ValueError, match='empty item'):
        site.split_list(['notes,'])


def test_one_go_creates_apps_instance_and_installs_then_resumes(tmp_path, capsys):
    target = tmp_path / 'myproject'
    main(['setup', str(target), '--app', 'notes,crm', '--app', 'tasks', '--instance', 'acme', '--trust'])
    out = capsys.readouterr().out
    root = target.resolve()
    assert 'Project created' in out and 'Instance created' in out
    assert 'installed crm, notes, tasks' in out
    assert 'evlet dev --instance acme' in out
    assert sorted(read_lock(root / 'instances/acme')['apps']) == ['crm', 'notes', 'tasks']
    load_runtime(root, 'acme').database.close()
    # The same command again skips every finished step.
    main(['setup', str(target), '--app', 'notes,crm', '--app', 'tasks', '--instance', 'acme', '--trust'])
    out = capsys.readouterr().out
    assert 'Project exists' in out and 'App exists: notes' in out and 'Instance exists: acme' in out
    assert 'already installed' in out


def test_one_go_asks_before_running_app_code(tmp_path, capsys, monkeypatch):
    command = sys.modules['evlet.cli.main']
    asked = []
    monkeypatch.setattr(command, 'confirm', lambda message: asked.append(message) or False)
    target = tmp_path / 'p'
    with pytest.raises(SystemExit):
        main(['setup', str(target), '--app', 'notes', '--instance', 'acme'])
    err = capsys.readouterr().err
    assert asked and 'notes' in asked[0]
    assert 'Step "install" failed' in err and 'run the same command again' in err
    assert read_lock(target / 'instances/acme')['apps'] == {}
    monkeypatch.setattr(command, 'confirm', lambda message: True)
    main(['setup', str(target), '--app', 'notes', '--instance', 'acme'])
    assert 'installed notes' in capsys.readouterr().out


def test_plain_setup_is_unchanged(tmp_path, capsys):
    main(['setup', str(tmp_path / 'p')])
    assert 'Project created' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(['setup', str(tmp_path / 'p')])


def test_dev_runs_evlet_dev_on_loopback(tmp_path, monkeypatch):
    import evlet.cli.serve as serving
    calls = []
    monkeypatch.setattr(serving, 'serve', lambda root, name, **values: calls.append((name, values)))
    main(['setup', str(tmp_path / 'p'), '--app', 'notes', '--instance', 'acme', '--trust', '--dev', '--port', '8123'])
    assert calls == [('acme', {'port': 8123, 'development': True, 'link': True})]


@pytest.mark.parametrize('arguments, message', [
    (['--app', 'notes', '--dev'], 'need --instance'),
    (['--instance', 'acme', '--dev'], 'need --instance'),
    (['--apply'], 'only work with --host'),
    (['--app', 'notes', '--port', '8001'], 'only works with --dev or --host'),
    (['--app', 'notes', '--instance', 'acme', '--host', 'localhost'], 'public lowercase hostname'),
    (['--app', 'notes', '--instance', 'acme', '--host', 'app.localhost'], 'public lowercase hostname'),
    (['--app', 'notes', '--instance', 'acme', '--host', 'https://erp.example.com'], 'hostname'),
    (['--get', 'http://example.com/notes.git'], 'HTTPS or ssh'),
    (['--get', 'https://user:secret@example.com/notes.git'], 'HTTPS or ssh'),
    (['--app', 'Notes'], 'lowercase name'),
])
def test_refusals_create_nothing(tmp_path, capsys, arguments, message):
    with pytest.raises(SystemExit):
        main(['setup', str(tmp_path / 'p'), *arguments])
    assert message in capsys.readouterr().err
    assert not (tmp_path / 'p').exists()


def test_dev_and_host_cannot_be_combined(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(['setup', str(tmp_path / 'p'), '--app', 'notes', '--instance', 'acme', '--dev', '--host', 'erp.example.com'])
    assert 'not allowed with argument' in capsys.readouterr().err
    with pytest.raises(ValueError, match='not both'):
        site.check(options(app=['notes'], instance='acme', dev=True, host='erp.example.com'))
    assert not (tmp_path / 'p').exists()


def git_app(tmp_path, name):
    """A committed app repository on disk, served to fetch_app through a local transport."""
    holder = setup(tmp_path / ('source-' + name))
    source = create_app(holder, name)
    for args in (['init'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'Initial app']):
        subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True)
    return source


def test_get_fetches_with_trust_prompt_and_resumes(tmp_path, monkeypatch, capsys):
    import evlet.cli.app as command
    cli = sys.modules['evlet.cli.main']
    sources = {f'https://example.test/{name}.git': git_app(tmp_path, name) for name in ('notes', 'crm')}
    actual_run = subprocess.run

    def local_transport(args, **values):
        if 'clone' in args:
            assert values['env']['GIT_CONFIG_GLOBAL'] == os.devnull and '--no-recurse-submodules' in args
            url, stage = args[-2], args[-1]
            result = actual_run(['git', '-c', 'core.hooksPath=' + os.devnull, 'clone', '--', str(sources[url]), stage], **values)
            actual_run(['git', '-C', stage, 'remote', 'set-url', 'origin', url], check=True, capture_output=True)
            return result
        return actual_run(args, **values)
    monkeypatch.setattr(command.subprocess, 'run', local_transport)
    asked = []
    monkeypatch.setattr(cli, 'confirm', lambda message: asked.append(message) or True)
    target = tmp_path / 'p'
    urls = ','.join(sources)
    main(['setup', str(target), '--get', urls, '--app', 'tasks', '--instance', 'acme'])
    out = capsys.readouterr().out
    assert 'Fetched notes' in out and 'Fetched crm' in out and 'no app code has run' in out
    assert [message.split(' from ')[0] for message in asked[:2]] == ['Trust and prepare notes', 'Trust and prepare crm']
    assert 'Trust and execute these local Python apps' in asked[2]
    assert sorted(read_lock(target / 'instances/acme')['apps']) == ['crm', 'notes', 'tasks']
    asked.clear()
    main(['setup', str(target), '--get', urls, '--app', 'tasks', '--instance', 'acme'])
    out = capsys.readouterr().out
    assert 'App exists: notes from https://example.test/notes.git' in out and 'already installed' in out
    assert asked == []


def test_get_refusal_keeps_fetched_source_unbuilt(tmp_path, monkeypatch, capsys):
    import evlet.cli.app as command
    cli = sys.modules['evlet.cli.main']
    source = git_app(tmp_path, 'notes')
    actual_run = subprocess.run

    def local_transport(args, **values):
        if 'clone' in args:
            return actual_run(['git', 'clone', '--', str(source), args[-1]], **values)
        return actual_run(args, **values)
    monkeypatch.setattr(command.subprocess, 'run', local_transport)
    monkeypatch.setattr(cli, 'confirm', lambda message: False)
    with pytest.raises(SystemExit):
        main(['setup', str(tmp_path / 'p'), '--get', 'https://example.test/notes.git', '--instance', 'acme'])
    assert 'Step "get https://example.test/notes.git" failed: Fetched but not built' in capsys.readouterr().err
    assert not (tmp_path / 'p/instances/acme').exists()


def resolving(address):
    return functools.partial(proxy.check, resolve=lambda host, port, proto: [(None, None, None, None, (address, 443))])


def test_host_writes_caddy_site_unit_and_prints_privileged_steps(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(proxy, 'check', resolving('203.0.113.7'))
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    target = tmp_path / 'p'
    command = ['setup', str(target), '--app', 'notes', '--instance', 'acme', '--trust', '--host', 'erp.example.com', '--ip', '203.0.113.7', '--port', '8010']
    main(command)
    out = capsys.readouterr().out
    root = target.resolve()
    assert 'Password set for administrator' in out and 'Domain set: erp.example.com' in out
    assert users(root) == [('administrator', 'admin')]
    with sqlite3.connect(root / 'instances/acme/data/database.sqlite3') as connection:
        assert connection.execute("SELECT password FROM evlet_users").fetchone()[0].startswith('scrypt:')
    config = read_instance(root / 'instances/acme')
    assert config.domain == 'erp.example.com'
    assert {str(network) for network in config.trusted_proxies} == {'127.0.0.1/32', '::1/128'}
    caddy = (root / 'deploy/evlet-acme.caddy').read_text()
    assert 'erp.example.com {' in caddy and 'reverse_proxy 127.0.0.1:8010' in caddy and 'already trusts this proxy' in caddy
    unit = (root / 'deploy/evlet-acme.service').read_text()
    assert f'ExecStart={root}/.venv/bin/python -P -m evlet serve acme --jobs --project {root} --host 127.0.0.1 --port 8010' in unit
    assert f'WorkingDirectory={root}\n' in unit and 'UMask=0077' in unit
    assert 'erp.example.com' in out and 'points here' in out
    assert f'sudo install -m 644 {root}/deploy/evlet-acme.service /etc/systemd/system/evlet-acme.service' in out
    assert 'sudo systemctl enable --now evlet-acme' in out and 'sudo systemctl reload caddy' in out
    assert "grep -qxF 'import /etc/caddy/evlet-acme.caddy' /etc/caddy/Caddyfile" in out
    assert '--apply' in out and 'Serving https://' not in out
    # Public serving refuses evlet dev, and the same command resumes without asking again.
    from evlet.cli.serve import serve
    with pytest.raises(ValueError, match='loopback'):
        serve(root, 'acme', development=True)
    monkeypatch.setattr(site, 'ask_admin', lambda: pytest.fail('asked twice'))
    main(command)
    out = capsys.readouterr().out
    assert 'Administrator exists. If nobody knows its password' in out and 'Domain is erp.example.com' in out


def test_host_stops_only_when_dns_is_surely_wrong_and_never_needs_ip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    target = tmp_path / 'p'
    command = ['setup', str(target), '--app', 'notes', '--instance', 'acme', '--trust', '--host', 'erp.example.com']
    # A private record while this server has a public address: stop.
    monkeypatch.setattr(proxy, 'check', resolving('10.0.0.9'))
    with pytest.raises(SystemExit):
        main(command + ['--ip', '84.12.169.35'])
    captured = capsys.readouterr()
    assert 'points elsewhere' in captured.out and 'sudo' not in captured.out
    assert 'Step "DNS check" failed' in captured.err and not (target / 'deploy/server.json').exists()
    assert (target / 'deploy/evlet-acme.caddy').is_file()
    # No record at all: stop.
    monkeypatch.setattr(proxy, 'check', functools.partial(proxy.check, resolve=lambda *args, **kwargs: []))
    with pytest.raises(SystemExit):
        main(command)
    assert 'no A or AAAA record' in capsys.readouterr().out
    # Behind NAT the server sees only a private address. A public record warns and setup goes on, without --ip.
    monkeypatch.setattr(proxy, 'server_addresses', lambda: {'10.0.0.4'})
    monkeypatch.setattr(proxy, 'check', resolving('84.12.169.35'))
    main(command)
    out = capsys.readouterr().out
    assert 'may point here' in out and 'sudo systemctl reload caddy' in out
    assert 'erp.example.com points at 84.12.169.35. If that is not this server, fix its A record. Ports 80 and 443 must be open.' in out


def test_site_answers_is_information_only():
    waits = []

    def failing(method, url):
        raise OSError('certificate not ready')
    line = site.site_answers('erp.example.com', fetch=failing, sleep=waits.append)
    assert line.startswith('https://erp.example.com does not answer yet (certificate not ready).') and waits == [5.0, 5.0, 5.0]
    calls = []

    def later(method, url):
        calls.append(url)
        if len(calls) < 2:
            raise OSError('refused')
        return 404, {}, b''
    assert site.site_answers('erp.example.com', fetch=later, sleep=waits.append) == 'https://erp.example.com answers (status 404).'
    assert calls == ['https://erp.example.com/.well-known/oauth-protected-resource'] * 2


def test_host_refuses_a_weak_admin_password_and_an_edited_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(proxy, 'check', resolving('203.0.113.7'))
    monkeypatch.setattr(site, 'ask_admin', lambda: 'short')
    target = tmp_path / 'p'
    command = ['setup', str(target), '--app', 'notes', '--instance', 'acme', '--trust', '--host', 'erp.example.com', '--ip', '203.0.113.7']
    with pytest.raises(SystemExit):
        main(command)
    assert 'Step "administrator" failed' in capsys.readouterr().err
    assert users(target) == [('administrator', 'admin')]
    with sqlite3.connect(target / 'instances/acme/data/database.sqlite3') as connection:
        assert connection.execute("SELECT password FROM evlet_users").fetchone()[0] == ''
    config = target / 'instances/acme/config.py'
    config.write_text(config.read_text() + '# edited\n')
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    with pytest.raises(SystemExit):
        main(command)
    assert 'config.py was edited' in capsys.readouterr().err
    assert read_instance(target / 'instances/acme').domain is None


def test_apply_runs_each_privileged_step_after_showing_it(tmp_path, monkeypatch, capsys):
    import shutil
    monkeypatch.setattr(proxy, 'check', resolving('203.0.113.7'))
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/' + name)
    caddyfile = tmp_path / 'Caddyfile'
    caddyfile.write_text('other.example.com {\n}\n')
    monkeypatch.setattr(site, 'CADDYFILE', str(caddyfile))
    ran = []
    actual_run = subprocess.run
    monkeypatch.setattr(site.subprocess, 'run', lambda args, **values: ran.append((args, values.get('input'))) if args[0] == 'sudo' else actual_run(args, **values))
    monkeypatch.setattr(site, 'site_answers', lambda host: f'https://{host} answers (status 200).')
    target = tmp_path / 'p'
    main(['setup', str(target), '--app', 'notes', '--instance', 'acme', '--trust', '--host', 'erp.example.com', '--ip', '203.0.113.7', '--apply'])
    out = capsys.readouterr().out
    root = target.resolve()
    assert [args for args, _ in ran] == [
        ['sudo', 'install', '-m', '644', f'{root}/deploy/evlet-acme.service', '/etc/systemd/system/evlet-acme.service'],
        ['sudo', 'install', '-m', '644', f'{root}/deploy/evlet-acme.caddy', '/etc/caddy/evlet-acme.caddy'],
        ['sudo', 'tee', '-a', str(caddyfile)],
        ['sudo', 'systemctl', 'daemon-reload'],
        ['sudo', 'systemctl', 'enable', '--now', 'evlet-acme'],
        ['sudo', 'systemctl', 'reload', 'caddy']]
    assert ran[2][1] == '\nimport /etc/caddy/evlet-acme.caddy\n'
    assert out.count('$ sudo') == 5 and 'Serving https://erp.example.com through Caddy' in out
    assert out.rstrip().endswith('https://erp.example.com answers (status 200).')


def test_apply_needs_systemd(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    with pytest.raises(ValueError, match='needs systemd'):
        site.run_privileged([['sudo', 'true']])


def test_unit_keeps_paths_literal(tmp_path):
    text = proxy.systemd_unit(tmp_path, 'acme', 8000, 'evlet')
    assert 'User=evlet\n' in text and '--host 127.0.0.1 --port 8000' in text and 'WantedBy=multi-user.target' in text
    for root, user in ((tmp_path / 'with space', 'evlet'), (tmp_path / '100%', 'evlet'), (tmp_path, 'root;rm')):
        with pytest.raises(ValueError, match='letters, digits'):
            proxy.systemd_unit(root, 'acme', 8000, user)


def test_host_remembers_the_public_address_and_replaces_outdated_installed_files(tmp_path, monkeypatch, capsys):
    import shutil
    seen, resolved = [], resolving('203.0.113.7')

    def check(routes, addresses=None):
        seen.append(addresses)
        return resolved(routes, addresses)
    monkeypatch.setattr(proxy, 'check', check)
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    etc = tmp_path / 'etc'
    (etc / 'caddy').mkdir(parents=True)
    (etc / 'systemd').mkdir()
    monkeypatch.setattr(site, 'CADDY_DIR', str(etc / 'caddy'))
    monkeypatch.setattr(site, 'SYSTEMD_DIR', str(etc / 'systemd'))
    target = tmp_path / 'p'
    command = ['setup', str(target), '--app', 'notes', '--instance', 'acme', '--trust', '--host', 'erp.example.com']
    main(command + ['--ip', '203.0.113.7'])
    out = capsys.readouterr().out
    root = target.resolve()
    assert 'Saved the public address in deploy/server.json' in out and seen == [['203.0.113.7']]
    assert (root / 'deploy/server.json').read_text() == '{\n  "ip": [\n    "203.0.113.7"\n  ]\n}\n'
    # A later run on the same server needs no --ip; --ip still wins when given.
    main(command)
    out = capsys.readouterr().out
    assert 'Using the public address saved in deploy/server.json: 203.0.113.7' in out and seen[-1] == ['203.0.113.7']
    # Files an earlier --apply installed from 0.4.0: the Caddy site blocked /_/mcp and the unit is older.
    (etc / 'caddy/evlet-acme.caddy').write_text('erp.example.com {\n\t@internal path /_/*\n\trespond @internal 404\n}\n')
    (etc / 'systemd/evlet-acme.service').write_text('[Service]\n')
    main(command)
    out = capsys.readouterr().out
    assert f'{etc}/caddy/evlet-acme.caddy is out of date. Run the steps below, or --apply, to replace it.' in out
    assert 'sudo systemctl restart evlet-acme' in out
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/' + name)
    caddyfile = tmp_path / 'Caddyfile'
    caddyfile.write_text(f'import {etc}/caddy/evlet-acme.caddy\n')
    monkeypatch.setattr(site, 'CADDYFILE', str(caddyfile))
    ran = []

    def fake(args, **values):
        ran.append(args)
        if args[:2] == ['sudo', 'install']:
            shutil.copyfile(args[-2], args[-1])  # what sudo install does
    actual_run = subprocess.run
    monkeypatch.setattr(site.subprocess, 'run', lambda args, **values: fake(args, **values) if args[0] == 'sudo' else actual_run(args, **values))
    monkeypatch.setattr(site, 'site_answers', lambda host: 'checked ' + host)
    main(command + ['--apply'])
    out = capsys.readouterr().out
    assert 'is out of date. Installing the new file.' in out
    assert ran == [['sudo', 'install', '-m', '644', f'{root}/deploy/evlet-acme.service', f'{etc}/systemd/evlet-acme.service'],
                   ['sudo', 'install', '-m', '644', f'{root}/deploy/evlet-acme.caddy', f'{etc}/caddy/evlet-acme.caddy'],
                   ['sudo', 'systemctl', 'daemon-reload'],
                   ['sudo', 'systemctl', 'enable', '--now', 'evlet-acme'],
                   ['sudo', 'systemctl', 'restart', 'evlet-acme'],
                   ['sudo', 'systemctl', 'reload', 'caddy']]
    assert proxy.caddy_refuses((etc / 'caddy/evlet-acme.caddy').read_text(), ['/_/mcp', '/_/tls-allowed']) == ['/_/tls-allowed']
    # Once current, --apply still runs every step (Caddy reloads), but restarts nothing and reports nothing outdated.
    ran.clear()
    main(command + ['--apply'])
    out = capsys.readouterr().out
    assert 'out of date' not in out and ['sudo', 'systemctl', 'restart', 'evlet-acme'] not in ran and ['sudo', 'systemctl', 'reload', 'caddy'] in ran
