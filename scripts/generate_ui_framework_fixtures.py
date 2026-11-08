"""Record real authenticated ASGI responses for the browser UI framework tests.

Writes frontend/src/evlet/tests/api-fixture.json and screens-fixture.json. The API client, record
store and screen tests replay these responses through a fetch stub, so they consume the shapes the
backend really sends. tests/test_ui_contracts.py records them again and compares.
Run from the repository root: .venv/bin/python -m scripts.generate_ui_framework_fixtures
"""

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import quote
from tempfile import TemporaryDirectory

from evlet import (Application, Database, LocalAuth, Registry, Model, Child, Table, Decimal, Currency, Integer, Text,
                   Date, Link, Rule, ValidationError, Select, action, Email, LongText, Attachments, Boolean, Count, Effect,
                   Move, MultiSelect, Naming, Reference, Related, Sequence, Series, State, Sum, Swap, AppSettings, UserPreferences, Slip, Outcome,
                   Report, Column, Chart, Count)
from evlet.calc import D, F, round, days
from evlet.identity import sequential_ids
from evlet.audit import fixed_times

PASSWORD = 'a sufficiently long fixture password'
FULL = Rule(('read', 'create', 'update', 'delete'))
ALL = {'admin': FULL, 'editor': FULL, 'viewer': Rule(('read',))}
OUTPUT = Path(__file__).resolve().parent.parent / 'frontend/src/evlet/tests/api-fixture.json'
SCREENS = OUTPUT.with_name('screens-fixture.json')


class FiniteMeta:
    calculation_language = 'finite-v1'
    numeric_profile = 'finite40-hu18-v1'


class Customer(Model):
    permissions = ALL
    title = Text(default='customer', label='Customer name')
    rate = Currency(default=0)
    credit_days = Integer(default=30)


class Product(Model):
    permissions = ALL
    title = Text(default='product')
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


class Contact(Model):
    """For the screens: field permissions per role, an action, attachments."""
    permissions = {'admin': FULL, 'editor': FULL, 'viewer': Rule(('read',), read_fields=('title', 'email'))}
    title = Text(required=True, label='Full name')
    email = Email()
    phone = Text()
    notes = LongText()

    class Meta:
        list_fields = ('title', 'email', 'phone')

    @action(label='Mark contacted')
    def contacted(self, note: str = ''):
        self.notes = note


class LedgerEntry(Model):
    """The target of the invoice's effects: editors read it, only the engine writes it."""
    permissions = {'admin': FULL, 'editor': Rule(('read',)), 'viewer': Rule(())}
    customer = Link(Customer)
    debit = Currency(default=0)
    credit = Currency(default=0)
    memo = Text(default='')
    source = Reference()
    reversal = Boolean(default=False)

    class Meta:
        plural = 'Ledger entries'


class InvoiceLine(Child):
    item = Text(default='')
    qty = Decimal(default=1, minimum='0')
    rate = Currency(default=0)
    amount = Currency(compute=round(F('qty') * F('rate'), 2))


class InvoiceMeta(FiniteMeta):
    revisable = True
    board = 'status'
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
                   locked=('issued', 'paid', 'void'), final=('paid', 'void'), voidable=('issued',), show='bar')
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


def record_api(folder):
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
    customer = keep('customer', c('POST', '/api/customer', {'title': 'Al Sahra Trading', 'rate': '12.50', 'credit_days': 45}))
    product = keep('product', c('POST', '/api/product', {'title': 'Support hours', 'price': '250.00'}))
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
    keep('not_found', c('GET', '/api/order/000000000yghz'))
    keep('logout', c('POST', '/api/auth/logout'))
    documents(out, folder)
    database.close()
    return out


