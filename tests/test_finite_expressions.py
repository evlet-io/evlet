"""Finite-v1 language: authoring, compiler checks, numeric profile and propagation shapes."""

from decimal import Decimal as Reference, ROUND_HALF_UP, localcontext
import random

import pytest

from evlet import Model, Child, Table, Decimal, Currency, Boolean, Integer, Text, Registry
from evlet.calc import (D, F, parent, row, round, coalesce, safe_divide, Document, CalcError, Num, parse, text,
                        add, sub, mul, div, quantize, total, compare)
from evlet.declarations import Ref


class FiniteMeta:
    calculation_language = 'finite-v1'
    numeric_profile = 'finite40-hu18-v1'


def plan(*models):
    registry = Registry(*models)
    return registry.plans[models[0]]


def document(root, data=None):
    return Document(plan(root), data or {})


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


def lines(*values):
    return {'children': {'items': [{'_key': 'r' + str(index), **value} for index, value in enumerate(values)]}}


# T01-T05, T11, T18, T19: invoice behaviour

def test_inclusive_and_exclusive_invoice_with_one_toggle():
    doc = document(Invoice, {'inclusive': True, **lines({'qty': '1', 'rate': '105'})})
    snap = doc.snapshot()
    item = snap['children']['items'][0]
    assert (item['net'], item['vat'], item['total']) == ('100.00', '5.00', '105.00')
    seen = []
    doc.observe(doc.root.handle, 'grand_total', lambda result: seen.append(result.value))
    doc.set(doc.root.handle, 'inclusive', False)
    item = doc.snapshot()['children']['items'][0]
    assert (item['net'], item['vat'], item['total']) == ('105.00', '5.25', '110.25')
    assert [text(value) for value in seen] == ['110.25'] and doc.revision == 2


def test_row_edit_only_recalculates_that_row_and_its_aggregates():
    doc = document(Invoice, {'inclusive': False, **lines(*[{'qty': '2', 'rate': '150'} for _ in range(3)])})
    assert doc.snapshot()['children']['items'][0]['entered_amount'] == '300.00'
    first, second = doc.find('items.r0'), doc.find('items.r1')
    before = doc.counts[second, 'net']
    doc.set(first, 'qty', '3')
    assert doc.counts[second, 'net'] == before
    assert 'items.r1.net' not in doc.trace['changed'] and 'items.r0.net' in doc.trace['changed']
    assert doc.snapshot()['grand_total'] == text(Num(47250 + 31500 + 31500, 2), 2)


def test_parent_toggle_publishes_one_coherent_revision():
    doc = document(Invoice, {'inclusive': True, **lines(*[{'qty': '1', 'rate': '105'} for _ in range(3)])})
    revisions = []
    doc.observe(doc.root.handle, 'grand_total', lambda result: revisions.append((doc.revision, text(result.value))))
    for index in range(3):
        doc.observe(doc.find(f'items.r{index}'), 'net', lambda result: revisions.append((doc.revision, text(result.value))))
    doc.set(doc.root.handle, 'inclusive', False)
    assert {revision for revision, _ in revisions} == {2}
    assert sorted(value for _, value in revisions) == ['105', '105', '105', '330.75']


def test_filtered_membership_changes_with_flag():
    doc = document(Invoice, {'inclusive': False, **lines({'qty': '1', 'rate': '10'}, {'qty': '1', 'rate': '20'})})
    assert doc.snapshot()['taxable_net'] == '30.00'
    doc.set(doc.find('items.r1'), 'taxable', False)
    snap = doc.snapshot()
    assert snap['taxable_net'] == '10.00' and snap['net_total'] == '30.00'


def test_rounding_policy_cases():
    tiny = document(Invoice, {'inclusive': True, **lines({'qty': '1', 'rate': '0.05', 'vat_rate': '100'})}).snapshot()['children']['items'][0]
    assert (tiny['net'], tiny['vat'], tiny['total']) == ('0.03', '0.02', '0.05')
    three = document(Invoice, {'inclusive': False, **lines(*[{'qty': '1', 'rate': '0.10'} for _ in range(3)])}).snapshot()
    assert three['vat_total'] == '0.03'


