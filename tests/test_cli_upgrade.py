"""evlet upgrade, evlet --version and the .venv version warning, on a real project with a fake pip and systemd."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from evlet import framework
from evlet.cli import proxy, setup as setting, upgrade as upgrading
from evlet.cli.main import main
from test_cli_lifecycle import environment_stub, project  # noqa: F401


def installed(root, version):
    """Write the dist-info pip leaves in the project's .venv."""
    info = root / '.venv/lib/python3.12/site-packages' / f'evlet-{version}.dist-info'
    for old in info.parent.glob('evlet-*.dist-info') if info.parent.is_dir() else ():
        for item in old.iterdir():
            item.unlink()
        old.rmdir()
    info.mkdir(parents=True)
    (info / 'METADATA').write_text(f'Metadata-Version: 2.4\nName: evlet\nVersion: {version}\n\nEvlet\n')


class Server:
    """Fake pip, evlet subprocesses and systemd. pip installs `newest`; `fail` names the step that exits 1."""

    def __init__(self, root, newest='0.4.4', fail=None):
        self.root, self.newest, self.fail, self.ran = root, newest, fail, []

    def run(self, args, **values):
        self.ran.append(args)
        kind = self.kind(args)
        if kind == self.fail:
            return subprocess.CompletedProcess(args, 1, stdout='', stderr='')
        if kind == 'pip':
            installed(Path(args[0]).parent.parent.parent, self.newest)
        if kind == 'backup':
            return subprocess.CompletedProcess(args, 0, stdout='Backup: /srv/b/20261113.sqlite3\nWith: x\n', stderr='')
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

    @staticmethod
    def kind(args):
        if args[:2] == ['sudo', 'systemctl']:
            return args[2]
        if args[1:3] == ['-m', 'pip']:
            return 'pip'
        if args[1:3] == ['-P', '-c']:
            return 'plan'
        return args[6]


@pytest.fixture
def ready(project, tmp_path):
    (project / '.venv/evlet-project.json').write_text(json.dumps({'version': 1}))
    installed(project, '0.4.3')
    units = tmp_path / 'systemd'
    units.mkdir()
    (units / 'evlet-acme.service').write_text(proxy.systemd_unit(project, 'acme', 8010, 'evlet'))
    return project, units


def call(root, units, server=None, answer='y', **values):
    server = server or Server(root)
    lines = []
    values = {'interactive': True, 'ask': lambda question: lines.append(question) or answer, **values}
    result = upgrading.upgrade(root, 'acme', unit_dir=units, run=server.run, which=lambda name: '/usr/bin/' + name,
                               out=lines.append, **values)
    return result, lines, server


def test_zero_x_stays_within_its_minor_and_later_releases_within_their_major():
    assert upgrading.allowed('0.4.3') == 'evlet>=0.4.3,<0.5'
    assert upgrading.allowed('1.2.0') == 'evlet>=1.2.0,<2'
    assert upgrading.allowed('0.4.3', '0.5.0') == 'evlet==0.5.0'
    with pytest.raises(ValueError, match='older than 0.4.3'):
        upgrading.allowed('0.4.3', '0.4.2')
    with pytest.raises(ValueError, match='--to takes a version'):
        upgrading.allowed('0.4.3', 'latest')


