"""The Playground's workspaces: named sidebars of model lists, filtered lists, reports, a slip and a link.

An app package declares them in its own workspaces.py; the Playground is built by hand, so app.py passes
them as Application(workspaces=WORKSPACES). Each role sees only the items it can open (docs/ui.md#workspaces).
"""

from evlet import NavGroup, Shortcut, Workspace

from .reports import InvoiceRegister, InvoicesByCustomer, LedgerBalances, SalesByCustomer
from .slips import QuickInvoice

SALES = Workspace('Sales', icon='receipt', groups=(
    NavGroup('Customers', icon='users', items=('customer',)),
    NavGroup('Invoicing', icon='file-text', items=(
        'invoice',
        # A filtered list: the same filters a list address carries, checked against Invoice's fields.
        Shortcut('invoice', filters={'payment_status__in': ['unpaid', 'partly_paid']}, label='Unpaid invoices'),
        'payment',
        'ledgerentry',
        # A slip without a source, presented from the sidebar.
        Shortcut(slip=QuickInvoice),
    )),
    NavGroup('Reports', icon='chart-column', items=(
        Shortcut(report=InvoiceRegister), Shortcut(report=InvoicesByCustomer), Shortcut(report=SalesByCustomer),
        Shortcut(report=LedgerBalances),
        # A link to an app page; http(s) addresses work too.
        Shortcut(url='/reports', label='All reports'),
    )),
))

PROJECTS = Workspace('Projects', icon='kanban', groups=(
    NavGroup('Projects', icon='kanban', items=(
        'project',
        Shortcut('project', filters={'status': 'active'}, label='Active projects'),
        Shortcut('project', filters=[['status', 'eq', 'planned'], ['budget', 'gt', '0']], label='Planned with a budget'),
    )),
))

COMPANY = Workspace('Company', icon='building-2', groups=(
    NavGroup('People', icon='users', items=('employee',)),
    NavGroup('Catalogue', icon='package', items=('item',)),
))

WORKSPACES = (SALES, PROJECTS, COMPANY)
