"""CSV import (Meta.importable): map columns to fields, a dry run that writes nothing, then one all-or-nothing
transaction of creates through the same pipeline as POST /api/<model>, on real SQLite and over HTTP."""

import csv
import io

import pytest

from evlet import Application, Child, Currency, Database, Date, Integer, Link, Model, Registry, Rule, Table, Text, ValidationError, validate
from evlet.auth import LocalAuth
from evlet.calc import round
from finite_fixtures import FiniteMeta
from support import PASSWORD, client, http
from test_rules_types import ALL

FULL = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'))}


class Customer(Model):
    class Meta:
        importable = True
        title_field = 'customer_name'
    permissions = {**FULL, 'editor': Rule(('read', 'create', 'update', 'delete'), write_fields=('customer_name', 'city'))}
    customer_name = Text(required=True, unique=True)
    city = Text(default='')
    credit = Integer(default=0)


class Line(Child):
    item = Text(required=True)
    qty = Integer(default=1, minimum=1)
    rate = Currency(default=0)
    amount = Currency(compute=round(qty * rate, 2))


class Invoice(Model):
    class Meta(FiniteMeta):
        importable = True
    permissions = FULL
    customer = Link(Customer, required=True)
    posted = Date(required=True)
    reference = Text(default='')
    lines = Table(Line)
    total = Currency(compute=lines.sum('amount'))

    @validate
    def has_lines(self):
        if not self.lines:
            raise ValidationError({'lines': 'Add at least one line'})


class Note(Model):
    """Not importable: no Meta.importable."""
    permissions = FULL
    text = Text(default='')


@pytest.fixture
def shop(tmp_path):
    database = Database(tmp_path / 'import.sqlite3', Registry(Customer, Invoice, Note))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    for name in ('editor', 'viewer'):
        assert admin('POST', '/api/auth/users', {'username': name, 'password': PASSWORD, 'role': name})[0] == 201
    return app, admin


