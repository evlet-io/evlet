"""Migration plans, named mismatches and owned tables, through a flat app on an instance (real SQLite)."""

import sqlite3
import sys

import pytest

from evlet.audit import COLUMNS as AUDIT_COLUMNS
from evlet.framework import version as framework_version
from evlet.migrations import describe

from evlet import (AddTable, Child, CreateModel, Database, Integer, Migration, Model, Registry, SchemaError, Table, Text,
                   User)
from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.cli.setup import setup
from evlet.instances import load_runtime

ADMIN = User(1, 'admin', 'admin')

MODELS = '''from evlet import Integer, Activity, Model, Rule, State, Text{imports}

RULES = {{'admin': Rule(('read', 'create', 'update', 'delete'))}}
LIFECYCLE = {{'admin': Rule(('read', 'create', 'update', 'delete'))}}
{child}

class Employee(Model):
    permissions = RULES
    full_name = Text(required=True, max_length=120)
{employee}

class LeaveApplication(Model):
    permissions = LIFECYCLE
    docstate = State(State.Step('draft'), State.Step('confirmed', lock=True, release=True),
                   rules=(State.Rule('draft', 'confirmed', 'Confirm', key='confirm'),))
    employee = Text(required=True)
    days = Integer(default=0)
    reason = Text()

    class Meta:
        activity = Activity(tamper_evident=True)
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
    """An employee and a confirmed leave application with hashed activity entries."""
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
            assert (context.get(Leave, leave).docstate, context.get(Leave, leave).reason) == ('confirmed', 'Annual')
    finally:
        app.database.close()
    main(['activity', 'verify', 'acme'])


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
    from evlet import Activity, Rule
    from evlet import State
    lifecycle = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    employee = {'__module__': __name__, 'full_name': Text(required=True), 'permissions': {'admin': Rule(('read', 'create', 'update', 'delete'))}}
    if nationality:
        employee['nationality'] = Text(default='AE')
    if dependents:
        employee['dependents'] = Table(type('Dependent', (Child,), {'__module__': __name__, 'full_name': Text(required=True), 'age': Integer()}))
    leave = {'__module__': __name__, 'permissions': lifecycle,
             'docstate': State(State.Step('draft'), State.Step('confirmed', lock=True, release=True),
                             rules=(State.Rule('draft', 'confirmed', 'Confirm', key='confirm'),)),
             'employee': Text(required=True), 'days': Integer(default=0), 'reason': Text(),
             'Meta': type('Meta', (), {'activity': Activity(tamper_evident=True)})}
    return Registry(type('Employee', (Model,), employee), type('LeaveApplication', (Model,), leave))


def application(database):
    from evlet import Application, LocalAuth
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',)))
    app.initialize()
    return app


def confirmed_leave(database):
    """An employee and a leave application confirmed once (hashed activity entries)."""
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
    from evlet.activity import verify
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
        assert (context.get(Leave, leave).docstate, context.get(Leave, leave).reason) == ('confirmed', 'Annual')
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
             if key in ('docstate', 'permissions', '__module__', 'Meta')}
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
    from evlet.activity import verify
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
        assert context.get(Leave, leave)['days'] == '3.0'
        assert (context.get(Leave, leave).docstate, context.get(Leave, leave).reason) == ('confirmed', 'Annual')
        half = context.create(Leave, employee='Mariam', days='1.5')['id']
        assert context.get(Leave, half)['days'] == '1.5'

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


# DropIndex: a removed or changed index migrates (finance v1's Party).

def party(indexes=(), *, code=None, kind=True):
    """A Party model: `indexes` is its Meta.indexes, `code` the code field's flags."""
    attributes = {'__module__': __name__, 'code': Text(required=True, max_length=20, **(code or {})),
                  'title': Text(max_length=120)}
    if kind:
        attributes['kind'] = Text(max_length=20)
    if indexes:
        attributes['Meta'] = type('Meta', (), {'indexes': list(indexes)})
    return type('Party', (Model,), attributes)


def suggested(plan):
    namespace = {}
    exec(plan.suggestion, namespace)
    return namespace['migration']


def index_shape(database, table):
    with database.transaction() as tx:
        # The standard name column's own unique index is left out.
        return sorted((found.columns, found.unique) for found in tx.connection.inspect(table).indexes
                      if not found.partial and found.columns != ('name',))


def test_party_unique_index_change_migrates_an_instance_that_ran_0001(tmp_path):
    from evlet import DropIndex
    path = tmp_path / 'finance.sqlite3'
    first = [Migration('0001_initial', CreateModel(party([(('kind', 'code'), {'unique': True})])))]
    database = Database(path, Registry(party([(('kind', 'code'), {'unique': True})])), migrations=first)
    database.migrate(first)
    Party = database.registry.models['party']
    database.create(Party, code='ACME', kind='customer')
    database.create(Party, code='GULF', kind='customer')
    # The models now make the code unique on its own and index the kind separately.
    target = Registry(party([(('code',), {'unique': True}), ('kind',)]))
    plan = Database(path, target).plan(first)
    assert "Meta.indexes differ" in plan.text()
    migration = suggested(Database(path, target).plan(first))
    assert migration.operations[0] == DropIndex('party', ('kind', 'code'), unique=True)
    assert "DropIndex('party', ('kind', 'code'), unique=True)" in plan.suggestion
    assert plan.suggestion.index('DropIndex(') < plan.suggestion.index('AddIndex(')
    migrations = [*first, Migration('0002_changes', *migration.operations)]
    assert Database(path, target).migrate(migrations) == ['0002_changes']
    database = Database(path, target, migrations=migrations)
    database.initialize()
    assert index_shape(database, 'party') == [(('code',), True), (('kind',), False)]
    assert Database(path, target).plan(migrations).text().endswith('The migrations produce the current models.')


def test_drop_index_removes_a_unique_composite_index(tmp_path):
    path = tmp_path / 'parties.sqlite3'
    first = [Migration('0001', CreateModel(party([(('kind', 'code'), {'unique': True})])))]
    Database(path, Registry(party([(('kind', 'code'), {'unique': True})])), migrations=first).migrate(first)
    target = Registry(party())
    migrations = [*first, Migration('0002', *suggested(Database(path, target).plan(first)).operations)]
    assert [describe(op) for op in migrations[1].operations] == ['drop unique index on party (kind, code)']
    database = Database(path, target, migrations=migrations)
    database.migrate(migrations)
    assert index_shape(database, 'party') == []
    database.create(target.models['party'], code='A', kind='x')
    database.create(target.models['party'], code='A', kind='x')


def test_changing_an_index_unique_flag(tmp_path):
    from evlet import AddIndex, DropIndex
    path = tmp_path / 'flags.sqlite3'
    composite = [Migration('0001', CreateModel(party([('kind', 'code')], code={'index': True})))]
    Database(path, Registry(party([('kind', 'code')], code={'index': True})), migrations=composite).migrate(composite)
    # The composite entry and the field-level index both become unique.
    target = Registry(party([(('kind', 'code'), {'unique': True})], code={'unique': True}))
    migration = suggested(Database(path, target).plan(composite))
    assert set(migration.operations) == {DropIndex('party', ('kind', 'code')), AddIndex('party', ('kind', 'code'), unique=True),
                                         DropIndex('party', 'code'), AddIndex('party', 'code', unique=True)}
    kinds = [type(op).__name__ for op in migration.operations]
    assert kinds == sorted(kinds, key=lambda name: name != 'DropIndex')
    migrations = [*composite, Migration('0002', *migration.operations)]
    database = Database(path, target, migrations=migrations)
    database.migrate(migrations)
    database.initialize()
    assert index_shape(database, 'party') == [(('code',), True), (('kind', 'code'), True)]
    # And back: DropIndex(unique=True) clears the field's unique flag.
    back = Registry(party([('kind', 'code')]))
    migration = suggested(Database(path, back).plan(migrations))
    assert set(migration.operations) == {DropIndex('party', 'code', unique=True), DropIndex('party', ('kind', 'code'), unique=True),
                                         AddIndex('party', ('kind', 'code'))}
    migrations = [*migrations, Migration('0003', *migration.operations)]
    database = Database(path, back, migrations=migrations)
    database.migrate(migrations)
    database.initialize()
    assert index_shape(database, 'party') == [(('kind', 'code'), False)]


def test_drop_index_refuses_an_index_the_plan_does_not_have(tmp_path):
    from evlet import DropIndex
    path = tmp_path / 'missing.sqlite3'
    first = [Migration('0001', CreateModel(party()))]
    Database(path, Registry(party()), migrations=first).migrate(first)
    for operation, message in ((DropIndex('party', ('kind', 'code')), r"party: there is no index \('kind', 'code'\) to drop"),
                               (DropIndex('party', 'code', unique=True), 'party.code: there is no unique index to drop')):
        with pytest.raises(SchemaError, match=message):
            Database(path, Registry(party())).migrate([*first, Migration('0002', operation)])
    with pytest.raises(SchemaError, match='DropIndex names distinct fields'):
        Migration('0002', DropIndex('party', ('code', 'code'))).checksum()


def test_drop_index_is_done_on_an_instance_created_after_the_index_left_the_models(tmp_path):
    """0001 made a unique (kind, code) index; the models dropped it without a migration, and an instance
    created from those models never had it. A later DropIndex migrates that instance and the one that ran 0001."""
    from evlet import AddField, DropIndex
    indexed = party([(('kind', 'code'), {'unique': True})], code={'index': True})
    first = [Migration('0001_initial', CreateModel(indexed)), Migration('0002_note', AddField('party', 'note', Text()))]
    attributes = {'__module__': __name__, 'code': Text(required=True, max_length=20), 'title': Text(max_length=120),
                  'kind': Text(max_length=20), 'note': Text()}
    target = Registry(type('Party', (Model,), attributes))
    migrations = [*first, Migration('0003_drop', DropIndex('party', ('kind', 'code'), unique=True),
                                    DropIndex('party', 'code'))]
    # The old instance ran 0001 with the index and 0002 later.
    old = tmp_path / 'old.sqlite3'
    Database(old, Registry(indexed), migrations=first[:1]).migrate(first[:1])
    with pytest.raises(SchemaError, match='Meta.indexes differ'):
        Database(old, target).migrate(first)
    assert Database(old, target).plan(migrations).text().endswith('The migrations produce the current models.')
    assert Database(old, target).migrate(migrations) == ['0002_note', '0003_drop']
    # The fresh instance was created from the models at 0002, which no longer have either index.
    fresh = tmp_path / 'fresh.sqlite3'
    Database(fresh, target, migrations=first).migrate(first)
    assert index_shape(Database(fresh, target), 'party') == []
    assert Database(fresh, target).plan(migrations).text().endswith('The migrations produce the current models.')
    assert Database(fresh, target).migrate(migrations) == ['0003_drop']
    for path in (old, fresh):
        database = Database(path, target, migrations=migrations)
        database.initialize()
        assert index_shape(database, 'party') == []
        database.create(target.models['party'], code='A', kind='x')
        database.create(target.models['party'], code='A', kind='x')
    # An index no applied migration ever made is still refused.
    with pytest.raises(SchemaError, match=r"party: there is no index \('kind', 'title'\) to drop"):
        Database(fresh, target).migrate([*migrations, Migration('0004', DropIndex('party', ('kind', 'title')))])


def test_finance_v1_0009_drop_index_runs_on_an_instance_created_at_0008(tmp_path):
    """finance_v1's shipped 0009, unchanged: DropIndex("party", ("kind", "record_id"), unique=True). An
    instance created at 0008 from models without that index migrates, as does one that ran 0001."""
    from evlet import AddField, DropIndex

    def finance_party(indexed):
        attributes = {'__module__': __name__, 'title': Text(max_length=120), 'kind': Text(max_length=20),
                      'record_id': Integer()}
        if indexed:
            attributes['Meta'] = type('Meta', (), {'indexes': [(('kind', 'record_id'), {'unique': True})]})
        return type('Party', (Model,), attributes)

    first = Migration('0001_initial', CreateModel(finance_party(True)))
    middle = [Migration(f'{number:04d}_step', AddField('party', f'note_{number}', Text())) for number in range(2, 9)]
    upto_0008 = [first, *middle]
    attributes = {'__module__': __name__, 'title': Text(max_length=120), 'kind': Text(max_length=20), 'record_id': Integer(),
                  **{f'note_{number}': Text() for number in range(2, 9)}}
    target = Registry(type('Party', (Model,), attributes))
    shipped = [*upto_0008, Migration('0009_seed_and_index', DropIndex('party', ('kind', 'record_id'), unique=True))]
    created_at_0008 = tmp_path / 'created-0008.sqlite3'
    Database(created_at_0008, target, migrations=upto_0008).migrate(upto_0008)
    assert Database(created_at_0008, target).migrate(shipped) == ['0009_seed_and_index']
    ran_0001 = tmp_path / 'ran-0001.sqlite3'
    Database(ran_0001, Registry(finance_party(True)), migrations=[first]).migrate([first])
    assert index_shape(Database(ran_0001, Registry(finance_party(True))), 'party') == [(('kind', 'record_id'), True)]
    assert Database(ran_0001, target).migrate(shipped) == [migration.name for migration in shipped[1:]]
    for path in (created_at_0008, ran_0001):
        database = Database(path, target, migrations=shipped)
        database.initialize()
        assert index_shape(database, 'party') == []


def test_drop_index_by_old_names_after_a_rename_drops_the_database_index(tmp_path):
    """0001 made a unique (kind, record_id) index; 0002 renamed kind to role_kind (SQLite renames the column inside the
    index, which keeps its old name). A later DropIndex naming the old columns drops the database index too, whether
    the instance ran 0002 earlier or runs them together, and the plan reports no drift."""
    import sqlite3
    from evlet import DropIndex, RenameField

    def finance_party(indexed, kind):
        attributes = {'__module__': __name__, 'title': Text(max_length=120), kind: Text(max_length=20), 'record_id': Integer()}
        if indexed:
            attributes['Meta'] = type('Meta', (), {'indexes': [((kind, 'record_id'), {'unique': True})]})
        return type('Party', (Model,), attributes)

    first = Migration('0001_initial', CreateModel(finance_party(True, 'kind')))
    renamed = Migration('0002_rename', RenameField('party', 'kind', 'role_kind'))
    dropped = Migration('0003_drop', DropIndex('party', ('kind', 'record_id'), unique=True))
    target = Registry(finance_party(False, 'role_kind'))
    migrations = [first, renamed, dropped]
    stepwise, together = tmp_path / 'stepwise.sqlite3', tmp_path / 'together.sqlite3'
    for path in (stepwise, together):
        Database(path, Registry(finance_party(True, 'kind')), migrations=[first]).migrate([first])
    Database(stepwise, Registry(finance_party(True, 'role_kind'))).migrate([first, renamed])
    with sqlite3.connect(stepwise) as connection:
        assert connection.execute("SELECT sql FROM sqlite_master WHERE name = 'evlet_party_kind_record_id_unique'").fetchone()[0] \
            .replace('"', '').endswith('(role_kind, record_id)')
    assert Database(stepwise, target).migrate(migrations) == ['0003_drop']
    assert Database(together, target).migrate(migrations) == ['0002_rename', '0003_drop']
    for path in (stepwise, together):
        database = Database(path, target, migrations=migrations)
        database.initialize()
        assert index_shape(database, 'party') == []
        with sqlite3.connect(path) as connection:
            assert not connection.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql LIKE '%record_id%'").fetchall()
        assert Database(path, target).plan(migrations).text().endswith('The migrations produce the current models.')
        database.create(target.models['party'], role_kind='customer', record_id=1)
        database.create(target.models['party'], role_kind='customer', record_id=1)


def test_drop_index_by_its_first_names_after_a_rename_and_a_new_field_under_the_old_name(tmp_path):
    """0001 made a unique (kind, record_id) index; 0002 renamed kind to role_kind and added a new kind field; 0003
    drops the index by the names it had when 0001 made it. kind now names the new field, which no index covers, so
    the DropIndex matches the renamed entry: an instance created at 0001 migrates, its database index is gone and the
    plan reports no drift. An index made later on the new field's names is still the one a DropIndex by them drops."""
    from evlet import AddField, AddIndex, DropIndex, RenameField

    def party_model(indexed, *fields):
        attributes = {'__module__': __name__, 'title': Text(max_length=120), 'record_id': Integer(),
                      **{name: Text(max_length=20) for name in fields}}
        if indexed:
            attributes['Meta'] = type('Meta', (), {'indexes': [(indexed, {'unique': True})]})
        return type('Party', (Model,), attributes)

    first = Migration('0001_initial', CreateModel(party_model(('kind', 'record_id'), 'kind')))
    reshaped = Migration('0002_reshape', RenameField('party', 'kind', 'role_kind'),
                         AddField('party', 'kind', Text(max_length=20)))
    dropped = Migration('0003_drop', DropIndex('party', ('kind', 'record_id'), unique=True))
    target = Registry(party_model(None, 'role_kind', 'kind'))
    migrations = [first, reshaped, dropped]
    path = tmp_path / 'fresh.sqlite3'
    Database(path, Registry(party_model(('kind', 'record_id'), 'kind')), migrations=[first]).migrate([first])
    plan = Database(path, target).plan(migrations)
    assert plan.text().endswith('The migrations produce the current models.')
    assert Database(path, target).migrate(migrations) == ['0002_reshape', '0003_drop']
    database = Database(path, target, migrations=migrations)
    database.initialize()
    assert index_shape(database, 'party') == []
    with sqlite3.connect(path) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql LIKE '%record_id%'").fetchall()
    database.create(target.models['party'], role_kind='customer', record_id=1)
    database.create(target.models['party'], role_kind='customer', record_id=1)

    # The same with 0002 applied in an earlier upgrade: the rename comes from the applied migrations.
    stepwise = tmp_path / 'stepwise.sqlite3'
    Database(stepwise, Registry(party_model(('kind', 'record_id'), 'kind')), migrations=[first]).migrate([first])
    Database(stepwise, Registry(party_model(('role_kind', 'record_id'), 'role_kind', 'kind'))).migrate([first, reshaped])
    assert Database(stepwise, target).migrate(migrations) == ['0003_drop']
    database = Database(stepwise, target, migrations=migrations)
    database.initialize()
    assert index_shape(database, 'party') == []
    assert Database(stepwise, target).plan(migrations).text().endswith('The migrations produce the current models.')

    # An index on the new field's names wins over the renamed one: the DropIndex drops the index it names now.
    both = party_model(None, 'role_kind', 'kind')
    both.Meta = type('Meta', (), {'indexes': [(('role_kind', 'record_id'), {'unique': True})]})
    later = [first, reshaped, Migration('0003_new', AddIndex('party', ('kind', 'record_id'), unique=True)),
             Migration('0004_drop', DropIndex('party', ('kind', 'record_id'), unique=True))]
    other = tmp_path / 'later.sqlite3'
    Database(other, Registry(party_model(('kind', 'record_id'), 'kind')), migrations=[first]).migrate([first])
    kept = Registry(party_model(('role_kind', 'record_id'), 'role_kind', 'kind'))
    assert Database(other, kept).migrate(later) == ['0002_reshape', '0003_new', '0004_drop']
    database = Database(other, kept, migrations=later)
    database.initialize()
    assert index_shape(database, 'party') == [(('role_kind', 'record_id'), True)]


def test_drop_field_takes_its_composite_index_out_of_the_plan(tmp_path):
    from evlet import DropField, RetireField
    path = tmp_path / 'drop.sqlite3'
    indexed = party([(('kind', 'code'), {'unique': True})])
    first = [Migration('0001', CreateModel(indexed))]
    Database(path, Registry(indexed), migrations=first).migrate(first)
    database = Database(path, Registry(indexed), migrations=first)
    database.create(indexed, code='A', kind='x')
    # Written before either ran: retire the kind, then drop it; the composite index goes with the field.
    target = Registry(party(kind=False))
    migrations = [*first, Migration('0002', RetireField('party', 'kind')), Migration('0003', DropField('party', 'kind'))]
    database = Database(path, target, migrations=migrations)
    database.migrate(migrations, allow_destructive=True)
    database.initialize()
    assert index_shape(database, 'party') == []
    database.create(target.models['party'], code='A')
    assert Database(path, target).plan(migrations).text().endswith('The migrations produce the current models.')


# Seed data: RunData(..., seed=True) reaches a new instance.

def seed_units(session):
    Unit = session.registry.models['unit']
    for code in ('PCS', 'KG'):
        if not session.list(Unit, filters=[['code', 'eq', code]]):
            session.create(Unit, code=code)


def mark_existing(session):
    Unit = session.registry.models['unit']
    session.create(Unit, code='OLD')


def unit_models():
    return Registry(type('Unit', (Model,), {'__module__': __name__, 'code': Text(required=True, max_length=10)}))


def unit_migrations():
    from evlet import RunData
    Unit = type('Unit', (Model,), {'__module__': __name__, 'code': Text(required=True, max_length=10)})
    return [Migration('0001_unit', CreateModel(Unit), RunData(mark_existing)),
            Migration('0002_units', RunData(seed_units, seed=True))]


def unit_codes(database):
    return sorted(record.code for record in database.list(database.registry.models['unit']))


def test_seed_steps_reach_a_fresh_migrate(tmp_path):
    database = Database(tmp_path / 'fresh.sqlite3', unit_models(), migrations=unit_migrations())
    assert database.migrate(unit_migrations()) == ['0001_unit', '0002_units']
    # The seed ran; the other data step, written for an older schema, did not.
    assert unit_codes(database) == ['KG', 'PCS']


def test_seed_steps_reach_a_fresh_startup(tmp_path):
    database = Database(tmp_path / 'startup.sqlite3', unit_models(), migrations=unit_migrations())
    database.initialize()
    assert unit_codes(database) == ['KG', 'PCS']
    database.initialize()
    assert unit_codes(database) == ['KG', 'PCS']


def test_seed_steps_reach_a_baseline_database(tmp_path):
    path = tmp_path / 'baseline.sqlite3'
    Database(path, unit_models()).initialize()
    database = Database(path, unit_models(), migrations=unit_migrations())
    assert database.migrate(unit_migrations()) == ['0001_unit', '0002_units']
    assert unit_codes(database) == ['KG', 'PCS']


def test_seed_steps_run_once_in_order_on_an_existing_database(tmp_path):
    path = tmp_path / 'existing.sqlite3'
    migrations = unit_migrations()
    Database(path, unit_models(), migrations=migrations[:1]).migrate(migrations[:1])
    database = Database(path, unit_models(), migrations=migrations[:1])
    assert unit_codes(database) == []
    database.create(database.registry.models['unit'], code='KG')
    # Upgrading runs the seed in sequence like any data step; it finds the KG the instance already has.
    assert database.migrate(migrations) == ['0002_units']
    assert unit_codes(database) == ['KG', 'PCS']
    assert database.migrate(migrations) == []
    Database(path, unit_models(), migrations=migrations).initialize()
    assert unit_codes(database) == ['KG', 'PCS']


def test_seed_is_part_of_the_checksum_and_not_a_maintenance_step():
    from evlet import RunData
    assert Migration('a', RunData(seed_units)).checksum() != Migration('a', RunData(seed_units, seed=True)).checksum()
    # Migrations written before seed= keep their checksum.
    assert 'seed' not in RunData(seed_units).signature()
    assert describe(RunData(seed_units, seed=True)) == 'run seed step seed_units'
    with pytest.raises(SchemaError, match='seed'):
        RunData(seed_units, seed=True, maintenance=True).signature()
    with pytest.raises(SchemaError, match='RunData seed is True or False'):
        RunData(seed_units, seed=1).signature()
