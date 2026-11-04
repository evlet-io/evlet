"""Finite-v1 documents through the authenticated HTTP routes on real SQLite."""

import re

import pytest

from evlet import (AddField, Migration, Application, Database, LocalAuth, Registry, Model, Child, Table, Decimal, Currency, Text, Integer, Rule,
                   Policy, Action, ValidationError, action)
from evlet.calc import round, parent
from evlet.business import Business
from evlet.identity import RecordId, sequential_ids
from test_business import http, client, PASSWORD
from finite_fixtures import FiniteMeta, Invoice

ALL = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}


class Task(Child):
    title = Text(default='')
    hours = Decimal(default=0, minimum='0')
    amount = Currency(compute=round(hours * parent.hourly_rate, 2))

    def validate(self):
        if self.title == 'invalid':
            raise ValidationError({'title': 'Rejected'})


class Stage(Child):
    name = Text(default='')
    hourly_rate = Currency(compute=parent.hourly_rate, stored=False)
    tasks = Table(Task)
    total = Currency(compute=tasks.sum('amount'))


class Plan(Model):
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='Plan')
    hourly_rate = Currency(default=100)
    stages = Table(Stage)
    total = Currency(compute=stages.sum('total'))

    @action
    def double(self):
        self.hourly_rate = self.hourly_rate * 2


def application(tmp_path, *models, **options):
    db = Database(tmp_path / 'db.sqlite3', Registry(*models))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True), **options)
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    return app


def stages():
    return [{'_key': 'A', 'name': 'Design', 'children': {'tasks': [{'_key': 'a1', 'hours': '2'}, {'_key': 'a2', 'hours': '3'}]}},
            {'_key': 'B', 'name': 'Build', 'children': {'tasks': [{'_key': 'b1', 'hours': '4'}]}}]


def test_nested_document_create_reload_update_and_delete(tmp_path):
    app = application(tmp_path, Plan)
    c = client(app)
    status, created, headers = c('POST', '/api/plan', {'children': {'stages': stages()}})
    assert status == 201, created
    assert created['total'] == '900.00' and created['_calculation']['model_schema_hash'] == app.registry.plans[Plan]['model_schema_hash']
    assert set(created['_row_ids']) == {'A', 'B', 'a1', 'a2', 'b1'}
    status, loaded, headers = c('GET', f"/api/plan/{created['id']}")
    assert [stage['total'] for stage in loaded['children']['stages']] == ['500.00', '400.00']
    assert [task['amount'] for task in loaded['children']['stages'][0]['children']['tasks']] == ['200.00', '300.00']
    task_ids = {key: value for key, value in created['_row_ids'].items() if key.startswith(('a', 'b'))}
    with app.database.transaction() as tx:
        stage_model = next(child.model for child in app.registry.children if child.name == 'stages')
        task_model = next(child.model for child in app.registry.children if child.name == 'tasks')
        owners = {row.id: row.evlet_owner for row in tx.list(task_model)}
        assert owners[RecordId(task_ids['a1'])] == RecordId(created['_row_ids']['A'])
        assert owners[RecordId(task_ids['b1'])] == RecordId(created['_row_ids']['B'])

    stage_a = loaded['children']['stages'][0]
    body = {'hourly_rate': '120', 'children': {'stages': [
        {'id': stage_a['id'], 'children': {'tasks': [{'id': stage_a['children']['tasks'][0]['id'], 'hours': '5'}]}},
        {'id': loaded['children']['stages'][1]['id']}]}}
    status, updated, headers = c('PATCH', f"/api/plan/{created['id']}", body, headers={b'if-match': headers[b'etag']})
    assert status == 200, updated
    assert updated['total'] == '1080.00'
    assert [task['hours'] for task in updated['children']['stages'][0]['children']['tasks']] == ['5.0000']
    with app.database.transaction() as tx:
        assert tx.count(task_model) == 2

    status, result, _ = c('PATCH', f"/api/plan/{created['id']}", {'children': {'stages': []}}, headers={b'if-match': headers[b'etag']})
    assert status == 200 and result['total'] == '0.00'
    with app.database.transaction() as tx:
        assert tx.count(task_model) == 0 and tx.count(stage_model) == 0

    status, again, headers = c('POST', '/api/plan', {'children': {'stages': stages()}})
    assert c('DELETE', f"/api/plan/{again['id']}", headers={b'if-match': headers[b'etag']})[0] == 204
    with app.database.transaction() as tx:
        assert tx.count(task_model) == 0 and tx.count(stage_model) == 0


