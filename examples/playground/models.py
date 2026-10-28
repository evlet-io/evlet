"""A small consumer of Evlet's public API, not an accounting system."""

from evlet import Boolean, Currency, Date, DateTime, Decimal, Integer, Link, LongText, Model, Registry, Select, Text


class Customer(Model):
    class Meta:
        description = "Good work starts with good relationships."
        list_fields = ('name', 'email', 'active')
        search_fields = ('name', 'email')

    name = Text(required=True, max_length=120)
    email = Text(max_length=254)
    active = Boolean(required=True, default=True)
    notes = LongText()


class Invoice(Model):
    class Meta:
        description = "Keep each piece of work connected to a customer."
        list_fields = ('reference', 'customer', 'total', 'status', 'posting_date')

    customer = Link(Customer, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    total = Currency(required=True, minimum="0.01")
    posting_date = Date()
    status = Select(["draft", "sent"], required=True, default="draft")
    tax_rate = Decimal(scale=4, minimum="0", maximum="100", default="0.0000", label="Tax rate (%)", help_text="Recorded separately; this does not calculate the total.")


class Payment(Model):
    class Meta:
        description = "Record payments and the invoices they belong to."

    invoice = Link(Invoice, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    amount = Currency(required=True, minimum="0.01")
    received_at = DateTime()


class Project(Model):
    class Meta:
        description = "Track a small project and its customer."
        list_fields = ('name', 'customer', 'status', 'budget')

    name = Text(required=True, max_length=120)
    customer = Link(Customer)
    status = Select(['planned', 'active', 'done'], required=True, default='planned')
    budget = Currency(minimum='0', default='0.00')
    progress = Integer(minimum=0, maximum=100, default=0, label='Progress (%)')
    starts_on = Date()
    notes = LongText()


registry = Registry(Customer, Invoice, Payment, Project)
