"""Record real authenticated ASGI responses for the browser UI framework tests.

Writes frontend/src/evlet/tests/api-fixture.json and screens-fixture.json. The API client, record
store and screen tests replay these responses through a fetch stub, so they consume the shapes the
backend really sends.
Run from the repository root: .venv/bin/python -m scripts.generate_ui_framework_fixtures
"""

import asyncio
import json
from pathlib import Path
import tempfile

from evlet import (Application, Database, LocalAuth, Registry, Model, Child, Table, Decimal, Currency, Integer, Text,
                   Date, Link, Rule, ValidationError, Select, action, Email, LongText)
from evlet.calc import F, round, days

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


class Contact(Model):
    """For the screens: field permissions per role, an action, attachments."""
    permissions = {'admin': FULL, 'editor': FULL, 'viewer': Rule(('read',), read_fields=('name', 'email'))}
    name = Text(required=True)
    email = Email()
    phone = Text()
    notes = LongText()

    class Meta:
        list_fields = ('name', 'email', 'phone')

    @action(label='Mark contacted')
    def contacted(self, note: str = ''):
        self.notes = note


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
    OUTPUT.write_text(json.dumps(out, indent=1, sort_keys=True) + '\n')


def screens():
    """Responses the generated screens consume: first-run setup, list paging, search, sorting and
    filters, field permissions per role, and attachments."""
    folder = Path(tempfile.mkdtemp())
    database = Database(folder / 'screens.sqlite3', Registry(Customer, Product, Order, Contact))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth, attachments=(Contact,))
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
             'Tilal Farms', 'Umm Suqeim Bakery', 'Wadi Textiles', 'Yas Fitness', 'Zabeel Printing', 'Zayed Medical Supplies']
    for index, name in enumerate(names):
        assert admin('POST', '/api/customer', {'name': name, 'rate': f'{10 + index}.50', 'credit_days': 15 + index})[0] == 201
    keep('customers_page1', admin('GET', '/api/customer?limit=20&offset=0&order_by=name'))
    keep('customers_page2', admin('GET', '/api/customer?limit=20&offset=20&order_by=name'))
    keep('customers_by_rate_desc', admin('GET', '/api/customer?limit=20&offset=0&order_by=-rate'))
    keep('customers_search', admin('GET', '/api/customer?limit=20&offset=0&q=medical'))
    for title, stage in (('Office fit-out', 'open'), ('Annual support', 'won'), ('Warehouse racks', 'open'), ('Fleet tracking', 'lost')):
        status, body, _ = admin('POST', '/api/order', {'title': title, 'stage': stage, 'customer': 1, 'issued': '2026-10-20',
                                                       **({'reason': 'Budget moved'} if stage == 'lost' else {})})
        assert status == 201, body
    keep('orders_open', admin('GET', '/api/order?limit=20&offset=0&filters=%7B%22stage%22%3A%22open%22%7D'))
    contact = keep('contact_create', admin('POST', '/api/contact', {'name': 'Mariam Haddad', 'email': 'mariam@example.com',
                                                                   'phone': '+971 4 555 0101', 'notes': 'Prefers email.'}))
    url = f"/api/contact/{contact['id']}"
    keep('contact_detail', admin('GET', url))
    # This base refuses uploads to models with declarations (permissions, actions...) with 405;
    # the record view shows that refusal. Listing works.
    keep('attachment_upload', admin('POST', f'{url}/attachments', {'name': 'brief.txt', 'content': 'SGVsbG8='}))
    keep('attachments', admin('GET', f'{url}/attachments'))
    keep('contacts', admin('GET', '/api/contact?limit=20&offset=0'))
    auth.create_user(username='viewer', password=PASSWORD, role='viewer')
    viewer = login('viewer')
    keep('viewer_session', viewer('GET', '/api/auth/session'))
    keep('viewer_schema', viewer('GET', '/api/schema'))
    keep('viewer_contact_detail', viewer('GET', url))
    keep('viewer_contacts', viewer('GET', '/api/contact?limit=20&offset=0'))
    SCREENS.write_text(json.dumps(out, indent=1, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
    screens()
