"""Models, migrations and data for tests/fixtures/sqlite-0.4.3: a database written by Evlet 0.4.3.

tests/test_database_adapter.py opens it with the current release and checks that nothing in it changes:
the schema, the stored rows and the migration checksums. Keep this file as it is; it describes what 0.4.3
wrote. To write the dumps again (only with Evlet 0.4.3 on the path), run make_dumps(folder).
"""

from datetime import date

from evlet import (AddIndex, AlterField, Application, CreateModel, Currency, Date, Decimal, Journal, Link, LocalAuth, Migration,
                   Model, Registry, RetireField, Select, Text)


class Customer(Model):
    full_name = Text(required=True, unique=True)
    phone = Text(required=True)

    class Meta:
        journal = Journal(tamper_evident=True)


class Invoice(Model):
    customer = Link(Customer, required=True)
    amount = Currency(required=True)
    rate = Decimal(scale=4, default=0)
    issued = Date(required=True, index=True)
    status = Select(('draft', 'sent'), default='draft')


def before():
    """The models as 0001_initial created them: frozen copies (a Registry binds a Link to one class)."""
    class Customer(Model):
        storage_name = 'customer'
        full_name = Text(required=True, unique=True)
        phone = Text()
        fax = Text()

    class Invoice(Model):
        storage_name = 'invoice'
        customer = Link('customer', required=True)
        amount = Currency(required=True)
        rate = Decimal(scale=4, default=0)
        issued = Date(required=True)
        status = Select(('draft', 'sent'), default='draft')

    class Move(Model):
        storage_name = 'move'
        invoice = Link('invoice', required=True)
        amount = Currency(required=True)

        class Meta:
            ledger = True
            append_only = True
            indexes = [('invoice',)]

    return Customer, Invoice, Move


CustomerBefore, InvoiceBefore, MoveBefore = before()


class Move(Model):
    """An append-only ledger."""
    invoice = Link(Invoice, required=True)
    amount = Currency(required=True)

    class Meta:
        ledger = True
        append_only = True
        indexes = [('invoice',)]


MIGRATIONS = (
    Migration('0001_initial', CreateModel(CustomerBefore), CreateModel(InvoiceBefore), CreateModel(MoveBefore)),
    Migration('0002_changes', RetireField('customer', 'fax'), AlterField('customer', 'phone', Text(required=True), fill='none'),
              AddIndex('invoice', 'issued')),
)

FILES = ('database', 'sessions', 'logs', 'eva')


def registry():
    return Registry(Customer, Invoice, Move)


def application(database):
    return Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))


def make_dumps(folder):
    """Write the fixture with the Evlet on the path: four SQL dumps, one per file of the instance."""
    import sqlite3
    import tempfile
    from pathlib import Path
    from evlet import Database
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'database.sqlite3'
        first = Database(path, Registry(CustomerBefore, InvoiceBefore, MoveBefore))
        first.migrate(MIGRATIONS[:1])
        with first.transaction() as tx:
            ada = tx.create(CustomerBefore, full_name='Ada Trading LLC', phone='+971 4 000 0000', fax='+971 4 000 0001')
            tx.create(CustomerBefore, full_name='Blue Dhow Foods', fax='+971 6 000 0002')
            invoice = tx.create(InvoiceBefore, customer=ada.id, amount='1250.75', rate='0.0500', issued=date(2026, 10, 1), status='sent')
            tx.insert_many(MoveBefore, [{'invoice': invoice.id, 'amount': '1250.75'}, {'invoice': invoice.id, 'amount': '-250.00'}])
        first.close()
        database = Database(path, registry(), migrations=MIGRATIONS)
        database.migrate(MIGRATIONS)
        app = application(database)
        app.initialize()
        with database.transaction() as tx:
            tx.create(Customer, full_name='Gulf Spices', phone='+971 2 000 0003')
        database.close()
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            with sqlite3.connect(path.with_name(name + '.sqlite3')) as connection:
                (target / (name + '.sql')).write_text('\n'.join(connection.iterdump()) + '\n')
