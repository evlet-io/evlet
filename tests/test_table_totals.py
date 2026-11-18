"""Table(totals=...): fields of the owning model shown under a table, in order (docs/ui.md, Record layouts)."""

import pytest

from evlet import Child, Currency, Database, Decimal, Main, Model, Percent, Prompt, Registry, Section, Table, Text
from evlet.calc import F
from evlet.prompts import schema as prompt_schema
from evlet.ui.metadata import view_for
from test_business import client
from test_meaning import serve
from test_rules_types import ALL


class Line(Child):
    qty = Decimal(default='1')
    rate = Currency(currency='AED', default='0')
    amount = Currency(currency='AED', compute=F('qty') * F('rate'))


def quotation(totals=('net_total', 'discount_percent', 'grand_total'), layout=None, **extra):
    """A fresh owning model with lines, a discount and a grand total; extra adds fields or tables."""
    namespace = {'permissions': ALL, '__module__': __name__, 'customer': Text(), **extra}
    namespace['lines'] = lines = Table(Line, totals=totals)
    namespace['net_total'] = Currency(currency='AED', compute=lines.sum('amount'))
    namespace['discount_percent'] = Percent(default='0')
    namespace['grand_total'] = Currency(currency='AED', compute=F('net_total') * (100 - F('discount_percent')) / 100)
    if layout is not None:
        namespace['Meta'] = type('Meta', (), {'layout': layout})
    return type('Quotation', (Model,), namespace)


@pytest.mark.parametrize('totals, extra, message', [
    (('net_total', 'nope'), {}, "Quotation.lines: totals names 'nope', which is not a field of Quotation"),
    (('net_total', 'notes'), {'notes': Table(Line)}, "Quotation.lines: totals names 'notes', which is a Table"),
    (('net_total', 'secret'), {'secret': Text(hidden=True)}, "Quotation.lines: totals names 'secret', which is hidden=True"),
    (('net_total', 'grand_total', 'net_total'), {}, "Quotation.lines: totals names 'net_total' twice"),
    (('net_total', 42), {}, 'Table totals must be a tuple of field names'),
])
def test_registration_names_the_model_table_and_field(totals, extra, message):
    with pytest.raises(ValueError, match=__import__('re').escape(message)):
        quotation(totals, **extra)


def test_a_field_belongs_to_the_totals_of_one_table():
    with pytest.raises(ValueError, match="Quotation.lines: totals names 'grand_total', which is already in the totals of extras"):
        quotation(extras=Table(Line, totals=('grand_total',)))


def test_the_schema_carries_totals_and_a_table_without_them_is_unchanged(tmp_path):
    class Order(Model):
        permissions = ALL
        lines = Table(Line)
        total = Currency(currency='AED', compute=lines.sum('amount'))
    Quotation = quotation()
    app = serve(Database(tmp_path / 'totals.sqlite3', Registry(Quotation, Order)))
    models = {item['name']: item for item in client(app)('GET', '/api/schema')[1]['models']}
    assert models['quotation']['children'][0]['totals'] == ['net_total', 'discount_percent', 'grand_total']
    assert 'totals' not in models['order']['children'][0]
    # Nothing new is stored: the totals are the model's ordinary fields.
    assert [field['name'] for field in models['quotation']['fields'] if field['name'] != 'name'][:4] == ['customer', 'net_total', 'discount_percent', 'grand_total']


def test_a_totals_field_counts_as_placed_and_a_block_naming_it_is_warned_about():
    # Without Meta.layout: Details leaves the totals out, and nothing falls to Other.
    assert [(group['label'], group['fields']) for group in view_for(quotation())['layout']] == [('Details', ['customer'])]
    registry = Registry(quotation(layout=(Main(('customer',)), Main('lines'), Section('Money', ('discount_percent',)))))
    model = registry.models['quotation']
    (_, text), = [item for item in registry.layout_warnings if item[0] is model]
    assert "names 'discount_percent', which shows in the totals of the Table 'lines'; it is ignored" in text
    assert [(group['label'], group['fields']) for group in view_for(model)['layout']] == [('Quotation', ['customer'])]


class Bill(Prompt):
    class Meta:
        title = 'Bill'
    lines = Table(Line, totals=('net_total', 'grand_total'))
    net_total = Currency(currency='AED', compute=lines.sum('amount'))
    grand_total = Currency(currency='AED', compute=F('net_total'))


def test_a_prompt_table_carries_its_totals_and_keeps_them_out_of_its_fields():
    entry = prompt_schema(Registry(), Bill, '_prompt.bill')[0]
    assert entry['children'][0]['totals'] == ['net_total', 'grand_total']
    assert all(block['kind'] == 'table' for block in entry['record_layout']['blocks'])
