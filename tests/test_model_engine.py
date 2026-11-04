from datetime import date, datetime, timezone
from decimal import Decimal as DecimalValue, localcontext
from urllib.parse import urlencode

import pytest

from evlet import (Boolean, Currency, Database, Date, DateTime, Decimal, Email, Integer,
                   Link, LongText, Model, Registry, Select, Text, URL, ValidationError,
                   ConflictError, NotFoundError)
from examples.playground.models import Customer, Invoice, Payment
from test_api import request


def test_rich_fields_round_trip(tmp_path):
    class Example(Model):
        active = Boolean(default=True, required=True)
        day = Date(default=date.today)
        recorded = DateTime()
        ratio = Decimal(scale=4)
        status = Select(['draft', 'sent'], default='draft')
        email = Email()
        website = URL()
        notes = LongText()
    db = Database(tmp_path / 'fields.sqlite3', Registry(Example))
    db.initialize()
    record = db.create(Example, day='2024-02-29', recorded='2026-10-28T12:00:00+04:00',
                       ratio='0.1234', email='a@example.test', website='https://example.test/a', notes='x' * 1000)
    assert record.day == date(2024, 2, 29)
    assert record.recorded == datetime(2026, 10, 28, 8, tzinfo=timezone.utc)
    assert record.to_dict()['recorded'] == '2026-10-28T08:00:00.000000Z'
    assert record.to_dict()['ratio'] == '0.1234'
    assert db.get(Example, record.id).active is True
    assert Example().day == date.today()
    assert db.list(Example, filters={'ratio__gt': '0.12', 'active': True})[0].id == record.id
    db.update(Example, record.id, active=False, email=None)
    assert db.get(Example, record.id).email is None


@pytest.mark.parametrize('field,value', [
    (Boolean(), 1), (Boolean(), 'false'), (Boolean(), []),
    (Date(), '2023-02-29'), (Date(), '20260101'), (Date(), datetime.now()),
    (DateTime(), '2026-10-28T12:00:00'), (DateTime(), datetime.now()),
    (Decimal(scale=3), '0.0001'), (Decimal(), 1.2), (Decimal(), 'NaN'),
    (Decimal(), '1e10000'), (Select(['a', 'b']), 'c'), (Email(), 'no-at-sign'),
    (Email(), 'a @example.test'), (URL(), 'javascript:alert(1)'),
    (URL(), 'https://example.test:bad'), (Integer(maximum=3), 4),
])
def test_rich_fields_reject_invalid_values(field, value):
    with pytest.raises(ValueError):
        field.clean(value)


def test_decimal_is_independent_of_callers_context():
    field = Decimal(scale=4)
    with localcontext() as context:
        context.prec = 2
        value = field.clean('123456789.1234')
        assert field.to_storage(value) == 1234567891234
        assert field.serialize(field.from_storage(1234567891234)) == '123456789.1234'
    assert Currency().serialize(Currency().clean('12.5')) == '12.50'
    for kwargs in ({'scale': True}, {'scale': 10}, {'minimum': 'NaN'}, {'minimum': '3', 'maximum': '1'}):
        with pytest.raises(ValueError):
            Decimal(**kwargs)


def test_custom_validation_runs_on_partial_update(tmp_path):
    def reject_reserved(value):
        if value == 'reserved':
            raise ValueError('Reserved name')
    class Booking(Model):
        name = Text(required=True, validator=reject_reserved)
        start = Date(required=True)
        end = Date(required=True)
        def validate(self):
            if self.end < self.start:
                raise ValidationError({'end': 'End precedes start'})
    db = Database(tmp_path / 'rules.sqlite3', Registry(Booking))
    db.initialize()
    booking = db.create(Booking, name='One', start='2026-10-01', end='2026-10-02')
    with pytest.raises(ValidationError):
        db.update(Booking, booking.id, end='2026-09-30')
    with pytest.raises(ValidationError):
        db.update(Booking, booking.id, name='reserved')
    assert db.get(Booking, booking.id).end == date(2026, 10, 2)
    assert db.update(Booking, booking.id, name='Two').start == booking.start


def test_updates_deletes_relationships_and_rollback(database):
    customer = database.create(Customer, customer_name='Original')
    invoice = database.create(Invoice, customer=customer.id, reference='INV', total='10.00')
    payment = database.create(Payment, invoice=invoice.id, reference='PAY', amount='10.00')
    assert database.related(invoice, 'customer').id == customer.id
    assert database.related(database.related(payment, 'invoice'), 'customer').customer_name == 'Original'
    with pytest.raises(ConflictError):
        database.delete(Customer, customer.id)
    with pytest.raises(ConflictError):
        database.update(Invoice, invoice.id, customer=999)
    with pytest.raises(ValidationError):
        database.update(Customer, customer.id, id=999)
    with pytest.raises(RuntimeError):
        with database.transaction() as tx:
            tx.update(Customer, customer.id, name='Rolled back')
            tx.delete(Payment, payment.id)
            raise RuntimeError('cancel')
    assert database.get(Customer, customer.id).customer_name == 'Original'
    assert database.get(Payment, payment.id).id == payment.id
    changed = database.update(Invoice, invoice.id, total='12.34')
    assert changed.reference == 'INV' and changed.total == DecimalValue('12.34')
    assert invoice.total == DecimalValue('10.00')  # existing snapshot stays unchanged
    database.delete(Payment, payment.id)
    database.delete(Invoice, invoice.id)
    database.delete(Customer, customer.id)
    with pytest.raises(NotFoundError):
        database.delete(Customer, customer.id)


