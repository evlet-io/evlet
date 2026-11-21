"""Decimal(display_scale=): how a value is shown, never how it is stored (the browser trims; frontend format tests)."""

import pytest

from evlet import Currency, Decimal, Model, Registry


def test_display_scale_reaches_the_screen_schema_and_not_the_stored_definition():
    class Line(Model):
        credited_qty = Decimal(scale=6, display_scale=2)
        plain = Decimal(scale=6)

    fields = {field['name']: field for field in Registry(Line).ui_schema()['models'][0]['fields']}
    assert fields['credited_qty']['display_scale'] == 2 and fields['credited_qty']['scale'] == 6
    assert 'display_scale' not in fields['plain']
    # Migrations compare stored definitions: a display option needs none.
    assert Line.__fields__['credited_qty'].metadata() == Line.__fields__['plain'].metadata() | {'name': 'credited_qty'}


@pytest.mark.parametrize('declare', [lambda: Decimal(scale=2, display_scale=3), lambda: Decimal(display_scale='2'),
                                     lambda: Currency(display_scale=2)])
def test_display_scale_is_checked(declare):
    with pytest.raises(ValueError, match='display_scale is a whole number'):
        declare()
