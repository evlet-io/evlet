"""A small consumer of Evlet's public API, not an accounting system."""

from evlet import (Boolean, Currency, Date, DateTime, Decimal, Hierarchy, Integer, Link, LongText, Model, Naming, Ref, Registry, RichText,
                   Rule, Section, Select, Series, Text)

# Currencies the Playground records may use. A Currency field only shows the code; it never converts.
CURRENCIES = ['AED', 'SAR', 'USD', 'EUR', 'INR']


class Customer(Model):
    class Meta:
        description = "Good work starts with good relationships."
        # The record title (Link pickers, lists, search) is the customer's name.
        title_field = 'customer_name'
        list_fields = ('customer_name', 'email', 'active')
        search_fields = ('customer_name', 'email')
        # flow='columns': down the left column (name, email, active), then the right (currency); Tab goes the
        # same way, and notes, full width, sits below both columns.
        layout = (Section('Details', ('customer_name', 'email', 'active', 'currency', 'notes'), collapsible=False, flow='columns'),)

    customer_name = Text(required=True, max_length=120, label='Customer name')
    email = Text(max_length=254)
    active = Boolean(required=True, default=True)
    currency = Select(CURRENCIES, required=True, default='AED', help_text='Invoices for this customer show amounts in it.')
    notes = LongText()


class Invoice(Model):
    class Meta:
        description = "Keep each piece of work connected to a customer."
        list_fields = ('name', 'reference', 'customer', 'total', 'payment_status', 'status', 'posting_date')
        search_fields = ('name', 'reference', 'status')
        # The server names each new invoice INV-2026-00001, INV-2026-00002, ... in its create transaction:
        # the standard name, shown as the document number. The year comes from the posting date, or the
        # creation day without one. Controls, Naming can add series and set the counter. With no title
        # source the title is the name.
        # label names the standard name field for invoices on the form, list columns, filters and search.
        naming = Naming(Series('INV-{YYYY}-{#####}', label='Invoices'), date_field='posting_date', label='Invoice number')
        # The list opens an invoice in a side panel beside it. People can switch back to the full page.
        open_in = 'panel'
        # The posting date places an invoice in a period. Once Controls, Periods locks that period, nobody
        # adds, changes or deletes invoices dated in it, app code included.
        period = 'posting_date'
        # The header shows the payment status as a badge; it is calculated, so nobody edits it.
        status = 'payment_status'

    customer = Link(Customer, required=True)
    # Only projects of the invoice's customer. Changing the customer clears a project of another one,
    # with a note under the field; the server names the mismatch if one is saved anyway.
    project = Link('project', filters={'customer': Ref('customer')}, help_text="One of this customer's projects.")
    # help_text reads under the input; tooltip sits behind a ? icon at its right end.
    reference = Text(required=True, unique=True, max_length=40, label='Customer reference',
                     help_text="The customer's reference for this invoice.",
                     tooltip="Use the customer's purchase order number if they ask for one.")
    # The currency comes from the customer the invoice is for.
    total = Currency(required=True, minimum="0.01", currency='customer.currency')
    posting_date = Date()
    status = Select(["draft", "sent"], required=True, default="draft")
    # suffix='%' puts a % box after the input, as Percent fields have.
    tax_rate = Decimal(scale=4, minimum="0", maximum="100", default="0.0000", label="Tax rate", suffix="%", help_text="Recorded separately; this does not calculate the total.")
    amount_paid = Currency(minimum="0", default="0.00", currency='customer.currency', label='Amount paid')
    # Calculated from the amount paid against the total, never set by hand. colors picks each badge colour.
    payment_status = Select(['unpaid', 'partly_paid', 'paid'], label='Payment status',
                            colors={'unpaid': 'rose', 'partly_paid': 'amber', 'paid': 'green'},
                            compute="'paid' if amount_paid >= total else ('partly_paid' if amount_paid > 0 else 'unpaid')")


