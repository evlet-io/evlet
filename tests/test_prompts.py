"""Prompts (one transient form) and bound records, shown with a Quotation that confirms into Customer Orders."""

from datetime import date

import pytest

from evlet import (Application, Child, Currency, Database, Date, Decimal, Link, LocalAuth, Main, Model, Outcome, Prompt,
                   RecordId, Registry, Rule, State, Table, Text, ValidationError, action, release, validate)
from evlet.auth import User, password_hash
from evlet.calc import F, today
from evlet.services import Endpoint
from support import PASSWORD, client, http

ALL = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
       'viewer': Rule(('read',))}


def note_on(self):
    return SourceNote


class Customer(Model):
    permissions = ALL
    customer_name = Text(required=True)
    note = action('Note')(note_on)

    @action('Rename', inputs={'new_name': Text(required=True, max_length=60)})
    def rename(self, new_name):
        self.customer_name = new_name


class Quotation(Model):
    permissions = ALL
    customer = Link(Customer, required=True)
    quote_date = Date(required=True)
    valid_until = Date()

    class Line(Child):
        item = Text(required=True)
        qty = Decimal(scale=2, minimum=0, required=True)
        rate = Currency(currency='AED', required=True)
        amount = Currency(currency='AED', compute=F('qty') * F('rate'))
        confirmed_qty = Decimal(scale=2, default='0', read_only=True, versioned=False)
        open_qty = Decimal(scale=2, compute=F('qty') - F('confirmed_qty'), stored=False)

    lines = Table(Line, required=True)
    total = Currency(currency='AED', compute=lines.sum('amount'))
    docstate = State(
        State.Step('draft', 'Draft'), State.Step('revision', 'Revision'),
        State.Step('submitted', 'Submitted', release=True), State.Step('partial', 'Partially confirmed', release=True),
        State.Step('confirmed', 'Confirmed', release=True), State.Step('terminated', 'Terminated', release=True, final=True),
        State.Step('cancelled', 'Cancelled', final=True),
        rules=(State.Rule(('draft', 'revision'), 'submitted', 'Submit', key='submit', primary=True, when=F('total') > 0),
               State.Rule('submitted', 'revision', 'Revise', key='revise', remark='optional'),
               State.Rule(('draft', 'submitted'), 'cancelled', 'Cancel', key='cancel', remark='required'),
               State.Rule(('partial', 'confirmed'), 'terminated', 'Terminate', key='terminate', remark='required'),
               # System rules: only application code moves by them, and they are never buttons.
               State.Rule('submitted', 'partial', key='part', by=()),
               State.Rule(('submitted', 'partial'), 'confirmed', key='full', by=()),
               State.Rule('confirmed', 'partial', key='reopen_part', by=()),
               State.Rule(('partial', 'confirmed'), 'submitted', key='reopen_all', by=())))

    note = action('Note')(note_on)

    @action('Reschedule')
    def reschedule(self):
        return Reschedule(from_start='2026-02-02')

    @action('Confirm', available_when=(F('docstate') == 'submitted') | (F('docstate') == 'partial'))
    def confirm(self):
        return ConfirmQuotation

    def create_order(self, lines, order_date, customer_po=None):
        """A submitted Customer Order with the confirmed lines; the quotation moves by full or part."""
        rows = [line for line in lines if line.confirm_qty]
        if not rows:
            raise ValidationError({'lines': 'Confirm a quantity on at least one line'})
        order = self.context.create(CustomerOrder, customer=self.customer, quotation=self.id, order_date=order_date,
                                    customer_po=customer_po, children={'lines': [
                                        {'quotation_line': row.quotation_line, 'item': row.item, 'qty': row.confirm_qty, 'rate': row.rate}
                                        for row in rows]})
        order.submit()
        self.add_confirmed({RecordId(row.quotation_line): row.confirm_qty for row in rows})
        return order

    def add_confirmed(self, quantities):
        """Add (or, negative, give back) confirmed quantities, then move to partial, confirmed or submitted."""
        self.lines = {'update': [{'id': line.id, 'confirmed_qty': line.confirmed_qty + quantities[line.id]}
                                 for line in self.lines if line.id in quantities]}
        self.save()
        confirmed = [line.confirmed_qty for line in self.lines]
        target = 'confirmed' if all(line.confirmed_qty >= line.qty for line in self.lines) else 'partial' if any(confirmed) else 'submitted'
        key = {('submitted', 'partial'): 'part', ('submitted', 'confirmed'): 'full', ('partial', 'confirmed'): 'full',
               ('confirmed', 'partial'): 'reopen_part', ('confirmed', 'submitted'): 'reopen_all',
               ('partial', 'submitted'): 'reopen_all'}.get((self.docstate, target))
        if key:
            self.move(key)


