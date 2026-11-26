"""The lifecycle core (evlet.cli.core): each function called directly on a real project and SQLite instance.

Every test runs with stdin that fails when read and checks that nothing reached stdout, so the core can serve
callers without a terminal. Services are the fake systemd and pip of the restart and upgrade tests.
"""

import builtins
import sys

import pytest

from evlet.cli import core, setup as setting
from evlet.cli.app import create_app as create_flat_app
from evlet.cli.install import install
from evlet.instances import read_lock
from test_batch3_dx import SALES, forget
from test_cli_hosts import routes_text, web  # noqa: F401  (fixture)
from test_cli_lifecycle import change_model, environment_stub, project, runtime  # noqa: F401  (fixtures)
from test_cli_restart import Clock, Server as Systemd, served  # noqa: F401  (fixture)
from test_cli_upgrade import Server as Pip, ready  # noqa: F401  (fixture)


class NoStdin:
    """A terminal that fails the test when the core reads from it."""

    def isatty(self):
        return True

    def read(self, *args):
        raise AssertionError('the lifecycle core read stdin')

    readline = read


@pytest.fixture(autouse=True)
def quiet(monkeypatch, capfd):
    monkeypatch.setattr(sys, 'stdin', NoStdin())
    monkeypatch.setattr(builtins, 'input', NoStdin().read)
    yield
    assert capfd.readouterr().out == '', 'the lifecycle core wrote to stdout'


def streamed():
    lines = []
    return lines, lines.append


def test_install_and_migrate_return_the_report_the_cli_prints(project):
    with pytest.raises(ValueError, match='requires explicit trust'):
        core.install_app(project, 'acme', 'notes')
    asked = []
    lines, out = streamed()
    outcome = core.install_app(project, 'acme', 'notes', confirm=lambda question: asked.append(question) or True, out=out)
    assert outcome.ok and len(asked) == 1 and 'notes' in asked[0]
    assert outcome.messages[0] == 'Instance acme: installed notes' and list(outcome.messages) == lines
    assert outcome.before == {'apps': {}} and outcome.after == read_lock(project / 'instances/acme')
    assert list(outcome.after['apps']) == ['notes'] and outcome.value['hosts'] == []
    assert outcome.backup is None and 'framework' in outcome.steps

    change_model(project)
    forget('notes')
    outcome = core.migrate(project, 'acme', trust=True)
    assert outcome.ok and outcome.steps[:2] == ('backup', 'migrations')
    assert outcome.messages[0] == 'Instance acme: apps notes'
    assert 'Applied migrations: notes 0002_done' in outcome.messages
    assert outcome.messages[-1] == 'Backup: ' + outcome.backup
    assert outcome.before['apps']['notes'] != outcome.after['apps']['notes']
    assert outcome.after == read_lock(project / 'instances/acme')
    from pathlib import Path
    assert Path(outcome.backup).is_file() and Path(outcome.backup).parent == project / 'instances/acme/data/backups'


def test_plan_writes_nothing_and_makemigration_writes_the_suggestion(project):
    install(project, 'acme', app_name='notes', trust=True)
    create_flat_app(project, 'sales')
    (project / 'apps/sales/models/item.py').unlink()
    (project / 'apps/sales/models/sales.py').write_text(SALES)
    core.install_app(project, 'acme', 'sales', trust=True, hosts='skip')
    forget('notes', 'sales')
    (project / 'apps/sales/models/sales.py').write_text(SALES + '    notes = Text()\n')
    database = (project / 'instances/acme/data/database.sqlite3').read_bytes()
    outcome = core.migration_plan(project, 'acme', trust=True)
    assert outcome.messages[0] == 'Instance acme: plan only; nothing was written'
    assert 'Or write it with: evlet makemigration sales --instance acme' in outcome.messages
    plan, suggestions, _, _ = outcome.value
    assert plan.differences and suggestions['sales'][0] == '0001_changes'
    assert outcome.before == outcome.after and (project / 'instances/acme/data/database.sqlite3').read_bytes() == database
    forget('notes', 'sales')
    outcome = core.make_migration(project, 'sales', 'acme', trust=True)
    target, review = outcome.value
    assert target == project / 'apps/sales/migrations/m0001_changes.py' and target.is_file() and not review
    assert outcome.messages == (f'Wrote {target}', 'Review it, then run evlet migrate acme.')


