"""Phase 4: State, Move, state locks, model actions with inputs and availability, on real SQLite."""

import pytest

from evlet import (Action, Boolean, Child, Currency, Database, Date, Decimal, Integer, Link, Model, Move, Registry, Rule,
                   State, Table, Text, ValidationError, action)
from evlet.auth import AccessError, User
from evlet.calc import F
from evlet.forms import Dialog
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import ALL, add_user, start

HIDDEN = {**ALL, 'editor': Rule(('read', 'create', 'update'), read_fields=('customer', 'name', 'remarks', 'approved', 'total',
                                                                           'reference', 'status')),
          'viewer': Rule(('read',), read_fields=('name', 'status', 'total'))}


class Customer(Model):
    permissions = ALL
    name = Text(default='')
    limit = Currency(default=0)


class Part(Child):
    code = Text(default='')


class InvoiceLine(Child):
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=F('qty') * F('rate'))
    note = Text(default='', editable_when_locked=True)
    parts = Table(Part)


class Invoice(Model):
    Meta = FiniteMeta
    permissions = HIDDEN
    customer = Link(Customer)
    name = Text(default='')
    remarks = Text(default='', editable_when_locked=True)
    approved = Boolean(default=False)
    secret = Currency(default=0)
    lines = Table(InvoiceLine)
    total = Currency(compute=lines.sum('amount'))
    reference = Text(default='', locked_when=F('status') != 'draft')
    status = State(('draft', 'issued', 'paid', 'void'), moves=(
        Move('draft', 'issued', 'Issue', when=(F('total') > 0) & (F('customer.limit') >= 0) & (F('secret') < 1000), run='on_issue'),
        Move('issued', 'paid', 'Mark paid', roles=('admin',), when=F('approved'), run='on_paid'),
        Move(('draft', 'issued'), 'void', 'Void', roles=('admin',)),
    ), locked=('issued', 'paid', 'void'), final=('paid', 'void'))

    def on_issue(self):
        if self.name == 'fail':
            raise ValidationError({'name': 'Run failed'})
        self.reference = 'INV-' + str(self.id)
        self.remarks = 'issued'

    def on_paid(self):
        # Runs on a locked record: it may change `name`, but `reference` keeps its locked_when.
        self.name = 'Paid'
        if self.remarks == 'rewrite':
            self.reference = 'CHANGED'

    @action('Record payment', inputs={'amount': Currency(required=True, minimum='0.01'), 'paid_on': Date(required=True), 'memo': Text()},
            available_when=F('status') == 'issued')
    def record_payment(self, amount, paid_on, memo=None):
        self.name = f'paid {amount} on {paid_on.isoformat()}' + (f' ({memo})' if memo else '')
        return {'amount': str(amount)}

    @action('Change reference', inputs={'value': Text(required=True)})
    def change_reference(self, value):
        self.reference = value

    @action('Hidden check', available_when=F('secret') < 10)
    def hidden_check(self):
        self.remarks = 'checked'

    class Rename(Dialog):
        action = 'rename'
        new_name = Text(required=True)

        def submit(self, source):
            self.context.update(Invoice, source.id, name=self.new_name)
            return {'renamed': True}


MODELS = (Customer, Invoice)


@pytest.fixture
def invoices(tmp_path):
    return seed(start(Database(tmp_path / 'db.sqlite3', Registry(*MODELS))))


def seed(app):
    c = client(app)
    customer = c('POST', '/api/customer', {'name': 'Acme', 'limit': '50'})[1]['id']
    return app, c, customer


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


def move(c, url, to, headers=None):
    return c('POST', f'{url}/moves/{to}', {}, headers=etag(c, url) if headers is None else headers)


def new_invoice(c, customer=None, **values):
    body = {'name': 'Invoice', 'customer': customer, 'children': {'lines': [{'_key': 'a', 'qty': '2', 'rate': '5',
                                                                              'children': {'parts': [{'_key': 'p', 'code': 'X'}]}}]}, **values}
    status, record, _ = c('POST', '/api/invoice', body)
    assert status == 201, record
    return record, f"/api/invoice/{record['id']}"


# Declarations

def test_state_and_move_declarations_are_checked():
    for options in ({'locked': ('x',)}, {'final': ('x',)}, {'voidable': ('a',)}, {'default': 'x'}, {'locked': 'a'},
                    {'moves': (Move('a', 'x'),)}, {'moves': (Move('b', 'a'),), 'final': ('b',)},
                    {'moves': (Move('a', 'b'), Move(('a', 'c'), 'b'))}, {'moves': ('a',)},
                    {'voidable': ('b',), 'locked': ('b',), 'final': ('b',)}, {'required': False}, {'locked_when': F('a')}):
        with pytest.raises(ValueError):
            State(('a', 'b', 'c'), **options)
    for arguments, options in ((('a', 'a'), {}), (((), 'b'), {}), (('a', 'b'), {'roles': ('owner',)}), (('a', 'b'), {'run': 'not valid'}),
                               (('a', 'b'), {'when': 'total > 0'}), (('a', 'b'), {'label': ''}), (('a', ''), {})):
        with pytest.raises(ValueError):
            Move(*arguments, **options)
    assert State(('a', 'b')).default == 'a' and Move('a', 'b').label == 'B'
    with pytest.raises(ValueError, match='more than one State'):
        type('Twice', (Model,), {'one': State(('a', 'b')), 'two': State(('a', 'b'))})
    with pytest.raises(ValueError, match='root document'):
        Registry(type('Holder', (Model,), {'rows': Table(type('Stated', (Child,), {'status': State(('a', 'b'))}))}))
    with pytest.raises(ValueError, match='not a method'):
        Registry(type('Runs', (Model,), {'status': State(('a', 'b'), moves=(Move('a', 'b', run='missing'),))}))
    with pytest.raises(ValueError, match='editable_when_locked'):
        Text(editable_when_locked='yes')
    with pytest.raises(ValueError):
        action(inputs={'amount': 'Currency'})(lambda self, amount: None)
    with pytest.raises(ValueError):
        action(inputs={'amount': Currency(compute=F('x'))})(lambda self, amount: None)
    with pytest.raises(ValueError, match='declared inputs'):
        action(inputs={'amount': Currency()})(lambda self, other: None)
    with pytest.raises(ValueError, match='declared inputs'):
        action(inputs={'amount': Currency()})(lambda self: None)