class Payment(Model):
    class Meta:
        description = "Record payments and the invoices they belong to."
        # A payment is entered in one go, so leaving one with unsaved edits asks to discard them
        # instead of keeping them for later (docs/ui.md, Unsaved changes).
        autosave = False

    invoice = Link(Invoice, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    # The currency is a field on the payment itself.
    currency = Select(CURRENCIES, required=True, default='AED')
    amount = Currency(required=True, minimum="0.01", currency='currency')
    received_at = DateTime()


class LedgerEntry(Model):
    class Meta:
        description = "What posted invoices owe. Only Post to ledger on an invoice writes entries."
        label = 'Ledger entry'
        plural = 'Ledger entries'
        list_fields = ('posting_date', 'customer', 'invoice', 'amount')
        # Every role reads entries and none writes them, admin included: the Post to ledger action
        # (app.py) is trusted Python and the only way in. Closed periods apply to entries too.
        api = 'read'
        period = 'posting_date'

    invoice = Link(Invoice, required=True, index=True)
    customer = Link(Customer, required=True)
    posting_date = Date(required=True)
    amount = Currency(required=True, currency='customer.currency')
    memo = Text(max_length=120)


class Project(Model):
    class Meta:
        description = "Track a small project and its customer."
        list_fields = ('title', 'customer', 'status', 'budget')
        # A plain ('Title', fields) group always shows; a Section can be opened and closed. Planning
        # starts open, Schedule starts closed and Notes starts closed when it is empty.
        layout = (('Project', ('title', 'customer', 'status')),
                  Section('Planning', ('budget', 'progress')),
                  Section('Schedule', ('starts_on',), collapsed=True),
                  Section('Notes', ('brief', 'notes'), collapsed='empty'))
        # The record page's tabs: Details first, then the project's tasks (Related) and its history.
        tabs = ('details', 'related', 'activity')
        default_tab = 'details'
        # The full page's side column: related tasks first, then the record's times, then key fields
        # (shown away from Details).
        side_blocks = ('related', 'info', 'key_fields')
        open_in = 'panel'
        # The list's tabs: All, Planned, Active and Done, with counts. It opens on Active.
        list_tabs = 'status'
        list_tab = 'active'

    # A declared title is the record title itself.
    title = Text(required=True, max_length=120, label='Project')
    customer = Link(Customer)
    status = Select(['planned', 'active', 'done'], required=True, default='planned', read_only=True)
    # The currency is the Projects app setting "Project currency" (Settings, Projects).
    budget = Currency(minimum='0', default='0.00', currency='settings.projects.currency')
    progress = Integer(minimum=0, maximum=100, default=0, label='Progress', suffix='%')
    starts_on = Date()
    # Formatted text: headings, lists, links (evlet/richtext.py sanitises every write).
    brief = RichText(label='Brief', help_text='Goals, scope and links, with formatting.')
    notes = LongText()


class ProjectTask(Model):
    class Meta:
        label = 'Project task'
        plural = 'Project tasks'
        list_fields = ('title', 'done')

    project = Link(Project, required=True, index=True)
    title = Text(required=True, max_length=160)
    done = Boolean(required=True, default=False)


# Who reports to whom. A viewer linked to an employee (LocalAuth.link_user) reads that employee and
# everyone below them, at any depth; editors and admins (HR) read everyone. See seed_team in team.py.
TEAM = Hierarchy('employee', parent='reports_to')


class Employee(Model):
    class Meta:
        description = "People and who they report to."
        title_field = 'full_name'
        list_fields = ('full_name', 'email', 'reports_to')
        search_fields = ('full_name', 'email')

    # Moving someone changes every team above them, so each save keeps who changed reports_to and when.
    track_changes = True
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update', 'delete')),
                   'viewer': Rule(('read',), scope=TEAM.team())}
    full_name = Text(required=True, max_length=120, label='Full name')
    email = Text(max_length=254)
    reports_to = Link('employee', index=True, label='Reports to')


# Items have about 200 fields and two child tables: the list filters at scale. Imported here so the
# registry lists every Playground model; items.py links to customers by name, not by import.
from .items import Item  # noqa: E402

registry = Registry(Customer, Invoice, Payment, LedgerEntry, Project, ProjectTask, Item, Employee)
