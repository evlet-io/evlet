"""Shared finite-v1 scenarios: Python compiles the plans and records the authoritative results.

scripts/generate_finite_fixtures.py writes them for the browser evaluator; the pytest suite
checks that the checked-in JSON still matches. Both runtimes consume the same serialized plan.
"""

import random

from evlet import Model, Child, Table, Decimal, Currency, Boolean, Integer, Text, Registry
from evlet.calc import (D, F, parent, row, round, coalesce, safe_divide, Document, CalcError,
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


MODELS = {'invoice': Invoice, 'project': Project, 'cost': CostSheet, 'states': States}


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


def strip(snapshot):
    if isinstance(snapshot, dict):
        return {key: strip(value) for key, value in snapshot.items() if key != '_handle'}
    if isinstance(snapshot, list):
        return [strip(item) for item in snapshot]
    return snapshot


NUMERIC = [(op, a, b) for op in ('add', 'sub', 'mul', 'div') for a, b in [
    ('1', '3'), ('-1', '3'), ('2', '3'), ('0.000000000000000001', '2'), ('-0.000000000000000001', '2'), ('123456.789', '-0.001'),
    ('0', '-5'), ('99999999999999999999', '0.07'), ('5', '8'), ('1.005', '1'), ('-7.25', '0.5'), ('0.1', '0.2')]] + [
    ('round', '1.005', 2), ('round', '-1.005', 2), ('round', '2.5', 0), ('round', '-2.5', 0), ('round', '1.004999', 2), ('round', '0.0049', 2)]


def numeric_cases():
    cases = []
    for op, a, b in NUMERIC:
        try:
            value = text(quantize(parse(a), b)) if op == 'round' else text({'add': add, 'sub': sub, 'mul': mul, 'div': div}[op](parse(a), parse(b)))
            cases.append({'op': op, 'a': a, 'b': b, 'expected': value})
        except CalcError as exc:
            cases.append({'op': op, 'a': a, 'b': b, 'error': exc.code})
    return cases


def collect():
    registry = Registry(*MODELS.values())
    plans = {name: registry.plans[model] for name, model in MODELS.items()}
    scenarios = []
    for name, model, data, commands in SCENARIOS + [random_scenario(seed) for seed in (1, 2, 3)]:
        document = Document(plans[model], data)
        steps = [{'snapshot': strip(document.snapshot()), 'revision': document.revision, 'evaluated': document.trace['evaluated']}]
        for command in commands:
            try:
                apply(document, command)
                steps.append({'command': command, 'snapshot': strip(document.snapshot()), 'revision': document.revision,
                              'evaluated': document.trace['evaluated']})
            except CalcError as exc:
                steps.append({'command': command, 'error': exc.code, 'snapshot': strip(document.snapshot()), 'revision': document.revision})
        scenarios.append({'name': name, 'model': model, 'data': data, 'steps': steps})
    return {'plans': plans, 'scenarios': scenarios, 'numeric': numeric_cases(),
            'invalid_plans': [{'name': 'unknown-op', 'expression': {'$expr': 'eval', 'args': []}},
                              {'name': 'prototype', 'expression': {'$expr': 'ref', 'scope': 'self', 'field': '__proto__'}}]}