class CustomerOrder(Model):
    permissions = ALL
    customer = Link(Customer, required=True)
    quotation = Link(Quotation, required=True)
    order_date = Date(required=True)
    customer_po = Text(max_length=60)

    class Line(Child):
        quotation_line = Text(required=True)
        item = Text(required=True)
        qty = Decimal(scale=2, minimum=0, required=True)
        rate = Currency(currency='AED')
        amount = Currency(currency='AED', compute=F('qty') * F('rate'))

    lines = Table(Line, required=True)
    total = Currency(currency='AED', compute=lines.sum('amount'))
    note = action('Note')(note_on)
    docstate = State(State.Step('draft', 'Draft'), State.Step('submitted', 'Submitted', release=True),
                     State.Step('cancelled', 'Cancelled', final=True),
                     rules=(State.Rule('draft', 'submitted', 'Submit', key='submit', primary=True),
                            State.Rule(('draft', 'submitted'), 'cancelled', 'Cancel', key='cancel', remark='required')))

    @release.exit
    def give_back(self):
        """Cancelling a submitted order gives its quantities back to the quotation."""
        quotation = self.context.get(Quotation, self.quotation)
        quotation.add_confirmed({RecordId(line.quotation_line): -line.qty for line in self.lines})


class ConfirmQuotation(Prompt):
    order_date = Date(required=True, initial=today())
    customer_po = Text(max_length=60)

    class Line(Child):
        quotation_line = Text(read_only=True, hidden=True)
        item = Text(read_only=True)
        open_qty = Decimal(scale=2, read_only=True)
        confirm_qty = Decimal(scale=2, minimum=0)
        rate = Currency(currency='AED', read_only=True)
        amount = Currency(currency='AED', compute=F('confirm_qty') * F('rate'))

        @validate
        def within_open(self):
            if (self.confirm_qty or 0) > self.open_qty:
                raise ValidationError({'confirm_qty': 'More than is still open'})

    lines = Table(Line)
    total = Currency(currency='AED', compute=lines.sum('amount'))

    class Meta:
        title = 'Confirm quotation'
        primary = 'Create sales order'
        layout = (Main(('order_date', 'customer_po', 'total')), Main('lines'))

    def load(self, quotation):
        self.lines = [{'quotation_line': str(line.id), 'item': line.item, 'open_qty': line.qty - line.confirmed_qty,
                       'confirm_qty': line.qty - line.confirmed_qty, 'rate': line.rate}
                      for line in quotation.lines if line.qty > line.confirmed_qty]

    def submit(self):
        order = self.source.create_order(self.lines, self.order_date, self.customer_po)
        return Outcome(open=order)


class QuickNote(Prompt):
    """A prompt without a record, opened by an @api function."""
    customer = Link(Customer, required=True)
    text = Text(required=True)

    def submit(self):
        return Outcome('Noted', data={'customer': self.customer, 'text': self.text})


class Reschedule(Prompt):
    """Four dates that suggest today: load() sets one, the starting values another, code a third."""
    from_load = Date(initial=today())
    from_start = Date(initial=today())
    from_code = Date(initial=today())
    untouched = Date(initial=today())

    def load(self, quotation):
        self.from_load = quotation.quote_date

    def submit(self):
        return Outcome(data={name: str(getattr(self, name)) for name in ('from_load', 'from_start', 'from_code', 'untouched')})


class SourceNote(Prompt):
    """One prompt opened from three models: it reads which through self.source.model."""
    answer = Text(required=True)

    def submit(self):
        model = self.source.model
        if self.answer == 'open':
            return Outcome(open=(model.__name__.lower(), self.source.id))
        if self.answer == 'new':
            return Outcome(new=('customer', {'customer_name': f'From {model.__name__}'}))
        if self.answer == 'unknown':
            return Outcome(new=('nosuchmodel', {}))
        return Outcome(data={'model': model.__name__})