def documents(out, folder):
    """Responses of the document routes, recorded as an editor: a move refused by its condition, a move,
    an action, void, revise, per-record and model measures, related records, line pages and details
    carrying _moves, _actions, _void, _revise, _effects and _journal, with the schema's state, key_fields,
    relations and journal, and the journal itself: engine and user entries, follow ups, and a viewer's partial view."""
    database = Database(folder / 'documents.sqlite3', Registry(Customer, LedgerEntry, Invoice, Payment))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    auth.create_user(username='sara', password=PASSWORD, role='editor')
    auth.create_user(username='omar', password=PASSWORD, role='viewer')

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

    admin, editor, viewer = login('admin'), login('sara'), login('omar')

    def write(method, record, route='', body=None):
        # Every write on a saved document sends the ETag of its last read.
        return editor(method, record + route, body, headers={b'if-match': editor('GET', record)[2][b'etag']})

    customer = admin('POST', '/api/customer', {'title': 'Al Sahra Trading', 'credit_days': 30})[1]['id']
    keep('invoice_session', editor('GET', '/api/auth/session'))
    keep('invoice_schema', editor('GET', '/api/schema'))
    lines = [{'_key': 'a', 'item': 'Support hours', 'qty': '10', 'rate': '250.00'}, {'_key': 'b', 'item': 'Licence', 'qty': '2', 'rate': '90.00'}]

    def invoice(**values):
        status, body, _ = editor('POST', '/api/invoice', {'customer': customer, 'posting_date': '2026-11-01', 'due_on': '2026-12-01', **values})
        assert status == 201, body
        return f"/api/invoice/{body['id']}"

    # An issued invoice that is voided, and one that stays issued until it is revised below.
    voided = invoice(children={'lines': lines})
    assert write('POST', voided, '/moves/issued', {})[0] == 200
    keep('invoice_void_before', editor('GET', voided))
    keep('invoice_void_reason_missing', write('POST', voided, '/void', {'reason': ''}))
    keep('invoice_void', write('POST', voided, '/void', {'reason': 'Customer cancelled the order'}))
    revised = invoice(children={'lines': lines})
    assert write('POST', revised, '/moves/issued', {})[0] == 200
    keep('invoice_revise_before', editor('GET', revised))
    # A draft refused while it has no lines, then given lines: the list holds one invoice per state.
    url = invoice()
    keep('invoice_draft_blocked', editor('GET', url))
    keep('invoice_move_refused', write('POST', url, '/moves/issued', {}))
    assert write('PATCH', url, '', {'children': {'lines': {'create': lines}}})[0] == 200
    keep('invoice_draft', editor('GET', url))
    keep('invoices', editor('GET', '/api/invoice?limit=50&offset=0'))
    keep('invoice_measures', editor('GET', '/api/invoice/measures'))
    keep('invoice_measures_issued', editor('GET', '/api/invoice/measures?filters=' + quote(json.dumps({'status': 'issued'}))))
    keep('invoice_aggregate_by_status', editor('GET', '/api/invoice/aggregate?measures=n:count&group_by=status'))
    keep('invoice_list_tabs', editor('GET', '/api/invoice/list_tabs'))
    keep('invoice_related_revisions', editor('GET', f'{url}/related/invoice.revises?limit=10&offset=0'))
    keep('invoice_attachment_upload', editor('POST', f'{url}/attachments', {'name': 'signed-copy.pdf', 'content': 'JVBERi0='}))
    keep('invoice_attachments', editor('GET', f'{url}/attachments'))
    # The draft's journal so far: created, its lines added, the file.
    keep('invoice_draft_journal', editor('GET', f'{url}/journal?limit=50'))
    # The draft is issued and paid in part.
    keep('invoice_issued', write('POST', url, '/moves/issued', {}))
    keep('invoice_issued_detail', editor('GET', url))
    keep('invoice_action', write('POST', url, '/actions/record_payment', {'amount': '500.00', 'paid_on': '2026-11-10'}))
    payment = admin('POST', '/api/payment', {'invoice': int(url.rsplit('/', 1)[1]), 'amount': '500.00', 'paid_on': '2026-11-10'})[1]
    keep('invoice_payment', editor('GET', f"/api/payment/{payment['id']}"))
    keep('invoice_customer', editor('GET', f'/api/customer/{customer}'))
    keep('invoice_lines_page', editor('GET', f'{url}/lines/lines?limit=1&offset=1'))
    keep('invoice_record_measures', editor('GET', f'{url}/measures'))
    keep('invoice_related_payments', editor('GET', f'{url}/related/payment.invoice?limit=5&offset=0'))
    # Files of a locked record cannot change.
    keep('invoice_attachment_locked', editor('POST', f'{url}/attachments', {'name': 'receipt.pdf', 'content': 'JVBERi0='}))
    # People add to the journal of the locked invoice: a note, a follow up that is done, one that stays open.
    keep('invoice_journal_note', editor('POST', f'{url}/journal', {'kind': 'note', 'text': 'Customer asked for a copy by email'}))
    called = keep('invoice_journal_follow_up', editor('POST', f'{url}/journal', {'kind': 'follow_up', 'text': 'Call about the balance',
                                                                                'due': '2026-11-15', 'assignee': 2}))
    keep('invoice_journal_follow_up_done', editor('POST', f'{url}/journal', {'kind': 'follow_up_done', 'follow_up': called['id'],
                                                                             'text': 'Balance due by the end of the month'}))
    editor('POST', f'{url}/journal', {'kind': 'follow_up', 'text': 'Send the statement', 'due': '2026-11-30'})
    # A scheduled job logs a reminder: an entry without a signed in user (actor null).
    from evlet import journal
    from evlet.auth import User
    with app.business.context(User(0, 'schedule', 'admin')) as context:
        journal.post(context, Invoice, int(url.rsplit('/', 1)[1]), 'message', text='Payment reminder logged', subject='Balance due')
    keep('invoice_journal', editor('GET', f'{url}/journal?limit=50'))
    # The viewer cannot read ledger entries, so the posting entry is a partial `changed` entry for it.
    keep('invoice_viewer_session', viewer('GET', '/api/auth/session'))
    keep('invoice_viewer_schema', viewer('GET', '/api/schema'))
    keep('invoice_viewer_detail', viewer('GET', url))
    keep('invoice_viewer_journal', viewer('GET', f'{url}/journal?limit=50'))
    keep('invoice_viewer_note_refused', viewer('POST', f'{url}/journal', {'kind': 'note', 'text': 'Seen'}))
    # Revising voids the issued invoice and opens a draft copy.
    keep('invoice_revision', write('POST', revised, '/revise', {'reason': 'Wrong quantity on line 2'}))
    keep('invoice_revised_original', editor('GET', revised))
    # An admin unposts an issued invoice (a reason is required) and posts it again; the editor has neither.
    def admin_write(method, record, route='', body=None):
        return admin(method, record + route, body, headers={b'if-match': admin('GET', record)[2][b'etag']})
    unposted = invoice(children={'lines': lines})
    assert write('POST', unposted, '/moves/issued', {})[0] == 200
    keep('invoice_unpost_before', admin('GET', unposted))
    keep('invoice_unpost_editor', editor('GET', unposted))
    keep('invoice_unpost_reason_missing', admin_write('POST', unposted, '/unpost', {'reason': ''}))
    keep('invoice_unpost', admin_write('POST', unposted, '/unpost', {'reason': 'Wrong customer'}))
    keep('invoice_repost', admin_write('POST', unposted, '/repost', {}))
    keep('invoice_admin_session', admin('GET', '/api/auth/session'))
    database.close()


