"""UX-21: the Playground Invoice has lines (item, quantity, rate, amount) and a calculated total.

Also the helpers other tests use now that the total is calculated: `lines(total)` is the body part that
gives a new invoice that total, and `raw_invoice` is a trusted raw write for tests of storage behaviour.
"""

import sqlite3
import subprocess
import sys

from evlet import Database, User
from examples.playground.models import Customer, Invoice
from support import client


def lines(total, item='Work'):
    """`children` of an invoice body whose total is `total`: one line of quantity 1 at that rate."""
    return {'lines': [{'item': item, 'qty': '1', 'rate': total}]}


def raw_invoice(target, **values):
    """A trusted raw write of an invoice with a given total and no lines, for tests of storage (ids, links, unique
    references, transactions). Invoices are finite-v1 documents, so this is a maintenance write (Session.maintenance),
    which skips the calculations: application code writes invoices through a Context or the HTTP routes."""
    if isinstance(target, Database):
        with target.transaction() as tx:
            return raw_invoice(tx, **values)
    with target.maintenance():
        return target.create(Invoice, **values)


def raw_update(target, record_id, **changes):
    with target.transaction() as tx, tx.maintenance():
        return tx.update(Invoice, record_id, **changes)


def test_the_total_is_the_sum_of_the_lines_in_the_customers_currency(secured):
    editor = client(secured, 'editor')
    customer = editor('POST', '/api/customer', {'customer_name': 'Kuwait Office Fitout', 'currency': 'KWD'})[1]
    status, invoice, _ = editor('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'KOF-1', 'amount_paid': '1.000', 'children': {
        'lines': [{'item': 'Design review', 'qty': '3', 'rate': '125.750'}, {'item': 'Travel', 'rate': '12.5'}]}})
    assert status == 201, invoice
    assert [(row['item'], row['qty'], row['rate'], row['amount']) for row in invoice['children']['lines']] == [
        ('Design review', '3.00', '125.75', '377.25'), ('Travel', '1.00', '12.50', '12.50')]
    assert invoice['total'] == '389.75' and invoice['payment_status'] == 'partly_paid'
    row = invoice['children']['lines'][1]
    url = f"/api/invoice/{invoice['id']}"
    status, changed, _ = editor('PATCH', url, {'children': {'lines': [
        {'id': invoice['children']['lines'][0]['id'], 'item': 'Design review', 'qty': '1', 'rate': '1.000'}, {'id': row['id'], 'item': row['item'], 'qty': row['qty'], 'rate': '0.000'}]}},
        headers={b'if-match': editor('GET', url)[2][b'etag']})
    assert status == 200, changed
    assert changed['total'] == '1.00' and changed['payment_status'] == 'paid'


def test_a_caller_cannot_type_the_total_and_an_invoice_needs_a_line(secured):
    editor = client(secured, 'editor')
    customer = editor('POST', '/api/customer', {'customer_name': 'Acme'})[1]
    status, body, _ = editor('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'R-1', 'total': '5.00'})
    assert status == 422 and 'COMPUTED_FIELD_WRITE' in body['fields']['total']
    status, body, _ = editor('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'R-2'})
    assert status == 422 and 'lines' in body['fields']
    assert editor('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'R-3', 'children': lines('5.00')})[1]['total'] == '5.00'


def test_the_schema_shows_the_lines_table_and_a_read_only_total(secured):
    schema = client(secured, 'editor')('GET', '/api/schema')[1]
    invoice = next(model for model in schema['models'] if model['name'] == 'invoice') if isinstance(schema.get('models'), list) else schema['models']['invoice']
    text = repr(invoice)
    assert 'lines' in text and 'amount' in text and 'qty' in text


def test_the_demo_data_has_invoices_with_lines(secured):
    from examples.playground.app import DEMO_INVOICES, seed_demo
    ids = seed_demo(secured)
    assert set(ids) == {reference for _, reference, *_ in DEMO_INVOICES}
    admin = client(secured)
    listed = admin('GET', '/api/invoice?limit=50')[1]['items']
    assert {row['reference']: row['total'] for row in listed} == {
        'PO-1001': '1500.00', 'ZMS-2207': '4620.00', 'ZMS-2231': '3600.00', 'GFP-88': '3000.00', 'KOF-14': '377.25'}
    assert {row['reference']: row['payment_status'] for row in listed} == {
        'PO-1001': 'paid', 'ZMS-2207': 'unpaid', 'ZMS-2231': 'partly_paid', 'GFP-88': 'unpaid', 'KOF-14': 'unpaid'}
    detail = admin('GET', f"/api/invoice/{ids['ZMS-2207']}")[1]
    assert [row['amount'] for row in detail['children']['lines']] == ['4200.00', '420.00']


def test_an_upgraded_playground_gives_each_old_invoice_one_line_for_its_total(tmp_path, monkeypatch):
    from examples.playground.migrate import legacy_registry
    from examples.playground.app import create_app
    path = tmp_path / 'playground.sqlite3'
    old_registry = legacy_registry()
    old = Database(path, old_registry)
    old.initialize()
    customer = old.create(old_registry.models['customer'], name='Kept')
    invoice = old.create(old_registry.models['invoice'], customer=customer.id, reference='OLD-1', total='123.45')
    old.close()
    monkeypatch.setenv('EVLET_DATABASE', str(path))
    result = subprocess.run([sys.executable, '-W', 'ignore', '-m', 'examples.playground.migrate'], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as connection:
        rows = connection.execute('SELECT evlet_owner, item, qty, rate, amount FROM invoice_lines').fetchall()
    assert rows == [(invoice.id, 'Invoiced amount', 100, 123450000, 123450000)]
    app = create_app()
    app.initialize()
    kept = app.database.get(Invoice, invoice.id)
    assert kept.to_dict()['total'] == '123.45' and app.database.get(Customer, customer.id).customer_name == 'Kept'
    app.database.close()


def test_quick_invoice_opens_a_new_invoice_with_its_lines_and_the_discount_as_a_line(secured):
    from examples.playground.items import Item
    admin = client(secured)
    customer = admin('POST', '/api/customer', {'customer_name': 'Acme'})[1]
    with secured.business.context(User(0, 'admin', 'admin')) as context:
        item = context.create(Item, item_code='CHAIR', item_name='Office chair', item_group='finished good', stock_uom='nos')['id']
    status, opened, _ = admin('POST', '/api/_services/playground.quick_invoice', {})
    assert status == 200, opened
    ticket = opened['prompt']['ticket']
    values = {'customer': customer['id'], 'reference': 'Q-1', 'discount_percent': '10',
              'children': {'lines': [{'_key': 'a', 'item': item, 'uom': 'nos', 'qty': '2', 'rate': '50.00'}]}}
    status, result, _ = admin('POST', f'/api/_prompts/{ticket}/submit', {'values': values})
    assert status == 200, result
    new = result['new'] if 'new' in result else result['_outcome']['new']
    assert new['model'] == 'invoice'
    assert [(row['item'], row['rate']) for row in new['values']['children']['lines']] == [('Office chair', '50.00'), ('Discount 10.00%', '-10.00')]
    status, created, _ = admin('POST', '/api/invoice', {**{key: value for key, value in new['values'].items() if key != 'children'},
                                                       'children': {'lines': [{key: row[key] for key in ('item', 'qty', 'rate')}
                                                                              for row in new['values']['children']['lines']]}})
    assert status == 201, created
    assert created['total'] == '90.00'
