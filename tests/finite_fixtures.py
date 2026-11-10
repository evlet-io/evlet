"""Shared finite-v1 scenarios: Python compiles the plans and records the authoritative results.

scripts/generate_finite_fixtures.py writes them for the browser evaluator; the pytest suite
checks that the checked-in JSON still matches. Both runtimes consume the same serialized plan.
"""

import random

from evlet import Model, Child, Table, Decimal, Currency, Boolean, Integer, Text, Date, DateTime, Link, Registry, Select
from evlet.fields import Duration, Percent
from evlet.calc import (D, F, parent, row, round, coalesce, safe_divide, when, days, today, Document, CalcError,
                        parse, text, add, sub, mul, div, quantize)


class FiniteMeta:
    calculation_language = 'finite-v1'
    numeric_profile = 'finite40-hu18-v1'


class InvoiceItem(Child):
    qty = Decimal(default=1, minimum='0')
    rate = Currency(default=0, minimum='0')
    vat_rate = Decimal(scale=4, default=5, minimum='0')
    taxable = Boolean(default=True)
    effective_rate = Decimal(scale=4, stored=False, compute=taxable * vat_rate)
    entered_amount = Currency(compute=round(qty * rate, 2))
    net_raw = Decimal(scale=None, stored=False, compute=entered_amount / (1 + parent.inclusive * effective_rate / 100))
    net = Currency(compute=round(net_raw, 2))
    vat = Currency(compute=parent.inclusive * (entered_amount - net) + (1 - parent.inclusive) * round(net * effective_rate / 100, 2))
    total = Currency(compute=net + vat)


class Invoice(Model):
    Meta = FiniteMeta
    inclusive = Boolean(default=True)
    items = Table(InvoiceItem)
    net_total = Currency(compute=items.sum('net'))
    vat_total = Currency(compute=items.sum('vat'))
    taxable_net = Currency(compute=items.where(taxable=True).sum('net'))
    grand_total = Currency(compute=items.sum('total'))
    line_count = Integer(compute=items.count())


class WorkItem(Child):
    hours = Decimal(default=0)
    amount = Currency(compute=round(hours * parent.hourly_rate, 2))


class Phase(Child):
    name = Text(default='')
    hourly_rate = Currency(compute=parent.hourly_rate, stored=False)
    tasks = Table(WorkItem)
    hours = Decimal(compute=tasks.sum('hours'))
    total = Currency(compute=tasks.sum('amount'))


class Project(Model):
    Meta = FiniteMeta
    hourly_rate = Currency(default=100)
    phases = Table(Phase)
    hours = Decimal(compute=phases.sum('hours'))
    total = Currency(compute=phases.sum('total'))


class CostLine(Child):
    qty = Decimal(default=0)
    rate = Currency(default=0)
    base_amount = Currency(compute=round(qty * rate * parent.exchange_rate, 2))
    allocated_cost = Currency(compute=round(qty * parent.unit_cost, 2))
    share = Decimal(scale=None, stored=False, compute=safe_divide(qty, parent.total_qty, default=0))


class CostSheet(Model):
    Meta = FiniteMeta
    exchange_rate = Decimal(default=1)
    limit = Currency(default=100)
    lines = Table(CostLine)
    total_qty = Decimal(compute=lines.sum('qty'))
    base_total = Currency(compute=lines.sum('base_amount'))
    unit_cost = Decimal(scale=None, stored=False, compute=safe_divide(base_total, total_qty, default=0))
    allocated_total = Currency(compute=lines.sum('allocated_cost'))
    large = Currency(compute=lines.where(row.base_amount > F('limit')).sum('base_amount'))
    largest = Currency(compute=coalesce(lines.max('base_amount'), 0))


class States(Model):
    Meta = FiniteMeta
    a = Decimal()
    b = Decimal()
    flag = Boolean()
    zero = Decimal(default=0)
    plain = Decimal(scale=None, stored=False, compute=a / zero)
    safe = Decimal(scale=None, stored=False, compute=safe_divide(a, zero, default=7))
    fallback = Decimal(scale=None, stored=False, compute=coalesce(b, D('3.5')))
    error_kept = Decimal(scale=None, stored=False, compute=coalesce(a / zero, 3))
    sum_null = Decimal(scale=None, stored=False, compute=a + b)
    ordered = Boolean(compute=b > 1)
    same = Boolean(compute=F('b') == None)  # noqa: E711 - symbolic null equality
    strict_and = Boolean(compute=(F('a') > 100) & (F('b') > 1))
    strict_or = Boolean(compute=(F('a') > 0) | (a / zero > 1))
    negated = Boolean(compute=~flag)
    product = Decimal(compute=flag * a)
    unrounded = Currency(compute=a / 3)
    rounded = Currency(compute=round(a / 3, 2))
    whole = Integer(compute=a * 2)


