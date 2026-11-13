"""Migration plans, named mismatches and owned tables, through a flat app on an instance (real SQLite)."""

import sqlite3
import sys

import pytest

from evlet.audit import COLUMNS as AUDIT_COLUMNS
from evlet.framework import version as framework_version

from evlet import (AddTable, Child, CreateModel, Database, Integer, Migration, Model, Registry, SchemaError, Table, Text,
                   User)
from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.cli.setup import setup
from evlet.instances import load_runtime

ADMIN = User(1, 'admin', 'admin')

MODELS = '''from evlet import Integer, Journal, Model, Rule, State, Text{imports}

RULES = {{'admin': Rule(('read', 'create', 'update', 'delete'))}}
LIFECYCLE = {{'admin': Rule(('read', 'create', 'update', 'delete'))}}
{child}

class Employee(Model):
    permissions = RULES
    full_name = Text(required=True, max_length=120)
{employee}

class LeaveApplication(Model):
    permissions = LIFECYCLE
    status = State(State.Step('draft'), State.Step('confirmed', lock=True, release=True),
                   rules=(State.Rule('draft', 'confirmed', 'Confirm', key='confirm'),))
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
    """An employee and a confirmed leave application with hashed journal entries."""
    app = runtime(root)
    try:
        with app.business.context(ADMIN) as context:
            employee = context.create(app.registry.models['employee'], full_name='Mariam')['id']
            leave = context.create(app.registry.models['leaveapplication'], employee='Mariam', days=3, reason='Annual')['id']
            context.move(app.registry.models['leaveapplication'], leave, 'confirm')
            return employee, leave
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
    assert f'Planned with Evlet {framework_version()}' in output
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
            assert (context.get(Leave, leave).status, context.get(Leave, leave).reason) == ('confirmed', 'Annual')
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
    assert "RetireField('leaveapplication', 'reason')" in output


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
    # Table rows get the record time columns too (evlet/audit.py).
    assert columns == {'id', 'item', 'quantity', 'evlet_owner', 'evlet_position', *AUDIT_COLUMNS}
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
    assert "AlterField('leave', 'days', Decimal(scale=1))" in plan.suggestion


def hr_models(*, dependents=False, nationality=False):
    from evlet import Journal, Rule
    from evlet import State
    lifecycle = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    employee = {'__module__': __name__, 'full_name': Text(required=True), 'permissions': {'admin': Rule(('read', 'create', 'update', 'delete'))}}
    if nationality:
        employee['nationality'] = Text(default='AE')
    if dependents:
        employee['dependents'] = Table(type('Dependent', (Child,), {'__module__': __name__, 'full_name': Text(required=True), 'age': Integer()}))
    leave = {'__module__': __name__, 'permissions': lifecycle,
             'status': State(State.Step('draft'), State.Step('confirmed', lock=True, release=True),
                             rules=(State.Rule('draft', 'confirmed', 'Confirm', key='confirm'),)),
             'employee': Text(required=True), 'days': Integer(default=0), 'reason': Text(),
             'Meta': type('Meta', (), {'journal': Journal(tamper_evident=True)})}
    return Registry(type('Employee', (Model,), employee), type('LeaveApplication', (Model,), leave))


def application(database):
    from evlet import Application, LocalAuth
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    return app


def confirmed_leave(database):
    """An employee and a leave application confirmed once (hashed journal entries)."""
    app = application(database)
    Leave = database.registry.models['leaveapplication']
    with app.business.context(ADMIN) as context:
        employee = context.create(database.registry.models['employee'], full_name='Mariam')['id']
        leave = context.create(Leave, employee='Mariam', days=3, reason='Annual')['id']
        context.move(Leave, leave, 'confirm')
        return employee, leave


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
        assert (context.get(Leave, leave).status, context.get(Leave, leave).reason) == ('confirmed', 'Annual')
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


def people_models(*, days=None, split=False):
    """hr_models() with Leave.days possibly changed and Employee.full_name possibly split into two fields."""
    registry = hr_models()
    Employee, Leave = registry.models['employee'], registry.models['leaveapplication']
    employee = {key: value for key, value in vars(Employee).items() if key in ('permissions', '__module__')}
    employee.update({'first_name': Text(default=''), 'last_name': Text(default='')} if split else {'full_name': Text(required=True)})
    leave = {key: value for key, value in vars(Leave).items()
             if key in ('status', 'permissions', '__module__', 'Meta')}
    leave.update({'employee': Text(required=True), 'days': days or Integer(default=0), 'reason': Text()})
    return Registry(type('Employee', (Model,), employee), type('LeaveApplication', (Model,), leave))


def split_names(session):
    """Move each employee's full name into first and last name (a RunData step)."""
    Employee = session.registry.models['employee']
    for record_id, full_name in session.connection.execute('SELECT "id", "full_name" FROM "employee" ORDER BY "id"').fetchall():
        first, _, last = full_name.partition(' ')
        session.update(Employee, record_id, first_name=first, last_name=last)


def broken_step(session):
    raise RuntimeError('stop here')


def raw(database, sql):
    with database.transaction() as tx:
        return [tuple(row) for row in tx.connection.execute(sql).fetchall()]


def check_retire_alter_and_run_data(make):
    """make(registry, migrations, migrate=False, plan=False, allow_destructive=False) on one engine."""
    from decimal import Decimal as D
    from evlet import AddField, AlterField, Decimal, DropField, RetireField, RunData
    from evlet.journal import verify
    database = make(people_models(), [])
    app = application(database)
    with app.business.context(ADMIN) as context:
        khan = context.create(database.registry.models['employee'], full_name='x')['id']
        context.update(database.registry.models['employee'], khan, full_name='Mariam Khan')
    employee, leave = confirmed_leave(database)

    # Integer to Decimal: a failing batch changes nothing.
    decimal_days = people_models(days=Decimal(scale=1, default='0'))
    plan = make(decimal_days, [], plan=True)
    assert "AlterField('leaveapplication', 'days', Decimal(scale=1, default='0'))" in plan.suggestion
    alter = AlterField('leaveapplication', 'days', Decimal(scale=1, default='0'))
    with pytest.raises(SchemaError, match='RunData broken_step failed: stop here'):
        make(decimal_days, [Migration('0001_days', alter, RunData(broken_step))], migrate=True)
    assert raw(database, 'SELECT days FROM leaveapplication') == [(3,)]
    history = [Migration('0001_days', alter)]
    database = make(decimal_days, history, migrate=True)
    assert raw(database, 'SELECT days FROM leaveapplication') == [(30,)]
    database = make(decimal_days, history)
    app = application(database)
    Leave = decimal_days.models['leaveapplication']
    with app.business.context(ADMIN) as context:
        assert context.get(Leave, leave).days == '3.0'
        assert (context.get(Leave, leave).status, context.get(Leave, leave).reason) == ('confirmed', 'Annual')
        half = context.create(Leave, employee='Mariam', days='1.5')['id']
        assert context.get(Leave, half).days == '1.5'

    # Split one field into two and retire the old one; its column and values stay.
    split = people_models(days=Decimal(scale=1, default='0'), split=True)
    plan = make(split, history, plan=True)
    assert "RetireField('employee', 'full_name')" in plan.suggestion
    history = history + [Migration('0002_split_name', AddField('employee', 'first_name', Text(default='')),
                                   AddField('employee', 'last_name', Text(default='')),
                                   RetireField('employee', 'full_name'), RunData(split_names))]
    database = make(split, history, migrate=True)
    database = make(split, history)
    assert make(split, history, plan=True).text().endswith('The migrations produce the current models.')
    app = application(database)
    Employee = split.models['employee']
    with app.business.context(ADMIN) as context:
        record = context.get(Employee, khan)
        assert (record.first_name, record.last_name) == ('Mariam', 'Khan')
        assert not hasattr(record, 'full_name')
        added = context.create(Employee, first_name='Omar')['id']
    assert raw(database, f'SELECT full_name FROM employee WHERE id IN ({int(khan)}, {int(added)}) ORDER BY id') == [('Mariam Khan',), (None,)]
    checked, breaks = verify(database)
    assert checked and not breaks

    # Dropping the retired field needs an explicit choice.
    later = history + [Migration('0003_drop_name', DropField('employee', 'full_name'))]
    with pytest.raises(SchemaError, match='allow_destructive'):
        make(split, later, migrate=True)
    assert raw(database, f'SELECT full_name FROM employee WHERE id = {int(khan)}') == [('Mariam Khan',)]
    database = make(split, later, migrate=True, allow_destructive=True)
    database = make(split, later)
    with pytest.raises(sqlite3.OperationalError):
        raw(database, 'SELECT full_name FROM employee')
    with application(database).business.context(ADMIN) as context:
        assert context.get(Employee, khan).first_name == 'Mariam'


def sqlite_maker(path):
    def make(registry, migrations, migrate=False, plan=False, allow_destructive=False):
        database = Database(path, registry, migrations=migrations)
        if plan:
            return database.plan(migrations)
        database.migrate(migrations, allow_destructive=allow_destructive) if migrate else database.initialize()
        return database
    return make


def test_sqlite_retire_alter_and_run_data_with_confirmed_documents(tmp_path):
    check_retire_alter_and_run_data(sqlite_maker(tmp_path / 'people.sqlite3'))


def leave_registry(days):
    return Registry(type('Leave', (Model,), {'days': days, '__module__': __name__}))


def test_the_plan_suggests_alter_field_for_a_field_that_becomes_required(tmp_path):
    path = tmp_path / 'leave.sqlite3'
    Database(path, leave_registry(Integer()), migrations=[]).initialize()
    plan = Database(path, leave_registry(Integer(required=True))).plan([])
    assert "AlterField('leave', 'days', Integer(required=True))" in plan.suggestion
    assert '# leave.days becomes required: add fill=... to its AlterField if some records have no value' in plan.suggestion


@pytest.mark.parametrize('old,new,message', [
    (Integer(), Integer(minimum=1), None),
    (Text(max_length=20), Text(max_length=200), None),
    (Text(max_length=20), __import__('evlet').LongText(), None),
    (__import__('evlet').Select(('a', 'b')), __import__('evlet').Select(('a', 'b', 'c')), None),
    (__import__('evlet').Decimal(scale=1), __import__('evlet').Decimal(scale=3), None),
    (__import__('evlet').Decimal(scale=2), Integer(), 'cannot convert decimal to integer'),
    (__import__('evlet').Decimal(scale=3), __import__('evlet').Decimal(scale=1), 'not narrow it'),
    (Text(max_length=20), Text(max_length=10), 'not shorten it'),
    (__import__('evlet').Select(('a', 'b')), __import__('evlet').Select(('a',)), 'not remove them'),
    (Integer(), Integer(required=True), None),
    (Integer(required=True), Integer(), None),
    (Integer(), Integer(unique=True), 'cannot change unique'),
    (Text(), __import__('evlet').Select(('a',)), 'cannot convert text to select'),
])
def test_alter_field_allows_only_safe_conversions(tmp_path, old, new, message):
    from evlet import AlterField
    path = tmp_path / 'leave.sqlite3'
    database = Database(path, leave_registry(old), migrations=[])
    database.initialize()
    migration = Migration('0001_alter', AlterField('leave', 'days', new))
    target = Database(path, leave_registry(type(new)(*new.arguments[0], **new.arguments[1])))
    if message is None:
        assert target.migrate([migration]) == ['0001_alter']
    else:
        with pytest.raises(SchemaError, match=message):
            target.migrate([migration])


def test_alter_field_refuses_values_that_overflow_and_rows_that_fail(tmp_path):
    from evlet import AlterField, Decimal
    path = tmp_path / 'leave.sqlite3'
    database = Database(path, leave_registry(Integer()), migrations=[])
    database.initialize()
    record = database.create(database.registry.models['leave'], days=2**62)
    with pytest.raises(SchemaError, match=rf'leave #{record.id}.days: the value is too large'):
        Database(path, leave_registry(Decimal(scale=9))).migrate([Migration('0001', AlterField('leave', 'days', Decimal(scale=9)))])
    with pytest.raises(SchemaError, match='stored data fails validation'):
        Database(path, leave_registry(Integer(maximum=5))).migrate([Migration('0001', AlterField('leave', 'days', Integer(maximum=5)))])
    assert raw(database, 'SELECT days FROM leave') == [(2**62,)]


def test_run_data_checksum_follows_its_source():
    from evlet import RunData
    first, second = Migration('0001', RunData(split_names)), Migration('0001', RunData(broken_step))
    assert first.checksum() != second.checksum()
    assert first.checksum() == Migration('0001', RunData(split_names)).checksum()
    with pytest.raises(SchemaError, match='needs a function'):
        Migration('0001', RunData('split')).checksum()


def test_retire_and_drop_rules(tmp_path):
    from evlet import DropField, RetireField
    path = tmp_path / 'leave.sqlite3'
    database = Database(path, leave_registry(Integer()), migrations=[])
    database.initialize()
    gone = Registry(type('Leave', (Model,), {'note': Text(), '__module__': __name__}))
    from evlet import AddField
    with pytest.raises(SchemaError, match='later migration'):
        Database(path, gone).migrate([Migration('0001', AddField('leave', 'note', Text()), RetireField('leave', 'days'),
                                                DropField('leave', 'days'))], allow_destructive=True)
    noted = Registry(type('Leave', (Model,), {'days': Integer(), 'note': Text(), '__module__': __name__}))
    with pytest.raises(SchemaError, match='retire the field with RetireField before DropField'):
        Database(path, noted).migrate([Migration('0001', AddField('leave', 'note', Text()), DropField('leave', 'days'))],
                                      allow_destructive=True)


def test_cli_migrate_allow_destructive_keeps_a_backup(project, capsys):
    seed(project)
    text = (project / 'apps/people/models/hr.py').read_text()
    (project / 'apps/people/models/hr.py').write_text(text.replace('    reason = Text()\n', ''))
    main(['makemigration', 'people', '--instance', 'acme', '--name', 'retire_reason', '--trust'])
    main(['migrate', 'acme', '--trust'])
    capsys.readouterr()
    (project / 'apps/people/migrations/m0002_drop_reason.py').write_text(
        'from evlet import DropField, Migration\n\nmigration = Migration("0002_drop_reason", DropField("leaveapplication", "reason"))\n')
    with pytest.raises(SystemExit):
        main(['migrate', 'acme', '--trust'])
    assert '--allow-destructive' in capsys.readouterr().err
    main(['migrate', 'acme', '--trust', '--allow-destructive'])
    output = capsys.readouterr().out
    assert 'app6_people__0002_drop_reason' in output and 'Backup: ' in output
    backup = output.split('Backup: ')[1].strip()
    with sqlite3.connect(backup) as connection:
        assert connection.execute('SELECT reason FROM leaveapplication').fetchall() == [('Annual',)]
    with sqlite3.connect(project / 'instances/acme/data/database.sqlite3') as connection:
        assert 'reason' not in {row[1] for row in connection.execute('PRAGMA table_info(leaveapplication)')}


def copy_phone(session):
    for record_id, phone in session.connection.execute('SELECT "id", "phone" FROM "contact" ORDER BY "id"').fetchall():
        session.update(session.registry.models['contact'], record_id, contact_phone=phone)


def shorten_reason(session):
    Leave = session.registry.models['leaveapplication']
    for (record_id,) in session.connection.execute('SELECT "id" FROM "leaveapplication"').fetchall():
        session.update(Leave, record_id, reason='Annual leave')


def contacts(*fields):
    from evlet import Link
    attributes = {'__module__': __name__, 'full_name': Text()}
    for name in fields:
        attributes[name] = {'phone': Text(required=True), 'contact_phone': Text(default=''),
                            'manager': Link('contact')}[name]
    return Registry(type('Contact', (Model,), attributes))


def check_run_data_order_and_maintenance(make):
    """make(registry, migrations, migrate=False, plan=False, allow_destructive=False, allow_maintenance=False)."""
    from evlet import AddField, DropField, RetireField, RunData

    # A run that spans releases copies values before a later migration drops their column.
    database = make(contacts('phone', 'manager'), [])
    boss = database.create(database.registry.models['contact'], full_name='Boss', phone='+971 4 000 0000').id
    database.create(database.registry.models['contact'], full_name='Mariam', phone='+971 50 123 4567', manager=boss)
    release = [Migration('0001_contact_phone', AddField('contact', 'contact_phone', Text(default='')),
                         RetireField('contact', 'phone'), RetireField('contact', 'manager'), RunData(copy_phone)),
               Migration('0002_drop_phone', DropField('contact', 'phone'))]
    database = make(contacts('contact_phone'), release, migrate=True, allow_destructive=True)
    Contact = database.registry.models['contact']
    assert sorted(record.contact_phone for record in database.list(Contact)) == ['+971 4 000 0000', '+971 50 123 4567']
    # The retired manager Link no longer guards the record it pointed at.
    database.delete(Contact, boss)
    assert raw(database, 'SELECT manager FROM contact') == [(boss,)]


def check_maintenance_steps(make):
    """Maintenance data steps run only when the run allows them, and then they are audited."""
    from evlet import AddField, RunData
    database = make(hr_models(), [])
    confirmed_leave(database)
    target = hr_models(nationality=True)
    base = AddField('employee', 'nationality', Text(default='AE'))
    steps = [Migration('0001', base, RunData(shorten_reason, maintenance=True))]
    with pytest.raises(SchemaError, match='--allow-maintenance'):
        make(target, steps, migrate=True)
    database = make(target, steps, migrate=True, allow_maintenance=True)
    assert raw(database, 'SELECT reason FROM leaveapplication') == [('Annual leave',)]
    assert raw(database, "SELECT actor, area, target FROM evlet_audit WHERE detail LIKE 'Data step shorten_reason%'") == [('cli', 'Records', '0001')]


def maker(path, cls=Database):
    def make(registry, migrations, migrate=False, plan=False, allow_destructive=False, allow_maintenance=False):
        database = cls(path, registry, migrations=migrations)
        if plan:
            return database.plan(migrations)
        if migrate:
            database.migrate(migrations, allow_destructive=allow_destructive, allow_maintenance=allow_maintenance)
        else:
            database.initialize()
        return database
    return make


def test_sqlite_run_data_runs_with_its_own_migration(tmp_path):
    check_run_data_order_and_maintenance(maker(tmp_path / 'contacts.sqlite3'))


def test_sqlite_maintenance_data_steps_are_opt_in_and_audited(tmp_path):
    check_maintenance_steps(maker(tmp_path / 'hr.sqlite3'))


def test_relax_explains_a_defensive_sqlite_build(monkeypatch):
    from evlet.sqlite import SQLiteConnection
    connection = sqlite3.connect(':memory:', factory=SQLiteConnection)
    connection.execute('CREATE TABLE "t" ("id" INTEGER PRIMARY KEY AUTOINCREMENT, "a" TEXT NOT NULL)')
    if not hasattr(sqlite3, 'SQLITE_DBCONFIG_DEFENSIVE'):
        pytest.skip('This Python cannot switch SQLite to defensive mode')
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
    with pytest.raises(SchemaError, match='does not allow changing column constraints'):
        connection.set_required('t', 'a', False)
