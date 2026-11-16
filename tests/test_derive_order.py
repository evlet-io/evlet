"""derive() runs between the formulas: real SQLite, through the ASGI routes and Context.

Formulas that read nothing derive() sets run first, so derive() sees final line amounts and subtotals; formulas
that read a derived value run after it. Reading one of those inside derive() is an error, not a stale value.
Both calculation paths: the finite-v1 plan and legacy string formulas."""

from decimal import Decimal as D

import pytest

from evlet import Application, Child, Database, Decimal, Integer, Model, Registry, Rule, Table
from evlet.auth import LocalAuth, User
from test_business import PASSWORD, client, http

FULL = {'admin': Rule(('read', 'create', 'update', 'delete'))}
ADMIN = User(1, 'admin', 'admin')
# The subtotal each derive() call saw, and whether a test asks it to read a formula that depends on it.
SEEN = []
CIRCLE = []


def vat_summary(document):
    """One tax line per VAT rate, from the formula amounts of the lines; a discount above 100."""
    SEEN.append(document.subtotal)
    bases = {}
    for line in document.lines:
        bases[line.vat] = bases.get(line.vat, D(0)) + line.amount
    document.tax_lines = [{'vat': vat, 'base': base, 'tax': (base * vat).quantize(D('0.01'))} for vat, base in sorted(bases.items())]
    document.line_count = len(document.lines)
    document.discount = D(10) if document.subtotal > 100 else D(0)
    if CIRCLE:
        document.discount = document.total


class Invoice(Model):
    permissions = FULL

    class Meta:
        calculation_language = 'finite-v1'

    class Line(Child):
        qty = Decimal(scale=2, default=1)
        rate = Decimal(scale=2, default=0)
        vat = Decimal(scale=2, default='0.05')
        amount = Decimal(scale=2, compute=qty * rate)

    class TaxLine(Child):
        vat = Decimal(scale=2)
        base = Decimal(scale=2)
        tax = Decimal(scale=2)

    lines = Table(Line)
    tax_lines = Table(TaxLine, derived=True)
    line_count = Integer(derived=True)
    discount = Decimal(scale=2, derived=True)
    subtotal = Decimal(scale=2, compute=lines.sum('amount'))
    tax_total = Decimal(scale=2, compute=tax_lines.sum('tax'))
    total = Decimal(scale=2, compute=subtotal + tax_total - discount)

    derive = vat_summary


class Bill(Model):
    """The same document with legacy string formulas."""
    permissions = FULL

    class Line(Child):
        qty = Decimal(scale=2, default=1)
        rate = Decimal(scale=2, default=0)
        vat = Decimal(scale=2, default='0.05')
        amount = Decimal(scale=2, compute='qty * rate')

    class TaxLine(Child):
        vat = Decimal(scale=2)
        base = Decimal(scale=2)
        tax = Decimal(scale=2)

    lines = Table(Line)
    tax_lines = Table(TaxLine, derived=True)
    line_count = Integer(derived=True)
    discount = Decimal(scale=2, derived=True)
    subtotal = Decimal(scale=2, compute='sum(lines.amount)')
    tax_total = Decimal(scale=2, compute='sum(tax_lines.tax)')
    total = Decimal(scale=2, compute='subtotal + tax_total - discount')

    derive = vat_summary


class Exchange(Model):
    """derive() fills a rate on each line people edit; a formula multiplies by it, and derive() reads the amount."""
    permissions = FULL

    class Meta:
        calculation_language = 'finite-v1'

    class Line(Child):
        qty = Decimal(scale=2, default=1)
        rate = Decimal(scale=2, default=0)
        amount = Decimal(scale=2, compute=qty * rate)
        rate_aed = Decimal(scale=6, derived=True)
        amount_aed = Decimal(scale=2, compute=amount * rate_aed)

    lines = Table(Line)
    total_aed = Decimal(scale=2, compute=lines.sum('amount_aed'))

    def derive(self):
        for line in self.lines:
            # A volume rate: larger amounts convert at a better rate, which no formula here can look up.
            line.rate_aed = D('3.6725') if line.amount < 1000 else D('3.6800')
            if CIRCLE:
                line.amount_aed


@pytest.fixture
def app(tmp_path):
    SEEN.clear()
    CIRCLE.clear()
    database = Database(tmp_path / 'order.sqlite3', Registry(Invoice, Bill, Exchange))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app, client(app)


