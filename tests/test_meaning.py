"""Phase 10: key fields, relations and attachment limits, on real SQLite over authenticated HTTP."""

import json
from urllib.parse import quote

import pytest

from evlet import (Application, Attachments, Child, ChildTable, Currency, Database, Date, Integer, Link, LocalAuth, LongText,
                   Model, Registry, Related, Rule, Table, Text)
from test_business import PASSWORD, client, http
from test_rules_types import ALL, add_user


class Customer(Model):
    permissions = ALL
    name = Text(required=True)
    notes = LongText(default='')

    class Meta:
        key_fields = ('name',)


class Invoice(Model):
    permissions = {**ALL, 'viewer': Rule(('read',), read_fields=('number', 'customer'))}
    number = Text(required=True)
    customer = Link(Customer)
    total = Currency(default=0)
    secret = Text(default='')
    original = Link('invoice')
    internal = Link(Customer, hidden=True)

    class Line(Child):
        description = Text(default='')
        customer = Link(Customer)

    lines = Table(Line)

    class Meta:
        key_fields = ('customer', 'total', 'secret')
        relations = (Related('payment', 'invoice', label='Payments received', limit=2, order=('-amount', 'id')),)
        attachments = Attachments(max_files=2, max_size=4)


class Payment(Model):
    # Editors see only their own payments; viewers cannot read amounts or order by them.
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'), scope=lambda user: {'owner': user.id}),
                   'viewer': Rule(('read',), read_fields=('invoice', 'paid_on', 'owner'))}
    invoice = Link(Invoice, required=True)
    refund_of = Link(Invoice)
    amount = Currency(default=0)
    paid_on = Date()
    owner = Integer(default=0)


class Audit(Model):
    # Only administrators read audits.
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    invoice = Link(Invoice)


MODELS = (Customer, Invoice, Payment, Audit)


def start(tmp_path, *models, attachments=()):
    db = Database(tmp_path / 'meaning.sqlite3', Registry(*models))
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      attachments=attachments)
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    return app


def entry(c, name):
    return next(item for item in c('GET', '/api/schema')[1]['models'] if item['name'] == name)


def test_reverse_links_are_discovered_named_ordered_and_limited():
    meaning = Registry(*MODELS).meaning
    relations = meaning[Invoice].relations
    # The declared relation comes first, then the rest by name; a self Link is included, the hidden
    # Link and the Table row's Link are not.
    assert [(item.name, item.label, item.limit, item.order) for item in relations] == [
        ('payment.invoice', 'Payments received', 2, ('-amount', 'id')),
        ('audit.invoice', 'Audits', 10, ('-id',)),
        ('invoice.original', 'Invoices', 10, ('-id',)),
        ('payment.refund_of', 'Payments (Refund of)', 10, ('-id',)),
    ]
    assert [item.name for item in meaning[Customer].relations] == ['invoice.customer']
    assert meaning[Invoice].key_fields == ('customer', 'total', 'secret')
    assert meaning[Payment].relations == () and meaning[Payment].key_fields == ()


def test_related_can_hide_a_discovered_relation():
    class Hidden(Model):
        name = Text(default='')

        class Meta:
            relations = (Related('ticket', 'hidden', hidden=True),)

    class Ticket(Model):
        hidden = Link(Hidden)
        other = Link(Hidden)

    assert [item.name for item in Registry(Hidden, Ticket).meaning[Hidden].relations] == ['ticket.other']


def test_revisions_relation_is_labelled():
    from evlet import Move, State
    from evlet.calc import F  # noqa: F401

    class Offer(Model):
        class Meta:
            calculation_language = 'finite-v1'
            revisable = True
        name = Text(default='')
        status = State(('draft', 'sent', 'void'), moves=(Move('draft', 'sent', 'Send'),), locked=('sent', 'void'),
                       final=('void',), voidable=('sent',))

    assert [(item.name, item.label) for item in Registry(Offer).meaning[Offer].relations] == [('offer.revises', 'Revisions')]