class SalesSettings(AppSettings):
    editors = ('editor',)
    sections = {'Orders': ('terms', 'vat', 'lock'), 'Work week': ('days',)}
    terms = Integer(default=30, minimum=0, label='Default payment terms', suffix='days')
    vat = Decimal(scale=2, default='5', label='Default VAT rate', suffix='%')
    lock = Boolean(default=True, label='Lock orders once they are sent')
    days = MultiSelect(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], default=['mon', 'tue', 'wed', 'thu', 'fri'], label='Working days')


class SalesPreferences(UserPreferences):
    print_format = Select(['standard', 'tax', 'simple'], default='standard', label='My default print format')
    cc_me = Boolean(default=False, label='Copy me on orders I send')


def settings_shape(payload):
    """Sessions carry random ids and times; the fixture keeps one fixed session."""
    payload['security']['sessions'] = [{'id': '0' * 16, 'current': True, 'started': 1793534400, 'expires': 1793563200, 'agent': 'Browser', 'realm': 'desk'}]
    return payload


class Quote(Model):
    """For the screens: a naming series names each quote when it is saved."""
    permissions = ALL
    number = Text(unique=True, max_length=40)
    customer = Link(Customer)
    issued = Date()

    class Meta:
        title_field = 'number'
        naming = Naming('number', Series('QT-{YYYY}-{#####}'), date_field='issued')


