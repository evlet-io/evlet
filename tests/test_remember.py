"""Field(remember=True): a new record's form suggests the value this person last saved, kept in evlet_user_prefs,
on real SQLite and over HTTP."""

import pytest

from evlet import Application, Currency, Database, Date, Link, Model, Registry, Rule, Select, Text
from evlet.auth import LocalAuth
from evlet.fields import Integer
from evlet.calc import F
from evlet.ui.metadata import field_schema
from finite_fixtures import FiniteMeta
from support import PASSWORD, client, http
from test_rules_types import ALL

FULL = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'))}


class Supplier(Model):
    permissions = FULL
    code = Text(default='')


class Expense(Model):
    """A plain model: the browser posts it to /api/expense."""
    permissions = {**FULL, 'viewer': Rule(('read',))}
    supplier = Link(Supplier, remember=True)
    category = Select(['travel', 'meals', 'office'], remember=True)
    memo = Text(default='', remember=True)
    amount = Currency(default=0)


class Bill(Model):
    """A finite-v1 document: its writes go through the document pipeline. Its initial= suggestion applies until the
    person has saved a value of their own."""
    Meta = FiniteMeta
    permissions = {**FULL, 'editor': Rule(('read', 'create', 'update', 'delete'), write_fields=('due', 'terms', 'amount'))}
    supplier = Link(Supplier, remember=True)
    terms = Integer(initial=F('amount') * 0 + 30, remember=True)
    due = Date()
    amount = Currency(default=0)


@pytest.fixture
def office(tmp_path):
    database = Database(tmp_path / 'remember.sqlite3', Registry(Supplier, Expense, Bill))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    for name in ('editor', 'viewer'):
        assert admin('POST', '/api/auth/users', {'username': name, 'password': PASSWORD, 'role': name})[0] == 201
    with app.database.transaction() as tx:
        acme, globex = tx.create(Supplier, code='acme').id, tx.create(Supplier, code='globex').id
    return app, admin, str(acme), str(globex)


def test_a_new_form_suggests_what_this_person_last_saved(office):
    app, admin, acme, globex = office
    editor = client(app, 'editor')
    assert editor('GET', '/api/expense/remembered')[:2] == (200, {'values': {}})
    status, saved, _ = editor('POST', '/api/expense', {'supplier': acme, 'category': 'travel', 'memo': 'Taxi', 'amount': '12.00'})
    assert status == 201, saved
    assert editor('GET', '/api/expense/remembered')[1] == {'values': {'supplier': acme, 'category': 'travel', 'memo': 'Taxi'}}
    # The last save wins, an update included; a cleared value is forgotten.
    url = f"/api/expense/{saved['id']}"
    status, _, _ = editor('PATCH', url, {'category': 'meals', 'memo': ''}, headers={b'if-match': editor('GET', url)[2][b'etag']})
    assert status == 200
    assert editor('GET', '/api/expense/remembered')[1] == {'values': {'supplier': acme, 'category': 'meals'}}
    # Each person has their own; a role that cannot create gets nothing.
    assert admin('GET', '/api/expense/remembered')[1] == {'values': {}}
    admin('POST', '/api/expense', {'supplier': globex, 'category': 'office'})
    assert admin('GET', '/api/expense/remembered')[1]['values'] == {'supplier': globex, 'category': 'office'}
    assert editor('GET', '/api/expense/remembered')[1]['values']['supplier'] == acme
    assert client(app, 'viewer')('GET', '/api/expense/remembered')[1] == {'values': {}}
    assert editor('POST', '/api/expense/remembered', {})[0] in (404, 405)
    schema = {field['name']: field for field in field_schema_of(admin, 'expense')}
    assert schema['supplier']['remember'] is True and 'remember' not in schema['amount']


def field_schema_of(c, model):
    return next(item for item in c('GET', '/api/schema')[1]['models'] if item['name'] == model)['fields']


def test_documents_remember_only_what_the_person_may_still_write_and_read(office):
    app, admin, acme, _ = office
    editor = client(app, 'editor')
    status, bill, _ = admin('POST', '/api/bill', {'supplier': acme, 'terms': 45, 'amount': '10.00'})
    assert status == 201, bill
    assert admin('GET', '/api/bill/remembered')[1] == {'values': {'supplier': acme, 'terms': 45}}
    status, bill, _ = editor('POST', '/api/bill', {'terms': 60, 'amount': '5.00'})
    assert status == 201, bill
    # The editor may not write supplier, so it is never suggested to them.
    assert editor('GET', '/api/bill/remembered')[1] == {'values': {'terms': 60}}
    # A remembered Link to a record that is gone is dropped.
    with app.database.transaction() as tx:
        tx.connection.run(__import__('evlet.qb', fromlist=['qb']).delete('bill'))
        tx.delete(Supplier, acme)
    assert admin('GET', '/api/bill/remembered')[1] == {'values': {'terms': 45}}


def test_remember_is_checked_on_the_field():
    with pytest.raises(ValueError, match='remember must be True or False'):
        Text(remember='yes')
    for field in (lambda: Text(remember=True, read_only=True), lambda: Currency(remember=True, stored=False),
                  lambda: Text(remember=True, fetch='supplier.code')):
        with pytest.raises(ValueError, match='remember=True suggests'):
            field()
    assert field_schema(Select(['a'], remember=True))['remember'] is True