class Rounding(Model):
    Meta = FiniteMeta
    a = Decimal()
    up = Currency(compute=F('a'))
    even = Decimal(scale=2, compute=F('a'))
    even_currency = Currency(rounding='half_even', compute=F('a'))
    up_decimal = Decimal(scale=2, rounding='half_up', compute=F('a'))
    whole = Integer(compute=F('a'))
    whole_even = Integer(rounding='half_even', compute=F('a'))
    explicit = Decimal(scale=2, compute=round(F('a'), 2))
    bounded = Currency(compute=F('a'), maximum='10')
    third = Currency(compute=F('a') / 3)
    raw = Decimal(scale=None, stored=False, compute=F('a') / 3)


class Schedule(Model):
    Meta = FiniteMeta
    start = Date()
    end = Date()
    span = Decimal(scale=1, default=0)
    finish = Date(compute=F('start') + days(F('span')))
    begin = Date(compute=F('end') - days(F('span')))
    early = Date(compute=days(F('span')) + F('start'))
    length = Decimal(scale=0, compute=F('end') - F('start'))
    late = Boolean(compute=F('end') > F('finish'))
    same = Boolean(compute=F('end') == F('start'))
    age = Integer(compute=today() - F('start'))
    started = Boolean(compute=today() >= F('start'))
    until = Date(compute=coalesce(F('end'), today()))


class Region(Model):
    grace = Integer(default=0)


class Customer(Model):
    credit_days = Integer(default=30)
    region = Link(Region)
    rate = Decimal(scale=2, default=0)
    vip = Boolean(default=False)
    since = Date()
    currency = Text(max_length=3, default='')


class SaleLine(Child):
    customer = Link(Customer)
    amount = Currency(default=0)
    discount = Currency(compute=F('amount') * F('customer.rate') / 100)


class Sale(Model):
    Meta = FiniteMeta
    customer = Link(Customer)
    issued = Date()
    amount = Currency(default=0)
    lines = Table(SaleLine)
    due = Date(compute=F('issued') + days(F('customer.credit_days')))
    grace_due = Date(compute=F('due') + days(F('customer.region.grace')))
    discount = Currency(compute=F('amount') * F('customer.rate') / 100)
    vip = Boolean(compute=coalesce(F('customer.vip'), False))
    net = Currency(compute=when(F('customer.vip'), F('amount') - F('discount'), F('amount')))
    loyal = Decimal(scale=0, compute=F('issued') - F('customer.since'))
    line_discounts = Currency(compute=lines.sum('discount'))


class Choice(Model):
    Meta = FiniteMeta
    flag = Boolean()
    a = Decimal()
    zero = Decimal(default=0)
    picked = Decimal(scale=None, stored=False, compute=when(F('flag'), F('a'), F('a') / F('zero')))
    label = Text(compute=when(F('flag'), 'yes', 'no'))
    dated = Date(compute=when(F('flag'), today(), None))
    nested = Decimal(compute=when(F('a') > 1, when(F('flag'), 1, 2), 3))


class Moments(Model):
    """DateTime references keep their earlier rules: equality and coalesce only."""
    Meta = FiniteMeta
    at = DateTime()
    at2 = DateTime()
    same = Boolean(compute=F('at') == F('at2'))
    empty = Boolean(compute=F('at') == None)  # noqa: E711
    first = DateTime(compute=coalesce(F('at'), F('at2')))


class OfferLine(Child):
    qty = Decimal(default=1)
    rate = Currency(initial=parent.price)
    amount = Currency(compute=F('qty') * F('rate'))
    share = Percent(initial=when(F('qty') > 10, D(5), D(0)))