def test_nested_failures_roll_back_everything_and_computed_writes_are_rejected(tmp_path):
    app = application(tmp_path, Plan)
    c = client(app)
    bad = stages()
    bad[1]['children']['tasks'][0]['title'] = 'invalid'
    status, result, _ = c('POST', '/api/plan', {'children': {'stages': bad}})
    assert status == 422 and result['fields'] == {'stages.B.tasks.b1.title': 'Rejected'}
    with app.database.transaction() as tx:
        assert all(tx.count(model) == 0 for model in app.registry.models.values())
    for body in ({'total': '900.00'}, {'children': {'stages': [{'total': '1'}]}},
                 {'children': {'stages': [{'children': {'tasks': [{'hours': '1', 'amount': '100.00'}]}}]}}):
        status, result, _ = c('POST', '/api/plan', body)
        assert status == 422 and 'COMPUTED_FIELD_WRITE' in str(result), body
    status, result, _ = c('POST', '/api/plan', {'children': {'stages': [{'children': {'tasks': [{'hours': '-1'}]}}]}})
    assert status == 422 and 'stages.' in next(iter(result['fields']))
    status, result, _ = c('POST', '/api/plan', {'_calculation': {'model_schema_hash': 'sha256:old'}})
    assert status == 409 and 'SCHEMA_VERSION_MISMATCH' in result['error']
    plan_hash = app.registry.plans[Plan]['model_schema_hash']
    assert c('POST', '/api/plan', {'_calculation': {'language': 'finite-v1', 'model_schema_hash': plan_hash}})[0] == 201


def test_actions_previews_and_revision_rules(tmp_path):
    app = application(tmp_path, Plan)
    c = client(app)
    status, created, headers = c('POST', '/api/plan', {'children': {'stages': stages()}})
    url = f"/api/plan/{created['id']}"
    assert c('PATCH', url, {'title': 'x'})[0] == 428
    assert c('PATCH', url, {'title': 'x'}, headers={b'if-match': b'"stale"'})[0] == 409
    status, doubled, headers = c('POST', url + '/actions/double', {}, headers={b'if-match': headers[b'etag']})
    assert status == 200 and doubled['total'] == '1800.00'
    preview = {'id': created['id'], 'values': {'hourly_rate': '10', 'children': {'stages': [
        {'id': doubled['children']['stages'][0]['id'], 'children': {'tasks': [{'_key': 'new', 'hours': '1'}]}}]}},
               'calculation': {'model_schema_hash': app.registry.plans[Plan]['model_schema_hash']}}
    status, result, _ = c('POST', '/api/_draft/plan', preview)
    assert status == 200, result
    first = result['values']['children']['stages'][0]
    # Submitted collections replace visible rows: stage B is dropped and stage A keeps only the new task.
    assert [task['amount'] for task in first['children']['tasks']] == ['10.00'] and result['values']['total'] == '10.00'
    assert c('GET', url)[1]['total'] == '1800.00'
    assert c('POST', '/api/_draft/plan', {**preview, 'calculation': {'model_schema_hash': 'sha256:x'}})[0] == 409


def test_legacy_actions_and_raw_writes_cannot_bypass_settlement(tmp_path):
    def bump(tx, user, record):
        tx.update(type(record), record.id, title='raw')
    with pytest.raises(ValueError, match='bypass finite-v1 settlement'):
        application(tmp_path, Plan, actions=(Action(Plan, 'bump', 'Bump', bump),))
    app = application(tmp_path / 'second', Plan) if (tmp_path / 'second').mkdir() is None else None
    with pytest.raises(ValueError, match='finite-v1 document'):
        app.database.create(Plan, title='raw')
    with app.database.transaction() as tx:
        with tx.maintenance():
            record = tx.create(Plan, title='repair', hourly_rate='1')
        with pytest.raises(ValueError, match='finite-v1 document'):
            tx.update(Plan, record.id, title='again')


def test_hidden_inputs_hide_derived_results_and_incomplete_collections(tmp_path):
    class Line(Child):
        cost = Currency(default=0)
        price = Currency(default=0)
        margin = Currency(compute=price - cost)

    class Deal(Model):
        Meta = FiniteMeta
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                       'editor': Rule(('read', 'create', 'update'))}
        lines = Table(Line)
        revenue = Currency(compute=lines.sum('price'))
        profit = Currency(compute=lines.sum('margin'))
    registry = Registry(Deal)
    line = registry.children[0].model
    policy = Policy(registry, rules={('editor', line): Rule(('read', 'create', 'update', 'delete'), read_fields=('price',))})
    from evlet import User
    editor = User(2, 'e', 'editor')
    assert 'margin' not in policy.fields(editor, line)
    assert 'profit' not in policy.fields(editor, Deal) and 'revenue' in policy.fields(editor, Deal)
    scoped = Policy(registry, rules={('editor', line): Rule(('read',), scope=lambda user: {'price__gt': '0'})})
    assert 'revenue' not in scoped.fields(editor, Deal)