def quick_note(context):
    return QuickNote(text='Call back')


@pytest.fixture
def app(tmp_path):
    db = Database(tmp_path / 'prompts.sqlite3', Registry(Customer, Quotation, CustomerOrder))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(db, auth=auth, apis=(Endpoint('test.quick_note', quick_note, 'POST', (Customer, 'update')),))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    with db.transaction() as tx:
        for role in ('editor', 'viewer'):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?)', (role, password_hash(PASSWORD), role))
    return app


ADMIN = User(1, 'admin', 'admin')


def quotation(app, *quantities):
    """A submitted quotation with one line per quantity, at 10.00 each."""
    with app.business.context(ADMIN) as context:
        customer = context.create(Customer, customer_name='Acme')
        record = context.create(Quotation, customer=customer.id, quote_date='2026-11-16', children={'lines': [
            {'item': f'Item {index}', 'qty': qty, 'rate': '10.00'} for index, qty in enumerate(quantities, 1)]})
        record.submit()
        return record.id


def read(app, model, record_id):
    with app.business.context(ADMIN, readonly=True) as context:
        return context.get(model, record_id)


# ---------- the demo through Python ----------

def test_confirm_all_lines_confirms_the_quotation(app):
    quotation_id = quotation(app, '2', '3')
    with app.business.context(ADMIN) as context:
        record = context.get(Quotation, quotation_id)
        outcome = record.confirm({'order_date': '2026-11-20', 'customer_po': 'PO-7'})
    order = read(app, CustomerOrder, outcome['open']['id'])
    assert order.docstate == 'submitted' and order.customer_po == 'PO-7' and str(order.total) == '50.00'
    assert [(line.item, str(line.qty)) for line in order.lines] == [('Item 1', '2.00'), ('Item 2', '3.00')]
    record = read(app, Quotation, quotation_id)
    assert record.docstate == 'confirmed' and [str(line.confirmed_qty) for line in record.lines] == ['2.00', '3.00']


def test_confirm_some_then_the_rest_and_cancel_an_order(app):
    quotation_id = quotation(app, '2', '3')
    with app.business.context(ADMIN) as context:
        prompt = context.get(Quotation, quotation_id).confirm()
        # Called without values, the action answers the loaded prompt; prompt.submit() submits it.
        assert [str(line.confirm_qty) for line in prompt.lines] == ['2.00', '3.00']
        prompt.lines[1].confirm_qty = 0
        first = prompt.submit()['open']['id']
    record = read(app, Quotation, quotation_id)
    assert record.docstate == 'partial' and [str(line.confirmed_qty) for line in record.lines] == ['2.00', '0.00']
    with app.business.context(ADMIN) as context:
        prompt = context.get(Quotation, quotation_id).confirm()
        # Only what is still open is offered.
        assert [(line.item, str(line.open_qty)) for line in prompt.lines] == [('Item 2', '3.00')]
        prompt.submit()
    assert read(app, Quotation, quotation_id).docstate == 'confirmed'
    with app.business.context(ADMIN) as context:
        context.get(CustomerOrder, first).cancel(remark='Customer withdrew line 1')
    record = read(app, Quotation, quotation_id)
    assert record.docstate == 'partial' and [str(line.confirmed_qty) for line in record.lines] == ['0.00', '3.00']


def test_a_submit_that_fails_rolls_back_the_order_and_the_quotation(app):
    quotation_id = quotation(app, '2')
    with pytest.raises(ValidationError) as error:
        with app.business.context(ADMIN) as context:
            context.get(Quotation, quotation_id).confirm({'order_date': '2026-11-20', 'lines': [
                {'quotation_line': 'x', 'item': 'Item 1', 'open_qty': '2', 'confirm_qty': '0', 'rate': '10'}]})
    assert 'lines' in error.value.errors
    assert app.database.count(CustomerOrder) == 0 and read(app, Quotation, quotation_id).docstate == 'submitted'
    with pytest.raises(ValidationError) as error:
        with app.business.context(ADMIN) as context:
            context.get(Quotation, quotation_id).confirm({'order_date': None})
    assert set(error.value.errors) == {'order_date'}


# ---------- over HTTP: tickets ----------

