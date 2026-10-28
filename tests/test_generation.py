import json
import sqlite3
from datetime import date, datetime, timezone
from urllib.parse import urlencode

import pytest

from evlet import (Application, Boolean, Currency, Database, Date, DateTime, Decimal, Email,
                   Integer, Link, LongText, Model, Registry, Select, Text, URL)
from examples.playground.migrate import legacy_registry, migrations
from examples.playground.models import Customer, Invoice, Payment, Project, registry
from test_api import request


def test_ui_schema_defaults_labels_and_storage_snapshot_are_separate(tmp_path):
    calls = []
    class Thing(Model):
        class Meta:
            label = 'Thing'
            plural = 'Things'
            list_fields = ('name', 'amount')
            search_fields = ('name',)
        name = Text(required=True, label='Display name')
        amount = Currency(default='12.50', read_only=True)
        when = Date(default=lambda: calls.append(1) or date(2026, 10, 28))
        secret = Text(hidden=True, default='internal')
    registered = Registry(Thing)
    original = registered.schema()
    db = Database(tmp_path / 'presentation.sqlite3', registered)
    db.initialize()
    metadata = registered.ui_schema()
    assert calls == []
    view = metadata['models'][0]
    fields = {field['name']: field for field in view['fields']}
    assert metadata['ui_version'] == 1
    assert view['title_field'] == 'name'
    assert view['list_fields'] == ['id', 'name', 'amount']
    assert fields['amount']['default'] == '12.50'
    assert fields['amount']['read_only'] is True
    assert fields['when']['default_kind'] == 'callable' and 'default' not in fields['when']
    assert 'primevue' not in json.dumps(metadata).lower()
    Thing.name.label = 'Renamed label'
    Thing.Meta.list_fields = ('name',)
    assert registered.schema() == original
    db.initialize()
    assert registered.ui_schema()['models'][0]['fields'][0]['label'] == 'Renamed label'
    app = Application(db)
    assert request(app, 'POST', '/api/thing', {'name': 'Saved', 'amount': '1.00'})[0] == 422
    status, saved, _ = request(app, 'POST', '/api/thing', {'name': 'Saved'})
    assert status == 201 and saved['amount'] == '12.50' and saved['when'] == '2026-10-28'
    assert calls == [1]
    assert request(app, 'PATCH', '/api/thing/1', {'amount': '2.00'})[0] == 422
    assert db.get(Thing, 1).to_dict()['amount'] == '12.50'


def test_unfamiliar_model_supports_every_field_and_standard_api(tmp_path):
    class Device(Model):
        name = Text(required=True)
        notes = LongText()
        count = Integer(default=0)
        cost = Currency(default='0.00')
        rate = Decimal(default='1.2345')
        enabled = Boolean(default=False)
        day = Date(default=date(2026, 10, 28))
        moment = DateTime(default=datetime(2026, 10, 28, tzinfo=timezone.utc))
        category = Select(['A', 'B'], default='A')
        email = Email()
        website = URL()
    db = Database(tmp_path / 'device.sqlite3', Registry(Device))
    db.initialize()
    app = Application(db)
    status, schema, _ = request(app, 'GET', '/api/schema')
    assert status == 200
    assert {f['type'] for f in schema['models'][0]['fields']} == {
        'text', 'longtext', 'integer', 'currency', 'decimal', 'boolean', 'date', 'datetime', 'select', 'email', 'url',
    }
    status, saved, _ = request(app, 'POST', '/api/device', {'name': 'Sensor', 'email': 'test@example.test', 'website': 'https://example.test'})
    assert status == 201 and saved['rate'] == '1.2345' and saved['enabled'] is False
    assert request(app, 'PATCH', '/api/device/1', {'notes': 'Updated'})[1]['name'] == 'Sensor'
    result = request(app, 'GET', '/api/device?q=example&limit=1')[1]
    assert result['total'] == 1 and len(result['items']) == 1
    assert request(app, 'DELETE', '/api/device/1')[0] == 204