def test_existing_invoice_fixture_saves_with_strict_values(tmp_path):
    app = application(tmp_path, Invoice)
    app.business.policy = Policy(app.registry, rules={('admin', Invoice): Rule(('read', 'create', 'update', 'delete'))})
    c = client(app)
    status, created, _ = c('POST', '/api/invoice', {'inclusive': True, 'children': {'items': [{'_key': 'a', 'qty': '1', 'rate': '105'}]}})
    assert status == 201, created
    assert (created['net_total'], created['vat_total'], created['grand_total'], created['line_count']) == ('100.00', '5.00', '105.00', 1)
    status, result, _ = c('POST', '/api/invoice', {'children': {'items': [{'_key': 'a', 'qty': '1', 'rate': '0.005'}]}})
    assert status == 422


def test_schema_projects_calculation_capabilities_without_hidden_inputs(tmp_path):
    from evlet import User
    from evlet.auth import password_hash
    class Line(Child):
        cost = Currency(default=0)
        price = Currency(default=0)
        margin = Currency(compute=price - cost)

    class Deal(Model):
        Meta = FiniteMeta
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'viewer': Rule(('read',))}
        lines = Table(Line)
        revenue = Currency(compute=lines.sum('price'))
        profit = Currency(compute=lines.sum('margin'))
    app = application(tmp_path, Deal)
    line = app.registry.children[0].model
    app.business.policy = Policy(app.registry, rules={('viewer', line): Rule(('read',), read_fields=('price',))})
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('viewer',?,'viewer')", (password_hash(PASSWORD),))
    admin = next(model for model in client(app)('GET', '/api/schema')[1]['models'] if model['name'] == 'deal')['calculation']
    assert set(admin['capabilities'].values()) == {'local_universal'} and admin['plan']['source_plan_hash'] == admin['model_schema_hash']
    from evlet.calc import Document
    assert Document(admin['plan'], {'children': {'lines': [{'cost': '2', 'price': '5'}]}}).snapshot()['profit'] == '3.00'
    viewer = next(model for model in client(app, 'viewer')('GET', '/api/schema')[1]['models'] if model['name'] == 'deal')['calculation']
    assert viewer['capabilities'] == {'revenue': 'local_universal', 'profit': 'unavailable'}
    assert 'plan' not in viewer and 'cost' not in str(viewer)


def test_shipped_plans_omit_unreadable_fields_and_field_checks_stay_pythonic(tmp_path):
    from evlet.auth import password_hash
    class Quote(Model):
        Meta = FiniteMeta
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                       'viewer': Rule(('read',), read_fields=('title', 'discount', 'net'))}
        title = Text(default='')
        discount = Currency(default=0)
        floor_price = Currency(default='1234.56')
        supplier_note = Text(default='Supplier X', hidden=True)
        net = Currency(compute=0 - discount)
    app = application(tmp_path, Quote)
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('viewer',?,'viewer')", (password_hash(PASSWORD),))
    calculation = next(model for model in client(app, 'viewer')('GET', '/api/schema')[1]['models'] if model['name'] == 'quote')['calculation']
    assert set(calculation['plan']['tables']['$root']['fields']) == {'title', 'discount', 'net'}
    assert '1234.56' not in str(calculation) and 'Supplier' not in str(calculation)
    fields = list(Quote.__declared_fields__.values())
    assert Quote.title in fields and fields.index(Quote.discount) == 1 and Quote.title is not None
    with pytest.raises(TypeError):
        sorted(fields)
    from evlet.calc import CalcError
    with pytest.raises(CalcError, match='EXPRESSION_TRUTH_TEST'):
        (Quote.discount == 1) and Quote.title
    with pytest.raises(CalcError, match='EXPRESSION_TRUTH_TEST'):
        not (Quote.discount == 1)


