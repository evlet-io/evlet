"""Print designs: the invoice as a PDF, and a report of invoices with its filters and totals.

Print needs the optional extra: pip install evlet[print]. Without it the designs still load and
the print routes answer 503 with the install command.
"""

from evlet import Column, PrintDesign, Report, Select
from evlet.printing import Fields, Footnote, Heading, PageNumber, Table, Text, Totals, Watermark

from .models import Invoice


class InvoicesByStatus(Report):
    title = 'Invoices by status'
    roles = ('admin', 'editor')
    status = Select(['draft', 'sent'], default='sent')
    columns = (Column('name', label='Invoice'), Column('reference'), Column('posting_date', type='date', label='Date'),
               Column('total', type='decimal', total=True, unit='mixed currencies'))

    def run(self):
        return self.context.list(Invoice, filters={'status': self.status}, order_by=('posting_date', 'id'),
                                 limit=self.context.limit, offset=self.context.offset)


class InvoicePrint(PrintDesign):
    model = Invoice
    title = 'Invoice'
    default = True
    header = (Text('Evlet Playground · {{ doc.name }}', weight='bold'),)
    main = (
        Heading('Invoice'),
        Fields(('name', 'Invoice no.'), 'posting_date', ('customer', 'Customer'), ('reference', 'Your reference'), 'status', 'tax_rate'),
        Totals(('total', 'Total')),
        # One block written as HTML: values stay escaped and only readable fields are there.
        Text(html='<p>Amount due: <b>{{ money(doc.total, doc.customer.currency) }}</b>. Please quote {{ doc.name }} with your payment.</p>'),
    )
    footnote = Footnote(Text('Received by: ______________________    Date: ____________'))
    footer = (PageNumber('Page {page} of {pages}', align='right'),)
    watermark = Watermark(text='DRAFT', size=55, opacity=0.08, show_if="doc.status == 'draft'")


class InvoicesByStatusPrint(PrintDesign):
    report = InvoicesByStatus
    title = 'Invoices by status'
    roles = ('admin', 'editor')
    size = 'A4'
    orientation = 'landscape'
    margins = ('22mm', '14mm', '18mm', '14mm')
    header = (Text('Evlet Playground', weight='bold'),)
    main = (Heading('{{ report.title }}'), Fields('status'), Table('rows'))
    footer = (PageNumber('{page}/{pages}', align='right'),)


REPORTS = (InvoicesByStatus,)
PRINTS = (InvoicePrint, InvoicesByStatusPrint)
