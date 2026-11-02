"""A finite-v1 document: one formula definition for browser previews and server saves."""
from evlet import Model, Child, Table, Decimal, Currency, Boolean, Text, Rule, Link, Date, Integer, Percent, Select, Tags
from evlet.calc import F, days, parent, round, when
from amendment.models.masters import Company


class Quote(Model):
    class Meta:
        calculation_language = 'finite-v1'
        numeric_profile = 'finite40-hu18-v1'

    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}
    name = Text(required=True)
    stage = Select(('draft', 'sent', 'won', 'lost'), default='draft')
    inclusive = Boolean(default=False)
    company = Link(Company)
    issued_on = Date()
    # Suggested from the company's payment terms whenever the company changes; the user may edit it.
    payment_days = Integer(initial=F('company.payment_days'), minimum=0)
    # when() leaves the date empty until both inputs are set, because date arithmetic with an empty
    # value stays pending and would block the save.
    valid_until = Date(compute=when((F('payment_days') == None) | (F('issued_on') == None), None,  # noqa: E711
                                    F('issued_on') + days(F('payment_days'))))
    # Percent is a two-place decimal from 0 to 100. A won quote keeps the discount it was won with.
    discount_rate = Percent(default='0', locked_when=F('stage') == 'won')
    lost_reason = Text(applies_when=F('stage') == 'lost', required_when=F('stage') == 'lost')
    labels = Tags()

    class Line(Child):
        description = Text(default='')
        qty = Decimal(default='1', minimum='0')
        rate = Currency(default='0', minimum='0')
        vat_rate = Decimal(scale=2, default='5', minimum='0')
        taxable = Boolean(default=True)
        amount = Currency(compute=round(qty * rate, 2))
        net_raw = Decimal(scale=None, stored=False, compute=amount / (1 + parent.inclusive * taxable * vat_rate / 100))
        net = Currency(compute=round(net_raw, 2))
        vat = Currency(compute=amount - net + (1 - parent.inclusive) * round(net * taxable * vat_rate / 100, 2))

    # Up to 500 lines. Clients may send only what changed: {"create": [...], "update": [...], "delete": [...], "order": [...]}.
    lines = Table(Line, max_rows=500)
    net_total = Currency(compute=lines.sum('net'))
    vat_total = Currency(compute=lines.sum('vat'))
    taxable_net = Currency(compute=lines.where(taxable=True).sum('net'))
    grand_total = Currency(compute=net_total + vat_total)
    # Quantized to 2 places with Currency's default half-up rounding; no round() needed.
    discount = Currency(compute=net_total * discount_rate / 100)
    payable = Currency(compute=grand_total - discount)
