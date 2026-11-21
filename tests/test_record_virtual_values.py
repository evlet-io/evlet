"""A Record carries its virtual values (stored=False): read through a Context they match the HTTP detail, on the
record and on its rows, and they are calculated only when one is read. Real SQLite."""

from decimal import Decimal as D

import pytest

from evlet import Child, Database, Decimal, Model, Registry, Table, Text, User
from evlet.calc import F, when
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import ALL, start

ADMIN = User(1, 'admin', 'admin')


class Invoice(Model):
    Meta = FiniteMeta
    permissions = ALL

    class Line(Child):
        amount = Decimal(scale=2, default=0)
        half = Decimal(scale=2, compute=F('amount') / 2, stored=False)

    lines = Table(Line)
    total = Decimal(scale=2, compute=lines.sum('amount'))
    paid = Decimal(scale=2, default=0)
    payment_status = Text(compute=when(F('paid') >= F('total'), 'paid', 'unpaid'), stored=False)


@pytest.fixture
def app(tmp_path):
    app = start(Database(tmp_path / 'virtual.sqlite3', Registry(Invoice)))
    yield app
    app.database.close()


def counted(app):
    calls = []
    real = app.business.virtual_values

    def virtual_values(*args, **kwargs):
        calls.append(args[1])
        return real(*args, **kwargs)
    app.business.virtual_values = virtual_values
    return calls


def test_a_record_reads_its_virtual_values_as_the_http_detail_does(app):
    with app.business.context(ADMIN) as context:
        record_id = context.create(Invoice, paid='10', children={'lines': [{'amount': '30'}]}).id
    _, detail, _ = client(app)('GET', f'/api/invoice/{record_id}')
    assert (detail['payment_status'], detail['children']['lines'][0]['half']) == ('unpaid', '15.00')
    with app.business.context(ADMIN) as context:
        record = context.get(Invoice, record_id)
        assert record.payment_status == 'unpaid' and record.lines[0].half == D('15.00')
        # After a write the record reads the new values, calculated again.
        record.paid = D('30')
        record.save()
        assert record.payment_status == 'paid'
        record.reload()
        assert record.payment_status == 'paid' and record.lines[0].half == D('15.00')


def test_a_row_reads_virtual_values_first(app):
    with app.business.context(ADMIN) as context:
        record_id = context.create(Invoice, children={'lines': [{'amount': '8'}]}).id
    with app.business.context(ADMIN) as context:
        assert context.get(Invoice, record_id).lines[0].half == D('4.00')


def test_listing_records_settles_none_until_a_virtual_value_is_read(app):
    with app.business.context(ADMIN) as context:
        for index in range(50):
            context.create(Invoice, paid=str(index), children={'lines': [{'amount': '10'}]})
    calls = counted(app)
    with app.business.context(ADMIN) as context:
        rows = context.list(Invoice, limit=50)['rows']
        assert len(rows) == 50 and [str(row.paid) for row in rows[:2]] == ['0.00', '1.00']
        assert calls == []
        assert [row.payment_status for row in rows[9:12]] == ['unpaid', 'paid', 'paid']
        assert rows[9].payment_status == 'unpaid'
        assert calls == [Invoice] * 3
