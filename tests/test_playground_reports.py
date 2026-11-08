"""The Playground's reports run through the report routes with the caller's permissions."""

import pytest

import test_business
import test_postgres
from test_business import client, hashed  # noqa: F401
from test_postgres import pg_dsn  # noqa: F401 - fixture


@pytest.fixture(params=['sqlite', 'postgresql'])
def secured(request, tmp_path, monkeypatch, hashed):
    """The Playground on SQLite, and on PostgreSQL when CI provides one."""
    if request.param == 'sqlite':
        return test_business.secured.__wrapped__(tmp_path, monkeypatch, hashed)
    return test_postgres.secured.__wrapped__(request.getfixturevalue('pg_dsn'), hashed)


def seed(api):
    owners = {}
    for title, currency in (('Al Sahra Trading', 'AED'), ('Gulf Supplies', 'AED'), ('Boston Imports', 'USD')):
        status, body, _ = api('POST', '/api/customer', {'customer_name': title, 'currency': currency})
        assert status == 201, body
        owners[title] = body['id']
    for owner, reference, total, status, posted in (('Al Sahra Trading', 'PO-1', '100.00', 'sent', '2026-10-01'),
                                                    ('Al Sahra Trading', 'PO-2', '250.50', 'sent', '2026-10-15'),
                                                    ('Gulf Supplies', 'PO-3', '75.00', 'draft', '2026-09-30'),
                                                    ('Boston Imports', 'PO-4', '999.00', 'sent', '2026-10-02')):
        assert api('POST', '/api/invoice', {'customer': owners[owner], 'reference': reference, 'total': total,
                                            'status': status, 'posting_date': posted})[0] == 201
    return owners


def test_invoice_register_filters_sorts_and_totals_every_row(secured):
    editor = client(secured, 'editor')
    owners = seed(editor)
    catalog = {item['name']: item for item in editor('GET', '/api/_reports')[1]['reports']}
    assert set(catalog) == {'InvoiceRegister', 'InvoicesByCustomer', 'InvoicesByStatus', 'SalesByCustomer', 'LedgerBalances'} and catalog['InvoiceRegister']['model'] == 'invoice'
    status, result, _ = editor('POST', '/api/_reports/InvoiceRegister', {'filters': {'from_date': '2026-10-01'}, 'limit': 1, 'sort': '-total'})
    assert status == 200, result
    # AED invoices only, posted from 1 October: two rows, the larger first, totalled over both.
    assert result['total'] == 2 and result['rows'][0]['reference'] == 'PO-2'
    assert result['totals'] == {'scope': 'all', 'values': {'total': '350.50'}}
    one = editor('POST', '/api/_reports/InvoiceRegister', {'filters': {'customer': owners['Gulf Supplies']}})[1]
    assert [row['reference'] for row in one['rows']] == ['PO-3']
    status, content, _ = editor('POST', '/api/_reports/InvoiceRegister/export.csv', {'filters': {}, 'sort': 'posting_date'})
    assert status == 200 and content.decode('utf-8').splitlines()[0] == '﻿Invoice,Customer,Customer reference,Posted,Status,Total (AED)'


def test_sales_by_customer_groups_and_is_for_editors(secured):
    editor = client(secured, 'editor')
    owners = seed(editor)
    result = editor('POST', '/api/_reports/SalesByCustomer', {'filters': {}})[1]
    assert result['rows'] == [{'customer': owners['Al Sahra Trading'], 'invoices': 2, 'total': '350.50'}]
    assert result['totals']['values'] == {'invoices': '2', 'total': '350.50'}
    everything = editor('POST', '/api/_reports/SalesByCustomer', {'filters': {'status': None}, 'sort': 'total'})[1]
    assert [row['total'] for row in everything['rows']] == ['75.00', '350.50']
    viewer = client(secured, 'viewer')
    assert [item['name'] for item in viewer('GET', '/api/_reports')[1]['reports']] == ['InvoiceRegister', 'InvoicesByCustomer']
    assert viewer('POST', '/api/_reports/SalesByCustomer', {'filters': {}})[0] == 403


def test_reports_run_before_there_is_data(secured):
    editor = client(secured, 'editor')
    for name in ('InvoiceRegister', 'InvoicesByCustomer', 'SalesByCustomer', 'LedgerBalances'):
        status, result, _ = editor('POST', '/api/_reports/' + name, {'filters': {}})
        assert status == 200 and result['rows'] == [] and result['total'] == 0, result


def test_invoices_by_customer_groups_with_subtotals_over_every_invoice(secured):
    editor = client(secured, 'editor')
    owners = seed(editor)
    status, result, _ = editor('POST', '/api/_reports/InvoicesByCustomer', {'filters': {}, 'limit': 2, 'sort': '-total'})
    assert status == 200, result
    assert [(row['customer'], row['total']) for row in result['rows']] == [(owners['Al Sahra Trading'], '250.50'), (owners['Al Sahra Trading'], '100.00')]
    assert result['groups'] == {'scope': 'all', 'items': [{'value': owners['Al Sahra Trading'], 'count': 2, 'totals': {'total': '350.50'}}]}
    assert result['totals']['values'] == {'total': '425.50'}


def test_ledger_balances_read_the_read_only_ledger(secured):
    editor = client(secured, 'editor')
    owners = seed(editor)
    for invoice in editor('GET', '/api/invoice')[1]['items']:
        if invoice['reference'] in ('PO-1', 'PO-2', 'PO-4'):
            url = f"/api/invoice/{invoice['id']}"
            assert editor('POST', f'{url}/actions/post', {}, headers={b'if-match': editor('GET', url)[2][b'etag']})[0] == 200
    result = editor('POST', '/api/_reports/LedgerBalances', {'filters': {}})[1]
    # AED customers only: Boston Imports bills in another currency.
    assert result['rows'] == [{'customer': owners['Al Sahra Trading'], 'entries': 2, 'amount': '350.50'}]
    assert editor('POST', '/api/_reports/LedgerBalances', {'filters': {'to_date': '2026-10-10'}})[1]['totals']['values'] == {'entries': '1', 'amount': '100.00'}
    assert client(secured, 'viewer')('POST', '/api/_reports/LedgerBalances', {'filters': {}})[0] == 403