def present(c, quotation_id):
    etag = c('GET', f'/api/quotation/{quotation_id}')[2][b'etag']
    status, result, _ = c('POST', f'/api/quotation/{quotation_id}/actions/confirm', {}, headers={b'if-match': etag})
    assert status == 200, result
    return result['_outcome']['prompt']


def test_an_action_returns_a_prompt_with_a_ticket_and_writes_nothing(app):
    quotation_id = quotation(app, '2', '3')
    before = read(app, Quotation, quotation_id)
    surface = present(client(app), quotation_id)
    assert surface['title'] == 'Confirm quotation' and surface['primary'] == 'Create sales order'
    assert surface['source']['model'] == 'quotation' and surface['source']['id'] == str(quotation_id)
    assert [row['confirm_qty'] for row in surface['values']['children']['lines']] == ['2.00', '3.00']
    model = surface['models'][0]
    assert model['name'] == '_prompt.confirm_quotation' and model['children'][0]['model'] == '_prompt.confirm_quotation.lines'
    assert [block['kind'] for block in model['record_layout']['blocks']] == ['section', 'table']
    # load() filled the lines, so the table takes no new rows: max_rows is the rows it has, and the grid offers no Add.
    assert model['children'][0]['max_rows'] == 2
    # The finite plan ships, so the browser calculates as on a record.
    assert model['calculation']['plan']['tables']['$root']['model'] == '_prompt.confirm_quotation'
    assert read(app, Quotation, quotation_id)._detail == before._detail
    with app.database._connection() as connection:
        assert connection.execute('SELECT count(*) FROM evlet_prompt_tickets').fetchone()[0] == 1


def test_check_and_submit_run_through_the_ticket_once(app):
    quotation_id = quotation(app, '2', '3')
    c = client(app)
    surface = present(c, quotation_id)
    ticket, rows = surface['ticket'], surface['values']['children']['lines']
    values = {'order_date': '2026-11-20', 'children': {'lines': [{'_key': rows[0]['_key'], 'confirm_qty': '5'},
                                                                 {'_key': rows[1]['_key'], 'confirm_qty': '1'}]}}
    status, checked, _ = c('POST', f'/api/_prompts/{ticket}/check', {'values': values})
    assert status == 200 and checked['values']['total'] == '60.00'
    assert checked['errors'] == {} and app.database.count(CustomerOrder) == 0
    status, result, _ = c('POST', f'/api/_prompts/{ticket}/submit', {'values': values})
    assert status == 422 and f'lines.{rows[0]["_key"]}.confirm_qty' in result['fields']
    values['children']['lines'][0]['confirm_qty'] = '2'
    status, result, _ = c('POST', f'/api/_prompts/{ticket}/submit', {'values': values})
    assert status == 200 and result['open']['model'] == 'customerorder'
    # The ticket is the idempotency key: a retry answers the first result and runs nothing again.
    assert c('POST', f'/api/_prompts/{ticket}/submit', {'values': values})[:2] == (200, result)
    assert app.database.count(CustomerOrder) == 1
    assert c('POST', f'/api/_prompts/{ticket}/check', {'values': values})[0] == 409
    assert read(app, Quotation, quotation_id).docstate == 'partial'


def test_tickets_are_refused_for_others_when_stale_or_unknown(app):
    quotation_id = quotation(app, '2')
    c = client(app)
    ticket = present(c, quotation_id)['ticket']
    assert client(app, 'editor')('POST', f'/api/_prompts/{ticket}/check', {})[0] == 404
    assert c('POST', '/api/_prompts/unknown/check', {})[0] == 404
    assert c('POST', f'/api/_prompts/{ticket}/check', {'other': 1})[0] == 422
    # The record changed after the prompt opened: refused.
    with app.business.context(ADMIN) as context:
        context.get(Quotation, quotation_id).revise()
    # Confirm is not available on the revised quotation, and the quotation is not the revision the prompt opened on.
    assert c('POST', f'/api/_prompts/{ticket}/submit', {'values': {}})[0] == 409
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_prompt_tickets SET expires = 0')
    assert c('POST', f'/api/_prompts/{ticket}/check', {})[0] == 410


