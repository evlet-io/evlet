"""Reactive help text: help_text="per {uom}" names fields the browser fills in from the record as it changes.
A Table row shows it as subtext under the cell; a form field as its help. Checked when the class is defined."""

import pytest

from evlet import Child, Currency, Decimal, Link, Model, Registry, Select, Table, Text
from evlet.fields import help_references
from test_business import client, hashed  # noqa: F401
from test_controls import add_user, password, save  # noqa: F401
from test_customization import build, models


class Product(Model):
    code = Text(required=True)


class Line(Child):
    product = Link(Product)
    uom = Select(['box', 'piece'])
    qty = Decimal(scale=2, help_text='How many units')
    rate = Currency(currency='AED', help_text='per {uom} in {parent.currency}')


class Order(Model):
    currency = Text(help_text='Shown on {reference}')
    reference = Text()
    lines = Table(Line)


def fields_of(schema, name):
    return {field['name']: field for field in next(model for model in schema['models'] if model['name'] == name)['fields']}


def test_references_are_field_names_and_double_braces_are_literal():
    assert help_references('per {uom} in {parent.currency}') == ('uom', 'parent.currency')
    assert help_references('JSON {{"a": 1}} for {item} and {item}') == ('item',)
    assert help_references('Plain help.') == ()
    for text, message in (('a { b', "stray '{'"), ('a } b', "stray '}'"), ('{b c}', 'must name a field'),
                          ('{parent.a.b}', 'must name a field'), ('{}', 'must name a field')):
        with pytest.raises(ValueError, match=message):
            Text(help_text=text)


def test_unknown_fields_fail_when_the_class_is_defined():
    with pytest.raises(ValueError, match=r"Bad\.a: help_text names \{uom\}, but Bad has no field 'uom'"):
        class Bad(Model):
            a = Text(help_text='per {uom}')
    with pytest.raises(ValueError, match=r'Top\.a: help_text names a parent\. field, but Top is not a Table row'):
        class Top(Model):
            a = Text(help_text='{parent.currency}')

    class Row(Child):
        a = Text(help_text='in {parent.nope}')
    with pytest.raises(ValueError, match=r"Row\.a: help_text names \{parent\.nope\}, but Owner has no field 'nope'"):
        class Owner(Model):
            currency = Text()
            rows = Table(Row)


def test_the_schema_names_the_fields_a_help_text_reads():
    schema = Registry(Order, Product).ui_schema()
    line, order = fields_of(schema, 'order_lines'), fields_of(schema, 'order')
    assert line['rate']['help_text'] == 'per {uom} in {parent.currency}'
    assert line['rate']['help_fields'] == ['uom', 'parent.currency']
    assert order['currency']['help_fields'] == ['reference']
    # Plain help text names no fields, so the browser shows it as it is and keeps it out of the cells.
    assert line['qty']['help_text'] == 'How many units' and 'help_fields' not in line['qty']


def test_the_playground_quick_invoice_rate_reads_per_unit():
    from examples.playground.prompts import QuickInvoice
    rate = QuickInvoice.Line.__app_fields__['rate']
    assert rate.help_text == 'per {uom}' and help_references(rate.help_text) == ('uom',)
    assert help_references(QuickInvoice.__app_fields__['total'].help_text) == ('customer',)


def test_controls_check_help_text_references(tmp_path, hashed):
    app = build(tmp_path, *models())
    add_user(app, 'admin', 'admin')
    admin = client(app)
    status, payload, _ = save(admin, ('field', 'Lead.title', {'help_text': 'For {nope}'}))
    assert status == 422, payload
    assert 'nope' in str(payload)
    status, payload, _ = save(admin, ('field', 'Lead.title', {'help_text': 'For {title}'}))
    assert status == 200, payload
    lead = fields_of(admin('GET', '/api/schema')[1], 'lead')
    assert lead['title']['help_text'] == 'For {title}' and lead['title']['help_fields'] == ['title']
