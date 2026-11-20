"""A suggestion (initial=) never overwrites what the caller set (docs/finite-calculations.md).

An input has changed in a write only when its value after the write differs from the saved one, and a value the
caller sent in the write always stays, whatever fires. Through context.create and context.update on real SQLite,
on a document and on a Table row; the browser engine runs the same rule (frontend/src/evlet/tests/finite-*.test.ts)."""

from datetime import date

import pytest

from evlet import Child, Currency, Database, Date, Integer, Link, Model, Outcome, Prompt, Registry, Select, Table, Text, User, action
from evlet.calc import F, coalesce, today
from finite_fixtures import FiniteMeta
from test_rules_types import ALL, start

ADMIN = User(1, 'admin', 'admin')
MODES = ('percent', 'amount')


class Item(Model):
    permissions = ALL
    price = Currency(currency='AED', default=0)


class Company(Model):
    permissions = ALL
    mode = Select(MODES)


class Customer(Model):
    permissions = ALL
    company = Link(Company)


class Line(Child):
    item = Link(Item)
    note = Text(default='')
    price = Currency(currency='AED', compute=coalesce(F('item.price'), 0))
    direct = Currency(currency='AED', initial=F('item.price'))
    via_calc = Currency(currency='AED', initial=F('price'))


class Doc(Model):
    Meta = FiniteMeta
    permissions = ALL
    note = Text(default='')
    item = Link(Item)
    price = Currency(currency='AED', compute=coalesce(F('item.price'), 0))
    direct = Currency(currency='AED', initial=F('item.price'))
    via_calc = Currency(currency='AED', initial=F('price'))
    customer = Link(Customer)
    company = Link(Company, initial=F('customer.company'))
    mode = Select(MODES, initial=coalesce(F('company.mode'), 'percent'))
    lines = Table(Line)
    # Set by derive() on every write; a suggestion that reads it follows it only when it really changes.
    line_count = Integer(derived=True)
    suggested_count = Integer(initial=F('line_count'))

    def derive(self):
        self.line_count = len(self.lines)


@pytest.fixture
def app(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Item, Company, Customer, Doc)))
    with app.business.context(ADMIN) as context:
        app.ids = {'item': context.create(Item, price='36').id, 'other': context.create(Item, price='40').id,
                   'company': context.create(Company, mode='amount').id}
        app.ids['customer'] = context.create(Customer, company=app.ids['company']).id
    yield app
    app.database.close()


def write(app, record_id=None, **values):
    with app.business.context(ADMIN) as context:
        record = context.create(Doc, **values) if record_id is None else context.update(Doc, record_id, **values)
        return context.get(Doc, record.id if record_id is None else record_id)


def pair(record):
    return str(record.direct), str(record.via_calc)


def row(record):
    line = record.lines[0]
    return str(line.direct), str(line.via_calc)


def test_a_suggestion_that_reads_a_calculated_field_keeps_what_was_typed(app):
    item = app.ids['item']
    doc = write(app, item=item, children={'lines': [{'item': item}]})
    assert pair(doc) == ('36.00', '36.00') and row(doc) == ('36.00', '36.00')
    line = doc.lines[0].id
    typed = write(app, doc.id, direct='35', via_calc='35', children={'lines': [{'id': line, 'direct': '35', 'via_calc': '35'}]})
    assert pair(typed) == ('35.00', '35.00') and row(typed) == ('35.00', '35.00')
    # Saved again with only a note changed: nothing the suggestions read changed, so both stay.
    again = write(app, doc.id, note='later', children={'lines': [{'id': line, 'note': 'later'}]})
    assert pair(again) == ('35.00', '35.00') and row(again) == ('35.00', '35.00')
    # Created with a value sent in both: the caller's value stays.
    sent = write(app, item=item, direct='30', via_calc='30', children={'lines': [{'item': item, 'direct': '30', 'via_calc': '30'}]})
    assert pair(sent) == ('30.00', '30.00') and row(sent) == ('30.00', '30.00')


def test_a_suggestion_still_follows_an_input_that_really_changed(app):
    doc = write(app, item=app.ids['item'], direct='35', via_calc='35', children={'lines': [{'item': app.ids['item'], 'via_calc': '35'}]})
    moved = write(app, doc.id, item=app.ids['other'], children={'lines': [{'id': doc.lines[0].id, 'item': app.ids['other']}]})
    assert pair(moved) == ('40.00', '40.00') and row(moved)[1] == '40.00'


