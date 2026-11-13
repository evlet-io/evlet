"""Regressions found by a full-feature app: formula read checks, Percent in string formulas, virtual values in
record responses and money measures in the record's currency. Field locks on create are in test_rules_types."""
import time

import pytest

from evlet import (Child, Count, Currency, Database, Decimal, Link, Model, Percent, Query, Ref, Registry, Rule, Select, Sum,
                   Table, Text, User, depends)
from test_business import client
from test_rules_types import add_user, start

ALL = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update'))}
CHAIN = 12


def chained(name, length=CHAIN):
    """A model whose string formulas read other formulas: f1 reads f0 and an input, f2 reads f1 and f0, and so on."""
    namespace = {'permissions': ALL, 'a': Decimal(scale=2, default='1'), 'b': Decimal(scale=2, default='2'),
                 'f0': Decimal(scale=2, compute='a + b')}
    for index in range(1, length):
        namespace[f'f{index}'] = Decimal(scale=2, compute=f'f{index - 1} + a - f{max(index - 2, 0)} + b')
    return type(name, (Model,), namespace)


# 1. Field read checks walk each formula once

def test_read_checks_on_chained_formulas_walk_each_formula_once(tmp_path):
    Chain = chained('Chain')
    app = start(Database(tmp_path / 'chain.sqlite3', Registry(Chain)))
    c = client(app)
    began = time.perf_counter()
    status, record, _ = c('POST', '/api/chain', {'a': '3'})
    assert status == 201, record
    assert c('GET', f"/api/chain/{record['id']}")[0] == 200
    assert c('GET', '/api/schema')[0] == 200
    # Twelve chained formulas used to take minutes (the walk doubled with each formula); now well under a second.
    assert time.perf_counter() - began < 5
    assert record[f'f{CHAIN - 1}'] is not None
    policy = app.business.policy
    walks = policy._derivation_walks
    # One walk per declared field for the admin role, whatever reads what.
    assert walks <= len(Chain.__declared_fields__)
    # Trusted code is answered separately from callers (Meta.api limits only callers): one more walk per field.
    policy.fields(User(1, 'admin', 'admin'), Chain)
    walks = policy._derivation_walks
    assert walks <= 2 * len(Chain.__declared_fields__)
    for _ in range(3):
        policy.fields(User(1, 'admin', 'admin'), Chain)
        c('GET', f"/api/chain/{record['id']}")
    assert policy._derivation_walks == walks


def test_a_formula_over_an_unreadable_formula_stays_unreadable(tmp_path):
    class Costed(Model):
        permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'), read_fields=('qty', 'total'))}
        qty = Decimal(scale=2, default='2')
        cost = Decimal(scale=2, default='5')
        unit = Decimal(scale=2, compute='cost * 2')
        total = Decimal(scale=2, compute='unit * qty')

    app = start(Database(tmp_path / 'costed.sqlite3', Registry(Costed)))
    editor = add_user(app, 'editor')
    record = client(app)('POST', '/api/costed', {})[1]
    shown = editor('GET', f"/api/costed/{record['id']}")[1]
    # total reads unit, which reads cost: the editor cannot read cost, so neither formula is shown.
    assert 'total' not in shown and 'unit' not in shown and shown['qty'] == '2.00'


# 2. Percent in string formulas

def test_percent_fields_are_numbers_in_string_formulas(tmp_path):
    class Quote(Model):
        permissions = ALL
        price = Currency(default='200.00')
        discount = Percent(default='12.50')
        saved = Currency(compute='price * discount / 100')
        share = Percent(compute='discount * 2')

        class Line(Child):
            rate = Percent(default='5')
        lines = Table(Line)
        rates = Decimal(scale=2, compute='sum(lines.rate)')

    app = start(Database(tmp_path / 'quote.sqlite3', Registry(Quote)))
    c = client(app)
    status, record, _ = c('POST', '/api/quote', {'children': {'lines': [{'rate': '2.25'}, {'rate': '1.10'}]}})
    assert status == 201, record
    # Exact decimals: 12.50 is twelve and a half, as finite-v1 reads a Percent.
    assert (record['saved'], record['share'], record['rates']) == ('25.00', '25.00', '3.35')


