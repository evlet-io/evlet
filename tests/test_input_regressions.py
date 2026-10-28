import json
from urllib.parse import urlencode

import pytest

from examples.playground.models import Customer, Invoice
from test_api import request


@pytest.mark.parametrize("amount", ["1e999999999", "-1e999999999"])
def test_extreme_currency_returns_field_validation(application, database, amount):
    customer = database.create(Customer, name="Currency check")
    status, payload, headers = request(application, "POST", "/api/invoice", {
        "customer": customer.id, "reference": "EXTREME", "total": amount,
    })
    assert status == 422
    assert "total" in payload["fields"]
    assert headers[b"content-type"].startswith(b"application/json")
    assert database.list(Invoice) == []
    assert request(application, "GET", "/health")[:2] == (200, {"status": "ok"})


@pytest.mark.parametrize("key", ["\ud800", "\udfff", "invalid\ud800name"])
def test_invalid_unicode_key_returns_json_validation(application, database, key):
    status, payload, headers = request(application, "POST", "/api/customer", {
        "name": "Unicode check", key: 1,
    })
    assert status == 422
    assert payload["fields"][key] == "Unknown field"
    assert headers[b"content-type"].startswith(b"application/json")
    assert database.list(Customer) == []
    assert request(application, "GET", "/health")[:2] == (200, {"status": "ok"})


def test_valid_unicode_still_round_trips(application):
    name = "مقهى دبي ☕"
    status, customer, _ = request(application, "POST", "/api/customer", {"name": name})
    assert status == 201 and customer["name"] == name
    assert request(application, "GET", f'/api/customer/{customer["id"]}')[1] == customer


@pytest.mark.parametrize('field', ['total', 'tax_rate'])
@pytest.mark.parametrize('amount', ['1e999999999', '-1e999999999'])
def test_extreme_decimals_in_create_update_and_query(application, database, field, amount):
    customer = database.create(Customer, name='Decimal check')
    values = {'customer': customer.id, 'reference': 'VALID', 'total': '12.50'}
    status, payload, _ = request(application, 'POST', '/api/invoice', {**values, field: amount})
    assert status == 422 and field in payload['fields']
    assert database.list(Invoice) == []
    invoice = database.create(Invoice, **values)
    status, payload, _ = request(application, 'PATCH', f'/api/invoice/{invoice.id}', {field: amount})
    assert status == 422 and field in payload['fields']
    assert database.get(Invoice, invoice.id).to_dict() == invoice.to_dict()
    query = urlencode({'filters': json.dumps({field + '__gte': amount})})
    status, payload, _ = request(application, 'GET', '/api/invoice?' + query)
    assert status == 422 and 'error' in payload
    assert request(application, 'GET', '/health')[0] == 200


def test_invalid_unicode_update_does_not_change_record(application, database):
    customer = database.create(Customer, name='Keep this name')
    key = '\ud800'
    status, payload, headers = request(application, 'PATCH', f'/api/customer/{customer.id}', {
        'name': 'Changed', key: 1,
    })
    assert status == 422 and payload['fields'][key] == 'Unknown field'
    assert headers[b'content-type'].startswith(b'application/json')
    assert database.get(Customer, customer.id).name == 'Keep this name'
