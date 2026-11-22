"""A new record that starts from other values arrives calculated: real SQLite, through the ASGI routes.

An action's Outcome(new=(model, values)) carries the new record as /api/_draft/<model> works it out for the
signed-in user: fetches from the linked records as they are now, derive() and formulas, with the values the action
gave kept (docs/prompts.md#outcome). More > Duplicate sends its copy through /api/_draft once when the new
record opens (docs/ui.md#duplicate); the browser side is in frontend/src/evlet/tests/record.test.ts."""

from decimal import Decimal as D

import pytest

from evlet import Child, Currency, Database, Decimal, Integer, Link, Model, Outcome, Registry, Rule, Table, Text, User, action
from evlet.calc import F, coalesce
from finite_fixtures import FiniteMeta
from support import client
from test_rules_types import add_user, start

ADMIN = User(1, 'admin', 'admin')
# The viewer may work on enquiries (run their actions) but only read quotations; the editor may not read the margin note.
SALES = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
         'viewer': Rule(('read', 'update'))}


class Item(Model):
    permissions = SALES
    title = Text(required=True)
    description = Text(default='')
    pack_size = Decimal(scale=2, default=1)
    price = Currency(currency='AED', default=0)


class QuotationLine(Child):
    item = Link(Item)
    # Fetched from the item when the line's item is set; a person may change it.
    description = Text(initial=F('item.description'))
    rate = Currency(currency='AED', initial=F('item.price'))
    qty = Decimal(scale=2)
    # Set by derive() from the item: the quantity control needs it.
    pack_steps = Decimal(scale=2, derived=True)
    amount = Currency(currency='AED', compute=coalesce(F('qty'), 0) * coalesce(F('rate'), 0))


class Quotation(Model):
    Meta = FiniteMeta
    permissions = {**SALES, 'viewer': Rule(('read',)), 'editor': Rule(('read', 'create', 'update'), read_fields=(
        'title', 'line_count'), write_fields=('title',))}
    title = Text(default='')
    margin_note = Text(default='')
    lines = Table(QuotationLine)
    line_count = Integer(derived=True)

    def derive(self):
        session = self.context.session
        for line in self.lines:
            line.pack_steps = None if line.item is None else session.get(Item, line.item).pack_size
        self.line_count = len(self.lines)


class EnquiryLine(Child):
    item = Link(Item)
    qty = Decimal(scale=2)
    rate = Currency(currency='AED')


class Enquiry(Model):
    permissions = SALES
    title = Text(default='')
    lines = Table(EnquiryLine)

    @action('New quotation', roles=('admin', 'editor', 'viewer'))
    def new_quotation(self):
        lines = [{'item': line.item, 'qty': line.qty, **({'rate': line.rate} if line.rate is not None else {})} for line in self.lines]
        return Outcome(new=('quotation', {'title': f'From {self.title}', 'margin_note': 'tight', 'lines': lines}))


@pytest.fixture
def app(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Item, Quotation, Enquiry)))
    with app.business.context(ADMIN) as context:
        app.ids = {'pen': context.create(Item, title='Pen', description='Blue pen', pack_size='12', price='2.50').id,
                   'pad': context.create(Item, title='Pad', description='A4 pad', pack_size='5', price='4').id}
    yield app
    app.database.close()


def enquiry(app, *lines):
    with app.business.context(ADMIN) as context:
        return context.create(Enquiry, title='ENQ-1', children={'lines': list(lines)}).id


