"""The standard name and title of every record (evlet/naming.py)."""

import sqlite3

import pytest

from evlet import (Application, Child, Database, LocalAuth, Model, Naming, Registry, Rule, Series, Table, Text,
                   User, ValidationError)
from evlet.auth import AccessError
from evlet.framework import upgrade
from test_business import client, http, PASSWORD

FULL = Rule(('read', 'create', 'update', 'delete'))


class Contact(Model):
    permissions = {'admin': FULL, 'editor': FULL,
                   'viewer': Rule(('read',), read_fields=('email', 'title', 'name'))}
    first_name = Text(required=True)
    last_name = Text()
    email = Text()

    class Meta:
        title = '{first_name} {last_name}'


class Note(Model):
    permissions = {'admin': FULL}
    body = Text()


class Ticket(Model):
    """Names from a method, assigned on create."""
    permissions = {'admin': FULL}
    subject = Text(required=True)

    class Meta:
        naming = Naming()
        title_field = 'subject'

    def autoname(self, context):
        return 'T-' + self.subject.upper().replace(' ', '-')


class Job(Model):
    """A numbered record whose name is labelled for its domain."""
    permissions = {'admin': FULL}
    summary = Text()

    class Meta:
        naming = Naming(Series('JOB-{#####}'), label='Job number')
        layout = (('Job', ('name', 'summary')),)
        list_fields = ('name', 'summary')
        search_fields = ('name', 'summary')


class Code(Model):
    permissions = {'admin': FULL, 'editor': Rule(('read', 'create', 'update'), write_fields=('label',))}
    label = Text()

    class Meta:
        naming = Naming(manual=True)


class Bill(Model):
    """A confirmable document numbered when it is confirmed, so drafts never take a number."""
    confirmable = True
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'confirm'))}
    customer = Text(required=True)

    class Meta:
        naming = Naming(Series('BILL-{####}'), assign_on='confirm')

    class Line(Child):
        name = Text()  # Row tables have no standard name, so a row may have its own.

    lines = Table(Line)


@pytest.fixture
def app(tmp_path):
    database = Database(tmp_path / 'names.sqlite3', Registry(Contact, Note, Ticket, Job, Code, Bill))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


def test_every_record_model_has_name_and_title_and_rows_do_not():
    assert {'name', 'title'} <= set(Contact.__fields__) and {'name', 'title'} <= set(Bill.__fields__)
    registry = Registry(Bill)
    row = next(child.model for child in registry.children)
    assert row.__fields__['name'].standard if hasattr(row.__fields__['name'], 'standard') else True
    assert 'title' not in row.__fields__ and getattr(row.__fields__['name'], 'standard', None) is None
    # The app schema that migrations compare leaves the framework's columns out, like id.
    assert all(field['name'] not in ('name', 'title') for model in Registry(Contact).schema()['models'] for field in model['fields'])


def test_an_application_refuses_a_model_that_declares_its_own_name(tmp_path):
    class Supplier(Model):
        name = Text(required=True)
    with pytest.raises(ValueError, match=r'Supplier.name: "name" is the standard record name.*RenameField'):
        Application(Database(tmp_path / 'x.sqlite3', Registry(Supplier)))


def test_naming_label_names_the_standard_name_field_everywhere_the_schema_reaches(app):
    c = client(app)
    models = {model['name']: model for model in c('GET', '/api/schema')[1]['models']}
    job = models['job']
    label = {field['name']: field['label'] for field in job['fields']}
    # The form, list columns, filters and search all read the field's label from the schema.
    assert label['name'] == 'Job number'
    assert 'name' in job['list_fields'] and 'name' in job['search_fields']
    assert {field['name']: field['label'] for field in models['ticket']['fields']}['name'] == 'Name'
    assert {field['name']: field['label'] for field in models['note']['fields'] if field['name'] == 'name'} in ({}, {'name': 'Name'})
    status, body = c('POST', '/api/job', {'summary': 'Paint'})[:2]
    assert status == 201 and body['name'] == 'JOB-00001'


@pytest.mark.parametrize('label', ['', '   ', 7, 'x' * 81, 'Two\nlines'])
def test_naming_label_is_a_short_line_of_text(label):
    with pytest.raises(ValueError, match='Naming label is a short line of text'):
        Naming(Series('JOB-{#####}'), label=label)


def test_naming_label_belongs_to_the_standard_name():
    with pytest.raises(ValueError, match='Naming label names the standard name field'):
        Naming('code', Series('C-{###}'), label='Code')
    assert Naming(Series('JOB-{#####}'), label='  Job number ').label == 'Job number'


