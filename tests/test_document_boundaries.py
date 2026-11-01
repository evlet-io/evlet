from decimal import Decimal as D

import pytest

from evlet import (Application, Database, LocalAuth, Registry, Model, Text, Currency,
                   Decimal, Child, Table, Query, Ref, depends, Rule, User, ValidationError,
                   Task, JobQueue)
from evlet.auth import AccessError
from test_business import http, client, PASSWORD

ALL = {'admin': Rule(('read', 'create', 'update', 'delete'))}


def application(tmp_path, *models):
    db = Database(tmp_path / 'db.sqlite3', Registry(*models))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    return app


def test_named_query_typed_deduplicated_and_private(tmp_path):
    calls = []
    @depends('qty')
    def read_stock(row):
        assert row.context.readonly and row.parent.context.readonly
        with pytest.raises(AttributeError): row.parent.items.append(row)
        calls.append(row.qty)
        return {'actual': row.qty + D(4), 'reserved': D(2)}
    class Order(Model):
        permissions = ALL
        class Item(Child):
            qty = Decimal(required=True)
            stock = Query(read_stock, fields={'actual': Decimal(), 'reserved': Decimal()})
            available = Decimal(compute='stock.actual - stock.reserved', stored=False)
        items = Table(Item)
    app = application(tmp_path, Order)
    c = client(app)
    status, result, _ = c('POST', '/api/_draft/order', {'revision': 9, 'values': {'children': {'items': [{'_key': 'a', 'qty': '3'}, {'_key': 'b', 'qty': '3'}]}}})
    assert status == 200, result
    assert calls == [D(3)]
    assert [row['available'] for row in result['values']['children']['items']] == ['5.0000', '5.0000']
    assert result['values']['children']['items'][0]['_sources']['stock']['actual'] == '7.0000'
    assert app.database.count(Order) == 0


def test_model_query_zero_missing_and_ambiguous(tmp_path):
    class Stock(Model):
        permissions = ALL
        item = Text(required=True)
        qty = Decimal()
    class Line(Model):
        permissions = ALL
        item = Text()
        stock = Query(Stock, filters={'item': Ref('item')}, fields=['qty'])
        available = Decimal(compute='stock.qty', stored=False)
    app = application(tmp_path, Stock, Line)
    c = client(app)
    c('POST', '/api/stock', {'item': 'one', 'qty': '0'})
    result = c('POST', '/api/_draft/line', {'values': {'item': 'one'}})[1]
    assert result['values']['available'] == '0.0000'
    result = c('POST', '/api/_draft/line', {'values': {'item': 'missing'}})[1]
    assert result['values']['available'] is None
    c('POST', '/api/stock', {'item': 'one', 'qty': '2'})
    result = c('POST', '/api/_draft/line', {'values': {'item': 'one'}})[1]
    assert result['values']['available'] is None


def test_readonly_conditions_reactions_rollback_noop_and_delete(tmp_path):
    reactions = []
    class Entry(Model):
        permissions = ALL
        state = Text(default='Draft')
        note = Text(read_only_if='state == "Closed"')
        def changed(self, change):
            reactions.append(change)
            with pytest.raises(AttributeError): change.operation = 'forged'
            with pytest.raises(ValidationError): self.context.update(Entry, self.id, state='Recursive')
            if self.note == 'fail': raise ValidationError({'note': 'Rejected reaction'})
    app = application(tmp_path, Entry)
    c = client(app)
    assert c('POST', '/api/entry', {'note': 'fail'})[0] == 422
    assert app.database.count(Entry) == 0
    record = c('POST', '/api/entry', {'note': 'ok'})[1]
    reactions.clear()
    current = c('GET', '/api/entry/1')
    headers = {b'if-match': current[2][b'etag']}
    assert c('PATCH', '/api/entry/1', {'note': 'ok'}, headers=headers)[0] == 200
    assert not reactions
    assert c('PATCH', '/api/entry/1', {'state': 'Closed', 'note': 'forged'}, headers=headers)[0] == 422
    assert c('DELETE', '/api/entry/1')[0] == 428
    assert c('DELETE', '/api/entry/1', headers=headers)[0] == 204
    assert reactions[-1].operation == 'delete' and reactions[-1].after is None
    assert reactions[-1].before.note == 'ok'


