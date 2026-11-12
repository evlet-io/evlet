"""evlet remove and evlet reset on temporary projects with real apps, instances and SQLite databases.

Every project lives under tmp_path. The systemd and Caddy directories are temporary folders and
sudo is a recording fake, so no test touches a real project, unit or Caddy configuration."""

import shutil
import sqlite3
import subprocess
import sys

import pytest

from evlet.auth import ADMINISTRATOR
from evlet.cli import proxy, site, wipe
from evlet.cli.main import main
from evlet.cli.project import instance_guard
from evlet.instances import load_runtime, read_lock

PASSWORD = 'correct horse battery staple 42'


@pytest.fixture(autouse=True)
def environment_stub(monkeypatch):
    import importlib
    module = importlib.import_module('evlet.cli.setup')
    monkeypatch.setattr(module, 'create_environment', lambda root, framework: (root / '.venv').mkdir())
    yield
    for name in list(sys.modules):
        if name.split('.')[0] in ('notes', 'crm'):
            del sys.modules[name]


@pytest.fixture
def etc(tmp_path, monkeypatch):
    """Temporary systemd and Caddy directories in place of /etc."""
    folder = tmp_path / 'etc'
    (folder / 'caddy').mkdir(parents=True)
    (folder / 'systemd').mkdir()
    monkeypatch.setattr(site, 'CADDY_DIR', str(folder / 'caddy'))
    monkeypatch.setattr(site, 'SYSTEMD_DIR', str(folder / 'systemd'))
    monkeypatch.setattr(site, 'CADDYFILE', str(folder / 'caddy/Caddyfile'))
    return folder


def project(tmp_path, name='myproject'):
    target = tmp_path / name
    main(['setup', str(target), '--app', 'notes', '--instance', 'acme', '--trust'])
    root = target.resolve()
    path = root / 'instances/acme'
    site.create_admin(root, 'acme', PASSWORD)
    (path / 'files/private').mkdir(parents=True, exist_ok=True)
    (path / 'files/private/invoice.pdf').write_bytes(b'%PDF')
    (path / 'secret.key').write_text('key\n')
    (path / 'data/backups').mkdir(exist_ok=True)
    (path / 'data/backups/old.sqlite3').write_bytes(b'old')
    return root


def users(database):
    with sqlite3.connect(database) as connection:
        return connection.execute('SELECT username, password<>"" FROM evlet_users').fetchall()


def snapshot(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob('*'))


def test_remove_plan_shows_everything_and_changes_nothing(tmp_path, capsys):
    root = project(tmp_path)
    (root / 'README.md').write_text('mine')
    before = snapshot(root)
    main(['remove', str(root), '--plan'])
    out = capsys.readouterr().out
    assert f'evlet remove deletes the Evlet project at {root}' in out
    assert '    notes' in out and 'acme: database' in out and 'with 1 account' in out and 'private files' in out
    assert 'secret.key' in out and '1 backup' in out and '.venv/' in out
    assert 'Kept, not made by Evlet: README.md' in out
    assert f'goes to {root.parent}/myproject-backup-' in out and 'Nothing was changed (--plan).' in out
    assert snapshot(root) == before and not list(tmp_path.glob('myproject-backup-*'))


def test_remove_refuses_without_a_terminal_or_with_the_wrong_name(tmp_path, capsys):
    root = project(tmp_path)
    before = snapshot(root)
    with pytest.raises(SystemExit):
        main(['remove', str(root)])
    assert 'Run this in a terminal to confirm, or pass --yes with the project name' in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(['remove', str(root), '--yes', 'otherproject'])
    assert '--yes otherproject does not match the project name myproject. Nothing was changed' in capsys.readouterr().err
    assert snapshot(root) == before and not list(tmp_path.glob('myproject-backup-*'))


