"""Finite-v1 language: authoring, compiler checks, numeric profile and propagation shapes."""

from decimal import Decimal as Reference, ROUND_HALF_UP, localcontext
from decimal import Decimal as PyDecimal
import random

import pytest

from evlet import Model, Child, Table, Decimal, Currency, Boolean, Integer, Text, Registry
from evlet.calc import (D, F, parent, row, round, coalesce, safe_divide, Document, CalcError, Num, parse, text,
                        add, sub, mul, div, quantize, total, compare)
from evlet.declarations import Ref
from finite_fixtures import sources, Capped as CappedFixture


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
    doc.set(doc.root.handle, 'inclusive', False)
    item = doc.snapshot()['children']['items'][0]
    assert (item['net'], item['vat'], item['total']) == ('105.00', '5.25', '110.25')
    assert doc.snapshot()['grand_total'] == '110.25' and doc.revision == 2


def test_row_edit_only_recalculates_that_row_and_its_aggregates():
    doc = document(Invoice, {'inclusive': False, **lines(*[{'qty': '2', 'rate': '150'} for _ in range(3)])})
    assert doc.snapshot()['children']['items'][0]['entered_amount'] == '300.00'
    first, second = doc.find('items.r0'), doc.find('items.r1')
    before = doc.counts[second, 'net']
    doc.set(first, 'qty', '3')
    assert doc.counts[second, 'net'] == before
    assert 'items.r1.net' not in doc.trace['changed'] and 'items.r0.net' in doc.trace['changed']
    assert doc.snapshot()['grand_total'] == text(Num(47250 + 31500 + 31500, 2), 2)


def test_row_edit_work_does_not_grow_with_the_number_of_rows():
    # The shared fixtures no longer compare evaluation counts, so each engine guards this itself.
    def evaluated(count):
        doc = document(Invoice, {'inclusive': False, **lines(*[{'qty': '2', 'rate': '150'} for _ in range(count)])})
        doc.set(doc.find('items.r0'), 'qty', '3')
        return doc.trace['evaluated']
    assert evaluated(3) == evaluated(30)


def test_parent_toggle_settles_in_one_revision():
    doc = document(Invoice, {'inclusive': True, **lines(*[{'qty': '1', 'rate': '105'} for _ in range(3)])})
    doc.set(doc.root.handle, 'inclusive', False)
    snap = doc.snapshot()
    assert doc.revision == 2 and snap['grand_total'] == '330.75'
    assert [item['net'] for item in snap['children']['items']] == ['105.00', '105.00', '105.00']


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


def test_diamond_settles_final_values_together():
    class Diamond(Model):
        Meta = FiniteMeta
        a = Decimal(default=0)
        b = Decimal(compute=a + 10)
        c = Decimal(compute=a * 3)
        d = Decimal(compute=b + c)
    doc = document(Diamond, {'a': '1'})
    doc.set(doc.root.handle, 'a', '4')
    snap = doc.snapshot()
    assert (snap['b'], snap['c'], snap['d']) == ('14.0000', '12.0000', '26.0000') and doc.revision == 2


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
    # Results are quantized to the target scale with the field's rounding (Currency and Integer: half up).
    assert snap['unrounded'] == '1.01' and snap['whole'] == '3' and '_states' not in snap
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

def test_structure_commands_and_batches():
    doc = document(Invoice, {'inclusive': False, **lines({'qty': '1', 'rate': '10'})})
    root = doc.root.handle

    def edits(cmds):
        new = cmds.add_row(root, 'items', {'_key': 'n', 'qty': '2', 'rate': '5'})
        cmds.set(new, 'rate', '6')
        cmds.batch(lambda inner: inner.set(doc.find('items.r0'), 'qty', '3'))
    doc.batch(edits)
    assert doc.snapshot()['grand_total'] == text(Num(4410, 2), 2) and doc.revision == 2
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

    handles = [doc.find('items.n'), doc.find('items.r0')]
    totals = doc.snapshot()['grand_total']
    doc.reorder(root, 'items', handles)
    assert doc.snapshot()['grand_total'] == totals and [row['_key'] for row in doc.snapshot()['children']['items']] == ['n', 'r0']
    doc.remove_row(handles[0])
    assert doc.snapshot()['children']['items'][0]['_key'] == 'r0'
    with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
        doc.add_row(root, 'items', {'_key': 'r0'})


