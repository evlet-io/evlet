"""Naming-series conditions run on the finite-v1 evaluator and decide exactly as the old evaluator did."""

from datetime import date, datetime
from decimal import Decimal as D
import itertools

import pytest

from evlet import Model, Text, Decimal, Integer, Boolean, Select, Date, DateTime, Link
from evlet.identity import RecordId
from evlet.expressions import EMPTY, Parser, parse


class Customer(Model):
    title = Text()


class Record(Model):
    customer = Link(Customer)
    title = Text()
    total = Decimal(scale=2)
    count = Integer()
    paid = Boolean()
    status = Select(('draft', 'sent', 'paid'))
    posting_date = Date()
    stamped = DateTime()


FIELDS = Record.__declared_fields__


# The evaluator naming conditions used before 0.5, kept here as the reference the new one must match.

def old_plain(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, D)):
        return D(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def old_same(actual, expected):
    if expected is EMPTY:
        return actual in (None, '')
    return type(actual) is type(expected) and actual == expected


def old_evaluate(node, values):
    kind = node[0]
    if kind == 'and':
        return old_evaluate(node[1], values) and old_evaluate(node[2], values)
    if kind == 'or':
        return old_evaluate(node[1], values) or old_evaluate(node[2], values)
    if kind == 'not':
        return not old_evaluate(node[1], values)
    actual = old_plain(values.get(node[1]))
    if kind == 'in':
        return any(old_same(actual, item) for item in node[2])
    _, _, op, expected = node
    if op == '==':
        return old_same(actual, expected)
    if op == '!=':
        return not old_same(actual, expected)
    if expected is EMPTY or actual in (None, '') or type(actual) is not type(expected):
        return False
    return {'>': actual > expected, '>=': actual >= expected, '<': actual < expected, '<=': actual <= expected}[op]


CONDITIONS = [
    'total >= 250000', 'total > 1', 'total < 1', 'total <= 0', 'total == 0', 'total != 0', 'total == -5.25',
    'total == 1.0', 'count > 3', 'count == empty', 'count != empty', 'total == empty', 'total != empty',
    'paid == true', 'paid == false', 'paid != true', 'paid == empty',
    'status == "sent"', "status == 'sent'", 'status != "draft"', 'status in ("sent", "paid")', 'status in ("draft")',
    'status in ("sent", empty)', 'status == empty', 'status != empty',
    'title == ""', 'title == empty', 'title != empty', 'title > "m"', 'title <= "b"', 'title in ("a", "b")',
    'posting_date >= "2026-01-01"', 'posting_date < "2026-12-31"', 'posting_date == empty', 'posting_date == "2026-03-01"',
    'stamped > "2026-01-01"', 'stamped == empty',
    'not status == "sent"', 'not (total < 1)', 'not total > 5', 'not not paid == true',
    '(total > 1 or count == 0) and status == "sent"', 'total > 1 and paid == true or status in ("paid")',
    'not (title == empty or posting_date == empty)', 'customer == 7', 'customer != 7', 'customer > 3', 'customer == empty',
]

VALUES = {
    'customer': [None, RecordId(7), RecordId(2)],
    'title': [None, '', 'alpha', 'zed'],
    'total': [None, D('0'), D('0.00'), D('1'), D('-5.25'), D('250000.00')],
    'count': [None, 0, 4],
    'paid': [None, True, False],
    'status': [None, 'draft', 'sent', 'paid'],
    'posting_date': [None, date(2026, 3, 1), date(2025, 12, 31)],
    'stamped': [None, datetime(2026, 5, 1, 9, 30)],
}


def records(limit=400):
    """A spread of records: every value of each field at least once, then combinations."""
    names = list(VALUES)
    for name in names:
        for value in VALUES[name]:
            yield {other: VALUES[other][1 % len(VALUES[other])] for other in names} | {name: value}
    for index, combination in enumerate(itertools.product(*(VALUES[name] for name in names))):
        if index % 7 == 0:
            yield dict(zip(names, combination))
        if index > limit * 7:
            return


@pytest.mark.parametrize('source', CONDITIONS)
def test_a_condition_decides_as_the_old_evaluator_did(source):
    condition, tree = parse(source, FIELDS), Parser(source, FIELDS).parse()
    for values in records():
        assert condition.matches(values) is old_evaluate(tree, values), (source, values)


def test_conditions_compile_to_finite_nodes():
    expression = parse('status in ("sent", "paid") and not total > 5', FIELDS).expression
    assert expression['$expr'] == 'and'
    operations = set()

    def walk(node):
        operations.add(node['$expr'])
        for arg in node.get('args', ()):
            walk(arg)
    walk(expression)
    assert operations <= {'and', 'or', 'not', 'eq', 'gt', 'when', 'ref', 'literal'}


def test_a_value_of_the_wrong_type_never_matches():
    # Values reach conditions cleaned by their fields, so this only guards against misuse.
    assert not parse('total > 1', FIELDS).matches({'total': 'lots'})
    assert not parse('status == "sent"', FIELDS).matches({'status': 7})


def test_a_link_compares_by_its_record_id_number():
    assert parse('customer == 7', FIELDS).matches({'customer': RecordId(7)})
    assert not parse('customer == 7', FIELDS).matches({'customer': RecordId(8)})
    assert parse('customer == empty', FIELDS).matches({'customer': None})
    assert parse('customer != empty', FIELDS).matches({'customer': RecordId(8)})