def test_title_follows_its_template_on_every_write_and_falls_back_to_name_then_id(app):
    c = client(app)
    status, contact, _ = c('POST', '/api/contact', {'first_name': 'Mariam', 'last_name': 'Haddad'})
    assert status == 201 and contact['title'] == 'Mariam Haddad' and contact['name'] is None
    url = f"/api/contact/{contact['id']}"
    status, contact, _ = c('PATCH', url, {'last_name': 'Al Haddad'}, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert status == 200 and contact['title'] == 'Mariam Al Haddad'
    # The client cannot write the title: it is derived.
    assert c('PATCH', url, {'title': 'Other'}, headers={b'if-match': c('GET', url)[2][b'etag']})[0] in (403, 422)
    status, note, _ = c('POST', '/api/note', {'body': 'x'})
    assert note['title'] == note['id']
    # Search finds records by title.
    assert [item['id'] for item in c('GET', '/api/contact?q=al%20haddad')[1]['items']] == [contact['id']]


def test_title_is_readable_only_with_its_source_fields(app):
    with app.business.context(User(1, 'admin', 'admin')) as context:
        record = context.create(Contact, first_name='Hidden', email='h@example.test')
    readable = app.business.policy.fields(User(3, 'viewer', 'viewer'), Contact)
    assert 'email' in readable and 'title' not in readable and 'name' in readable
    assert record['title'] == 'Hidden'


def test_autoname_runs_in_the_transaction_and_names_are_unique(app):
    c = client(app)
    status, ticket, _ = c('POST', '/api/ticket', {'subject': 'Printer jam'})
    assert status == 201 and ticket['name'] == 'T-PRINTER-JAM' and ticket['title'] == 'Printer jam'
    status, body, _ = c('POST', '/api/ticket', {'subject': 'printer jam'})
    assert status == 422 and body['fields']['name'] == 'This name is already taken'
    # A system-assigned name is not typed on create.
    status, body, _ = c('POST', '/api/ticket', {'subject': 'Other', 'name': 'MINE'})
    assert status == 422 and body['fields']['name'] == 'Assigned on save'


def test_a_name_changes_through_the_normal_edit_and_the_journal_has_no_rename_feature(app):
    c = client(app)
    ticket = c('POST', '/api/ticket', {'subject': 'Login'})[1]
    url = f"/api/ticket/{ticket['id']}"
    status, changed, _ = c('PATCH', url, {'name': 'T-AUTH-1'}, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert status == 200 and changed['name'] == 'T-AUTH-1' and changed['id'] == ticket['id']
    other = c('POST', '/api/ticket', {'subject': 'Logout'})[1]
    other_url = f"/api/ticket/{other['id']}"
    status, body, _ = c('PATCH', other_url, {'name': 'T-AUTH-1'}, headers={b'if-match': c('GET', other_url)[2][b'etag']})
    assert status in (409, 422)


def test_manual_names_follow_field_permissions(app):
    admin = client(app)
    status, code, _ = admin('POST', '/api/code', {'name': 'VAT-5', 'label': 'Five percent'})
    assert status == 201 and code['name'] == 'VAT-5' and code['title'] == 'VAT-5'
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_users(username,password,role) VALUES ('editor', (SELECT password FROM evlet_users WHERE username='admin'), 'editor')")
    editor = client(app, 'editor')
    url = f"/api/code/{code['id']}"
    assert editor('PATCH', url, {'name': 'VAT-0'}, headers={b'if-match': editor('GET', url)[2][b'etag']})[0] == 403


def test_confirm_time_names_leave_drafts_unnumbered_and_stay_on_reopen(app):
    with app.business.context(User(1, 'admin', 'admin')) as context:
        first = context.create(Bill, customer='A', children={'lines': [{'name': 'row names are fine'}]})
        second = context.create(Bill, customer='B')
        assert first['name'] is None and second['name'] is None and first['title'] == str(first['id'])
        context.confirm(Bill, second['id'])
        context.confirm(Bill, first['id'])
        assert context.get(Bill, second['id']).name == 'BILL-0001' and context.get(Bill, first['id']).name == 'BILL-0002'
        assert context.get(Bill, first['id']).title == 'BILL-0002'
        context.cancel(Bill, first['id'], reason='Typo')
        context.reopen(Bill, first['id'])
        context.confirm(Bill, first['id'])
        assert context.get(Bill, first['id']).name == 'BILL-0002'


def test_links_point_to_ids_so_a_name_change_touches_one_row(app):
    class Order(Model):
        ticket = Text()
    c = client(app)
    ticket = c('POST', '/api/ticket', {'subject': 'Keep'})[1]
    url = f"/api/ticket/{ticket['id']}"
    c('PATCH', url, {'name': 'T-NEW'}, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert c('GET', url)[1]['id'] == ticket['id']
    assert c('GET', f"/api/ticket?q=T-NEW")[1]['items'][0]['id'] == ticket['id']


def test_upgrading_a_database_with_data_adds_the_columns_and_fills_titles(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE contact ("id" INTEGER PRIMARY KEY AUTOINCREMENT, "first_name" TEXT NOT NULL, "last_name" TEXT, "email" TEXT)')
        connection.execute("INSERT INTO contact (first_name, last_name) VALUES ('Old', 'Row')")
    database = Database(path, Registry(Contact))
    database.initialize()  # A table without the standard columns is accepted until the framework step runs.
    assert upgrade(database)[-7:] == ['0003_record_names', '0004_record_times', '0005_unsaved_changes', '0006_fixed_ids', '0007_money_scale', '0008_administrator', '0009_admin_username']
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT id, name, title FROM contact').fetchall() == [(1, None, 'Old Row')]
        assert any(row[2] for row in connection.execute("PRAGMA index_list(contact)") if row[1].endswith('name_unique'))
    database.initialize()
    assert database.create(Contact, first_name='New').title == 'New'
    assert upgrade(database) == []