def test_nested_additions_and_removal_leave_no_rows_behind():
    doc = document(Project, project_data())
    root = doc.root.handle
    doc.remove_row(doc.find('phases.A.tasks.a2'))
    doc.add_row(doc.find('phases.B'), 'tasks', {'_key': 'a2', 'hours': '3'})
    snap = doc.snapshot()
    assert [phase['total'] for phase in snap['children']['phases']] == ['200.00', '700.00'] and snap['total'] == '900.00'
    with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
        doc.add_row(doc.find('phases.A'), 'phases', {})
    for _ in range(20):
        phase = doc.add_row(root, 'phases', {'children': {'tasks': [{'hours': '1'}]}})
        doc.remove_row(phase)
    assert len(doc.rows) == 6 and len(doc.deps) == len(Document(doc.plan, sources(doc)).deps)
    doc.remove_row(doc.find('phases.B'))
    assert doc.snapshot()['total'] == '200.00'


def test_replace_rows_validates_ids_and_keeps_handles():
    doc = document(Invoice, {'inclusive': False, 'children': {'items': [{'id': 7, 'qty': '1', 'rate': '10', 'vat_rate': '5', 'taxable': True}]}})
    root, handle = doc.root.handle, doc.find('items.7')
    doc.replace_rows(root, 'items', [{'id': 7, 'qty': '2'}, {'_key': 'new', 'qty': '1', 'rate': '1'}])
    assert doc.find('items.7') == handle and doc.snapshot()['grand_total'] == '22.05'
    for bad in ([{'id': 8}], [{'id': 7}, {'id': 7}]):
        with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
            doc.replace_rows(root, 'items', bad)
    assert doc.snapshot()['grand_total'] == '22.05'
    capped = Document(Registry(CappedFixture).plans[CappedFixture], {'children': {'lines': [{'_key': 'a'}]}})
    with pytest.raises(CalcError, match='CALCULATION_LIMIT_EXCEEDED'):
        capped.replace_rows(capped.root.handle, 'lines', [{'_key': 'a'}, {'_key': 'b'}, {'_key': 'c'}])
    assert len(capped.snapshot()['children']['lines']) == 1


def test_duplicate_recompute():
    doc = document(Invoice, {'inclusive': True, **lines({'qty': '1', 'rate': '105'})})
    copy = Document(doc.plan, sources(doc, identities=False))
    assert copy.snapshot()['grand_total'] == doc.snapshot()['grand_total']


def test_plan_fingerprint_is_deterministic_and_checked():
    first, second = plan(Invoice), plan(Invoice)
    assert first['model_schema_hash'] == second['model_schema_hash']
    tampered = dict(first, fields=first['fields'][:-1])
    with pytest.raises(CalcError, match='SCHEMA_VERSION_MISMATCH'):
        Document(tampered)
    with pytest.raises(CalcError, match='SCHEMA_VERSION_MISMATCH'):
        Document(dict(first, numeric_profile='decimal28-half-up-v1'))


def test_formula_hash_ignores_fields_and_settings_formulas_do_not_use():
    from evlet.calc import formula_hash

    def build(note=False, default=1, factor=1, minimum=None):
        class Line(Child):
            qty = Decimal(default=default)
            rate = Currency(default=0)
            amount = Currency(compute=round(qty * rate * factor, 2), minimum=minimum)
        lines_table = Table(Line)
        namespace = {'Meta': FiniteMeta, 'lines': lines_table, 'total': Currency(compute=lines_table.sum('amount'))}
        if note:
            namespace['note'] = Text(default='')
        return plan(type('Order', (Model,), {'__module__': __name__, **namespace}))
    base = build()
    for unrelated in (build(note=True), build(default=5)):
        assert unrelated['model_schema_hash'] != base['model_schema_hash']
        assert formula_hash(unrelated) == formula_hash(base)
    for related in (build(factor=2), build(minimum='0')):
        assert formula_hash(related) != formula_hash(base)


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
        fresh = Document(doc.plan, sources(doc))
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
    path = Path(__file__).resolve().parents[1] / 'frontend/src/evlet/tests/finite-fixtures.json'
    assert json.loads(path.read_text()) == json.loads(json.dumps(collect())), 'Run scripts/generate_finite_fixtures.py'
    for op, a, b in NUMERIC:
        if not op.startswith('round'):
            assert text({'add': add, 'sub': sub, 'mul': mul, 'div': div}[op](parse(a), parse(b))) == plain(reference(op, a, b))


