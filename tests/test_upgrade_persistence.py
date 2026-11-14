"""Shared-environment upgrade scope, live planning and failure recovery."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
import subprocess
import sys

import pytest

from evlet.cli import proxy
from evlet.cli.install import install, plan
from evlet.cli.instance import create_instance
from evlet.cli.project import instance_guard
from evlet.cli import upgrade as upgrading
from evlet.cli.setup import environment_version
from test_cli_upgrade import ready, call, Server, installed  # noqa: F401
from test_cli_lifecycle import environment_stub, project  # noqa: F401


def sibling(root, units):
    create_instance(root, 'shop')
    data = root / 'instances/shop/data'
    data.mkdir()
    (data / 'database.sqlite3').touch()
    (units / 'evlet-shop.service').write_text(proxy.systemd_unit(root, 'shop', 8011, 'evlet'))


def test_staged_plan_works_while_a_real_service_lease_is_held(project):
    install(project, 'acme', app_name='notes', trust=True)
    with instance_guard(project / 'instances/acme', serving=True):
        result, _, _, steps = plan(project, 'acme')
    assert not result.pending and not steps


def test_shared_environment_refuses_single_scope_before_pip(ready):
    root, units = ready
    sibling(root, units)
    server = Server(root)
    with pytest.raises(ValueError, match='--all-instances'):
        call(root, units, server=server)
    assert not server.ran and environment_version(root) == '0.4.3'


def test_shared_environment_plans_and_migrates_every_instance_before_restart(ready):
    root, units = ready
    sibling(root, units)
    result, _, server = call(root, units, all_instances=True)
    assert result
    assert [Server.kind(args) for args in server.ran] == [
        'pip', 'plan', 'plan', 'stop', 'stop', 'backup', 'backup', 'migrate', 'migrate', 'restart', 'restart']
    assert [args[-1] for args in server.ran if Server.kind(args) == 'plan'] == ['acme', 'shop']
    assert [args[-1] for args in server.ran if Server.kind(args) == 'migrate'] == ['acme', 'shop']
    assert environment_version(root) == '0.4.4'


def test_every_service_is_validated_before_staging_pip(ready):
    root, units = ready
    sibling(root, units)
    (units / 'evlet-shop.service').unlink()
    server = Server(root)
    with pytest.raises(ValueError, match='Service shop'):
        call(root, units, server=server, all_instances=True)
    assert not server.ran


def test_second_migration_failure_preserves_all_backups_and_restarts_nobody(ready):
    root, units = ready
    sibling(root, units)
    class FailSecond(Server):
        def run(self, args, **values):
            if self.kind(args) == 'migrate' and args[-1] == 'shop':
                self.ran.append(args)
                return subprocess.CompletedProcess(args, 1, stdout='', stderr='')
            return super().run(args, **values)
    server = FailSecond(root)
    result, _, _ = call(root, units, server=server, all_instances=True)
    assert not result
    assert not any(Server.kind(args) == 'restart' for args in server.ran)
    record = json.loads((root / '.evlet-upgrade.json').read_text())
    assert set(record['backups']) == {'acme', 'shop'}
    assert Path(record['previous_environment']).is_dir()
    with pytest.raises(ValueError, match='interrupted project upgrade'):
        call(root, units, all_instances=True)


def test_project_upgrade_lock_excludes_another_process_before_staging(ready):
    root, units = ready
    child = subprocess.Popen([sys.executable, '-c', '''
import sys
from evlet.writes import file_lock
with file_lock(sys.argv[1]):
    print('locked', flush=True)
    sys.stdin.readline()
''', str(root / '.evlet-upgrade.lock')], text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == 'locked'
        server = Server(root)
        with pytest.raises(ValueError, match='in use'):
            call(root, units, server=server)
        assert not server.ran
    finally:
        child.stdin.write('done\n')
        child.stdin.flush()
        assert child.wait(timeout=5) == 0


def test_pip_entry_points_survive_environment_directory_publication(tmp_path):
    staged, live = tmp_path / 'stage with space/.venv', tmp_path / '.venv'
    folder = staged / 'bin'
    folder.mkdir(parents=True)
    plain = folder / 'evlet'
    plain.write_text(f'#!{staged}/bin/python\nprint("plain")\n')
    wrapped = folder / 'wrapped'
    wrapped.write_text(f'#!/bin/sh\n\'\'\'exec\' "{staged}/bin/python" "$0" "$@"\n\' \'\'\'\nprint("wrapped")\n')
    upgrading.relocate_scripts(staged, live)
    assert str(staged) not in plain.read_text() and str(live) in plain.read_text()
    assert str(staged) not in wrapped.read_text() and str(live) in wrapped.read_text()


def test_rollback_snapshot_includes_the_last_write_before_service_stop(ready):
    root, units = ready
    main = root / 'instances/acme/data/database.sqlite3'
    main.parent.mkdir()
    with closing(sqlite3.connect(main)) as connection:
        connection.execute('CREATE TABLE accepted(n)')
        connection.execute('INSERT INTO accepted VALUES (1)')
        connection.commit()
    captured = []
    class LastWrite(Server):
        def run(self, args, **values):
            if self.kind(args) == 'stop':
                with closing(sqlite3.connect(main)) as connection:
                    connection.execute('INSERT INTO accepted VALUES (2)')
                    connection.commit()
            elif self.kind(args) == 'backup':
                with closing(sqlite3.connect(main)) as connection:
                    captured.extend(row[0] for row in connection.execute('SELECT n FROM accepted ORDER BY n'))
            return super().run(args, **values)
    result, _, _ = call(root, units, server=LastWrite(root, fail='migrate'))
    assert not result and captured == [1, 2]


def test_failure_before_environment_rename_keeps_original_release_retryable(ready, monkeypatch):
    root, units = ready
    original = upgrading.os.replace
    def fail(source, target):
        if Path(source) == root / '.venv':
            raise OSError('environment rename failed')
        return original(source, target)
    monkeypatch.setattr(upgrading.os, 'replace', fail)
    result, _, server = call(root, units)
    assert not result and environment_version(root) == '0.4.3'
    assert not (root / '.evlet-upgrade.json').exists()
    assert server.ran[-1] == ['sudo', 'systemctl', 'start', 'evlet-acme']
    monkeypatch.setattr(upgrading.os, 'replace', original)
    assert call(root, units)[0]