def test_stage_plan_backup_stop_publish_migrate_restart(ready):
    root, units = ready
    result, lines, server = call(root, units)
    python = str(setting.python_in(root))
    evlet = [python, '-P', '-m', 'evlet', '--project', str(root)]
    staged_python = server.ran[0][0]
    assert staged_python != python and '.evlet-upgrade-' in staged_python
    assert result is True
    assert server.ran == [
        [staged_python, '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'evlet>=0.4.3,<0.5'],
        [staged_python, '-P', '-c', upgrading.PLAN, str(root), 'acme'],
        ['sudo', 'systemctl', 'stop', 'evlet-acme'],
        [*evlet, 'backup', '--instance', 'acme'],
        [*evlet, 'migrate', 'acme'],
        [*evlet, 'restart', 'acme', '--yes'],
    ]
    assert setting.environment_version(root) == '0.4.4'
    assert not (root / '.evlet-upgrade.json').exists()
    assert not list(root.glob('.venv-before-*'))
    assert 'Stop, back up, migrate and restart acme on Evlet 0.4.4? [y/N] ' in lines


def test_declining_after_plan_keeps_the_live_environment_and_service(ready):
    root, units = ready
    result, lines, server = call(root, units, answer='n')
    assert result is False and len(server.ran) == 2
    assert setting.environment_version(root) == '0.4.3'
    assert lines[-1] == 'Upgrade cancelled. The project environment and services were left unchanged.'


def test_yes_asks_nothing_and_another_service_is_passed_to_restart(ready):
    root, units = ready
    (units / 'web.service').write_text((units / 'evlet-acme.service').read_text())
    result, lines, server = call(root, units, yes=True, service='web', ask=None, interactive=False)
    assert result is True
    assert server.ran[2] == ['sudo', 'systemctl', 'stop', 'web'] and server.ran[-1][-3:] == ['--yes', '--service', 'web']


def test_without_a_terminal_it_needs_yes_and_changes_nothing(ready):
    root, units = ready
    result, lines, server = call(root, units, interactive=False)
    assert result is False and server.ran == []
    assert lines == ['No terminal to confirm the upgrade. Run evlet upgrade acme --yes to upgrade without asking.']


def test_a_failed_pip_changes_nothing(ready):
    root, units = ready
    result, lines, server = call(root, units, server=Server(root, fail='pip'))
    assert result is False and len(server.ran) == 1
    assert lines[-1] == 'The staged installation failed. The project environment and services were left unchanged.'
    assert setting.environment_version(root) == '0.4.3'
    assert not (root / '.evlet-upgrade.json').exists()
    assert not list(root.glob('.evlet-upgrade-*'))


def test_no_newer_release_stops_after_pip(ready):
    root, units = ready
    result, lines, server = call(root, units, server=Server(root, newest='0.4.3'))
    assert result is True and len(server.ran) == 1
    assert lines[-1] == 'Evlet 0.4.3 is already the newest release allowed by evlet>=0.4.3,<0.5. Nothing else was done.'


def test_a_failed_migration_retains_previous_environment_and_recovery_record(ready):
    root, units = ready
    result, lines, server = call(root, units, server=Server(root, fail='migrate'))
    assert result is False and server.ran[-1][-2:] == ['migrate', 'acme']
    record = json.loads((root / '.evlet-upgrade.json').read_text())
    assert record['before'] == '0.4.3' and record['after'] == '0.4.4'
    assert Path(record['previous_environment']).is_dir()
    assert record['backups']['acme'] == '/srv/b/20261113.sqlite3'
    assert any('restore /srv/b/20261113.sqlite3 --instance acme --yes acme' in line for line in record['recovery'])
    assert not any(Server.kind(command) == 'restart' for command in server.ran)


def test_failed_plan_or_backup_leaves_live_environment_untouched(ready):
    root, units = ready
    for step in ('plan', 'backup'):
        result, lines, server = call(root, units, server=Server(root, fail=step))
        assert result is False
        if step == 'plan':
            assert ['sudo', 'systemctl', 'stop', 'evlet-acme'] not in server.ran
        else:
            assert server.ran[-1] == ['sudo', 'systemctl', 'start', 'evlet-acme']
        assert setting.environment_version(root) == '0.4.3'
        assert not (root / '.evlet-upgrade.json').exists()


def test_a_failed_restart_prints_the_rollback(ready):
    root, units = ready
    result, lines, server = call(root, units, server=Server(root, fail='restart'))
    assert result is False and 'The restart for acme failed. The upgrade stopped here.' in lines
    assert any('restore /srv/b/20261113.sqlite3 --instance acme --yes acme' in line for line in lines)


def test_it_refuses_before_pip_without_a_unit_a_project_venv_or_the_instance(ready):
    root, units = ready
    (units / 'evlet-acme.service').unlink()
    with pytest.raises(ValueError, match='Service acme: .*evlet-acme.service is not installed'):
        call(root, units)
    with pytest.raises(ValueError, match='no instance named shop'):
        upgrading.upgrade(root, 'shop', unit_dir=units, run=None)
    (root / '.venv/evlet-project.json').unlink()
    with pytest.raises(ValueError, match='no .venv made by evlet setup'):
        call(root, units)


def test_the_environment_version_is_read_from_its_dist_info(ready):
    root, _ = ready
    assert setting.environment_version(root) == '0.4.3'
    installed(root, '0.4.4')
    assert setting.environment_version(root) == '0.4.4'
    assert setting.environment_version(root.parent) is None


def test_delegation_warns_when_the_project_venv_has_another_version(ready, monkeypatch, capsys):
    root, _ = ready
    executable = setting.python_in(root)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text('')
    monkeypatch.setattr(os, 'execve', lambda path, argv, env: None)
    monkeypatch.delenv('EVLET_DELEGATED_PROJECT', raising=False)
    monkeypatch.setattr(framework, 'version', lambda: '0.4.4')
    setting.delegate(root, ['serve', 'acme'])
    assert capsys.readouterr().err == (f'This evlet is 0.4.4, but the project in {root} has Evlet 0.4.3 in its .venv. '
                                       'The command runs with 0.4.3. Run evlet upgrade INSTANCE to update the project.\n')
    installed(root, '0.4.4')
    setting.delegate(root, ['serve', 'acme'])
    assert capsys.readouterr().err == ''


def test_version_prints_this_evlet_and_the_project_venv(ready, monkeypatch, capsys):
    root, _ = ready
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit) as stopped:
        main(['--version'])
    assert stopped.value.code == 0
    assert capsys.readouterr().out == f'evlet {framework.version()}\nProject {root}: Evlet 0.4.3 in .venv\n'


def test_upgrade_needs_an_instance_and_checks_to(ready, capsys):
    root, units = ready
    with pytest.raises(SystemExit):
        main(['upgrade', 'acme', '--to', 'soon', '--project', str(root)])
    assert '--to takes a version' in capsys.readouterr().err