def test_backup_list_and_restore(project):
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    app.database.create(app.registry.models['notesitem'], title='Kept')
    app.database.close()
    outcome = core.backup_create(project, 'acme')
    assert outcome.ok and outcome.steps == ('backup',) and outcome.messages[0] == 'Backup: ' + outcome.backup
    assert outcome.value[0].name == outcome.backup.rsplit('/', 1)[1] and len(outcome.value) == 4
    assert core.backup_list(project, 'acme').value[0] == outcome.value[0]
    app = runtime(project)
    app.database.create(app.registry.models['notesitem'], title='Lost')
    app.database.close()
    with pytest.raises(ValueError, match='Restore cancelled; nothing was changed'):
        core.backup_restore(project, 'acme', outcome.backup)
    with pytest.raises(ValueError, match='Restore cancelled'):
        core.backup_restore(project, 'acme', outcome.backup, confirm=lambda question: False)
    restored = core.backup_restore(project, 'acme', outcome.backup, confirm=lambda question: question.endswith('to restore: '))
    assert restored.ok and restored.steps == ('safety copy', 'restore')
    assert restored.messages[0] == f'Instance acme: restored from {outcome.backup}'
    safety, single = restored.value
    assert restored.backup == str(safety) and safety.is_file() and not single
    assert restored.before == restored.after
    listed = core.backup_list(project, 'acme').value
    assert safety in listed and outcome.value[0] in listed and not any(item.name.endswith('-sessions.sqlite3') for item in listed)
    app = runtime(project)
    try:
        assert [record.title for record in app.database.list(app.registry.models['notesitem'])] == ['Kept']
    finally:
        app.database.close()
    with pytest.raises(ValueError, match='Instance does not exist'):
        core.backup_list(project, 'nobody')


def restart(root, units, server, **values):
    clock = Clock()
    return core.restart_instance(root, 'acme', unit_dir=units, run=server.run, which=lambda name: '/usr/bin/' + name,
                                 fetch=server.fetch, sleep=clock.sleep, clock=clock, **values)


def test_restart_needs_yes_or_confirm_and_captures_systemctl(served):
    root, units = served
    server = Systemd()
    outcome = restart(root, units, server, check_only=True)
    assert outcome.ok and outcome.steps == () and server.ran == []
    assert outcome.messages[-1] == 'All checks passed. Run evlet restart acme to restart evlet-acme.'
    outcome = restart(root, units, server)
    assert not outcome.ok and server.ran == []
    assert outcome.messages[-1] == 'No terminal to confirm the restart. Run evlet restart acme --yes to restart without asking.'
    asked = []
    outcome = restart(root, units, server, confirm=lambda question: asked.append(question) or False)
    assert not outcome.ok and asked == ['Restart evlet-acme now? [y/N] '] and outcome.messages[-1] == 'Nothing was restarted.'
    calls = []
    outcome = restart(root, units, Systemd(), yes=True, out=calls.append)
    assert outcome.ok and outcome.steps == ('sudo systemctl restart evlet-acme',)
    assert outcome.messages[-1] == 'acme is back up on 127.0.0.1:8010' and tuple(calls) == outcome.messages
    assert outcome.before == outcome.after == read_lock(root / 'instances/acme')
    with pytest.raises(ValueError, match='--timeout between 1 and 3600'):
        restart(root, units, server, yes=True, timeout=0)


def test_restart_reports_failed_checks(served):
    root, units = served
    (root / 'instances/acme/apps.lock').write_text('{}')
    outcome = restart(root, units, Systemd(), yes=True)
    assert not outcome.ok and outcome.before is None
    assert 'Service: ok (' + str(units / 'evlet-acme.service') + ', port 8010)' in outcome.messages
    assert outcome.messages[-1] == 'Checks failed. evlet-acme was not restarted and keeps running as it was.'


class Captured(Pip):
    """The fake pip and systemd, which also record that each command's output was captured."""

    def __init__(self, root):
        super().__init__(root)
        self.captured = []

    def run(self, args, **values):
        self.captured.append(values.get('capture_output', False))
        return super().run(args, **values)


def upgrade(root, units, server, **values):
    return core.upgrade_evlet(root, 'acme', unit_dir=units, run=server.run, which=lambda name: '/usr/bin/' + name, **values)