def test_a_viewer_cannot_open_the_prompt(app):
    quotation_id = quotation(app, '2')
    c = client(app, 'viewer')
    etag = c('GET', f'/api/quotation/{quotation_id}')[2][b'etag']
    assert c('POST', f'/api/quotation/{quotation_id}/actions/confirm', {}, headers={b'if-match': etag})[0] == 403


def test_an_api_function_returns_a_prompt_without_a_record(app):
    with app.business.context(ADMIN) as context:
        customer = context.create(Customer, customer_name='Acme').id
    c = client(app)
    status, result, _ = c('POST', '/api/_services/test.quick_note', {})
    assert status == 200 and result['prompt']['source'] is None and result['prompt']['values']['text'] == 'Call back'
    ticket = result['prompt']['ticket']
    status, outcome, _ = c('POST', f'/api/_prompts/{ticket}/submit', {'values': {'customer': customer}})
    assert status == 200 and outcome == {'message': 'Noted', 'data': {'customer': str(customer), 'text': 'Call back'}}


# ---------- bound records ----------

def test_bound_records_save_move_reload_and_delete(app):
    with app.business.context(ADMIN) as context:
        customer = context.create(Customer, customer_name='Acme')
        customer.customer_name = 'Acme Trading'
        customer.save()
        assert customer.customer_name == 'Acme Trading'
        record = context.create(Quotation, customer=customer.id, quote_date='2026-11-16',
                                children={'lines': [{'item': 'Desk', 'qty': '1', 'rate': '10.00'}]})
        assert record.docstate == 'draft' and len(record.lines) == 1
        record.move('submit')
        assert record.docstate == 'submitted'
        record.revise(remark='Price change')
        assert record.reload().docstate == 'revision'
        stale = context.get(Customer, customer.id)
        customer.customer_name = 'Acme LLC'
        customer.save()
        stale.customer_name = 'Other'
        with pytest.raises(Exception, match='changed since it was read'):
            stale.save()
        other = context.create(Customer, customer_name='Gone')
        other.delete()
        assert context.count(Customer) == 1
        assert [row.customer_name for row in context.list(Customer)['rows']] == ['Acme LLC']


def test_record_methods_need_a_context(app):
    with pytest.raises(ValueError, match='through a context'):
        Customer(customer_name='Acme').save()
    with pytest.raises(ValueError, match='through a context'):
        Quotation.confirm(Customer(customer_name='Acme'))
    quotation_id = quotation(app, '1')
    with app.business.context(ADMIN, readonly=True) as context:
        with pytest.raises(Exception, match='read only'):
            context.get(Quotation, quotation_id).confirm({'order_date': '2026-11-20'})


def test_an_action_on_self_runs_its_checks_on_the_same_draft():
    class Ticket(Model):
        permissions = ALL
        title = Text(required=True)
        hits = Decimal(scale=0, default='0')

        @action('Bump')
        def bump(self):
            self.hits = (self.hits or 0) + 1

        @action('Bump twice')
        def bump_twice(self):
            self.bump()
            self.bump()

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as folder:
        db = Database(Path(folder) / 'nested.sqlite3', Registry(Ticket))
        app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
        app.initialize()
        with app.business.context(ADMIN) as context:
            ticket = context.create(Ticket, title='One')
            ticket.bump_twice()
            assert str(ticket.hits) == '2'


def test_names_are_one_namespace():
    with pytest.raises(ValueError, match="the field 'save' and the record method 'save'"):
        class Saved(Model):
            save = Text()
    with pytest.raises(ValueError, match="the action 'submit' and the rule 'submit'"):
        class Clash(Model):
            docstate = State(State.Step('draft'), State.Step('done'), rules=(State.Rule('draft', 'done', key='submit'),))

            @action
            def submit(self):
                pass


