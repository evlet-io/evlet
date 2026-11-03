"""Migration plans, named mismatches and owned tables, through a flat app on an instance (real SQLite)."""

import sqlite3
import sys

import pytest

from evlet import (AddTable, Child, CreateModel, Database, Integer, Migration, Model, Registry, SchemaError, Table, Text,
                   User)
from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.cli.setup import setup
from evlet.instances import load_runtime

ADMIN = User(1, 'admin', 'admin')

MODELS = '''from evlet import Integer, Journal, Model, Rule, Text{imports}

RULES = {{'admin': Rule(('read', 'create', 'update', 'delete'))}}
LIFECYCLE = {{'admin': Rule(('read', 'create', 'update', 'delete', 'confirm', 'cancel', 'reopen'))}}
{child}

class Employee(Model):
    permissions = RULES
    name = Text(required=True, max_length=120)
{employee}

class LeaveApplication(Model):
    confirmable = True
    track_changes = True
    permissions = LIFECYCLE
    employee = Text(required=True)
    days = Integer(default=0)
    reason = Text()

    class Meta:
        journal = Journal(tamper_evident=True)
'''


def write_models(root, *, employee='', child='', imports=''):
    (root / 'apps/people/models/__init__.py').write_text('')
    for path in (root / 'apps/people/models').glob('*.py'):
        if path.name != '__init__.py':
            path.unlink()
    (root / 'apps/people/models/hr.py').write_text(MODELS.format(employee=employee, child=child, imports=imports))


@pytest.fixture(autouse=True)
def environment_stub(monkeypatch):
    import importlib
    module = importlib.import_module('evlet.cli.setup')
    monkeypatch.setattr(module, 'create_environment', lambda root, framework: (root / '.venv').mkdir())


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = setup(tmp_path / 'project').resolve()
    create_app(root, 'people')
    write_models(root)
    create_instance(root, 'acme')
    install(root, 'acme', app_name='people', trust=True)
    monkeypatch.chdir(root)
    yield root
    for name in list(sys.modules):
        if name == 'people' or name.startswith('people.'):
            del sys.modules[name]


def runtime(root):
    for name in list(sys.modules):
        if name == 'people' or name.startswith('people.'):
            del sys.modules[name]
    app = load_runtime(root, 'acme')
    app.initialize()
    return app


def seed(root):
    """An employee and a confirmed leave application with a revision and journal entries."""
    app = runtime(root)
    try:
        with app.business.context(ADMIN) as context:
            employee = context.create(app.registry.models['employee'], name='Mariam')['id']
            document = context.document(app.registry.models['leaveapplication'], employee='Mariam', days=3, reason='Annual')
            document.save()
            document.confirm()
            return employee, document.id
    finally:
        app.database.close()


DEPENDENT = """    class Dependent(Child):
        full_name = Text(required=True, max_length=120)
        age = Integer()

    dependents = Table(Dependent)
"""


def database_bytes(root):
    path = root / 'instances/acme/data/database.sqlite3'
    return path.read_bytes(), sorted(item.name for item in path.parent.iterdir())


def test_plan_names_each_difference_writes_nothing_and_its_suggestion_migrates(project, capsys):
    employee, leave = seed(project)
    write_models(project, employee="    nationality = Text(default='AE')\n" + DEPENDENT, imports=', Child, Table')
    with pytest.raises(SchemaError) as error:
        install(project, 'acme', trust=True)
    assert 'employee.nationality: field is in the models but not in the migrations' in str(error.value)
    assert 'employee_dependents: model is in the models but not in the migrations' in str(error.value)
    before = database_bytes(project)
    lock = (project / 'instances/acme/apps.lock').read_text()

    main(['migrate', 'acme', '--plan', '--trust'])
    output = capsys.readouterr().out
    assert 'plan only; nothing was written' in output
    assert 'employee.nationality: field is in the models but not in the migrations' in output
    assert "AddTable('employee', 'dependents', Dependent)" in output
    assert "AddField('employee', 'nationality', Text(default='AE'))" in output
    assert 'apps/people/migrations/m0001_changes.py' in output
    assert database_bytes(project) == before
    assert (project / 'instances/acme/apps.lock').read_text() == lock

    main(['makemigration', 'people', '--instance', 'acme', '--name', 'dependents', '--trust'])
    written = project / 'apps/people/migrations/m0001_dependents.py'
    assert 'Wrote' in capsys.readouterr().out and written.exists()
    assert "'0001_dependents'" in written.read_text()
    assert database_bytes(project) == before

    main(['migrate', 'acme', '--trust'])
    assert 'app6_people__0001_dependents' in capsys.readouterr().out
    main(['migrate', 'acme', '--plan', '--trust'])
    assert 'The migrations produce the current models.' in capsys.readouterr().out

    app = runtime(project)
    try:
        Employee, Leave = app.registry.models['employee'], app.registry.models['leaveapplication']
        with app.business.context(ADMIN) as context:
            assert context.get(Employee, employee).nationality == 'AE'
            context.update(Employee, employee, children={'dependents': [{'full_name': 'Omar', 'age': 7}]})
        with app.database.transaction() as tx:
            rows = tx.connection.execute('SELECT evlet_owner, full_name, age FROM employee_dependents').fetchall()
        assert [tuple(row) for row in rows] == [(employee, 'Omar', 7)]
        with app.business.context(ADMIN) as context:
            assert context.snapshot(Leave, leave, revision=1)['document']['reason'] == 'Annual'
    finally:
        app.database.close()
    main(['journal', 'verify', 'acme'])


