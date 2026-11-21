"""Developer experience: evlet.testing on a scaffolded app, installing a second app, the instance argument and grouped help."""

import sqlite3
import subprocess
import sys

import pytest

from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install, migration_label
from evlet.cli.main import main, move_instance_option, parser
from evlet.cli.setup import setup
from evlet.instances import load_runtime
from evlet.testing import TestApp

SALES = '''from evlet import Currency, Model, Rule, Text


class Customer(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    customer_name = Text(required=True, max_length=120)


class Invoice(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    reference = Text(required=True)
    total = Currency()
'''


@pytest.fixture(autouse=True)
def environment_stub(monkeypatch):
    import importlib
    module = importlib.import_module('evlet.cli.setup')
    monkeypatch.setattr(module, 'create_environment', lambda root, framework: (root / '.venv').mkdir())


def forget(*names):
    for name in list(sys.modules):
        if name in names or name.startswith(tuple(item + '.' for item in names)):
            del sys.modules[name]


@pytest.fixture
def project(tmp_path):
    root = setup(tmp_path / 'project').resolve()
    create_app(root, 'notes')
    create_instance(root, 'acme')
    yield root
    forget('notes', 'sales')


def tables(root):
    connection = sqlite3.connect(root / 'instances/acme/data/database.sqlite3')
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}, \
            [row[0] for row in connection.execute('SELECT name FROM evlet_migrations ORDER BY position')]
    finally:
        connection.close()


# Second app on an instance that already has one

def test_a_new_app_on_an_installed_instance_gets_its_tables_like_a_first_install(project, capsys):
    install(project, 'acme', app_name='notes', trust=True)
    create_app(project, 'sales')
    (project / 'apps/sales/models/item.py').unlink()
    (project / 'apps/sales/models/sales.py').write_text(SALES)
    main(['install', 'sales', '--instance', 'acme', '--trust', '--no-host', '--project', str(project)])
    out = capsys.readouterr().out
    assert 'Instance acme: installed notes, sales' in out
    assert 'Tables created from the models of sales.' in out and 'evlet makemigration APP --instance acme' in out
    assert 'Backup: ' in out
    found, history = tables(project)
    assert {'notesitem', 'customer', 'invoice'} <= found and history == []
    forget('notes', 'sales')
    app = load_runtime(project, 'acme')
    try:
        app.initialize()
        from evlet import User
        with app.business.context(User(1, 'admin', 'admin')) as ctx:
            customer = ctx.create(app.registry.models['customer'], customer_name='Studio')
            assert ctx.create(app.registry.models['invoice'], reference='INV-1', total='12.50')['total'] == '12.50'
            assert customer['customer_name'] == 'Studio'
    finally:
        app.database.close()
    # From now on a model change needs a migration, written by makemigration for the installed app.
    forget('notes', 'sales')
    (project / 'apps/sales/models/sales.py').write_text(SALES + '    notes = Text()\n')
    main(['makemigration', 'sales', 'acme', '--trust', '--project', str(project)])
    assert (project / 'apps/sales/migrations/m0001_changes.py').is_file()
    capsys.readouterr()
    forget('notes', 'sales')
    main(['migrate', '--instance', 'acme', '--trust', '--project', str(project)])
    out = capsys.readouterr().out
    assert 'Instance acme: apps notes, sales' in out and 'Applied migrations: sales 0001_changes' in out
    assert 'Tables created' not in out
    assert tables(project)[1] == ['app5_sales__0001_changes']


def test_a_new_app_with_migrations_records_them_without_running_them(project):
    install(project, 'acme', app_name='notes', trust=True)
    create_app(project, 'sales')
    (project / 'apps/sales/models/item.py').unlink()
    (project / 'apps/sales/models/sales.py').write_text(SALES)
    # An app that already shipped migrations elsewhere: its first one creates a table the models already have.
    (project / 'apps/sales/migrations').mkdir()
    (project / 'apps/sales/migrations/__init__.py').write_text('')
    (project / 'apps/sales/migrations/m0001_add.py').write_text(
        'from evlet import AddField, Migration, Text\n\nmigration = Migration("0001_add", AddField("customer", "unused", Text()))\n')
    install(project, 'acme', app_name='sales', trust=True)
    found, history = tables(project)
    assert {'customer', 'invoice'} <= found and history == ['app5_sales__0001_add']
    connection = sqlite3.connect(project / 'instances/acme/data/database.sqlite3')
    try:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(customer)')}
    finally:
        connection.close()
    assert 'customer_name' in columns and 'unused' not in columns


def test_makemigration_for_an_app_that_is_not_installed_says_to_install_it(project, capsys):
    install(project, 'acme', app_name='notes', trust=True)
    create_app(project, 'sales')
    with pytest.raises(SystemExit):
        main(['makemigration', 'sales', '--instance', 'acme', '--trust', '--project', str(project)])
    assert 'Install it first with evlet install sales --instance acme' in capsys.readouterr().err


def test_first_install_summarises_the_framework_steps(project, capsys):
    main(['install', 'notes', 'acme', '--trust', '--no-host', '--project', str(project)])
    out = capsys.readouterr().out
    assert 'Tables created from the models of notes.' in out
    assert 'Framework tables: created (' in out and 'Framework migrations:' not in out