# T06-T09, T15: propagation shapes

def test_sibling_shares_through_parent_total():
    class Share(Child):
        qty = Decimal(default=0)
        share = Decimal(scale=None, stored=False, compute=safe_divide(qty, parent.total_qty, default=0))

    class Pool(Model):
        Meta = FiniteMeta
        rows = Table(Share)
        total_qty = Decimal(compute=rows.sum('qty'))
    doc = document(Pool, {'children': {'rows': [{'_key': 'a', 'qty': '1'}, {'_key': 'b', 'qty': '3'}]}})
    a, b = doc.find('rows.a'), doc.find('rows.b')
    assert [doc.snapshot()['children']['rows'][i]['share'] for i in (0, 1)] == ['0.25', '0.75']
    doc.set(a, 'qty', '2')
    snap = doc.snapshot()
    assert snap['total_qty'] == '5.0000' and [row['share'] for row in snap['children']['rows']] == ['0.4', '0.6']
    assert doc.rows[b].values['qty'] == '3'


def test_different_child_tables_and_cost_round_trip():
    class Material(Child):
        amount = Currency(default=0)

    class Surcharge(Child):
        percent = Decimal(default=0)
        amount = Currency(compute=round(parent.material_total * percent / 100, 2))

    class Quote(Model):
        Meta = FiniteMeta
        materials = Table(Material)
        surcharges = Table(Surcharge)
        material_total = Currency(compute=materials.sum('amount'))
        surcharge_total = Currency(compute=surcharges.sum('amount'))
        grand_total = Currency(compute=material_total + surcharge_total)
    doc = document(Quote, {'children': {'materials': [{'_key': 'm', 'amount': '200'}], 'surcharges': [{'_key': 's', 'percent': '10'}]}})
    assert doc.snapshot()['grand_total'] == '220.00'
    doc.set(doc.find('materials.m'), 'amount', '300')
    assert doc.snapshot()['grand_total'] == '330.00'

    class CostLine(Child):
        qty = Decimal(default=0)
        rate = Currency(default=0)
        base_amount = Currency(compute=round(qty * rate * parent.exchange_rate, 2))
        allocated_cost = Currency(compute=round(qty * parent.unit_cost, 2))

    class CostSheet(Model):
        Meta = FiniteMeta
        exchange_rate = Decimal(default=1)
        lines = Table(CostLine)
        total_qty = Decimal(compute=lines.sum('qty'))
        base_total = Currency(compute=lines.sum('base_amount'))
        unit_cost = Decimal(scale=None, stored=False, compute=safe_divide(base_total, total_qty, default=0))
        allocated_total = Currency(compute=lines.sum('allocated_cost'))
    doc = document(CostSheet, {'children': {'lines': [{'_key': 'a', 'qty': '2', 'rate': '50'}, {'_key': 'b', 'qty': '3', 'rate': '100'}]}})
    snap = doc.snapshot()
    assert (snap['base_total'], snap['unit_cost'], snap['allocated_total']) == ('400.00', '80', '400.00')
    doc.set(doc.root.handle, 'exchange_rate', '2')
    snap = doc.snapshot()
    assert (snap['base_total'], snap['unit_cost'], [row['allocated_cost'] for row in snap['children']['lines']]) == ('800.00', '160', ['320.00', '480.00'])


class WorkItem(Child):
    hours = Decimal(default=0)
    amount = Currency(compute=round(hours * parent.hourly_rate, 2))


class Phase(Child):
    hourly_rate = Currency(compute=parent.hourly_rate, stored=False)
    tasks = Table(WorkItem)
    total = Currency(compute=tasks.sum('amount'))


class Project(Model):
    Meta = FiniteMeta
    hourly_rate = Currency(default=100)
    phases = Table(Phase)
    total = Currency(compute=phases.sum('total'))


def project_data():
    return {'children': {'phases': [
        {'_key': 'A', 'children': {'tasks': [{'_key': 'a1', 'hours': '2'}, {'_key': 'a2', 'hours': '3'}]}},
        {'_key': 'B', 'children': {'tasks': [{'_key': 'b1', 'hours': '4'}]}}]}}