class Offer(Model):
    """Initial suggestions and field rules (applies_when, required_when, locked_when)."""
    Meta = FiniteMeta
    customer = Link(Customer)
    stage = Select(('open', 'won', 'lost'), default='open')
    discount = Decimal(scale=2, initial=F('customer.rate'))
    terms = Integer(initial=F('customer.credit_days'), locked_when=F('stage') == 'won')
    reason = Text(applies_when=F('stage') == 'lost', required_when=F('stage') == 'lost')
    price = Currency(default=0, locked_when=F('stage') == 'won')
    note = Text(required_when=F('price') > 100)
    hours = Duration(default=0)
    remark = Text(applies_when=F('stage') != 'lost', locked_when=F('stage') == 'won')
    billable = Decimal(scale=2, compute=F('hours') / 3600)
    lines = Table(OfferLine)
    total = Currency(compute=lines.sum('amount'))


class CappedLine(Child):
    qty = Decimal(default=1)


class Capped(Model):
    """Per-table row limits: the plan carries max_rows and both runtimes enforce it."""
    Meta = FiniteMeta
    lines = Table(CappedLine, min_rows=1, max_rows=2)
    total = Decimal(compute=lines.sum('qty'))


class Money(Model):
    """Currency targets round to their own currency: a fixed code, a field of the row, a field behind a Link, an app
    setting (a host input), or none (2 places). The host gives the currency table."""
    Meta = FiniteMeta
    customer = Link(Customer)
    currency = Text(max_length=3, default='KWD')
    qty = Decimal(scale=3, default='1')
    rate = Decimal(scale=6, default='0')
    fixed = Currency(currency='KWD', compute=F('qty') * F('rate'))
    own = Currency(currency='currency', compute=F('qty') * F('rate'))
    even = Currency(currency='currency', rounding='half_even', compute=F('qty') * F('rate'))
    linked = Currency(currency='customer.currency', compute=F('qty') * F('rate'))
    shared = Currency(currency='settings.billing.currency', compute=F('qty') * F('rate'))
    plain = Currency(compute=F('qty') * F('rate'))
    suggested = Currency(currency='currency', initial=F('rate') * 2)


# Host inputs of a scenario's document, when it has any: the currency table and setting currencies.
HOSTS = {'money-places': {'decimals': {'JPY': 0, 'KWD': 3, 'XCL': 5}, 'settings': {'billing.currency': 'JPY'}}}

MODELS = {'invoice': Invoice, 'project': Project, 'cost': CostSheet, 'states': States,
          'rounding': Rounding, 'schedule': Schedule, 'sale': Sale, 'choice': Choice, 'moments': Moments,
          'offer': Offer, 'capped': Capped, 'money': Money}
LOOKUP_MODELS = (Region, Customer)


def items(*values):
    return [{'_key': 'r' + str(index), **value} for index, value in enumerate(values)]


