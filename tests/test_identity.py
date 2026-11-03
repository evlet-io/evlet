"""Record ids: 63-bit time-ordered integers in storage, 13-character text everywhere else."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time

import pytest

from evlet import identity
from evlet.identity import RecordId, decode, encode, new_id, path_id
from examples.playground.models import Customer, Invoice
from tests.test_business import client, hashed, secured  # noqa: F401 (fixtures)


def test_text_form_is_13_lowercase_crockford_characters():
    for value in (1, 42, 2**53 + 1, 2**62, 2**63 - 1):
        text = encode(value)
        assert len(text) == 13 and text == text.lower()
        assert set(text) <= set(identity.ALPHABET)
        assert decode(text) == value
        assert decode(text.upper()) == value
    assert encode(1) == '0000000000001'
    assert encode(2**63 - 1) == '7zzzzzzzzzzzz'


def test_input_reads_i_l_o_as_digits_and_refuses_the_rest():
    assert decode('0000000000O1I') == decode('0000000000011')
    assert decode('000000000001L') == decode('0000000000011')
    for bad in ('000000000001u', '00000000000001', '000000000001', '8000000000000', '0000000000000', '', 42, None, ' 000000000001'):
        with pytest.raises(ValueError):
            decode(bad)
    assert path_id('0000000000001') == 1 and path_id('42') is None and path_id('new') is None


def test_legacy_ids_keep_their_value_and_show_padded():
    # Rows made before time-ordered ids keep their small ids; 42 reads as 000000000001a.
    assert str(RecordId(42)) == '000000000001a'
    assert RecordId('000000000001A') == 42
    assert identity.from_argument('42') == 42 and identity.from_argument('000000000001a') == 42


def test_record_id_is_an_int_with_a_text_face():
    record_id = RecordId(42)
    assert record_id == 42 and hash(record_id) == hash(42) and {42: 'x'}[record_id] == 'x'
    assert f'{record_id}' == '000000000001a' and f'{record_id:d}' == '42'
    assert json.loads(json.dumps(identity.jsonable({'id': record_id, 'rows': [(record_id,)]}))) == {'id': '000000000001a', 'rows': [['000000000001a']]}
    with pytest.raises(ValueError):
        RecordId(True)
    with pytest.raises(ValueError):
        RecordId(0)


def test_new_ids_carry_the_time_and_increase_strictly():
    before = int(time.time() * 1000) - identity.EPOCH_MS
    ids = [new_id() for _ in range(5000)]
    after = int(time.time() * 1000) - identity.EPOCH_MS
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert all(0 < value < 2**63 for value in ids)
    assert before <= ids[0] >> identity.RANDOM_BITS and ids[-1] >> identity.RANDOM_BITS <= after + 1
    # Text ids sort the same way as the numbers.
    assert sorted(str(value) for value in ids) == [str(value) for value in ids]


def test_new_ids_are_unique_across_threads():
    with ThreadPoolExecutor(8) as pool:
        batches = list(pool.map(lambda _: [new_id() for _ in range(2000)], range(8)))
    flat = [value for batch in batches for value in batch]
    assert len(set(flat)) == len(flat)


def test_records_get_time_ordered_ids_and_concurrent_writers_never_clash(database):
    first = database.create(Customer, name='First')
    assert first.id > 2**41 and isinstance(first.id, RecordId)

    def create(index):
        return database.create(Customer, name=f'Customer {index}').id
    with ThreadPoolExecutor(6) as pool:
        made = list(pool.map(create, range(60)))
    assert len(set(made)) == 60 and min(made) > first.id
    assert database.get(Customer, str(made[0])).id == made[0]


def test_a_taken_id_is_drawn_again(database, monkeypatch):
    taken = database.create(Customer, name='Taken')
    later = RecordId(taken.id + 1)
    drawn = iter([taken.id, taken.id, later])
    monkeypatch.setattr('evlet.database.new_id', lambda: next(drawn))
    fresh = database.create(Customer, name='Fresh')
    assert fresh.id == later and database.get(Customer, taken.id).name == 'Taken'


def test_other_unique_values_still_conflict(database):
    customer = database.create(Customer, name='Acme')
    database.create(Invoice, customer=customer.id, reference='INV-1', total='1.00')
    from evlet import ConflictError
    with pytest.raises(ConflictError):
        database.create(Invoice, customer=customer.id, reference='INV-1', total='2.00')


def test_legacy_rows_keep_working_next_to_new_ones(database):
    with sqlite3.connect(database.path) as connection:
        connection.execute("INSERT INTO customer(id, name, active, currency) VALUES (42, ?, 1, 'AED')", ('Old customer',))
    old = database.get(Customer, '000000000001a')
    assert old.id == 42 and str(old.to_dict()['id']) == '000000000001a'
    invoice = database.create(Invoice, customer=42, reference='INV-OLD', total='5.00')
    assert invoice.customer == 42 and identity.jsonable(invoice.to_dict())['customer'] == '000000000001a'
    new = database.create(Customer, name='New customer')
    assert [record.id for record in database.list(Customer, order_by='id')] == [42, new.id]


def test_http_sends_ids_only_as_text_and_takes_them_in_any_case(secured):
    call = client(secured)
    status, customer, headers = call('POST', '/api/customer', {'name': 'Acme'})
    assert status == 201 and isinstance(customer['id'], str) and len(customer['id']) == 13
    assert headers[b'location'] == f"/api/customer/{customer['id']}".encode()
    status, invoice, _ = call('POST', '/api/invoice', {'customer': customer['id'].upper(), 'reference': 'INV-9', 'total': '9.00'})
    assert status == 201, invoice
    assert invoice['customer'] == customer['id'] and isinstance(invoice['id'], str)
    status, read, _ = call('GET', f"/api/invoice/{invoice['id'].upper()}")
    assert status == 200 and read['id'] == invoice['id']
    # A decimal id is no longer a record route; the text id is the only form.
    assert call('GET', f"/api/invoice/{decode(invoice['id'])}")[0] == 404
    status, page, _ = call('GET', '/api/invoice?' + 'filters=' + json.dumps({'customer': customer['id']}))
    assert status == 200 and [item['id'] for item in page['items']] == [invoice['id']]
    status, page, _ = call('GET', '/api/customer?q=' + customer['id'])
    assert [item['id'] for item in page['items']] == [customer['id']]
    # No number in any response is an id: every large integer would lose precision in a browser.
    assert not any(isinstance(value, int) and value > 2**53 for value in _values(read))


def test_described_schemas_type_ids_as_text(secured):
    call = client(secured)
    status, description, _ = call('GET', '/api/_discovery/invoice')
    assert status == 200
    text = json.dumps(description)
    assert identity.PATTERN.replace('\\', '\\\\') in text


def _values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)
    else:
        yield value