def test_nested_relays_and_independent_branches():
    doc = document(Project, project_data())
    assert doc.snapshot()['total'] == '900.00'
    doc.set(doc.root.handle, 'hourly_rate', '120')
    assert doc.snapshot()['total'] == '1080.00'
    doc.set(doc.root.handle, 'hourly_rate', '100')
    b = doc.find('phases.B.tasks.b1')
    before = doc.counts[b, 'amount']
    doc.set(doc.find('phases.A.tasks.a1'), 'hours', '5')
    snap = doc.snapshot()
    assert [phase['total'] for phase in snap['children']['phases']] == ['800.00', '400.00'] and snap['total'] == '1200.00'
    assert doc.counts[b, 'amount'] == before


def test_diamond_publishes_final_values_together():
    class Diamond(Model):
        Meta = FiniteMeta
        a = Decimal(default=0)
        b = Decimal(compute=a + 10)
        c = Decimal(compute=a * 3)
        d = Decimal(compute=b + c)
    doc = document(Diamond, {'a': '1'})
    seen = []
    for name in 'bcd':
        doc.observe(doc.root.handle, name, lambda result, name=name: seen.append((name, text(result.value), doc.snapshot()['d'])))
    doc.set(doc.root.handle, 'a', '4')
    assert sorted(seen) == [('b', '14', '26.0000'), ('c', '12', '26.0000'), ('d', '26', '26.0000')]


# T16, A25, A44: cycles are rejected before execution

def test_value_cycles_fail_with_closed_paths():
    with pytest.raises(CalcError) as caught:
        class Loop(Model):
            Meta = FiniteMeta
            a = Decimal(compute=F('c') + 1)
            b = Decimal(compute=a + 1)
            c = Decimal(compute=b + 1)
        plan(Loop)
    assert caught.value.code == 'MODEL_DEPENDENCY_CYCLE'
    assert caught.value.causes[0] == caught.value.causes[-1] and len(caught.value.causes) == 4

    with pytest.raises(CalcError, match='MODEL_DEPENDENCY_CYCLE'):
        class Self(Model):
            Meta = FiniteMeta
            a = Decimal(compute=F('a') + 1)
        plan(Self)

    class Line(Child):
        qty = Decimal(default=0)
        allocated = Currency(compute=round(qty * parent.unit, 2))

    class Sheet(Model):
        Meta = FiniteMeta
        lines = Table(Line)
        allocated_total = Currency(compute=lines.sum('allocated'))
        unit = Decimal(scale=None, stored=False, compute=safe_divide(allocated_total, lines.sum('qty'), default=0))
    with pytest.raises(CalcError) as caught:
        plan(Sheet)
    assert 'lines[*].allocated' in caught.value.causes

    class Flagged(Child):
        amount = Currency(default=0)

    class Filtered(Model):
        Meta = FiniteMeta
        limit = Currency(compute=F('kept'))
        rows = Table(Flagged)
        kept = Currency(compute=rows.where(row.amount > F('limit')).sum('amount'))
    with pytest.raises(CalcError, match='MODEL_DEPENDENCY_CYCLE'):
        plan(Filtered)


def test_containment_cycle_is_distinct_from_value_cycle():
    class Node(Child):
        name = Text()

    Node.__tables__ = {'nodes': Table(Node)}

    class Tree(Model):
        Meta = FiniteMeta
        nodes = Table(Node)
    with pytest.raises(CalcError, match='MODEL_CONTAINMENT_CYCLE'):
        Registry(Tree)


# T17, A05-A08, A01-A04: authoring and compiler rules

def test_same_child_under_compatible_and_incompatible_parents():
    class Priced(Child):
        qty = Decimal(default=1)
        amount = Currency(compute=round(qty * parent.unit_price, 2))

    class Good(Model):
        Meta = FiniteMeta
        unit_price = Currency(default=5)
        lines = Table(Priced)

    class Bad(Model):
        Meta = FiniteMeta
        unit_price = Text()
        lines = Table(Priced)
    assert document(Good, {'children': {'lines': [{'_key': 'x', 'qty': '2'}]}}).snapshot()['children']['lines'][0]['amount'] == '10.00'
    with pytest.raises(CalcError) as caught:
        Registry(Good, Bad)
    assert caught.value.code == 'PARENT_FIELD_INCOMPATIBLE' and 'lines[*].amount' in str(caught.value)

    class Missing(Model):
        Meta = FiniteMeta
        lines = Table(Priced)
    with pytest.raises(CalcError, match='PARENT_FIELD_INCOMPATIBLE'):
        Registry(Missing)