@pytest.mark.parametrize('meta, message', [
    ({'key_fields': ('missing',)}, 'key_fields'),
    ({'key_fields': ('lines',)}, 'key_fields'),
    ({'key_fields': ('hidden_note',)}, 'key_fields'),
    ({'key_fields': ('name', 'name')}, 'key_fields'),
    ({'key_fields': 'name'}, 'key_fields'),
    ({'key_fields': tuple(f'f{i}' for i in range(9))}, 'key_fields'),
    ({'relations': Related('note', 'owner')}, 'tuple of Related'),
    ({'relations': (Related('note', 'missing'),)}, 'must be a Link'),
    ({'relations': (Related('note', 'text'),)}, 'must be a Link'),
    ({'relations': (Related('note', 'secret_owner'),)}, 'hidden Link'),
    ({'relations': (Related('note', 'owner'), Related('note', 'owner', label='Again'))}, 'declared twice'),
    ({'relations': (Related('note', 'owner', order='missing'),)}, 'stored fields'),
    ({'attachments': (1, 2)}, 'Attachments'),
    ({'unknown': 1}, 'Model.Meta supports'),
])
def test_meaning_metadata_is_validated_at_registration(meta, message):
    class Row(Child):
        text = Text(default='')

    fields = {f'f{i}': Text(default='') for i in range(9)}
    Owner = type('Owner', (Model,), {'name': Text(default=''), 'hidden_note': Text(default='', hidden=True),
                                     'lines': Table(Row), **fields, 'Meta': type('Meta', (), meta)})

    class Note(Model):
        owner = Link(Owner)
        secret_owner = Link(Owner, hidden=True)
        text = Text(default='')

    with pytest.raises(ValueError, match=message):
        Registry(Owner, Note)


@pytest.mark.parametrize('arguments', [
    {'limit': 0}, {'limit': 101}, {'limit': True}, {'order': ()}, {'order': ('a', 'b', 'c', 'd')}, {'order': 'Bad'},
    {'label': ''}, {'hidden': 1},
])
def test_related_arguments_are_checked(arguments):
    with pytest.raises(ValueError):
        Related('payment', 'invoice', **arguments)


@pytest.mark.parametrize('arguments', [{'max_files': 0}, {'max_files': 11}, {'max_size': 0}, {'max_size': 1024 * 1024 + 1},
                                       {'max_files': 2.0}, {'max_size': True}])
def test_attachment_limits_stay_within_the_framework_maximums(arguments):
    with pytest.raises(ValueError):
        Attachments(**arguments)


def test_table_rows_cannot_declare_meaning():
    class Row(Child):
        text = Text(default='')

        class Meta:
            key_fields = ('text',)

    class Owner(Model):
        lines = Table(Row)

    with pytest.raises(ValueError, match='root models'):
        Registry(Owner)


def test_schema_exposes_meaning_filtered_by_permissions(tmp_path):
    app = start(tmp_path, *MODELS)
    admin, viewer = client(app), add_user(app, 'viewer')
    invoice = entry(admin, 'invoice')
    assert invoice['key_fields'] == ['customer', 'total', 'secret']
    assert [item['name'] for item in invoice['relations']] == ['payment.invoice', 'audit.invoice', 'invoice.original', 'payment.refund_of']
    assert invoice['relations'][0] == {'name': 'payment.invoice', 'label': 'Payments received', 'model': 'payment',
                                       'link': 'invoice', 'limit': 2, 'order': ['-amount', 'id']}
    assert invoice['attachments'] is True and invoice['attachment_limits'] == {'max_files': 2, 'max_size': 4}
    assert entry(admin, 'customer')['key_fields'] == ['name']
    assert 'attachment_limits' not in entry(admin, 'customer') and 'key_fields' not in entry(admin, 'payment')
    # The viewer reads only number and customer of an invoice, cannot read audits, and cannot see
    # the parent or refund Links (an unreadable field hides its relation).
    invoice = entry(viewer, 'invoice')
    assert invoice['key_fields'] == ['customer']
    assert [item['name'] for item in invoice['relations']] == ['payment.invoice']