def test_row_cap_counts_each_row_once_and_unversioned_records_adopt_the_plan(tmp_path):
    app = application(tmp_path, Plan)
    c = client(app)
    tree = [{'_key': f's{s}', 'children': {'tasks': [{'_key': f's{s}t{t}', 'hours': '1'} for t in range(70)]}} for s in range(10)]
    status, created, headers = c('POST', '/api/plan', {'children': {'stages': tree}})
    assert status == 201, created
    _, current, headers = c('GET', f"/api/plan/{created['id']}")
    resubmit = {'children': {'stages': [{'id': stage['id'], 'children': {'tasks': [{'id': task['id']} for task in stage['children']['tasks']]}}
                                        for stage in current['children']['stages']]}}
    assert c('POST', '/api/_draft/plan', {'id': created['id'], 'values': resubmit})[0] == 200
    status, result, headers = c('PATCH', f"/api/plan/{created['id']}", resubmit, headers={b'if-match': headers[b'etag']})
    assert status == 200, result
    with app.database.transaction() as tx:
        with tx.maintenance():
            record = tx.create(Plan, title='repair')
    _, loaded, headers = c('GET', f'/api/plan/{record.id}')
    status, result, _ = c('PATCH', f'/api/plan/{record.id}', {'title': 'fixed'}, headers={b'if-match': headers[b'etag']})
    assert status == 200 and result['total'] == '0.00'


def test_nested_review_regressions(tmp_path):
    from evlet.auth import password_hash
    app = application(tmp_path, Plan)
    stage = next(child.model for child in app.registry.children if child.name == 'stages')
    task = next(child.model for child in app.registry.children if child.name == 'tasks')
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('editor',?,'editor')", (password_hash(PASSWORD),))
    c = client(app)
    # Sequential ids give the first stage and the first task the same id, so their keys collide below.
    with sequential_ids():
        status, created, headers = c('POST', '/api/plan', {'children': {'stages': stages()}})
    assert status == 201
    status, result, _ = c('POST', '/api/plan', {'children': {'stages': [{'_key': 'x', 'children': {'tasks': [{'_key': 'x'}]}}]}})
    assert status == 422 and 'within the document' in str(result)
    _, current, headers = c('GET', f"/api/plan/{created['id']}")
    first = current['children']['stages'][0]
    same_keys = {'children': {'stages': [{'id': first['id'], '_key': str(first['id']), 'children': {'tasks': [
        {'id': task['id'], '_key': str(task['id'])} for task in first['children']['tasks']]}}]}}
    assert str(first['id']) in {str(task['id']) for task in first['children']['tasks']}
    status, result, headers = c('PATCH', f"/api/plan/{created['id']}", same_keys, headers={b'if-match': headers[b'etag']})
    assert status == 200, result
    status, created, headers = c('POST', '/api/plan', {'children': {'stages': stages()}})

    app.business.policy = Policy(app.registry, rules={('editor', stage): Rule(('read', 'create', 'update', 'delete')),
                                                      ('editor', task): Rule(('read',))})
    editor = client(app, 'editor')
    _, current, headers = editor('GET', f"/api/plan/{created['id']}")
    with app.database.transaction() as tx:
        before = tx.count(task)
    status, result, _ = editor('PATCH', f"/api/plan/{created['id']}", {'children': {'stages': []}}, headers={b'if-match': headers[b'etag']})
    assert status == 403
    with app.database.transaction() as tx:
        assert tx.count(task) == before

    hidden_task = created['_row_ids']['a1']
    app.business.policy = Policy(app.registry, rules={('editor', task): Rule(('read', 'create', 'update', 'delete'), scope=lambda user: {'id__ne': hidden_task})})
    from evlet import User
    errors = app.business.visible_errors(User(2, 'editor', 'editor'), Plan,
                                         {f"stages.{created['_row_ids']['A']}.tasks.{hidden_task}.amount": 'COMPUTE_DIVISION_BY_ZERO'})
    assert errors == {'_form': 'One or more values are invalid'}

    with pytest.raises(ValueError, match='finite-v1 document'):
        app.database.delete(task, created['_row_ids']['b1'])