def test_truth_tests_and_forbidden_syntax():
    qty = Decimal()
    with pytest.raises(CalcError, match='EXPRESSION_TRUTH_TEST'):
        bool(qty + 1)
    with pytest.raises(CalcError, match='EXPRESSION_TRUTH_TEST'):
        0 < F('a') < 2
    with pytest.raises(CalcError, match='EXPRESSION_TRUTH_TEST'):
        (F('a') > 1) and (F('b') > 1)
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        parent.parent.rate
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        Table(InvoiceItem).sum('qty * rate')
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        Table(InvoiceItem).where('x > 0')
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        D(1.5)
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        F('x') + 1.5

    class Root(Model):
        Meta = FiniteMeta
        a = Decimal(compute=parent.x)
    with pytest.raises(CalcError, match='PARENT_CONTEXT_REQUIRED'):
        plan(Root)

    class Writers(Model):
        Meta = FiniteMeta
        a = Decimal(default=0)
        b = Decimal(default=1, compute=a + 1)
    with pytest.raises(CalcError, match='one writer'):
        plan(Writers)

    class Unknown(Model):
        Meta = FiniteMeta
        a = Decimal(compute=F('later') + 1)
    with pytest.raises(CalcError, match='unknown field later'):
        plan(Unknown)

    class Forward(Model):
        Meta = FiniteMeta
        a = Decimal(compute=F('later') + 1)
        later = Decimal(default=2)
    assert document(Forward).snapshot()['a'] == '3.0000'


def test_field_identity_survives_reuse_and_equality_stays_safe():
    first, second = Decimal(), Decimal()
    assert first != second and first == first and len({first, second}) == 2
    assert {first: 1}[first] == 1
    doc = document(Invoice, lines({'qty': '1', 'rate': '1'}))
    assert doc.snapshot()['children']['items'][0]['net'] == '0.95'


def test_lexical_filter_scope_and_type_rules():
    class Entry(Child):
        amount = Currency(default=0)

    class Ledger(Model):
        Meta = FiniteMeta
        limit = Currency(default=10)
        entries = Table(Entry)
        large = Currency(compute=entries.where(row.amount > F('limit')).sum('amount'))
        count_large = Integer(compute=entries.where(row.amount > limit).count())
    doc = document(Ledger, {'children': {'entries': [{'_key': 'a', 'amount': '5'}, {'_key': 'b', 'amount': '50'}]}})
    assert (doc.snapshot()['large'], doc.snapshot()['count_large']) == ('50.00', '1')
    doc.set(doc.root.handle, 'limit', '1')
    assert doc.snapshot()['large'] == '55.00'

    class Typed(Model):
        Meta = FiniteMeta
        flag = Boolean(default=True)
        number = Decimal(default=1)
        wrong = Boolean(compute=F('flag') == 1)
    with pytest.raises(CalcError, match='COMPUTE_TYPE_ERROR'):
        plan(Typed)

    class Text2(Model):
        Meta = FiniteMeta
        name = Text()
        bad = Decimal(compute=F('name') + 1)
    with pytest.raises(CalcError, match='COMPUTE_TYPE_ERROR'):
        plan(Text2)


def test_legacy_and_finite_routing_stay_separate():
    class Legacy(Model):
        qty = Decimal(default=1)
        doubled = Decimal(compute='qty * 2 if qty else 0')
    assert Registry(Legacy).plans == {}

    class Mixed(Model):
        Meta = FiniteMeta
        qty = Decimal(default=1)
        legacy = Decimal(compute='qty * 2')
    with pytest.raises(CalcError, match='legacy string computes'):
        plan(Mixed)

    with pytest.raises(ValueError, match='scale=None'):
        Decimal(scale=None)
    with pytest.raises(ValueError, match='scale=None'):
        Decimal(scale=None, compute=F('a') + 1)


