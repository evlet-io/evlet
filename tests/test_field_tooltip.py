"""Field(tooltip=...): text behind a ? icon at the end of the input, carried like help_text."""

import pytest

from evlet import Currency, Model, Registry, Sequence, Text
from test_business import client, hashed, secured  # noqa: F401
from test_controls import add_user, password, save  # noqa: F401
from test_customization import build, models


class Order(Model):
    reference = Text(help_text='The number on the purchase order', tooltip='Ask the customer if it is missing')
    total = Currency()
    number = Sequence('ORD-{n:04}', tooltip='Assigned when the order is created')


def test_tooltip_is_text_and_reaches_the_schema_only_when_set():
    fields = {field['name']: field for field in Registry(Order).ui_schema()['models'][0]['fields']}
    assert fields['reference']['tooltip'] == 'Ask the customer if it is missing'
    assert fields['reference']['help_text'] == 'The number on the purchase order'
    assert fields['number']['tooltip'] == 'Assigned when the order is created'
    assert 'tooltip' not in fields['total']
    with pytest.raises(ValueError, match='tooltip'):
        Text(tooltip=3)


def test_the_playground_shows_help_text_and_a_tooltip(secured):
    schema = client(secured, 'editor')('GET', '/api/schema')[1]
    invoice = {field['name']: field for field in next(model for model in schema['models'] if model['name'] == 'invoice')['fields']}
    assert invoice['reference']['help_text'] and invoice['reference']['tooltip']


def test_controls_change_a_tooltip_and_the_schema_follows(tmp_path, hashed):
    app = build(tmp_path, *models())
    add_user(app, 'admin', 'admin')
    admin = client(app)
    status, payload, _ = save(admin, ('field', 'Lead.title', {'tooltip': 'Name the deal after the customer'}))
    assert status == 200, payload
    lead = next(model for model in admin('GET', '/api/schema')[1]['models'] if model['name'] == 'lead')
    assert next(field for field in lead['fields'] if field['name'] == 'title')['tooltip'] == 'Name the deal after the customer'
    status, payload, _ = save(admin, ('field', 'Lead.title', {'tooltip': 'x' * 201}))
    assert status == 422