def test_the_state_field_is_part_of_the_finite_plan_and_readable_in_rules():
    registry = Registry(*MODELS)
    fields = registry.plans[Invoice]['tables']['$root']['fields']
    assert fields['status']['type'] == 'text' and fields['status']['default'] == {'value': 'draft'}
    assert any(entry['field'] == 'reference' and entry.get('kind') == 'locked_when' for entry in registry.plans[Invoice]['fields'])


# Ordinary writes

def check_ordinary_writes_cannot_set_the_state(invoices):
    app, c, customer = invoices
    record, url = new_invoice(c, customer)
    assert record['status'] == 'draft'
    assert new_invoice(c, customer, status='draft')[0]['status'] == 'draft'
    status, body, _ = c('POST', '/api/invoice', {'status': 'issued'})
    assert status == 422 and body['fields'] == {'status': 'A new record starts as draft; use its moves to change it'}
    status, body, _ = c('PATCH', url, {'status': 'issued'}, headers=etag(c, url))
    assert status == 422 and body['fields'] == {'status': 'This changes only through its moves'}
    # Sending the saved value back is not a change.
    assert c('PATCH', url, {'status': 'draft', 'name': 'Renamed'}, headers=etag(c, url))[1]['name'] == 'Renamed'
    with app.business.context(User(1, 'admin', 'admin')) as context:
        with pytest.raises(ValidationError):
            context.update(Invoice, record['id'], status='void')


def test_ordinary_writes_cannot_set_the_state(invoices):
    check_ordinary_writes_cannot_set_the_state(invoices)


# Moves

def check_moves_check_state_role_guard_and_revision(invoices):
    app, c, customer = invoices
    record, url = new_invoice(c, customer)
    assert c('POST', url + '/moves/issued', {})[0] == 428
    assert c('POST', url + '/moves/issued', {}, headers={b'if-match': b'"stale"'})[0] == 409
    assert move(c, url, 'unknown')[0] == 404
    assert move(c, url, 'paid')[1] == {'error': 'This record cannot move from draft to paid'}
    assert c('POST', url + '/moves/issued', {'status': 'issued'}, headers=etag(c, url))[0] == 422
    # Guard pending (no customer), false (no total), then a role that the move does not list.
    pending, url_pending = new_invoice(c)
    status, body, _ = move(c, url_pending, 'issued')
    assert status == 409 and body['error'] == 'Issue is not available: the condition for this move needs values that are not filled in yet'
    empty, url_empty = new_invoice(c, customer, children={'lines': []})
    assert move(c, url_empty, 'issued')[1]['error'] == 'Issue is not available: the condition for this move is not met'
    viewer, editor = add_user(app, 'viewer'), add_user(app, 'editor')
    assert move(viewer, url, 'issued')[0] == 403
    assert move(editor, url, 'void')[1] == {'error': 'Your role cannot use Void'}
    # The editor can issue: the run method sets fields in the same transaction and the ETag changes.
    before = etag(c, url)
    status, issued, headers = move(editor, url, 'issued')
    assert status == 200, issued
    assert (issued['status'], issued['reference'], issued['remarks']) == ('issued', f"INV-{record['id']}", 'issued')
    assert headers[b'etag'] != before[b'if-match'] and headers[b'etag'] == editor('GET', url)[2][b'etag']
    assert issued['_moves'] == [{'to': 'paid', 'label': 'Mark paid', 'allowed': False, 'reason': 'Your role cannot use this move'},
                                {'to': 'void', 'label': 'Void', 'allowed': False, 'reason': 'Your role cannot use this move'}]
    assert issued['_locked'] is True
    assert move(c, url, 'paid')[1]['error'] == 'Mark paid is not available: the condition for this move is not met'
    with app.business.context(User(1, 'admin', 'admin')) as context:
        context.update(Invoice, record['id'], remarks='rewrite')
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(Invoice, record['id'], approved=True)
    status, body, _ = move(c, url, 'paid')
    assert status == 422 and body['fields'] == {'reference': 'This field is locked and cannot be changed now'}
    assert c('GET', url)[1]['status'] == 'issued'
    with app.business.context(User(1, 'admin', 'admin')) as context:
        context.update(Invoice, record['id'], remarks='approved')
    paid = move(c, url, 'paid')[1]
    assert (paid['status'], paid['name'], paid['reference']) == ('paid', 'Paid', f"INV-{record['id']}")
    assert move(c, url, 'void')[1] == {'error': 'This record is paid, which is final; it has no moves'}
    assert c('GET', url)[1]['_moves'] == []


def test_moves_check_state_role_guard_and_revision(invoices):
    check_moves_check_state_role_guard_and_revision(invoices)


def check_a_failing_run_rolls_the_move_back(invoices):
    app, c, customer = invoices
    record, url = new_invoice(c, customer, name='fail')
    status, body, _ = move(c, url, 'issued')
    assert status == 422 and body['fields'] == {'name': 'Run failed'}
    after = c('GET', url)[1]
    assert (after['status'], after['reference'], after['remarks']) == ('draft', '', '')


def test_a_failing_run_rolls_the_move_back(invoices):
    check_a_failing_run_rolls_the_move_back(invoices)