def test_a_suggestion_through_a_suggested_link_keeps_the_callers_value(app):
    customer, company = app.ids['customer'], app.ids['company']
    assert write(app, customer=customer, mode='percent').mode == 'percent'
    sent = write(app, customer=customer, mode='amount')
    assert (sent.company, sent.mode) == (company, 'amount')
    # Without a value sent, the suggestion follows the company the customer suggested.
    with app.business.context(ADMIN) as context:
        context.update(Company, company, mode='percent')
    assert write(app, customer=customer).mode == 'percent'
    assert write(app, customer=customer, company=company, mode='amount').mode == 'amount'


def test_a_suggestion_over_a_derived_input_keeps_what_was_typed(app):
    doc = write(app, children={'lines': [{}]})
    assert (doc.line_count, doc.suggested_count) == (1, 1)
    typed = write(app, doc.id, suggested_count=5)
    assert write(app, doc.id, note='again').suggested_count == 5 == typed.suggested_count
    # The derived input really changes when a line is added: then the suggestion follows it.
    added = write(app, doc.id, children={'lines': [{'id': doc.lines[0].id}, {}]})
    assert (added.line_count, added.suggested_count) == (2, 2)
    assert write(app, suggested_count=9, children={'lines': [{}]}).suggested_count == 9


# ---------- derive() reads suggestions (docs/model-engine.md#derived-values) ----------

class Party(Model):
    permissions = ALL
    party_name = Text(default='')
    currency = Text(default='AED')

    @action('Charge')
    def charge(self):
        return ChargePrompt


class Charge(Model):
    """derive() reads two suggested fields; `label` suggests from what derive() set, so it runs after it."""
    Meta = FiniteMeta
    permissions = ALL
    party = Link(Party)
    note = Text(default='')
    currency = Text(initial=F('party.currency'))
    charge_date = Date(initial=today())
    seen = Text(derived=True)
    label = Text(initial=F('seen'))

    def derive(self):
        self.seen = f'{self.currency} {self.charge_date}'


class ChargePrompt(Prompt):
    """Opened from a Party; submit() creates a Charge with no suggested values."""
    note = Text()

    def submit(self):
        created = self.context.create(Charge, party=self.source.id, note=self.note)
        return Outcome(data={'seen': self.context.get(Charge, created.id).seen})


@pytest.fixture
def charges(tmp_path):
    app = start(Database(tmp_path / 'charges.sqlite3', Registry(Party, Charge)))
    with app.business.context(ADMIN) as context:
        app.ids = {'aed': context.create(Party, currency='AED').id, 'usd': context.create(Party, currency='USD').id}
    yield app
    app.database.close()


def charge(app, record_id=None, **values):
    with app.business.context(ADMIN) as context:
        record = context.create(Charge, **values) if record_id is None else context.update(Charge, record_id, **values)
        return context.get(Charge, record.id if record_id is None else record_id)


def test_derive_reads_suggestions_on_create(charges):
    day = str(date.today())
    record = charge(charges, party=charges.ids['usd'])
    assert record.seen == f'USD {day}' and record.label == record.seen
    assert (record.currency, str(record.charge_date)) == ('USD', day)
    # Nothing at all: the date suggestion still reaches derive().
    assert charge(charges).seen == f'None {day}'


def test_derive_reads_suggestions_on_a_prompt_submit(charges):
    day = str(date.today())
    with charges.business.context(ADMIN) as context:
        outcome = context.get(Party, charges.ids['usd']).charge({'note': 'from a prompt'})
    assert outcome['data'] == {'seen': f'USD {day}'}


def test_derive_reads_a_suggestion_that_follows_a_changed_input(charges):
    day = str(date.today())
    record = charge(charges, party=charges.ids['aed'])
    assert record.seen == f'AED {day}'
    moved = charge(charges, record.id, party=charges.ids['usd'])
    assert (moved.currency, moved.seen) == ('USD', f'USD {day}')
    # The saved suggestion is not a caller value: the next change of its input fires it again.
    back = charge(charges, record.id, party=charges.ids['aed'])
    assert (back.currency, back.seen) == ('AED', f'AED {day}')
    # A save that changes nothing it reads keeps it.
    assert charge(charges, record.id, note='later').currency == 'AED'


def test_derive_never_replaces_a_callers_value(charges):
    record = charge(charges, party=charges.ids['usd'], currency='EUR', charge_date='2026-01-05')
    assert (record.currency, str(record.charge_date), record.seen) == ('EUR', '2026-01-05', 'EUR 2026-01-05')
    moved = charge(charges, record.id, party=charges.ids['aed'], currency='GBP')
    assert (moved.currency, moved.seen) == ('GBP', 'GBP 2026-01-05')
