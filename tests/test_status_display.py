"""How a record's state and status show: State(show=...), Meta.status and Select(colors=...), on SQLite over HTTP."""

import pytest

from evlet import Currency, Database, Model, Registry, Rule, Select, State, Text
from evlet.calc import F, when
from test_business import client
from test_meaning import serve
from test_rules_types import ALL, add_user
from test_states import etag


class Member(Model):
    """Active or left: not a pipeline, so it shows as a badge (the default)."""
    permissions = {**ALL, 'viewer': Rule(('read',), read_fields=('title',))}
    title = Text(required=True)
    standing = State(
        State.Step('active'),
        State.Step('left'),
        rules=(
            State.Rule('active', 'left', 'Mark as left', key='left'),
            State.Rule('left', 'active', 'Reinstate', key='active', by=('admin',)),
        ))


class Bill(Model):
    """A pipeline drawn as a stage bar, with a calculated payment status as the header badge."""
    permissions = ALL

    class Meta:
        calculation_language = 'finite-v1'
        numeric_profile = 'finite40-hu18-v1'
        status = 'payment_status'

    title = Text(required=True)
    stage = State(
        State.Step('draft'),
        State.Step('submitted'),
        State.Step('approved'),
        rules=(
            State.Rule('draft', 'submitted', 'Submit', key='submitted'),
            State.Rule('submitted', 'approved', 'Approve', key='approved'),
        ), show='bar')
    total = Currency(default=0)
    paid = Currency(default=0)
    payment_status = Select(('unpaid', 'partly_paid', 'paid'), colors={'unpaid': 'rose', 'partly_paid': 'amber', 'paid': 'green'},
                            compute=when(F('paid') >= F('total'), 'paid', when(F('paid') > 0, 'partly_paid', 'unpaid')))


class Ledger(Model):
    """Callers only read it: every move is blocked for every role."""
    permissions = ALL

    class Meta:
        api = 'read'

    title = Text(required=True)
    phase = State(
        State.Step('open'),
        State.Step('closed'),
        rules=(
            State.Rule('open', 'closed', 'Close', key='closed'),
        ), show='none')


def entry(c, name):
    return next(item for item in c('GET', '/api/schema')[1]['models'] if item['name'] == name)


@pytest.fixture
def app(tmp_path):
    return serve(Database(tmp_path / 'status.sqlite3', Registry(Member, Bill, Ledger)))


def test_show_is_validated():
    assert State(State.Step('a'), State.Step('b')).show == 'badge'
    assert State(State.Step('a'), State.Step('b'), show='bar').show == 'bar'
    assert State(State.Step('a'), State.Step('b'), show='none').show == 'none'
    for wrong in ('stepper', None, True, 'Badge'):
        with pytest.raises(ValueError, match='State show is "badge", "bar" or "none"'):
            State(State.Step('a'), State.Step('b'), show=wrong)


def test_select_colors_are_validated():
    assert Select(('a', 'b'), colors={'a': 'green'}).metadata()['colors'] == {'a': 'green'}
    assert 'colors' not in Select(('a', 'b')).metadata()
    with pytest.raises(ValueError, match='maps some of its choices'):
        Select(('a', 'b'), colors={'c': 'green'})
    with pytest.raises(ValueError, match='maps some of its choices'):
        Select(('a', 'b'), colors=['green'])
    with pytest.raises(ValueError, match='Select colors are blue, green'):
        Select(('a', 'b'), colors={'a': 'red'})
    assert State(State.Step('a'), State.Step('b', color='rose')).metadata()['colors'] == {'b': 'rose'}


@pytest.mark.parametrize('name, field, message', [
    ('missing', None, 'Meta.status names a visible Select or State'),
    ('note', Text(), 'Meta.status names a visible Select or State'),
    ('kind', Select(('a', 'b'), hidden=True), 'Meta.status names a visible Select or State'),
    (('kind',), Select(('a', 'b')), 'Meta.status names a visible Select or State'),
])
def test_meta_status_is_validated(name, field, message):
    meta = type('Meta', (), {'status': name})
    attrs = {'Meta': meta, 'note': Text(default='')}
    if field is not None:
        attrs['kind'] = field
    model = type('Thing', (Model,), attrs)
    with pytest.raises(ValueError, match=message):
        Registry(model).meaning


def test_meta_status_is_for_root_models():
    from evlet import Child, Table

    class Order(Model):
        class Line(Child):
            kind = Select(('a', 'b'))

            class Meta:
                status = 'kind'
        lines = Table(Line)

    with pytest.raises(ValueError, match='Meta.key_fields, relations, attachments and status are for root models'):
        Registry(Order).meaning


def test_schema_carries_show_status_and_colors(app):
    c = client(app)
    member, bill, ledger = entry(c, 'member'), entry(c, 'bill'), entry(c, 'ledger')
    assert member['state']['show'] == 'badge' and 'status' not in member
    assert bill['state']['show'] == 'bar' and bill['status'] == 'payment_status'
    assert ledger['state']['show'] == 'none'
    field = next(item for item in bill['fields'] if item['name'] == 'payment_status')
    assert field['colors'] == {'unpaid': 'rose', 'partly_paid': 'amber', 'paid': 'green'} and field['read_only']