SCENARIOS = [
    ('invoice-toggle', 'invoice', {'inclusive': True, 'children': {'items': items({'qty': '1', 'rate': '105'}, {'qty': '3', 'rate': '0.10', 'vat_rate': '100'})}},
     [['set', '', 'inclusive', False], ['set', 'items.r1', 'taxable', False], ['set', '', 'inclusive', True],
      ['set', 'items.r0', 'qty', '2'], ['set', 'items.r0', 'qty', {'$state': 'pending'}], ['set', 'items.r0', 'qty', '2.5'],
      ['add', '', 'items', {'_key': 'n', 'qty': '1', 'rate': '0.05', 'vat_rate': '100'}], ['remove', 'items.r1'],
      ['set', '', 'grand_total', '1'], ['set', 'items.n', 'rate', 'abc']]),
    ('invoice-structure', 'invoice', {'inclusive': False, 'children': {'items': [{'id': 7, 'qty': '1', 'rate': '10', 'vat_rate': '5', 'taxable': True}]}},
     [['replace', '', 'items', [{'id': 7, 'qty': '2'}, {'_key': 'b', 'qty': '1', 'rate': '1'}]], ['reorder', '', 'items', ['items.b', 'items.7']],
      ['replace', '', 'items', [{'id': 8}]], ['add', '', 'items', {'_key': 'b'}]]),
    ('project-nested', 'project', {'children': {'phases': [
        {'_key': 'A', 'children': {'tasks': [{'_key': 'a1', 'hours': '2'}, {'_key': 'a2', 'hours': '3'}]}},
        {'_key': 'B', 'children': {'tasks': [{'_key': 'b1', 'hours': '4'}]}}]}},
     [['set', '', 'hourly_rate', '120'], ['set', '', 'hourly_rate', '100'], ['set', 'phases.A.tasks.a1', 'hours', '5'],
      ['move', 'phases.A.tasks.a2', 'phases.B', 'tasks', 0], ['add', 'phases.B', 'tasks', {'_key': 'b2', 'hours': '1.5'}],
      ['remove', 'phases.A'], ['move', 'phases.B', 'phases.B', 'tasks', 0],
      ['add', '', 'phases', {'_key': 'C', 'children': {'tasks': [{'_key': 'c1', 'hours': '1'}]}}]]),
    ('cost-round-trip', 'cost', {'children': {'lines': items({'qty': '2', 'rate': '50'}, {'qty': '3', 'rate': '100'})}},
     [['set', '', 'exchange_rate', '2'], ['set', 'lines.r0', 'qty', '0'], ['set', 'lines.r1', 'qty', '0'],
      ['set', 'lines.r1', 'qty', '7'], ['set', '', 'limit', '1000'], ['remove', 'lines.r1'], ['remove', 'lines.r0']]),
    ('states', 'states', {'a': '6'},
     [['set', '', 'flag', True], ['set', '', 'b', '2'], ['set', '', 'zero', '4'], ['set', '', 'a', '-1.5'],
      ['set', '', 'b', None], ['set', '', 'flag', None], ['set', '', 'a', '1' * 20], ['set', '', 'a', '0.000000000000000001']]),
    ('rounding-modes', 'rounding', {'a': '2.345'},
     [['set', '', 'a', value] for value in ('2.355', '-2.345', '-2.355', '2.5', '3.5', '-2.5', '-3.5', '0.005', '-0.005',
                                            '9.995', '10.005', '2.3450001', '1', None)]),
    ('dates', 'schedule', {'start': '2024-02-28', 'end': '2024-03-01', 'span': '1'},
     [['today', '2024-03-02'], ['set', '', 'span', '2'], ['set', '', 'span', '365'], ['set', '', 'span', '-1'],
      ['set', '', 'span', '1.5'], ['set', '', 'span', '0'], ['set', '', 'end', None], ['set', '', 'end', '2024-02-28'],
      ['today', '2023-12-31'], ['set', '', 'start', '9999-12-31'], ['set', '', 'span', '1'], ['set', '', 'start', '0001-01-01'],
      ['set', '', 'end', '0001-01-01'], ['set', '', 'start', '2023-02-29'], ['set', '', 'start', '2024-1-01'],
      ['today', 'tomorrow'], ['today', None]]),
    ('hops', 'sale', {'customer': 7, 'issued': '2026-10-28', 'amount': '100.05',
                      'children': {'lines': items({'customer': 7, 'amount': '10'}, {'customer': 8, 'amount': '20'}, {'amount': '5'})}},
     [['lookup', 'customer', 7, {'credit_days': 30, 'rate': '2.50', 'vip': False, 'region': 3, 'since': '2026-10-01'}],
      ['lookup', 'customer', 8, {'credit_days': 10, 'rate': '1.25', 'vip': True, 'region': None, 'since': None}],
      ['lookup', 'region', 3, {'grace': 5}], ['lookup', 'customer', 7, {'vip': True}],
      ['set', '', 'customer', 8], ['set', '', 'customer', None], ['set', '', 'customer', 7],
      ['lookup', 'customer', 7, {'rate': {'$state': 'pending'}}], ['lookup', 'region', 3, {'grace': 'x'}],
      ['lookup', 'customer', 7, {'unknown': 1}], ['lookup', 'invoice', 1, {'credit_days': 1}], ['lookup', 'customer', 0, {'vip': True}],
      ['set', 'lines.r2', 'customer', 9], ['set', '', 'customer', '7']]),
    ('datetime', 'moments', {'at2': '2026-11-01T08:00:00.000000Z'},
     [['set', '', 'at', '2026-11-01T08:00:00.000000Z'], ['set', '', 'at2', None], ['set', '', 'at', None]]),
    ('initial-and-rules', 'offer', {'customer': 7, 'hours': 'PT1H30M', 'children': {'lines': items({'qty': '2'}, {'qty': '12', 'rate': '1.50'})}},
     [['lookup', 'customer', 7, {'credit_days': 30, 'rate': '2.50'}], ['set', '', 'discount', '9'],
      ['lookup', 'customer', 8, {'credit_days': 10, 'rate': '1.25'}], ['set', '', 'customer', 8], ['set', '', 'customer', 7],
      ['batch', [['set', '', 'customer', 8], ['set', '', 'discount', '4.5']]], ['set', '', 'customer', None],
      ['set', '', 'price', '120'], ['set', 'lines.r0', 'rate', '3'], ['add', '', 'lines', {'_key': 'n', 'qty': '11'}],
      ['set', '', 'note', 'big'], ['set', '', 'stage', 'lost'], ['set', '', 'reason', 'Too dear'], ['set', '', 'stage', 'open'],
      ['set', '', 'reason', 'Ignored'], ['set', '', 'stage', 'won'], ['set', '', 'customer', 7], ['set', '', 'price', None],
      ['set', '', 'hours', 5400], ['set', '', 'hours', 'P1D'], ['set', '', 'hours', 'PT'], ['set', 'lines.n', 'qty', '1']]),
    ('loaded-offer', 'offer', {'id': 4, 'customer': 7, 'discount': '1', 'terms': 3, 'stage': 'lost', 'reason': 'Kept',
                               'children': {'lines': [{'id': 9, 'qty': '20', 'rate': '7', 'share': '1'}]}},
     [['lookup', 'customer', 7, {'credit_days': 30, 'rate': '2.50'}], ['set', '', 'price', '5'], ['set', 'lines.9', 'qty', '3'],
      ['lookup', 'customer', 8, {'credit_days': 10, 'rate': '1.25'}], ['set', '', 'customer', 8], ['set', '', 'stage', None]]),
    ('saved-locks-and-resends', 'offer', {'id': 5, 'customer': 7, 'stage': 'won', 'price': '150', 'terms': 45, 'remark': 'kept',
                                          'children': {'lines': [{'id': 2, 'qty': '1', 'rate': '3'}]}},
     [['lookup', 'customer', 7, {'credit_days': 30, 'rate': '2.50'}], ['set', '', 'stage', 'lost'], ['set', '', 'customer', 7], ['set', '', 'stage', 'open'],
      ['set', '', 'customer', 8], ['lookup', 'customer', 8, {'credit_days': 10, 'rate': '1.25'}], ['rebase'],
      ['set', '', 'terms', 60], ['set', '', 'customer', 8], ['set', '', 'stage', 'won'], ['set', '', 'customer', 7],
      ['add', '', 'lines', {'_key': 'n', 'qty': '1'}], ['set', 'lines.2', 'qty', '1'], ['set', '', 'stage', 'lost']]),
    ('table-limits', 'capped', {'children': {'lines': items({'qty': '1'})}},
     [['add', '', 'lines', {'_key': 'n1', 'qty': '2'}], ['add', '', 'lines', {'_key': 'n2', 'qty': '4'}],
      ['replace', '', 'lines', [{'_key': 'a'}, {'_key': 'b'}, {'_key': 'c'}]], ['remove', 'lines.r0'],
      ['add', '', 'lines', {'_key': 'n3', 'qty': '5'}], ['batch', [['remove', 'lines.n1'], ['add', '', 'lines', {'_key': 'n4'}]]],
      ['add', '', 'lines', {'_key': 'n5'}]]),
    ('money-places', 'money', {'customer': 7, 'qty': '1.5', 'rate': '1.3335'},
     [['lookup', 'customer', 7, {'currency': 'XCL'}], ['set', '', 'currency', 'JPY'], ['set', '', 'rate', '101.5'],
      ['set', '', 'currency', 'XCL'], ['set', '', 'rate', '0.0000033'], ['set', '', 'currency', ''], ['set', '', 'currency', 'AED'],
      ['set', '', 'rate', '-1.3335'], ['lookup', 'customer', 7, {'currency': 'JPY'}], ['set', '', 'customer', None],
      ['set', '', 'currency', 'KWD'], ['set', '', 'rate', '2.0005'], ['set', '', 'qty', '-1']]),
    ('when', 'choice', {'a': '6'},
     [['set', '', 'flag', True], ['set', '', 'a', None], ['set', '', 'flag', False], ['set', '', 'a', '6'],
      ['set', '', 'zero', '4'], ['today', '2026-11-01'], ['set', '', 'flag', True], ['set', '', 'a', '0.5'], ['set', '', 'flag', None]]),
]