def test_formula_changes_never_silently_rewrite_saved_documents(tmp_path):
    app = application(tmp_path, globals()['Plan'])
    c = client(app)
    status, created, headers = c('POST', '/api/plan', {'children': {'stages': stages()}})
    assert status == 201 and created['total'] == '900.00'

    class Task(Child):
        title = Text(default='')
        hours = Decimal(default=0, minimum='0')
        amount = Currency(compute=round(hours * parent.hourly_rate * 2, 2))

    class Stage(Child):
        name = Text(default='')
        hourly_rate = Currency(compute=parent.hourly_rate, stored=False)
        tasks = Table(Task)
        total = Currency(compute=tasks.sum('amount'))

    class Plan(Model):  # Same model, one formula changed.
        Meta = FiniteMeta
        permissions = ALL
        title = Text(default='Plan')
        hourly_rate = Currency(default=100)
        stages = Table(Stage)
        total = Currency(compute=stages.sum('total'))
    database = Database(tmp_path / 'db.sqlite3', Registry(Plan))
    upgraded = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    upgraded.initialize()
    c = client(upgraded)
    url = f"/api/plan/{created['id']}"
    status, loaded, headers = c('GET', url)
    assert status == 200 and loaded['total'] == '900.00'
    status, result, _ = c('PATCH', url, {'title': 'renamed'}, headers={b'if-match': headers[b'etag']})
    assert status == 409 and 'SCHEMA_VERSION_MISMATCH' in result['error']
    assert c('GET', url)[1]['total'] == '900.00'
    active = upgraded.registry.plans[Plan]['model_schema_hash']
    status, result, _ = c('PATCH', url, {'title': 'renamed', '_calculation': {'model_schema_hash': active, 'recalculate': True}},
                          headers={b'if-match': headers[b'etag']})
    assert status == 200 and result['total'] == '1800.00'
    _, current, headers = c('GET', url)
    assert c('PATCH', url, {'title': 'again'}, headers={b'if-match': headers[b'etag']})[0] == 200


def test_unrelated_model_edits_keep_saved_documents_writable_and_old_versions_migrate(tmp_path):
    original = globals()['Plan']
    app = application(tmp_path, original)
    c = client(app)
    status, created, _ = c('POST', '/api/plan', {'children': {'stages': stages()}})
    assert status == 201
    with app.database.transaction() as tx:  # How releases before the formula hash recorded versions.
        tx.connection.execute('UPDATE evlet_calculation_versions SET plan_hash=?', (app.registry.plans[original]['model_schema_hash'],))
    application(tmp_path, original)  # Starting the upgraded framework moves those rows to the formula hash.
    with app.database.transaction() as tx:
        assert [tuple(row) for row in tx.connection.execute('SELECT DISTINCT plan_hash FROM evlet_calculation_versions')] == [(app.registry.formula_hashes[original],)]

    class Plan(Model):  # Same formulas plus one text field nothing calculates from.
        Meta = FiniteMeta
        storage_name = 'plan'
        permissions = ALL
        title = Text(default='Plan')
        hourly_rate = Currency(default=100)
        stages = Table(Stage)
        total = Currency(compute=stages.sum('total'))
        note = Text(default='')
    database = Database(tmp_path / 'db.sqlite3', Registry(Plan))
    database.migrate([Migration('add_note', AddField('plan', 'note', Text(default='')))])
    upgraded = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    upgraded.initialize()
    assert upgraded.registry.plans[Plan]['model_schema_hash'] != app.registry.plans[original]['model_schema_hash']
    c = client(upgraded)
    url = f"/api/plan/{created['id']}"
    _, loaded, headers = c('GET', url)
    status, result, _ = c('PATCH', url, {'title': 'renamed'}, headers={b'if-match': headers[b'etag']})
    assert status == 200 and result['total'] == '900.00'


class Line(Child):
    n = Integer(default=0)


Ledger = type('Ledger', (Model,), {'permissions': ALL, 'title': Text(default='x'), **{f't{i}': Table(Line) for i in range(11)}})


def test_row_limits_apply_to_finite_roots_only_and_allow_full_swaps(tmp_path):
    app = application(tmp_path, Plan, Ledger)
    c = client(app)
    # A legacy one-level document keeps only the per-collection limit.
    status, created, headers = c('POST', '/api/ledger', {'children': {f't{i}': [{'n': j} for j in range(95)] for i in range(11)}})
    assert status == 201, created
    status, result, _ = c('PATCH', f"/api/ledger/{created['id']}", {'title': 'renamed'}, headers={b'if-match': headers[b'etag']})
    assert status == 200, result
    # A finite document at the cap can drop and add rows in one write.
    tree = [{'_key': f's{s}', 'children': {'tasks': [{'_key': f's{s}t{t}', 'hours': '1'} for t in range(99)]}} for s in range(10)]
    status, plan, _ = c('POST', '/api/plan', {'children': {'stages': tree}})
    assert status == 201, plan
    _, current, headers = c('GET', f"/api/plan/{plan['id']}")
    stages = [{'id': stage['id'], 'children': {'tasks': [{'id': task['id']} for task in stage['children']['tasks']]}} for stage in current['children']['stages']]
    stages[0]['children']['tasks'] = stages[0]['children']['tasks'][5:] + [{'_key': f'n{k}', 'hours': '2'} for k in range(5)]
    status, result, _ = c('PATCH', f"/api/plan/{plan['id']}", {'children': {'stages': stages}}, headers={b'if-match': headers[b'etag']})
    assert status == 200, result
    # The owned-row cap is 5,000, so one more row fits; a 101st task exceeds the table's max_rows (100).
    stages[1]['children']['tasks'].append({'_key': 'fits', 'hours': '1'})
    _, _, headers = c('GET', f"/api/plan/{plan['id']}")
    status, result, _ = c('PATCH', f"/api/plan/{plan['id']}", {'children': {'stages': stages}}, headers={b'if-match': headers[b'etag']})
    assert status == 200, result
    _, current, headers = c('GET', f"/api/plan/{plan['id']}")
    stages = [{'id': stage['id'], 'children': {'tasks': [{'id': task['id']} for task in stage['children']['tasks']]}} for stage in current['children']['stages']]
    stages[1]['children']['tasks'].append({'_key': 'over', 'hours': '1'})
    assert c('PATCH', f"/api/plan/{plan['id']}", {'children': {'stages': stages}}, headers={b'if-match': headers[b'etag']})[0] == 422