def record_screens(folder):
    """Responses the generated screens consume: first-run setup, list paging, search, sorting and
    filters, field permissions per role, and attachments."""
    database = Database(folder / 'screens.sqlite3', Registry(Customer, Product, Order, Contact, Quote))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth, attachments=(Contact,))
    app.business.settings.register('sales', SalesSettings, SalesPreferences, (Order,))
    app.initialize()
    out = {}

    def keep(name, response):
        status, payload, headers = response
        if isinstance(payload, dict) and isinstance(payload.get('csrf'), str):
            payload['csrf'] = 'fixture-csrf-token'
        out[name] = {'status': status, 'body': payload, 'etag': headers.get(b'etag', b'').decode() or None}
        return payload

    keep('session_setup', http(app, 'GET', '/api/auth/session'))
    keep('setup', http(app, 'POST', '/api/auth/setup', {'username': 'owner', 'password': PASSWORD}))

    def login(username):
        status, data, headers = http(app, 'POST', '/api/auth/login', {'username': username, 'password': PASSWORD})
        assert status == 200, data
        cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']
        return lambda method, path, body=None, **kwargs: http(app, method, path, body, cookie=cookie, csrf=csrf, **kwargs)

    admin = login('owner')
    keep('schema', admin('GET', '/api/schema'))
    names = ['Al Sahra Trading', 'Bayan Medical', 'Corniche Foods', 'Desert Rose Interiors', 'Emirates Glass', 'Falcon Logistics',
             'Gulf Pearl Hotels', 'Hamdan Engineering', 'Ibn Battuta Travel', 'Jumeirah Dental', 'Khalifa Steel', 'Lulu Packaging',
             'Marina Yachts', 'Noor Pharmacy', 'Oasis Water', 'Palm Realty', 'Qasr Events', 'Ras Al Khor Motors', 'Sahara Labs',
             'Tilal Farms', 'Umm Suqeim Bakery', 'Wadi Textiles', 'Yas Fitness', 'Zabeel Printing', 'Zayed Medical Supplies'] + [f'Trading house {number}' for number in range(26, 56)]
    for index, name in enumerate(names):
        assert admin('POST', '/api/customer', {'title': name, 'rate': f'{10 + index}.50', 'credit_days': 15 + index})[0] == 201
    keep('customers_page1', admin('GET', '/api/customer?limit=50&offset=0&order_by=title'))
    keep('customers_page2', admin('GET', '/api/customer?limit=50&offset=50&order_by=title'))
    keep('customers_by_rate_desc', admin('GET', '/api/customer?limit=50&offset=0&order_by=-rate'))
    keep('customers_search', admin('GET', '/api/customer?limit=50&offset=0&q=medical'))
    for title, stage in (('Office fit-out', 'open'), ('Annual support', 'won'), ('Warehouse racks', 'open'), ('Fleet tracking', 'lost')):
        status, body, _ = admin('POST', '/api/order', {'title': title, 'stage': stage, 'customer': 1, 'issued': '2026-10-20',
                                                       **({'reason': 'Budget moved'} if stage == 'lost' else {})})
        assert status == 201, body
    keep('orders_open', admin('GET', '/api/order?limit=20&offset=0&filters=%7B%22stage%22%3A%22open%22%7D'))
    contact = keep('contact_create', admin('POST', '/api/contact', {'title': 'Mariam Haddad', 'email': 'mariam@example.com',
                                                                   'phone': '+971 4 555 0101', 'notes': 'Prefers email.'}))
    url = f"/api/contact/{contact['id']}"
    keep('contact_detail', admin('GET', url))
    keep('attachments_empty', admin('GET', f'{url}/attachments'))
    keep('attachment_upload', admin('POST', f'{url}/attachments', {'name': 'brief.txt', 'content': 'SGVsbG8='}))
    keep('attachments', admin('GET', f'{url}/attachments'))
    # One byte over the framework maximum of 1 MiB: the record view shows why it was refused.
    keep('attachment_refused', admin('POST', f'{url}/attachments', {'name': 'scan.pdf', 'content': base64.b64encode(bytes(2**20 + 1)).decode()}))
    keep('contacts', admin('GET', '/api/contact?limit=20&offset=0'))
    quote = keep('quote_create', admin('POST', '/api/quote', {'customer': 1, 'issued': '2026-10-20'}))
    keep('quote_detail', admin('GET', f"/api/quote/{quote['id']}"))
    assert admin('PATCH', '/api/_settings/me/profile', {'full_name': 'Owner Admin', 'short_name': 'Owner', 'title': 'Managing Director'})[0] == 200
    out['settings_me'] = {'status': 200, 'body': settings_shape(admin('GET', '/api/_settings/me')[1]), 'etag': None}
    keep('settings_prefs', admin('PATCH', '/api/_settings/me/prefs', {'date': 'd MMM yyyy'}))
    keep('settings_app', admin('GET', '/api/_settings/app/sales'))
    keep('settings_app_saved', admin('PATCH', '/api/_settings/app/sales', {'values': {'terms': 45, 'days': ['mon', 'tue', 'wed', 'thu']}}))
    auth.create_user(username='viewer', password=PASSWORD, role='viewer')
    viewer = login('viewer')
    keep('viewer_settings_app', viewer('GET', '/api/_settings/app/sales'))
    keep('viewer_session', viewer('GET', '/api/auth/session'))
    keep('viewer_schema', viewer('GET', '/api/schema'))
    keep('viewer_contact_detail', viewer('GET', url))
    keep('viewer_contacts', viewer('GET', '/api/contact?limit=20&offset=0'))
    database.close()
    return out