def test_plan_suggests_a_rename_and_flags_removals(project, capsys):
    seed(project)
    text = (project / 'apps/people/models/hr.py').read_text()
    (project / 'apps/people/models/hr.py').write_text(text.replace('    reason = Text()\n', '    purpose = Text()\n'))
    main(['migrate', 'acme', '--plan', '--trust'])
    output = capsys.readouterr().out
    assert 'leaveapplication.reason: field is in the migrations but not in the models' in output
    assert "RenameField('leaveapplication', 'reason', 'purpose')" in output

    (project / 'apps/people/models/hr.py').write_text(text.replace('    reason = Text()\n', ''))
    main(['migrate', 'acme', '--plan', '--trust'])
    output = capsys.readouterr().out
    assert '# leaveapplication.reason: the field was removed' in output


def test_makemigration_refuses_when_nothing_changed(project, capsys):
    with pytest.raises(SystemExit):
        main(['makemigration', 'people', '--instance', 'acme', '--trust'])
    assert 'already produce the current models' in capsys.readouterr().err
    assert not (project / 'apps/people/migrations').exists()


class Order(Model):
    title = Text()


class Line(Child):
    item = Text(required=True)
    quantity = Integer(default=1)


class FrozenLine(Child):
    item = Text(required=True)
    quantity = Integer(default=1)


def orders(lines=False):
    attributes = {'title': Text(), '__module__': __name__}
    if lines:
        attributes['lines'] = Table(Line)
    return Registry(type('Order', (Model,), attributes))


def test_add_table_matches_the_registry_and_keeps_rows(tmp_path):
    path = tmp_path / 'orders.sqlite3'
    old = Database(path, orders(), migrations=[])
    old.initialize()
    old.create(old.registry.models['order'], title='First')
    migration = Migration('0001_lines', AddTable('order', 'lines', FrozenLine))
    new = Database(path, orders(lines=True))
    assert new.plan([migration]).text().endswith('The migrations produce the current models.')
    assert new.migrate([migration]) == ['0001_lines']
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(order_lines)')}
        links = connection.execute('PRAGMA foreign_key_list(order_lines)').fetchall()
    assert columns == {'id', 'item', 'quantity', 'evlet_owner', 'evlet_position'}
    assert [(row[2], row[3]) for row in links] == [('order', 'evlet_owner')]
    Database(path, orders(lines=True), migrations=[migration]).initialize()


def test_add_table_rejects_framework_fields_and_unknown_parents(tmp_path):
    class Owned(Child):
        evlet_position = Integer()

    with pytest.raises(SchemaError, match='framework-managed'):
        Migration('bad', AddTable('order', 'lines', Owned)).checksum()
    with pytest.raises(SchemaError, match='Child subclass'):
        Migration('bad', AddTable('order', 'lines', Order)).checksum()
    path = tmp_path / 'orders.sqlite3'
    Database(path, orders(), migrations=[]).initialize()
    with pytest.raises(SchemaError, match='unknown parent model invoice'):
        Database(path, orders(lines=True)).migrate([Migration('0001', AddTable('invoice', 'lines', FrozenLine))])