@pytest.mark.parametrize('options', [
    {'list_fields': ('missing',)}, {'list_fields': ('name', 'name')},
    {'search_fields': ('active',)}, {'search_fields': ('hidden',)},
    {'title_field': 'active'}, {'title_field': 'hidden'}, {'plural': ''}, {'typo': 'x'},
])
def test_invalid_view_overrides_fail_early(options):
    Dynamic = type('Dynamic', (Model,), {
        'name': Text(), 'active': Boolean(), 'hidden': Text(hidden=True), 'Meta': type('Meta', (), options),
    })
    with pytest.raises(ValueError):
        Registry(Dynamic)


def test_search_combines_text_fields_with_typed_filters_and_count(database, application):
    first = database.create(Customer, name="Literal %_'); DROP TABLE customer;--", email='alpha@example.test', active=True)
    database.create(Customer, name='Alpha', active=False)
    database.create(Customer, name='Elsewhere', email='alpha@elsewhere.test', active=True)
    query = urlencode({'q': 'ALPHA', 'filters': json.dumps({'active': True}), 'limit': 1, 'offset': 1, 'order_by': 'id'})
    status, page, _ = request(application, 'GET', '/api/customer?' + query)
    assert status == 200 and page['total'] == 2 and len(page['items']) == 1
    assert page['items'][0]['name'] == 'Elsewhere'
    assert database.count(Customer, search='alpha', filters={'active': True}) == 2
    assert database.list(Customer, search="%_'); DROP TABLE customer;--")[0].id == first.id
    assert database.list(Customer, search=f'#{first.id}')[0].id == first.id
    for q in ('x' * 201, '\ud800'):
        with pytest.raises(ValueError):
            database.list(Customer, search=q)
    assert request(application, 'GET', '/api/customer?q=' + 'x' * 201)[0] == 422
    assert request(application, 'GET', '/api/customer?q=x&q=y')[0] == 422


def test_relationship_search_reaches_records_beyond_first_hundred(database, application):
    first = database.create(Customer, name='Old customer')
    with database.transaction() as tx:
        for i in range(105): tx.create(Customer, name=f'Recent customer {i}')
    invoice = database.create(Invoice, customer=first.id, reference='OLD', total='12.50')
    page = request(application, 'GET', '/api/customer?q=Old&limit=20')[1]
    assert page['total'] == 1 and page['items'][0]['id'] == first.id
    assert request(application, 'GET', f'/api/customer/{first.id}')[1]['name'] == 'Old customer'
    assert request(application, 'GET', f'/api/invoice/{invoice.id}')[1]['customer'] == first.id


@pytest.mark.parametrize('installation', ['legacy', 'milestone2', 'milestone3', 'fresh'])
def test_project_migration_preserves_existing_records(tmp_path, installation):
    path = tmp_path / 'playground.sqlite3'
    if installation != 'fresh':
        old_registry = legacy_registry() if installation == 'legacy' else Registry(Customer, Invoice, Payment, *([Project] if installation == 'milestone3' else []))
        db = Database(path, old_registry, migrations=None if installation == 'legacy' else migrations[:2 if installation == 'milestone3' else 1])
        db.initialize()
        customer = db.create(old_registry.models['customer'], name='Keep')
        invoice = db.create(old_registry.models['invoice'], customer=customer.id, reference='KEEP', total='123.45')
        db.create(old_registry.models['payment'], invoice=invoice.id, reference='KEEP', amount='123.45')
    target = Database(path, registry, migrations=migrations)
    applied = target.migrate(migrations)
    assert applied == ([m.name for m in migrations][2 if installation == 'milestone3' else 1 if installation == 'milestone2' else 0:])
    target.initialize()
    assert target.migrate(migrations) == []
    project = target.create(Project, name='Generated screens', budget='1.23')
    assert project.to_dict()['budget'] == '1.23' and project.status == 'planned'
    if installation != 'fresh':
        assert target.get(Customer, customer.id).name == 'Keep'
        assert target.get(Invoice, invoice.id).to_dict()['total'] == '123.45'
        assert target.get(Payment, 1).invoice == invoice.id
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
