"""Reports: filters, typed columns, totals over every row, sorting and a chart, shown at /reports.

A report reads through its context, so each person sees only the rows and fields they may read.
"""

from evlet import Chart, Column, Count, Date, Link, Ref, Report, Select, Sum, ValidationError

from .models import Customer, Invoice, LedgerEntry, PointMove

# A filter's `in` takes at most 100 values (evlet/query.py), so a report reads at most that many customers.
MOST_CUSTOMERS = 100


def aed_customers(context):
    """Customers billed in AED. A total needs one declared unit, so these reports keep to one currency.
    Filters never follow a Link to another model, so the customers are read first: keyset pages (id after
    the last one read) cost one index range each, where OFFSET pages would rescan every earlier row."""
    ids = []
    while len(ids) <= MOST_CUSTOMERS:
        after = {'id__gt': ids[-1]} if ids else {}
        page = context.list(Customer, filters={'currency': 'AED', **after}, order_by=('id',), limit=100)
        ids += [row['id'] for row in page['rows']]
        if len(page['rows']) < 100 and len(ids) <= MOST_CUSTOMERS: return ids
    raise ValidationError({'customer': f'More than {MOST_CUSTOMERS} customers bill in AED: pick one customer'})


def invoice_filters(report):
    """The invoice filters of a run, or None when no customer is billed in AED."""
    # A chosen customer needs one indexed read instead of every AED customer; the grouped report has no customer filter.
    if getattr(report, 'customer', None):
        chosen = report.context.list(Customer, filters={'id': report.customer, 'currency': 'AED'}, limit=1)
        if not chosen['rows']: return None
        filters = {'customer': report.customer}
    else:
        customers = aed_customers(report.context)
        if not customers: return None
        filters = {'customer__in': customers}
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
    # Each customer opens the Invoice register with that customer and this run's status and dates (a drill down).
    columns = (Column('customer', type='link', link='customer', report='InvoiceRegister',
                      filters={'customer': 'customer', 'status': 'status', 'from_date': 'from_date', 'to_date': 'to_date'}),
               Column('invoices', type='integer', total=True, unit='invoices'),
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


class CustomerStatement(Report):
    """One customer's invoices in any currency: the total's unit is the customer's currency, read from the filter
    (Column(unit=Ref('customer.currency'))), so a KWD customer's total shows in KWD with three places."""
    title = 'Customer statement'
    roles = ('admin', 'editor', 'viewer')
    group = 'Sales'
    model = Invoice
    customer = Link(Customer, required=True)
    columns = (Column('id', type='link', link='invoice', label='Invoice'), Column('posting_date', type='date', label='Posted'),
               Column('status'), Column('total', type='currency', total=True, unit=Ref('customer.currency'), source='invoice.total'))

    def run(self):
        filters = {'customer': self.customer}
        page = self.context.list(Invoice, filters=filters, order_by=('-posting_date', '-id'), limit=self.context.limit, offset=self.context.offset)
        [everything] = self.context.aggregate(Invoice, measures={'total': Sum('total')}, filters=filters)
        rows = [{name: row.get(name) for name in ('id', 'posting_date', 'status', 'total')} for row in page['rows']]
        return {'rows': rows, 'total': page['total'], 'totals': {'total': everything['total']}}


class PointsByCustomer(Report):
    """Customer points from the points ledger, which has no routes or screens. Report.trusted = True gives run() a
    read-only transaction session that reads the ledger whatever the runner's own rules; the report's roles still
    decide who runs it."""
    title = 'Points by customer'
    roles = ('admin', 'editor')
    group = 'Sales'
    model = PointMove
    autorun = True
    trusted = True
    columns = (Column('customer', type='link', link='customer'), Column('points', type='integer', total=True, unit='points'))

    def run(self):
        groups = self.context.session.aggregate(PointMove, measures={'points': Sum('points')}, group_by=('customer',))
        groups.sort(key=lambda row: -(row['points'] or 0))
        offset, limit = self.context.offset, self.context.limit
        return {'rows': groups[offset:offset + limit], 'total': len(groups), 'totals': {'points': sum(row['points'] or 0 for row in groups)}}


REPORTS = (InvoiceRegister, InvoicesByCustomer, SalesByCustomer, LedgerBalances, CustomerStatement, PointsByCustomer)
