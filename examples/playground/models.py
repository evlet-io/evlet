"""A small consumer of Evlet's public API, not an accounting system."""

from evlet import Boolean, Currency, Date, DateTime, Decimal, Link, LongText, Model, Registry, Select, Text


class Customer(Model):
    name = Text(required=True, max_length=120)
    email = Text(max_length=254)
    active = Boolean(required=True, default=True)
    notes = LongText()


class Invoice(Model):
    customer = Link(Customer, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    total = Currency(required=True, minimum="0.01")
    posting_date = Date()
    status = Select(["draft", "sent"], required=True, default="draft")
    tax_rate = Decimal(scale=4, minimum="0", maximum="100", default="0.0000")


class Payment(Model):
    invoice = Link(Invoice, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    amount = Currency(required=True, minimum="0.01")
    received_at = DateTime()


registry = Registry(Customer, Invoice, Payment)
