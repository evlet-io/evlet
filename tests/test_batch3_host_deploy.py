"""Host deployment hardening: rendered installs, the service account, unit secrets and cookie names."""

from collections import namedtuple
import functools
import grp
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

import pytest

from evlet.cli import proxy, site
from evlet.cli.main import main
from evlet.instances import load_runtime

PASSWORD = 'correct horse battery staple 42'


@pytest.fixture(autouse=True)
def operator(monkeypatch, tmp_path):
    """A non-root operator (the container runs as root), no real venv, and private /etc paths."""
    import importlib
    monkeypatch.setattr(os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(importlib.import_module('evlet.cli.setup'), 'create_environment', lambda root, framework: (root / '.venv').mkdir())
    monkeypatch.setattr(proxy, 'check', functools.partial(proxy.check, resolve=lambda host, port, proto: [(None, None, None, None, ('203.0.113.7', 443))]))
    monkeypatch.setattr(site, 'ask_admin', lambda: PASSWORD)
    monkeypatch.setattr(site, 'site_answers', lambda host: 'checked ' + host)
    monkeypatch.delenv('EVLET_SERVICE_USER', raising=False)
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


def sudo_recorder(monkeypatch):
    """Record sudo calls; sudo install /dev/stdin writes its input to the target, like the real one."""
    ran = []
    actual = subprocess.run
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/' + name)

    def fake(args, **values):
        if args[0] != 'sudo':
            return actual(args, **values)
        ran.append((args, values.get('input')))
        if args[:2] == ['sudo', 'install']:
            assert args[-2] == '/dev/stdin'
            Path(args[-1]).write_text(values['input'])
    monkeypatch.setattr(site.subprocess, 'run', fake)
    return ran


def test_apply_installs_the_rendered_text_not_a_rewritten_deploy_file(tmp_path, monkeypatch, capsys, operator):
    main(command(tmp_path))
    root = (tmp_path / 'p').resolve()
    unit = root / 'deploy/evlet-acme.service'
    reviewed = unit.read_text()
    # The service account rewrites deploy/ between generation and the next --apply.
    unit.write_text('[Service]\nUser=root\nExecStart=/bin/sh -c "chmod 4755 /tmp/x"\n')
    (root / 'deploy/evlet-acme.caddy').write_text('evil.example.com {\n}\n')
    ran = sudo_recorder(monkeypatch)
    main(command(tmp_path) + ['--apply'])
    out = capsys.readouterr().out
    installed = (operator / 'systemd/evlet-acme.service').read_text()
    assert installed == reviewed and 'User=root' not in installed and 'chmod' not in installed
    assert 'evil.example.com' not in (operator / 'caddy/evlet-acme.caddy').read_text()
    assert [args[-1] for args, _ in ran if args[1] == 'install'] == [str(operator / 'systemd/evlet-acme.service'),
                                                                      str(operator / 'caddy/evlet-acme.caddy'), str(operator / 'caddy/evlet-global.caddy')]
    # The shown step names the reviewed copy, and the copy in deploy/ is rewritten with the same text.
    assert f'$ sudo install -m 644 {unit} {operator}/systemd/evlet-acme.service' in out
    assert unit.read_text() == reviewed


def test_unit_reads_an_optional_root_owned_environment_file():
    text = proxy.systemd_unit(Path('/srv/evlet'), 'acme', 8000, 'evlet-acme')
    service = text.split('[Install]')[0]
    assert 'EnvironmentFile=-/etc/evlet/acme.env\n' in service
    assert not [line for line in service.splitlines() if line.startswith('Environment=')]
    # Jobs run in the web process, in the same write queue as requests (no second worker unit).
    assert ' -m evlet serve acme --jobs ' in text


Account = namedtuple('Account', 'pw_name pw_uid pw_gid')
Group = namedtuple('Group', 'gr_name gr_gid gr_mem')


def test_service_account_refuses_root_and_sudoers_and_keeps_the_installed_one(monkeypatch, operator):
    accounts = {'root': Account('root', 0, 0), 'ops': Account('ops', 1000, 1000), 'wheelie': Account('wheelie', 1001, 10),
                'evlet-acme': Account('evlet-acme', 990, 990)}

    def account(name):
        return accounts[name]
    monkeypatch.setattr(pwd, 'getpwnam', account)
    monkeypatch.setattr(grp, 'getgrall', lambda: [Group('sudo', 27, ['ops']), Group('wheel', 10, []), Group('evlet-acme', 990, [])])
    monkeypatch.setattr('getpass.getuser', lambda: 'ops')
    assert site.privileged_account('root') == 'root is root'
    assert site.privileged_account('ops') == 'ops is in the sudo group and can use sudo'
    assert site.privileged_account('wheelie') == 'wheelie is in the wheel group and can use sudo'
    assert site.privileged_account('evlet-acme') is None and site.privileged_account('unknown') is None
    # The default stays the login user, with a note when it can use sudo.
    assert site.service_account('acme') == ('ops', False)
    assert 'Note: the service runs as ops, and ops is in the sudo group' in site.account_note('ops', False)
    # A dedicated account is chosen with EVLET_SERVICE_USER (or a user argument) and gets no note.
    monkeypatch.setenv('EVLET_SERVICE_USER', 'evlet-acme')
    assert site.service_account('acme') == ('evlet-acme', True) and site.account_note('evlet-acme', True) is None
    for chosen in ('root', 'ops', 'wheelie'):
        monkeypatch.setenv('EVLET_SERVICE_USER', chosen)
        with pytest.raises(ValueError, match='dedicated account without sudo rights, such as evlet-acme'):
            site.service_account('acme')
    with pytest.raises(ValueError, match='root is root'):
        site.service_account('acme', 'root')
    # Without a choice, the account of the installed (root-owned) unit stays.
    monkeypatch.delenv('EVLET_SERVICE_USER')
    (operator / 'systemd/evlet-acme.service').write_text(proxy.systemd_unit(Path('/srv/evlet'), 'acme', 8000, 'evlet-acme'))
    assert site.installed_user('acme') == 'evlet-acme' and site.service_account('acme') == ('evlet-acme', False)


def test_setup_writes_the_chosen_service_account_and_a_host_prefixed_cookie(tmp_path, monkeypatch, capsys, operator):
    monkeypatch.setenv('EVLET_SERVICE_USER', 'nobody')
    main(command(tmp_path))
    root = (tmp_path / 'p').resolve()
    assert 'User=nobody\n' in (root / 'deploy/evlet-acme.service').read_text()
    assert 'Note: the service runs as' not in capsys.readouterr().out
    # host add keeps the account of the unit that --apply installed, without the variable.
    sudo_recorder(monkeypatch)
    main(command(tmp_path) + ['--apply'])
    monkeypatch.delenv('EVLET_SERVICE_USER')
    from evlet.cli import hosts
    app = load_runtime(root, 'acme')
    app.database.close()
    texts = hosts.proxy_files(root, 'acme', app, 8000)
    assert 'User=nobody\n' in texts['unit']
    # A public instance names its session cookies with the __Host- prefix; the portal cookie follows it.
    auth = app.business.auth
    assert auth.cookie_name == '__Host-evlet_session' and auth.cookie_for('portal') == '__Host-evlet_portal_session'
    assert auth.secure_cookies


def test_a_local_instance_keeps_the_plain_cookie_name(tmp_path):
    main(['setup', str(tmp_path / 'p'), '--app', 'notes', '--instance', 'acme', '--trust'])
    app = load_runtime((tmp_path / 'p').resolve(), 'acme')
    app.database.close()
    assert app.business.auth.cookie_name == 'evlet_session'
