"""Decimal places per currency (evlet.currencies), on real SQLite: ISO defaults (KWD 3, JPY 0), a site override or a
new code in Controls (admin only), the write check, rounding in formulas, derive, sums, measures, print and CSV, raising
and lowering a currency's decimals, and moving databases written before amounts were stored at 6 places."""

from decimal import Decimal as D
import json

import pytest

from evlet import (AddField, AlterField, Application, Child, CreateModel, Currency, Database, Decimal, Link, Migration, Model, Registry, Rule,
                   Select, Sum, Table, Text, ValidationError, currency_decimals, round_money, round_stored_money)
from evlet.auth import LocalAuth, User, password_hash
from evlet.calc import F, Document, compile_plan, formula_hash, parent
from evlet.currencies import MONEY_SCALE, check, decimals_table, stored_excess
from evlet.identity import RecordId
from evlet.migrations import SchemaError, money_scale
from test_business import PASSWORD, client, http

ALL = Rule(('read', 'create', 'update', 'delete'))
FULL = {'admin': ALL, 'editor': ALL}


class Client(Model):
    permissions = FULL
    title = Text(default='')
    currency = Select(['AED', 'KWD', 'JPY', 'XCL'], default='AED')


class Deposit(Model):
    """Plain records: fixed codes, a code on the record, a code behind a Link, and an amount with no currency."""
    permissions = FULL
    client = Link(Client)
    currency = Select(['AED', 'KWD', 'JPY', 'XCL'], default='AED')
    dinars = Currency(currency='KWD')
    dirhams = Currency(currency='AED')
    amount = Currency(currency='currency')
    billed = Currency(currency='client.currency')
    plain = Currency()

    class Meta:
        measures = {'dinars': Sum('dinars'), 'amounts': Sum('amount')}


class Quote(Model):
    """A finite document in its own currency: every calculated amount rounds to that currency's places."""
    permissions = FULL

    class Line(Child):
        currency = Text(max_length=3, compute=parent.currency)
        qty = Decimal(scale=3, default='1')
        rate = Currency(currency='currency', default='0')
        amount = Currency(currency='currency', compute=F('qty') * F('rate'))

    currency = Select(['AED', 'KWD', 'JPY', 'XCL'], default='AED')
    qty = Decimal(scale=3, default='1')
    rate = Currency(currency='currency', default='0')
    net = Currency(currency='currency', compute=F('qty') * F('rate'))
    vat = Currency(currency='currency', compute=F('net') * D('0.05'))
    fee = Currency(currency='KWD', compute=F('qty') * D('0.1234'))
    lines = Table(Line)
    lines_total = Currency(currency='currency', compute=lines.sum('amount'))


class Ledger(Model):
    """derive() rounds with the public helper; an amount it leaves with too many places is refused."""
    permissions = FULL

    class Meta:
        derives = ('vat',)

    currency = Select(['AED', 'KWD'], default='AED')
    net = Currency(currency='currency', default='0')
    vat = Currency(currency='currency')
    exact = Text(default='yes')

    def derive(self):
        raw = (self.net or D(0)) * D('0.05')
        self.vat = round_money(raw, self.currency) if self.exact == 'yes' else raw


class Legacy(Model):
    """A legacy string formula: rounds half even to the currency of the record."""
    permissions = FULL
    currency = Select(['AED', 'KWD', 'JPY'], default='AED')
    qty = Decimal(scale=3, default='1')
    rate = Currency(currency='currency', default='0')
    total = Currency(currency='currency', compute='qty * rate')


MODELS = (Client, Deposit, Quote, Ledger, Legacy)


def application(path, *models, migrations=None):
    database = Database(path, Registry(*(models or MODELS)), migrations=migrations)
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    return app


@pytest.fixture
def app(tmp_path):
    app = application(tmp_path / 'money.sqlite3')
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    with app.database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password', ('editor', password_hash(PASSWORD), 'editor'))
    return app


def login(app, username='admin'):
    return client(app, username)