def random_scenario(seed, steps=40):
    generator = random.Random(seed)
    data = {'inclusive': True, 'children': {'items': items(*[{'qty': str(i + 1), 'rate': '1.10'} for i in range(4)])}}
    keys, commands, counter = ['r0', 'r1', 'r2', 'r3'], [], 0
    for _ in range(steps):
        choice = generator.random()
        if choice < 0.5 and keys:
            commands.append(['set', 'items.' + generator.choice(keys), generator.choice(['qty', 'rate', 'vat_rate']), str(generator.randint(0, 900) / 100)])
        elif choice < 0.62:
            commands.append(['set', '', 'inclusive', generator.random() < 0.5])
        elif choice < 0.8 or not keys:
            counter += 1
            keys.append('x' + str(counter))
            commands.append(['add', '', 'items', {'_key': keys[-1], 'qty': str(generator.randint(1, 5)), 'rate': '2.5'}])
        elif choice < 0.9:
            commands.append(['set', 'items.' + generator.choice(keys), 'taxable', generator.random() < 0.5])
        else:
            key = generator.choice(keys)
            keys.remove(key)
            commands.append(['remove', 'items.' + key])
    return ('invoice-random-' + str(seed), 'invoice', data, commands)


def sources(document, row=None, *, identities=True):
    """A document's source-only data, to rebuild it from scratch and compare with the incremental result."""
    row = row or document.root
    result = dict(row.values)
    if identities:
        result.update({**({'id': row.id} if row.id is not None else {}), **({'_key': row.key} if row.parent else {})})
    if row.children:
        result['children'] = {table: [sources(document, item, identities=identities) for item in items] for table, items in row.children.items()}
    return result