def test_queries_use_types_relationship_paths_and_stable_order(database):
    a = database.create(Customer, customer_name='Alpha 100%_')
    b = database.create(Customer, customer_name="B'); DROP TABLE customer;--")
    inv_a = database.create(Invoice, customer=a.id, reference='A', total='10.00')
    inv_b = database.create(Invoice, customer=b.id, reference='B', total='2.00')
    payment = database.create(Payment, invoice=inv_a.id, reference='P', amount='10.00')
    assert database.list(Invoice, order_by='total')[0].id == inv_b.id
    assert database.list(Invoice, filters={'total__gte': '0', 'customer__customer_name__contains': '%_'})[0].id == inv_a.id
    assert database.list(Payment, filters={'invoice__customer__customer_name__icontains': 'ALPHA'})[0].id == payment.id
    assert database.list(Invoice, order_by='customer__customer_name')[0].id == inv_a.id
    assert database.count(Customer, filters={'email__isnull': True}) == 2
    assert database.count(Customer, filters={'id__in': [a.id, b.id]}) == 2
    assert database.count(Customer, filters={'id__in': []}) == 0
    assert database.count(Customer, filters={'customer_name': b.customer_name}) == 1
    assert database.count(Customer, filters={'customer_name__startswith': 'Alpha'}) == 1
    assert [x.id for x in database.list(Invoice, order_by=['total'], limit=1, offset=1)] == [inv_a.id]


@pytest.mark.parametrize('kwargs', [
    {'filters': [['customer_name']]}, {'filters': 'customer_name'}, {'filters': {'customer_name; DROP TABLE customer': 'x'}},
    {'filters': {'customer_name__contains': 5}}, {'filters': {'customer_name__gt': None}},
    {'filters': {'id__in': [None]}}, {'filters': {'id__in': list(range(101))}},
    {'filters': {'customer_name__isnull': 1}}, {'filters': {'customer_name__fake': 'x'}},
    {'filters': {'customer_name__id': 1}}, {'order_by': 'id DESC; DROP TABLE customer'},
    {'order_by': []}, {'order_by': [True]},
])
def test_query_rejects_unsafe_or_invalid_inputs(database, kwargs):
    with pytest.raises(ValidationError):
        database.list(Customer, **kwargs)
    assert database.count(Customer) == 0


def test_http_update_delete_and_filters(application):
    _, customer, _ = request(application, 'POST', '/api/customer', {'customer_name': 'Before'})
    url = f'/api/customer/{customer["id"]}'
    assert request(application, 'PATCH', url, {'customer_name': 'After'})[1]['customer_name'] == 'After'
    assert request(application, 'PATCH', url, {'customer_name': ''})[0] == 422
    query = urlencode({'filters': '{"customer_name__contains":"Aft"}', 'order_by': 'customer_name', 'limit': 1})
    assert request(application, 'GET', '/api/customer?' + query)[1]['items'][0]['customer_name'] == 'After'
    assert request(application, 'DELETE', url)[:2] == (204, None)
    assert request(application, 'GET', url)[0] == 404
    assert request(application, 'PATCH', url, {'customer_name': 'Missing'})[0] == 404
    for filters in ('[["customer_name"]]', '"customer_name"', '{"id__in": [null]}', '{"unknown": 1}', '{"id": NaN}'):
        assert request(application, 'GET', '/api/customer?' + urlencode({'filters': filters}))[0] == 422
    assert request(application, 'PATCH', '/api/customer/2', body=b'bad')[0] == 400
    assert request(application, 'PATCH', '/api/customer/2', body=b'x' * 65537)[0] == 413


def test_concurrent_partial_updates_preserve_other_fields(database):
    from concurrent.futures import ThreadPoolExecutor
    customer = database.create(Customer, customer_name='Before', email='before@example.test')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(database.update, Customer, customer.id, **changes) for changes in (
            {'customer_name': 'After'}, {'email': 'after@example.test'},
        )]
        for future in futures:
            future.result()
    saved = database.get(Customer, customer.id)
    assert saved.customer_name == 'After' and saved.email == 'after@example.test'


def test_extreme_datetimes_are_validation_errors():
    with pytest.raises(ValueError, match='supported UTC range'):
        DateTime().clean('0001-01-01T00:00:00+14:00')


def test_delete_has_no_content_headers(application):
    _, customer, _ = request(application, 'POST', '/api/customer', {'customer_name': 'Delete'})
    status, body, headers = request(application, 'DELETE', f'/api/customer/{customer["id"]}')
    assert status == 204 and body is None
    assert b'content-length' not in headers and b'content-type' not in headers