def test_tools_get_the_prompt_and_answer_it_by_ticket(app):
    quotation_id = quotation(app, '2')
    c = client(app)
    tools = {tool['name'] for tool in c('GET', '/api/_tools')[1]['tools']}
    assert {'run_action', 'answer_prompt'} <= tools
    status, ran, _ = c('POST', '/api/_tools/execute', {'tool': 'run_action', 'arguments': {
        'model': 'quotation', 'id': str(quotation_id), 'action': 'confirm'}})
    assert status == 200, ran
    prompt = ran['result']['_outcome']['prompt']
    assert prompt['models'][0]['name'] == '_prompt.confirm_quotation'
    row = prompt['values']['children']['lines'][0]['_key']
    values = {'order_date': '2026-11-20', 'children': {'lines': [{'_key': row, 'confirm_qty': '1'}]}}
    status, checked, _ = c('POST', '/api/_tools/execute', {'tool': 'answer_prompt', 'arguments': {'ticket': prompt['ticket'], 'values': values, 'step': 'check'}})
    assert status == 200, checked
    assert checked['result']['values']['total'] == '10.00' and app.database.count(CustomerOrder) == 0
    status, done, _ = c('POST', '/api/_tools/execute', {'tool': 'answer_prompt', 'arguments': {'ticket': prompt['ticket'], 'values': values}})
    assert status == 200 and done['result']['open']['model'] == 'customerorder'
    assert read(app, Quotation, quotation_id).docstate == 'partial'


def test_an_assistant_is_asked_once_for_an_action_that_opens_a_prompt(app):
    """Over MCP, running Confirm only opens its prompt (its body is `return ConfirmQuotation`): a read, no approval.
    Approval is asked once, on the submit that writes."""
    from evlet import qb
    from evlet.auth import KeyAccess
    from evlet.tools import ApprovalNeeded
    from evlet.prompts import opens_prompt
    assert opens_prompt(Quotation.confirm) and opens_prompt(Quotation.reschedule) and opens_prompt(Quotation.note)

    def writes_first(self):
        self.valid_until = None
        return ConfirmQuotation

    def computes(self):
        return ConfirmQuotation(customer_po=self.create_order([], None))
    assert not opens_prompt(writes_first) and not opens_prompt(computes) and not opens_prompt(Quotation.create_order)
    quotation_id = quotation(app, '2')
    changes = [{'kind': 'setting', 'key': 'mcp.allow_writes', 'value': True}]
    assert client(app)('POST', '/api/_controls/overrides', {'changes': changes})[0] == 200
    assistant = User(1, 'admin', 'admin', access=KeyAccess(True, None, None, 0))
    tools = app.business.tools

    def approvals():
        with app.database._connection() as connection:
            return connection.run(qb.select('evlet_tool_approvals', 'id', 'tool', 'status').order_by('id')).all()
    ran = tools.execute(assistant, 'run_action', {'model': 'quotation', 'id': str(quotation_id), 'action': 'confirm'})
    prompt = ran['_outcome']['prompt']
    assert prompt['models'][0]['name'] == '_prompt.confirm_quotation' and approvals() == []
    row = prompt['values']['children']['lines'][0]['_key']
    values = {'order_date': '2026-11-20', 'children': {'lines': [{'_key': row, 'confirm_qty': '1'}]}}
    with pytest.raises(ApprovalNeeded):
        tools.execute(assistant, 'answer_prompt', {'ticket': prompt['ticket'], 'values': values})
    assert [(item['tool'], item['status']) for item in approvals()] == [('answer_prompt', 'pending')]
    with app.database.transaction() as tx:
        tx.connection.run(qb.update('evlet_tool_approvals', status='approved'))
    done = tools.execute(assistant, 'answer_prompt', {'ticket': prompt['ticket'], 'values': values})
    assert done['open']['model'] == 'customerorder' and read(app, Quotation, quotation_id).docstate == 'partial'
    assert len(approvals()) == 1