def test_arithmetic_on_a_field_that_is_not_a_number_names_it():
    class Labelled(Model):
        permissions = ALL
        label = Text(default='')
        doubled = Decimal(scale=2, compute='label * 2')

    with pytest.raises(ValueError) as caught:
        Registry(Labelled)
    message = str(caught.value)
    assert 'Labelled.doubled (compute)' in message and 'label is a text field' in message
    assert 'percent' in message


# 3. Virtual values in record responses

class Terms(Model):
    permissions = ALL
    title = Text(required=True)
    limit = Currency(default='100.00')


class Visit(Model):
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'),
                                         read_fields=('terms_link', 'hours', 'double', 'title_copy', 'hours_plus'))}
    terms_link = Link(Terms, required=True)
    hours = Decimal(scale=2, default='2')
    rate = Currency(default='10.00')
    terms = Query(Terms, filters={'id': Ref('terms_link')}, fields=['limit'])
    limit_copy = Currency(compute='terms.limit', stored=False)
    double = Currency(compute='hours * 10', stored=False)
    charged = Currency(compute='hours * rate', stored=False)
    title_copy = Text(fetch='terms_link.title', stored=False)

    @depends('hours')
    def plus(self):
        return self.hours + 1
    hours_plus = Decimal(scale=2, query=plus, stored=False)

    class Line(Child):
        qty = Decimal(scale=2, default='1')
        twice = Decimal(scale=2, compute='qty * 2', stored=False)
    lines = Table(Line)


@pytest.fixture
def visits(tmp_path):
    app = start(Database(tmp_path / 'visits.sqlite3', Registry(Terms, Visit)))
    c = client(app)
    terms = c('POST', '/api/terms', {'title': 'Gold', 'limit': '500'})[1]
    status, visit, _ = c('POST', '/api/visit', {'terms_link': terms['id'],
                                                 'children': {'lines': [{'qty': str(index)} for index in range(1, 4)]}})
    assert status == 201, visit
    return app, c, terms, visit


VIRTUAL = {'limit_copy': '500.00', 'double': '20.00', 'charged': '20.00', 'title_copy': 'Gold', 'hours_plus': '3.00'}


def test_record_responses_include_virtual_results(visits):
    app, c, terms, visit = visits
    # The create response, the record and an update response all carry them, rows too.
    assert {name: visit[name] for name in VIRTUAL} == VIRTUAL
    status, shown, headers = c('GET', f"/api/visit/{visit['id']}")
    assert status == 200 and {name: shown[name] for name in VIRTUAL} == VIRTUAL
    assert [row['twice'] for row in shown['children']['lines']] == ['2.00', '4.00', '6.00']
    status, changed, _ = c('PATCH', f"/api/visit/{visit['id']}", {'hours': '4'}, headers={b'if-match': headers[b'etag']})
    assert status == 200 and (changed['double'], changed['charged'], changed['hours_plus']) == ('40.00', '40.00', '5.00')
    # A change on the linked record shows on the next read, and does not change the record's revision.
    _, before, etag = c('GET', f"/api/visit/{visit['id']}")
    _, _, terms_etag = c('GET', f"/api/terms/{terms['id']}")
    c('PATCH', f"/api/terms/{terms['id']}", {'title': 'Platinum', 'limit': '900'}, headers={b'if-match': terms_etag[b'etag']})
    _, after, again = c('GET', f"/api/visit/{visit['id']}")
    assert (after['title_copy'], after['limit_copy']) == ('Platinum', '900.00')
    assert etag[b'etag'] == again[b'etag']


def test_virtual_results_follow_field_read_checks(visits):
    app, c, terms, visit = visits
    editor = add_user(app, 'editor')
    shown = editor('GET', f"/api/visit/{visit['id']}")[1]
    # charged reads rate and limit_copy reads a query output, which the editor cannot read: they stay out.
    assert {name for name in VIRTUAL if name in shown} == {'double', 'title_copy', 'hours_plus'}
    assert 'charged' not in shown and 'limit_copy' not in shown and 'rate' not in shown


