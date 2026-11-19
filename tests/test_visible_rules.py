"""visible_when on fields, and applies_when / visible_when on Table, on real SQLite."""

import pytest

from evlet import Child, Currency, Decimal, Model, Registry, Select, Table, Text
from evlet.calc import CalcError, Document, F
from finite_fixtures import FiniteMeta
from test_business import client
from test_rules_types import ALL, application, patch


def no_bad(value):
    if value == 'bad':
        raise ValueError('Not allowed')


class PayLine(Child):
    label = Text(default='')
    amount = Currency(default=0)


class Claim(Model):
    Meta = FiniteMeta
    permissions = ALL
    kind = Select(('cash', 'card', 'split'), default='cash')
    card_ref = Text(visible_when=F('kind') == 'card', validator=no_bad)
    splits = Table(PayLine, applies_when=F('kind') == 'split')
    extras = Table(PayLine, visible_when=F('kind') != 'cash')
    split_total = Currency(compute=splits.sum('amount'))
    extra_total = Currency(compute=extras.sum('amount'))


@pytest.fixture
def claims(tmp_path):
    app = application(tmp_path, Claim)
    return app, client(app)


# The error finance_v1 reported: these options on a Table were not accepted at all.

def test_a_table_takes_applies_when_and_visible_when():
    table = Table(PayLine, applies_when=F('kind') == 'split', visible_when=F('kind') != 'cash')
    assert table.applies_when is not None and table.visible_when is not None
    Registry(Claim)


# Definition checks

def test_visible_when_refuses_combinations_that_hide_a_required_value():
    for options, message in (({'required': True}, 'must stay visible'), ({'required_when': F('b')}, 'must stay visible'),
                             ({'hidden': True}, 'never shown'), ({'visible_if': 'b'}, 'not both'),
                             ({}, None)):
        if message is None:
            Text(visible_when=F('b'), **options)
            continue
        with pytest.raises(ValueError, match=message):
            Text(visible_when=F('b'), **options)
    with pytest.raises(ValueError, match='finite expression'):
        Text(visible_when='kind == "card"')
    # With applies_when and locked_when it is allowed: shown only while both hold.
    Text(visible_when=F('b'), applies_when=F('c'), locked_when=F('d'))


def test_table_rules_refuse_required_rows_derived_rows_and_reading_their_own_rows():
    with pytest.raises(ValueError, match='may not apply'):
        Table(PayLine, required=True, applies_when=F('kind') == 'split')
    with pytest.raises(ValueError, match='may not apply'):
        Table(PayLine, min_rows=2, applies_when=F('kind') == 'split')
    with pytest.raises(ValueError, match='must stay visible'):
        Table(PayLine, min_rows=1, visible_when=F('kind') == 'split')
    with pytest.raises(ValueError, match='derive'):
        Table(PayLine, derived=True, applies_when=F('kind') == 'split')
    with pytest.raises(ValueError, match='finite expression'):
        Table(PayLine, visible_when=lambda record: True)

    class Loop(Model):
        Meta = FiniteMeta
        permissions = ALL
        rows = Table(PayLine, applies_when=F('kind') == 'x')
        kind = Text(default='')
    Registry(Loop)

    class Own(Model):
        Meta = FiniteMeta
        permissions = ALL
        rows = Table(PayLine)
        rows.applies_when = rows.count() > 0
    with pytest.raises(CalcError, match='cannot read its own rows'):
        Registry(Own)

    class Typed(Model):
        Meta = FiniteMeta
        permissions = ALL
        kind = Text(default='')
        rows = Table(PayLine, visible_when=F('kind'))
    with pytest.raises(CalcError, match='Boolean'):
        Registry(Typed)


# visible_when keeps values

def test_visible_when_hides_but_keeps_saves_and_validates_the_value(claims):
    app, c = claims
    status, claim, _ = c('POST', '/api/claim', {'card_ref': 'R-1'})
    assert status == 201 and claim['card_ref'] == 'R-1'  # Hidden (cash), kept anyway.
    url = f"/api/claim/{claim['id']}"
    assert patch(c, url, {'kind': 'card'})[1]['card_ref'] == 'R-1'
    assert patch(c, url, {'kind': 'cash'})[1]['card_ref'] == 'R-1'
    # Its validator still runs while it is hidden.
    status, result, _ = patch(c, url, {'card_ref': 'bad'})
    assert status == 422 and result['fields'] == {'card_ref': 'Not allowed'}
    document = Document(app.registry.plans[Claim], {'kind': 'cash', 'card_ref': 'x'})
    states = document.rule_states(document.root.handle)
    assert states == {'card_ref': {'applies': True, 'required': False, 'locked': False, 'visible': False}}


def test_rule_states_show_undecided_visible_when_fields():
    class Gate(Model):
        Meta = FiniteMeta
        permissions = ALL
        level = Decimal()
        note = Text(visible_when=F('level') > 3)

    plan = Registry(Gate).plans[Gate]
    document = Document(plan, {})
    assert document.rule_states(document.root.handle)['note']['visible'] is True  # level is empty: undecided shows it
    document.set(document.root.handle, 'level', '2')
    assert document.rule_states(document.root.handle)['note']['visible'] is False
    document.set(document.root.handle, 'level', '5')
    assert document.rule_states(document.root.handle)['note']['visible'] is True


