"""Explicitly upgrade the configured Playground database."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from evlet import (AddField, Boolean, CreateModel, Currency, Database, Date, DateTime, Decimal, Integer,
                   Link, LongText, Migration, Model, PostgreSQLDatabase, Registry, RenameField, RetireField, RunData, Select, Text)
from .items import core_fields, item_fields
from .models import Customer, Invoice, registry

migrations = [Migration('0001_model_engine',
    AddField('customer', 'active', Boolean(required=True, default=True)),
    AddField('customer', 'notes', LongText()),
    AddField('invoice', 'posting_date', Date()),
    AddField('invoice', 'status', Select(['draft', 'sent'], required=True, default='draft')),
    AddField('invoice', 'tax_rate', Decimal(scale=4, minimum='0', maximum='100', default='0.0000')),
    AddField('payment', 'received_at', DateTime()),
)]


class Project(Model):
    """Frozen creation schema for the first Project table."""
    name = Text(required=True, max_length=120)
    customer = Link(Customer)
    status = Select(['planned', 'active', 'done'], required=True, default='planned')
    budget = Currency(minimum='0', default='0.00')
    progress = Integer(minimum=0, maximum=100, default=0)
    starts_on = Date()
    notes = LongText()


migrations.append(Migration('0002_projects', CreateModel(Project)))


class ProjectTask(Model):
    project = Link(Project, required=True, index=True)
    title = Text(required=True, max_length=160)
    done = Boolean(required=True, default=False)


migrations.append(Migration('0003_project_tasks', CreateModel(ProjectTask)))

# Currency sources: the customer's currency for invoices, the payment's own currency.
migrations.append(Migration('0004_currencies',
    AddField('customer', 'currency', Select(['AED', 'SAR', 'USD', 'EUR', 'INR'], required=True, default='AED')),
    AddField('payment', 'currency', Select(['AED', 'SAR', 'USD', 'EUR', 'INR'], required=True, default='AED')),
))


# Items: about 200 fields and two child tables (examples/playground/items.py), frozen here.
FrozenItem = type('Item', (Model,), {'__module__': __name__, **core_fields(), **item_fields(), 'default_customer': Link(Customer)})


class Price(Model):
    storage_name = 'item_prices'
    evlet_owner = Link(FrozenItem, required=True, index=True)
    evlet_position = Integer(required=True, default=0)
    price_list = Select(['standard selling', 'wholesale', 'export', 'standard buying'], required=True, default='standard selling')
    customer = Link(Customer)
    currency = Select(['AED', 'SAR', 'USD', 'EUR', 'INR'], required=True, default='AED')
    rate = Currency(minimum='0', default='0.00')
    min_qty = Integer(minimum=0, default=0)
    valid_from = Date()


class Barcode(Model):
    storage_name = 'item_barcodes'
    evlet_owner = Link(FrozenItem, required=True, index=True)
    evlet_position = Integer(required=True, default=0)
    barcode = Text(required=True, max_length=40)
    barcode_type = Select(['EAN', 'UPC', 'Code 128'], default='EAN')


migrations.append(Migration('0005_items', CreateModel(FrozenItem), CreateModel(Price), CreateModel(Barcode)))


# Invoice numbers from the naming series (Invoice.Meta.naming). Invoices saved before this keep an empty number.
migrations.append(Migration('0006_invoice_number', AddField('invoice', 'number', Text(unique=True, max_length=40))))


class Employee(Model):
    """Frozen creation schema for the team demo."""
    name = Text(required=True, max_length=120)
    email = Text(max_length=254)
    reports_to = Link('employee', index=True)


# The frozen model is in no registry, so its Link to itself names the class directly.
Employee.__fields__['reports_to'].target = Employee
migrations.append(Migration('0007_employees', CreateModel(Employee)))


def invoice_numbers_become_names(session):
    """Records have a standard name now: invoice numbers move into it and their counters follow."""
    session.add_standard_columns(session.registry.models['invoice'])
    session.connection.execute('UPDATE invoice SET "name" = "number" WHERE "name" IS NULL AND "number" IS NOT NULL')
    found = session.connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'evlet_sequence'" if isinstance(session.connection, sqlite3.Connection)
                                       else "SELECT to_regclass('evlet_sequence')").fetchone()
    if found and found[0]:
        session.connection.execute("UPDATE evlet_sequence SET field = 'name' WHERE model = 'invoice' AND field = 'number'")


# Record identity: `name` is the standard record name, so the customer's, project's and employee's own
# fields move aside (the project's becomes its declared title) and invoice numbers become names.
migrations.append(Migration('0008_record_names',
    RenameField('customer', 'name', 'customer_name'),
    RenameField('project', 'name', 'title'),
    RenameField('employee', 'name', 'full_name'),
    RetireField('invoice', 'number'),
    RunData(invoice_numbers_become_names),
))



class LedgerEntry(Model):
    """Frozen creation schema for the ledger written by Post to ledger."""
    invoice = Link(Invoice, required=True, index=True)
    customer = Link(Customer, required=True)
    posting_date = Date(required=True)
    amount = Currency(required=True)
    memo = Text(max_length=120)


migrations.append(Migration('0009_ledger', CreateModel(LedgerEntry)))


def legacy_registry():
    """Frozen Milestone 1 definitions used only to validate a legacy baseline."""
    class Customer(Model):
        name = Text(required=True, max_length=120)
        email = Text(max_length=254)
    class Invoice(Model):
        customer = Link(Customer, required=True)
        reference = Text(required=True, unique=True, max_length=40)
        total = Currency(required=True, minimum='0.01')
    class Payment(Model):
        invoice = Link(Invoice, required=True)
        reference = Text(required=True, unique=True, max_length=40)
        amount = Currency(required=True, minimum='0.01')
    return Registry(Customer, Invoice, Payment)


def configured_database():
    dsn = os.environ.get('EVLET_POSTGRES_DSN')
    if dsn:
        return PostgreSQLDatabase(dsn, registry, migrations=migrations)
    path = os.environ.get('EVLET_DATABASE', str(Path(__file__).parent / '.data' / 'playground.sqlite3'))
    return Database(path, registry, migrations=migrations)


def upgrade(path):
    path = Path(path)
    if path.exists():
        backup = path.with_name(path.name + '.' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.backup')
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
            has_metadata = source.execute("SELECT 1 FROM sqlite_master WHERE name = 'evlet_schema'").fetchone()
            has_records = source.execute("SELECT 1 FROM sqlite_master WHERE name = 'customer'").fetchone()
        print(f'Backup: {backup}')
        if has_records and not has_metadata:
            Database(path, legacy_registry()).initialize()
    applied = Database(path, registry).migrate(migrations)
    print('Applied: ' + ', '.join(applied) if applied else 'Schema is current.')


def main():
    database = configured_database()
    try:
        if isinstance(database, PostgreSQLDatabase):
            applied = database.migrate(migrations)
            print('Applied: ' + ', '.join(applied) if applied else 'Schema is current.')
        else:
            upgrade(database.path)
    finally:
        database.close()


if __name__ == '__main__':
    main()