def test_row_pages_include_virtual_results(visits):
    app, c, terms, visit = visits
    status, page, _ = c('GET', f"/api/visit/{visit['id']}/lines/lines?limit=2&offset=1")
    assert status == 200, page
    assert [row['twice'] for row in page['items']] == ['4.00', '6.00'] and page['total'] == 3


# 5. Money measures carry the currency they are in

class Customer(Model):
    permissions = ALL
    title = Text(required=True)
    currency_code = Select(['AED', 'SAR', 'USD'], default='AED')

    class Meta:
        measures = {'contracts': Count('contract', link='customer'),
                    'contract_value': Sum('value', model='contract', link='customer'),
                    'fees': Sum('fee', model='contract', link='customer')}


class Contract(Model):
    permissions = ALL
    customer = Link(Customer, required=True)
    currency_code = Select(['AED', 'SAR', 'USD'], default='AED')
    value = Currency(default='0.00', currency='customer.currency_code')
    fee = Currency(default='0.00', currency='currency_code')

    class Meta:
        measures = {'count': Count(), 'total_fees': Sum('fee'), 'total_value': Sum('value')}


@pytest.fixture
def contracts(tmp_path):
    app = start(Database(tmp_path / 'money.sqlite3', Registry(Customer, Contract)))
    c = client(app)
    riyadh = c('POST', '/api/customer', {'title': 'Riyadh Co', 'currency_code': 'SAR'})[1]
    dubai = c('POST', '/api/customer', {'title': 'Dubai Co', 'currency_code': 'AED'})[1]
    for customer, code, value in ((riyadh, 'SAR', '100.00'), (riyadh, 'SAR', '50.00'), (dubai, 'USD', '10.00')):
        assert c('POST', '/api/contract', {'customer': customer['id'], 'currency_code': code, 'value': value, 'fee': '1.00'})[0] == 201
    return app, c, riyadh, dubai


def test_per_record_measures_use_the_records_currency(contracts):
    app, c, riyadh, dubai = contracts
    body = c('GET', f"/api/customer/{riyadh['id']}/measures")[1]
    assert body['measures'] == {'contracts': 2, 'contract_value': '150.00', 'fees': '2.00'}
    # A count has no currency; a sum is in the record's currency (SAR), not the default.
    assert body['currencies'] == {'contract_value': 'SAR', 'fees': 'SAR'}
    assert c('GET', f"/api/customer/{dubai['id']}/measures")[1]['currencies'] == {'contract_value': 'AED', 'fees': 'USD'}


def test_model_measures_and_aggregates_name_a_currency_only_when_all_rows_share_it(contracts):
    app, c, riyadh, dubai = contracts
    # Fees are in SAR and USD across all contracts: no code rather than a wrong one.
    assert 'currencies' not in c('GET', '/api/contract/measures')[1]
    filtered = c('GET', '/api/contract/measures?filters=' + '{"currency_code":"SAR"}')[1]
    assert filtered['currencies'] == {'total_fees': 'SAR', 'total_value': 'SAR'}
    # The aggregate route (record charts) answers the same way.
    aggregate = c('GET', f"/api/contract/aggregate?measures=count,total_fees&filters=" + '{"customer":"' + str(riyadh['id']) + '"}')[1]
    assert aggregate['items'] == [{'count': 2, 'total_fees': '2.00'}] and aggregate['currencies'] == {'total_fees': 'SAR'}


def test_a_currency_the_caller_cannot_read_is_left_out(tmp_path):
    class Wallet(Model):
        permissions = {**ALL, 'editor': Rule(('read',), read_fields=('owner', 'balance'))}
        owner = Text(default='')
        code = Select(['AED', 'SAR'], default='SAR')
        balance = Currency(default='5.00', currency='code')

        class Meta:
            measures = {'balances': Sum('balance')}

    app = start(Database(tmp_path / 'wallet.sqlite3', Registry(Wallet)))
    c = client(app)
    c('POST', '/api/wallet', {})
    assert c('GET', '/api/wallet/measures')[1]['currencies'] == {'balances': 'SAR'}
    assert 'currencies' not in add_user(app, 'editor')('GET', '/api/wallet/measures')[1]