class Stop(Child):
    place = Text(required=True)
    hours = Decimal(minimum='0', default='0')


class PlanVisit(Slip):
    title = 'Plan a visit'
    hand_in_label = 'Plan visit'
    source = Customer
    layout = (('Visit', ('customer', 'visit_on', 'kind', 'rate')), ('Stops', ('stops',)))
    customer = Link(Customer, read_only=True)
    visit_on = Date(required=True)
    kind = Select(['survey', 'install'], default='survey')
    rate = Currency(visible_if="kind == 'install'", required_if="kind == 'install'")
    stops = Table(Stop, required=True)
    hours = Decimal(compute='sum(stops.hours)', stored=False)

    def load(self, source):
        self.customer = source.id

    def hand_in(self):
        return Outcome(f'Visit planned for {self.visit_on.isoformat()}', data={'hours': str(self.hours)})


class QuickCustomer(Slip):
    title = 'Quick customer'
    menu = True
    becomes = Customer
    customer_name = Text(required=True)
    credit_days = Integer(default=30)

    def document(self, source):
        # A slip's own `title` is its heading, so the slip names the customer in customer_name.
        return {'title': self.customer_name, 'credit_days': self.credit_days}


def record_slips(folder):
    """Slips: a schema that lists them, then present, check and hand-in of a slip from a record and of a
    New menu slip that becomes a record for review."""
    database = Database(folder / 'slips.sqlite3', Registry(Customer, Product))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth, slips=(PlanVisit, QuickCustomer))
    app.initialize()
    out = {}

    def keep(name, response):
        status, payload, headers = response
        if isinstance(payload, dict) and isinstance(payload.get('csrf'), str):
            payload['csrf'] = 'fixture-csrf-token'
        out[name] = {'status': status, 'body': payload, 'etag': headers.get(b'etag', b'').decode() or None}
        return payload

    assert http(app, 'POST', '/api/auth/setup', {'username': 'owner', 'password': PASSWORD})[0] == 201
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': 'owner', 'password': PASSWORD})
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']
    admin = lambda method, path, body=None, **kwargs: http(app, method, path, body, cookie=cookie, csrf=csrf, **kwargs)
    keep('slip_session', admin('GET', '/api/auth/session'))
    keep('slip_schema', admin('GET', '/api/schema'))
    assert admin('POST', '/api/customer', {'title': 'Corniche Foods', 'rate': '12.50', 'credit_days': 30})[0] == 201
    customer = keep('slip_customer', admin('GET', '/api/customer/0000000000001'))
    source = {'model': 'customer', 'id': customer['id']}
    keep('slip_present', admin('POST', '/api/_slips/plan_visit/present', {'source': source, 'values': {}}))
    keep('slip_check', admin('POST', '/api/_slips/plan_visit/check', {'source': source, 'revision': 1, 'values': {
        'visit_on': None, 'kind': 'install', 'rate': None, 'children': {'stops': [{'_key': 'row-1', 'place': 'Warehouse', 'hours': '2.5'}]}}}))
    revision = {b'if-match': out['slip_customer']['etag'].encode()}
    keep('slip_hand_in_invalid', admin('POST', '/api/_slips/plan_visit/hand-in', {'source': source, 'key': 'fixture-visit-1', 'values': {
        'visit_on': None, 'kind': 'install', 'rate': None, 'children': {'stops': [{'_key': 'row-1', 'place': None, 'hours': '2.5'}]}}}, headers=revision))
    keep('slip_hand_in', admin('POST', '/api/_slips/plan_visit/hand-in', {'source': source, 'key': 'fixture-visit-2', 'values': {
        'visit_on': '2026-11-16', 'kind': 'install', 'rate': '350.00', 'children': {'stops': [{'_key': 'row-1', 'place': 'Warehouse', 'hours': '2.5'}]}}}, headers=revision))
    keep('slip_quick_present', admin('POST', '/api/_slips/quick_customer/present', {'source': None, 'values': {}}))
    keep('slip_quick_hand_in', admin('POST', '/api/_slips/quick_customer/hand-in', {'source': None, 'key': 'fixture-quick-1', 'values': {'customer_name': 'Oasis Water', 'credit_days': 45}}))
    database.close()
    return out


