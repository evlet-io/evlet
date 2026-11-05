from decimal import Decimal as D

import pytest

from evlet import (Model, Child, Table, Decimal, Currency, Text, Rule, Registry,
                   Database, LocalAuth, Application, User, ValidationError, action, depends, Ref)
from evlet.identity import RecordId
from evlet.reactivity import compile_expression, evaluate
from test_business import http, client, PASSWORD
from pathlib import Path
import json


class Order(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read', 'create', 'update'))}
    title = Text(required=True)
    status = Text(default='Draft', read_only=True)
    class Item(Child):
        qty = Decimal(required=True, default='1', minimum='0')
        rate = Currency(required=True, default='0')
        amount = Currency(compute='qty * rate')
        @depends('qty')
        def available(self):
            return self.qty + D(2)
        stock = Decimal(query=available, stored=False)
        def validate(self):
            if self.qty <= 0: raise ValidationError({'qty': 'Enter a positive quantity'})
    items = Table(Item, required=True)
    total = Currency(compute='sum(items.amount)')
    def validate(self):
        if not self.items: raise ValidationError({'items': 'Add an item'})
    @action
    def confirm(self, note: str = ''):
        if self.status != 'Draft': raise ValidationError({'status': 'Already confirmed'})
        self.status = 'Confirmed'


@pytest.fixture
def application(tmp_path):
    registry = Registry(Order)
    database = Database(tmp_path / 'test.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


def test_owned_document_http_and_revision(application):
    c = client(application)
    status, record, headers = c('POST', '/api/order', {'title': 'Test', 'children': {'items': [{'_key': 'row-a', 'qty': '2', 'rate': '1.25'}]}})
    assert status == 201, record
    assert record['total'] == '2.50'
    assert record['children']['items'][0]['amount'] == '2.50'
    assert record['_row_ids']['row-a'] == record['children']['items'][0]['id']
    assert c('GET', '/api/order_items')[0] == 404
    path = f"/api/order/{record['id']}"
    assert c('PATCH', path, {'title': 'Changed'})[0] == 428
    saved = c('GET', path)
    assert c('POST', path + '/actions/confirm', {'note': 'Ready'}, headers={b'if-match': saved[2][b'etag']})[0] == 200
    assert c('PATCH', path, {'title': 'Old'}, headers={b'if-match': saved[2][b'etag']})[0] == 409
    assert application.database.get(Order, RecordId(record['id'])).status == 'Confirmed'
    with pytest.raises(AttributeError): application.database.get(Order, RecordId(record['id'])).title = 'Invalid'


def test_partial_preview_unsaved_rows_no_persistence(application):
    c = client(application)
    status, result, _ = c('POST', '/api/_draft/order', {'revision': 7, 'values': {'children': {'items': [{'_key': 'draft-a', 'qty': '3', 'rate': '2.50'}]}}})
    assert status == 200, result
    assert result['revision'] == 7
    assert result['values']['total'] == '7.50'
    assert result['values']['children']['items'][0]['stock'] == '5.0000'
    assert application.database.count(Order) == 0
    assert c('POST', '/api/_draft/order', {'values': {'status': 'Confirmed'}})[0] == 403


def test_failure_rolls_back_all_rows(application):
    c = client(application)
    status, result, _ = c('POST', '/api/order', {'title': 'Invalid', 'children': {'items': [{'qty': '1', 'rate': '4'}, {'qty': '0'}]}})
    assert status == 422, result
    assert application.database.count(Order) == 0
    child = application.registry.children[0].model
    assert application.database.count(child) == 0


def test_permissions_no_defaults_for_new_resources(application):
    assert not application.business.policy.allows(User(1, 'viewer', 'viewer'), Order, 'read')


def test_expression_lazy_exact_and_invalid():
    assert evaluate(compile_expression('qty * rate'), lambda path: {'qty': D('3'), 'rate': D('0.10')}[path]) == D('0.30')
    assert evaluate(compile_expression('1 / 0 if False else 7'), lambda path: None) == 7
    assert evaluate(compile_expression('False and 1 / 0'), lambda path: None) is False
    for expression in ('__import__("os")', 'x.__class__', '[x for x in rows]', '1.2 + 3'):
        with pytest.raises(ValueError): compile_expression(expression)
    with pytest.raises(ValueError, match='Division by zero'): evaluate(compile_expression('1 / 0'), lambda path: None)


def test_cross_parent_row_cycle_rejected():
    class Cyclic(Model):
        class Item(Child):
            amount = Currency(compute='parent.total')
        items = Table(Item)
        total = Currency(compute='sum(items.amount)')
    with pytest.raises(ValueError, match='circular'): Registry(Cyclic)


def test_browser_server_shared_vectors():
    from evlet.reactivity import serialized, round_field
    vectors = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/document-vectors.json').read_text())
    for vector in vectors:
        expression = compile_expression(vector['source'])
        assert serialized(expression) == vector['expression']
        def convert(value):
            if isinstance(value, list): return [convert(item) for item in value]
            return None if value is None else D(value)
        field = Decimal(scale=vector['scale'])
        def run(): return field.serialize(field.clean(round_field(field, evaluate(expression, lambda path: convert(vector['inputs'][path])))))
        if vector.get('error'):
            with pytest.raises((ValueError, ArithmeticError)): run()
        else:
            assert run() == vector['expected']


def test_negation_keeps_full_precision_before_currency_rounding(tmp_path):
    class Refund(Model):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
        title = Text(required=True)
        qty = Decimal(scale=9)
        fx = Decimal(scale=9)
        credit = Currency(compute='-(qty * fx)')
        debit = Currency(compute='qty * fx')
    database = Database(tmp_path / 'refund.sqlite3', Registry(Refund))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    status, body, _ = client(app)('POST', '/api/refund', {'title': 'x', 'qty': '1016111116.666666665', 'fx': '10.000000003'})
    assert status == 201 and (body['credit'], body['debit']) == ('-10161111169.71', '10161111169.71')


def test_document_contract_fixture(application):
    from scripts.generate_document_fixtures import collect
    from evlet.audit import fixed_times
    from evlet.identity import sequential_ids
    fixture = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/document-contract.json').read_text())
    with sequential_ids(), fixed_times():
        assert collect(application) == fixture