def test_external_task_releases_database_write_lock(tmp_path):
    class Note(Model):
        title = Text()
    db = Database(tmp_path / 'jobs.sqlite3', Registry(Note))
    db.initialize()
    keys = []
    def send(context, payload):
        keys.append(context.idempotency_key)
        # A separate transaction can commit while the external handler runs.
        db.create(Note, title=payload['title'])
    queue = JobQueue(db, [Task('send', send, external=True)])
    queue.initialize()
    with db.transaction() as tx: queue.enqueue(tx, 'send', {'title': 'effect'}, key='effect-once')
    assert queue.run_one()
    assert db.count(Note) == 1 and len(keys) == 1
    assert queue.recent()[0]['state'] == 'succeeded'


def test_legacy_child_conversion_preserves_ids_links_and_history(tmp_path):
    from evlet import Link, Integer, Migration, CreateModel, AddField
    Invoice = type('Invoice', (Model,), {'title': Text()})
    Item = type('Item', (Model,), {'storage_name': 'line', 'invoice': Link(Invoice, required=True, index=True), 'qty': Decimal()})
    history = [Migration('initial', CreateModel(Invoice), CreateModel(Item))]
    db = Database(tmp_path / 'legacy.sqlite3', Registry(Invoice, Item), migrations=history)
    db.initialize()
    parent = db.create(Invoice, title='Preserved')
    row = db.create(Item, invoice=parent.id, qty='2')
    with db.transaction() as tx: checksum = tx.connection.execute('SELECT checksum FROM evlet_migrations').fetchone()[0]
    Row = type('Item', (Child,), {'qty': Decimal()})
    Updated = type('Invoice', (Model,), {'permissions': ALL, 'title': Text(), 'items': Table(Row, storage_name='line', owner_field='invoice')})
    updated = Database(db.path, Registry(Updated), migrations=[*history, Migration('row_order', AddField('line', 'evlet_position', __import__('evlet').Integer(required=True, default=0)))])
    updated.migrate(updated.migrations)
    stored = updated.get(updated.registry.models['line'], row.id)
    assert stored.invoice == parent.id and stored.qty == D(2)
    with updated.transaction() as tx: assert tx.connection.execute('SELECT checksum FROM evlet_migrations ORDER BY position').fetchone()[0] == checksum


def test_expression_type_errors_fail_before_schema_creation():
    class Invalid(Model):
        title = Text()
        amount = Currency(compute='title * 2')
    with pytest.raises(ValueError, match='numeric'): Registry(Invalid)


def test_hidden_dependencies_and_outputs_are_not_disclosed(tmp_path):
    class Salary(Model):
        permissions = ALL
        name = Text()
        secret = Currency(hidden=True, default='1250')
        inferred = Currency(compute='secret * 2')
    app = application(tmp_path, Salary)
    c = client(app)
    result = c('POST', '/api/_draft/salary', {'values': {'name': 'Example'}})[1]
    assert 'secret' not in result['values'] and 'inferred' not in result['values']
    metadata = c('GET', '/api/schema')[1]['models'][0]
    assert {field['name'] for field in metadata['fields']} == {'name'}
    assert 'secret' not in str(metadata['behavior'])


def test_scheduled_context_preserves_limited_identity_for_effects(tmp_path):
    from evlet.documents import Context
    class Note(Model):
        permissions = ALL
        title = Text()
    app = application(tmp_path, Note)
    identities = []
    queue = JobQueue(app.database, [Task('send', lambda context, payload: identities.append(context.user), external=True)], policy=app.business.policy)
    queue.initialize()
    app.business.jobs = queue
    with app.database.transaction() as tx:
        context = Context(app.business, tx, User(0, 'schedule', 'editor'))
        context.enqueue('send', {}, key='scheduled-intent')
    assert queue.run_one()
    assert identities == [User(0, 'schedule', 'editor')]


def test_computed_fields_keep_legacy_permission_defaults():
    from evlet import Policy
    class Invoice(Model):
        qty = Decimal(default='1')
        rate = Currency(default='0')
        note = Text(hidden=True)
        total = Currency(compute='qty * rate')
    registry = Registry(Invoice)
    policy = Policy(registry, rules={('viewer', Invoice): Rule(('read',))})
    assert policy.rule(User(1, 'a', 'admin'), Invoice).operations == ('read', 'create', 'update', 'delete')
    assert policy.rule(User(2, 'e', 'editor'), Invoice).operations == ('read', 'create', 'update', 'delete')
    assert 'note' in policy.fields(User(3, 'v', 'viewer'), Invoice)