def test_related_records_respect_row_scope_order_and_limits(tmp_path):
    app = start(tmp_path, *MODELS)
    admin, editor, viewer = client(app), add_user(app, 'editor'), add_user(app, 'viewer')
    editor_id = next(user['id'] for user in admin('GET', '/api/auth/users')[1]['items'] if user['username'] == 'editor')
    customer = admin('POST', '/api/customer', {'name': 'Acme'})[1]['id']
    invoice = admin('POST', '/api/invoice', {'number': 'INV-1', 'customer': customer})[1]['id']
    other = admin('POST', '/api/invoice', {'number': 'INV-2', 'customer': customer})[1]['id']
    for amount, owner, target in (('5', editor_id, invoice), ('9', 0, invoice), ('7', editor_id, invoice), ('1', editor_id, other)):
        assert admin('POST', '/api/payment', {'invoice': target, 'amount': amount, 'owner': owner})[0] == 201
    base = f'/api/invoice/{invoice}/related/payment.invoice'
    status, page, _ = admin('GET', base)
    assert status == 200 and page['total'] == 3 and page['limit'] == 2
    assert [item['amount'] for item in page['items']] == ['9.00', '7.00']
    assert [item['amount'] for item in admin('GET', base + '?limit=5&offset=1')[1]['items']] == ['7.00', '5.00']
    # The editor's row scope applies: only their own payments are listed and counted.
    page = editor('GET', base)[1]
    assert page['total'] == 2 and [item['amount'] for item in page['items']] == ['7.00', '5.00']
    # The viewer cannot read amounts, so only the readable part of the order (id) applies.
    page = viewer('GET', base + '?limit=5')[1]
    assert page['total'] == 3 and all('amount' not in item for item in page['items'])
    assert [item['id'] for item in page['items']] == [item['id'] for item in sorted(page['items'], key=lambda item: item['id'])]
    # Hidden relations, unknown names and bad queries.
    assert viewer('GET', f'/api/invoice/{invoice}/related/audit.invoice')[0] == 404
    assert admin('GET', f'/api/invoice/{invoice}/related/audit.invoice')[0] == 200
    assert admin('GET', f'/api/invoice/{invoice}/related/nothing')[0] == 404
    assert admin('GET', f'/api/invoice/{invoice}/related')[0] == 404
    assert admin('GET', base + '?limit=0')[0] == 422
    assert admin('GET', base + '?sort=amount')[0] == 422
    assert admin('POST', base, {})[0] == 405
    assert admin('GET', f'/api/invoice/999/related/payment.invoice')[0] == 404
    # The existing list filter gives the same rows when the owning model has no row scope.
    listed = editor('GET', '/api/payment?filters=' + quote(json.dumps({'invoice': invoice})))[1]
    assert listed['total'] == 2


def test_related_records_need_a_readable_owning_record(tmp_path):
    class Account(Model):
        permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'), scope=lambda user: {'owner': user.id})}
        name = Text(default='')
        owner = Integer(default=0)

    class Entry(Model):
        permissions = ALL
        account = Link(Account)
        memo = Text(default='')

    app = start(tmp_path, Account, Entry)
    admin, editor = client(app), add_user(app, 'editor')
    hidden = admin('POST', '/api/account', {'name': 'Hidden', 'owner': 0})[1]['id']
    admin('POST', '/api/entry', {'account': hidden, 'memo': 'x'})
    # The account is outside the editor's scope, so its related entries are not listed.
    assert editor('GET', f'/api/account/{hidden}/related/entry.account')[0] == 404
    # Filtering entries by a scoped Link stays refused, as before.
    assert editor('GET', '/api/entry?filters=' + quote(json.dumps({'account': hidden})))[0] == 403
    mine = editor('POST', '/api/account', {'name': 'Mine', 'owner': 2})[1]['id']
    admin('POST', '/api/entry', {'account': mine, 'memo': 'y'})
    page = editor('GET', f'/api/account/{mine}/related/entry.account')[1]
    assert page['total'] == 1 and page['items'][0]['memo'] == 'y'