def test_moves_from_python_use_the_same_checks(invoices, tmp_path):
    app, c, customer = invoices
    record, url = new_invoice(c, customer)
    with app.business.context(User(0, 'viewer', 'viewer')) as context:
        with pytest.raises(AccessError):
            context.move(Invoice, record['id'], 'issued')
    with pytest.raises(AccessError, match='cannot move from draft to paid'):
        with app.business.context(User(1, 'admin', 'admin')) as context:
            context.move(Invoice, record['id'], 'paid')
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.move(Invoice, record['id'], 'issued')['status'] == 'issued'
        assert context.get(Invoice, record['id']).status == 'issued'

    class Watched(Model):
        status = State(('open', 'done'), moves=(Move('open', 'done', 'Finish'),))
        changes = []

        def changed(self, change):
            Watched.changes.append((change.operation, change.before.status if change.before else None, change.after.status))
    app = start(Database(tmp_path / 'watched.sqlite3', Registry(Watched)))
    with app.business.context(User(1, 'admin', 'admin')) as context:
        created = context.create(Watched)
        context.move(Watched, created['id'], 'done')
    assert Watched.changes == [('create', None, 'open'), ('move', 'open', 'done')]


# Locks

def check_locked_records_refuse_ordinary_writes_at_every_depth(invoices):
    app, c, customer = invoices
    record, url = new_invoice(c, customer)
    line = record['children']['lines'][0]
    part = line['children']['parts'][0]
    move(c, url, 'issued')
    locked = 'This record is issued, so it is locked'
    assert c('PATCH', url, {'name': 'Changed'}, headers=etag(c, url))[1]['fields'] == {'name': locked}
    assert c('PATCH', url, {'children': {'lines': {'create': [{'qty': '1'}]}}}, headers=etag(c, url))[1]['fields'] == {'lines': locked}
    assert c('PATCH', url, {'children': {'lines': {'delete': [line['id']]}}}, headers=etag(c, url))[1]['fields'] == {'lines': locked}
    assert c('PATCH', url, {'children': {'lines': {'update': [{'id': line['id'], 'qty': '9'}]}}},
             headers=etag(c, url))[1]['fields'] == {f"lines.{line['id']}.qty": locked}
    nested = {'lines': {'update': [{'id': line['id'], 'children': {'parts': {'update': [{'id': part['id'], 'code': 'Y'}]}}}]}}
    assert c('PATCH', url, {'children': nested}, headers=etag(c, url))[1]['fields'] == {f"lines.{line['id']}.parts.{part['id']}.code": locked}
    added = {'lines': {'update': [{'id': line['id'], 'children': {'parts': {'create': [{'code': 'Z'}]}}}]}}
    assert c('PATCH', url, {'children': added}, headers=etag(c, url))[1]['fields'] == {f"lines.{line['id']}.parts": locked}
    # Fields declared editable_when_locked still change, on the record and on its rows.
    status, saved, _ = c('PATCH', url, {'remarks': 'Paid by transfer', 'children': {'lines': {'update': [{'id': line['id'], 'note': 'ok'}]}}},
                         headers=etag(c, url))
    assert status == 200 and saved['remarks'] == 'Paid by transfer' and saved['children']['lines'][0]['note'] == 'ok'
    assert c('DELETE', url, headers=etag(c, url))[1] == {'error': 'This record is issued, so it is locked and cannot be deleted'}
    with app.business.context(User(1, 'admin', 'admin')) as context:
        with pytest.raises(ValidationError):
            context.update(Invoice, record['id'], name='Python')
        with pytest.raises(AccessError):
            context.delete(Invoice, record['id'])
    dialog = f"/api/_dialogs/invoice/{record['id']}/rename/submit"
    status, body, _ = c('POST', dialog, {'key': 'rename-once', 'values': {'new_name': 'Dialog'}}, headers=etag(c, url))
    assert status == 422 and body['fields'] == {'name': locked}
    assert c('GET', url)[1]['name'] == 'Invoice'


def test_locked_records_refuse_ordinary_writes_at_every_depth(invoices):
    check_locked_records_refuse_ordinary_writes_at_every_depth(invoices)


def test_draft_records_stay_editable_and_deletable(invoices):
    app, c, customer = invoices
    record, url = new_invoice(c, customer)
    dialog = f"/api/_dialogs/invoice/{record['id']}/rename/submit"
    assert c('POST', dialog, {'key': 'rename-draft', 'values': {'new_name': 'Dialog'}}, headers=etag(c, url))[0] == 200
    assert c('GET', url)[1]['name'] == 'Dialog'
    assert c('DELETE', url, headers=etag(c, url))[0] == 204


# Actions

def check_actions_validate_inputs_and_availability(invoices):
    app, c, customer = invoices
    record, url = new_invoice(c, customer)
    action_url = url + '/actions/record_payment'
    pay = {'amount': '12.50', 'paid_on': '2026-11-01'}
    assert c('POST', action_url, pay, headers=etag(c, url))[1] == {
        'error': 'Record payment is not available: the condition for this action is not met'}
    move(c, url, 'issued')
    assert c('POST', action_url, {'paid_on': '2026-11-01'}, headers=etag(c, url))[1]['fields'] == {'amount': 'This field is required'}
    status, body, _ = c('POST', action_url, {'amount': 'twelve', 'paid_on': '1 Nov'}, headers=etag(c, url))
    assert status == 422 and set(body['fields']) == {'amount', 'paid_on'}
    assert c('POST', action_url, {**pay, 'amount': '0'}, headers=etag(c, url))[0] == 422
    assert c('POST', action_url, {**pay, 'extra': 1}, headers=etag(c, url))[1]['fields'] == {'extra': 'Unknown input'}
    # The action changes a locked record; locked_when still applies to what it writes.
    status, paid, _ = c('POST', action_url, {**pay, 'memo': 'bank'}, headers=etag(c, url))
    assert status == 200, paid
    assert paid['name'] == 'paid 12.50 on 2026-11-01 (bank)' and paid['_outcome'] == {'amount': '12.50'}
    status, body, _ = c('POST', url + '/actions/change_reference', {'value': 'NEW'}, headers=etag(c, url))
    assert status == 422 and body['fields'] == {'reference': 'This field is locked and cannot be changed now'}
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.action(Invoice, record['id'], 'record_payment', amount='3', paid_on='2026-11-02')['name'] == 'paid 3.00 on 2026-11-02'
        with pytest.raises(ValidationError):
            context.action(Invoice, record['id'], 'record_payment', amount='3')
    draft, draft_url = new_invoice(c, customer)
    assert c('POST', draft_url + '/actions/change_reference', {'value': 'NEW'}, headers=etag(c, draft_url))[1]['reference'] == 'NEW'


