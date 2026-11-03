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
    return serve(Database(tmp_path / 'meaning.sqlite3', Registry(*models)), attachments=attachments)


def serve(db, attachments=()):
    """An initialized application with an admin account (SQLite or PostgreSQL)."""
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
    ({'open_in': 'drawer'}, 'open_in must be "page" or "panel"'),
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


def test_open_in_says_how_a_list_opens_records():
    from evlet.ui.metadata import view_for

    class Ticket(Model):
        title = Text(default='')

        class Meta:
            open_in = 'panel'

    class Plain(Model):
        title = Text(default='')

    assert view_for(Ticket)['open_in'] == 'panel'
    assert view_for(Plain)['open_in'] == 'page'


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
    check_related(start(tmp_path, *MODELS))


def check_related(app):
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
    check_attachment_limits(start(tmp_path, *MODELS))


def check_attachment_limits(app):
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


def test_related_order_skips_links_to_row_scoped_models_in_route_and_schema(tmp_path):
    class Owner(Model):
        permissions = ALL
        name = Text(default='')

        class Meta:
            relations = (Related('item', 'owner', order=('-secret', '-amount')),)

    class Secret(Model):
        # Editors see no secrets, so ordering by the Link would reveal which items point to one.
        permissions = {**ALL, 'editor': Rule(('read',), scope=lambda user: {'owner': -1})}
        owner = Integer(default=0)

    class Item(Model):
        permissions = {**ALL, 'viewer': Rule(('read',), read_fields=('owner', 'secret'))}
        owner = Link(Owner)
        secret = Link(Secret)
        amount = Currency(default=0)

    app = start(tmp_path, Owner, Secret, Item)
    admin, editor, viewer = client(app), add_user(app, 'editor'), add_user(app, 'viewer')
    owner = admin('POST', '/api/owner', {'name': 'a'})[1]['id']
    secret = admin('POST', '/api/secret', {})[1]['id']
    ids = [admin('POST', '/api/item', {'owner': owner, 'secret': value, 'amount': amount})[1]['id']
           for value, amount in ((None, '1'), (secret, '2'), (None, '3'))]
    assert editor('GET', '/api/item?order_by=-secret')[0] == 403
    url = f'/api/owner/{owner}/related/item.owner'
    # The editor may not order by the scoped Link, so only the amount applies.
    assert [item['id'] for item in editor('GET', url)[1]['items']] == ids[::-1]
    # The viewer cannot read amounts and may order by the Link (Secret has no scope for viewers).
    assert [item['id'] for item in viewer('GET', url)[1]['items']][0] == ids[1]
    orders = {name: entry(c, 'owner')['relations'][0]['order'] for name, c in (('admin', admin), ('editor', editor), ('viewer', viewer))}
    assert orders == {'admin': ['-secret', '-amount'], 'editor': ['-amount'], 'viewer': ['-secret']}


def test_related_schema_order_falls_back_to_id(tmp_path):
    class Folder(Model):
        permissions = ALL
        name = Text(default='')

        class Meta:
            relations = (Related('sheet', 'folder', order=('-amount',)),)

    class Sheet(Model):
        permissions = {**ALL, 'viewer': Rule(('read',), read_fields=('folder',))}
        folder = Link(Folder)
        amount = Currency(default=0)

    app = start(tmp_path, Folder, Sheet)
    viewer = add_user(app, 'viewer')
    assert entry(viewer, 'folder')['relations'][0]['order'] == ['-id']
    assert entry(client(app), 'folder')['relations'][0]['order'] == ['-amount']


@pytest.mark.parametrize('names', [(['name'],), ({'a': 1},), (None,), 'name', ['name', ['x']]])
def test_key_fields_of_other_types_raise_value_error(names):
    Owner = type('Owner', (Model,), {'name': Text(default=''), 'Meta': type('Meta', (), {'key_fields': names})})
    with pytest.raises(ValueError, match='key_fields'):
        Registry(Owner)


def test_size_message_uses_singular_for_one_byte(tmp_path):
    class Note(Model):
        name = Text(default='')

        class Meta:
            attachments = Attachments(max_files=1, max_size=1)

    app = start(tmp_path, Note)
    admin = client(app)
    note = admin('POST', '/api/note', {'name': 'x'})[1]['id']
    status, body, _ = admin('POST', f'/api/note/{note}/attachments', {'name': 'a.txt', 'content': 'YWI='})
    assert status == 422 and body['fields']['attachment'] == 'Use a nonempty file up to 1 byte'
    assert admin('POST', f'/api/note/{note}/attachments', {'name': 'a.txt', 'content': 'YQ=='})[0] == 201
    status, body, _ = admin('POST', f'/api/note/{note}/attachments', {'name': 'b.txt', 'content': 'YQ=='})
    assert body['fields']['attachment'] == 'Use at most 1 attachment per record'


@pytest.mark.parametrize('meta', [{'key_fields': ('title',)}, {'relations': (Related('leaf', 'page'),)}])
def test_child_table_models_cannot_declare_key_fields_or_relations(tmp_path, meta):
    class Folder(Model):
        name = Text(default='')

    Page = type('Page', (Model,), {'folder': Link(Folder, required=True), 'title': Text(default=''),
                                   'Meta': type('Meta', (), meta)})

    class Leaf(Model):
        page = Link(Page)

    db = Database(tmp_path / 'child.sqlite3', Registry(Folder, Page, Leaf))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    with pytest.raises(ValueError, match='child table'):
        Application(db, auth=auth, children=(ChildTable(Folder, 'pages', Page, 'folder', 'Pages'),))


def test_application_attachments_on_a_child_table_model_keep_working(tmp_path):
    class Folder(Model):
        name = Text(default='')

    class Page(Model):
        folder = Link(Folder, required=True)

    db = Database(tmp_path / 'child.sqlite3', Registry(Folder, Page))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    # Unchanged from earlier releases: accepted, though child rows have no route of their own.
    app = Application(db, auth=auth, attachments=(Page,), children=(ChildTable(Folder, 'pages', Page, 'folder', 'Pages'),))
    assert Page in app.business.attachments


def test_subclasses_apply_inherited_relations_that_point_to_them():
    class Base(Model):
        name = Text(default='')

        class Meta:
            key_fields = ('name',)
            relations = (Related('entry', 'base', label='Entries'),)

    class Special(Base):
        pass

    class Entry(Model):
        base = Link(Base)
        special = Link(Special)

    meaning = Registry(Base, Special, Entry).meaning
    assert [(item.name, item.label) for item in meaning[Base].relations] == [('entry.base', 'Entries')]
    # The inherited Related names Entry.base, which points to Base, so Special keeps the defaults.
    assert [(item.name, item.label) for item in meaning[Special].relations] == [('entry.special', 'Entries')]
    assert meaning[Special].key_fields == ('name',)


def test_a_subclass_redeclaring_meta_is_checked_strictly():
    class Base(Model):
        name = Text(default='')

    class Special(Base):
        class Meta:
            relations = (Related('entry', 'base'),)

    class Entry(Model):
        base = Link(Base)

    with pytest.raises(ValueError, match='must be a Link to Special'):
        Registry(Base, Special, Entry)