def from_source(root, editable=True, place=None):
    """Record a source install the way pip does: direct_url.json in the dist-info."""
    place = place or root.parent / 'evlet-clone'
    info = next((root / '.venv/lib/python3.12/site-packages').glob('evlet-*.dist-info'))
    (info / 'direct_url.json').write_text(json.dumps({'url': place.as_uri(), 'dir_info': {'editable': True} if editable else {}}))
    return place


def test_the_source_of_an_editable_or_git_install_is_read_from_direct_url(ready):
    root, _ = ready
    assert setting.environment_source(root) is None
    place = from_source(root)
    assert setting.environment_source(root) == (str(place), True)
    from_source(root, editable=False)
    assert setting.environment_source(root) == (str(place), False)
    info = next((root / '.venv/lib/python3.12/site-packages').glob('evlet-*.dist-info'))
    (info / 'direct_url.json').write_text(json.dumps({'url': 'git+https://github.com/evlet-io/evlet', 'vcs_info': {'vcs': 'git', 'commit_id': 'abc'}}))
    assert setting.environment_source(root) == ('git+https://github.com/evlet-io/evlet', False)
    # A release pip downloaded as an archive is not source.
    (info / 'direct_url.json').write_text(json.dumps({'url': 'https://files.example/evlet-0.4.3.tar.gz', 'archive_info': {}}))
    assert setting.environment_source(root) is None


def test_an_editable_install_skips_pip_and_still_stops_backs_up_migrates_and_restarts(ready):
    root, units = ready
    place = from_source(root)
    result, lines, server = call(root, units)
    evlet = [str(setting.python_in(root)), '-P', '-m', 'evlet', '--project', str(root)]
    assert result is True
    assert server.ran == [
        ['sudo', 'systemctl', 'stop', 'evlet-acme'],
        [*evlet, 'backup', '--instance', 'acme'],
        [*evlet, 'migrate', 'acme'],
        [*evlet, 'restart', 'acme', '--yes'],
    ]
    assert lines[0] == (f'Evlet is installed from source at {place}, so evlet upgrade does not change the framework. '
                        'Update that code with git. Backing up and migrating the instances on the code that is there now.')
    assert 'Stop, back up, migrate and restart acme? [y/N] ' in lines
    assert setting.environment_version(root) == '0.4.3' and not list(root.glob('.evlet-upgrade-*'))
    assert lines[-1] == f'acme migrated on Evlet from {place}. Backups: acme: /srv/b/20261113.sqlite3.'


def test_a_source_install_refuses_to_and_names_git_and_a_failed_migration_names_restore(ready):
    root, units = ready
    place = from_source(root, editable=False)
    with pytest.raises(ValueError, match=f'installed from source at {place}, so --to does not apply'):
        call(root, units, to='0.4.4')
    result, lines, server = call(root, units, server=Server(root, fail='migrate'), yes=True)
    assert result is False and 'reinstall it with pip install' in lines[0]
    assert [Server.kind(args) for args in server.ran] == ['stop', 'backup', 'migrate']
    assert lines[-1] == ('The migration for acme failed. The upgrade stopped here with the services stopped. '
                         'To go back, restore the backups: evlet restore /srv/b/20261113.sqlite3 --instance acme.')
    result, lines, _ = call(root, units, answer='n')
    assert result is False and lines[-1] == 'Upgrade cancelled. Nothing was changed.'


def test_an_editable_development_project_backs_up_and_migrates_a_real_database(ready):
    """No systemd unit (a dev machine): no service steps; the real backup and migrate run on SQLite."""
    from evlet.cli.install import install
    from test_cli_lifecycle import runtime
    root, units = ready
    (units / 'evlet-acme.service').unlink()
    installed(root, framework.version())
    from_source(root)
    install(root, 'acme', app_name='notes', trust=True)
    app = runtime(root)
    app.database.create(app.registry.models['notesitem'], title='Original')
    app.database.close()
    # A real project interpreter: a venv that sees this Evlet and its dependencies, like an editable install.
    import site, venv
    venv.EnvBuilder(with_pip=False).create(root / '.venv')
    (root / f'.venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/evlet-source.pth').write_text(
        '\n'.join([str(Path(upgrading.__file__).parents[2]), *site.getsitepackages()]) + '\n')
    ran = []

    def real(args, **values):
        ran.append(args)
        return subprocess.run(args, **values)

    lines = []
    result = upgrading.upgrade(root, 'acme', yes=True, unit_dir=units, run=real, which=lambda name: None, out=lines.append)
    assert result is True, lines
    assert [args[6] for args in ran] == ['backup', 'migrate']
    backup = lines[-1].removeprefix('acme migrated on Evlet from ').split('Backups: acme: ')[1].rstrip('.')
    assert Path(backup).is_file() and Path(backup).parent == root / 'instances/acme/data/backups'
    app = runtime(root)
    assert [record.title for record in app.database.list(app.registry.models['notesitem'])] == ['Original']
