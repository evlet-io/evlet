"""A dedicated service account end to end (SECH-01), loopback is no trusted proxy by default (SECH-08), and
hash-checked app dependencies (SECH-13). No real sudo runs: sudo calls are recorded."""

from collections import namedtuple
import functools
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys

import pytest

from evlet import Instance
from evlet.auth import client_address
from evlet.cli import app as app_cli, proxy, restart, site, upgrade
from evlet.cli.main import main
from evlet.cli.setup import python_in

PASSWORD = 'correct horse battery staple 42'
Account = namedtuple('Account', 'pw_name pw_uid pw_gid')
SERVICE_UID = 4242


@pytest.fixture(autouse=True)
def operator(monkeypatch, tmp_path):
    """A non-root deploy user 'ops' (the container runs as root), no real venv, private /etc paths."""
    import importlib
    monkeypatch.setattr(os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(importlib.import_module('evlet.cli.setup'), 'create_environment', lambda root, framework: (root / '.venv').mkdir())
    monkeypatch.setattr(proxy, 'check', functools.partial(proxy.check, resolve=lambda host, port, proto: [(None, None, None, None, ('203.0.113.7', 443))]))
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    monkeypatch.setattr(site, 'site_answers', lambda host: 'checked ' + host)
    monkeypatch.delenv('EVLET_SERVICE_USER', raising=False)
    monkeypatch.delenv('EVLET_REQUIRE_HASHES', raising=False)
    monkeypatch.setattr('getpass.getuser', lambda: 'ops')
    etc = tmp_path / 'etc'
    (etc / 'caddy').mkdir(parents=True)
    (etc / 'systemd').mkdir()
    monkeypatch.setattr(site, 'CADDY_DIR', str(etc / 'caddy'))
    monkeypatch.setattr(site, 'SYSTEMD_DIR', str(etc / 'systemd'))
    caddyfile = tmp_path / 'Caddyfile'
    caddyfile.write_text(f'import {etc}/caddy/evlet-global.caddy\nimport {etc}/caddy/evlet-acme.caddy\n')
    monkeypatch.setattr(site, 'CADDYFILE', str(caddyfile))
    yield etc
    for name in list(sys.modules):
        if name.split('.')[0] == 'notes':
            del sys.modules[name]


def command(tmp_path):
    return ['setup', str(tmp_path / 'p'), '--app', 'notes', '--instance', 'acme', '--trust', '--host', 'erp.example.com', '--ip', '203.0.113.7']


def no_account(monkeypatch, *, exists=False):
    """The account 'evlet' does not exist yet (or exists with SERVICE_UID); everyone else is looked up for real."""
    real_name, real_uid = pwd.getpwnam, pwd.getpwuid

    def by_name(name):
        if name == 'evlet':
            if exists:
                return Account('evlet', SERVICE_UID, SERVICE_UID)
            raise KeyError(name)
        return real_name(name)

    def by_uid(uid):
        return Account('evlet', SERVICE_UID, SERVICE_UID) if uid == SERVICE_UID else real_uid(uid)
    monkeypatch.setattr(pwd, 'getpwnam', by_name)
    monkeypatch.setattr(pwd, 'getpwuid', by_uid)


def sudo_recorder(monkeypatch, module=site):
    ran = []
    actual = subprocess.run
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/' + name)

    def fake(args, **values):
        if args[0] != 'sudo':
            return actual(args, **values)
        ran.append(list(args))
        if args[:2] == ['sudo', 'install']:
            Path(args[-1]).write_text(values['input'])
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(module.subprocess, 'run', fake)
    return ran


def test_setup_with_a_service_user_creates_it_and_hands_over_only_the_instance_state(tmp_path, monkeypatch, capsys, operator):
    no_account(monkeypatch)
    main(command(tmp_path) + ['--service-user', 'evlet'])
    out = capsys.readouterr().out
    root = (tmp_path / 'p').resolve()
    path = root / 'instances/acme'
    unit = (root / 'deploy/evlet-acme.service').read_text()
    # The unit runs as the account; only the instance state is writable for it.
    assert 'User=evlet\n' in unit and 'ProtectSystem=strict\n' in unit and 'ProtectSystem=full' not in unit
    assert f'ReadWritePaths={path}/data {path}/files {path}\n' in unit
    # The printed steps create the system account and give it the state, not the code or the configuration.
    useradd = 'sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin evlet'
    assert useradd in out
    state = [str(path / item) for item in ('data', 'files', 'secret.key', 'apps.lock') if (path / item).exists()]
    assert f'sudo chown -R evlet:evlet {" ".join(state)}' in out
    assert str(path / 'config.py') not in ' '.join(state) and str(root / '.venv') not in out.split('useradd')[1].split('daemon-reload')[0]
    assert f'sudo chmod 660 {path}/.evlet.lock' in out and f'sudo chgrp evlet {path}' in out and f'sudo chmod 1770 {path}' in out
    assert f'chmod 644 {path}/config.py' in out
    # Before the handover the deploy user prepared the directories and the shared lock privately.
    assert f'sudo chmod -R go-rwx {" ".join(state[:-1])}\n' in out and f'sudo chmod 644 {path}/apps.lock' in out
    assert stat.S_IMODE((path / '.evlet.lock').stat().st_mode) == 0o600

    ran = sudo_recorder(monkeypatch)
    main(command(tmp_path) + ['--service-user', 'evlet', '--apply'])
    out = capsys.readouterr().out
    order = [' '.join(args[:3]) for args in ran]
    assert order.index('sudo useradd --system') < order.index('sudo chown -R') < order.index('sudo systemctl daemon-reload')
    assert ['sudo', 'chmod', '1770', str(path)] in ran
    assert 'User=evlet\n' in (operator / 'systemd/evlet-acme.service').read_text()
    # The one step without sudo really ran: the service can read config.py, which the deploy user keeps.
    assert stat.S_IMODE((path / 'config.py').stat().st_mode) == 0o644
    assert 'evlet migrate, backup, restore, users and secrets run themselves again as evlet' in out


def test_the_default_stays_the_deploy_user_with_the_unit_of_earlier_releases(tmp_path, capsys):
    main(command(tmp_path))
    out = capsys.readouterr().out
    unit = ((tmp_path / 'p') / 'deploy/evlet-acme.service').read_text()
    assert 'User=ops\n' in unit and 'ProtectSystem=full\n' in unit and 'useradd' not in out and 'chown' not in out


def hand_over(root, name='acme'):
    """What --apply's chown does: the service account owns data/ (the container runs as root, so this is real)."""
    os.chown(root / 'instances' / name / 'data', SERVICE_UID, SERVICE_UID)


def test_deploy_user_commands_run_again_as_the_owner_of_the_state(tmp_path, monkeypatch, capsys):
    main(['setup', str(tmp_path / 'p'), '--app', 'notes', '--instance', 'acme', '--trust'])
    root = (tmp_path / 'p').resolve()
    assert site.state_owner(root, 'acme') is None  # root-owned state is never handed over
    no_account(monkeypatch, exists=True)
    hand_over(root)
    assert site.state_owner(root, 'acme') == 'evlet'
    ran = sudo_recorder(monkeypatch)
    main(['backup', 'acme', '--project', str(root)])
    expected = ['sudo', '-u', 'evlet', str(python_in(root)), '-P', '-m', 'evlet', '--project', str(root), 'backup', 'acme', '--project', str(root)]
    assert ran == [expected]
    err = capsys.readouterr().err
    assert 'instances/acme belongs to evlet; running the command as evlet:\n$ sudo -u evlet ' in err
    # Restore asks its question inside the re-run, as the owner.
    ran.clear()
    main(['restore', 'data/backups/x.sqlite3', '--instance', 'acme', '--yes', 'acme', '--project', str(root)])
    assert ran[0][:3] == ['sudo', '-u', 'evlet']
    assert ran[0][-8:] == ['restore', 'data/backups/x.sqlite3', '--instance', 'acme', '--yes', 'acme', '--project', str(root)]
    # Without sudo the exact command is printed instead, and nothing runs.
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    with pytest.raises(SystemExit):
        main(['backup', 'acme', '--project', str(root)])
    assert 'instances/acme belongs to evlet. Run the command as that account:\n    sudo -u evlet ' in capsys.readouterr().err
    # The app builds stay the deploy user's, readable by the service.
    builds = root / '.evlet' / 'builds'
    assert builds.is_dir() and all(stat.S_IMODE(item.stat().st_mode) & 0o005 == 0o005 for item in builds.rglob('*') if item.is_dir())


def test_restart_checks_as_the_owner_and_restarts_with_the_deploy_users_sudo(tmp_path, monkeypatch, operator):
    main(['setup', str(tmp_path / 'p'), '--app', 'notes', '--instance', 'acme', '--trust'])
    root = (tmp_path / 'p').resolve()
    (operator / 'systemd/evlet-acme.service').write_text(proxy.systemd_unit(root, 'acme', 8123, 'evlet', dedicated=True))
    no_account(monkeypatch, exists=True)
    hand_over(root)
    ran, lines = [], []

    def run(args, **values):
        ran.append(list(args))
        return subprocess.CompletedProcess(args, 0, '', '')
    assert restart.restart(root, 'acme', yes=True, run=run, which=lambda name: '/usr/bin/' + name,
                           fetch=lambda port, host: port == 8123, out=lines.append)
    assert ran == [['sudo', '-u', 'evlet', str(python_in(root)), '-P', '-m', 'evlet', '--project', str(root),
                    'restart', 'acme', '--check', '--service', 'evlet-acme'],
                   ['sudo', 'systemctl', 'restart', 'evlet-acme']]
    assert lines[0].startswith('instances/acme belongs to evlet; checking it as evlet:\n$ sudo -u evlet ')
    # A failed check as the owner leaves the service alone.
    ran.clear()
    assert not restart.restart(root, 'acme', yes=True, run=lambda args, **values: ran.append(args) or subprocess.CompletedProcess(args, 1),
                               which=lambda name: '/usr/bin/' + name, out=lines.append)
    assert len(ran) == 1 and 'systemctl' not in ran[0]


def test_upgrade_runs_the_state_steps_as_the_owner():
    command = ['python', '-P', '-m', 'evlet', 'migrate', 'acme']
    assert upgrade.owned('evlet', command) == ['sudo', '-u', 'evlet', *command] and upgrade.owned(None, command) == command
    compile(upgrade.BUILD, 'build', 'exec')


def test_a_privileged_or_deploy_account_is_refused_as_the_dedicated_one(tmp_path):
    main(['setup', str(tmp_path / 'p'), '--app', 'notes', '--instance', 'acme', '--trust'])
    with pytest.raises(ValueError, match='must differ from the deploy user'):
        site.ownership_steps((tmp_path / 'p').resolve(), 'acme', 'ops')


# SECH-08: loopback is no trusted proxy unless trusted_proxies says so.

def scope(peer, forwarded):
    return {'client': (peer, 5000), 'headers': [(b'x-forwarded-for', forwarded.encode())]}


def test_a_local_process_cannot_claim_another_client_address_by_default():
    assert Instance('acme').trusted_proxies == ()
    for peer in ('127.0.0.1', '::1'):
        assert client_address(scope(peer, '203.0.113.7'), ()) == peer
    # Only an explicit setting (written by evlet setup --host for its Caddy) reads the header.
    assert client_address(scope('127.0.0.1', '203.0.113.7'), ('127.0.0.1',)) == '203.0.113.7'
    assert client_address(scope('192.0.2.9', '203.0.113.7'), ('127.0.0.1',)) == '192.0.2.9'


def test_an_instance_without_trusted_proxies_ignores_forwarded_headers_from_loopback(tmp_path):
    from evlet.instances import load_runtime
    main(['setup', str(tmp_path / 'p'), '--app', 'notes', '--instance', 'acme', '--trust'])
    app = load_runtime((tmp_path / 'p').resolve(), 'acme')
    try:
        assert app.business.auth.client_address(scope('127.0.0.1', '198.51.100.4')) == '127.0.0.1'
    finally:
        app.database.close()
    # evlet serve never lets uvicorn rewrite the peer from the header (its default trusts 127.0.0.1).
    source = (Path(site.__file__).parent / 'serve.py').read_text()
    assert source.count('proxy_headers=False') >= 2


def test_the_generated_host_setup_trusts_its_proxy_explicitly(tmp_path):
    main(command(tmp_path))
    config = ((tmp_path / 'p') / 'instances/acme/config.py').read_text()
    assert 'trusted_proxies=("127.0.0.1", "::1")' in config


# SECH-13: hash-checked dependencies are opt-in; unpinned ones are named.

def project_with_dependency(tmp_path, monkeypatch, dependency):
    main(['setup', str(tmp_path / 'p'), '--app', 'notes'])
    root = (tmp_path / 'p').resolve()
    pyproject = root / 'apps/notes/pyproject.toml'
    pyproject.write_text(pyproject.read_text().replace('dependencies = ["evlet>=0.4.0,<0.5"]', f'dependencies = ["evlet>=0.4.0,<0.5", "{dependency}"]'))
    monkeypatch.setattr(sys, 'prefix', str(root / '.venv'))
    ran = []

    def run(args, **values):
        ran.append(list(args))
        return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(app_cli.subprocess, 'run', run)
    monkeypatch.setattr('evlet.packages.owned_distribution', lambda name, path: True)
    from importlib import metadata
    real = metadata.version
    monkeypatch.setattr(metadata, 'version', lambda name: {'notes': '0.1.0', 'packaging': '1.0'}.get(name) or real(name))
    return root, ran


def test_unpinned_dependencies_install_as_before_with_a_warning(tmp_path, monkeypatch, capsys):
    root, ran = project_with_dependency(tmp_path, monkeypatch, 'packaging>=999')
    try:
        app_cli.prepare_dependencies(root, ['notes'])
    except ValueError:
        pass  # resolving the fake installation afterwards is not this test's concern
    assert any(args[-1] == 'packaging>=999' and '-c' in args and '--require-hashes' not in args for args in ran)
    err = capsys.readouterr().err
    assert 'Warning: unpinned app dependencies: notes: packaging>=999.' in err and 'requirements.lock' in err
    from packaging.requirements import Requirement
    assert app_cli.unpinned_warning({'notes': [Requirement('x==1.2'), Requirement('y===2')]}) is None
    assert 'notes: z==1.*' in app_cli.unpinned_warning({'notes': [Requirement('z==1.*')]})


def test_require_hashes_installs_only_the_apps_lock(tmp_path, monkeypatch, capsys):
    root, ran = project_with_dependency(tmp_path, monkeypatch, 'packaging>=999')
    with pytest.raises(ValueError, match='--require-hashes needs a requirements.lock with hashes in notes for: packaging>=999. Nothing was installed'):
        app_cli.prepare_dependencies(root, ['notes'], require_hashes=True)
    assert ran == []
    lock = root / 'apps/notes/requirements.lock'
    lock.write_text('packaging==999.0 --hash=sha256:' + '0' * 64 + '\n')
    monkeypatch.setenv('EVLET_REQUIRE_HASHES', '1')
    # The fake pip installs nothing, so the declared range stays unmet and the command says so.
    with pytest.raises(ValueError, match='do not provide packaging>=999'):
        app_cli.prepare_dependencies(root, ['notes'])
    assert ran[0][4:] == ['--disable-pip-version-check', '--require-hashes', '--no-deps', '-r', str(lock)]
    assert 'Warning: unpinned' not in capsys.readouterr().err


def test_atomic_json_keeps_the_replaced_files_mode(tmp_path):
    import stat
    from evlet.cli.project import atomic_json
    target = tmp_path / 'apps.lock'
    target.write_text('{}')
    target.chmod(0o644)
    atomic_json(target, {'apps': []})
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    fresh = tmp_path / 'new.json'
    atomic_json(fresh, {})
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600


def test_migrate_users_and_secrets_hand_over_to_the_state_owner(monkeypatch, tmp_path):
    from evlet.cli.commands import accounts, install
    from evlet.cli import site
    calls = []
    monkeypatch.setattr(site, 'as_state_owner', lambda root, name, values: calls.append(name) or True)
    context = type('Context', (), {'root': tmp_path, 'values': ['x']})()
    monkeypatch.setattr(install, 'require_instance', lambda root, args: None)
    install.run_install(type('Args', (), {'instance': 'acme', 'command': 'migrate', 'plan': False})(), context)
    monkeypatch.setattr('evlet.instances.read_instance', lambda path: None)
    (tmp_path / 'instances/acme').mkdir(parents=True)
    accounts.run_users(type('Args', (), {'instance': 'acme', 'users_command': 'set-password', 'username': 'admin'})(), context)
    accounts.run_secrets(type('Args', (), {'instance': 'acme', 'secrets_command': 'rotate'})(), context)
    assert calls == ['acme', 'acme', 'acme']