def test_attachment_limits_from_meta_are_enforced(tmp_path):
    app = start(tmp_path, *MODELS)
    admin = client(app)
    invoice = admin('POST', '/api/invoice', {'number': 'INV-1'})[1]['id']
    path = f'/api/invoice/{invoice}/attachments'
    status, body, _ = admin('POST', path, {'name': 'big.txt', 'content': 'YWJjZGU='})
    assert status == 422 and body['fields']['attachment'] == 'Use a nonempty file up to 4 bytes'
    assert admin('POST', path, {'name': 'a.txt', 'content': 'YWJjZA=='})[0] == 201
    assert admin('POST', path, {'name': 'b.txt', 'content': 'YQ=='})[0] == 201
    status, body, _ = admin('POST', path, {'name': 'c.txt', 'content': 'YQ=='})
    assert status == 422 and body['fields']['attachment'] == 'Use at most 2 attachments per record'
    # Customers have no attachments.
    customer = admin('POST', '/api/customer', {'name': 'Acme'})[1]['id']
    assert admin('GET', f'/api/customer/{customer}/attachments')[0] == 404


def test_application_attachments_keep_defaults_and_meta_wins(tmp_path):
    app = start(tmp_path, *MODELS, attachments=(Customer, Invoice))
    admin = client(app)
    assert entry(admin, 'customer')['attachment_limits'] == {'max_files': 10, 'max_size': 1024 * 1024}
    assert entry(admin, 'invoice')['attachment_limits'] == {'max_files': 2, 'max_size': 4}
    customer = admin('POST', '/api/customer', {'name': 'Acme'})[1]['id']
    path = f'/api/customer/{customer}/attachments'
    for index in range(10):
        assert admin('POST', path, {'name': f'{index}.txt', 'content': 'YQ=='})[0] == 201
    status, body, _ = admin('POST', path, {'name': 'x.txt', 'content': 'YQ=='})
    assert status == 422 and body['fields']['attachment'] == 'Use at most 10 attachments per record'


def test_child_table_models_are_not_relations_and_cannot_hold_attachments(tmp_path):
    class Folder(Model):
        name = Text(default='')

    class Page(Model):
        folder = Link(Folder, required=True)
        title = Text(default='')

    class Shelf(Model):
        folder = Link(Folder)

    db = Database(tmp_path / 'child.sqlite3', Registry(Folder, Page, Shelf))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(db, auth=auth, children=(ChildTable(Folder, 'pages', Page, 'folder', 'Pages'),))
    assert [item.name for item in app.business.relations[Folder]] == ['shelf.folder']

    class Book(Model):
        folder = Link(Folder, required=True)

        class Meta:
            attachments = Attachments()

    db = Database(tmp_path / 'child2.sqlite3', Registry(Folder, Book))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    with pytest.raises(ValueError, match='child table'):
        Application(db, auth=auth, children=(ChildTable(Folder, 'books', Book, 'folder', 'Books'),))

    class Binder(Model):
        name = Text(default='')

        class Meta:
            relations = (Related('sheet', 'binder'),)

    class Sheet(Model):
        binder = Link(Binder, required=True)

    db = Database(tmp_path / 'child3.sqlite3', Registry(Binder, Sheet))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    with pytest.raises(ValueError, match='child table'):
        Application(db, auth=auth, children=(ChildTable(Binder, 'sheets', Sheet, 'binder', 'Sheets'),))