def test_actions_validate_inputs_and_availability(invoices):
    check_actions_validate_inputs_and_availability(invoices)


def test_details_show_moves_and_actions_per_role_without_leaks(invoices):
    app, c, customer = invoices
    record, url = new_invoice(c)
    editor, viewer = add_user(app, 'editor'), add_user(app, 'viewer')
    admin_view = c('GET', url)[1]
    assert admin_view['_locked'] is False
    assert admin_view['_moves'] == [
        {'to': 'issued', 'label': 'Issue', 'allowed': False, 'reason': 'The condition for this move needs values that are not filled in yet'},
        {'to': 'void', 'label': 'Void', 'allowed': True}]
    assert {item['name']: item for item in admin_view['_actions']}['record_payment'] == {
        'name': 'record_payment', 'label': 'Record payment', 'allowed': False, 'reason': 'The condition for this action is not met'}
    assert {item['name']: item['allowed'] for item in admin_view['_actions']} == {
        'record_payment': False, 'change_reference': True, 'hidden_check': True}
    # The editor cannot read `secret`, which the Issue guard and Hidden check read: the reasons are generic.
    editor_view = editor('GET', url)[1]
    assert 'secret' not in editor_view
    assert editor_view['_moves'] == [
        {'to': 'issued', 'label': 'Issue', 'allowed': False, 'reason': 'This move is not available now'},
        {'to': 'void', 'label': 'Void', 'allowed': False, 'reason': 'Your role cannot use this move'}]
    assert {item['name']: item for item in editor_view['_actions']}['hidden_check'] == {
        'name': 'hidden_check', 'label': 'Hidden check', 'allowed': True}
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(Invoice, record['id'], secret=50)
    assert {item['name']: item for item in editor('GET', url)[1]['_actions']}['hidden_check']['reason'] == 'This action is not available now'
    assert editor('POST', url + '/actions/hidden_check', {}, headers=etag(editor, url))[1] == {
        'error': 'Hidden check is not available: this action is not available now'}
    viewer_view = viewer('GET', url)[1]
    assert [item['reason'] for item in viewer_view['_moves']] == ['Your role cannot use this move'] * 2
    assert viewer_view['_actions'] == []
    # The ETag does not depend on the availability keys.
    assert c('GET', url)[2][b'etag'] == c('GET', url)[2][b'etag']


def test_schema_describes_states_and_actions_per_role(invoices):
    app, c, customer = invoices
    editor, viewer = add_user(app, 'editor'), add_user(app, 'viewer')

    def entry(user):
        return next(model for model in user('GET', '/api/schema')[1]['models'] if model['name'] == 'invoice')
    admin = entry(c)
    state = admin['state']
    assert {key: state[key] for key in ('field', 'states', 'default', 'locked', 'final', 'voidable')} == {
        'field': 'status', 'states': ['draft', 'issued', 'paid', 'void'], 'default': 'draft',
        'locked': ['issued', 'paid', 'void'], 'final': ['paid', 'void'], 'voidable': []}
    issue = state['moves'][0]
    assert (issue['from'], issue['to'], issue['label'], issue['roles'], issue['guarded']) == (['draft'], 'issued', 'Issue', ['admin', 'editor'], True)
    assert issue['when']['$expr'] == 'and' and state['moves'][2]['when'] is None and state['moves'][2]['guarded'] is False
    status_field = next(field for field in admin['fields'] if field['name'] == 'status')
    assert status_field['read_only'] and status_field['state'] and status_field['choices'] == ['draft', 'issued', 'paid', 'void']
    assert next(field for field in admin['fields'] if field['name'] == 'remarks')['editable_when_locked'] is True
    actions = {item['name']: item for item in admin['actions']}
    payment = actions['record_payment']
    assert [field['name'] for field in payment['inputs']] == ['amount', 'paid_on', 'memo']
    assert payment['inputs'][0]['type'] == 'currency' and payment['inputs'][0]['required'] is True
    assert payment['arguments'] == [{'name': 'amount', 'type': 'str', 'required': True}, {'name': 'paid_on', 'type': 'str', 'required': True},
                                    {'name': 'memo', 'type': 'str', 'required': False}]
    assert payment['available_when'] == {'$expr': 'eq', 'args': [{'$expr': 'ref', 'scope': 'self', 'field': 'status'},
                                                                 {'$expr': 'literal', 'type': 'text', 'value': 'issued'}]}
    assert payment['roles'] == ['admin', 'editor'] and actions['hidden_check']['available_when'] is not None
    restricted = entry(editor)
    assert restricted['state']['moves'][0]['when'] is None and restricted['state']['moves'][0]['guarded'] is True
    assert {item['name']: item for item in restricted['actions']}['hidden_check']['available_when'] is None
    # The viewer cannot read `approved`, which Mark paid reads.
    assert entry(viewer)['actions'] == [] and entry(viewer)['state']['moves'][1]['when'] is None


# Legacy roots and legacy actions

class Ticket(Model):
    title = Text(default='')
    status = State(('open', 'closed'), moves=(Move('open', 'closed', 'Close'), Move('closed', 'open', 'Reopen', when=F('title') != '')),
                   locked=('closed',))


