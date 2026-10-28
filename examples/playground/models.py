"""A small consumer of Evlet's public API, not an accounting system."""

from evlet import Currency, Link, Model, Registry, Text


class Customer(Model):
    name = Text(required=True, max_length=120)
    email = Text(max_length=254)


class Invoice(Model):
    customer = Link(Customer, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    total = Currency(required=True, minimum="0.01")


class Payment(Model):
    invoice = Link(Invoice, required=True)
    reference = Text(required=True, unique=True, max_length=40)
    amount = Currency(required=True, minimum="0.01")


registry = Registry(Customer, Invoice, Payment)