def test_review_regressions_for_engine_edges():
    from decimal import Decimal as PyDecimal
    from evlet.calc import Expr, to_decimal
    qty = Decimal()
    assert isinstance(qty == 1, Expr) and isinstance(qty != None, Expr) and isinstance(F('qty') == None, Expr)  # noqa: E711

    class NullCheck(Model):
        Meta = FiniteMeta
        qty = Decimal()
        flagged = Decimal(compute=(qty == None) * 5 + coalesce(qty, 0))  # noqa: E711
    assert document(NullCheck).snapshot()['flagged'] == '5.0000'
    assert document(NullCheck, {'qty': '3'}).snapshot()['flagged'] == '3.0000'
    assert (qty == qty) is True and (qty == Decimal()) is False
    with pytest.raises(CalcError, match='COMPUTE_RANGE_ERROR'):
        parse(PyDecimal('1E+5000'))
    assert to_decimal(parse('1234567890123456789012345678.12345')) == PyDecimal('1234567890123456789012345678.12345')
    with pytest.raises(CalcError, match='DOCUMENT_STRUCTURE_ERROR'):
        document(Invoice, {'children': {'items': [{'id': True}]}})
    doc = document(Invoice, {'inclusive': False})
    root = doc.root.handle

    def failing(cmds):
        cmds.add_row(root, 'items', {'qty': '1', 'rate': '7'})
        raise RuntimeError('stop')
    with pytest.raises(RuntimeError):
        doc.batch(failing)
    assert len(doc.rows) == 1


def test_allocation_residuals_are_explicit_not_silently_lost():
    class Share(Child):
        weight = Decimal(default=1)
        allocated = Currency(compute=round(parent.amount * weight / parent.weights, 2))

    class Allocation(Model):
        Meta = FiniteMeta
        amount = Currency(default='0.01')
        shares = Table(Share)
        weights = Decimal(compute=shares.sum('weight'))
        allocated_total = Currency(compute=shares.sum('allocated'))
        residual = Currency(compute=amount - allocated_total)
    snap = document(Allocation, {'children': {'shares': [{'_key': k} for k in 'abc']}}).snapshot()
    assert [row['allocated'] for row in snap['children']['shares']] == ['0.00', '0.00', '0.00']
    assert (snap['allocated_total'], snap['residual']) == ('0.00', '0.01')