# States: null, pending, errors and safe operations

def test_states_null_pending_and_safe_operations():
    class States(Model):
        Meta = FiniteMeta
        a = Decimal()
        b = Decimal()
        zero = Decimal(default=0)
        plain = Decimal(scale=None, stored=False, compute=a / zero)
        safe = Decimal(scale=None, stored=False, compute=safe_divide(a, zero, default=7))
        null_safe = Decimal(scale=None, stored=False, compute=safe_divide(b, zero, default=7))
        fallback = Decimal(scale=None, stored=False, compute=coalesce(b, 3))
        error_kept = Decimal(scale=None, stored=False, compute=coalesce(a / zero, 3))
        sum_null = Decimal(scale=None, stored=False, compute=a + b)
        ordered = Boolean(compute=b > 1)
        same = Boolean(compute=F('b') == None)  # noqa: E711 - symbolic null equality
        strict_and = Boolean(compute=(F('a') > 100) & (F('b') > 1))
        strict_or = Boolean(compute=(F('a') > 0) | (a / zero > 1))
    doc = document(States, {'a': '6'})
    snap = doc.snapshot()
    states = snap['_states']
    assert states['plain']['code'] == 'COMPUTE_DIVISION_BY_ZERO' and states['plain']['state'] == 'error'
    assert snap['safe'] == '7' and states['null_safe']['state'] == 'pending'
    assert snap['fallback'] == '3' and states['error_kept']['state'] == 'error'
    assert states['sum_null']['code'] == 'COMPUTE_INPUT_MISSING' and states['ordered']['state'] == 'pending'
    assert snap['same'] is True
    assert states['strict_and']['state'] == 'pending' and states['strict_or']['state'] == 'error'
    doc.set(doc.root.handle, 'b', {'$state': 'pending'})
    assert doc.snapshot()['_states']['fallback']['state'] == 'pending'


def test_bool_casts_and_scale_targets():
    class Casts(Model):
        Meta = FiniteMeta
        flag = Boolean()
        value = Decimal(default=5)
        product = Decimal(compute=flag * value)
        unrounded = Currency(compute=D('1.005') + 0)
        rounded = Currency(compute=round(D('1.005'), 2))
        negative = Currency(compute=round(D('-1.005'), 2))
        whole = Integer(compute=value / 2)
    doc = document(Casts, {'flag': True})
    snap = doc.snapshot()
    assert snap['product'] == '5.0000' and snap['rounded'] == '1.01' and snap['negative'] == '-1.01'
    assert snap['_states']['unrounded']['code'] == 'COMPUTE_SCALE_ERROR'
    assert snap['_states']['whole']['code'] == 'COMPUTE_SCALE_ERROR'
    doc.set(doc.root.handle, 'flag', False)
    assert doc.snapshot()['product'] == '0.0000'
    doc.set(doc.root.handle, 'flag', None)
    assert doc.snapshot()['_states']['product']['state'] == 'pending'


def test_empty_tables_and_excluded_invalid_rows():
    class Item(Child):
        include = Boolean(default=True)
        amount = Decimal()

    class Box(Model):
        Meta = FiniteMeta
        items = Table(Item)
        total = Decimal(compute=items.where(include=True).sum('amount'))
        count = Integer(compute=items.count())
        smallest = Decimal(compute=items.min('amount'))
        smallest_or_zero = Decimal(compute=coalesce(items.min('amount'), 0))
    snap = document(Box).snapshot()
    assert (snap['total'], snap['count'], snap['smallest'], snap['smallest_or_zero']) == ('0.0000', '0', None, '0.0000')
    assert '_states' not in snap
    doc = document(Box, {'children': {'items': [{'_key': 'x', 'include': False}, {'_key': 'y', 'amount': '2'}]}})
    snap = doc.snapshot()
    assert snap['total'] == '2.0000' and snap['_states']['smallest']['state'] == 'pending'


# Structure, batches and publication

