"""A finite-v1 document: one formula definition for browser previews and server saves."""
from evlet import (Model, Child, Table, Decimal, Currency, Boolean, Text, Rule, Link, Date, Integer, Percent, Tags, State, Move,
                   Sequence, Reference, Effect, action, Count, Sum, Journal)
from evlet.calc import F, days, parent, round, when
from amendment.models.masters import Company


class SalesLedger(Model):
    """Won business, one row per won quote. Rows are written only by the Quote's effect and are read only."""
    permissions = {'admin': Rule(('read',)), 'editor': Rule(('read',)), 'viewer': Rule(())}
    company = Link(Company)
    amount = Currency(required=True)
    memo = Text(default='')
    # Filled by the engine: the quote that posted the row, and whether the row reverses another one.
    source = Reference()
    reversal = Boolean(default=False)


class Quote(Model):
    class Meta:
        calculation_language = 'finite-v1'
        numeric_profile = 'finite40-hu18-v1'
        # A sent quote is corrected by revising it: it is voided and a new draft copy links back to it.
        revisable = True
        # Totals a quote list can show: GET /api/quote/measures, or /api/quote/aggregate to group them.
        measures = {
            'open_total': Sum('grand_total', where=F('stage') == 'sent'),
            'won_total': Sum('grand_total', where=F('stage') == 'won'),
            'quotes': Count(),
        }
        # Shown beside the quote; its revisions are listed as related records automatically.
        key_fields = ('company', 'stage', 'payable', 'valid_until')
        # Winning a quote posts one row to the sales ledger in the same transaction. A won quote is final,
        # so it is never voided and the effect needs no reverse.
        effects = (Effect(SalesLedger, rows='ledger_rows', on='won'),)
        # A quote has a State, so it keeps a journal anyway; tamper_evident also chains each entry's hash
        # (GET or POST /api/quote/{id}/journal; evlet journal verify <instance> checks the chains).
        journal = Journal(tamper_evident=True)

    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read', 'create', 'update')), 'viewer': Rule(('read',))}
    name = Text(required=True)
    # The stage changes only through its moves. Sent, won and lost quotes are locked against ordinary
    # edits; a won quote is final. Only an administrator can reopen a sent or lost quote, or void or
    # revise a sent one (POST /api/quote/{id}/void or /revise with a reason).
    stage = State(('draft', 'sent', 'won', 'lost', 'void'), moves=(
        Move('draft', 'sent', 'Send', when=F('grand_total') > 0),
        Move('sent', 'won', 'Mark won'),
        Move('sent', 'lost', 'Mark lost'),
        Move(('sent', 'lost'), 'draft', 'Reopen', roles=('admin',)),
    ), locked=('sent', 'won', 'lost', 'void'), final=('won', 'void'), voidable=('sent',))
    # The quote number is given when a quote is first sent, counted per year (Q-2026-0001). A reopened
    # and resent quote keeps its number; only the engine writes it.
    number = Sequence('Q-{year}-{n:04}', assign_on='sent')
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
    # Filled in after a quote is lost, so it stays editable while the quote is locked.
    lost_reason = Text(applies_when=F('stage') == 'lost', editable_when_locked=True)
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

    # An action may change a locked quote; the inputs are validated before it runs.
    @action('Extend validity', inputs={'extra_days': Integer(required=True, minimum=1, maximum=365)},
            available_when=F('stage') == 'sent')
    def extend_validity(self, extra_days):
        self.payment_days = (self.payment_days or 0) + extra_days

    # Effect rows are a pure function of the saved quote.
    def ledger_rows(self):
        return [{'company': self.company, 'amount': self.payable, 'memo': self.name}]