class CustomerRates(Report):
    """A report for the report view tests: filters, a link column, a sortable total and a chart."""
    title = 'Customer rates'
    roles = ('admin', 'editor', 'viewer')
    group = 'Sales'
    model = Customer
    sortable = ('title', 'rate')
    chart = Chart('bar', x='title', y=('rate',))
    min_days = Integer(default=0, label='Credit days from')
    search = Text(default='', label='Name contains')
    columns = (Column('id', type='link', link='customer', label='Customer'), Column('title'),
               Column('credit_days', type='integer', label='Credit days'),
               Column('rate', type='currency', total=True, unit='AED', source='customer.rate'))

    def run(self):
        filters = {'credit_days__gte': self.min_days or 0}
        if self.search: filters['title__contains'] = self.search
        page = self.context.list(Customer, filters=filters, order_by=self.context.sort or ('title',), limit=self.context.limit, offset=self.context.offset)
        everything = self.context.aggregate(Customer, measures={'rate': Sum('rate')}, filters=filters)
        return {'rows': [{key: row.get(key) for key in ('id', 'title', 'credit_days', 'rate')} for row in page['rows']],
                'total': page['total'], 'totals': {'rate': everything[0]['rate'] or 0}}


class RatesByTerms(CustomerRates):
    """The same rows grouped by credit days, with each group's count and total over every row."""
    title = 'Rates by terms'
    group_by = 'credit_days'
    sortable = ('rate',)
    chart = None
    columns = (Column('credit_days', type='integer', label='Credit days'), Column('id', type='link', link='customer', label='Customer'),
               Column('rate', type='currency', total=True, unit='AED', source='customer.rate'))

    def run(self):
        filters = {'credit_days__gte': self.min_days or 0}
        page = self.context.list(Customer, filters=filters, order_by=self.context.sort, limit=self.context.limit, offset=self.context.offset)
        groups = self.context.aggregate(Customer, measures={'n': Count(), 'rate': Sum('rate')}, group_by=('credit_days',), filters=filters)
        return {'rows': [{key: row.get(key) for key in ('credit_days', 'id', 'rate')} for row in page['rows']], 'total': page['total'],
                'groups': [{'value': group['credit_days'], 'count': group['n'], 'totals': {'rate': group['rate']}} for group in groups]}