def test_action_inputs_open_as_a_prompt_over_http(app):
    with app.business.context(ADMIN) as context:
        customer = context.create(Customer, customer_name='Acme')
        # Trusted Python passes the inputs directly.
        customer.rename(new_name='Acme Trading')
        assert customer.customer_name == 'Acme Trading'
    c = client(app)
    url = f'/api/customer/{customer.id}'
    status, opened, _ = c('POST', f'{url}/actions/rename', {}, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert status == 200, opened
    prompt = opened['_outcome']['prompt']
    assert prompt['title'] == prompt['primary'] == 'Rename' and opened['customer_name'] == 'Acme Trading'
    assert [field['name'] for field in prompt['models'][0]['fields']] == ['new_name']
    status, result, _ = c('POST', f"/api/_prompts/{prompt['ticket']}/submit", {'values': {}})
    assert status == 422 and 'new_name' in result['fields']
    assert c('POST', f"/api/_prompts/{prompt['ticket']}/submit", {'values': {'new_name': 'Acme LLC'}})[0] == 200
    assert c('GET', url)[1]['customer_name'] == 'Acme LLC'
    # Values sent with the action run it at once, as before.
    assert c('POST', f'{url}/actions/rename', {'new_name': 'Acme'}, headers={b'if-match': c('GET', url)[2][b'etag']})[1]['customer_name'] == 'Acme'


# ---------- values code sets on a new prompt are the caller's ----------

def test_values_set_by_code_keep_through_submit(app):
    quotation_id = quotation(app, '2')
    with app.business.context(ADMIN) as context:
        prompt = context.get(Quotation, quotation_id).reschedule()
        prompt.from_code = '2026-03-03'
        outcome = prompt.submit()
    assert outcome['data'] == {'from_load': '2026-11-16', 'from_start': '2026-02-02', 'from_code': '2026-03-03',
                               'untouched': str(date.today())}


def test_an_attribute_set_on_a_loaded_prompt_keeps_through_submit(app):
    quotation_id = quotation(app, '2')
    with app.business.context(ADMIN) as context:
        prompt = context.get(Quotation, quotation_id).confirm()
        prompt.order_date = '2026-12-01'
        order = prompt.submit()['open']['id']
    assert str(read(app, CustomerOrder, order).order_date) == '2026-12-01'


def test_values_set_by_code_keep_through_a_ticket(app):
    quotation_id = quotation(app, '2')
    c = client(app)
    etag = c('GET', f'/api/quotation/{quotation_id}')[2][b'etag']
    status, result, _ = c('POST', f'/api/quotation/{quotation_id}/actions/reschedule', {}, headers={b'if-match': etag})
    assert status == 200, result
    ticket = result['_outcome']['prompt']['ticket']
    status, outcome, _ = c('POST', f'/api/_prompts/{ticket}/submit', {'values': {'from_code': '2026-03-03'}})
    assert status == 200, outcome
    assert outcome['data'] == {'from_load': '2026-11-16', 'from_start': '2026-02-02', 'from_code': '2026-03-03',
                               'untouched': str(date.today())}


# ---------- record.model, and outcomes that name a model ----------

def three_sources(app):
    quotation_id = quotation(app, '2')
    with app.business.context(ADMIN) as context:
        record = context.get(Quotation, quotation_id)
        order = record.confirm({'order_date': '2026-11-20'})['open']['id']
        return [(Customer, record.customer), (Quotation, quotation_id), (CustomerOrder, order)]


def test_one_prompt_reads_which_model_opened_it(app):
    for model, record_id in three_sources(app):
        with app.business.context(ADMIN) as context:
            record = context.get(model, record_id)
            assert record.model is model
            assert record.note({'answer': 'which'}) == {'data': {'model': model.__name__}}


def test_an_outcome_names_a_model_by_name(app):
    model, record_id = three_sources(app)[1]
    with app.business.context(ADMIN) as context:
        record = context.get(model, record_id)
        assert record.note({'answer': 'open'})['open'] == {'model': 'quotation', 'id': record_id}
        assert record.note({'answer': 'new'})['new'] == {'model': 'customer', 'values': {'customer_name': 'From Quotation'}}
        with pytest.raises(ValueError, match='Unknown model: nosuchmodel'):
            record.note({'answer': 'unknown'})
    # Over HTTP the name resolves where the outcome goes into the answer.
    c = client(app)
    etag = c('GET', f'/api/quotation/{record_id}')[2][b'etag']
    status, result, _ = c('POST', f'/api/quotation/{record_id}/actions/note', {}, headers={b'if-match': etag})
    ticket = result['_outcome']['prompt']['ticket']
    status, outcome, _ = c('POST', f'/api/_prompts/{ticket}/submit', {'values': {'answer': 'new'}})
    assert status == 200 and outcome['new'] == {'model': 'customer', 'values': {'customer_name': 'From Quotation'}}


def test_an_outcome_still_takes_a_class():
    outcome = Outcome(new=(Customer, {'customer_name': 'Acme'}), open=(Customer, 7))
    assert outcome.resolve(None) == {'open': {'model': 'customer', 'id': RecordId(7)},
                                     'new': {'model': 'customer', 'values': {'customer_name': 'Acme'}}}
