"""The Playground's workspaces: named sidebars of model lists, filtered lists, reports, a prompt and links.

Each sidebar has three levels: NavSection titles (bold, in capitals, never collapsing), parents (a NavGroup with
its icon on a tile in its colour, or a Shortcut that is a link) and their children, which take the parent's colour.
Home sits under the switcher, and a child's badge shows the bounded count of its badge filter.

An app package declares them in its own workspaces.py; the Playground is built by hand, so app.py passes
them as Application(workspaces=WORKSPACES). Each role sees only the items it can open (docs/ui.md#workspaces).
"""

from evlet import NavGroup, NavSection, Shortcut, Workspace

from .reports import InvoiceRegister, InvoicesByCustomer, LedgerBalances, SalesByCustomer

SALES = Workspace('Sales', icon='receipt', home=True, groups=(
    NavSection('Sell', items=(
        # A parent that is a link: its own icon and colour, no children.
        Shortcut('customer', icon='users', color='violet'),
        NavGroup('Invoicing', icon='file-text', color='blue', items=(
            # badge: the count of unpaid invoices beside the list, read lazily and capped at "99+".
            Shortcut('invoice', icon='receipt', badge={'payment_status': 'unpaid'}),
            # A filtered list: the same filters a list address carries, checked against Invoice's fields.
            Shortcut('invoice', filters={'payment_status__in': ['unpaid', 'partly_paid']}, label='Unpaid invoices',
                     badge={'payment_status': 'partly_paid'}),
            Shortcut('payment', icon='wallet'),
            'ledgerentry',
            # An @api function that returns a prompt (Quick invoice), opened from the sidebar.
            Shortcut(api='playground.quick_invoice', label='Quick invoice', icon='zap'),
        )),
    )),
    NavSection('Analyse', items=(
        NavGroup('Reports', icon='chart-column', color='rose', items=(
            Shortcut(report=InvoiceRegister), Shortcut(report=InvoicesByCustomer), Shortcut(report=SalesByCustomer),
            Shortcut(report=LedgerBalances),
            # A link to an app page, opened in place; new_tab=True opens it in a new tab.
            Shortcut(url='/reports', label='All reports'),
        )),
    )),
    NavSection('Help', items=(
        # An http(s) address opens in a new tab, with the open-externally icon.
        Shortcut(url='https://docs.evlet.io', label='Evlet docs', icon='file-text', color='slate'),
    )),
))

PROJECTS = Workspace('Projects', icon='kanban', home=True, groups=(
    NavSection('Work', items=(
        NavGroup('Projects', icon='kanban', color='teal', items=(
            Shortcut('project', badge={'docstate': 'planned'}),
            Shortcut('project', filters={'docstate': 'active'}, label='Active projects'),
            Shortcut('project', filters=[['docstate', 'eq', 'planned'], ['budget', 'gt', '0']], label='Planned with a budget'),
        )),
    )),
))

COMPANY = Workspace('Company', icon='building-2', groups=(
    NavGroup('People', icon='users', color='green', items=('employee', 'expense')),
    NavGroup('Catalogue', icon='package', color='amber', items=('item',)),
))

WORKSPACES = (SALES, PROJECTS, COMPANY)