# Table rules

def test_a_table_that_does_not_apply_reads_empty_and_loses_its_rows_on_save(claims):
    app, c = claims
    status, claim, _ = c('POST', '/api/claim', {'kind': 'split', 'children': {'splits': [{'label': 'A', 'amount': '4.00'}, {'label': 'B', 'amount': '6.00'}]}})
    assert status == 201, claim
    assert (len(claim['children']['splits']), claim['split_total']) == (2, '10.00')
    url = f"/api/claim/{claim['id']}"
    cash = patch(c, url, {'kind': 'cash'})[1]
    assert cash['children']['splits'] == [] and cash['split_total'] == '0.00'
    assert c('GET', url)[1]['children']['splits'] == []
    rows = next(child.model for child in app.registry.children if child.parent is Claim and child.name == 'splits')
    assert app.database.list(rows) == []
    # Rows sent while it does not apply are dropped too, and a preview reads it as empty.
    created = c('POST', '/api/claim', {'kind': 'card', 'children': {'splits': [{'label': 'A', 'amount': '9.00'}]}})[1]
    assert created['children']['splits'] == [] and created['split_total'] == '0.00'
    preview = c('POST', '/api/_draft/claim', {'values': {'kind': 'card', 'children': {'splits': [{'_key': 'a', 'amount': '9.00'}]}}})[1]
    assert preview['values']['split_total'] == '0.00'


def test_a_hidden_table_keeps_its_rows(claims):
    app, c = claims
    claim = c('POST', '/api/claim', {'kind': 'card', 'children': {'extras': [{'label': 'Tip', 'amount': '2.00'}]}})[1]
    url = f"/api/claim/{claim['id']}"
    cash = patch(c, url, {'kind': 'cash'})[1]
    assert len(cash['children']['extras']) == 1 and cash['extra_total'] == '2.00'


def test_table_states_follow_the_owner_row_live():
    plan = Registry(Claim).plans[Claim]
    document = Document(plan, {'kind': 'split', 'children': {'splits': [{'_key': 'a', 'amount': '3.00'}]}})
    root = document.root.handle
    assert document.table_states(root) == {'extras': {'applies': True, 'visible': True}, 'splits': {'applies': True, 'visible': True}}
    assert document.snapshot()['split_total'] == '3.00'
    document.set(root, 'kind', 'cash')
    assert document.table_states(root) == {'extras': {'applies': True, 'visible': False}, 'splits': {'applies': False, 'visible': False}}
    # Calculations read a Table that does not apply as empty; the rows stay in the session until a save.
    assert document.snapshot()['split_total'] == '0.00' and len(document.snapshot()['children']['splits']) == 1
    document.set(root, 'kind', 'split')
    assert document.snapshot()['split_total'] == '3.00'
    # Table rules are not field rules.
    assert 'splits' not in document.rule_states(root)


def test_rows_of_a_table_that_does_not_apply_are_not_checked(claims):
    app, c = claims
    status, claim, _ = c('POST', '/api/claim', {'kind': 'cash', 'children': {'splits': [{'label': 'x' * 300}]}})
    assert status == 201, claim
    assert claim['children']['splits'] == []


def test_visible_when_may_hide_a_calculated_result(tmp_path):
    class Quote(Model):
        Meta = FiniteMeta
        permissions = ALL
        qty = Decimal(default=1)
        discount = Decimal(default=0)
        saving = Decimal(compute=F('qty') * F('discount'), visible_when=F('discount') > 0)

    app = application(tmp_path, Quote)
    c = client(app)
    assert c('POST', '/api/quote', {'qty': '2', 'discount': '3'})[1]['saving'] == '6.0000'
    document = Document(app.registry.plans[Quote], {'qty': '2', 'discount': '0'})
    assert document.rule_states(document.root.handle)['saving']['visible'] is False
    entry = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'quote')
    assert next(field for field in entry['fields'] if field['name'] == 'saving')['visible_when'] is not None


def test_finance_v1_report_a_quote_with_rules_on_its_table_and_a_field(tmp_path):
    """The model finance_v1 reported: each line raised TypeError: unexpected keyword argument on 0.4.13."""
    from evlet import Boolean

    class Quote(Model):
        permissions = ALL
        customer = Text(max_length=120, visible_when=F('itemised'))
        itemised = Boolean(default=True)

        class Line(Child):
            note = Text(max_length=80)
            rate = Currency(default=0)
        lines = Table(Line, applies_when=F('itemised'))
        notes = Table(Line, visible_when=F('itemised'))

    app = application(tmp_path, Quote)
    c = client(app)
    quote = c('POST', '/api/quote', {'customer': 'Acme', 'children': {'lines': [{'note': 'a', 'rate': '5'}], 'notes': [{'note': 'b'}]}})[1]
    assert len(quote['children']['lines']) == 1
    plain = patch(c, f"/api/quote/{quote['id']}", {'itemised': False})[1]
    assert plain['children']['lines'] == [] and len(plain['children']['notes']) == 1 and plain['customer'] == 'Acme'