def test_second_review_engine_regressions():
    from copy import deepcopy
    from evlet.calc import fingerprint
    # The emitted IR (with Boolean conversions) is what must fit the expression limits.
    def balanced(n): return F('flag') if n == 1 else balanced(n // 2) + balanced(n - n // 2)

    wide = type(Model)('Wide', (Model,), {'Meta': FiniteMeta, 'flag': Boolean(default=True), 'score': Decimal(scale=0, compute=balanced(64)), '__module__': __name__})
    with pytest.raises(CalcError, match='CALCULATION_LIMIT_EXCEEDED'):
        plan(wide)
    assert document(type(Model)('Fits', (Model,), {'Meta': FiniteMeta, 'flag': Boolean(default=True), 'score': Decimal(scale=0, compute=balanced(40)), '__module__': __name__})).snapshot()['score'] == '40'

    # A failed command whose error the batch catches leaves the document unchanged.
    doc = document(Invoice, {'inclusive': False, 'children': {'items': [{'_key': 'a', 'qty': '1', 'rate': '10'}, {'_key': 'b', 'qty': '2', 'rate': '10'}]}})
    before, rows = doc.snapshot(), len(doc.rows)
    a = doc.find('items.a')

    def run(commands):
        for attempt in (lambda: commands.reorder(doc.root.handle, 'items', [a]),
                        lambda: commands.reorder(doc.root.handle, 'items', [a, a]),
                        lambda: commands.add_row(doc.root.handle, 'items', {'_key': 'b', 'qty': '5'})):
            with pytest.raises(CalcError):
                attempt()
    doc.batch(run)
    assert doc.snapshot() == before and len(doc.rows) == rows
    doc.reorder(doc.root.handle, 'items', [doc.find('items.b'), a])
    assert [item['_key'] for item in doc.snapshot()['children']['items']] == ['b', 'a']

    # Malformed plans fail as calculation errors, never as Python exceptions.
    source = plan(Invoice)
    for mutate in (lambda node: node.update({'$expr': 'add', 'args': []}),
                   lambda node: node.update({'$expr': 'add', 'args': [{'$expr': 'literal', 'type': 'integer', 'value': 1}] * 2}),
                   lambda node: node.update({'$expr': 'round', 'args': [{'$expr': 'literal', 'type': 'decimal', 'value': '1'}] * 2}),
                   lambda node: node.update({'$expr': 'aggregate', 'kind': 'sum', 'table': 'items', 'field': 'net'}),
                   lambda node: node.update({'$expr': 'literal', 'type': 'decimal', 'value': 'NaN'})):
        crafted = deepcopy(source)
        mutate(crafted['fields'][0]['expression'])
        crafted['model_schema_hash'] = fingerprint(crafted)
        with pytest.raises(CalcError):
            Document(crafted, {})

    # Declared bounds and the scaled range hold in the engine, as on the server.
    class Bounded(Model):
        Meta = FiniteMeta
        price = Currency(default=0)
        cost = Currency(default=0)
        profit = Currency(compute=price - cost, minimum='0')
        huge = Currency(stored=False, compute=price * D('100000000000000000'))
    result = document(Bounded, {'price': '5', 'cost': '8'})
    assert result.results[result.root.handle, 'profit'].code == 'COMPUTE_RANGE_ERROR'
    assert result.results[result.root.handle, 'huge'].code == 'COMPUTE_RANGE_ERROR'

    # Equality with values that are not calculation operands keeps plain Python semantics.
    field = Decimal()
    assert field in [{'k': 1}, field] and [object(), field].index(field) == 1 and (field == 1.5) is False


def test_rolled_back_commands_keep_earlier_batch_rows_and_bounds_accept_exponents():
    doc = document(Invoice, {'inclusive': False})
    root = doc.root.handle

    def run(commands):
        commands.add_row(root, 'items', {'qty': '2', 'rate': '10'})
        with pytest.raises(CalcError):
            commands.set('nope', 'qty', '1')
        with pytest.raises(RuntimeError):
            commands.batch(lambda inner: (inner.set(root, 'inclusive', True), (_ for _ in ()).throw(RuntimeError('stop'))))
    doc.batch(run)
    assert doc.snapshot()['grand_total'] == Document(plan(Invoice), sources(doc)).snapshot()['grand_total'] == '21.00'
    assert doc.snapshot()['inclusive'] is False

    class Capped(Model):
        Meta = FiniteMeta
        a = Decimal(scale=2, default=1)
        b = Decimal(scale=2, compute=a * 2, maximum='1E+3', minimum=PyDecimal('-1E+2'))
    capped = plan(Capped)
    assert capped['tables']['$root']['fields']['b']['maximum'] == '1000'
    assert Document(capped, {'a': '600'}).results[(Document(capped, {}).root.handle, 'b')].code == 'COMPUTE_RANGE_ERROR'


# Phase 1 additions: auto-rounding, dates, Link paths and when()

def test_formula_hashes_of_existing_fixture_models_are_unchanged():
    from finite_fixtures import MODELS, LOOKUP_MODELS
    registry = Registry(*MODELS.values(), *LOOKUP_MODELS)
    # Recorded before rounding, dates, Link paths and when() were added: saved documents stay writable.
    assert {name: registry.formula_hashes[MODELS[name]] for name in ('invoice', 'project', 'cost', 'states')} == {
        'invoice': 'sha256:3e1dd4bbee2d54a3378254f56fce575409dec29671fe202af97efd08e8adaea5',
        'project': 'sha256:39993470d571dd469180910aadd12210d59218b5e89f72c4d0b6ec5315b43351',
        'cost': 'sha256:1af8439ec26a35bbcf48912510fed60f569aebf8c8e8e09d2de7ca6154bca7bf',
        'states': 'sha256:55edc5618ae03cc26efcb1423bab708f87aae64a6ecd4d41fec3352226b805e0'}


def test_rounding_defaults_options_and_hash():
    from evlet.calc import formula_hash
    assert (Decimal().rounding, Currency().rounding, Integer().rounding) == ('half_even', 'half_up', 'half_up')
    assert Currency(rounding='half_even').rounding == 'half_even' and Decimal(rounding='half_up').rounding == 'half_up'
    with pytest.raises(ValueError, match='rounding'):
        Decimal(rounding='down')

    def build(**options):
        class Rounded(Model):
            Meta = FiniteMeta
            a = Decimal()
            b = Decimal(scale=2, compute=F('a'), **options)
        return plan(Rounded)
    default, explicit, other = build(), build(rounding='half_even'), build(rounding='half_up')
    assert next(field for field in default['fields'] if field['field'] == 'b')['rounding'] == 'half_even'
    assert formula_hash(default) == formula_hash(explicit) != formula_hash(other)
    assert Document(default, {'a': '0.125'}).snapshot()['b'] == '0.12'
    assert Document(other, {'a': '0.125'}).snapshot()['b'] == '0.13'

    class Bounded(Model):
        Meta = FiniteMeta
        a = Decimal()
        capped = Currency(compute=F('a'), maximum='1')
        exact = Decimal(scale=2, compute=round(F('a'), 2))
        full = Decimal(scale=None, stored=False, compute=F('a') / 8)
    snap = document(Bounded, {'a': '1.004'}).snapshot()
    assert snap['capped'] == '1.00' and snap['exact'] == '1.00' and snap['full'] == '0.1255'
    snap = document(Bounded, {'a': '1.005'}).snapshot()
    # Rounding happens before the bounds check; a rounded value outside them is still a range error.
    assert snap['_states']['capped']['code'] == 'COMPUTE_RANGE_ERROR' and snap['exact'] == '1.01'


def test_quantize_half_even_matches_the_decimal_module():
    from decimal import ROUND_HALF_EVEN
    generator = random.Random(11)
    for _ in range(500):
        value = str(generator.randint(-10**8, 10**8)) + '.' + str(generator.randint(0, 999)).rjust(3, '0')
        scale = generator.randint(0, 2)
        with localcontext() as context:
            context.prec = 60
            expected = Reference(value).quantize(Reference(1).scaleb(-scale), rounding=ROUND_HALF_EVEN)
        assert parse(text(quantize(parse(value), scale, 'half_even'))) == parse(str(expected)), (value, scale)


def test_date_expressions_reject_mixed_types_at_registry_build():
    from evlet import Date, DateTime
    from evlet.calc import days, today, when

    def build(**fields):
        return Registry(type('Dated', (Model,), {'__module__': __name__, 'Meta': FiniteMeta, 'start': Date(), 'end': Date(),
                                                 'n': Decimal(), 'flag': Boolean(), 'at': DateTime(), **fields}))
    for compute, target in [(F('start') + 1, Date), (F('start') * 2, Decimal), (F('start') + F('end'), Date), (days(1), Date),
                            (days(1) - F('start'), Date), (F('start') < 1, Boolean), (days(1) == days(2), Boolean),
                            (-F('start'), Date), (round(F('start'), 0), Decimal), (F('start') - F('end'), Date),
                            (F('start') + days(F('start')), Date), (coalesce(F('start'), 1), Date), (F('n') + days(1), Decimal),
                            (when(F('n'), 1, 2), Decimal), (when(F('flag'), F('start'), 1), Date), (when(F('flag'), days(1), days(2)), Date),
                            (today() + days(1), Decimal), (F('start') + days(True), Date), (F('start') + days(F('flag')), Date),
                            (today(), DateTime), (F('at') + days(1), DateTime), (F('at') < F('at'), Boolean)]:
        with pytest.raises(CalcError, match='COMPUTE_TYPE_ERROR'):
            build(result=target(compute=compute))
    registry = build(result=Date(compute=F('start') - days(F('n'))), gap=Decimal(compute=F('end') - F('start')),
                     later=Boolean(compute=(F('end') >= F('start')) & (today() < F('end'))), pick=Date(compute=when(F('flag'), F('start'), None)))
    expressions = {field['field']: field['expression']['$expr'] for field in next(iter(registry.plans.values()))['fields']}
    assert expressions == {'result': 'date_sub', 'gap': 'date_diff', 'later': 'and', 'pick': 'when'}


def test_link_paths_are_validated_at_registry_build():
    from evlet import Link, Date
    from evlet.calc import days

    class Country(Model):
        offset = Integer(default=0)
        virtual = Decimal(compute=F('offset') * 1, stored=False, scale=None)

    class Area(Model):
        country = Link(Country)
        label = Text(default='')

    class Party(Model):
        area = Link(Area)
        days_due = Integer(default=30)
        when_seen = Date()

    def build(compute, target=Decimal):
        namespace = {'__module__': __name__, 'Meta': FiniteMeta, 'party': Link(Party), 'note': Text(default=''), 'result': target(compute=compute)}
        return Registry(Country, Area, Party, type('Doc', (Model,), namespace))
    for path, code in [('party.missing', 'UNSUPPORTED_EXPRESSION'), ('missing.days_due', 'UNSUPPORTED_EXPRESSION'),
                       ('party.days_due.extra', 'COMPUTE_TYPE_ERROR'), ('note.length', 'COMPUTE_TYPE_ERROR'),
                       ('party.area.country.offset.more', 'CALCULATION_LIMIT_EXCEEDED'),
                       ('party.area.country.virtual', 'UNSUPPORTED_EXPRESSION'), ('party.id', 'UNSUPPORTED_EXPRESSION')]:
        with pytest.raises(CalcError, match=code):
            build(F(path))
    with pytest.raises(CalcError, match='COMPUTE_TYPE_ERROR'):
        build(F('party.area.label'))
    for bad in ('party..x', 'party.', '.x', 'party.1x', 'a.b c'):
        with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
            F(bad)
    with pytest.raises(CalcError, match='UNSUPPORTED_EXPRESSION'):
        parent.party.days_due
    registry = build(F('party.area.country.offset') + F('party.days_due'))
    doc_plan = registry.plans[registry.models['doc']]
    field = doc_plan['fields'][0]
    assert field['expression']['args'][0] == {'$expr': 'hop', 'field': 'party', 'steps': [['party', 'area', 'link'], ['area', 'country', 'link'], ['country', 'offset', 'decimal']]}
    assert doc_plan['tables']['$root']['lookups'] == [{'model': 'area', 'fields': ['country']}, {'model': 'country', 'fields': ['offset']},
                                                      {'model': 'party', 'fields': ['area', 'days_due']}]
    dated = build(F('party.when_seen') + days(F('party.days_due')), Date)
    assert dated.plans[dated.models['doc']]['fields'][0]['expression']['$expr'] == 'date_add'

    doc = Document(doc_plan, {'party': 4})
    assert doc.lookup_requests() == [['party', '0000000000004', ['area', 'days_due']]]
    doc.set_lookups('party', 4, {'area': 2, 'days_due': 10})
    assert doc.lookup_requests() == [['area', '0000000000002', ['country']]]
    doc.set_lookups('area', 2, {'country': 1})
    doc.set_lookups('country', 1, {'offset': '5'})
    assert doc.snapshot()['result'] == '15.0000' and doc.lookup_requests() == []
    assert ('@country', 'offset') in doc.deps[doc.root.handle, 'result']
    # A failed command inside a batch leaves the lookups as they were.
    doc.batch(lambda session: pytest.raises(CalcError, session.set_lookups, 'country', 1, {'nope': 1}))
    with pytest.raises(RuntimeError):
        doc.batch(lambda session: (session.set_lookups('country', 1, {'offset': '6'}), (_ for _ in ()).throw(RuntimeError())))
    assert doc.lookups['country'][1] == {'offset': '5'} and doc.snapshot()['result'] == '15.0000'
    doc.set_lookups('area', 2, {'country': None})
    assert doc.snapshot()['_states']['result']['code'] == 'COMPUTE_INPUT_MISSING' and doc.lookup_requests() == []
    doc.set(doc.root.handle, 'party', True)
    assert doc.snapshot()['_states']['result']['code'] == 'COMPUTE_TYPE_ERROR'


def test_datetime_references_keep_their_previous_behaviour():
    from evlet import DateTime
    from evlet.calc import days

    class Moments(Model):
        Meta = FiniteMeta
        at = DateTime()
        at2 = DateTime()
        same = Boolean(compute=F('at') == F('at2'))
        empty = Boolean(compute=F('at') == None)  # noqa: E711
        first = DateTime(compute=coalesce(F('at'), F('at2')))
    first = '2026-11-01T08:00:00.000000Z'
    snap = document(Moments, {'at': None, 'at2': first}).snapshot()
    assert (snap['same'], snap['empty'], snap['first']) == (False, True, first)
    assert document(Moments, {'at': first, 'at2': first}).snapshot()['same'] is True
    for compute, target in [(F('at') < F('at2'), Boolean), (F('at') + days(1), DateTime), (F('at') - F('at2'), Decimal)]:
        with pytest.raises(CalcError, match='COMPUTE_TYPE_ERROR'):
            plan(type('Bad', (Model,), {'__module__': __name__, 'Meta': FiniteMeta, 'at': DateTime(), 'at2': DateTime(), 'result': target(compute=compute)}))