def test_remove_needs_the_name_typed_back_in_a_terminal(tmp_path, monkeypatch, capsys):
    root = project(tmp_path)
    monkeypatch.setattr(wipe, 'terminal', lambda: True)
    asked = []

    def answers(*replies):
        replies = list(replies)
        return lambda prompt: asked.append(prompt) or replies.pop(0)
    with pytest.raises(ValueError, match='"myproj" is not myproject. Nothing was changed'):
        wipe.remove(root, ask=answers('n', 'myproj'))
    assert asked == ['Write a backup first? [Y/n] ', 'Type the project name, myproject, to confirm: ']
    assert root.exists() and (root / 'instances/acme/data/database.sqlite3').exists()
    assert wipe.remove(root, ask=answers('', 'myproject')) is not None  # Enter means yes to the backup
    assert not root.exists()
    assert 'Removed ' + str(root) in capsys.readouterr().out


def test_remove_writes_a_final_backup_outside_the_project_then_deletes_it(tmp_path, capsys):
    root = project(tmp_path)
    main(['remove', str(root), '--yes', 'myproject'])  # without a terminal the backup is the default
    out = capsys.readouterr().out
    assert not root.exists()
    [backup] = tmp_path.glob('myproject-backup-*')
    assert f'Backup written: {backup}' in out
    saved = backup / 'instances/acme'
    assert users(saved / 'database.sqlite3') == [(ADMINISTRATOR, 1)]
    assert (saved / 'files/private/invoice.pdf').read_bytes() == b'%PDF'
    assert (saved / 'secret.key').stat().st_mode & 0o777 == 0o600
    assert (saved / 'config.py').is_file() and (saved / 'apps.lock').is_file()
    assert (backup / 'apps/notes').is_dir()


def test_remove_keeps_what_it_did_not_make_and_what_is_asked(tmp_path, capsys):
    root = project(tmp_path)
    (root / 'README.md').write_text('mine')
    main(['remove', str(root), '--yes', 'myproject', '--no-backup', '--keep-backups'])
    out = capsys.readouterr().out
    assert 'Backup written' not in out and not list(tmp_path.glob('myproject-backup-*'))
    assert 'Kept: README.md, instances' in out
    assert sorted(path.name for path in root.iterdir()) == ['README.md', 'instances']
    assert snapshot(root / 'instances') == ['acme', 'acme/data', 'acme/data/backups', 'acme/data/backups/old.sqlite3']
    other = project(tmp_path, 'second')
    main(['remove', str(other), '--yes', 'second', '--no-backup', '--keep-data'])
    assert sorted(path.name for path in other.iterdir()) == ['instances']
    assert users(other / 'instances/acme/data/database.sqlite3') == [(ADMINISTRATOR, 1)]


def test_remove_never_follows_symbolic_links_out_of_the_project(tmp_path):
    root = project(tmp_path)
    outside = tmp_path / 'outside'
    (outside / 'env').mkdir(parents=True)
    (outside / 'env/keep.txt').write_text('keep')
    (outside / 'app').mkdir()
    (outside / 'app/keep.py').write_text('keep')
    shutil.rmtree(root / '.venv')
    (root / '.venv').symlink_to(outside / 'env')
    (root / 'apps/linked').symlink_to(outside / 'app')
    (root / 'instances/acme/files/private/shared').symlink_to(outside)
    main(['remove', str(root), '--yes', 'myproject', '--no-backup'])
    assert not root.exists()
    assert (outside / 'env/keep.txt').read_text() == 'keep' and (outside / 'app/keep.py').read_text() == 'keep'


def test_remove_refuses_while_an_instance_is_served_by_evlet(tmp_path, capsys):
    root = project(tmp_path)
    with instance_guard(root / 'instances/acme', serving=True):
        with pytest.raises(SystemExit):
            main(['remove', str(root), '--yes', 'myproject'])
    assert 'In use: acme. Stop evlet serve or evlet dev for them first. Nothing was changed' in capsys.readouterr().err
    assert (root / 'instances/acme/data/database.sqlite3').exists()