def handle(document, label):
    return document.root.handle if label == '' else document.find(label)


def apply(document, command):
    kind, *args = command
    if kind == 'set': document.set(handle(document, args[0]), args[1], args[2])
    elif kind == 'add': document.add_row(handle(document, args[0]), args[1], args[2])
    elif kind == 'remove': document.remove_row(handle(document, args[0]))
    elif kind == 'move': document.move_row(handle(document, args[0]), handle(document, args[1]), args[2], args[3])
    elif kind == 'reorder': document.reorder(handle(document, args[0]), args[1], [handle(document, label) for label in args[2]])
    elif kind == 'replace': document.replace_rows(handle(document, args[0]), args[1], args[2])
    elif kind == 'today': document.set_today(args[0])
    elif kind == 'lookup': document.set_lookups(args[0], args[1], args[2])
    elif kind == 'batch': document.batch(lambda _: [apply(document, item) for item in args[0]])
    elif kind == 'rebase': document.rebase()


def rule_map(document):
    """Rule states of every row that declares rules, by row label."""
    result = {}
    for handle, row in document.rows.items():
        states = document.rule_states(handle)
        if states:
            result[row.label.rstrip('.') or '$root'] = states
    return dict(sorted(result.items()))


def strip(snapshot):
    if isinstance(snapshot, dict):
        return {key: strip(value) for key, value in snapshot.items() if key != '_handle'}
    if isinstance(snapshot, list):
        return [strip(item) for item in snapshot]
    return snapshot


NUMERIC = [(op, a, b) for op in ('add', 'sub', 'mul', 'div') for a, b in [
    ('1', '3'), ('-1', '3'), ('2', '3'), ('0.000000000000000001', '2'), ('-0.000000000000000001', '2'), ('123456.789', '-0.001'),
    ('0', '-5'), ('99999999999999999999', '0.07'), ('5', '8'), ('1.005', '1'), ('-7.25', '0.5'), ('0.1', '0.2')]] + [
    ('round', '1.005', 2), ('round', '-1.005', 2), ('round', '2.5', 0), ('round', '-2.5', 0), ('round', '1.004999', 2), ('round', '0.0049', 2)] + [
    ('round_even', a, b) for a, b in [('1.005', 2), ('1.015', 2), ('-1.005', 2), ('-1.015', 2), ('2.5', 0), ('3.5', 0), ('-2.5', 0),
                                      ('0.5', 0), ('1.0051', 2), ('-0.0049', 2), ('99999999999999999999.5', 0)]]


def numeric_cases():
    cases = []
    for op, a, b in NUMERIC:
        try:
            if op.startswith('round'):
                value = text(quantize(parse(a), b, 'half_even' if op == 'round_even' else 'half_up'))
            else:
                value = text({'add': add, 'sub': sub, 'mul': mul, 'div': div}[op](parse(a), parse(b)))
            cases.append({'op': op, 'a': a, 'b': b, 'expected': value})
        except CalcError as exc:
            cases.append({'op': op, 'a': a, 'b': b, 'error': exc.code})
    return cases


