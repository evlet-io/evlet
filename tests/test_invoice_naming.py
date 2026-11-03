"""The Playground Invoice is named by its naming series: INV-<year>-00001 from the posting date."""

import re
from concurrent.futures import ThreadPoolExecutor

from evlet import Database
from examples.playground.models import Invoice
from test_business import client, hashed, secured  # noqa: F401


def customer(api):
    status, body, _ = api('POST', '/api/customer', {'name': 'Al Sahra Trading'})
    assert status == 201, body
    return body['id']


def invoice(api, owner, reference, **values):
    status, body, _ = api('POST', '/api/invoice', {'customer': owner, 'reference': reference, 'total': '100.00', **values})
    assert status == 201, body
    return body


def test_new_invoices_are_numbered_from_the_posting_date_year(secured):
    editor = client(secured, 'editor')
    owner = customer(editor)
    first = invoice(editor, owner, 'PO-1', posting_date='2026-03-01')
    second = invoice(editor, owner, 'PO-2', posting_date='2026-12-31')
    # A new year starts a new counter.
    january = invoice(editor, owner, 'PO-3', posting_date='2027-01-01')
    assert (first['number'], second['number'], january['number']) == ('INV-2026-00001', 'INV-2026-00002', 'INV-2027-00001')
    # Without a posting date the year is the creation day's, in the instance timezone.
    assert re.fullmatch(r'INV-\d{4}-\d{5}', invoice(editor, owner, 'PO-4')['number'])
    listed = editor('GET', '/api/invoice?limit=10&offset=0&q=INV-2026-00002')[1]['items']
    assert [item['reference'] for item in listed] == ['PO-2']


def test_the_number_cannot_be_sent_or_changed(secured):
    editor = client(secured, 'editor')
    owner = customer(editor)
    assert editor('POST', '/api/invoice', {'customer': owner, 'reference': 'PO-1', 'total': '10.00', 'number': 'MINE'})[0] == 403
    created = invoice(editor, owner, 'PO-1', posting_date='2026-03-01')
    url = f"/api/invoice/{created['id']}"
    assert editor('PATCH', url, {'number': 'INV-2026-09999'})[0] == 403
    status, edited, _ = editor('PATCH', url, {'total': '250.00', 'posting_date': '2027-02-01'})
    assert status == 200, edited
    # Editing the invoice, even into another year, keeps its number.
    assert (edited['total'], edited['number']) == ('250.00', 'INV-2026-00001')
    assert editor('GET', url)[1]['number'] == 'INV-2026-00001'


def test_permissions_and_the_schema(secured):
    viewer = client(secured, 'viewer')
    assert viewer('POST', '/api/invoice', {'customer': 1, 'reference': 'PO-1', 'total': '10.00'})[0] == 403
    for role in ('admin', 'editor', 'viewer'):
        model = next(item for item in client(secured, role)('GET', '/api/schema')[1]['models'] if item['name'] == 'invoice')
        field = next(item for item in model['fields'] if item['name'] == 'number')
        assert model['naming'] == {'field': 'number'} and model['title_field'] == 'number'
        assert (field['read_only'], field['required'], field['placeholder']) == (True, False, 'Assigned on save')


def test_concurrent_creates_get_distinct_numbers(secured):
    editor = client(secured, 'editor')
    owner = customer(editor)
    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(lambda index: invoice(editor, owner, f'PO-{index}', posting_date='2026-05-01')['number'], range(24)))
    assert sorted(numbers) == [f'INV-2026-{index:05}' for index in range(1, 25)]


def test_an_upgraded_playground_keeps_old_invoices_and_numbers_new_ones(tmp_path):
    from examples.playground.migrate import legacy_registry, migrations
    from examples.playground.models import registry
    path = tmp_path / 'playground.sqlite3'
    old_registry = legacy_registry()
    old = Database(path, old_registry)
    old.initialize()
    owner = old.create(old_registry.models['customer'], name='Preserved')
    kept = old.create(old_registry.models['invoice'], customer=owner.id, reference='OLD-1', total='10.00').id
    old.close()
    current = Database(path, registry, migrations=migrations)
    assert '0006_invoice_number' in current.migrate(migrations)
    assert current.get(Invoice, kept).number is None
    created = current.create(Invoice, customer=owner.id, reference='NEW-1', total='20.00', posting_date='2026-06-01')
    assert created.number == 'INV-2026-00001'