def force_open(tx, user, ticket):
    tx.update(Ticket, ticket.id, title=ticket.title + ' (forced)')


def test_states_and_legacy_actions_work_on_roots_without_calculations(tmp_path):
    from evlet import Application, LocalAuth
    from test_business import http, PASSWORD
    registry = Registry(Ticket)
    assert Ticket not in registry.plans and Ticket in registry.guard_plans
    db = Database(tmp_path / 'tickets.sqlite3', registry)
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      actions=(Action(Ticket, 'force', 'Force', force_open),))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    c = client(app)
    ticket = c('POST', '/api/ticket', {'title': ''})[1]
    url = f"/api/ticket/{ticket['id']}"
    assert move(c, url, 'closed')[1]['status'] == 'closed'
    assert c('PATCH', url, {'title': 'x'}, headers=etag(c, url))[0] == 422
    assert move(c, url, 'open')[0] == 409
    # A legacy Action is trusted code: it writes through the transaction, also on a locked record.
    status, forced, _ = c('POST', url + '/actions/force', {}, headers=etag(c, url))
    assert status == 200 and forced['title'] == ' (forced)'
    assert move(c, url, 'open')[1]['status'] == 'open'
    schema = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'ticket')
    assert schema['actions'] == [{'name': 'force', 'label': 'Force'}] and schema['state']['moves'][1]['guarded'] is True


# Review fixes

class Product(Model):
    permissions = ALL
    price = Currency(default=0)


class PricedLine(Child):
    product = Link(Product)
    price = Currency(compute=F('product.price'))
    note = Text(default='', editable_when_locked=True)


class Priced(Model):
    Meta = FiniteMeta
    permissions = ALL
    product = Link(Product)
    price = Currency(compute=F('product.price'))
    remarks = Text(default='', editable_when_locked=True)
    lines = Table(PricedLine)
    status = State(('draft', 'issued', 'paid'), moves=(Move('draft', 'issued', 'Issue'), Move('issued', 'paid', 'Pay')),
                   locked=('issued', 'paid'))


def test_locked_records_keep_saved_results_on_ordinary_writes(tmp_path):
    app = start(Database(tmp_path / 'priced.sqlite3', Registry(Product, Priced)))
    c = client(app)
    product = c('POST', '/api/product', {'price': '10'})[1]['id']
    record = c('POST', '/api/priced', {'product': product, 'children': {'lines': [{'_key': 'a', 'product': product}]}})[1]
    url, line = f"/api/priced/{record['id']}", record['children']['lines'][0]['id']
    assert move(c, url, 'issued')[0] == 200
    c('PATCH', f'/api/product/{product}', {'price': '99'}, headers=etag(c, f'/api/product/{product}'))
    # Editing the remarks and a line note keeps the prices the record was issued with, at every depth.
    saved = c('PATCH', url, {'remarks': 'Called', 'children': {'lines': {'update': [{'id': line, 'note': 'ok'}]}}}, headers=etag(c, url))[1]
    assert (saved['remarks'], saved['price'], saved['children']['lines'][0]['price']) == ('Called', '10.00', '10.00')
    assert saved['children']['lines'][0]['note'] == 'ok' and c('GET', url)[1]['price'] == '10.00'
    plan = app.business.registry.plan_for(Priced)
    status, body, _ = c('PATCH', url, {'remarks': 'Again', '_calculation': {'model_schema_hash': plan['model_schema_hash'], 'recalculate': True}},
                        headers=etag(c, url))
    assert status == 409 and 'cannot be recalculated' in body['error']
    # A move settles normally, so it takes the current price.
    paid = move(c, url, 'paid')[1]
    assert (paid['status'], paid['price'], paid['children']['lines'][0]['price']) == ('paid', '99.00', '99.00')


def test_stored_results_cannot_read_fields_editable_while_locked():
    class Line(Child):
        note = Text(default='', editable_when_locked=True)
        copied = Text(compute=F('note'))

    for fields in ({'remarks': Text(default='', editable_when_locked=True), 'shown': Text(compute=F('remarks'))},
                   {'lines': Table(Line)},
                   {'remarks': Text(default='', editable_when_locked=True), 'legacy': Text(compute='remarks')}):
        model = type('Bad', (Model,), {'status': State(('a', 'b'), locked=('b',)), **fields})
        with pytest.raises(ValueError, match='editable while locked'):
            Registry(model)
    # A virtual result may read it, and without a State the option changes nothing.
    Registry(type('Fine', (Model,), {'status': State(('a', 'b'), locked=('b',)), 'remarks': Text(default='', editable_when_locked=True),
                                     'shown': Text(compute=F('remarks'), stored=False)}))
    Registry(type('Plain', (Model,), {'remarks': Text(default='', editable_when_locked=True), 'shown': Text(compute=F('remarks'))}))


class Plain(Model):
    title = Text(default='')


class Folder(Model):
    title = Text(default='')
    status = State(('open', 'closed'), moves=(Move('open', 'closed', 'Close'), Move('closed', 'open', 'Open')), locked=('closed',))


