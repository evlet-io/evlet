import json
from urllib.parse import urlencode

import pytest

from evlet import Application, Currency, Database, Decimal, Link, Model, Registry, Text
from examples.playground.models import Customer, Invoice
from test_api import request


@pytest.mark.parametrize("amount", ["1e999999999", "-1e999999999"])
def test_extreme_currency_returns_field_validation(application, database, amount):
    customer = database.create(Customer, customer_name="Currency check")
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
        "customer_name": "Unicode check", key: 1,
    })
    assert status == 422
    assert payload["fields"][key] == "Unknown field"
    assert headers[b"content-type"].startswith(b"application/json")
    assert database.list(Customer) == []
    assert request(application, "GET", "/health")[:2] == (200, {"status": "ok"})


def test_valid_unicode_still_round_trips(application):
    name = "مقهى دبي ☕"
    status, customer, _ = request(application, "POST", "/api/customer", {"customer_name": name})
    assert status == 201 and customer["customer_name"] == name and customer["title"] == name
    assert request(application, "GET", f'/api/customer/{customer["id"]}')[1] == customer


class Charge(Model):
    """A Currency and a Decimal on a plain model: the Playground Invoice's total is calculated from its lines now."""
    customer = Link(Customer, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    total = Currency(required=True, minimum='0.01')
    tax_rate = Decimal(scale=4, minimum='0', maximum='100', default='0.0000')


@pytest.mark.parametrize('field', ['total', 'tax_rate'])
@pytest.mark.parametrize('amount', ['1e999999999', '-1e999999999'])
def test_extreme_decimals_in_create_update_and_query(tmp_path, field, amount):
    database = Database(tmp_path / 'charges.sqlite3', Registry(Customer, Charge))
    database.initialize()
    application = Application(database)
    customer = database.create(Customer, customer_name='Decimal check')
    values = {'customer': customer.id, 'reference': 'VALID', 'total': '12.50'}
    status, payload, _ = request(application, 'POST', '/api/charge', {**values, field: amount})
    assert status == 422 and field in payload['fields']
    assert database.list(Charge) == []
    charge = database.create(Charge, **values)
    status, payload, _ = request(application, 'PATCH', f'/api/charge/{charge.id}', {field: amount})
    assert status == 422 and field in payload['fields']
    assert database.get(Charge, charge.id).to_dict() == charge.to_dict()
    query = urlencode({'filters': json.dumps({field + '__gte': amount})})
    status, payload, _ = request(application, 'GET', '/api/charge?' + query)
    assert status == 422 and 'error' in payload
    assert request(application, 'GET', '/health')[0] == 200


def test_invalid_unicode_update_does_not_change_record(application, database):
    customer = database.create(Customer, customer_name='Keep this name')
    key = '\ud800'
    status, payload, headers = request(application, 'PATCH', f'/api/customer/{customer.id}', {
        'customer_name': 'Changed', key: 1,
    })
    assert status == 422 and payload['fields'][key] == 'Unknown field'
    assert headers[b'content-type'].startswith(b'application/json')
    assert database.get(Customer, customer.id).customer_name == 'Keep this name'