def text(*rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


def count(app, model):
    with app.database.transaction() as tx:
        return tx.count(model)


CUSTOMERS = {'Customer': 'customer_name', 'Town': 'city', 'Limit': 'credit'}


def test_the_import_describes_what_a_csv_can_fill(shop):
    app, admin = shop
    status, page, _ = admin('GET', '/api/invoice/import')
    assert status == 200, page
    assert [(column['path'], column.get('table')) for column in page['columns']] == [
        ('customer', None), ('posted', None), ('reference', None),
        ('children.lines.item', 'lines'), ('children.lines.qty', 'lines'), ('children.lines.rate', 'lines')]
    assert page['columns'][0]['target'] == 'customer' and page['saved'] is None
    schema = {item['name']: item for item in admin('GET', '/api/schema')[1]['models']}
    assert schema['invoice']['importable'] is True and 'importable' not in schema['note']
    assert admin('GET', '/api/note/import')[0] == 404
    # A role that cannot create sees no Import and is refused.
    viewer = client(app, 'viewer')
    assert 'importable' not in {item['name']: item for item in viewer('GET', '/api/schema')[1]['models']}['customer']
    assert viewer('GET', '/api/customer/import')[0] == 403
    # The editor may write name and city only, so credit is not offered.
    assert [column['path'] for column in client(app, 'editor')('GET', '/api/customer/import')[1]['columns']] == ['customer_name', 'city']


def test_a_dry_run_checks_every_row_and_writes_nothing(shop):
    app, admin = shop
    body = {'csv': text(['Customer', 'Town', 'Limit'], ['Acme', 'Dubai', '1,000'], ['Globex', '', 'lots'], ['', 'Doha', '5']),
            'mapping': CUSTOMERS}
    status, result, _ = admin('POST', '/api/customer/import/check', body)
    assert status == 200, result
    assert (result['dry_run'], result['records'], result['valid'], result['created']) == (True, 3, 1, 0)
    assert [(item['line'], item['column'], item['message']) for item in result['errors']] == [
        (3, 'Limit', 'Expected a whole number'), (4, 'Customer', 'This field is required')]
    assert list(csv.reader(io.StringIO(result['error_csv']))) == [
        ['Customer', 'Town', 'Limit', 'Error'], ['Globex', '', 'lots', 'Limit: Expected a whole number'],
        ['', 'Doha', '5', 'Customer: This field is required']]
    assert count(app, Customer) == 0
    # A clean file: the dry run passes and still writes nothing.
    body['csv'] = text(['Customer', 'Town', 'Limit'], ['Acme', 'Dubai', '1,000'])
    status, result, _ = admin('POST', '/api/customer/import/check', body)
    assert status == 200 and result['errors'] == [] and result['valid'] == 1 and count(app, Customer) == 0


def test_an_import_is_all_or_nothing(shop):
    app, admin = shop
    good = text(['Customer', 'Town', 'Limit'], ['Acme', 'Dubai', '1000'], ['Globex', 'Doha', '5'])
    status, result, _ = admin('POST', '/api/customer/import', {'csv': good, 'mapping': CUSTOMERS, 'save_mapping': True})
    assert status == 201, result
    assert result['created'] == 2 and len(result['ids']) == 2 and count(app, Customer) == 2
    assert admin('GET', '/api/customer/import')[1]['saved'] == {'mapping': CUSTOMERS, 'key': None}
    # A duplicate name fails its row; nothing in the file is created.
    again = text(['Customer', 'Town'], ['Initech', 'Riyadh'], ['Acme', 'Dubai'])
    status, result, _ = admin('POST', '/api/customer/import', {'csv': again, 'mapping': {'Customer': 'customer_name', 'Town': 'city'}})
    assert status == 200 and result['created'] == 0 and [item['line'] for item in result['errors']] == [3]
    assert count(app, Customer) == 2
    # all_or_nothing=false keeps the records that pass.
    status, result, _ = admin('POST', '/api/customer/import', {'csv': again, 'mapping': {'Customer': 'customer_name', 'Town': 'city'}, 'all_or_nothing': False})
    assert status == 201 and result['created'] == 1 and count(app, Customer) == 3


def test_field_write_rules_apply(shop):
    app, _ = shop
    editor = client(app, 'editor')
    status, result, _ = editor('POST', '/api/customer/import', {'csv': text(['Customer', 'Limit'], ['Acme', '5']), 'mapping': {'Customer': 'customer_name', 'Limit': 'credit'}})
    assert status == 422 and result['fields'] == {'mapping': 'credit is not a field you can import into'}
    status, result, _ = editor('POST', '/api/customer/import', {'csv': text(['Customer'], ['Acme']), 'mapping': {'Customer': 'customer_name'}})
    assert status == 201 and result['created'] == 1
    assert client(app, 'viewer')('POST', '/api/customer/import', {'csv': text(['Customer'], ['B']), 'mapping': {'Customer': 'customer_name'}})[0] == 403


def test_a_key_column_groups_rows_into_one_document_with_lines(shop):
    app, admin = shop
    admin('POST', '/api/customer', {'customer_name': 'Acme'})
    rows = text(['Invoice', 'Customer', 'Date', 'Item', 'Qty', 'Rate'],
                ['INV-1', 'Acme', '31-03-2026', 'Widget', '2', '5.00'],
                ['INV-1', '', '', 'Gadget', '1', '7.50'],
                ['', '', '', 'Bolt', '10', '0.10'],
                ['INV-2', 'Acme', '2026-04-01', 'Widget', '1', '5.00'],
                ['INV-3', 'Nobody', '2026-04-02', '', '', ''])
    mapping = {'Invoice': 'reference', 'Customer': 'customer', 'Date': 'posted', 'Item': 'children.lines.item',
               'Qty': 'children.lines.qty', 'Rate': 'children.lines.rate'}
    status, result, _ = admin('POST', '/api/invoice/import/check', {'csv': rows, 'mapping': mapping, 'key': 'Invoice'})
    assert status == 200, result
    assert result['records'] == 3 and [(item['line'], item['column'], item['message']) for item in result['errors']] == [
        (6, 'Customer', 'No Customer is called Nobody')]
    status, result, _ = admin('POST', '/api/invoice/import', {'csv': rows, 'mapping': mapping, 'key': 'Invoice', 'all_or_nothing': False})
    assert status == 201, result
    assert result['created'] == 2
    invoices = admin('GET', '/api/invoice')[1]['items']
    first = admin('GET', f"/api/invoice/{next(item['id'] for item in invoices if item['reference'] == 'INV-1')}")[1]
    assert first['posted'] == '2026-03-31' and first['total'] == '18.50'
    assert [(row['item'], row['qty']) for row in first['children']['lines']] == [('Widget', 2), ('Gadget', 1), ('Bolt', 10)]
    # A document's own validation runs: an invoice without lines is refused.
    status, result, _ = admin('POST', '/api/invoice/import', {'csv': text(['Customer', 'Date'], ['Acme', '2026-05-01']),
                                                              'mapping': {'Customer': 'customer', 'Date': 'posted'}})
    assert status == 200 and result['created'] == 0 and result['errors'][0]['column'] == 'lines'


def test_bad_files_and_mappings_are_refused(shop):
    _, admin = shop
    for body, field in (({'csv': '', 'mapping': {}}, 'csv'), ({'csv': 'a,a\n1,2', 'mapping': {}}, 'csv'),
                        ({'csv': 'a\n1', 'mapping': {'b': 'customer_name'}}, 'mapping'), ({'csv': 'a\n1', 'mapping': {'a': 'nope'}}, 'mapping'),
                        ({'csv': 'a,b\n1,2', 'mapping': {'a': 'customer_name', 'b': 'customer_name'}}, 'mapping'),
                        ({'csv': 'a\n1', 'mapping': {'a': 'customer_name'}, 'key': 'z'}, 'key'), ({'csv': 'a\n', 'mapping': {'a': 'customer_name'}}, 'csv'),
                        ({'csv': 'a\n1', 'mapping': {'a': 'customer_name'}, 'extra': 1}, '_form')):
        status, result, _ = admin('POST', '/api/customer/import/check', body)
        assert status == 422 and field in result['fields'], (body, result)


def test_meta_importable_is_a_boolean(tmp_path):
    from evlet.imports import importable
    wrong = type('Wrong', (Model,), {'Meta': type('Meta', (), {'importable': 'yes'}), 'permissions': FULL, 'code': Text()})
    database = Database(tmp_path / 'wrong.sqlite3', Registry(wrong))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    with pytest.raises(ValueError, match='importable is True or False'):
        importable(app.business, wrong)