def test_attachments_on_document_models_and_locked_records(tmp_path):
    from evlet import Application, LocalAuth
    from test_business import http, PASSWORD
    db = Database(tmp_path / 'files.sqlite3', Registry(Plain, Folder, Invoice, Customer))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      attachments=(Plain, Folder, Invoice))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    c = client(app)
    upload = {'name': 'a.txt', 'content': 'YQ=='}
    for name in ('plain', 'folder', 'invoice'):
        record = c('POST', f'/api/{name}', {})[1]['id']
        status, added, _ = c('POST', f'/api/{name}/{record}/attachments', upload)
        assert status == 201, (name, added)
        assert c('GET', f'/api/{name}/{record}/attachments')[1]['items'][0]['name'] == 'a.txt'
    folder = c('POST', '/api/folder', {})[1]['id']
    url = f'/api/folder/{folder}'
    first = c('POST', url + '/attachments', upload)[1]['id']
    assert move(c, url, 'closed')[0] == 200
    locked = 'This record is closed, so it is locked, so its attachments cannot change'
    assert c('POST', url + '/attachments', upload)[1] == {'error': locked}
    assert c('DELETE', url + f'/attachments/{first}')[1] == {'error': locked}
    assert c('GET', url + f'/attachments/{first}')[0] == 200
    assert move(c, url, 'open')[0] == 200
    assert c('DELETE', url + f'/attachments/{first}')[0] == 204
    # A viewer still cannot upload, as on plain models.
    viewer = add_user(app, 'viewer')
    assert viewer('POST', url + '/attachments', upload)[0] == 403


class Blind(Model):
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'), read_fields=('title',), write_fields=('title',))}
    title = Text(default='')
    status = State(('a', 'b', 'c'), moves=(Move('a', 'b', 'B'), Move('b', 'c', 'C', roles=('admin',))), locked=('b', 'c'), final=('c',))

    @action('Touch')
    def touch(self):
        self.title = 'touched'


def test_messages_do_not_name_the_state_to_roles_that_cannot_read_it(tmp_path):
    app = start(Database(tmp_path / 'blind.sqlite3', Registry(Blind)))
    c, editor = client(app), add_user(app, 'editor')
    record = c('POST', '/api/blind', {})[1]
    url = f"/api/blind/{record['id']}"
    assert move(editor, url, 'c')[1] == {'error': 'This record has no such move now'}
    assert move(c, url, 'c')[1] == {'error': 'This record cannot move from a to c'}
    view = editor('GET', url)[1]
    assert 'status' not in view and '_moves' not in view and '_locked' not in view
    assert view['_actions'] == [{'name': 'touch', 'label': 'Touch', 'allowed': True}]
    assert move(editor, url, 'b')[0] == 200
    assert editor('PATCH', url, {'title': 'x'}, headers=etag(editor, url))[1]['fields'] == {'title': 'This record is locked'}
    assert editor('DELETE', url, headers=etag(editor, url))[1] == {'error': 'This record is locked and cannot be deleted'}
    assert c('PATCH', url, {'title': 'x'}, headers=etag(c, url))[1]['fields'] == {'title': 'This record is b, so it is locked'}
    assert move(c, url, 'c')[0] == 200
    assert move(editor, url, 'a')[1] == {'error': 'This record has no such move now'}
    assert move(c, url, 'a')[1] == {'error': 'This record is c, which is final; it has no moves'}


class Sheet(Model):
    title = Text(default='')
    qty = Integer(default=1)
    double = Integer(compute='qty * 2')
    status = State(('open', 'closed'), moves=(Move('open', 'closed', 'Close', when=F('double') > 2),), locked=('closed',))

    @action('Bump', available_when=F('status') == 'open')
    def bump(self):
        self.qty = (self.qty or 0) + 1


def test_guards_work_on_roots_with_legacy_string_calculations(tmp_path):
    registry = Registry(Sheet)
    assert Sheet not in registry.plans and Sheet in registry.guard_plans
    app = start(Database(tmp_path / 'sheet.sqlite3', registry))
    c = client(app)
    url = f"/api/sheet/{c('POST', '/api/sheet', {})[1]['id']}"
    assert move(c, url, 'closed')[1]['error'] == 'Close is not available: the condition for this move is not met'
    assert c('POST', url + '/actions/bump', {}, headers=etag(c, url))[1]['double'] == 4
    closed = move(c, url, 'closed')[1]
    assert closed['status'] == 'closed' and closed['_actions'][0]['allowed'] is False
    # The locked record keeps its stored legacy result too.
    assert c('PATCH', url, {'qty': 5}, headers=etag(c, url))[0] == 422


def test_sessions_change_a_state_only_inside_maintenance(tmp_path):
    from evlet import Application, LocalAuth
    from test_business import http, PASSWORD
    db = Database(tmp_path / 'tickets.sqlite3', Registry(Ticket))
    db.initialize()
    with db.transaction() as tx:
        ticket = tx.create(Ticket, title='t')
        assert tx.create(Ticket, title='u', status='open').status == 'open'
        with pytest.raises(ValueError, match='changes only through its moves'):
            tx.create(Ticket, status='closed')
        with pytest.raises(ValueError, match='changes only through its moves'):
            tx.update(Ticket, ticket.id, status='closed')
        assert tx.update(Ticket, ticket.id, title='v', status='open').title == 'v'
        with tx.maintenance():
            assert tx.update(Ticket, ticket.id, status='closed').status == 'closed'

    def close(tx, user, record):
        tx.update(Ticket, record.id, status='closed')
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      actions=(Action(Ticket, 'close', 'Close', close),))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    c = client(app)
    url = f"/api/ticket/{c('POST', '/api/ticket', {})[1]['id']}"
    status, body, _ = c('POST', url + '/actions/close', {}, headers=etag(c, url))
    assert status == 500 and body == {'error': 'Internal server error'}
    assert c('GET', url)[1]['status'] == 'open'


def test_action_inputs_are_copied_and_cannot_shadow_context_arguments():
    shared = Currency(required=True)
    first = action(inputs={'amount': shared})(lambda self, amount: None)
    second = action(inputs={'total': shared})(lambda self, total: None)
    assert shared.name == '' and first.__action__['inputs']['amount'].name == 'amount'
    assert second.__action__['inputs']['total'].name == 'total' and first.__action__['inputs']['amount'] is not shared
    for name in ('name', 'model', 'record_id', 'self'):
        with pytest.raises(ValueError, match='reserved'):
            action(inputs={name: Text()})(lambda self, **values: None)