def test_draft_previews_reject_bad_ids_and_report_invalid_values(tmp_path):
    app = application(tmp_path, Plan)
    c = client(app)
    for bad in ([1], {'a': 1}, True, 0):
        assert c('POST', '/api/_draft/plan', {'id': bad, 'values': {}})[0] == 422
    status, result, _ = c('POST', '/api/_draft/plan', {'values': {'hourly_rate': 'abc', 'children': {'stages': [
        {'_key': 'A', 'children': {'tasks': [{'_key': 'a1', 'hours': 'NaN'}, {'_key': 'a2', 'hours': [1]}]}}]}}})
    assert status == 200, result
    assert result['values']['hourly_rate'] is None and 'hourly_rate' in result['errors']
    assert {'stages.A.tasks.a1.hours', 'stages.A.tasks.a2.hours'} <= set(result['errors'])


def users(app):
    admin = client(app)
    for role in ('editor', 'viewer'):
        assert admin('POST', '/api/auth/users', {'username': role, 'password': PASSWORD, 'role': role})[0] == 201
    return admin, client(app, 'editor')


def test_scoped_rows_bounded_results_and_unreadable_tables_stay_private(tmp_path):
    full = Rule(('read', 'create', 'update', 'delete'))

    class Area(Child):
        permissions = {'admin': full, 'editor': Rule(('read', 'create', 'update', 'delete'), scope=lambda user: {'region': 'east'})}
        region = Text(default='east')
        amount = Currency(default=0)

    class Cost(Child):
        permissions = {'admin': full}
        supplier_code = Text(default='ACME-PRIVATE')

    class Deal(Model):
        Meta = FiniteMeta
        permissions = {'admin': full, 'editor': Rule(('read', 'create', 'update'), read_fields=('title', 'price'), write_fields=('title', 'price'))}
        title = Text(default='x')
        price = Currency(default=0)
        secret_cost = Currency(default=0)
        margin = Currency(compute=price - secret_cost, minimum='0')

    class Order(Model):
        Meta = FiniteMeta
        permissions = {'admin': full, 'editor': full}
        areas = Table(Area)
        costs = Table(Cost)
        total = Currency(compute=areas.sum('amount'))

    app = application(tmp_path, Deal, Order)
    admin, editor = users(app)
    # An editor cannot move a scoped row out of their own scope.
    _, order, _ = admin('POST', '/api/order', {'children': {'areas': [{'region': 'east', 'amount': '5'}, {'region': 'west', 'amount': '9'}]}})
    _, seen, headers = editor('GET', f"/api/order/{order['id']}")
    row = seen['children']['areas'][0]
    status, _, _ = editor('PATCH', f"/api/order/{order['id']}", {'children': {'areas': [{'id': row['id'], 'region': 'west'}]}}, headers={b'if-match': headers[b'etag']})
    assert status == 404
    assert [area['region'] for area in admin('GET', f"/api/order/{order['id']}")[1]['children']['areas']] == ['east', 'west']
    # A bounded result over a hidden input is a redacted calculation error, never a 500 oracle.
    _, deal, _ = admin('POST', '/api/deal', {'price': '100', 'secret_cost': '42.50'})
    for price in ('42.49', '42.50'):
        status, result, _ = editor('POST', '/api/_draft/deal', {'id': deal['id'], 'values': {'price': price}})
        assert status == 200 and result['errors'] == {} and 'margin' not in result['values']
    status, result, _ = admin('POST', '/api/_draft/deal', {'id': deal['id'], 'values': {'price': '42.49'}})
    assert result['errors']['margin'].startswith('COMPUTE_RANGE_ERROR')
    # Tables and calculated fields the role cannot read are not described in the schema.
    calculation = next(model for model in editor('GET', '/api/schema')[1]['models'] if model['name'] == 'order')['calculation']
    assert 'ACME' not in str(calculation) and 'supplier_code' not in str(calculation)
    calculation = next(model for model in editor('GET', '/api/schema')[1]['models'] if model['name'] == 'deal')['calculation']
    assert calculation['capabilities'] == {} and 'plan' not in calculation


