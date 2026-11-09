"""The Playground Invoice is named by its naming series into the standard `name`: INV-<year>-00001
from the posting date. Databases from before record names keep their numbers as names."""

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from evlet import (Boolean, Child, Currency, Database, Date, DateTime, Decimal, Integer, Link, LongText, Model, Naming,
                   Registry, Select, Series, Table, Text)
from evlet import framework
from examples.playground.items import core_fields, item_fields
from examples.playground.models import CURRENCIES, Customer, Invoice, Project
from test_business import client, hashed, secured  # noqa: F401


def customer(api):
    status, body, _ = api('POST', '/api/customer', {'customer_name': 'Al Sahra Trading'})
    assert status == 201, body
    return body['id']


def invoice(api, owner, reference, **values):
    status, body, _ = api('POST', '/api/invoice', {'customer': owner, 'reference': reference, 'total': '100.00', **values})
    assert status == 201, body
    return body


def test_new_invoices_are_numbered_from_the_posting_date_year(secured):
    editor = client(secured, 'editor')
    owner = customer(editor)
    first = invoice(editor, owner, 'PO-1', posting_date='2026-03-01')
    second = invoice(editor, owner, 'PO-2', posting_date='2026-12-31')
    # A new year starts a new counter.
    january = invoice(editor, owner, 'PO-3', posting_date='2027-01-01')
    assert (first['name'], second['name'], january['name']) == ('INV-2026-00001', 'INV-2026-00002', 'INV-2027-00001')
    # Without a title source the invoice's title is its name, and the old number field is gone.
    assert first['title'] == 'INV-2026-00001' and 'number' not in first
    # Without a posting date the year is the creation day's, in the instance timezone.
    assert re.fullmatch(r'INV-\d{4}-\d{5}', invoice(editor, owner, 'PO-4')['name'])
    listed = editor('GET', '/api/invoice?limit=10&offset=0&q=INV-2026-00002')[1]['items']
    assert [item['reference'] for item in listed] == ['PO-2']


def test_the_name_cannot_be_sent_on_create_and_edits_keep_it(secured):
    editor = client(secured, 'editor')
    owner = customer(editor)
    status, body, _ = editor('POST', '/api/invoice', {'customer': owner, 'reference': 'PO-1', 'total': '10.00', 'name': 'MINE'})
    assert status == 422 and body['fields']['name'] == 'Assigned on save'
    created = invoice(editor, owner, 'PO-1', posting_date='2026-03-01')
    url = f"/api/invoice/{created['id']}"
    # The payment status is calculated, so an edit sends the record's ETag (If-Match).
    status, edited, _ = editor('PATCH', url, {'total': '250.00', 'posting_date': '2027-02-01'},
                               headers={b'if-match': editor('GET', url)[2][b'etag']})
    assert status == 200, edited
    # Editing the invoice, even into another year, keeps its name.
    assert (edited['total'], edited['name'], edited['title']) == ('250.00', 'INV-2026-00001', 'INV-2026-00001')
    assert editor('GET', url)[1]['name'] == 'INV-2026-00001'
    # Once assigned, a name changes through the normal edit (no rename feature); a taken name is refused.
    status, renamed, _ = editor('PATCH', url, {'name': 'INV-2026-09999'}, headers={b'if-match': editor('GET', url)[2][b'etag']})
    assert status == 200 and (renamed['id'], renamed['name'], renamed['title']) == (created['id'], 'INV-2026-09999', 'INV-2026-09999')
    other = invoice(editor, owner, 'PO-2', posting_date='2026-03-02')
    assert other['name'] == 'INV-2026-00002'
    other_url = f"/api/invoice/{other['id']}"
    assert editor('PATCH', other_url, {'name': 'INV-2026-09999'}, headers={b'if-match': editor('GET', other_url)[2][b'etag']})[0] in (409, 422)
    # A viewer changes nothing.
    assert client(secured, 'viewer')('PATCH', url, {'name': 'INV-2026-00007'})[0] == 403