def test_create_model_ignores_the_python_class_name(tmp_path):
    path = tmp_path / 'names.sqlite3'
    Database(path, orders(), migrations=[]).initialize()

    class Frozen(Model):
        storage_name = 'note'
        body = Text()

    live = type('Note', (Model,), {'body': Text(), '__module__': __name__})
    registry = Registry(orders().models['order'], live)
    migrations = [Migration('0001_note', CreateModel(Frozen))]
    assert Database(path, registry).migrate(migrations) == ['0001_note']
    Database(path, registry, migrations=migrations).initialize()


def test_mismatch_names_the_changed_property(tmp_path):
    path = tmp_path / 'days.sqlite3'
    Database(path, Registry(type('Leave', (Model,), {'days': Integer(), '__module__': __name__})), migrations=[]).initialize()
    from evlet import Decimal
    changed = Registry(type('Leave', (Model,), {'days': Decimal(scale=1), '__module__': __name__}))
    with pytest.raises(SchemaError) as error:
        Database(path, changed).migrate([])
    assert "leave.days: type is 'integer' after the migrations and 'decimal' in the models" in str(error.value)
    assert "leave.days: scale is None after the migrations and 1 in the models" in str(error.value)
    plan = Database(path, changed).plan([])
    assert plan.state == 'upgrade' and plan.differences
    assert '# leave.days: ' in plan.suggestion and 'not supported' in plan.suggestion


def hr_models(*, dependents=False, nationality=False):
    from evlet import Journal, Rule
    lifecycle = {'admin': Rule(('read', 'create', 'update', 'delete', 'confirm', 'cancel', 'reopen'))}
    employee = {'__module__': __name__, 'name': Text(required=True), 'permissions': {'admin': Rule(('read', 'create', 'update', 'delete'))}}
    if nationality:
        employee['nationality'] = Text(default='AE')
    if dependents:
        employee['dependents'] = Table(type('Dependent', (Child,), {'__module__': __name__, 'full_name': Text(required=True), 'age': Integer()}))
    leave = {'__module__': __name__, 'confirmable': True, 'track_changes': True, 'permissions': lifecycle,
             'employee': Text(required=True), 'days': Integer(default=0), 'reason': Text(),
             'Meta': type('Meta', (), {'journal': Journal(tamper_evident=True)})}
    return Registry(type('Employee', (Model,), employee), type('LeaveApplication', (Model,), leave))


def application(database):
    from evlet import Application, LocalAuth
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    return app


def confirmed_leave(database):
    """An employee and a leave application confirmed once (revision 1, hashed journal entries)."""
    app = application(database)
    with app.business.context(ADMIN) as context:
        employee = context.create(database.registry.models['employee'], name='Mariam')['id']
        document = context.document(database.registry.models['leaveapplication'], employee='Mariam', days=3, reason='Annual')
        document.save()
        document.confirm()
        return employee, document.id


def check_plan_and_add_table(make):
    """make(registry, migrations, migrate=False, plan=False) builds a database on one engine, then initializes it,
    migrates it or returns its plan."""
    from evlet.journal import verify
    database = make(hr_models(), [])
    employee, leave = confirmed_leave(database)
    target = hr_models(dependents=True, nationality=True)
    with pytest.raises(SchemaError) as error:
        make(target, [], migrate=True)
    assert 'employee.nationality: field is in the models but not in the migrations' in str(error.value)
    plan = make(target, [], plan=True)
    assert [text for _, _, text in plan.differences] == [
        'employee.nationality: field is in the models but not in the migrations',
        'employee_dependents: model is in the models but not in the migrations']
    namespace = {}
    exec(plan.suggestion, namespace)
    migrations = [namespace['migration']]
    database = make(target, migrations, migrate=True)
    assert make(target, migrations, plan=True).text().endswith('The migrations produce the current models.')
    database = make(target, migrations)
    app = application(database)
    Employee, Leave = target.models['employee'], target.models['leaveapplication']
    with app.business.context(ADMIN) as context:
        assert context.get(Employee, employee).nationality == 'AE'
        context.update(Employee, employee, children={'dependents': [{'full_name': 'Omar', 'age': 7}]})
        assert context.snapshot(Leave, leave, revision=1)['document']['reason'] == 'Annual'
        assert context.get(Leave, leave).days == 3
    checked, breaks = verify(database)
    assert checked and not breaks


def test_sqlite_plan_and_add_table_with_confirmed_documents(tmp_path):
    def make(registry, migrations, migrate=False, plan=False):
        database = Database(tmp_path / 'hr.sqlite3', registry, migrations=migrations)
        if plan:
            return database.plan(migrations)
        database.migrate(migrations) if migrate else database.initialize()
        return database
    check_plan_and_add_table(make)