def test_draft_previews_report_invalid_dates_without_failing(tmp_path):
    from evlet import Date, DateTime

    class Visit(Model):
        permissions = ALL
        due = Date()
        at = DateTime()
        lines = Table(Line)
    app = application(tmp_path, Visit)
    c = client(app)
    for values in ({'due': 'not-a-date'}, {'at': 'nope'}, {'due': 5}):
        status, result, _ = c('POST', '/api/_draft/visit', {'values': values})
        assert status == 200 and set(values) <= set(result['errors']) and all(result['values'][name] is None for name in values)


def test_link_paths_and_today_settle_on_the_server_and_withhold_unreadable_paths(tmp_path):
    from datetime import date
    from evlet import Date, Link, Boolean
    from evlet.calc import F, days, today, when
    from evlet.documents import host_today
    full = Rule(('read', 'create', 'update', 'delete'))

    class Region(Model):
        permissions = {'admin': full, 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
        grace = Integer(default=0)

    class Account(Model):
        # Editors cannot read rates; viewers see only accounts in their own scope.
        permissions = {'admin': full, 'editor': Rule(('read',), read_fields=('name', 'credit_days', 'region')),
                       'viewer': Rule(('read',), scope=lambda user: {'name': 'mine'})}
        name = Text(default='')
        credit_days = Integer(default=30)
        rate = Decimal(scale=2, default=0)
        vip = Boolean(default=False)
        region = Link(Region)

    class Bill(Model):
        Meta = FiniteMeta
        permissions = {'admin': full, 'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}
        account = Link(Account)
        issued = Date()
        amount = Currency(default=0)
        due = Date(compute=F('issued') + days(F('account.credit_days')))
        grace_due = Date(compute=F('due') + days(F('account.region.grace')))
        age = Integer(compute=today() - F('issued'))
        discount = Currency(compute=F('amount') * F('account.rate') / 100)
        net = Currency(compute=when(F('account.vip'), F('amount') - F('discount'), F('amount')))

    app = application(tmp_path, Region, Account, Bill)
    admin, editor = users(app)
    viewer = client(app, 'viewer')
    _, region, _ = admin('POST', '/api/region', {'grace': 5})
    _, account, _ = admin('POST', '/api/account', {'name': 'acme', 'credit_days': 30, 'rate': '2.50', 'region': region['id']})
    before = date.fromisoformat(host_today(app.business))
    status, bill, _ = admin('POST', '/api/bill', {'account': account['id'], 'issued': '2026-10-01', 'amount': '100.05'})
    after = date.fromisoformat(host_today(app.business))
    assert status == 201, bill
    # Two hops resolve on the server; 100.05 * 2.5% = 2.50125 is quantized half up to the Currency scale.
    assert (bill['due'], bill['grace_due'], bill['discount'], bill['net']) == ('2026-10-31', '2026-11-05', '2.50', '100.05')
    assert int(bill['age']) in {(day - date(2026, 10, 1)).days for day in (before, after)}

    # The editor cannot read Account.rate, so results over that path are withheld; the rest stay visible.
    status, seen, _ = editor('GET', f"/api/bill/{bill['id']}")
    assert seen['due'] == '2026-10-31' and seen['grace_due'] == '2026-11-05' and 'age' in seen
    assert 'discount' not in seen and 'net' not in seen
    status, created, _ = editor('POST', '/api/bill', {'account': account['id'], 'issued': '2026-02-28', 'amount': '10'})
    assert status == 201 and created['due'] == '2026-03-30' and 'discount' not in created
    assert admin('GET', f"/api/bill/{created['id']}")[1]['discount'] == '0.25'
    _, preview, _ = editor('POST', '/api/_draft/bill', {'values': {'account': account['id'], 'issued': '2024-02-28', 'amount': '1'}})
    assert preview['values']['due'] == '2024-03-29' and 'discount' not in preview['values']
    # A row-scoped looked-up model withholds every result that follows a path through it.
    status, scoped, _ = viewer('GET', f"/api/bill/{bill['id']}")
    assert status == 200 and not {'due', 'grace_due', 'discount', 'net'} & set(scoped) and 'age' in scoped

    schemas = {role: next(model for model in call('GET', '/api/schema')[1]['models'] if model['name'] == 'bill')['calculation']
               for role, call in (('admin', admin), ('editor', editor))}
    assert set(schemas['admin']['capabilities'].values()) == {'local_universal'}
    assert schemas['admin']['plan']['tables']['$root']['lookups'] == [{'model': 'account', 'fields': ['credit_days', 'rate', 'region', 'vip']},
                                                                     {'model': 'region', 'fields': ['grace']}]
    assert schemas['editor']['capabilities']['due'] == 'local_universal' and schemas['editor']['capabilities']['discount'] == 'unavailable'
    assert 'plan' not in schemas['editor']

    # A changed link value is looked up again; the server never keeps stale path values.
    _, other, _ = admin('POST', '/api/account', {'name': 'other', 'credit_days': 7, 'vip': True, 'rate': '10', 'region': region['id']})
    _, loaded, headers = admin('GET', f"/api/bill/{bill['id']}")
    status, moved, _ = admin('PATCH', f"/api/bill/{bill['id']}", {'account': other['id']}, headers={b'if-match': headers[b'etag']})
    assert status == 200 and (moved['due'], moved['discount'], moved['net']) == ('2026-10-08', '10.01', '90.04')
    # Without a region the second hop is a ready null; date arithmetic with it stays pending, so the stored result blocks the save.
    _, plain, _ = admin('POST', '/api/account', {'name': 'plain'})
    status, result, _ = admin('POST', '/api/bill', {'account': plain['id'], 'issued': '2026-10-01'})
    assert status == 422 and result['fields']['grace_due'].startswith('COMPUTE_INPUT_MISSING')
    status, result, _ = admin('POST', '/api/bill', {'issued': '2026-10-01'})
    assert status == 422 and result['fields']['due'].startswith('COMPUTE_INPUT_MISSING')


def test_today_uses_the_instance_timezone(tmp_path):
    from datetime import datetime, timedelta, timezone
    from evlet import Date
    from evlet.calc import today
    from evlet.documents import host_today

    class Stamp(Model):
        Meta = FiniteMeta
        permissions = ALL
        on = Date(compute=today())

    app = application(tmp_path, Stamp)
    admin = client(app)
    for zone, offset in (('Pacific/Kiritimati', 14), ('Etc/GMT+12', -12)):
        app.business.timezone = zone
        expected = {(datetime.now(timezone.utc) + timedelta(hours=offset, seconds=delta)).date().isoformat() for delta in (-5, 0, 5)}
        assert host_today(app.business) in expected
        status, stamp, _ = admin('POST', '/api/stamp', {})
        assert status == 201 and stamp['on'] in expected


def test_link_lookups_query_each_record_once_per_settle(tmp_path):
    from evlet import Link
    from evlet.calc import F, Document
    from evlet.documents import resolve_lookups

    class Region(Model):
        permissions = ALL
        grace = Integer(default=0)

    class Account(Model):
        permissions = ALL
        region = Link(Region)

    class Ticket(Model):
        Meta = FiniteMeta
        permissions = ALL
        account = Link(Account)
        other = Link(Account)
        grace = Decimal(compute=F('account.region.grace'), stored=False, scale=None)
        other_grace = Decimal(compute=F('other.region.grace'), stored=False, scale=None)

    app = application(tmp_path, Region, Account, Ticket)
    # The server reads lookups with full access, so no user is involved.
    with app.business.context(None) as context:
        statements = []
        context._tx.connection.set_trace_callback(lambda sql: statements.append(sql) if sql.lstrip().upper().startswith('SELECT') else None)
        region = context._tx.create(Region, grace=4)
        account = context._tx.create(Account, region=region.id)
        statements.clear()
        found = Document(app.registry.plans[Ticket], {'account': account.id}, checked=True)
        resolve_lookups(context, found)
        assert found.snapshot()['grace'] == '4' and len(statements) == 2
        statements.clear()
        # A record that does not exist stays pending and is not queried again in the next round.
        mixed = Document(app.registry.plans[Ticket], {'account': account.id, 'other': 999}, checked=True)
        resolve_lookups(context, mixed)
        snap = mixed.snapshot()
        assert snap['grace'] == '4' and snap['_states']['other_grace']['code'] == 'COMPUTE_INPUT_MISSING'
        # Ids are partly random, so match 999 as a whole number, not a digit run inside another id.
        assert len(statements) == 2 and not re.search(r'\b999\b', str(statements[1:]))
        context._tx.connection.set_trace_callback(None)
