"""Reports: filters, typed columns, totals over every row, sorting and a chart, shown at /reports.

A report reads through its context, so each person sees only the rows and fields they may read.
"""

from evlet import Chart, Column, Count, Date, Link, Report, Select, Sum

from .models import Customer, Invoice, LedgerEntry

def aed_customers(context):
    """Customers billed in AED. A total needs one declared unit, so these reports keep to one currency.
    Filters never follow a Link to another model, so the customers are read first."""
    ids, offset = [], 0
    while True:
        page = context.list(Customer, filters={'currency': 'AED'}, order_by=('id',), limit=100, offset=offset)
        ids += [row['id'] for row in page['rows']]
        offset += len(page['rows'])
        if not page['rows'] or offset >= page['total']: return ids


def invoice_filters(report):
    """The invoice filters of a run, or None when no customer is billed in AED."""
    customers = aed_customers(report.context)
    if not customers: return None
    filters = {'customer__in': customers}
    # The grouped report has no customer filter.
    if getattr(report, 'customer', None): filters['customer'] = report.customer
    if report.status: filters['status'] = report.status
    if report.from_date: filters['posting_date__gte'] = report.from_date
    if report.to_date: filters['posting_date__lte'] = report.to_date
    return filters


class InvoiceRegister(Report):
    """A list report: one row per invoice, sortable by date and amount, totalled over every row."""
    title = 'Invoice register'
    roles = ('admin', 'editor', 'viewer')
    group = 'Sales'
    model = Invoice
    sortable = ('posting_date', 'total')
    customer = Link(Customer, filters={'currency': 'AED'})
    status = Select(['draft', 'sent'])
    from_date = Date(label='Posted from')
    to_date = Date(label='Posted to')
    columns = (Column('id', type='link', link='invoice', label='Invoice'), Column('customer', type='link', link='customer'),
               Column('reference', label='Customer reference'), Column('posting_date', type='date', label='Posted'),
               Column('status'), Column('total', type='currency', total=True, unit='AED', source='invoice.total'))

    def run(self):
        filters = invoice_filters(self)
        if filters is None: return {'rows': [], 'total': 0, 'totals': {'total': 0}}
        page = self.context.list(Invoice, filters=filters, order_by=self.context.sort or ('-posting_date', '-id'),
                                 limit=self.context.limit, offset=self.context.offset)
        [everything] = self.context.aggregate(Invoice, measures={'total': Sum('total')}, filters=filters)
        rows = [{name: row.get(name) for name in ('id', 'customer', 'reference', 'posting_date', 'status', 'total')} for row in page['rows']]
        return {'rows': rows, 'total': page['total'], 'totals': {'total': everything['total']}}


class SalesByCustomer(Report):
    """A summary report: invoices grouped by customer, with a chart. It runs as soon as it opens."""
    title = 'Sales by customer'
    roles = ('admin', 'editor')
    group = 'Sales'
    model = Customer
    autorun = True
    sortable = ('invoices', 'total')
    chart = Chart('bar', x='customer', y=('total',), label='Invoiced, AED')
    status = Select(['draft', 'sent'], default='sent')
    from_date = Date(label='Posted from')
    to_date = Date(label='Posted to')
    columns = (Column('customer', type='link', link='customer'), Column('invoices', type='integer', total=True, unit='invoices'),
               Column('total', type='currency', total=True, unit='AED', source='invoice.total'))

    def run(self):
        filters = invoice_filters(self)
        if filters is None: return {'rows': [], 'total': 0, 'totals': {'invoices': 0, 'total': 0}}
        groups = self.context.aggregate(Invoice, measures={'invoices': Count(), 'total': Sum('total')}, group_by=('customer',), filters=filters)
        order = (self.context.sort or ('-total',))[0]
        groups.sort(key=lambda row: row[order.lstrip('-')] or 0, reverse=order.startswith('-'))
        offset, limit = self.context.offset, self.context.limit
        totals = {'invoices': sum(row['invoices'] for row in groups), 'total': sum((row['total'] or 0 for row in groups), 0)}
        return {'rows': groups[offset:offset + limit], 'total': len(groups), 'totals': totals}



class InvoicesByCustomer(InvoiceRegister):
    """The register grouped by customer: a heading and a subtotal for each customer, over all their invoices."""
    title = 'Invoices by customer'
    group_by = 'customer'
    columns = (Column('customer', type='link', link='customer'), Column('id', type='link', link='invoice', label='Invoice'),
               Column('posting_date', type='date', label='Posted'), Column('status'),
               Column('total', type='currency', total=True, unit='AED', source='invoice.total'))

    def run(self):
        filters = invoice_filters(self)
        if filters is None: return {'rows': [], 'total': 0, 'totals': {'total': 0}}
        # context.sort starts with the group column, so each customer's invoices stay together.
        page = self.context.list(Invoice, filters=filters, order_by=self.context.sort + ('-posting_date',),
                                 limit=self.context.limit, offset=self.context.offset)
        groups = self.context.aggregate(Invoice, measures={'n': Count(), 'total': Sum('total')}, group_by=('customer',), filters=filters)
        rows = [{name: row.get(name) for name in ('customer', 'id', 'posting_date', 'status', 'total')} for row in page['rows']]
        return {'rows': rows, 'total': page['total'], 'totals': {'total': sum((group['total'] or 0 for group in groups), 0)},
                'groups': [{'value': group['customer'], 'count': group['n'], 'totals': {'total': group['total']}} for group in groups]}


class LedgerBalances(Report):
    """What each customer owes from posted invoices, read from the ledger. Ledger entries have Meta.api = "read":
    only Post to ledger writes them, and a report reads them like any model. A report would read a model with
    Meta.api = None too, and show only the columns it returns, to the roles it names."""
    title = 'Ledger balances'
    roles = ('admin', 'editor')
    group = 'Sales'
    model = LedgerEntry
    autorun = True
    sortable = ('entries', 'amount')
    to_date = Date(label='Posted up to')
    columns = (Column('customer', type='link', link='customer'), Column('entries', type='integer', total=True, unit='entries'),
               Column('amount', type='currency', total=True, unit='AED', label='Owed'))

    def run(self):
        customers = aed_customers(self.context)
        if not customers: return {'rows': [], 'total': 0, 'totals': {'entries': 0, 'amount': 0}}
        filters = {'customer__in': customers, **({'posting_date__lte': self.to_date} if self.to_date else {})}
        groups = self.context.aggregate(LedgerEntry, measures={'entries': Count(), 'amount': Sum('amount')}, group_by=('customer',), filters=filters)
        order = (self.context.sort or ('-amount',))[0]
        groups.sort(key=lambda row: row[order.lstrip('-')] or 0, reverse=order.startswith('-'))
        offset, limit = self.context.offset, self.context.limit
        totals = {'entries': sum(row['entries'] for row in groups), 'amount': sum((row['amount'] or 0 for row in groups), 0)}
        return {'rows': groups[offset:offset + limit], 'total': len(groups), 'totals': totals}


REPORTS = (InvoiceRegister, InvoicesByCustomer, SalesByCustomer, LedgerBalances)
