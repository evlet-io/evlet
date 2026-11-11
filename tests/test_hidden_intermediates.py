"""Hidden unstored intermediates and fields kept out of default columns, through the HTTP routes on real SQLite."""

import json
from pathlib import Path

import pytest

from evlet import Child, Currency, Decimal, Model, Rule, Table, Text
from evlet.auth import password_hash
from evlet.calc import D, F, coalesce, parent
from test_business import PASSWORD, client
from test_nested_documents import ALL, application

FIXTURE = Path(__file__).resolve().parents[1] / 'frontend/src/evlet/tests/intermediate-fixture.json'


class Item(Child):
    qty = Decimal(scale=2, default='1')
    price = Decimal(scale=2, default='0')
    code = Text(compute=parent.code, max_length=3, column=False)
    net = Currency(currency='code', compute=F('qty') * F('price'))
    # Read by the formulas below, never shown: the tax before the bill rounds it once.
    tax_exact = Decimal(scale=None, stored=False, hidden=True, compute=F('net') * D('0.05'))
    gross = Decimal(scale=2, compute=F('net') + F('tax_exact'))
    note = Text(default='')


class Bill(Model):
    permissions = {**ALL, 'viewer': Rule(('read',), read_fields=('title', 'code', 'tax_total'))}
    title = Text(default='Bill')
    code = Text(max_length=3, default='KWD', column=False)
    items = Table(Item)
    tax_total = Currency(currency='code', compute=items.sum('tax_exact'))
    net_total = Currency(currency='code', compute=items.sum('net'))


def bill_schema(call):
    return next(model for model in call('GET', '/api/schema')[1]['models'] if model['name'] == 'bill')


def fixture():
    """What the browser test replays: the bill and item schema entries an admin gets."""
    import tempfile
    app = application(Path(tempfile.mkdtemp()), Bill)
    models = client(app)('GET', '/api/schema')[1]['models']
    keep = ('name', 'label', 'plural', 'title_field', 'list_fields', 'fields', 'children', 'calculation')
    fields = ('name', 'type', 'label', 'required', 'read_only', 'compute', 'default_kind', 'default', 'scale', 'currency_source', 'column', 'hidden')
    return {'models': [{key: [{name: field[name] for name in fields if name in field} for field in model[key]] if key == 'fields' else model[key]
                        for key in keep if key in model} for model in models]}


def test_hidden_intermediates_ship_in_the_plan_so_every_formula_on_the_row_runs_locally(tmp_path):
    app = application(tmp_path, Bill)
    admin = client(app)
    entry = bill_schema(admin)
    calculation = entry['calculation']
    # Everything is local, and the plan carries the intermediate's formula without naming it as a result.
    assert set(calculation['capabilities'].values()) == {'local_universal'} and 'plan' in calculation
    assert 'items[*].tax_exact' not in calculation['capabilities']
    assert {'items[*].gross', 'tax_total', 'items[*].net'} <= set(calculation['capabilities'])
    item_entry = next(value for value in calculation['plan']['tables'].values() if value['path'] == ['items'])
    assert 'tax_exact' in item_entry['fields']
    from evlet.calc import Document
    snapshot = Document(calculation['plan'], {'children': {'items': [{'qty': '2', 'price': '10'}]}}).snapshot()
    assert snapshot['tax_total'] == '1.00' and snapshot['children']['items'][0]['gross'] == '21.00'
    # The intermediate stays out of the schema's fields and every record response.
    item_model = next(model for model in admin('GET', '/api/schema')[1]['models'] if model['name'] == entry['children'][0]['model'])
    assert 'tax_exact' not in {field['name'] for field in item_model['fields']}
    status, created, _ = admin('POST', '/api/bill', {'children': {'items': [{'_key': 'a', 'qty': '2', 'price': '10'}]}})
    assert status == 201, created
    row = created['children']['items'][0]
    assert 'tax_exact' not in row and row['gross'] == '21.00' and created['tax_total'] == '1.00'
    assert 'tax_exact' not in json.dumps(admin('GET', f"/api/bill/{created['id']}")[1])


def test_writing_a_hidden_intermediate_or_a_formula_reading_it_reports_the_specific_error(tmp_path):
    app = application(tmp_path, Bill)
    admin = client(app)
    status, created, _ = admin('POST', '/api/bill', {'children': {'items': [{'_key': 'a', 'qty': '2', 'price': '10'}]}})
    row_id = created['children']['items'][0]['id']
    status, body, _ = admin('POST', '/api/bill', {'children': {'items': [{'_key': 'a', 'qty': '2', 'tax_exact': '1'}]}})
    assert status == 422 and body['fields'] == {'items.tax_exact': 'COMPUTED_FIELD_WRITE: this value is calculated'}
    status, body, _ = admin('POST', '/api/bill', {'tax_total': '1', 'children': {'items': []}})
    assert status == 422 and body['fields'] == {'tax_total': 'COMPUTED_FIELD_WRITE: this value is calculated'}
    status, body, _ = admin('POST', '/api/_draft/bill', {'id': created['id'], 'values': {
        'children': {'items': {'update': [{'id': row_id, 'tax_exact': '3'}]}}}})
    assert status == 422 and body['fields'] == {'items.tax_exact': 'COMPUTED_FIELD_WRITE: this value is calculated'}
    # A preview shows the formulas that read it, never the intermediate itself.
    status, body, _ = admin('POST', '/api/_draft/bill', {'id': created['id'], 'values': {
        'children': {'items': {'update': [{'id': row_id, 'qty': '3'}]}}}})
    assert status == 200 and body['values']['tax_total'] == '1.50' and body['values']['children']['items'][0]['gross'] == '31.50'
    assert 'tax_exact' not in json.dumps(body)


def test_a_role_that_cannot_read_every_result_still_gets_no_plan(tmp_path):
    app = application(tmp_path, Bill)
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('viewer',?,'viewer')", (password_hash(PASSWORD),))
    clerk = client(app, 'viewer')
    calculation = bill_schema(clerk)['calculation']
    # A viewer cannot read net_total: no plan is shipped, and the intermediate is never named.
    assert 'plan' not in calculation and 'tax_exact' not in json.dumps(calculation)


def test_column_false_is_display_only_metadata(tmp_path):
    app = application(tmp_path, Bill)
    admin = client(app)
    fields = {field['name']: field for field in bill_schema(admin)['fields']}
    assert fields['code']['column'] is False and 'column' not in fields['title']
    assert 'code' not in bill_schema(admin)['list_fields']
    # The currency source stays readable, so amounts still show in its currency.
    assert fields['net_total']['currency_source'] == {'field': 'code'} and fields['code']['hidden'] is False
    status, created, _ = admin('POST', '/api/bill', {'children': {'items': [{'_key': 'a', 'qty': '1', 'price': '1.5'}]}})
    assert status == 201 and created['code'] == 'KWD' and created['children']['items'][0]['code'] == 'KWD'
    assert created['net_total'] == '1.50'
    with pytest.raises(ValueError, match='column must be True or False'):
        Text(column='no')


def test_browser_fixture_matches_the_schema():
    assert json.loads(FIXTURE.read_text()) == json.loads(json.dumps(fixture())), 'Regenerate it with tests/test_hidden_intermediates.py'


if __name__ == '__main__':
    FIXTURE.write_text(json.dumps(fixture(), indent=1, sort_keys=True) + '\n')
