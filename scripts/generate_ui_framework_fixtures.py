"""Record real authenticated ASGI responses for the browser UI framework tests.

Writes frontend/src/evlet/tests/api-fixture.json. The API client and record store tests replay
these responses through a fetch stub, so they consume the shapes the backend really sends.
Run from the repository root: .venv/bin/python -m scripts.generate_ui_framework_fixtures
"""

import asyncio
import json
from pathlib import Path
import tempfile

from evlet import (Application, Database, LocalAuth, Registry, Model, Child, Table, Decimal, Currency, Integer, Text,
                   Date, Link, Rule, ValidationError, Select, action, Attachments, Boolean, Count, Effect, Move, Reference,
                   Related, Sequence, State, Sum, Swap)
from evlet.calc import D, F, round, days

PASSWORD = 'a sufficiently long fixture password'
FULL = Rule(('read', 'create', 'update', 'delete'))
ALL = {'admin': FULL, 'editor': FULL, 'viewer': Rule(('read',))}
OUTPUT = Path(__file__).resolve().parent.parent / 'frontend/src/evlet/tests/api-fixture.json'


class FiniteMeta:
    calculation_language = 'finite-v1'
    numeric_profile = 'finite40-hu18-v1'


class Customer(Model):
    permissions = ALL
    name = Text(default='customer')
    rate = Currency(default=0)
    credit_days = Integer(default=30)


class Product(Model):
    permissions = ALL
    name = Text(default='product')
    price = Currency(default=0)


class Part(Child):
    hours = Decimal(default=0, minimum='0')


class Line(Child):
    product = Link(Product)
    qty = Decimal(default=1, minimum='0')
    rate = Currency(initial=F('product.price'))
    amount = Currency(compute=round(F('qty') * F('rate'), 2))
    parts = Table(Part, max_rows=9)

    def validate(self):
        if self.qty == 13:
            raise ValidationError({'qty': 'Thirteen is not allowed'})


class Order(Model):
    Meta = FiniteMeta
    permissions = ALL
    title = Text(default='order')
    customer = Link(Customer)
    stage = Select(('open', 'won', 'lost'), default='open')
    issued = Date()
    due = Date(compute=F('issued') + days(F('customer.credit_days')))
    reason = Text(applies_when=F('stage') == 'lost', required_when=F('stage') == 'lost')
    remark = Text(locked_when=F('stage') == 'won')
    lines = Table(Line, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))
    line_count = Integer(compute=lines.count())

    @action
    def close(self, note: str = ''):
        self.stage = 'won'


class LedgerEntry(Model):
    """The target of the invoice's effects: editors read it, only the engine writes it."""
    permissions = {'admin': FULL, 'editor': Rule(('read',)), 'viewer': Rule(())}
    customer = Link(Customer)
    debit = Currency(default=0)
    credit = Currency(default=0)
    memo = Text(default='')
    source = Reference()
    reversal = Boolean(default=False)


class InvoiceLine(Child):
    item = Text(default='')
    qty = Decimal(default=1, minimum='0')
    rate = Currency(default=0)
    amount = Currency(compute=round(F('qty') * F('rate'), 2))


class InvoiceMeta(FiniteMeta):
    revisable = True
    effects = (Effect(LedgerEntry, rows='ledger_rows', on='issued', reverse=Swap('debit', 'credit'), balanced=('debit', 'credit')),)
    measures = {'outstanding': Sum('outstanding', where=F('status') == 'issued'), 'count': Count(),
                'payments': Count('payment', link='invoice'), 'paid': Sum('amount', model='payment', link='invoice')}
    key_fields = ('customer', 'total', 'due_on')
    relations = (Related('payment', 'invoice', label='Payments', limit=5, order=('-paid_on',)),
                 Related('invoice', 'revises', label='Revisions', limit=10, order=('-id',)))
    attachments = Attachments(max_files=5, max_size=262144)
    title_field = 'number'
    list_fields = ('id', 'number', 'customer', 'posting_date', 'status', 'total', 'outstanding')
    search_fields = ('number',)
    layout = (('Invoice', ('number', 'customer', 'posting_date', 'due_on', 'status')), ('Amounts', ('total', 'outstanding', 'remarks', 'revises')))