class Crashing(Model):
    title = Text(default='')
    status = State(('a', 'b'), moves=(Move('a', 'b', 'Go', run='explode'),))

    def explode(self):
        self.title = 'half done'
        raise RuntimeError('secret internal detail')


def test_a_run_that_crashes_rolls_back_without_leaking_its_message(tmp_path):
    app = start(Database(tmp_path / 'crash.sqlite3', Registry(Crashing)))
    c = client(app)
    url = f"/api/crashing/{c('POST', '/api/crashing', {})[1]['id']}"
    assert move(c, url, 'b')[:2] == (500, {'error': 'Internal server error'})
    assert c('GET', url)[1]['status'] == 'a' and c('GET', url)[1]['title'] == ''


class BigLine(Child):
    qty = Decimal(default=1)
    rate = Currency(default=0)
    amount = Currency(compute=F('qty') * F('rate'))


class BigPlain(Model):
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='')
    lines = Table(BigLine, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))


class BigStated(Model):
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='')
    lines = Table(BigLine, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))
    status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue', when=F('total') > 0),), locked=('issued',))

    @action('Note', available_when=F('total') > 0)
    def note(self):
        self.title = 'noted'


def test_reading_a_stated_document_costs_at_most_one_more_statement(tmp_path):
    from test_lines import Counting, counted
    app = start(Counting(tmp_path / 'big.sqlite3', Registry(BigPlain, BigStated)))
    c = client(app)
    rows = [{'_key': f'l{index}', 'qty': '1', 'rate': '2.50'} for index in range(1000)]
    counts = {}
    for name in ('bigplain', 'bigstated'):
        record = c('POST', f'/api/{name}', {'children': {'lines': rows}})[1]
        (status, body, _), counts[name] = counted(c, 'GET', f"/api/{name}/{record['id']}")
        assert status == 200
    assert body['_moves'] == [{'to': 'issued', 'label': 'Issue', 'allowed': True}] and body['_actions'][0]['allowed'] is True
    # One statement for the guards over saved values, and one for the journal summary (`_journal`),
    # since a model with a State journals by default.
    assert body['_journal'] == {'count': 1, 'open_follow_ups': 0}
    assert counts['bigstated'] <= counts['bigplain'] + 2, counts


# Remaining lock and leak gaps

def test_stored_results_reading_editable_fields_through_legacy_formulas_and_fetches_are_rejected():
    class Owner(Model):
        name = Text(default='')

    class Extra(Child):
        extra = Integer(default=0, editable_when_locked=True)

    class Fixed(Child):
        qty = Integer(default=0)

    def build(fields, meta=False):
        namespace = {'status': State(('a', 'b'), locked=('b',)), **fields}
        if meta:
            namespace['Meta'] = FiniteMeta
        return Registry(Owner, type('Bad', (Model,), namespace))
    rejected = (
        {'lines': Table(Extra), 'total': Integer(compute='sum(lines.extra)')},
        {'remarks': Integer(default=0, editable_when_locked=True), 'v': Integer(compute='remarks * 2', stored=False),
         's': Integer(compute='v + 1')},
        {'owner': Link(Owner, editable_when_locked=True), 'owner_name': Text(fetch='owner.name')},
        {'owner': Link(Owner, editable_when_locked=True), 'owner_name': Text(fetch='owner.name', read_only=True)},
    )
    for fields in rejected:
        with pytest.raises(ValueError, match='editable while locked'):
            build(fields)
    # A finite stored result that reads a fetched copy of an editable Link is rejected too.
    with pytest.raises(ValueError, match='editable while locked'):
        build({'owner': Link(Owner, editable_when_locked=True), 'owner_name': Text(fetch='owner.name'),
               'shown': Text(compute=F('owner_name'))}, meta=True)
    # Counting rows reads no field; a virtual result and a fetch over a fixed Link stay allowed.
    build({'lines': Table(Extra), 'n': Integer(compute='count(lines)')})
    build({'lines': Table(Fixed), 'total': Integer(compute='sum(lines.qty)'), 'remarks': Integer(default=0, editable_when_locked=True),
           'v': Integer(compute='remarks * 2', stored=False)})
    build({'owner': Link(Owner), 'owner_name': Text(fetch='owner.name', read_only=True)})


class Account(Model):
    permissions = ALL
    name = Text(default='')
    limit = Currency(default=0)


class KeptLine(Child):
    qty = Decimal(default=1)
    note = Text(default='', editable_when_locked=True)


class Kept(Model):
    Meta = FiniteMeta
    permissions = ALL
    account = Link(Account)
    limit = Currency(compute=F('account.limit'))
    remarks = Text(default='', editable_when_locked=True, required_when=F('limit') > 50)
    memo = Text(default='', editable_when_locked=True, applies_when=F('limit') <= 50)
    lines = Table(KeptLine)
    status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued',))


class KeptLegacy(Model):
    permissions = ALL
    account = Link(Account)
    account_name = Text(fetch='account.name', read_only=True)
    remarks = Text(default='', editable_when_locked=True, required_if='account_name == "Big"')
    note = Text(default='', editable_when_locked=True)
    status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued',))


def test_rules_on_locked_records_read_the_saved_results(tmp_path):
    app = start(Database(tmp_path / 'kept.sqlite3', Registry(Account, Kept, KeptLegacy)))
    c = client(app)
    account = c('POST', '/api/account', {'name': 'Small', 'limit': '10'})[1]['id']
    record = c('POST', '/api/kept', {'account': account, 'memo': 'm', 'children': {'lines': [{'_key': 'a'}]}})[1]
    url, line = f"/api/kept/{record['id']}", record['children']['lines'][0]['id']
    legacy = f"/api/keptlegacy/{c('POST', '/api/keptlegacy', {'account': account})[1]['id']}"
    for path in (url, legacy):
        assert move(c, path, 'issued')[0] == 200
    c('PATCH', f'/api/account/{account}', {'name': 'Big', 'limit': '99'}, headers=etag(c, f'/api/account/{account}'))
    # The record keeps limit 10.00, so remarks is not required and memo still applies and keeps its value.
    status, body, _ = c('PATCH', url, {'children': {'lines': {'update': [{'id': line, 'note': 'n'}]}}}, headers=etag(c, url))
    assert status == 200, body
    assert (body['limit'], body['memo'], body['children']['lines'][0]['note']) == ('10.00', 'm', 'n')
    status, body, _ = c('PATCH', legacy, {'note': 'n'}, headers=etag(c, legacy))
    assert status == 200 and (body['account_name'], body['note']) == ('Small', 'n'), body