def record_reports(folder):
    """Reports: the schema that lists one, a filter check, a run, a sorted run and a run with an unfinished filter."""
    database = Database(folder / 'reports.sqlite3', Registry(Customer))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth, reports=(CustomerRates, RatesByTerms))
    app.initialize()
    out = {}

    def keep(name, response):
        status, payload, headers = response
        if isinstance(payload, dict) and isinstance(payload.get('csrf'), str):
            payload['csrf'] = 'fixture-csrf-token'
        out[name] = {'status': status, 'body': payload, 'etag': headers.get(b'etag', b'').decode() or None}
        return payload

    assert http(app, 'POST', '/api/auth/setup', {'username': 'owner', 'password': PASSWORD})[0] == 201
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': 'owner', 'password': PASSWORD})
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']
    admin = lambda method, path, body=None, **kwargs: http(app, method, path, body, cookie=cookie, csrf=csrf, **kwargs)
    for title, rate, days in (('Corniche Foods', '1250.00', 30), ('Al Noor Trading', '480.50', 45), ('Blue Dune Interiors', '2100.00', 15),
                              ('Oasis Water', '310.00', 30)):
        assert admin('POST', '/api/customer', {'title': title, 'rate': rate, 'credit_days': days})[0] == 201
    keep('report_session', admin('GET', '/api/auth/session'))
    keep('report_schema', admin('GET', '/api/schema'))
    keep('report_evaluate', admin('POST', '/api/_reports/CustomerRates/evaluate', {'filters': {'min_days': 20, 'search': ''}, 'revision': 1}))
    keep('report_evaluate_invalid', admin('POST', '/api/_reports/CustomerRates/evaluate', {'filters': {'min_days': 'many', 'search': ''}, 'revision': 1}))
    keep('report_run', admin('POST', '/api/_reports/CustomerRates', {'filters': {'min_days': 20, 'search': ''}, 'limit': 50, 'offset': 0, 'sort': None, 'revision': 1}))
    keep('report_run_sorted', admin('POST', '/api/_reports/CustomerRates', {'filters': {'min_days': 20, 'search': ''}, 'limit': 50, 'offset': 0, 'sort': '-rate', 'revision': 2}))
    keep('report_grouped_run', admin('POST', '/api/_reports/RatesByTerms', {'filters': {'min_days': 20, 'search': ''}, 'limit': 50, 'offset': 0, 'sort': None, 'revision': 1}))
    database.close()
    return out


def generate():
    """Both fixtures, recorded from fresh databases. Ids count up per table so the recording is stable."""
    with TemporaryDirectory() as directory, sequential_ids(), fixed_times():
        return {OUTPUT.name: record_api(Path(directory)), SCREENS.name: {**record_screens(Path(directory)), **record_slips(Path(directory)), **record_reports(Path(directory))}}


def main():
    for name, value in generate().items():
        OUTPUT.with_name(name).write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
        print('Updated', name)


if __name__ == '__main__':
    main()