def test_structure_commands_batches_and_disposal():
    doc = document(Invoice, {'inclusive': False, **lines({'qty': '1', 'rate': '10'})})
    root = doc.root.handle
    events = []
    doc.observe(root, 'grand_total', lambda result: events.append(text(result.value)))
    doc.observe_table(root, 'items', lambda handles: events.append(len(handles)))

    def edits(cmds):
        new = cmds.add_row(root, 'items', {'_key': 'n', 'qty': '2', 'rate': '5'})
        cmds.set(new, 'rate', '6')
        cmds.batch(lambda inner: inner.set(doc.find('items.r0'), 'qty', '3'))
    doc.batch(edits)
    assert doc.snapshot()['grand_total'] == text(Num(4410, 2), 2) and doc.revision == 2
    assert events == ['44.1', 2]
    with pytest.raises(CalcError, match='COMPUTED_FIELD_WRITE'):
        doc.set(root, 'grand_total', '1')
    with pytest.raises(CalcError, match='COMPUTED_FIELD_WRITE'):
        doc.add_row(root, 'items', {'qty': '1', 'net': '5'})

    def failing(cmds):
        cmds.set(doc.find('items.n'), 'qty', '100')
        raise RuntimeError('stop')
    with pytest.raises(RuntimeError):
        doc.batch(failing)
    assert doc.snapshot()['grand_total'] == '44.10' and doc.revision == 2

    async def later(cmds):
        return None
    with pytest.raises(CalcError, match='ASYNC_BATCH_UNSUPPORTED'):
        doc.batch(later)

    def reenter(result):
        doc.set(root, 'inclusive', True)
    stop = doc.observe(root, 'net_total', reenter)
    with pytest.raises(CalcError, match='REACTIVE_REENTRANCY'):
        doc.set(doc.find('items.n'), 'qty', '4')
    stop()
    stop()
    assert doc.snapshot()['inclusive'] is False and doc.revision == 3

    handles = [doc.find('items.n'), doc.find('items.r0')]
    totals = doc.snapshot()['grand_total']
    doc.reorder(root, 'items', handles)
    assert doc.snapshot()['grand_total'] == totals and [row['_key'] for row in doc.snapshot()['children']['items']] == ['n', 'r0']
    doc.remove_row(handles[0])
    assert doc.snapshot()['children']['items'][0]['_key'] == 'r0'
    with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
        doc.add_row(root, 'items', {'_key': 'r0'})
    doc.dispose()
    assert doc.subscriptions() == 0
    with pytest.raises(CalcError):
        doc.set(root, 'inclusive', True)


def test_nested_moves_removal_and_leaks():
    doc = document(Project, project_data())
    root = doc.root.handle
    task = doc.find('phases.A.tasks.a2')
    doc.observe(task, 'amount', lambda result: None)
    doc.move_row(task, doc.find('phases.B'), 'tasks', 0)
    snap = doc.snapshot()
    assert [phase['total'] for phase in snap['children']['phases']] == ['200.00', '700.00'] and snap['total'] == '900.00'
    with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
        doc.move_row(doc.find('phases.A'), doc.find('phases.B'), 'tasks', 0)
    for _ in range(20):
        phase = doc.add_row(root, 'phases', {'children': {'tasks': [{'hours': '1'}]}})
        doc.observe(phase, 'total', lambda result: None)
        doc.remove_row(phase)
    assert len(doc.rows) == 6 and len(doc.deps) == len(Document(doc.plan, doc.sources()).deps)
    assert doc.subscriptions() == 1
    doc.remove_row(doc.find('phases.B'))
    assert doc.subscriptions() == 0 and doc.snapshot()['total'] == '200.00'


def test_replace_rows_validates_ids_and_keeps_handles():
    doc = document(Invoice, {'inclusive': False, 'children': {'items': [{'id': 7, 'qty': '1', 'rate': '10', 'vat_rate': '5', 'taxable': True}]}})
    root, handle = doc.root.handle, doc.find('items.7')
    doc.replace_rows(root, 'items', [{'id': 7, 'qty': '2'}, {'_key': 'new', 'qty': '1', 'rate': '1'}])
    assert doc.find('items.7') == handle and doc.snapshot()['grand_total'] == '22.05'
    for bad in ([{'id': 8}], [{'id': 7}, {'id': 7}]):
        with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
            doc.replace_rows(root, 'items', bad)
    assert doc.snapshot()['grand_total'] == '22.05'