def test_permissions_and_the_schema(secured):
    viewer = client(secured, 'viewer')
    owner = customer(client(secured, 'editor'))
    assert viewer('POST', '/api/invoice', {'customer': owner, 'reference': 'PO-1', 'total': '10.00'})[0] == 403
    for role in ('admin', 'editor', 'viewer'):
        model = next(item for item in client(secured, role)('GET', '/api/schema')[1]['models'] if item['name'] == 'invoice')
        fields = {item['name']: item for item in model['fields']}
        assert 'number' not in fields
        assert model['naming']['field'] == 'name' and model['naming']['assign_on'] == 'create' and model['title_field'] == 'title'
        assert {'name', 'title'} <= set(model['search_fields'])
        assert (fields['name']['required'], fields['name']['placeholder'], fields['name']['hidden']) == (False, 'Assigned on save', False)
        assert fields['title']['read_only'] and fields['title']['hidden']


def test_concurrent_creates_get_distinct_numbers(secured):
    editor = client(secured, 'editor')
    owner = customer(editor)
    with ThreadPoolExecutor(max_workers=8) as pool:
        names = list(pool.map(lambda index: invoice(editor, owner, f'PO-{index}', posting_date='2026-05-01')['name'], range(24)))
    assert sorted(names) == [f'INV-2026-{index:05}' for index in range(1, 25)]


def registry_0006():
    """The Playground models as migration 0006_invoice_number left them: invoices numbered into `number`."""
    class Customer(Model):
        name = Text(required=True, max_length=120)
        email = Text(max_length=254)
        active = Boolean(required=True, default=True)
        currency = Select(CURRENCIES, required=True, default='AED')
        notes = LongText()

    class Invoice(Model):
        class Meta:
            naming = Naming('number', Series('INV-{YYYY}-{#####}'), date_field='posting_date')
        customer = Link(Customer, required=True)
        reference = Text(required=True, unique=True, max_length=40)
        total = Currency(required=True, minimum='0.01')
        posting_date = Date()
        status = Select(['draft', 'sent'], required=True, default='draft')
        tax_rate = Decimal(scale=4, minimum='0', maximum='100', default='0.0000')
        number = Text(unique=True, max_length=40)

    class Payment(Model):
        invoice = Link(Invoice, required=True)
        reference = Text(required=True, unique=True, max_length=40)
        amount = Currency(required=True, minimum='0.01')
        received_at = DateTime()
        currency = Select(CURRENCIES, required=True, default='AED')

    class Project(Model):
        name = Text(required=True, max_length=120)
        customer = Link(Customer)
        status = Select(['planned', 'active', 'done'], required=True, default='planned')
        budget = Currency(minimum='0', default='0.00')
        progress = Integer(minimum=0, maximum=100, default=0)
        starts_on = Date()
        notes = LongText()

    class ProjectTask(Model):
        project = Link(Project, required=True, index=True)
        title = Text(required=True, max_length=160)
        done = Boolean(required=True, default=False)

    class Price(Child):
        price_list = Select(['standard selling', 'wholesale', 'export', 'standard buying'], required=True, default='standard selling')
        customer = Link(Customer)
        currency = Select(CURRENCIES, required=True, default='AED')
        rate = Currency(minimum='0', default='0.00', currency='currency')
        min_qty = Integer(minimum=0, default=0)
        valid_from = Date()

    class Barcode(Child):
        barcode = Text(required=True, max_length=40)
        barcode_type = Select(['EAN', 'UPC', 'Code 128'], default='EAN')

    Item = type('Item', (Model,), {'__module__': __name__, **core_fields(), **item_fields(),
                                   'prices': Table(Price), 'barcodes': Table(Barcode)})
    return Registry(Customer, Invoice, Payment, Project, ProjectTask, Item)


def columns(path, table):
    with sqlite3.connect(path) as connection:
        return [row[1] for row in connection.execute(f'PRAGMA table_info({table})')]


