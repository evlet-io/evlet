"""Formula helpers build the same nodes as the long form, so plans, hashes and both engines are unchanged."""

import pytest

from evlet import Model, Child, Table, Decimal, Boolean, Select, Registry
from evlet.calc import (D, F, row, when, choose, isin, is_empty, has_value, least, greatest, absolute, Document,
                        CalcError, formula_hash)


class FiniteMeta:
    calculation_language = 'finite-v1'


def expressions(model):
    """{field: expression} of a model's plan."""
    plan = Registry(model).plans[model]
    return {entry['field']: entry['expression'] for entry in plan['fields'] if not entry.get('kind')}


def test_each_helper_builds_the_same_formula_as_its_long_form():
    class Pairs(Model):
        Meta = FiniteMeta
        stage = Select(('lead', 'won', 'lost'))
        a = Decimal()
        b = Decimal()
        choose_short = Decimal(compute=choose(F('stage'), {'won': 100, 'lost': 0}, default=10))
        choose_long = Decimal(compute=when(F('stage') == 'won', D('100'), when(F('stage') == 'lost', D('0'), D('10'))))
        isin_short = Boolean(compute=F('stage').isin('won', 'lost'))
        isin_long = Boolean(compute=(F('stage') == 'won') | (F('stage') == 'lost'))
        empty_short = Boolean(compute=is_empty(F('a')))
        empty_long = Boolean(compute=F('a') == None)  # noqa: E711 - the long form being compared
        value_short = Boolean(compute=has_value(F('a')))
        value_long = Boolean(compute=F('a') != None)  # noqa: E711 - the long form being compared
        least_short = Decimal(compute=least(F('a'), F('b')))
        least_long = Decimal(compute=when(F('a') <= F('b'), F('a'), F('b')))
        greatest_short = Decimal(compute=greatest(F('a'), F('b')))
        greatest_long = Decimal(compute=when(F('a') >= F('b'), F('a'), F('b')))
        absolute_short = Decimal(compute=absolute(F('a')))
        absolute_long = Decimal(compute=when(F('a') < 0, -F('a'), F('a')))

    found = expressions(Pairs)
    for name in ('choose', 'isin', 'empty', 'value', 'least', 'greatest', 'absolute'):
        assert found[name + '_short'] == found[name + '_long'], name


def test_a_formula_rewritten_with_helpers_keeps_its_hash():
    class Before(Model):
        Meta = FiniteMeta
        stage = Select(('lead', 'won'))
        closed_on = Decimal()
        lines = Table(type('BeforeLine', (Child,), {'repeat': Boolean(default=False), 'amount': Decimal()}))
        score = Decimal(compute=when(F('stage') == 'won', D('100'), when(F('stage') == 'lead', D('10'), D('0'))))
        late = Boolean(compute=F('closed_on') == None)  # noqa: E711 - the old spelling
        repeated = Decimal(compute=lines.where(row.repeat == True).sum('amount'))  # noqa: E712 - the old spelling

    class After(Model):
        Meta = FiniteMeta
        stage = Select(('lead', 'won'))
        closed_on = Decimal()
        lines = Table(type('AfterLine', (Child,), {'repeat': Boolean(default=False), 'amount': Decimal()}))
        score = Decimal(compute=choose(F('stage'), {'won': 100, 'lead': 10}, default=0))
        late = Boolean(compute=is_empty(F('closed_on')))
        repeated = Decimal(compute=lines.where(repeat=True).sum('amount'))

    before, after = Registry(Before).plans[Before], Registry(After).plans[After]
    assert formula_hash(before) == formula_hash(after)


def test_helpers_evaluate_like_their_long_form():
    class Values(Model):
        Meta = FiniteMeta
        stage = Select(('lead', 'won', 'lost'))
        a = Decimal()
        b = Decimal()
        weight = Decimal(compute=choose(F('stage'), {'won': 100, 'lost': 0}, default=10))
        smaller = Decimal(compute=least(F('a'), F('b'), 5))
        larger = Decimal(compute=greatest(F('a'), F('b')))
        gap = Decimal(compute=absolute(F('a') - F('b')))

    plan = Registry(Values).plans[Values]
    snap = Document(plan, {'stage': 'lost', 'a': '3', 'b': '-7.5'}).snapshot()
    assert (snap['weight'], snap['smaller'], snap['larger'], snap['gap']) == ('0.0000', '-7.5000', '3.0000', '10.5000')
    # An empty choice falls to the default; an empty number leaves the result pending, as in any formula.
    snap = Document(plan, {'a': '3'}).snapshot()
    assert snap['weight'] == '10.0000' and snap['_states']['smaller']['state'] == 'pending'


def test_helpers_refuse_what_they_cannot_express():
    for attempt in (lambda: choose(F('stage'), {}), lambda: choose(F('stage'), [('won', 1)]),
                    lambda: choose(F('stage'), {None: 1}), lambda: isin(F('stage'))):
        with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
            attempt()
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        choose(F('stage'), {1.5: 1})