def test_explain_and_duplicate_recompute():
    doc = document(Invoice, {'inclusive': True, **lines({'qty': '1', 'rate': '105'})})
    info = doc.explain(doc.root.handle, 'net_total')
    assert info['role'] == 'computed' and 'items.r0.net' in info['inputs'] and info['profile'] == 'finite40-hu18-v1'
    copy = Document(doc.plan, doc.sources(identities=False))
    assert copy.snapshot()['grand_total'] == doc.snapshot()['grand_total']


def test_plan_fingerprint_is_deterministic_and_checked():
    first, second = plan(Invoice), plan(Invoice)
    assert first['model_schema_hash'] == second['model_schema_hash']
    tampered = dict(first, fields=first['fields'][:-1])
    with pytest.raises(CalcError, match='SCHEMA_VERSION_MISMATCH'):
        Document(tampered)
    with pytest.raises(CalcError, match='SCHEMA_VERSION_MISMATCH'):
        Document(dict(first, numeric_profile='decimal28-half-up-v1'))


def test_incremental_settlement_matches_full_recomputation():
    generator = random.Random(7)
    doc = document(Invoice, {'inclusive': True, **lines(*[{'qty': str(i + 1), 'rate': '1.10'} for i in range(5)])})
    for step in range(60):
        handles = [handle for handle, row in doc.rows.items() if row.path == ('items',)]
        choice = generator.random()
        if choice < 0.5 and handles:
            doc.set(generator.choice(handles), generator.choice(['qty', 'rate', 'vat_rate']), str(generator.randint(0, 900) / 100))
        elif choice < 0.65:
            doc.set(doc.root.handle, 'inclusive', generator.random() < 0.5)
        elif choice < 0.8 or not handles:
            doc.add_row(doc.root.handle, 'items', {'qty': str(generator.randint(1, 5)), 'rate': '2.5'})
        elif choice < 0.9:
            doc.set(generator.choice(handles), 'taxable', generator.random() < 0.5)
        else:
            doc.remove_row(generator.choice(handles))
        fresh = Document(doc.plan, doc.sources())
        assert strip(fresh.snapshot()) == strip(doc.snapshot()), step


def strip(snapshot):
    if isinstance(snapshot, dict):
        return {key: strip(value) for key, value in snapshot.items() if key != '_handle'}
    if isinstance(snapshot, list):
        return [strip(item) for item in snapshot]
    return snapshot


def test_limits_and_budget():
    many = {'children': {'items': [{'qty': '1', 'rate': '1'} for _ in range(101)]}}
    with pytest.raises(CalcError, match='CALCULATION_LIMIT_EXCEEDED'):
        document(Invoice, many)
    with pytest.raises(CalcError, match='CALCULATION_LIMIT_EXCEEDED'):
        Document(plan(Invoice), lines(*[{'qty': '1'} for _ in range(50)]), budget=100)


# Numeric profile against an independent Decimal reference

def reference(op, a, b=None):
    with localcontext() as context:
        context.prec = 200
        x, y = Reference(a), Reference(b) if b is not None else None
        if op == 'div':
            return (x / y).quantize(Reference('1e-18'), rounding=ROUND_HALF_UP).normalize()
        return {'add': x + (y or 0), 'sub': x - (y or 0), 'mul': x * (y or 1)}[op].normalize()


def plain(value):
    return format(value, 'f') if value != 0 else '0'


@pytest.mark.parametrize('a,b', [('1', '3'), ('-1', '3'), ('2', '3'), ('0.000000000000000001', '2'), ('-0.000000000000000001', '2'),
                                  ('123456.789', '-0.001'), ('0', '-5'), ('99999999999999999999', '0.07'), ('5', '8')])
def test_numeric_profile_matches_reference(a, b):
    for op, function in (('add', add), ('sub', sub), ('mul', mul), ('div', div)):
        assert text(function(parse(a), parse(b))) == plain(reference(op, a, b)), (op, a, b)