def test_migration_labels_drop_the_internal_prefix():
    assert migration_label('app5_notes__0001_changes') == 'notes 0001_changes'
    assert migration_label('app9_long_name__0002_x') == 'long_name 0002_x'
    assert migration_label('0001_initial') == '0001_initial'


# Instance as a name or as --instance

@pytest.mark.parametrize('values,expected', [
    (['dev', 'acme'], 'acme'), (['dev', '--instance', 'acme'], 'acme'),
    (['install', 'notes', 'acme'], 'acme'), (['install', 'notes', '--instance', 'acme'], 'acme'),
    (['makemigration', 'notes', 'acme'], 'acme'), (['backup', 'acme'], 'acme'), (['jobs', 'list', 'acme'], 'acme'),
    (['ledger', 'list', '--instance', 'acme'], 'acme'), (['restore', 'set.sqlite3', 'acme'], 'acme'),
    (['serve', 'acme'], 'acme'), (['serve', '--instance', 'acme'], 'acme'), (['serve', '--instance=acme'], 'acme'),
    (['migrate', '--instance', 'acme', '--plan'], 'acme'), (['routes', '--instance', 'acme'], 'acme'),
    (['users', 'set-password', '--instance', 'acme', 'mariam'], 'acme'),
    (['--project', 'p', 'link-user', '--instance', 'acme', 'buyer', 'customer', '42'], 'acme'),
    (['host', 'list', '--instance', 'acme'], 'acme'),
])
def test_every_instance_command_takes_the_name_either_way(values, expected):
    args = parser().parse_args(move_instance_option(values))
    instance = getattr(args, 'instance_arg', None) or args.instance
    assert instance == expected
    if values[-3:] == ['buyer', 'customer', '42']:
        assert (args.username, args.model) == ('buyer', 'customer')
    if 'mariam' in values:
        assert args.username == 'mariam'


def test_two_different_instances_are_refused(project, capsys):
    with pytest.raises(SystemExit):
        main(['dev', 'acme', '--instance', 'beta', '--project', str(project)])
    assert 'Two instances given' in capsys.readouterr().err


def test_install_and_migrate_use_the_only_instance(project, capsys):
    main(['install', 'notes', '--trust', '--no-host', '--project', str(project)])
    assert 'Instance acme: installed notes' in capsys.readouterr().out
    forget('notes')
    main(['migrate', '--trust', '--project', str(project)])
    assert 'Instance acme: apps notes' in capsys.readouterr().out


# Grouped help

def test_help_lists_every_command_in_a_group(capsys):
    with pytest.raises(SystemExit):
        main(['--help'])
    out = capsys.readouterr().out
    for title in ('Start:', 'Develop:', 'Change:', 'Operate:', 'Recover:', 'Connector:'):
        assert title in out
    assert out.index('Start:') < out.index('  install ') < out.index('Develop:') < out.index('  dev ') < out.index('Recover:')
    assert '{setup,app' not in out
    assert 'evlet dev acme or evlet dev --instance acme' in out


# evlet.testing on a scaffolded flat app

def test_testing_client_runs_the_real_permission_checks(project):
    with TestApp(project / 'apps/notes') as app:
        directory = app.directory
        editor = app.client(role='editor')
        created = editor.post('/api/notesitem', {'title': 'First'})
        assert created.status == 201 and created.data['_audit']['created_by']['name'] == 'editor'
        assert editor.delete('/api/notesitem/' + created.data['id']).status == 403
        viewer = app.client(role='viewer')
        assert viewer.post('/api/notesitem', {'title': 'No'}).status == 403
        assert [item['title'] for item in viewer.get('/api/notesitem').data['items']] == ['First']
        assert app.client().get('/api/notesitem').status == 401
        refused = app.as_admin().post('/api/notesitem', {})
        assert refused.status == 422 and 'title' in refused.data['fields']
        # Without the request protection header a write is refused, as for a browser.
        bare = app.as_admin()
        assert bare.request('POST', '/api/notesitem', {'title': 'x'}, headers={'x-evlet-request': '0'}).status == 403
        # Signing in goes through the real sign-in route.
        app.user('owner', role='admin', password='a long enough password')
        signed = app.client().sign_in('owner', 'a long enough password')
        assert signed.get('/api/notesitem').status == 200
        with pytest.raises(AssertionError):
            app.client().sign_in('owner', 'wrong password!')
        with app.context(role='admin') as ctx:
            assert ctx.create(app.model('NotesItem'), title='Python')['title'] == 'Python'
        assert app.model('notesitem') is app.model('NotesItem')
    assert not directory.exists()
    # Nothing in the project was written.
    assert not (project / 'instances/acme/data').exists()


def test_testing_with_a_project_and_one_of_its_instances(project):
    config = project / 'instances/acme/config.py'
    config.write_text(config.read_text().replace("domain=None", "domain=None, timezone='Asia/Dubai'"))
    with TestApp(project, instance='acme') as app:
        assert app.apps == ['notes'] and app.business.timezone == 'Asia/Dubai'
        assert app.as_admin().post('/api/notesitem', {'title': 'x'}).status == 201
    assert not (project / 'instances/acme/data').exists()


def test_scaffolded_app_tests_pass_with_pytest(project):
    test_file = project / 'apps/notes/tests/test_notes.py'
    assert test_file.is_file()
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(test_file.parent)],
                            cwd=project, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '3 passed' in result.stdout