def test_meta_status_is_left_out_for_roles_that_cannot_read_it(app):
    add_user(app, 'viewer')
    viewer = client(app, 'viewer')
    member = entry(viewer, 'member')
    # The viewer cannot read the State, so it gets no state schema (and so no badge or bar).
    assert 'state' not in member and 'status' not in member


def test_calculated_status_follows_its_inputs_and_is_never_written(app):
    c = client(app)
    status, created, _ = c('POST', '/api/bill', {'title': 'Rent', 'total': '100.00', 'paid': '0.00'})
    assert status == 201 and created['payment_status'] == 'unpaid'
    url = f'/api/bill/{created["id"]}'
    status, updated, _ = c('PATCH', url, {'paid': '40.00'}, headers=etag(c, url))
    assert status == 200 and updated['payment_status'] == 'partly_paid'
    assert c('PATCH', url, {'paid': '100.00'}, headers=etag(c, url))[1]['payment_status'] == 'paid'
    # Nobody sets it by hand.
    assert c('PATCH', url, {'payment_status': 'unpaid'}, headers=etag(c, url))[0] in (403, 422)
    assert c('GET', url)[1]['payment_status'] == 'paid'


def test_moves_name_what_blocks_them(app):
    c = client(app)
    status, member, _ = c('POST', '/api/member', {'title': 'Sam'})
    assert status == 201
    assert c('GET', f'/api/member/{member["id"]}')[1]['_moves'] == [{'key': 'left', 'to': 'left', 'label': 'Mark as left', 'allowed': True}]
    # A model callers only read: its moves are listed but never allowed.
    with app.business.database.transaction() as tx:
        record = tx.create(Ledger, title='Q1')
    moves = c('GET', f'/api/ledger/{record.id}')[1]['_moves']
    assert moves == [{'key': 'closed', 'to': 'closed', 'label': 'Close', 'allowed': False, 'reason': 'You cannot change this record'}]


def test_moves_in_a_closed_period_are_blocked(tmp_path):
    from datetime import date

    from evlet import Date
    from test_periods import lock

    class Claim(Model):
        permissions = ALL

        class Meta:
            period = 'posted_on'

        title = Text(required=True)
        posted_on = Date()
        stage = State(
            State.Step('draft'),
            State.Step('submitted'),
            rules=(
                State.Rule('draft', 'submitted', 'Submit', key='submitted'),
            ), show='bar')

    app = serve(Database(tmp_path / 'closed.sqlite3', Registry(Claim)))
    c = client(app)
    old = c('POST', '/api/claim', {'title': 'Old', 'posted_on': '2026-01-10'})[1]
    new = c('POST', '/api/claim', {'title': 'New', 'posted_on': '2026-03-10'})[1]
    lock(app, date(2026, 1, 31))
    assert c('GET', f'/api/claim/{old["id"]}')[1]['_moves'] == [
        {'key': 'submitted', 'to': 'submitted', 'label': 'Submit', 'allowed': False, 'reason': 'This record is in a closed period'}]
    assert c('GET', f'/api/claim/{new["id"]}')[1]['_moves'][0]['allowed'] is True


def test_playground_invoice_shows_a_calculated_payment_status(tmp_path):
    from examples.playground.models import registry

    app = serve(Database(tmp_path / 'playground.sqlite3', registry))
    c = client(app)
    invoice = entry(c, 'invoice')
    assert invoice['status'] == 'payment_status'
    field = next(item for item in invoice['fields'] if item['name'] == 'payment_status')
    assert field['read_only'] and field['colors'] == {'unpaid': 'rose', 'partly_paid': 'amber', 'paid': 'green'}
    customer = c('POST', '/api/customer', {'customer_name': 'Al Sahra'})[1]
    status, created, _ = c('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'PO-1', 'total': '100.00'})
    assert status == 201 and created['payment_status'] == 'unpaid'
    url = f'/api/invoice/{created["id"]}'
    assert c('PATCH', url, {'amount_paid': '40.00'}, headers=etag(c, url))[1]['payment_status'] == 'partly_paid'
    assert c('PATCH', url, {'amount_paid': '100.00'}, headers=etag(c, url))[1]['payment_status'] == 'paid'
    assert c('PATCH', url, {'payment_status': 'unpaid'}, headers=etag(c, url))[0] == 403


def test_a_legacy_formula_may_calculate_a_select_but_only_its_choices(tmp_path):
    class Ticket(Model):
        permissions = ALL
        hours = Currency(default=0)
        size = Select(('small', 'large'), compute="'large' if hours > 8 else 'small'")
        odd = Select(('a', 'b'), compute="'c' if hours > 100 else 'a'")

    app = serve(Database(tmp_path / 'legacy.sqlite3', Registry(Ticket)))
    c = client(app)
    status, body, _ = c('POST', '/api/ticket', {'hours': '10.00'})
    assert status == 201 and body['size'] == 'large' and body['odd'] == 'a'
    assert c('POST', '/api/ticket', {'hours': '200.00'})[:2] == (422, {'error': 'Validation failed', 'fields': {'odd': 'Choose one of: a, b'}})