class Invoice(Model):
    """A transaction document: State and moves, a Sequence, void and revise, effects, measures and meaning."""
    Meta = InvoiceMeta
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void', 'revise')),
                   'editor': Rule(('read', 'create', 'update', 'void', 'revise')), 'viewer': Rule(('read',))}
    number = Sequence('INV-{year}-{n:05}', assign_on='issued', date='posting_date')
    customer = Link(Customer, required=True)
    posting_date = Date(required=True)
    due_on = Date()
    status = State(('draft', 'issued', 'paid', 'void'),
                   moves=(Move('draft', 'issued', 'Issue', when=F('total') > D('0'), run='open_balance'), Move('issued', 'paid', 'Mark paid', roles=('admin',))),
                   locked=('issued', 'paid', 'void'), final=('paid', 'void'), voidable=('issued',))
    lines = Table(InvoiceLine, max_rows=1000)
    total = Currency(compute=lines.sum('amount'))
    outstanding = Currency(default=0)
    remarks = Text(default='', editable_when_locked=True)

    @action(label='Record payment', inputs={'amount': Currency(required=True), 'paid_on': Date(required=True)},
            available_when=F('status') == 'issued')
    def record_payment(self, amount, paid_on):
        self.remarks = f'Paid {amount} on {paid_on.isoformat()}'

    def open_balance(self):
        self.outstanding = self.total

    def ledger_rows(self):
        rows = [{'customer': self.customer, 'debit': line.amount, 'memo': line.item} for line in self.lines]
        return rows + [{'customer': self.customer, 'credit': self.total, 'memo': 'Sales'}]


class Payment(Model):
    permissions = ALL
    invoice = Link(Invoice, required=True)
    amount = Currency(default=0)
    paid_on = Date()


def http(app, method, path, data=None, cookie='', csrf='', headers=None):
    async def run():
        route, _, query = path.partition('?')
        events = [{'type': 'http.request', 'body': json.dumps({} if data is None else data).encode()}]
        sent = []

        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}

        async def send(event):
            sent.append(event)
        request_headers = {b'host': b'127.0.0.1:8000', b'content-type': b'application/json', b'x-evlet-request': b'1',
                           b'cookie': cookie.encode(), b'x-csrf-token': csrf.encode(), **(headers or {})}
        await app({'type': 'http', 'method': method, 'path': route, 'query_string': query.encode(),
                   'headers': list(request_headers.items()), 'client': ('127.0.0.1', 1234)}, receive, send)
        response_headers, content = dict(sent[0]['headers']), sent[1]['body']
        payload = json.loads(content) if content and response_headers.get(b'content-type', b'').startswith(b'application/json') else None
        return sent[0]['status'], payload, response_headers
    return asyncio.run(run())


def main():
    folder = Path(tempfile.mkdtemp())
    database = Database(folder / 'fixture.sqlite3', Registry(Customer, Product, Order))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    out = {}

    def keep(name, response):
        status, payload, headers = response
        if isinstance(payload, dict) and isinstance(payload.get('csrf'), str):
            payload['csrf'] = 'fixture-csrf-token'  # The real token is random; tests only check that it is echoed.
        out[name] = {'status': status, 'body': payload, 'etag': headers.get(b'etag', b'').decode() or None}
        return payload

    keep('session_anonymous', http(app, 'GET', '/api/auth/session'))
    keep('login_failed', http(app, 'POST', '/api/auth/login', {'username': 'admin', 'password': 'not the password'}))
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']
    keep('login', (status, data, {}))

    def c(method, path, body=None, **kwargs):
        return http(app, method, path, body, cookie=cookie, csrf=csrf, **kwargs)

    keep('session', c('GET', '/api/auth/session'))
    keep('unauthenticated', http(app, 'GET', '/api/schema'))
    keep('schema', c('GET', '/api/schema'))
    customer = keep('customer', c('POST', '/api/customer', {'name': 'Al Sahra Trading', 'rate': '12.50', 'credit_days': 45}))
    product = keep('product', c('POST', '/api/product', {'name': 'Support hours', 'price': '250.00'}))
    lines = [{'_key': f'l{index}', 'product': product['id'], 'qty': '1', 'rate': '2.50'} for index in range(120)]
    lines[0]['children'] = {'parts': [{'_key': 'p0', 'hours': '1.5'}, {'_key': 'p1', 'hours': '2'}]}
    created = keep('create', c('POST', '/api/order', {'title': 'Big order', 'customer': customer['id'], 'issued': '2026-11-01',
                                                      'children': {'lines': lines}}))
    url = f"/api/order/{created['id']}"
    keep('detail', c('GET', url))
    keep('lines_page', c('GET', f'{url}/lines/lines?limit=100&offset=100'))
    first = created['children']['lines'][0]['id']
    keep('nested_lines_page', c('GET', f'{url}/lines/lines/{first}/parts?limit=5'))
    keep('list', c('GET', '/api/order?limit=20&offset=0'))
    etag = c('GET', url)[2][b'etag']
    keep('missing_if_match', c('PATCH', url, {'title': 'x'}))
    keep('stale_if_match', c('PATCH', url, {'title': 'x'}, headers={b'if-match': b'"stale"'}))
    ids = created['_row_ids']
    change = {'title': 'Renamed', 'children': {'lines': {'create': [{'_key': 'n1', 'qty': '2', 'rate': '1'}],
                                                         'update': [{'id': ids['l1'], 'qty': '3'}], 'delete': [ids['l2']]}}}
    keep('update', c('PATCH', url, change, headers={b'if-match': etag}))
    keep('lines_page_after_update', c('GET', f'{url}/lines/lines?limit=100&offset=100'))
    etag = c('GET', url)[2][b'etag']
    keep('validation', c('PATCH', url, {'children': {'lines': {'update': [{'id': ids['l3'], 'qty': '13'}]}}}, headers={b'if-match': etag}))
    keep('draft', c('POST', '/api/_draft/order', {'id': created['id'], 'values': {'title': 'Draft', 'stage': 'lost'}, 'revision': 3}))
    keep('action', c('POST', f'{url}/actions/close', {'note': 'done'}, headers={b'if-match': etag}))
    keep('not_found', c('GET', '/api/order/999999'))
    keep('logout', c('POST', '/api/auth/logout'))
    documents(out)
    OUTPUT.write_text(json.dumps(out, indent=1, sort_keys=True) + '\n')