def run(c, enquiry_id):
    url = f'/api/enquiry/{enquiry_id}'
    status, result, _ = c('POST', f'{url}/actions/new_quotation', {}, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert status == 200, result
    return result['_outcome']['new']


def test_an_outcome_new_carries_derived_fetched_and_calculated_values(app):
    enquiry_id = enquiry(app, {'item': app.ids['pen'], 'qty': '24'}, {'item': app.ids['pad'], 'qty': '3', 'rate': '3.75'})
    new = run(client(app), enquiry_id)
    assert new['model'] == 'quotation'
    values = new['values']
    assert values['title'] == 'From ENQ-1' and values['line_count'] == 2
    pen, pad = values['children']['lines']
    # derive() set pack_steps; the description and rate were fetched from the item; amount is the formula's.
    assert pen['pack_steps'] == '12.00' and pen['description'] == 'Blue pen' and pen['rate'] == '2.50' and pen['amount'] == '60.00'
    # The quantity the action passed is kept, and so is a rate it passed instead of the item's price.
    assert pen['qty'] == '24.00' and pad['qty'] == '3.00'
    assert pad['rate'] == '3.75' and pad['amount'] == '11.25' and pad['pack_steps'] == '5.00'
    # Only a new record's values: no preview bookkeeping, no empty fields.
    assert not {'id', '_key', '_sources'} & set(values) and not {'id', '_key', '_sources'} & set(pen)


def test_fetched_values_come_from_the_linked_record_as_it_is_now(app):
    enquiry_id = enquiry(app, {'item': app.ids['pen'], 'qty': '12'})
    # The item changed after the enquiry was saved: the new quotation reads it as it is now.
    with app.business.context(ADMIN) as context:
        context.update(Item, app.ids['pen'], description='Blue pen 0.7 mm', pack_size='10', price='3')
    line = run(client(app), enquiry_id)['values']['children']['lines'][0]
    assert line['description'] == 'Blue pen 0.7 mm' and line['pack_steps'] == '10.00' and line['rate'] == '3.00' and line['amount'] == '36.00'


def test_the_new_record_holds_only_fields_the_user_may_read(app):
    enquiry_id = enquiry(app, {'item': app.ids['pen'], 'qty': '1'})
    new = run(add_user(app, 'editor'), enquiry_id)
    assert 'margin_note' not in new['values'] and new['values']['line_count'] == 1
    assert new['values']['children']['lines'][0]['pack_steps'] == '12.00'


def test_a_user_who_may_not_create_the_record_gets_the_values_as_given(app):
    # As /api/_draft/quotation refuses this user, the outcome is not calculated; the save would refuse it too.
    enquiry_id = enquiry(app, {'item': app.ids['pen'], 'qty': '2'})
    c = add_user(app, 'viewer')
    assert c('POST', '/api/_draft/quotation', {'values': {}})[0] == 403
    values = run(c, enquiry_id)['values']
    assert values['children']['lines'] == [{'item': str(app.ids['pen']), 'qty': '2.00'}]
    assert 'pack_steps' not in values['children']['lines'][0]


def test_a_duplicate_draft_recalculates_the_copy_and_keeps_the_typed_values(app):
    """Duplicate sends its copy (derived values left out, as the browser copies) through /api/_draft when the copy
    opens: derived values come back, fetched ones are read again, and values the person typed stay."""
    c = client(app)
    status, saved, _ = c('POST', '/api/quotation', {'title': 'Q1', 'children': {'lines': [
        {'item': str(app.ids['pen']), 'qty': '24', 'rate': '2.20', 'description': 'Pens, gift box'}]}})
    assert status == 201, saved
    with app.business.context(ADMIN) as context:
        context.update(Item, app.ids['pen'], pack_size='6')
    copy = {'title': 'Q1', 'children': {'lines': [{'_key': 'n1', 'item': str(app.ids['pen']), 'qty': '24', 'rate': '2.20',
                                                    'description': 'Pens, gift box'}]}}
    status, draft, _ = c('POST', '/api/_draft/quotation', {'values': copy})
    assert status == 200, draft
    line = draft['values']['children']['lines'][0]
    assert line['pack_steps'] == '6.00' and line['qty'] == '24.00' and line['rate'] == '2.20' and line['amount'] == '52.80'
    assert line['description'] == 'Pens, gift box' and draft['values']['line_count'] == 1
    assert D(line['amount']) == D('24') * D('2.20')
