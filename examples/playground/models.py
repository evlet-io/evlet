"""A small consumer of Evlet's public API, not an accounting system."""

from evlet import (Boolean, Currency, Date, DateTime, Decimal, Hierarchy, Integer, Link, LongText, Model, Naming, Registry,
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

    customer_name = Text(required=True, max_length=120, label='Customer name')
    email = Text(max_length=254)
    active = Boolean(required=True, default=True)
    currency = Select(CURRENCIES, required=True, default='AED', help_text='Invoices for this customer show amounts in it.')
    notes = LongText()


class Invoice(Model):
    class Meta:
        description = "Keep each piece of work connected to a customer."
        list_fields = ('name', 'reference', 'customer', 'total', 'status', 'posting_date')
        search_fields = ('name', 'reference', 'status')
        # The server names each new invoice INV-2026-00001, INV-2026-00002, ... in its create transaction:
        # the standard name, shown as the document number. The year comes from the posting date, or the
        # creation day without one. Controls, Naming can add series and set the counter. With no title
        # source the title is the name.
        naming = Naming(Series('INV-{YYYY}-{#####}', label='Invoices'), date_field='posting_date')
        # The list opens an invoice in a side panel beside it. People can switch back to the full page.
        open_in = 'panel'

    customer = Link(Customer, required=True)
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


class Payment(Model):
    class Meta:
        description = "Record payments and the invoices they belong to."

    invoice = Link(Invoice, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    # The currency is a field on the payment itself.
    currency = Select(CURRENCIES, required=True, default='AED')
    amount = Currency(required=True, minimum="0.01", currency='currency')
    received_at = DateTime()


class Project(Model):
    class Meta:
        description = "Track a small project and its customer."
        list_fields = ('title', 'customer', 'status', 'budget')
        # A plain ('Title', fields) group always shows; a Section can be opened and closed. Planning
        # starts open, Schedule starts closed and Notes starts closed when it is empty.
        layout = (('Project', ('title', 'customer', 'status')),
                  Section('Planning', ('budget', 'progress')),
                  Section('Schedule', ('starts_on',), collapsed=True),
                  Section('Notes', ('notes',), collapsed='empty'))
        open_in = 'panel'

    # A declared title is the record title itself.
    title = Text(required=True, max_length=120, label='Project')
    customer = Link(Customer)
    status = Select(['planned', 'active', 'done'], required=True, default='planned', read_only=True)
    # The currency is the Projects app setting "Project currency" (Settings, Projects).
    budget = Currency(minimum='0', default='0.00', currency='settings.projects.currency')
    progress = Integer(minimum=0, maximum=100, default=0, label='Progress', suffix='%')
    starts_on = Date()
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

registry = Registry(Customer, Invoice, Payment, Project, ProjectTask, Item, Employee)