def documents(out):
    """Responses of the document routes, recorded as an editor: a move refused by its condition, a move,
    an action, void, revise, per-record and model measures, related records, line pages and details
    carrying _moves, _actions, _void, _revise and _effects, with the schema's state, key_fields and relations."""
    folder = Path(tempfile.mkdtemp())
    database = Database(folder / 'documents.sqlite3', Registry(Customer, LedgerEntry, Invoice, Payment))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    auth.create_user(username='sara', password=PASSWORD, role='editor')

    def keep(name, response):
        status, payload, headers = response
        if isinstance(payload, dict) and isinstance(payload.get('csrf'), str):
            payload['csrf'] = 'fixture-csrf-token'
        out[name] = {'status': status, 'body': payload, 'etag': headers.get(b'etag', b'').decode() or None}
        return payload

    def login(username):
        status, data, headers = http(app, 'POST', '/api/auth/login', {'username': username, 'password': PASSWORD})
        assert status == 200, data
        cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']
        return lambda method, path, body=None, **kwargs: http(app, method, path, body, cookie=cookie, csrf=csrf, **kwargs)

    admin, editor = login('admin'), login('sara')

    def write(method, record, route='', body=None):
        # Every write on a saved document sends the ETag of its last read.
        return editor(method, record + route, body, headers={b'if-match': editor('GET', record)[2][b'etag']})

    customer = admin('POST', '/api/customer', {'name': 'Al Sahra Trading', 'credit_days': 30})[1]['id']
    keep('invoice_session', editor('GET', '/api/auth/session'))
    keep('invoice_schema', editor('GET', '/api/schema'))
    lines = [{'_key': 'a', 'item': 'Support hours', 'qty': '10', 'rate': '250.00'}, {'_key': 'b', 'item': 'Licence', 'qty': '2', 'rate': '90.00'}]

    def invoice(**values):
        status, body, _ = editor('POST', '/api/invoice', {'customer': customer, 'posting_date': '2026-11-01', 'due_on': '2026-12-01', **values})
        assert status == 201, body
        return f"/api/invoice/{body['id']}"

    # The first invoice: refused while it has no lines, issued once it has, then paid in part.
    url = invoice()
    keep('invoice_draft_blocked', editor('GET', url))
    keep('invoice_move_refused', write('POST', url, '/moves/issued', {}))
    assert write('PATCH', url, '', {'children': {'lines': {'create': lines}}})[0] == 200
    keep('invoice_draft', editor('GET', url))
    keep('invoice_issued', write('POST', url, '/moves/issued', {}))
    keep('invoice_issued_detail', editor('GET', url))
    keep('invoice_action', write('POST', url, '/actions/record_payment', {'amount': '500.00', 'paid_on': '2026-11-10'}))
    assert admin('POST', '/api/payment', {'invoice': int(url.rsplit('/', 1)[1]), 'amount': '500.00', 'paid_on': '2026-11-10'})[0] == 201
    keep('invoice_lines_page', editor('GET', f'{url}/lines/lines?limit=1&offset=1'))
    keep('invoice_record_measures', editor('GET', f'{url}/measures'))
    keep('invoice_related_payments', editor('GET', f'{url}/related/payment.invoice?limit=5&offset=0'))
    keep('invoice_related_revisions', editor('GET', f'{url}/related/invoice.revises?limit=10&offset=0'))
    # A draft that stays a draft, and an issued invoice that is voided.
    invoice(children={'lines': lines})
    voided = invoice(children={'lines': lines})
    assert write('POST', voided, '/moves/issued', {})[0] == 200
    keep('invoice_void_reason_missing', write('POST', voided, '/void', {'reason': ''}))
    keep('invoice_void', write('POST', voided, '/void', {'reason': 'Customer cancelled the order'}))
    keep('invoices', editor('GET', '/api/invoice?limit=50&offset=0'))
    keep('invoice_measures', editor('GET', '/api/invoice/measures'))
    keep('invoice_aggregate_by_status', editor('GET', '/api/invoice/aggregate?measures=n:count&group_by=status'))
    # Revising the first invoice voids it and opens a draft copy.
    keep('invoice_revision', write('POST', url, '/revise', {'reason': 'Wrong quantity on line 2'}))
    keep('invoice_revised_original', editor('GET', url))


if __name__ == '__main__':
    main()