def collect():
    registry = Registry(*MODELS.values(), *LOOKUP_MODELS)
    plans = {name: registry.plans[model] for name, model in MODELS.items()}
    scenarios = []
    for name, model, data, commands in SCENARIOS + [random_scenario(seed) for seed in (1, 2, 3)]:
        document = Document(plans[model], data, currencies=HOSTS.get(name))
        ruled = any(field.get('kind') for field in plans[model]['fields'])
        extra = (lambda: {'rules': rule_map(document)}) if ruled else dict
        steps = [{'snapshot': strip(document.snapshot()), 'revision': document.revision, 'trace': document.trace,
                  'requests': document.lookup_requests(), **extra()}]
        for command in commands:
            try:
                apply(document, command)
                steps.append({'command': command, 'snapshot': strip(document.snapshot()), 'revision': document.revision,
                              'trace': document.trace, 'requests': document.lookup_requests(), **extra()})
            except CalcError as exc:
                steps.append({'command': command, 'error': exc.code, 'snapshot': strip(document.snapshot()), 'revision': document.revision,
                              'requests': document.lookup_requests(), **extra()})
        scenarios.append({'name': name, 'model': model, 'data': data, 'steps': steps, **({'currencies': HOSTS[name]} if name in HOSTS else {})})
    return {'plans': plans, 'scenarios': scenarios, 'numeric': numeric_cases(),
            'invalid_plans': [{'name': 'unknown-op', 'expression': {'$expr': 'eval', 'args': []}},
                              {'name': 'prototype', 'expression': {'$expr': 'ref', 'scope': 'self', 'field': '__proto__'}},
                              {'name': 'scope', 'expression': {'$expr': 'ref', 'scope': 'window', 'field': 'qty'}},
                              {'name': 'round-scale', 'expression': {'$expr': 'round', 'args': [{'$expr': 'literal', 'type': 'decimal', 'value': '1'},
                                                                                                {'$expr': 'literal', 'type': 'integer', 'value': 50000000}]}},
                              {'name': 'deep', 'expression': deep(40)},
                              {'name': 'hop-depth', 'expression': {'$expr': 'hop', 'field': 'customer', 'steps': [['customer', 'region', 'link']] * 3 + [['region', 'grace', 'decimal']]}},
                              {'name': 'hop-through-value', 'expression': {'$expr': 'hop', 'field': 'customer', 'steps': [['customer', 'rate', 'decimal'], ['region', 'grace', 'decimal']]}},
                              {'name': 'hop-model', 'expression': {'$expr': 'hop', 'field': 'customer', 'steps': [['Customer.x', 'rate', 'decimal']]}},
                              {'name': 'hop-empty', 'expression': {'$expr': 'hop', 'field': 'customer', 'steps': []}},
                              {'name': 'when-arity', 'expression': {'$expr': 'when', 'args': [{'$expr': 'literal', 'type': 'boolean', 'value': True}]}},
                              {'name': 'today-scale', 'expression': {'$expr': 'round', 'args': [{'$expr': 'literal', 'type': 'decimal', 'value': '1'}, {'$expr': 'today'}]}}],
            'invalid_inputs': invalid_inputs(plans['states'])}


def deep(depth):
    node = {'$expr': 'literal', 'type': 'decimal', 'value': '1'}
    for _ in range(depth):
        node = {'$expr': 'neg', 'args': [node]}
    return node


def invalid_inputs(plan):
    """Edge inputs whose outcome both runtimes must agree on."""
    cases = []
    for value in [2.0, 1e20, 2.5, '2.0', '-0', '01', True, {'__proto__': {'a': '9'}}, '9' * 41, -(2**53)]:
        document = Document(plan, {'a': value})
        snap = document.snapshot()
        cases.append({'input': value, 'sum_null': snap.get('_states', {}).get('sum_null'), 'whole': snap['whole'],
                      'whole_state': snap.get('_states', {}).get('whole')})
    structure = []
    for data in [{'__proto__': {'a': '9'}}, {'valueOf': '1'}, {'id': True}, {'id': 3.0}, {'id': 2**60}]:
        try:
            Document(plan, data)
            structure.append({'data': data, 'error': None})
        except CalcError as exc:
            structure.append({'data': data, 'error': exc.code})
    return {'values': cases, 'documents': structure}