def test_children_follow_their_document_permissions():
    from evlet import Policy
    class Order(Model):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update'))}
        class Item(Child):
            qty = Decimal(default='1')
            amount = Decimal(compute='qty * 2')
        class Note(Child):
            body = Text()
        items = Table(Item)
        notes = Table(Note)
    registry = Registry(Order)
    policy = Policy(registry)
    editor, viewer = User(2, 'e', 'editor'), User(3, 'v', 'viewer')
    for child in (table.model for table in registry.children):
        assert policy.rule(editor, child).operations == ('read', 'create', 'update', 'delete')
        assert policy.rule(viewer, child).operations == ()
    assert policy.rule(editor, Order).operations == ('read', 'create', 'update')


def test_draft_returns_only_source_outputs_used_by_readable_fields(tmp_path):
    @depends('item')
    def stock_of(doc):
        return {'qty': D(5), 'cost': D('12.34')}
    class Order(Model):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete'), read_fields=('item', 'rate', 'available'))}
        item = Text()
        rate = Currency()
        stock = Query(stock_of, fields={'qty': Decimal(), 'cost': Currency()})
        available = Decimal(compute='stock.qty', stored=False)
        margin = Currency(compute='rate - stock.cost', stored=False)
    app = application(tmp_path, Order)
    status, result, _ = client(app)('POST', '/api/_draft/order', {'values': {'item': 'A', 'rate': '20'}})
    assert status == 200, result
    assert result['values']['_sources'] == {'stock': {'qty': '5.0000'}}
    assert 'margin' not in result['values'] and '12.34' not in str(result)
    order = next(model for model in client(app)('GET', '/api/schema')[1]['models'] if model['name'] == 'order')
    assert set(order['sources']['stock']) == {'qty'}


def test_legacy_child_table_under_document_parent_saves(tmp_path):
    from evlet import Link, Integer
    from evlet.business import ChildTable
    class Parent(Model):
        permissions = ALL
        name = Text()
    class Line(Model):
        permissions = ALL
        owner = Link(Parent, required=True)
        qty = Integer()
    db = Database(tmp_path / 'db.sqlite3', Registry(Parent, Line))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      children=(ChildTable(Parent, 'lines', Line, 'owner', 'Lines'),))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    c = client(app)
    status, created, _ = c('POST', '/api/parent', {'name': 'p', 'children': {'lines': [{'qty': 1}, {'qty': 2}]}})
    assert status == 201, created
    assert [row['qty'] for row in created['children']['lines']] == [1, 2]
    _, current, headers = c('GET', f"/api/parent/{created['id']}")
    status, updated, _ = c('PATCH', f"/api/parent/{created['id']}", {'name': 'q', 'children': {'lines': [current['children']['lines'][1]]}},
                           headers={b'if-match': headers[b'etag']})
    assert status == 200, updated
    assert updated['name'] == 'q' and [row['qty'] for row in updated['children']['lines']] == [2]
    with db.transaction() as tx:
        assert [line.owner for line in tx.list(Line)] == [created['id']]


def test_action_on_model_without_permissions_runs(tmp_path):
    from evlet import action
    class Ticket(Model):
        name = Text()
        @action
        def close(self, note: str = ''):
            self.name = 'closed ' + note
    app = application(tmp_path, Ticket)
    c = client(app)
    _, ticket, _ = c('POST', '/api/ticket', {'name': 'open'})
    _, _, headers = c('GET', f"/api/ticket/{ticket['id']}")
    status, result, _ = c('POST', f"/api/ticket/{ticket['id']}/actions/close", {'note': 'now'}, headers={b'if-match': headers[b'etag']})
    assert status == 200, result
    assert result['name'] == 'closed now'


def test_child_and_inherited_permissions_are_kept():
    from evlet import Policy
    admin, editor, viewer = User(1, 'a', 'admin'), User(2, 'e', 'editor'), User(3, 'v', 'viewer')
    class Order(Model):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'viewer': Rule(('read',))}
        class Secret(Child):
            permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
            note = Text()
        secrets = Table(Secret)
    class Base(Model):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',))}
    class Invoice(Base):
        y = Text(hidden=True)
    class Sheet(Model):
        class Line(Child):
            qty = Decimal()
        lines = Table(Line)
    registry = Registry(Order, Invoice, Sheet)
    policy = Policy(registry)
    secret = next(table.model for table in registry.children if table.name == 'secrets')
    line = next(table.model for table in registry.children if table.name == 'lines')
    assert policy.rule(viewer, secret).operations == () and policy.rule(editor, secret).operations == ()
    assert policy.rule(admin, secret).operations == ('read', 'create', 'update', 'delete')
    assert policy.rule(editor, Invoice).operations == ('read',) and policy.rule(viewer, Invoice).operations == ()
    assert 'y' not in policy.fields(editor, Invoice)
    assert policy.rule(admin, Sheet).operations == () and policy.rule(admin, line).operations == ()