class Versioned(Model):
    Meta = FiniteMeta
    permissions = ALL
    qty = Decimal(default=1)
    double = Decimal(compute=F('qty') * 2)
    remarks = Text(default='', editable_when_locked=True)
    status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue'), Move('issued', 'draft', 'Reopen')),
                   locked=('issued',))


def test_locked_records_saved_under_an_older_formula_stay_editable(tmp_path):
    from evlet import Application, LocalAuth
    app = start(Database(tmp_path / 'versions.sqlite3', Registry(Versioned)))
    c = client(app)
    url = f"/api/versioned/{c('POST', '/api/versioned', {'qty': '2'})[1]['id']}"
    assert move(c, url, 'issued')[0] == 200

    class Newer(Model):
        __resource__ = 'versioned'
        Meta = FiniteMeta
        permissions = ALL
        qty = Decimal(default=1)
        double = Decimal(compute=F('qty') * 3)
        remarks = Text(default='', editable_when_locked=True)
        status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue'), Move('issued', 'draft', 'Reopen')),
                       locked=('issued',))
    Newer.__name__ = 'Versioned'
    db = Database(tmp_path / 'versions.sqlite3', Registry(Newer))
    newer = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    newer.initialize()
    c = client(newer)
    version = "SELECT plan_hash FROM evlet_calculation_versions WHERE resource='versioned'"
    with db.transaction() as tx:
        recorded = tx.connection.execute(version).fetchone()[0]
    status, body, _ = c('PATCH', url, {'remarks': 'still editable'}, headers=etag(c, url))
    assert status == 200 and (body['remarks'], body['double']) == ('still editable', '4.0000'), body
    with db.transaction() as tx:
        assert tx.connection.execute(version).fetchone()[0] == recorded
    # A move recalculates, so it needs the active formula version.
    status, body, _ = move(c, url, 'draft')
    assert status == 409 and 'SCHEMA_VERSION_MISMATCH' in body['error']
    plan = newer.business.registry.plan_for(Newer)
    status, body, _ = c('POST', url + '/moves/draft', {'_calculation': {'model_schema_hash': plan['model_schema_hash'], 'recalculate': True}},
                        headers=etag(c, url))
    assert status == 200 and (body['status'], body['double']) == ('draft', '6.0000'), body


class Veiled(Model):
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'), read_fields=('title',), write_fields=('title',))}
    title = Text(default='')
    status = State(('a', 'b', 'c'), moves=(Move('a', 'b', 'B', when=F('title') == 'go'), Move('b', 'c', 'C', roles=('admin',))),
                   locked=('b', 'c'), final=('c',))


def test_roles_that_cannot_read_the_state_get_one_answer_for_every_refused_move(tmp_path):
    app = start(Database(tmp_path / 'veiled.sqlite3', Registry(Veiled)))
    c, editor = client(app), add_user(app, 'editor')
    url = f"/api/veiled/{c('POST', '/api/veiled', {})[1]['id']}"
    generic = (409, {'error': 'This record has no such move now'})
    # An unknown state, no such move from here and a guard that is not met all look the same.
    assert move(editor, url, 'zzz')[:2] == generic
    assert move(editor, url, 'c')[:2] == generic
    assert move(editor, url, 'b')[:2] == generic
    editor('PATCH', url, {'title': 'go'}, headers=etag(editor, url))
    assert move(editor, url, 'b')[0] == 200
    # A move this role may not use looks the same as one that does not exist.
    assert move(editor, url, 'c')[:2] == generic
    assert move(editor, url, 'zzz')[:2] == generic
    # Roles that can read the State keep the detailed messages.
    assert move(c, url, 'zzz')[:2] == (404, {'error': 'Move not found'})
    assert move(c, url, 'a')[:2] == (409, {'error': 'This record cannot move from b to a'})


class BigRowGuard(Model):
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='')
    lines = Table(BigLine, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))
    shown = Currency(compute=lines.sum('amount'), stored=False)
    status = State(('draft', 'issued'), moves=(Move('draft', 'issued', 'Issue', when=F('shown') > 0),), locked=('issued',))

    @action('Note', available_when=F('shown') > 0)
    def note(self):
        self.title = 'noted'


def test_guards_over_rows_reuse_the_rows_the_detail_loaded(tmp_path):
    from test_lines import Counting, counted
    app = start(Counting(tmp_path / 'rows.sqlite3', Registry(BigPlain, BigRowGuard)))
    c = client(app)
    rows = [{'_key': f'l{index}', 'qty': '1', 'rate': '2.50'} for index in range(1000)]
    counts = {}
    for name in ('bigplain', 'bigrowguard'):
        record = c('POST', f'/api/{name}', {'children': {'lines': rows}})[1]
        (status, body, _), counts[name] = counted(c, 'GET', f"/api/{name}/{record['id']}")
        assert status == 200
    assert body['_moves'] == [{'to': 'issued', 'label': 'Issue', 'allowed': True}] and body['_actions'][0]['allowed'] is True
    # The second extra statement is the journal summary (`_journal`) of a model with a State.
    assert counts['bigrowguard'] <= counts['bigplain'] + 2, counts