def save(call, value):
    return call('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'display.currencies', 'value': value}]})


def stored(app, table, column, record_id):
    with app.database._connection() as connection:
        return connection.execute(f'SELECT "{column}" FROM "{table}" WHERE id = ?', (record_id,)).fetchone()[0]


# ---- the table and the helpers -----------------------------------------------------------------

def test_iso_defaults_site_overrides_and_rounding_helpers():
    assert [currency_decimals(code) for code in ('AED', 'USD', 'KWD', 'BHD', 'OMR', 'JPY', 'CLF', 'XYZ', None, 'aed')] == [2, 2, 3, 3, 3, 0, 4, 2, 2, 2]
    site = {'AED': {'decimals': 3}, 'XCL': {'decimals': 5}}
    assert currency_decimals('AED', site) == 3 and currency_decimals('XCL', site) == 5 and currency_decimals('KWD', site) == 3
    assert round_money(D('1.2345'), 'KWD') == D('1.235') and round_money('2.5', 'JPY') == D('3') and round_money('-2.5', 'JPY') == D('-3')
    assert round_money('0.125', 'AED') == D('0.13') and round_money('0.125', 'AED', 'half_even') == D('0.12')
    assert round_money('0.1234567', 'XCL', displays=site) == D('0.12346')
    with pytest.raises(ValueError, match='not a float'):
        round_money(0.1, 'AED')
    table = decimals_table(site)
    assert table['KWD'] == 3 and table['JPY'] == 0 and table['AED'] == 3 and table['XCL'] == 5 and 'USD' not in table
    # The setting keeps a changed or added code's decimals; a code at its ISO value with nothing else changed drops out.
    assert check({'XCL': {'decimals': 5}, 'KWD': {'decimals': 3}, 'AED': {'decimals': 4}}) == {
        'AED': {'symbol': 'AED', 'image': None, 'position': 'prefix', 'space': True, 'tint': False, 'decimals': 4},
        'XCL': {'symbol': 'XCL', 'image': None, 'position': 'prefix', 'space': True, 'tint': False, 'decimals': 5}}
    for bad in (7, -1, '3', True, 2.0):
        with pytest.raises(ValueError, match='decimals is a whole number from 0 to 6'):
            check({'AED': {'decimals': bad}})


# ---- writes ------------------------------------------------------------------------------------

def test_kwd_amounts_keep_three_places_and_others_are_checked_against_their_own(app):
    with app.database.transaction() as tx:
        client = tx.create(Client, title='Kuwait Trading', currency='KWD')
        deposit = tx.create(Deposit, client=client.id, currency='JPY', dinars='1.234', dirhams='1.23', amount='1500', billed='12.345')
    assert stored(app, 'deposit', 'dinars', deposit.id) == 1_234_000  # millionths
    assert deposit.to_dict()['dinars'] == '1.234' and deposit.to_dict()['amount'] == '1500.00' and deposit.dinars == D('1.234')
    cases = [({'dinars': '1.2345'}, 'dinars', 'Use at most 3 decimal places for KWD'),
             ({'dirhams': '1.234'}, 'dirhams', 'Use at most 2 decimal places for AED'),
             ({'amount': '1500.5'}, 'amount', 'Use at most 0 decimal places for JPY'),     # the code on the record
             ({'billed': '1.2345'}, 'billed', 'Use at most 3 decimal places for KWD'),     # the code behind the Link
             ({'plain': '1.234'}, 'plain', 'Use at most 2 decimal places'),               # no currency: 2, as before
             ({'dinars': '1.2345678'}, 'dinars', 'Use at most 6 decimal places')]
    for values, field, message in cases:
        with pytest.raises(ValidationError) as caught, app.database.transaction() as tx:
            tx.update(Deposit, deposit.id, **values)
        assert caught.value.errors[field] == message
    # Changing the record's currency checks its amounts against the new one.
    with pytest.raises(ValidationError) as caught, app.database.transaction() as tx:
        tx.update(Deposit, deposit.id, currency='AED', amount='1.234')
    assert caught.value.errors == {'amount': 'Use at most 2 decimal places for AED'}
    status, payload, _ = login(app)('POST', '/api/deposit', {'currency': 'KWD', 'amount': '0.0005'})
    assert status == 422 and payload['fields']['amount'] == 'Use at most 3 decimal places for KWD'


def test_a_five_decimal_currency_added_in_controls_is_admin_only_and_reaches_the_schema(app):
    admin, editor = login(app), login(app, 'editor')
    status, payload, _ = editor('POST', '/api/deposit', {'currency': 'XCL', 'amount': '0.12345'})
    assert status == 422 and payload['fields']['amount'] == 'Use at most 2 decimal places for XCL'
    assert save(editor, {'XCL': {'decimals': 5}})[0] in (403, 404)   # Controls is for admins
    status, payload, _ = save(admin, {'XCL': {'decimals': 5}, 'AED': {'symbol': 'Dh', 'decimals': 2}})
    assert status == 200, payload
    schema = editor('GET', '/api/schema')[1]
    assert schema['currency_decimals']['XCL'] == 5 and schema['currency_decimals']['KWD'] == 3 and 'AED' not in schema['currency_decimals']
    assert schema['currency_display']['XCL']['decimals'] == 5 and schema['currency_display']['AED']['symbol'] == 'Dh'
    fields = {field['name']: field for field in next(m for m in schema['models'] if m['name'] == 'deposit')['fields']}
    assert fields['amount']['scale'] == MONEY_SCALE and fields['amount']['currency_source'] == {'field': 'currency'}
    status, record, _ = editor('POST', '/api/deposit', {'currency': 'XCL', 'amount': '0.12345'})
    assert status == 201, record
    assert record['amount'] == '0.12345'
    with app.database._connection() as connection:
        detail = connection.execute("SELECT detail FROM evlet_audit WHERE area='Display' ORDER BY id DESC").fetchone()[0]
    assert detail == 'Changed AED, XCL'


# ---- raising and lowering ----------------------------------------------------------------------

def test_raising_is_free_lowering_is_refused_until_an_explicit_step_rounds(app):
    admin = login(app)
    assert save(admin, {'AED': {'decimals': 3}})[0] == 200
    with app.database.transaction() as tx:
        client = tx.create(Client, currency='AED')
        first = tx.create(Deposit, client=client.id, dirhams='10.125', amount='-2.005', billed='1.10')
        second = tx.create(Deposit, currency='KWD', dirhams='3.50', amount='1.001')
    status, payload, _ = save(admin, {'AED': {'decimals': 2}})
    assert status == 409
    assert payload['error'].startswith('2 stored AED amounts have more than 2 decimal places (the first is Deposit #')
    assert 'evlet.round_stored_money' in payload['error']
    assert currency_decimals('AED', app.business.controls.settings['display.currencies']) == 3   # nothing saved
    with app.database._connection() as connection:
        assert stored_excess(connection, app.database.registry, 'AED', 2) == (2, f'Deposit #{first.id} dirhams')
    # The explicit step: half up on the stored millionths, then the setting, both audited.
    assert round_stored_money(app, 'AED', 2, actor='finance-admin') == 2
    with app.database.transaction() as tx:
        first, second = tx.get(Deposit, first.id), tx.get(Deposit, second.id)
    assert (first.dirhams, first.amount, first.billed, second.dirhams, second.amount) == (D('10.13'), D('-2.01'), D('1.10'), D('3.50'), D('1.001'))
    assert currency_decimals('AED', app.business.controls.settings['display.currencies']) == 2
    assert round_stored_money(app, 'AED', 2) == 0     # repeating it changes nothing
    with app.database._connection() as connection:
        details = [row[0] for row in connection.execute("SELECT detail FROM evlet_audit WHERE area='Display' ORDER BY id")]
    assert 'Rounded 2 stored AED amounts to 2 places' in details
    # Lowering with nothing in the way goes straight through, and KWD amounts were never touched.
    assert save(admin, {'AED': {'decimals': 2}, 'KWD': {'decimals': 3}})[0] == 200
    assert save(admin, {'KWD': {'decimals': 2}})[0] == 409


def test_rounding_stored_amounts_is_half_up_away_from_zero_for_negatives(app):
    admin = login(app)
    assert save(admin, {'AED': {'decimals': 3}})[0] == 200
    with app.database.transaction() as tx:
        client = tx.create(Client, currency='AED')
        ids = [tx.create(Deposit, client=client.id, dirhams=dirhams, amount=amount, billed=billed).id for dirhams, amount, billed in (
            ('-0.005', '-0.004', '-1.006'), ('0.005', '0.004', '-0.001'), ('-10.995', '-10.994', '-0.01'))]
    with app.database._connection() as connection:
        assert stored_excess(connection, app.database.registry, 'AED', 2)[0] == 8
    assert round_stored_money(app, 'AED', 2) == 8
    with app.database.transaction() as tx:
        rows = [(row.dirhams, row.amount, row.billed) for row in (tx.get(Deposit, item) for item in ids)]
    assert rows == [(D('-0.01'), D('0.00'), D('-1.01')), (D('0.01'), D('0.00'), D('0.00')), (D('-11.00'), D('-10.99'), D('-0.01'))]
    with app.database._connection() as connection:
        assert stored_excess(connection, app.database.registry, 'AED', 2) == (0, None)


# ---- calculations ------------------------------------------------------------------------------

def test_finite_formulas_round_to_each_rows_currency_and_follow_a_change_of_currency(app):
    admin = login(app)
    # Rows read the document's currency, so their amounts round to it too.
    lines = [{'qty': '3', 'rate': '0.333'}, {'qty': '1.5', 'rate': '1.001'}]
    status, quote, _ = admin('POST', '/api/quote', {'currency': 'KWD', 'qty': '1.5', 'rate': '1.333', 'children': {'lines': lines}})
    assert status == 201, quote
    assert quote['net'] == '2.00' and quote['vat'] == '0.10'        # 1.9995 -> 2.000 at KWD's 3 places; 0.1
    assert quote['fee'] == '0.185'                                  # a fixed KWD fee: 0.1851 -> 0.185
    assert [row['amount'] for row in quote['children']['lines']] == ['0.999', '1.502'] and quote['lines_total'] == '2.501'
    # A change of currency recalculates the amounts that round to it.
    status, quote, headers = admin('POST', '/api/quote', {'currency': 'AED', 'qty': '1.5', 'rate': '1.33'})
    assert status == 201, quote
    assert quote['net'] == '2.00' and quote['vat'] == '0.10'        # 1.995 -> 2.00 at AED's 2 places
    status, quote, headers = admin('PATCH', f'/api/quote/{quote["id"]}', {'currency': 'KWD'}, headers={b'if-match': headers[b'etag']})
    assert status == 200, quote
    assert quote['net'] == '1.995' and quote['vat'] == '0.10'     # 0.09975 -> 0.100
    status, quote, _ = admin('PATCH', f'/api/quote/{quote["id"]}', {'currency': 'JPY', 'rate': '1333'}, headers={b'if-match': headers[b'etag']})
    assert status == 200, quote
    assert quote['net'] == '2000.00' and quote['vat'] == '100.00'   # 1999.5 -> 2000; 100


def test_plan_carries_the_currency_source_and_keeps_saved_formula_hashes():
    registry = Registry(*MODELS)
    plan = compile_plan(registry, Quote)
    entries = {field['target']: field for field in plan['fields']}
    assert entries['net']['currency'] == {'expression': {'$expr': 'ref', 'scope': 'self', 'field': 'currency'}, 'reads': [['self', None, 'currency']]}
    assert entries['fee']['currency'] == {'code': 'KWD'} and entries['net']['scale'] == MONEY_SCALE
    # The hash a saved document recorded before: currency fields at scale 2, no currency source, its own reads only.
    legacy = {**plan, 'fields': [{key: value for key, value in field.items() if key != 'currency'}
                                 | ({'scale': 2, 'reads': [read for read in field['reads'] if read not in field.get('currency', {}).get('reads', [])]}
                                    if field['target_type'] == 'currency' else {}) for field in plan['fields']]}
    for table in legacy['tables'].values():
        for meta in table['fields'].values():
            if meta['kind'] == 'currency':
                meta['scale'] = 2
    assert formula_hash(plan) == formula_hash(legacy)


def test_the_browser_equivalent_document_takes_the_currency_table_as_a_host_input():
    plan = compile_plan(Registry(*MODELS), Quote)
    document = Document(plan, {'currency': 'XCL', 'qty': '1', 'rate': '0.123455'},
                        currencies={'decimals': {'XCL': 5, 'KWD': 3}})
    snapshot = document.snapshot()
    assert snapshot['net'] == '0.12346' and snapshot['fee'] == '0.123'    # XCL 5 places; the fixed KWD fee 0.1234 -> 0.123
    document = Document(plan, {'currency': 'XCL', 'qty': '1', 'rate': '0.1234'}, currencies={'decimals': {}})
    assert document.snapshot()['net'] == '0.12'                            # a code the host does not list has 2


def test_derive_rounds_with_round_money_and_an_unrounded_amount_is_refused(app):
    with app.database.transaction() as tx:
        kwd = tx.create(Ledger, currency='KWD', net='10.123')
        aed = tx.create(Ledger, currency='AED', net='10.13')
    assert (kwd.vat, aed.vat) == (D('0.506'), D('0.51'))   # 0.50615 -> 0.506, 0.5065 -> 0.51
    with pytest.raises(ValidationError) as caught, app.database.transaction() as tx:
        tx.create(Ledger, currency='AED', net='10.13', exact='no')
    assert caught.value.errors == {'vat': 'Use at most 2 decimal places for AED'}


def test_legacy_string_formulas_round_to_the_records_currency(app):
    admin = login(app)
    results = []
    for currency, qty, rate in (('KWD', '1.5', '1.333'), ('JPY', '1.5', '101'), ('AED', '1.5', '1.33')):
        status, record, _ = admin('POST', '/api/legacy', {'currency': currency, 'qty': qty, 'rate': rate})
        assert status == 201, record
        results.append(D(record['total']))
    assert results == [D('2.000'), D('152'), D('2.00')]   # half even: 1.9995 -> 2.000, 151.5 -> 152, 1.995 -> 2.00


def test_measures_and_table_sums_stay_exact_at_the_currency_places(app):
    admin = login(app)
    with app.database.transaction() as tx:
        for value in ('1.111', '2.222', '0.001'):
            tx.create(Deposit, currency='KWD', dinars=value, amount=value)
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.measures(Deposit) == {'dinars': D('3.334'), 'amounts': D('3.334')}
    status, quote, _ = admin('POST', '/api/quote', {'currency': 'KWD', 'rate': '0', 'children': {'lines': [
        {'qty': '1', 'rate': '0.001'}, {'qty': '2', 'rate': '0.001'}]}})
    assert status == 201, quote
    assert D(quote['lines_total']) == D('0.003')


# ---- print and CSV -----------------------------------------------------------------------------

def test_print_shows_each_amount_with_its_currencys_places():
    from evlet.printing import Formats

    class Design:
        currency = None
    site = {'XCL': {'symbol': 'XCL', 'image': None, 'position': 'prefix', 'space': True, 'tint': False, 'decimals': 5}}
    formats = Formats(Design, str, site)   # str stands in for markupsafe.Markup: the same text, unescaped
    shown = lambda value, code: formats.money(value, code).replace('<bdi dir="ltr">', '').replace('</bdi>', '')
    assert shown('1234.5', 'KWD') == 'KWD 1,234.500'
    assert shown('1234.5', 'JPY') == 'JPY 1,235'
    assert shown('0.12345', 'XCL') == 'XCL 0.12345'
    assert shown('5616.35', 'AED') == 'AED 5,616.35'
    assert shown('7', None) == '7.00'


def test_csv_cells_carry_exactly_the_currencys_places():
    from evlet.forms import money_cell

    class Column:
        def __init__(self, unit, type='currency'):
            self.unit, self.type = unit, type
    assert [money_cell('1.5', Column('KWD')), money_cell('100.00', Column('JPY')), money_cell('2.5', Column('AED')),
            money_cell('2.5', Column(None)), money_cell('2.5', Column('KWD', 'decimal'))] == ['1.500', '100', '2.50', '2.5', '2.5']


# ---- moving a database written at 2 places -----------------------------------------------------

def legacy_database(app):
    """Turn a database into one Evlet 0.4 or earlier wrote: amounts as hundredths, Currency fields at scale 2 in the
    saved definition, and no evlet_money row."""
    from evlet import ledger
    with app.database._connection() as connection:
        definition = json.loads(connection.execute('SELECT definition FROM evlet_schema WHERE id = 1').fetchone()[0])
        for model in definition['models']:
            locked = 'ledger' in model and ledger.has_triggers(connection, model['name'])
            if locked:
                ledger.drop_triggers(connection, model['name'])
            for field in model['fields']:
                if field['type'] == 'currency':
                    connection.execute(f'UPDATE "{model["name"]}" SET "{field["name"]}" = "{field["name"]}" / 10000')
                    field['scale'] = 2
            if locked:
                ledger.add_triggers(connection, model['name'])
        connection.execute('UPDATE evlet_schema SET definition = ? WHERE id = 1', (json.dumps(definition, sort_keys=True, separators=(',', ':')),))
        connection.execute('DROP TABLE evlet_money')
        connection.commit()


def test_existing_databases_move_to_six_places_exactly_and_only_once(tmp_path):
    path = tmp_path / 'old.sqlite3'
    app = application(path, Client, Deposit)
    app.initialize()
    with app.database.transaction() as tx:
        first = tx.create(Deposit, dinars='-12.34', dirhams='1234.56', amount='0.05', plain='7')
        empty = tx.create(Deposit)
    app.database.close()
    legacy_database(app)
    assert stored(app, 'deposit', 'dirhams', first.id) == 123456   # hundredths, as 0.4 stored them
    with app.database._connection() as connection:
        # The largest amount 0.4 could store does not fit at 6 places.
        connection.execute('UPDATE deposit SET dirhams = 9223372036854775 WHERE id = ?', (first.id,))
        connection.commit()
    # An instance being served refuses and points at evlet migrate.
    served = application(path, Client, Deposit)
    served.managed_framework = True
    with pytest.raises(SchemaError, match='run evlet migrate'):
        served.initialize()
    # A database with an amount too large for 6 places names it and changes nothing.
    with pytest.raises(SchemaError, match=r'deposit #.*dirhams is too large'):
        application(path, Client, Deposit).initialize()
    with app.database._connection() as connection:
        connection.execute('UPDATE deposit SET dirhams = 123456 WHERE id = ?', (first.id,))
        connection.commit()
    moved = application(path, Client, Deposit)
    moved.initialize()
    with moved.database.transaction() as tx:
        record, blank = tx.get(Deposit, first.id), tx.get(Deposit, empty.id)
    assert (record.dinars, record.dirhams, record.amount, record.plain) == (D('-12.34'), D('1234.56'), D('0.05'), D('7.00'))
    assert blank.dinars is None
    assert stored(moved, 'deposit', 'dinars', first.id) == -12_340_000
    with moved.database._connection() as connection:
        assert connection.execute('SELECT scale FROM evlet_money').fetchone()[0] == MONEY_SCALE
        saved = json.loads(connection.execute('SELECT definition FROM evlet_schema').fetchone()[0])
    assert {field['scale'] for model in saved['models'] for field in model['fields'] if field['type'] == 'currency'} == {MONEY_SCALE}
    # Starting again moves nothing twice.
    again = application(path, Client, Deposit)
    again.initialize()
    assert stored(again, 'deposit', 'dinars', first.id) == -12_340_000


def test_explicit_migrations_move_amounts_before_app_steps_and_keep_their_checksums(tmp_path):
    class Fee(Model):
        permissions = FULL
        amount = Currency(currency='KWD')

    def with_tax():
        class Fee(Model):
            permissions = FULL
            amount = Currency(currency='KWD')
            tax = Currency(currency='KWD')
        return Fee
    FeeWithTax = with_tax()

    first = Migration('0001_fees', CreateModel(Fee))
    # The checksum 0.4 recorded: a Currency field's scale was 2 in the signature.
    signature = json.loads(json.dumps([op.signature() for op in first.operations]))
    for field in signature[0]['fields']:
        if field['type'] == 'currency':
            assert field['scale'] == MONEY_SCALE
            field['scale'] = 2
    import hashlib
    assert first.checksum() == hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    path = tmp_path / 'fees.sqlite3'
    database = Database(path, Registry(Fee), migrations=[first])
    database.migrate([first])
    with database.transaction() as tx:
        fee = tx.create(Fee, amount='1.25')
    database.close()
    with database._connection() as connection:
        connection.execute('UPDATE fee SET amount = amount / 10000')
        definition = json.loads(connection.execute('SELECT definition FROM evlet_schema').fetchone()[0])
        for model in definition['models']:
            for field in model['fields']:
                if field['type'] == 'currency':
                    field['scale'] = 2
        connection.execute('UPDATE evlet_schema SET definition = ?', (json.dumps(definition),))
        connection.execute('DROP TABLE evlet_money')
        connection.commit()
    second = Migration('0002_tax', AddField('fee', 'tax', Currency(currency='KWD'), backfill='0.125'))
    upgraded = Database(path, Registry(FeeWithTax), migrations=[first, second])
    assert upgraded.plan([first, second]).differences == ()
    assert upgraded.migrate([first, second]) == ['0002_tax']
    with upgraded.transaction() as tx:
        row = tx.get(FeeWithTax, fee.id)
    assert (row.amount, row.tax) == (D('1.25'), D('0.125'))
    upgraded.initialize()


def test_append_only_ledgers_are_moved_and_keep_their_triggers(tmp_path):
    from evlet import ledger

    class Entry(Model):
        permissions = {'admin': Rule(('read', 'create'))}
        amount = Currency(currency='AED', required=True)

        class Meta:
            ledger = True
            append_only = True

    path = tmp_path / 'entries.sqlite3'
    app = application(path, Entry)
    app.initialize()
    with app.database.transaction() as tx:
        ids = tx.insert_many(Entry, [{'amount': '10.50'}, {'amount': '-0.01'}])
    legacy_database(app)
    moved = application(path, Entry)
    moved.initialize()
    with moved.database.transaction() as tx:
        assert [tx.get(Entry, item).amount for item in ids] == [D('10.50'), D('-0.01')]
    with moved.database._connection() as connection:
        assert ledger.has_triggers(connection, 'entry')
        with pytest.raises(Exception, match='append'):
            connection.execute('UPDATE entry SET amount = 0')


class Buyer(Model):
    permissions = FULL
    currency = Select(['AED', 'KWD', 'JPY'], default='AED')
    discount = Decimal(scale=2, default='0')


class Order(Model):
    """A finite document saved before amounts moved to 6 places: one of its amounts reads the same Link its currency
    comes through."""
    permissions = FULL

    class Line(Child):
        qty = Decimal(scale=3, default='1')
        rate = Currency(currency='AED', default='0')
        amount = Currency(currency='AED', compute=F('qty') * F('rate'))

    buyer = Link(Buyer)
    reference = Text(default='')
    currency = Select(['AED', 'KWD', 'JPY'], default='AED')
    lines = Table(Line)
    net = Currency(currency='currency', compute=lines.sum('amount'))
    discount = Currency(currency='buyer.currency', compute=F('net') * F('buyer.discount') / D('100'))
    vat = Currency(currency='currency', compute=F('net') * D('0.05'))


# The formula version Evlet 0.4 recorded for every saved Order (its formula_hash before amounts moved to 6 places).
ORDER_HASH_0_4 = 'sha256:a9279561798663a46a1050b6e15d1789225c11eb923be3054ef3cfcbb723902c'


def test_saved_finite_documents_stay_editable_after_the_move_to_six_places(tmp_path):
    # The currency sources add reads (buyer, buyer.currency) that the formula version leaves out, even when a formula
    # reads the same Link: the move changes how amounts are stored, not the formulas.
    assert formula_hash(Registry(Buyer, Order).plan_for(Order)) == ORDER_HASH_0_4
    path = tmp_path / 'orders.sqlite3'
    app = application(path, Buyer, Order)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = login(app)
    status, buyer, _ = admin('POST', '/api/buyer', {'currency': 'KWD', 'discount': '10'})
    assert status == 201, buyer
    status, order, _ = admin('POST', '/api/order', {'buyer': buyer['id'], 'children': {'lines': [{'qty': '2', 'rate': '12.5'}]}})
    assert status == 201, order
    record_id = int(RecordId(order['id']))
    app.database.close()
    legacy_database(app)
    with app.database._connection() as connection:
        changed = connection.execute("UPDATE evlet_calculation_versions SET plan_hash = ? WHERE resource = 'order'", (ORDER_HASH_0_4,))
        assert changed.rowcount == 1
        connection.commit()
    assert stored(app, 'order', 'net', record_id) == 2500
    # evlet migrate: framework step 0007_money_scale moves the amounts; the saved Order is then edited as before.
    moved = application(path, Buyer, Order)
    moved.initialize()
    assert stored(moved, 'order', 'net', record_id) == 25_000_000
    admin = login(moved)
    status, saved, headers = admin('GET', f'/api/order/{order["id"]}')
    assert status == 200 and D(saved['net']) == D('25') and D(saved['discount']) == D('2.5'), saved
    status, saved, headers = admin('PATCH', f'/api/order/{order["id"]}', {'currency': 'KWD', 'reference': 'PO-7'},
                                   headers={b'if-match': headers[b'etag']})
    assert status == 200, saved
    assert (saved['currency'], D(saved['net']), D(saved['vat']), D(saved['discount'])) == ('KWD', D('25'), D('1.25'), D('2.5'))
    # A real formula change is still refused until the record is recalculated.
    with moved.database._connection() as connection:
        connection.execute("UPDATE evlet_calculation_versions SET plan_hash = 'sha256:other' WHERE resource = 'order'")
        connection.commit()
    status, refused, _ = admin('PATCH', f'/api/order/{order["id"]}', {'reference': 'PO-8'}, headers={b'if-match': headers[b'etag']})
    assert status == 409 and refused['error'].startswith('SCHEMA_VERSION_MISMATCH'), refused


def test_money_scale_is_a_no_op_on_a_new_database(tmp_path):
    database = Database(tmp_path / 'new.sqlite3', Registry(Client, Deposit))
    database.initialize()
    with database._connection() as connection:
        assert money_scale(connection, database.registry, None) is None
        assert connection.execute('SELECT scale FROM evlet_money').fetchone()[0] == MONEY_SCALE


def test_an_app_syncs_its_currency_list_and_scales_tolerances_with_the_unit(app):
    from evlet import currency_unit, set_currency_decimals
    assert (currency_unit('AED'), currency_unit('KWD'), currency_unit('JPY')) == (D('0.01'), D('0.001'), D('1'))
    assert save(login(app), {'AED': {'symbol': 'Dh'}})[0] == 200
    saved = set_currency_decimals(app, {'XCL': 4, 'KWD': 3}, actor='finance')
    assert saved['XCL']['decimals'] == 4 and saved['AED']['symbol'] == 'Dh' and 'KWD' not in saved   # KWD 3 is ISO
    with app.database.transaction():
        # Inside a transaction (derive, postings, jobs) the helpers read the site's table.
        assert currency_decimals('XCL') == 4 and currency_unit('XCL') == D('0.0001')
    with app.database.transaction() as tx:
        tx.create(Deposit, currency='XCL', amount='0.0001')
    with pytest.raises(Exception, match='stored XCL amounts have more than 2'):
        set_currency_decimals(app, {'XCL': 2})


def test_alter_field_moves_decimal_money_to_currency_exactly(tmp_path):
    """The documented path for apps that kept money in Decimal(scale=4): AlterField to Currency multiplies each stored
    value by 100, exactly, in the migration transaction."""
    def model(field):
        class Posting(Model):
            permissions = FULL
            currency = Select(['AED', 'KWD'], default='AED')
            amount = field
        return Posting

    Old = model(Decimal(scale=4))
    first = Migration('0001_postings', CreateModel(Old))
    database = Database(tmp_path / 'finance.sqlite3', Registry(Old), migrations=[first])
    database.migrate([first])
    with database.transaction() as tx:
        kwd = tx.create(Old, currency='KWD', amount='-1234.567')
        aed = tx.create(Old, currency='AED', amount='0.05')
    database.close()
    New = model(Currency(currency='currency'))
    second = Migration('0002_money', AlterField('posting', 'amount', Currency(currency='currency')))
    moved = Database(tmp_path / 'finance.sqlite3', Registry(New), migrations=[first, second])
    assert moved.migrate([first, second]) == ['0002_money']
    with moved.transaction() as tx:
        assert (tx.get(New, kwd.id).amount, tx.get(New, aed.id).amount) == (D('-1234.567'), D('0.05'))
    assert stored(type('A', (), {'database': moved})(), 'posting', 'amount', kwd.id) == -1_234_567_000