def install_system_files(root, etc, name='acme'):
    unit, caddy = site.installed(name)
    (root / 'deploy').mkdir(exist_ok=True)
    unit.write_text(proxy.systemd_unit(root, name, 8000, 'evlet'))
    (root / 'deploy' / caddy.name).write_text('erp.example.com {\n}\n')
    caddy.write_text('erp.example.com {\n}\n')
    return unit, caddy


def test_remove_stops_and_removes_only_the_system_files_this_project_installed(tmp_path, etc, monkeypatch, capsys):
    root = project(tmp_path)
    main(['instance', 'beta', '--project', str(root)])
    unit, caddy = install_system_files(root, etc)
    # Another project serves an instance also called beta: its unit and site are left alone.
    foreign_unit, foreign_caddy = site.installed('beta')
    foreign_unit.write_text(proxy.systemd_unit(tmp_path / 'elsewhere', 'beta', 8001, 'evlet'))
    foreign_caddy.write_text('beta.example.com {\n}\n')
    caddyfile = etc / 'caddy/Caddyfile'
    caddyfile.write_text(f'other.example.com {{\n}}\nimport {caddy}\nimport {foreign_caddy}\n')
    before = snapshot(root)
    with pytest.raises(SystemExit):
        main(['remove', str(root), '--yes', 'myproject'])
    captured = capsys.readouterr()
    assert 'Run again with --sudo' in captured.err and snapshot(root) == before
    assert f'sudo systemctl disable --now evlet-acme\n    sudo rm -f {unit}' in captured.out
    assert f"sudo sed -i '\\|^import {caddy}$|d' {caddyfile}" in captured.out
    assert f'Left alone, they belong to another project or were not made by Evlet:\n    {foreign_unit}\n    {foreign_caddy}' in captured.out
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/' + name)
    ran = []

    def fake(args, **values):
        ran.append(args)
        if args[:3] == ['sudo', 'rm', '-f']:
            (etc / args[3]).unlink()
        if args[:2] == ['sudo', 'tee']:
            caddyfile.write_text(values['input'])
    actual_run = subprocess.run
    monkeypatch.setattr(wipe.subprocess, 'run', lambda args, **values: fake(args, **values) if args[0] == 'sudo' else actual_run(args, **values))
    main(['remove', str(root), '--yes', 'myproject', '--no-backup', '--sudo'])
    out = capsys.readouterr().out
    assert ran == [['sudo', 'systemctl', 'disable', '--now', 'evlet-acme'],
                   ['sudo', 'rm', '-f', str(unit)],
                   ['sudo', 'tee', str(caddyfile)],
                   ['sudo', 'rm', '-f', str(caddy)],
                   ['sudo', 'systemctl', 'daemon-reload'],
                   ['sudo', 'systemctl', 'reload', 'caddy']]
    assert out.count('$ sudo') == 6 and not root.exists()
    assert not unit.exists() and not caddy.exists() and foreign_unit.exists() and foreign_caddy.exists()
    assert caddyfile.read_text() == f'other.example.com {{\n}}\nimport {foreign_caddy}\n'


def test_reset_plan_shows_what_goes_and_changes_nothing(tmp_path, capsys):
    root = project(tmp_path)
    before = snapshot(root)
    main(['reset', 'acme', '--project', str(root), '--plan'])
    out = capsys.readouterr().out
    assert 'evlet reset wipes instance acme' in out and 'with 1 account' in out and 'private files' in out
    assert 'Kept: config.py, routes.py and data/backups.' in out and 'Installed again: notes.' in out
    assert f'The built-in Administrator ({ADMINISTRATOR}) starts again with no password.' in out
    assert 'Nothing was changed (--plan).' in out and snapshot(root) == before