def test_an_upgraded_playground_keeps_invoice_numbers_as_names_and_continues_the_series(tmp_path):
    from examples.playground.migrate import legacy_registry, migrations
    from examples.playground.models import registry
    path = tmp_path / 'playground.sqlite3'
    # A Milestone 1 database with an invoice from before numbering...
    old = Database(path, legacy_registry())
    old.initialize()
    owner = old.create(old.registry.models['customer'], name='Preserved')
    unnumbered = old.create(old.registry.models['invoice'], customer=owner.id, reference='OLD-1', total='10.00')
    old.close()
    # ...upgraded to 0006, where the naming series numbered invoices into `number`.
    numbered = registry_0006()
    v6 = Database(path, numbered)
    assert v6.migrate(migrations[:6])[-1] == '0006_invoice_number'
    first = v6.create(numbered.models['invoice'], customer=owner.id, reference='N-1', total='5.00', posting_date='2026-03-01')
    second = v6.create(numbered.models['invoice'], customer=owner.id, reference='N-2', total='6.00', posting_date='2026-04-01')
    assert (first.number, second.number) == ('INV-2026-00001', 'INV-2026-00002')
    v6.close()
    # Evlet before record names made no standard columns: drop the ones today's initialize() added, so the
    # file is what an older Playground left behind. Its project was saved with the old `name` field.
    with sqlite3.connect(path) as connection:
        for table in ('customer', 'invoice', 'payment'):
            present = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            if 'name' in present and table != 'customer':
                connection.execute(f'DROP INDEX IF EXISTS evlet_{table}_name_unique')
                connection.execute(f'ALTER TABLE {table} DROP COLUMN name')
            if 'title' in present:
                connection.execute(f'ALTER TABLE {table} DROP COLUMN title')
        connection.execute("INSERT INTO project (name, customer, status, budget, progress) VALUES ('Fit-out', ?, 'planned', '0.00', 0)",
                           (owner.id,))
    assert 'title' not in columns(path, 'invoice') and 'number' in columns(path, 'invoice')

    # evlet migrate: app migrations first, then framework step 0003.
    current = Database(path, registry)
    assert current.migrate(migrations) == ['0007_employees', '0008_record_names', '0009_ledger', '0010_invoice_project', '0011_project_brief', '0012_invoice_payment_status', '0013_points',
                                              '0014_project_task_index', '0015_recognition',
                                              '0016_employee_profile', '0017_employee_skill_levels']
    assert '0003_record_names' in framework.upgrade(current)
    assert not framework.pending(current)
    assert {'name', 'title', 'customer_name'} <= set(columns(path, 'customer')) and 'number' not in Invoice.__fields__

    # Numbers became names, the unnumbered invoice stays unnamed, and every title is filled.
    assert [current.get(Invoice, record.id).name for record in (unnumbered, first, second)] == [None, 'INV-2026-00001', 'INV-2026-00002']
    assert current.get(Invoice, first.id).title == 'INV-2026-00001'
    assert current.get(Invoice, unnumbered.id).title == str(unnumbered.id)
    assert (current.get(Customer, owner.id).customer_name, current.get(Customer, owner.id).title) == ('Preserved', 'Preserved')
    project = current.list(Project)[0]
    assert (project.title, project.name) == ('Fit-out', None)

    # The series continues in the same counter: no name is issued twice.
    created = current.create(Invoice, customer=owner.id, reference='NEW-1', total='20.00', posting_date='2026-06-01')
    assert (created.name, created.title) == ('INV-2026-00003', 'INV-2026-00003')
    assert current.create(Invoice, customer=owner.id, reference='NEW-2', total='20.00', posting_date='2027-01-05').name == 'INV-2027-00001'
    current.close()


def test_a_database_initialized_from_milestone_models_upgrades(tmp_path):
    """The path `examples/playground/migrate.py upgrade` takes: a Milestone 1 file initialized with the frozen
    models, never numbered (so no counter table), migrated straight to the current models."""
    from examples.playground.migrate import legacy_registry, migrations
    from examples.playground.models import registry
    path = tmp_path / 'playground.sqlite3'
    old = Database(path, legacy_registry())
    old.initialize()
    owner = old.create(old.registry.models['customer'], name='Preserved')
    old.create(old.registry.models['invoice'], customer=owner.id, reference='OLD-1', total='10.00')
    old.close()
    current = Database(path, registry)
    assert '0008_record_names' in current.migrate(migrations)
    framework.upgrade(current)
    assert current.get(Customer, owner.id).title == 'Preserved'
    created = current.create(Invoice, customer=owner.id, reference='NEW-1', total='20.00', posting_date='2026-06-01')
    assert created.name == 'INV-2026-00001'