def test_upgrade_declined_is_a_plan_and_yes_upgrades_with_every_command_captured(ready):
    root, units = ready
    server = Captured(root)
    outcome = upgrade(root, units, server)
    assert not outcome.ok and server.ran == []
    assert outcome.messages[-1] == 'No terminal to confirm the upgrade. Run evlet upgrade acme --yes to upgrade without asking.'
    asked = []
    outcome = upgrade(root, units, server, confirm=lambda question: asked.append(question) or False)
    assert not outcome.ok and asked == ['Stop, back up, migrate and restart acme on Evlet 0.4.4? [y/N] ']
    assert [Pip.kind(args) for args in server.ran] == ['pip', 'plan'] and setting.environment_version(root) == '0.4.3'
    assert outcome.messages[-1] == 'Upgrade cancelled. The project environment and services were left unchanged.'
    server = Captured(root)
    outcome = upgrade(root, units, server, yes=True)
    assert outcome.ok and setting.environment_version(root) == '0.4.4'
    assert [Pip.kind(args) for args in server.ran] == ['pip', 'plan', 'stop', 'backup', 'migrate', 'restart']
    assert all(server.captured) and len(outcome.steps) == 6 and outcome.steps[2] == 'sudo systemctl stop evlet-acme'
    assert outcome.backup == '/srv/b/20261113.sqlite3' and outcome.value == {'acme': '/srv/b/20261113.sqlite3'}


def test_hosts_add_list_check_and_remove(web):
    install(web, 'acme', app_name='notes', trust=True)
    checked = core.host_check(web, 'acme', 'shop.acme.com', 'notes.www')
    assert checked.ok and 'shop.acme.com' in checked.messages[0] and not (web / 'instances/acme/routes.py').exists()
    with pytest.raises(ValueError, match='is the primary host'):
        core.host_check(web, 'acme', 'erp.acme.com')
    outcome = core.host_add(web, 'acme', 'shop.acme.com', 'notes.www', routes_only=True)
    assert outcome.steps == ('host shop.acme.com',)
    assert outcome.messages[-2] == 'Added shop.acme.com to instances/acme/routes.py, served by notes.www'
    assert 'Host("shop.acme.com", root="notes.www")' in routes_text(web)
    listed = core.host_list(web, 'acme').messages[0]
    assert 'shop.acme.com' in listed and 'notes.www' in listed
    outcome = core.host_remove(web, 'acme', 'shop.acme.com', routes_only=True)
    assert outcome.messages == ('Removed shop.acme.com from instances/acme/routes.py',
                                'Restart the instance to stop serving it: evlet restart acme')
    assert 'shop.acme.com' not in routes_text(web)


def test_install_takes_or_offers_suggested_hosts_without_a_terminal(web):
    outcome = core.install_app(web, 'acme', 'notes', trust=True)
    assert outcome.value['hosts'] == [] and outcome.messages[-1] == (
        'notes.www suggests the host www.erp.acme.com. Serve it with: evlet host add acme www.erp.acme.com --root notes.www')
    with pytest.raises(ValueError, match="hosts is 'offer', 'take' or 'skip'"):
        core.install_app(web, 'acme', 'notes', trust=True, hosts='yes')


def test_install_asks_for_each_suggested_host(web):
    asked = []
    outcome = core.install_app(web, 'acme', 'notes', trust=True, ask=lambda question: asked.append(question) or '')
    assert outcome.value['hosts'] == ['www.erp.acme.com'] and outcome.steps[-1] == 'host www.erp.acme.com'
    assert asked == ['Serve notes.www on its own host? Press Enter for www.erp.acme.com, type your own domain, or type no: ']
    assert 'Host("www.erp.acme.com", root="notes.www")' in routes_text(web)


def test_apps_instances_and_builds(project):
    created = core.app_create(project, 'sales')
    assert created.value == project / 'apps/sales' and created.messages == (f'App created: {created.value}',)
    instance = core.instance_create(project, 'shop')
    assert instance.value == project / 'instances/shop' and instance.after == {'apps': {}}
    with pytest.raises(ValueError, match='Build requires explicit trust'):
        core.build(project)
    asked = []
    built = core.build(project, confirm=lambda question: asked.append(question) or True)
    assert asked == ['Trust and build these local applications: notes, sales?']
    assert built.messages == ('Built generation: ' + built.value['fingerprint'],)