def test_numeric_boundaries():
    assert text(quantize(parse('1.005'), 2)) == '1.01' and text(quantize(parse('-1.005'), 2)) == '-1.01'
    assert text(quantize(parse('1.004999'), 2)) == '1' and text(quantize(parse('-0.0'), 0)) == '0'
    assert text(div(parse('0.000000000000000001'), parse('2'))) == '0.000000000000000001'
    assert text(div(parse('-0.000000000000000001'), parse('2'))) == '-0.000000000000000001'
    big = parse('9' * 40)
    assert text(total([big, big, Num(-int('9' * 40), 0)])) == '9' * 40
    with pytest.raises(CalcError, match='COMPUTE_RANGE_ERROR'):
        total([big, big])
    with pytest.raises(CalcError, match='COMPUTE_RANGE_ERROR'):
        parse('0.' + '1' * 37)
    for bad in ('1e5', 'NaN', 'Infinity', '1.', '.5', '01', True, 1.5, '1' * 129, 2**60):
        with pytest.raises(CalcError):
            parse(bad)
    assert compare(parse('1.10'), parse('1.1')) == 0 and parse('1.10') == parse('1.1')
    generator = random.Random(11)
    for _ in range(300):
        a = str(generator.randint(-10**12, 10**12)) + '.' + str(generator.randint(0, 999999)).zfill(6)
        b = str(generator.randint(1, 10**9)) + '.' + str(generator.randint(0, 99)).zfill(2)
        for op, function in (('add', add), ('sub', sub), ('mul', mul), ('div', div)):
            assert text(function(parse(a), parse(b))) == plain(reference(op, a, b))


def test_ref_defaults_copy_parent_values_once():
    class Line(Child):
        warehouse = Text(default=Ref('parent.warehouse'))
        qty = Decimal(default=1)

    class Order(Model):
        Meta = FiniteMeta
        warehouse = Text(default='Main')
        lines = Table(Line)
        total = Decimal(compute=lines.sum('qty'))
    doc = document(Order)
    row_handle = doc.add_row(doc.root.handle, 'lines')
    assert doc.rows[row_handle].values['warehouse'] == 'Main'
    doc.set(doc.root.handle, 'warehouse', 'Other')
    assert doc.rows[row_handle].values['warehouse'] == 'Main'


def test_browser_fixtures_match_python_plans_and_results():
    import json
    from pathlib import Path
    from finite_fixtures import collect, NUMERIC
    path = Path(__file__).resolve().parents[1] / 'frontend/src/tests/finite-fixtures.json'
    assert json.loads(path.read_text()) == json.loads(json.dumps(collect())), 'Run scripts/generate_finite_fixtures.py'
    for op, a, b in NUMERIC:
        if op != 'round':
            assert text({'add': add, 'sub': sub, 'mul': mul, 'div': div}[op](parse(a), parse(b))) == plain(reference(op, a, b))


def test_review_regressions_for_engine_edges():
    from decimal import Decimal as PyDecimal
    from evlet.calc import Expr, to_decimal
    qty = Decimal()
    assert isinstance(qty == 1, Expr) and isinstance(qty != None, Expr)  # noqa: E711
    assert (qty == qty) is True and (qty == Decimal()) is False
    with pytest.raises(CalcError, match='COMPUTE_RANGE_ERROR'):
        parse(PyDecimal('1E+5000'))
    assert to_decimal(parse('1234567890123456789012345678.12345')) == PyDecimal('1234567890123456789012345678.12345')
    with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
        document(Invoice, {'children': {'items': [{'id': True}]}})
    doc = document(Invoice, {'inclusive': False})
    root = doc.root.handle

    def failing(cmds):
        new = cmds.add_row(root, 'items', {'qty': '1', 'rate': '7'})
        cmds.observe(new, 'total', lambda result: None)
        raise RuntimeError('stop')
    with pytest.raises(RuntimeError):
        doc.batch(failing)
    assert doc.subscriptions() == 0 and len(doc.rows) == 1
    doc.observe(root, 'grand_total', lambda result: doc.dispose())
    with pytest.raises(CalcError, match='REACTIVE_REENTRANCY'):
        doc.add_row(root, 'items', {'qty': '1', 'rate': '1'})
    assert doc.snapshot()['grand_total'] == '1.05'
    with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
        doc.observe(root, 'missing', lambda result: None)