LINES = [{'_key': 'a', 'qty': '2', 'rate': '30.00'}, {'_key': 'b', 'qty': '1', 'rate': '10.00', 'vat': '0'},
         {'_key': 'c', 'qty': '4', 'rate': '5.00'}]


def summary(body):
    return [(row['vat'], row['base'], row['tax']) for row in body['children']['tax_lines']]


@pytest.mark.parametrize('name', ['invoice', 'bill'])
def test_derive_builds_a_vat_summary_from_formula_amounts(app, name):
    app, admin = app
    status, body, _ = admin('POST', f'/api/{name}', {'children': {'lines': LINES}})
    assert status == 201, body
    # derive() saw the line amounts and the subtotal of this write; tax_total and total read what it built.
    assert SEEN == [D('90.00')]
    assert summary(body) == [('0.00', '10.00', '0.00'), ('0.05', '80.00', '4.00')]
    assert (body['subtotal'], body['tax_total'], body['discount'], body['total'], body['line_count']) == \
        ('90.00', '4.00', '0.00', '94.00', 3)
    # Editing a line's qty: the new amount reaches derive(), and the formulas after it follow.
    url = f"/api/{name}/{body['id']}"
    line = body['children']['lines'][0]['id']
    status, after, _ = admin('PATCH', url, {'children': {'lines': {'update': [{'id': line, 'qty': '3'}]}}},
                             headers={b'if-match': admin('GET', url)[2][b'etag']})
    assert status == 200, after
    assert SEEN[-1] == D('120.00')
    assert summary(after) == [('0.00', '10.00', '0.00'), ('0.05', '110.00', '5.50')]
    assert (after['subtotal'], after['tax_total'], after['discount'], after['total']) == ('120.00', '5.50', '10.00', '115.50')
    # A rebuilt row keeps the id of the saved row it replaces.
    assert [row['id'] for row in after['children']['tax_lines']] == [row['id'] for row in body['children']['tax_lines']]
    # The draft preview runs in the same order.
    status, preview, _ = admin('POST', f'/api/_draft/{name}', {'values': {'children': {'lines': LINES[:1]}}})
    assert status == 200 and preview['errors'] == {}, preview
    values = preview['values']
    assert summary(values) == [('0.05', '60.00', '3.00')]
    assert (values['subtotal'], values['tax_total'], values['total']) == ('60.00', '3.00', '63.00')


@pytest.mark.parametrize('model', [Invoice, Bill])
def test_reading_a_formula_that_depends_on_a_derived_value_is_an_error(app, model):
    app, _ = app
    CIRCLE.append(True)
    with app.business.context(ADMIN) as context, \
            pytest.raises(AttributeError, match=r'derive\(\) read total, which depends on discount that derive\(\) sets'):
        context.create(model, children={'lines': LINES})
    with app.database.transaction() as tx:
        assert tx.count(model) == 0


def test_a_derived_row_field_on_a_table_people_edit(app):
    app, admin = app
    status, body, _ = admin('POST', '/api/exchange', {'children': {'lines': [{'qty': '2', 'rate': '10.00'}, {'qty': '1', 'rate': '1000.00'}]}})
    assert status == 201, body
    rows = [(row['amount'], row['rate_aed'], row['amount_aed']) for row in body['children']['lines']]
    assert rows == [('20.00', '3.672500', '73.45'), ('1000.00', '3.680000', '3680.00')]
    assert body['total_aed'] == '3753.45'
    CIRCLE.append(True)
    with app.business.context(ADMIN) as context, \
            pytest.raises(AttributeError, match=r'derive\(\) read lines.amount_aed, which depends on lines.rate_aed that derive\(\) sets'):
        context.create(Exchange, children={'lines': [{'qty': '1'}]})


def test_the_split_is_cached_per_model(app):
    app, _ = app
    registry = app.business.registry
    before, after = registry.derive_split(Invoice)
    assert registry.derive_split(Invoice) is registry.derive_split(Invoice)
    assert after == {'tax_total': 'tax_lines', 'total': 'discount'}
    assert {(tuple(field['path']), field['field']) for field in before['fields']} == {(('lines',), 'amount'), ((), 'subtotal')}
    keys, after = registry.derive_split(Bill)
    assert after == {'tax_total': 'tax_lines', 'total': 'discount'} and {'lines.amount', 'subtotal'} <= set(keys)
