"""Finite-v1 documents through the authenticated HTTP routes on real SQLite."""

import pytest

from evlet import (Application, Database, LocalAuth, Registry, Model, Child, Table, Decimal, Currency, Text, Rule,
                   Policy, Action, ValidationError, action)
from evlet.calc import round, parent
from evlet.business import Business
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
        assert owners[task_ids['a1']] == created['_row_ids']['A'] and owners[task_ids['b1']] == created['_row_ids']['B']

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