def test_reset_wipes_the_instance_and_installs_its_apps_fresh(tmp_path, capsys):
    root = project(tmp_path)
    path = root / 'instances/acme'
    config = (path / 'config.py').read_text()
    with pytest.raises(SystemExit):
        main(['reset', 'acme', '--project', str(root)])
    assert 'Run this in a terminal to confirm, or pass --yes with the instance name' in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(['reset', 'acme', '--project', str(root), '--yes', 'acmee'])
    assert users(path / 'data/database.sqlite3') == [(ADMINISTRATOR, 1)]
    capsys.readouterr()
    main(['reset', 'acme', '--project', str(root), '--yes', 'acme', '--trust'])
    out = capsys.readouterr().out
    assert 'Installed again: notes' in out and f'Instance acme is fresh. The built-in Administrator ({ADMINISTRATOR}) has no password.' in out
    assert users(path / 'data/database.sqlite3') == [(ADMINISTRATOR, 0)]
    assert not (path / 'files').exists() and not (path / 'secret.key').exists() and not (path / wipe.RESET_MARKER).exists()
    assert (path / 'config.py').read_text() == config and list(read_lock(path)['apps']) == ['notes']
    [backup] = (path / 'data/backups').glob('*-before-reset')
    assert (path / 'data/backups/old.sqlite3').read_bytes() == b'old'
    assert users(backup / 'database.sqlite3') == [(ADMINISTRATOR, 1)] and (backup / 'files/private/invoice.pdf').exists()
    load_runtime(root, 'acme').database.close()


def test_reset_asks_for_the_typed_name_in_a_terminal(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.setattr(wipe, 'terminal', lambda: True)
    replies = ['no', 'acm']
    with pytest.raises(ValueError, match='"acm" is not acme. Nothing was changed'):
        wipe.reset(root, 'acme', ask=lambda prompt: replies.pop(0), trust=True)
    assert users(root / 'instances/acme/data/database.sqlite3') == [(ADMINISTRATOR, 1)]
    replies = ['no', 'acme']
    assert wipe.reset(root, 'acme', ask=lambda prompt: replies.pop(0), trust=True) is None  # no backup chosen
    assert not list((root / 'instances/acme/data/backups').glob('*-before-reset'))


def test_reset_refuses_while_the_instance_is_served(tmp_path, etc, monkeypatch, capsys):
    root = project(tmp_path)
    with instance_guard(root / 'instances/acme', serving=True):
        with pytest.raises(SystemExit):
            main(['reset', 'acme', '--project', str(root), '--yes', 'acme', '--trust'])
    assert 'Instance acme is in use. Stop its evlet serve or evlet dev first. Nothing was changed' in capsys.readouterr().err
    install_system_files(root, etc)
    monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/' + name)
    asked = []
    active = lambda args, **values: asked.append(args) or subprocess.CompletedProcess(args, 0)
    with pytest.raises(ValueError, match='being served by systemd. Stop it first with: sudo systemctl stop evlet-acme'):
        wipe.reset(root, 'acme', yes='acme', trust=True, run=active)
    assert asked == [['systemctl', 'is-active', '--quiet', 'evlet-acme']]
    assert users(root / 'instances/acme/data/database.sqlite3') == [(ADMINISTRATOR, 1)]


def test_an_interrupted_reset_resumes_with_the_recorded_apps(tmp_path, capsys):
    root = project(tmp_path)
    path = root / 'instances/acme'
    with pytest.raises(SystemExit):
        main(['reset', 'acme', '--project', str(root), '--yes', 'acme'])  # no --trust and no terminal to ask
    assert 'The data is already wiped' in capsys.readouterr().err
    assert not (path / 'data/database.sqlite3').exists() and read_lock(path)['apps'] == {}
    main(['reset', 'acme', '--project', str(root), '--plan'])
    assert 'An earlier reset stopped after wiping' in capsys.readouterr().out
    main(['reset', 'acme', '--project', str(root), '--yes', 'acme', '--trust'])
    assert list(read_lock(path)['apps']) == ['notes'] and users(path / 'data/database.sqlite3') == [(ADMINISTRATOR, 0)]
    assert not (path / wipe.RESET_MARKER).exists()
