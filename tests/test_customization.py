"""Controls model customization: field, layout and list overrides, instance fields, naming series and counters."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest

from evlet import (Application, Database, Date, Decimal, LocalAuth, Link, LongText, Model, Move, Naming, Registry, Select, Series,
                   State, Text)
from evlet.expressions import ConditionError, UnknownField, parse
from test_business import client, hashed, http  # noqa: F401
from test_controls import add_user, password, save  # noqa: F401


def models(lead_meta=None):
    """Fresh classes per test: instance fields are attached to the classes themselves. lead_meta is Lead's Meta."""
    class Customer(Model):
        customer_name = Text(required=True)

    class Lead(Model):
        title = Text(required=True)
        source = Select(('web', 'walk_in', 'referral'))
        value = Decimal(scale=2)
        customer = Link(Customer)
        notes = LongText()
        if lead_meta is not None:
            Meta = lead_meta

    class Invoice(Model):
        reference = Text(unique=True)
        customer = Text(required=True)
        total = Decimal(scale=2, default='0')
        tax_rate = Decimal(scale=2, default='5')
        status = Select(('draft', 'sent'), default='draft')
        posting_date = Date()

        class Meta:
            naming = Naming('reference', Series('ZR-{YYYY}-{####}', when='tax_rate == 0', label='Zero-rated', id='zero'),
                            Series('INV-{YYYY}-{####}', label='Invoices'), date_field='posting_date')

    class Order(Model):
        reference = Text(unique=True)
        amount = Decimal(scale=2)
        status = State(('draft', 'done'), moves=(Move('draft', 'done', 'Finish'),))

        class Meta:
            naming = Naming('reference', Series('ORD-{#####}'))

    return Customer, Lead, Invoice, Order


def build(path, *classes, name='custom.sqlite3'):
    database = Database(path / name, Registry(*classes))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    app.initialize()
    return app


@pytest.fixture
def app(tmp_path, hashed):
    app = build(tmp_path, *models())
    for role in ('admin', 'editor', 'viewer'):
        add_user(app, role, role)
    return app


def model_of(app, name):
    return app.registry.models[name.lower()]


def schema_of(c, name):
    return next(entry for entry in c('GET', '/api/schema')[1]['models'] if entry['name'] == name)


def field_of(entry, name):
    return next(field for field in entry['fields'] if field['name'] == name)


# ---------- fields ----------

def test_a_required_override_is_enforced_by_post_and_shown_in_the_schema(app):
    admin = client(app)
    assert save(admin, ('field', 'Lead.value', {'required': True, 'label': 'Expected value'}))[0] == 200
    status, payload, _ = admin('POST', '/api/lead', {'title': 'Fit-out'})
    assert status == 422 and payload['fields'] == {'value': 'This field is required'}
    assert admin('POST', '/api/lead', {'title': 'Fit-out', 'value': '1200.00'})[0] == 201
    entry = schema_of(client(app, 'editor'), 'lead')
    assert field_of(entry, 'value')['required'] is True and field_of(entry, 'value')['label'] == 'Expected value'
    # Rows saved before the override are not back-filled; an edit must fill the field.
    with app.database.transaction() as tx:
        old = tx.create(model_of(app, 'Lead'), title='Old lead')
    assert admin('PATCH', f'/api/lead/{old.id}', {'title': 'Old lead, renamed'})[1]['fields'] == {'value': 'This field is required'}
    status, payload, _ = admin('POST', '/api/order', {})
    assert status == 201
    assert save(admin, ('field', 'Order.amount', {'required': True}))[0] == 200
    assert admin('POST', '/api/order', {})[1]['fields'] == {'amount': 'This field is required'}


def test_a_hidden_option_is_refused_on_create_and_kept_on_an_unchanged_update(app):
    admin = client(app)
    with app.database.transaction() as tx:
        kept = tx.create(model_of(app, 'Lead'), title='Walked in', source='walk_in')
    assert save(admin, ('field', 'Lead.source', {'options_hidden': ['walk_in'], 'options_add': ['exhibition']}))[0] == 200
    status, payload, _ = admin('POST', '/api/lead', {'title': 'New', 'source': 'walk_in'})
    assert status == 422 and payload['fields'] == {'source': 'This option is no longer offered. Choose another one.'}
    assert admin('POST', '/api/lead', {'title': 'Stand visit', 'source': 'exhibition'})[1]['source'] == 'exhibition'
    status, record, _ = admin('PATCH', f'/api/lead/{kept.id}', {'title': 'Walked in, called back', 'source': 'walk_in'})
    assert status == 200 and record['source'] == 'walk_in'
    moved = admin('POST', '/api/lead', {'title': 'Moved', 'source': 'web'})[1]
    assert admin('PATCH', f'/api/lead/{moved["id"]}', {'source': 'walk_in'})[0] == 422
    field = field_of(schema_of(admin, 'lead'), 'source')
    assert field['choices'] == ['web', 'referral', 'exhibition'] and field['retired_choices'] == ['walk_in']


def test_read_only_and_default_overrides_apply_to_the_api(app):
    admin, editor = client(app), client(app, 'editor')
    assert save(admin, ('field', 'Lead.notes', {'read_only': True}), ('field', 'Lead.source', {'default': 'referral'}))[0] == 200
    assert editor('POST', '/api/lead', {'title': 'A', 'notes': 'hello'})[0] == 403
    record = editor('POST', '/api/lead', {'title': 'A'})[1]
    assert record['source'] == 'referral'
    entry = schema_of(editor, 'lead')
    assert field_of(entry, 'notes')['read_only'] is True
    assert field_of(entry, 'source')['default'] == 'referral' and field_of(entry, 'source')['default_kind'] == 'static'
    with app.database.transaction() as tx:
        # Server code still writes a field that is read only for people.
        assert tx.update(model_of(app, 'Lead'), record['id'], notes='from a job').notes == 'from a job'


@pytest.mark.parametrize('key, value, message', [
    ('Lead.title', {'required': False}, 'is required in models.py and stays required'),
    ('Lead.title', {'hidden': True}, "A required field can't be hidden"),
    ('Lead.value', {'required': True, 'hidden': True}, "A required field can't be hidden"),
    ('Lead.title', {'label': '  '}, "A label can't be empty."),
    ('Lead.source', {'options_hidden': ['web', 'walk_in', 'referral']}, 'Keep at least one option shown.'),
    ('Lead.source', {'options_add': ['web']}, 'web is already an option.'),
    ('Lead.source', {'options_add': ['Walk In']}, 'is not a usable option'),
    ('Lead.source', {'default': 'walk_in', 'options_hidden': ['walk_in']}, 'Choose a default from the options shown.'),
    ('Lead.customer', {'default': 1}, "Links don't take a default."),
    ('Lead.value', {'default': 'lots'}, 'The default is not a valid value'),
    ('Lead.title', {'options_add': ['x']}, 'Only choice fields have options.'),
    ('Invoice.reference', {'required': True}, 'filled by the app or the server'),
])
def test_field_overrides_can_only_make_things_stricter(app, key, value, message):
    status, payload, _ = save(client(app), ('field', key, value))
    assert status == 422 and message in payload['fields']['changes.0'], payload


def test_a_field_override_that_no_longer_validates_makes_the_field_read_only(app):
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_overrides(kind,key,value) VALUES ('field','Lead.source',?)", (json.dumps({'options_add': ['web']}),))
        tx.connection.execute('UPDATE evlet_config SET version=version+1')
    admin = client(app)
    assert admin('POST', '/api/lead', {'title': 'x', 'source': 'web'})[0] == 403
    state = admin('GET', '/api/_controls/state')[1]
    assert any(item['page'] == 'models' and 'Lead.source' in item['title'] for item in state['attention'])


# ---------- layout and list ----------

def test_an_unplaced_field_is_added_to_the_last_section(app):
    admin = client(app)
    layout = [{'title': 'Lead', 'fields': [{'name': 'title', 'width': 12}]}, {'title': 'Money', 'fields': [{'name': 'value', 'width': 6}]}]
    assert save(admin, ('layout', 'Lead', layout))[0] == 200
    entry = schema_of(admin, 'lead')
    assert entry['layout'] == [{'label': 'Lead', 'fields': ['title'], 'wide': ['title'], 'collapsible': False, 'collapsed': False, 'flow': 'rows'},
                               {'label': 'Money', 'fields': ['value', 'source', 'customer', 'notes'], 'wide': ['notes'], 'collapsible': False, 'collapsed': False, 'flow': 'rows'}]
    for bad, message in (([], 'Keep between 1 and 12 sections.'), ([{'title': '', 'fields': []}], 'Give every section a title'),
                         ([{'title': 'A', 'fields': [{'name': 'title', 'width': 6}, {'name': 'title', 'width': 6}]}], 'Put each field in one place.'),
                         ([{'title': 'A', 'fields': [{'name': 'nothing', 'width': 6}]}], 'Place fields of Lead by name')):
        assert message in save(admin, ('layout', 'Lead', bad))[1]['fields']['changes.0']


def test_a_layout_section_can_collapse_and_choose_how_it_starts(app):
    admin = client(app)
    layout = [{'title': 'Lead', 'fields': [{'name': 'title', 'width': 12}]},
              {'title': 'Money', 'fields': [{'name': 'value', 'width': 6}], 'collapsible': True, 'collapsed': True},
              {'title': 'Notes', 'fields': [{'name': 'notes', 'width': 12}], 'collapsible': True, 'collapsed': 'empty'}]
    assert save(admin, ('layout', 'Lead', layout))[0] == 200
    assert [(group['label'], group['collapsible'], group['collapsed']) for group in schema_of(admin, 'lead')['layout']] == [
        ('Lead', False, False), ('Money', True, True), ('Notes', True, 'empty')]
    # Stored with the options only on sections that can collapse, so plain sections read as before.
    saved = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'layout' and item['key'] == 'Lead')
    assert [{key: section.get(key) for key in ('collapsible', 'collapsed') if key in section} for section in saved['value']] == [
        {}, {'collapsible': True, 'collapsed': True}, {'collapsible': True, 'collapsed': 'empty'}]
    assert saved['file'] == [{'title': 'Details', 'fields': [{'name': name, 'width': 12 if name == 'notes' else 6} for name in ('title', 'source', 'value', 'customer', 'notes')]}]
    # Turning collapse off again with Starts open is the plain section.
    plain = [{'title': 'Details', 'fields': [{'name': name, 'width': 12 if name == 'notes' else 6} for name in ('title', 'source', 'value', 'customer', 'notes')],
              'collapsible': False, 'collapsed': False, 'flow': 'rows'}]
    assert save(admin, ('layout', 'Lead', plain))[0] == 200
    assert next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'layout' and item['key'] == 'Lead')['value'] is None
    for value, message in ((dict(collapsible='yes'), 'Section Money: use true or false for Can collapse.'),
                           (dict(collapsible=True, collapsed='later'), 'Section Money: Starts must be open'),
                           (dict(collapsible=True, collapsed=1), 'Section Money: Starts must be open'),
                           (dict(collapsible=False, collapsed=True), "Section Money can't collapse, so it starts open."),
                           (dict(collapsed='empty'), "Section Money can't collapse"),
                           (dict(open=True), 'optionally collapsible, collapsed and flow')):
        bad = [{'title': 'Money', 'fields': [{'name': 'value', 'width': 6}], **value}]
        assert message in save(admin, ('layout', 'Lead', bad))[1]['fields']['changes.0']


def test_a_layout_saved_before_collapsible_sections_still_loads(tmp_path, hashed):
    build(tmp_path, *models())
    # Written the way older versions stored it: no collapsible or collapsed keys.
    old = [{'title': 'Main', 'fields': [{'name': 'title', 'width': 6}]}, {'title': 'Rest', 'fields': [{'name': 'notes', 'width': 12}]}]
    with sqlite3.connect(tmp_path / 'custom.sqlite3') as connection:
        connection.execute("INSERT INTO evlet_overrides(kind,key,value,changed_by,changed_at) VALUES ('layout','Lead',?,NULL,0)", (json.dumps(old),))
    app = build(tmp_path, *models())
    add_user(app, 'admin', 'admin')
    admin = client(app)
    assert admin('GET', '/api/_controls/state')[1]['attention'] == []
    assert [(group['label'], group['collapsible'], group['collapsed']) for group in schema_of(admin, 'lead')['layout']] == [
        ('Main', False, False), ('Rest', False, False)]
    saved = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'layout' and item['key'] == 'Lead')
    assert saved['value'] == old


def test_a_layout_sets_section_flow(app):
    admin = client(app)
    sections = [{'title': 'Lead', 'fields': [{'name': 'title', 'width': 6}, {'name': 'source', 'width': 6}, {'name': 'value', 'width': 6}], 'flow': 'columns'},
                {'title': 'Notes', 'fields': [{'name': 'notes', 'width': 12}]}]
    assert save(admin, ('layout', 'Lead', sections))[0] == 200
    entry = schema_of(admin, 'lead')
    assert [(group['label'], group['flow']) for group in entry['layout']] == [('Lead', 'columns'), ('Notes', 'rows')]
    assert 'tabs' not in entry and 'default_tab' not in entry
    state = admin('GET', '/api/_controls/state')[1]
    lead = next(model for model in state['models'] if model['name'] == 'Lead')
    # The record's parts the side panel can hide.
    assert [tab['name'] for tab in lead['tabs']] == ['activity', 'details', 'revisions', 'related', 'postings']
    # Sections alone are saved as the plain list they always were; rows is not stored.
    assert save(admin, ('layout', 'Lead', [{**sections[0], 'flow': 'rows'}, sections[1]]))[0] == 200
    saved = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'layout' and item['key'] == 'Lead')['value']
    assert isinstance(saved, list) and 'flow' not in saved[0]
    # Record pages have no tabs: a record tab order is refused.
    for bad, message in (({'sections': sections, 'tabs': ['activity', 'details']}, 'Record pages have no tabs: send the layout as a list of sections'),
                         ({'sections': sections, 'default_tab': 'activity'}, 'Record pages have no tabs'),
                         ({'sections': sections, 'extra': 1}, 'Send the layout as a list of sections, or as sections and panel.'),
                         ([{**sections[0], 'flow': 'down'}], 'Section Lead: fill fields in rows ("rows") or down each column ("columns").')):
        assert message in save(admin, ('layout', 'Lead', bad))[1]['fields']['changes.0']


def test_a_layout_saved_with_a_record_tab_order_keeps_its_sections(app):
    # Saved before record tabs were removed in 0.4: its sections and side panel still apply, the tab order is dropped.
    admin = client(app)
    sections = [{'title': 'Lead', 'fields': [{'name': 'title', 'width': 6}, {'name': 'source', 'width': 6}]},
                {'title': 'More', 'fields': [{'name': 'notes', 'width': 12}]}]
    legacy = {'sections': sections, 'tabs': ['activity', 'details'], 'default_tab': 'activity', 'panel': {'sections': ['More']}}
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_overrides (kind, key, value) VALUES ('layout', 'Lead', ?)", (json.dumps(legacy),))
    problems = []
    custom = app.business.controls.custom
    with app.database.transaction() as tx:
        rows = tx.connection.execute("SELECT kind, key, value FROM evlet_overrides").fetchall()
    custom.apply(rows, problems)
    assert problems == []
    entry = schema_of(admin, 'lead')
    assert [group['label'] for group in entry['layout']] == ['Lead', 'More'] and entry['panel']['sections'] == ['More']
    assert 'tabs' not in entry and 'default_tab' not in entry


def test_removing_a_field_keeps_the_sections_of_a_layout(app):
    admin = client(app)
    assert admin('POST', '/api/_controls/fields/Lead', {'name': 'region', 'type': 'Text', 'label': 'Region'})[0] == 201
    value = [{'title': 'Lead', 'fields': [{'name': 'title', 'width': 6}, {'name': 'x_region', 'width': 6}]}]
    assert save(admin, ('layout', 'Lead', value))[0] == 200
    assert admin('DELETE', '/api/_controls/fields/Lead/x_region')[0] == 200
    saved = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'layout' and item['key'] == 'Lead')['value']
    assert saved == [{'title': 'Lead', 'fields': [{'name': 'title', 'width': 6}]}]


def test_a_list_override_sets_columns_page_size_and_sort_and_drops_unreadable_columns(app):
    admin = client(app)
    value = {'columns': ['title', 'value', 'source'], 'page_size': 25, 'sort': '-value'}
    assert save(admin, ('list', 'Lead', value), ('perm', 'editor|Lead', {'ops': ['read'], 'read': ['title', 'source', 'customer', 'notes'], 'write': None, 'scope': None}))[0] == 200
    entry = schema_of(admin, 'lead')
    assert (entry['list_fields'], entry['page_size'], entry['order_by']) == (['id', 'title', 'value', 'source'], 25, '-value')
    entry = schema_of(client(app, 'editor'), 'lead')
    assert entry['list_fields'] == ['id', 'title', 'source'] and 'order_by' not in entry
    assert save(admin, ('list', 'Lead', {**value, 'page_size': 30}))[1]['fields']['changes.0'] == 'Show 25, 50 or 100 rows per page.'


def test_a_list_override_chooses_whether_records_open_in_a_side_panel(app):
    admin = client(app)
    assert schema_of(admin, 'lead')['open_in'] == 'page'
    # A list view saved before open_in existed keeps the app's choice.
    value = {'columns': ['title', 'value'], 'page_size': 25, 'sort': 'title'}
    assert save(admin, ('list', 'Lead', value))[0] == 200
    assert schema_of(admin, 'lead')['open_in'] == 'page'
    assert save(admin, ('list', 'Lead', {**value, 'open_in': 'panel'}))[0] == 200
    assert schema_of(admin, 'lead')['open_in'] == 'panel'
    assert schema_of(client(app, 'editor'), 'lead')['open_in'] == 'panel'
    assert save(admin, ('list', 'Lead', {**value, 'open_in': 'drawer'}))[1]['fields']['changes.0'] == 'Open records in the full page or a side panel.'
    assert save(admin, ('list', 'Lead', {**value, 'open': 'panel'}))[1]['fields']['changes.0'] == 'Send columns, page_size and sort, and optionally open_in, list_tabs and list_tab.'


# ---------- instance fields ----------

def backups(app):
    folder = app.database.path.parent / 'backups'
    return sorted(folder.iterdir()) if folder.exists() else []


def columns(app, table):
    with app.database._connection() as db:
        return [row[1] for row in db.execute(f'PRAGMA table_info({table})')]


def test_adding_and_removing_an_instance_field_takes_backups_and_cleans_overrides(app):
    admin = client(app)
    status, added, _ = admin('POST', '/api/_controls/fields/Invoice', {'name': 'branch', 'type': 'Text', 'label': 'Branch'})
    assert status == 201 and added['name'] == 'x_branch' and len(backups(app)) == 1
    assert 'x_branch' in columns(app, 'invoice')
    record = admin('POST', '/api/invoice', {'customer': 'Gulf Steel', 'x_branch': 'dxb'})[1]
    assert record['x_branch'] == 'dxb'
    assert field_of(schema_of(admin, 'invoice'), 'x_branch')['label'] == 'Branch'
    series = [{'id': 'dubai', 'label': 'Dubai', 'format': 'DXB-{####}', 'when': 'x_branch == "dxb"'},
              {'id': 'default', 'label': 'Invoices', 'format': 'INV-{YYYY}-{####}', 'when': ''}]
    assert save(admin, ('field', 'Invoice.x_branch', {'label': 'Branch code'}),
                ('list', 'Invoice', {'columns': ['reference', 'x_branch'], 'page_size': 50, 'sort': '-x_branch'}),
                ('layout', 'Invoice', [{'title': 'Main', 'fields': [{'name': 'customer', 'width': 6}, {'name': 'x_branch', 'width': 6}]}]),
                ('perm', 'editor|Invoice', {'ops': ['read'], 'read': ['reference', 'x_branch'], 'write': None, 'scope': None}),
                ('naming', 'Invoice', series))[0] == 200
    assert admin('POST', '/api/invoice', {'customer': 'Noor', 'x_branch': 'dxb'})[1]['reference'] == 'DXB-0001'
    status, removed, _ = admin('DELETE', '/api/_controls/fields/Invoice/x_branch')
    assert status == 200 and len(backups(app)) == 2
    assert 'x_branch' not in columns(app, 'invoice') and 'x_branch' not in model_of(app, 'Invoice').__declared_fields__
    overrides = {(item['kind'], item['key']): item['value'] for item in admin('GET', '/api/_controls/overrides')[1]['items']}
    assert ('field', 'Invoice.x_branch') not in overrides
    assert overrides[('list', 'Invoice')] == {'columns': ['reference'], 'page_size': 50, 'sort': '-id'}
    assert overrides[('layout', 'Invoice')] == [{'title': 'Main', 'fields': [{'name': 'customer', 'width': 6}]}]
    assert overrides[('perm', 'editor|Invoice')]['read'] == ['reference']
    assert overrides[('naming', 'Invoice')] == [series[1]]
    assert admin('GET', '/api/_controls/state')[1]['attention'] == []
    assert admin('POST', '/api/invoice', {'customer': 'Blue Dune'})[0] == 201
    with app.database._connection() as db:
        details = [row[0] for row in db.execute("SELECT detail FROM evlet_audit WHERE area='Models' ORDER BY id")]
    assert details[0].startswith('Field added (Text)') and details[-1].startswith('Field removed with 5 overrides cleaned')


def test_instance_field_rules(app):
    admin = client(app)
    admin('POST', '/api/lead', {'title': 'One'})
    for body, field, message in (({'name': 'Bad Name', 'type': 'Text', 'label': 'T'}, 'name', 'Use lowercase letters'),
                                 ({'name': 'size', 'type': 'Blob', 'label': 'T'}, 'type', None),
                                 ({'name': 'kind', 'type': 'Select', 'label': 'Kind', 'options': ['one']}, 'options', None),
                                 ({'name': 'due', 'type': 'Date', 'label': 'Due', 'required': True}, 'default', '1 records already exist')):
        status, payload, _ = admin('POST', '/api/_controls/fields/Lead', body)
        assert status == 422 and field in payload['fields'] and (message is None or message in payload['fields'][field]), payload
    assert admin('POST', '/api/_controls/fields/Lead', {'name': 'title', 'type': 'Text', 'label': 'T'})[0] == 201
    assert admin('POST', '/api/_controls/fields/Lead', {'name': 'x_title', 'type': 'Text', 'label': 'T'})[1]['fields']['name'] == 'Lead already has a field called x_title.'
    status, _, _ = admin('POST', '/api/_controls/fields/Lead', {'name': 'tier', 'type': 'Select', 'label': 'Tier', 'options': ['gold', 'silver'],
                                                                 'required': True, 'default': 'silver'})
    assert status == 201
    assert admin('GET', '/api/lead')[1]['items'][0]['x_tier'] == 'silver'
    assert admin('POST', '/api/lead', {'title': 'Two', 'x_tier': None})[1]['fields'] == {'x_tier': 'This field is required'}
    assert admin('POST', '/api/_controls/fields/Lead', {'name': 'account', 'type': 'Link', 'label': 'Account', 'target': 'customer'})[0] == 201
    customer = admin('POST', '/api/customer', {'customer_name': 'Gulf Steel'})[1]
    assert admin('POST', '/api/lead', {'title': 'Three', 'x_tier': 'gold', 'x_account': customer['id']})[1]['x_account'] == customer['id']
    assert admin('DELETE', '/api/_controls/fields/Lead/x_account')[0] == 200
    assert admin('DELETE', '/api/_controls/fields/Lead/x_nothing')[0] == 404
    assert client(app, 'editor')('POST', '/api/_controls/fields/Lead', {'name': 'a', 'type': 'Text', 'label': 'A'})[0] == 404


def test_old_sqlite_disables_removal_with_a_message(tmp_path, hashed, monkeypatch):
    from evlet import sqlite
    monkeypatch.setattr(sqlite, 'sqlite_version', lambda connection: '3.31.1')
    app = build(tmp_path, *models())
    add_user(app, 'admin', 'admin')
    admin = client(app)
    assert admin('POST', '/api/_controls/fields/Lead', {'name': 'grade', 'type': 'Integer', 'label': 'Grade'})[0] == 201
    status, payload, _ = admin('DELETE', '/api/_controls/fields/Lead/x_grade')
    assert status == 409 and payload['error'].startswith('Removing a field needs SQLite 3.35 or later, and this server has 3.31.1.')
    assert admin('GET', '/api/_controls/state')[1]['remove_fields'] == payload['error']
    assert 'x_grade' in columns(app, 'lead')


def test_a_second_process_picks_up_an_instance_field(tmp_path, hashed):
    first = build(tmp_path, *models())
    add_user(first, 'admin', 'admin')
    second = build(tmp_path, *models())
    assert client(first)('POST', '/api/_controls/fields/Lead', {'name': 'region', 'type': 'Text', 'label': 'Region'})[0] == 201
    record = client(second)('POST', '/api/lead', {'title': 'Elsewhere', 'x_region': 'north'})[1]
    assert record['x_region'] == 'north'
    # A restart reads the instance fields again; app schema checks leave x_ columns alone.
    third = build(tmp_path, *models())
    assert client(third)('GET', f'/api/lead/{record["id"]}')[1]['x_region'] == 'north'


def test_app_fields_cannot_use_the_instance_prefix():
    with pytest.raises(ValueError, match='kept for fields added on an instance'):
        type('Clash', (Model,), {'x_code': Text()})


# ---------- the condition language ----------

def fields():
    return models()[2].__declared_fields__


@pytest.mark.parametrize('source', [
    'total >= 250000', 'status == "sent"', "status == 'sent'", 'status != "draft"', 'total > 1', 'total < 1', 'total <= 1',
    'status in ("sent", "draft")', 'customer != empty', 'not status == "sent"', '(total > 1 or tax_rate == 0) and status == "sent"',
    'posting_date >= "2026-01-01"', 'total == -5.25',
])
def test_each_grammar_rule_parses(source):
    assert parse(source, fields()).fields


@pytest.mark.parametrize('source, message', [
    ('status = "sent"', 'Use == to compare, not =.'),
    ('totl > 5', "totl isn't a field on this model. Did you mean total?"),
    ('status == export', 'Put text values in quotes, like "export".'),
    ('status == "sent', 'A quote is not closed.'),
    ('zzz == 1', "zzz isn't a field on this model."),
    ('total > ', 'A value is missing at the end.'),
    ('total >= 1 and', 'The condition ends too early.'),
    ('total 5', 'Compare total with ==, !=, >, >=, < or <=.'),
    ('total > 1 status == "sent"', 'Unexpected "status". Join conditions with and / or.'),
    ('len(customer) > 2', "len isn't a field on this model."),
    ('customer.name == "a"', '"." isn\'t allowed here.'),
    ('total + 1 > 2', '"+" isn\'t allowed here.'),
    ('(total > 1', 'A bracket is not closed.'),
    ('status in "sent"', 'Put the values in brackets'),
    ('total == "5"', 'total is a number, so leave out the quotes.'),
    ('status == 5', 'Put text values in quotes, like "5".'),
    ('x' * 501, 'Keep a condition under 500 characters.'),
    ('not ' * 11 + 'total > 1', 'Nest brackets and not at most 10 deep.'),
    ('== 1', 'Start with a field name'),
])
def test_each_grammar_error_has_its_message(source, message):
    with pytest.raises(ConditionError) as raised:
        parse(source, fields())
    assert str(raised.value).startswith(message)


def test_conditions_compare_by_type():
    from datetime import date
    from decimal import Decimal as D
    values = {'total': D('250000.00'), 'status': 'sent', 'customer': '', 'tax_rate': D('0'), 'posting_date': date(2026, 3, 1)}
    assert parse('total >= 250000 and status == "sent"', fields()).matches(values)
    assert parse('customer == empty and tax_rate == 0', fields()).matches(values)
    assert parse('posting_date < "2026-12-31"', fields()).matches(values)
    assert not parse('status in ("draft")', fields()).matches(values)
    assert parse('not (total < 1)', fields()).matches(values)
    with pytest.raises(UnknownField):
        parse('x_gone == "a"', fields())


# ---------- naming series ----------

def test_the_first_matching_series_wins_and_the_last_catches_everything_else(app):
    admin = client(app)
    assert admin('POST', '/api/invoice', {'customer': 'A', 'tax_rate': '0', 'posting_date': '2026-03-01'})[1]['reference'] == 'ZR-2026-0001'
    assert admin('POST', '/api/invoice', {'customer': 'B', 'posting_date': '2026-03-01'})[1]['reference'] == 'INV-2026-0001'
    series = [{'id': 'large', 'label': 'Large contracts', 'format': 'LC-{YY}{MM}-{customer}-{###}', 'when': 'total >= 250000 and status == "sent"'},
              {'id': 'zero', 'label': 'Zero-rated', 'format': 'ZR-{YYYY}-{####}', 'when': 'tax_rate == 0'},
              {'id': 'default', 'label': 'Invoices', 'format': 'INV-{YYYY}-{####}', 'when': ''}]
    assert save(admin, ('naming', 'Invoice', series))[0] == 200
    big = {'customer': 'Gulf Steel', 'total': '300000', 'status': 'sent', 'tax_rate': '0', 'posting_date': '2026-11-02'}
    assert admin('POST', '/api/invoice', big)[1]['reference'] == 'LC-2611-Gulf-Steel-001'
    assert admin('POST', '/api/invoice', {**big, 'status': 'draft'})[1]['reference'] == 'ZR-2026-0002'
    assert admin('POST', '/api/invoice', {'customer': 'C', 'posting_date': '2026-11-02'})[1]['reference'] == 'INV-2026-0002'
    assert admin('POST', '/api/invoice', {'customer': 'D', 'reference': 'MINE-1'})[0] == 403
    assert field_of(schema_of(admin, 'invoice'), 'reference')['read_only'] is True
    assert admin('POST', '/api/order', {})[1]['reference'] == 'ORD-00001'


@pytest.mark.parametrize('series, message', [
    ([{'id': 'a', 'label': 'A', 'format': 'A-{####}', 'when': 'total > 1'}], 'The last series has no condition'),
    ([{'id': 'a', 'label': 'A', 'format': 'A-{####}', 'when': ''}, {'id': 'b', 'label': 'B', 'format': 'B-{####}', 'when': ''}], 'Only the last series'),
    ([{'id': 'a', 'label': 'A', 'format': 'A-{####}', 'when': 'totl > 1'}, {'id': 'b', 'label': 'B', 'format': 'B-{####}', 'when': ''}], 'Did you mean total?'),
    ([{'id': 'a', 'label': 'A', 'format': 'A-{##}', 'when': ''}], 'A counter has 3 to 8 #'),
    ([{'id': 'a', 'label': 'A', 'format': 'A-{YYYY}', 'when': ''}], 'Use exactly one counter'),
    ([{'id': 'a', 'label': 'A', 'format': 'A-{####', 'when': ''}], 'A brace is not closed.'),
    ([{'id': 'a', 'label': 'A', 'format': 'A-{branch}-{####}', 'when': ''}], "{branch} isn't a token or a field on Invoice."),
    ([{'id': 'a', 'label': 'A', 'format': 'A-{total}-{####}', 'when': ''}], 'must be a short text field'),
    ([{'id': 'a', 'label': 'A', 'format': 'A' * 37 + '{####}', 'when': ''}], 'Names from this format would be longer than 40 characters.'),
])
def test_naming_overrides_are_checked_when_saved(app, series, message):
    status, payload, _ = save(client(app), ('naming', 'Invoice', series))
    assert status == 422 and message in payload['fields']['changes.0'], payload


def test_a_series_whose_condition_names_a_missing_field_is_skipped_and_listed(app):
    series = [{'id': 'gone', 'label': 'Gone', 'format': 'G-{####}', 'when': 'x_gone == "a"'},
              {'id': 'default', 'label': 'Invoices', 'format': 'N-{####}', 'when': ''}]
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_overrides(kind,key,value) VALUES ('naming','Invoice',?)", (json.dumps(series),))
        tx.connection.execute('UPDATE evlet_config SET version=version+1')
    admin = client(app)
    assert admin('POST', '/api/invoice', {'customer': 'A'})[1]['reference'] == 'N-0001'
    attention = admin('GET', '/api/_controls/state')[1]['attention']
    assert attention[0]['page'] == 'naming' and 'Series gone is skipped' in attention[0]['text']


def test_a_year_change_starts_a_new_counter(app):
    admin = client(app)
    names = [admin('POST', '/api/invoice', {'customer': 'A', 'posting_date': day})[1]['reference']
             for day in ('2025-12-31', '2026-01-01', '2025-12-30', '2026-01-02')]
    assert names == ['INV-2025-0001', 'INV-2026-0001', 'INV-2025-0002', 'INV-2026-0002']
    counters = admin('GET', '/api/_controls/naming/Invoice/counters')[1]
    assert {item['prefix']: (item['value'], item['last_name'], item['next']) for item in counters['items']} == {
        'INV-2025-': (2, 'INV-2025-0002', 'INV-2025-0003'), 'INV-2026-': (2, 'INV-2026-0002', 'INV-2026-0003')}
    assert {item['changed_by'] for item in counters['items']} == {'admin'}


def test_fifty_concurrent_creates_get_distinct_names(app):
    admin = client(app)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda index: admin('POST', '/api/invoice', {'customer': f'C{index}', 'posting_date': '2026-05-05'}), range(50)))
    assert all(status == 201 for status, _, _ in results)
    names = {record['reference'] for _, record, _ in results}
    assert names == {f'INV-2026-{number:04d}' for number in range(1, 51)}


def test_imported_names_are_kept_and_skipped(app):
    with app.database.transaction() as tx:
        tx.create(model_of(app, 'Invoice'), customer='Old', reference='INV-2026-0001', posting_date='2026-02-02')
        assert tx.create(model_of(app, 'Invoice'), customer='Script', posting_date='2026-02-02').reference == 'INV-2026-0002'
    assert client(app)('POST', '/api/invoice', {'customer': 'New', 'posting_date': '2026-02-02'})[1]['reference'] == 'INV-2026-0003'


def test_a_counter_cannot_be_set_below_the_last_issued_name(app):
    admin = client(app)
    for _ in range(3):
        admin('POST', '/api/invoice', {'customer': 'A', 'posting_date': '2026-04-04'})
    url = '/api/_controls/naming/Invoice/counters'
    status, payload, _ = admin('POST', url, {'prefix': 'INV-2026-', 'value': 2, 'note': 'Oops'})
    assert status == 422 and payload['fields']['value'].startswith('Lower than INV-2026-0003, which already exists.')
    assert admin('POST', url, {'prefix': 'INV-2026-', 'value': 500, 'note': ' '})[1]['fields']['note'].startswith('Give a reason')
    assert admin('POST', url, {'prefix': 'XX-', 'value': 5, 'note': 'x'})[1]['fields']['prefix'] == 'No series of this model uses this prefix.'
    items = admin('POST', url, {'prefix': 'INV-2026-', 'value': 500, 'note': 'Continues from the old system'})[1]['items']
    assert items[0]['how'] == 'set' and items[0]['note'] == 'Continues from the old system' and items[0]['next'] == 'INV-2026-0501'
    assert admin('POST', url, {'prefix': 'ZR-2027-', 'value': 37, 'note': 'Carried over'})[0] == 200
    assert admin('POST', '/api/invoice', {'customer': 'B', 'posting_date': '2026-04-04'})[1]['reference'] == 'INV-2026-0501'
    with app.database._connection() as db:
        assert db.execute("SELECT detail FROM evlet_audit WHERE area='Naming'").fetchone()[0] == 'Counter 3 → 500 · Continues from the old system'
    assert client(app, 'editor')('GET', url)[0] == 404


def test_try_it_changes_no_counter(app):
    admin = client(app)
    admin('POST', '/api/invoice', {'customer': 'A', 'posting_date': '2026-04-04'})
    with app.database._connection() as db:
        before = [tuple(row) for row in db.execute('SELECT * FROM evlet_sequence')]
    url = '/api/_controls/naming/Invoice/try'
    result = admin('POST', url, {'values': {'customer': 'A', 'tax_rate': '0', 'posting_date': '2026-04-04'}})[1]
    assert (result['match'], result['name']) == ('zero', 'ZR-2026-0001')
    assert admin('POST', url, {'values': {'posting_date': '2026-04-04'}})[1]['name'] == 'INV-2026-0002'
    draft = [{'id': 'big', 'label': 'Big', 'format': 'BIG-{####}', 'when': 'totl > 5'},
             {'id': 'all', 'label': 'All', 'format': 'ALL-{####}', 'when': ''}]
    result = admin('POST', url, {'values': {'total': '10'}, 'series': draft})[1]
    assert result['checks'][0]['when'] == "totl isn't a field on this model. Did you mean total?" and result['name'] == 'ALL-0001'
    with app.database._connection() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM evlet_sequence')] == before


def test_naming_declarations_are_checked():
    with pytest.raises(ValueError, match='must be a stored Text field with unique=True'):
        class Bad(Model):
            reference = Text()

            class Meta:
                naming = Naming('reference', Series('B-{####}'))
        Registry(Bad)
    with pytest.raises(ValueError, match='The last series has no condition'):
        Naming('reference', Series('B-{####}', when='total > 1'))
    with pytest.raises(ValueError, match='Use exactly one counter'):
        Series('B-{YYYY}')


def test_the_counter_table_gains_its_new_columns(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE evlet_sequence (model TEXT NOT NULL, field TEXT NOT NULL, scope_key TEXT NOT NULL, period TEXT NOT NULL, '
                   '"last" INTEGER NOT NULL, PRIMARY KEY (model, field, scope_key, period))')
    from evlet.sequences import upgrade_counters
    from evlet.sqlite import SQLiteConnection
    with sqlite3.connect(path, factory=SQLiteConnection) as db:
        upgrade_counters(db)
        assert {row[1] for row in db.execute('PRAGMA table_info(evlet_sequence)')} >= {'last_name', 'how', 'note', 'changed_by', 'changed_at'}


def test_an_instance_field_on_a_finite_document(tmp_path, hashed):
    from evlet import Currency
    from evlet.calc import round

    class Quote(Model):
        qty = Decimal(default=1)
        rate = Currency(default=0)
        amount = Currency(compute=round(qty * rate, 2))

        class Meta:
            calculation_language = 'finite-v1'

    app = build(tmp_path, Quote)
    add_user(app, 'admin', 'admin')
    admin = client(app)
    first = admin('POST', '/api/quote', {'qty': '2', 'rate': '3'})
    assert admin('POST', '/api/_controls/fields/Quote', {'name': 'note', 'type': 'Text', 'label': 'Note'})[0] == 201
    status, record, _ = admin('POST', '/api/quote', {'qty': '2', 'rate': '4', 'x_note': 'rush'})
    assert status == 201, record
    assert (record['amount'], record['x_note']) == ('8.00', 'rush')
    # The new field is part of each record now, so the revision a client holds from before has moved.
    assert admin('PATCH', f'/api/quote/{first[1]["id"]}', {'x_note': 'later'}, headers={b'if-match': first[2][b'etag']})[0] == 409
    etag = admin('GET', f'/api/quote/{first[1]["id"]}')[2][b'etag']
    status, record, _ = admin('PATCH', f'/api/quote/{first[1]["id"]}', {'x_note': 'later'}, headers={b'if-match': etag})
    assert status == 200, record
    assert (record['amount'], record['x_note']) == ('6.00', 'later')


def test_a_revision_gets_a_new_name(tmp_path, hashed):
    class Quote(Model):
        reference = Text(unique=True)
        status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),), locked=('issued', 'void'),
                       final=('void',), voidable=('issued',))

        class Meta:
            revisable = True
            naming = Naming('reference', Series('Q-{###}'))

    app = build(tmp_path, Quote)
    add_user(app, 'admin', 'admin')
    admin = client(app)
    status, record, headers = admin('POST', '/api/quote', {})
    assert (status, record['reference']) == (201, 'Q-001')
    quote = f"/api/quote/{record['id']}"
    status, record, headers = admin('POST', quote + '/moves/issued', {}, headers={b'if-match': headers[b'etag']})
    assert status == 200, record
    status, copy, _ = admin('POST', quote + '/revise', {'reason': 'Wrong price'}, headers={b'if-match': headers[b'etag']})
    assert status == 201 and copy['reference'] == 'Q-002', copy


# ---------- review findings ----------

def test_two_applications_sharing_classes_keep_their_own_instance_fields(tmp_path, hashed):
    classes = models()
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    first, second = build(tmp_path / 'a', *classes), build(tmp_path / 'b', *classes)
    for app in (first, second):
        add_user(app, 'admin', 'admin')
    a, b = client(first), client(second)
    assert a('POST', '/api/_controls/fields/Customer', {'name': 'region', 'type': 'Text', 'label': 'Region'})[0] == 201
    assert b('POST', '/api/_controls/fields/Customer', {'name': 'tier', 'type': 'Integer', 'label': 'Tier'})[0] == 201
    assert b('POST', '/api/customer', {'customer_name': 'y', 'x_tier': 2})[1]['x_tier'] == 2
    assert b('POST', '/api/customer', {'customer_name': 'y', 'x_region': 'n'})[0] == 403
    assert save(b, ('field', 'Lead.title', {'label': 'Topic'}))[0] == 200
    record = a('POST', '/api/customer', {'customer_name': 'z', 'x_region': 'n'})[1]
    assert record['x_region'] == 'n' and 'x_tier' not in record
    assert [field['name'] for field in schema_of(a, 'customer')['fields']] == ['customer_name', 'name', 'title', 'x_region']
    assert 'x_region' not in classes[0].__declared_fields__ and not hasattr(classes[0], 'x_region')


@pytest.mark.parametrize('phase', ['add', 'remove'])
def test_requests_during_instance_field_changes_never_fail(app, phase):
    admin, editor = client(app), client(app, 'editor')
    record = admin('POST', '/api/lead', {'title': 'seed'})[1]
    if phase == 'remove':
        for index in range(4):
            assert admin('POST', '/api/_controls/fields/Lead', {'name': f'f{index}', 'type': 'Text', 'label': 'F'})[0] == 201
    stop, failures = threading.Event(), []

    def work(index):
        while not stop.is_set():
            for status, payload, _ in (editor('POST', '/api/lead', {'title': f'w{index}'}), editor('GET', '/api/lead'),
                                       editor('GET', f'/api/lead/{record["id"]}'), editor('PATCH', f'/api/lead/{record["id"]}', {'title': 'p'})):
                if status >= 500:
                    failures.append((status, payload))
    with ThreadPoolExecutor(max_workers=4) as pool:
        running = [pool.submit(work, index) for index in range(4)]
        for index in range(4):
            if phase == 'add':
                assert admin('POST', '/api/_controls/fields/Lead', {'name': f'f{index}', 'type': 'Text', 'label': 'F'})[0] == 201
            else:
                assert admin('DELETE', f'/api/_controls/fields/Lead/x_f{index}')[0] == 200
        stop.set()
        for future in running:
            future.result()
    assert failures == []


def test_added_options_hold_on_every_write_path(app):
    from evlet.auth import User
    admin = client(app)
    assert save(admin, ('field', 'Lead.source', {'options_add': ['exhibition']}))[0] == 200
    Lead = model_of(app, 'Lead')
    record = app.database.create(Lead, title='a', source='exhibition')
    assert app.database.update(Lead, record.id, title='b').source == 'exhibition'
    with app.database.transaction() as tx:
        assert tx.create(Lead, title='c', source='exhibition').source == 'exhibition'
    with app.business.context(User(0, 'job', 'admin')) as context:
        assert context._tx.create(Lead, title='d', source='exhibition').source == 'exhibition'
    with pytest.raises(Exception):
        app.database.create(Lead, title='e', source='nowhere')


def test_a_required_instance_field_stays_required_and_shown(app):
    admin = client(app)
    assert admin('POST', '/api/_controls/fields/Customer', {'name': 'code', 'type': 'Text', 'label': 'Code', 'required': True})[0] == 201
    assert save(admin, ('field', 'Customer.x_code', {'hidden': True}))[1]['fields']['changes.0'] == "A required field can't be hidden. People couldn't fill it in."
    assert save(admin, ('field', 'Customer.x_code', {'required': False}))[1]['fields']['changes.0'].startswith('x_code is required on this instance.')
    assert field_of(schema_of(admin, 'customer'), 'x_code')['required'] is True


def test_trusted_sessions_date_names_in_the_instance_timezone(tmp_path, hashed):
    from evlet.database import today

    class Memo(Model):
        reference = Text(unique=True)

        class Meta:
            naming = Naming('reference', Series('M-{YYYY}{MM}{DD}-{###}'))

    app = build(tmp_path, Memo)
    for zone in ('Pacific/Kiritimati', 'Pacific/Pago_Pago'):
        app.business.timezone = zone
        with app.database.transaction() as tx:
            assert tx.create(Memo).reference.startswith('M-' + today(zone).replace('-', ''))


@pytest.mark.parametrize('when, message', [
    ('status == "snet"', "snet isn't an option of status. Use draft, sent."),
    ('posting_date >= "1/1/2026"', 'Write dates as year-month-day'),
    ('total > empty', 'Compare with empty using == or !=, like total != empty.'),
    ('status == "a"\x00', "A condition can't contain control characters."),
])
def test_condition_values_are_checked_against_the_field(app, when, message):
    series = [{'id': 'big', 'label': 'Big', 'format': 'BIG-{####}', 'when': when}, {'id': 'default', 'label': 'Inv', 'format': 'INV-{####}', 'when': ''}]
    assert save(client(app), ('naming', 'Invoice', series))[1]['fields']['changes.0'].startswith(message)


def test_a_format_counts_its_length_after_expansion(app):
    admin = client(app)
    series = [{'id': 'default', 'label': 'Inv', 'format': 'A-{customer}-{YYYY}-{MM}-{DD}-{########}-LONGER', 'when': ''}]
    assert save(admin, ('naming', 'Invoice', series))[0] == 200
    assert admin('POST', '/api/invoice', {'customer': 'Gulf', 'posting_date': '2026-03-04'})[1]['reference'] == 'A-Gulf-2026-03-04-00000001-LONGER'
    assert admin('POST', '/api/invoice', {'customer': 'x' * 20})[1]['fields'] == {'reference': 'This name would be longer than 40 characters'}
    assert admin('POST', '/api/invoice', {'customer': 'B', 'reference': None})[0] == 201


@pytest.mark.parametrize('key, value, message', [
    ('Invoice.status', {'options_hidden': ['draft']}, "draft is the default in models.py, so it can't be hidden."),
    ('Lead.value', {'required': True, 'read_only': True}, 'A field that is required and read only needs a default'),
])
def test_overrides_that_would_block_every_create_are_refused(app, key, value, message):
    assert message in save(client(app), ('field', key, value))[1]['fields']['changes.0']


def test_layouts_keep_section_ids_and_hide_unreadable_sections(app):
    admin, editor = client(app), client(app, 'editor')
    empty = [{'title': 'One', 'fields': []}, {'title': 'Two', 'fields': [{'name': 'title', 'width': 12}]}]
    assert save(admin, ('layout', 'Lead', empty))[1]['fields']['changes.0'].startswith('Every section needs at least one field')
    assert save(admin, ('perm', 'editor|Lead', {'ops': ['read'], 'read': ['title'], 'write': None, 'scope': None}),
                ('field', 'Lead.notes', {'label': 'Secret label'}),
                ('layout', 'Lead', [{'title': 'Secret section', 'fields': [{'name': 'notes', 'width': 12}]}, {'title': 'Main', 'fields': [{'name': 'title', 'width': 6}]}]),
                ('list', 'Lead', {'columns': ['notes', 'title'], 'page_size': 25, 'sort': '-notes'}))[0] == 200
    seen = json.dumps(schema_of(editor, 'lead'))
    assert 'Secret' not in seen and '"notes"' not in seen


def test_people_meet_required_overrides_and_app_code_does_not(tmp_path, hashed):
    from evlet import Integer, action

    class Ticket(Model):
        title = Text(required=True)
        kind = Select(('bug', 'task', 'idea'), default='task')
        estimate = Integer()
        status = State(('open', 'done'), moves=(Move('open', 'done', 'Close'),))

        @action('Spawn')
        def spawn(self):
            self.context.create(Ticket, title='child', kind='idea')

    app = build(tmp_path, Ticket)
    add_user(app, 'admin', 'admin')
    admin = client(app)
    assert admin('POST', '/api/_controls/fields/Ticket', {'name': 'region', 'type': 'Text', 'label': 'Region', 'required': True})[0] == 201
    assert save(admin, ('field', 'Ticket.estimate', {'required': True}), ('field', 'Ticket.kind', {'options_hidden': ['idea']}))[0] == 200
    assert admin('POST', '/api/ticket', {'title': 'a'})[1]['fields'] == {'estimate': 'This field is required', 'x_region': 'This field is required'}
    status, record, headers = admin('POST', '/api/ticket', {'title': 'a', 'estimate': 1, 'x_region': 'n'})
    assert status == 201
    assert admin('POST', f'/api/ticket/{record["id"]}/actions/spawn', {}, headers={b'if-match': headers[b'etag']})[0] == 200


def test_instance_field_names_clash_ignoring_case_and_with_reference_columns(tmp_path, hashed):
    from evlet import Reference

    class Thing(Model):
        X_code = Text()

    class Note(Model):
        body = Text()
        x = Reference()

    app = build(tmp_path, Thing, Note)
    add_user(app, 'admin', 'admin')
    admin = client(app)
    assert admin('POST', '/api/_controls/fields/Thing', {'name': 'code', 'type': 'Text', 'label': 'Code'})[1]['fields']['name'] == 'Thing already has a field called x_code.'
    assert admin('POST', '/api/_controls/fields/Note', {'name': 'id', 'type': 'Integer', 'label': 'Id'})[1]['fields']['name'] == 'Note already has a field called x_id.'
    assert build(tmp_path, Thing, Note).registry.models['note'] is Note


def test_the_list_sort_is_the_default_order_on_the_server(app):
    admin, editor = client(app), client(app, 'editor')
    for value in ('3', '1', '2'):
        admin('POST', '/api/lead', {'title': 't' + value, 'value': value})
    assert save(admin, ('list', 'Lead', {'columns': ['title', 'value'], 'page_size': 25, 'sort': 'value'}))[0] == 200
    assert [item['title'] for item in editor('GET', '/api/lead')[1]['items']] == ['t1', 't2', 't3']
    assert save(admin, ('perm', 'editor|Lead', {'ops': ['read'], 'read': ['title'], 'write': None, 'scope': None}))[0] == 200
    assert [item['title'] for item in editor('GET', '/api/lead')[1]['items']] == ['t2', 't1', 't3']


def test_try_it_takes_draft_series_without_ids(app):
    result = client(app)('POST', '/api/_controls/naming/Invoice/try', {'values': {}, 'series': [{'format': 'A-{####}', 'when': ''}]})[1]
    assert (result['match'], result['name']) == ('series_1', 'A-0001')


def test_records_keep_their_instance_fields_outside_requests(app):
    admin = client(app)
    assert admin('POST', '/api/_controls/fields/Lead', {'name': 'region', 'type': 'Text', 'label': 'R'})[0] == 201
    Lead = model_of(app, 'Lead')
    with app.database.transaction() as tx:
        record = tx.create(Lead, title='t', x_region='north')
        with pytest.raises(AttributeError, match='Records are snapshots'):
            record.x_region = 'south'
        tx.update(Lead, record.id, x_region='south')
    assert record.x_region == 'north' and 'x_region' not in record.__dict__
    with app.database.transaction() as tx:
        stored = tx.get(Lead, record.id)
    assert stored.x_region == 'south' and stored.to_dict()['x_region'] == 'south'
    with pytest.raises(Exception, match='x_region is an instance field. Build the record inside database.transaction'):
        Lead(title='q', x_region='east')
    with app.database.activated():
        built = Lead(title='q', x_region='east')
    assert built.x_region == 'east'
    with app.database.transaction() as tx:
        assert tx.create(Lead, **built._values).x_region == 'east'


def test_jobs_and_event_pages_wait_for_a_schema_change(app):
    from evlet.auth import AccessError
    from evlet.jobs import JobQueue
    business, done = app.business, threading.Event()
    queue = JobQueue(app.database, [])
    queue.business, queue._execute = business, lambda claim, **options: done.set()

    def later():
        try:
            business.event_page({'method': 'GET', 'headers': []})
        except AccessError:
            queue.execute((1, 'lease'))
    with business.gate.exclusive():
        thread = threading.Thread(target=later)
        thread.start()
        assert not done.wait(0.2)
    thread.join(2)
    assert done.is_set()


# ---------- reconciliation with the model engine ----------

def test_a_current_user_default_cannot_be_replaced(tmp_path, hashed):
    from evlet import CurrentUser
    from test_rules_types import ALL

    class Visit(Model):
        permissions = ALL
        title = Text()
        host = Text(default=CurrentUser)

    app = build(tmp_path, Visit)
    add_user(app, 'admin', 'admin')
    admin = client(app)
    status, payload, _ = save(admin, ('field', 'Visit.host', {'default': 'someone'}))
    assert status == 422 and 'filled with the signed-in user' in payload['fields']['changes.0'], payload
    # Its label still changes, and new records still take the signed-in user.
    assert save(admin, ('field', 'Visit.host', {'label': 'Host'}))[0] == 200
    status, record, _ = admin('POST', '/api/visit', {'title': 'Call'})
    assert status == 201 and record['host'] == 'admin', record


def test_unplaced_fields_follow_a_file_layout_under_other_and_an_override_in_its_last_section(tmp_path, hashed):
    class Site(Model):
        title = Text(required=True)
        city = Text()

        class Meta:
            layout = (('Site', ('title', 'city')),)

    class Room(Model):
        title = Text(required=True)
        floor = Text()
        notes = LongText()

        class Meta:
            layout = (('Room', ('title',)),)

    class Desk(Model):
        title = Text(required=True)

    app = build(tmp_path, Site, Room, Desk)
    add_user(app, 'admin', 'admin')
    admin = client(app)
    for model in ('Site', 'Room', 'Desk'):
        assert admin('POST', f'/api/_controls/fields/{model}', {'name': 'zone', 'type': 'Text', 'label': 'Zone'})[0] == 201
    # A declared layout leaves unplaced fields to Other, joining the Other group it already has.
    assert [(group['label'], group['fields']) for group in schema_of(admin, 'site')['layout']] == [('Site', ['title', 'city']), ('Other', ['x_zone'])]
    assert [(group['label'], group['fields']) for group in schema_of(admin, 'room')['layout']] == [('Room', ['title']), ('Other', ['floor', 'notes', 'x_zone'])]
    # The default Details group takes every field.
    assert [(group['label'], group['fields']) for group in schema_of(admin, 'desk')['layout']] == [('Details', ['title', 'x_zone'])]
    # A Controls layout puts them in its last section.
    layout = [{'title': 'Where', 'fields': [{'name': 'city', 'width': 6}]}, {'title': 'Site', 'fields': [{'name': 'title', 'width': 6}]}]
    assert save(admin, ('layout', 'Site', layout))[0] == 200
    assert [(group['label'], group['fields']) for group in schema_of(admin, 'site')['layout']] == [('Where', ['city']), ('Site', ['title', 'x_zone'])]


def test_recompute_keeps_instance_fields_and_skips_posted_records(tmp_path, hashed):
    from evlet import AddIndex, Migration, Recompute
    from test_effects import etag, move, new_bill, posting_models
    path, base = tmp_path / 'db.sqlite3', Migration('0000_base', AddIndex('bill', 'memo'))
    database = Database(path, Registry(*posting_models(1)), migrations=[base])
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    app.initialize()
    add_user(app, 'admin', 'admin')
    admin = client(app)
    assert admin('POST', '/api/_controls/fields/Bill', {'name': 'region', 'type': 'Text', 'label': 'Region'})[0] == 201
    customer = admin('POST', '/api/customer', {})[1]['id']
    draft, issued = new_bill(admin, customer), new_bill(admin, customer)
    for url in (draft, issued):
        status, body, _ = admin('PATCH', url, {'x_region': 'north'}, headers=etag(admin, url))
        assert status == 200 and body['x_region'] == 'north', body
    assert move(admin, issued, 'issued')[0] == 200
    app.database.close()
    # Instance columns are not part of the app schema, so the migration check passes and Recompute leaves them.
    steps = [base, Migration('0001_recompute', Recompute('bill_lines', ('amount',)), Recompute('bill', ('fee', 'total', 'charged')))]
    database = Database(path, Registry(*posting_models(2)), migrations=steps)
    applied = database.migrate(database.migrations)
    assert applied == ['0001_recompute'] and applied.skipped == 1
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    app.initialize()
    admin = client(app)
    assert [(admin('GET', url)[1]['total'], admin('GET', url)[1]['x_region']) for url in (draft, issued)] == [('6.00', 'north'), ('3.00', 'north')]
    # A locked, posted record refuses an ordinary write to an instance field like any other field.
    status, body, _ = admin('PATCH', issued, {'x_region': 'south'}, headers=etag(admin, issued))
    assert status in (409, 422) and admin('GET', issued)[1]['x_region'] == 'north', body


def test_instance_fields_follow_the_record_for_roles_whose_rules_list_fields(tmp_path, hashed):
    """An instance field is readable by every role that reads the record and writable by every role that creates or
    updates it, also when the app lists the fields a role may read or write. Overrides narrow it per role."""
    from evlet import Policy, Rule

    class Deal(Model):
        title = Text(required=True)
        amount = Decimal(scale=2)
        notes = Text()
    registry = Registry(Deal)
    database = Database(tmp_path / 'deal.sqlite3', registry)
    rules = {('editor', Deal): Rule(('read', 'create', 'update'), read_fields=('title', 'amount'), write_fields=('title',)),
             ('viewer', Deal): Rule(('read',), read_fields=('title',)),
             ('portal', Deal): Rule(('read',), read_fields=('title',))}
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False),
                      policy=Policy(database.registry, rules=rules))
    app.initialize()
    for role in ('admin', 'editor', 'viewer'):
        add_user(app, role, role)
    admin, editor, viewer = client(app), client(app, 'editor'), client(app, 'viewer')
    assert admin('POST', '/api/_controls/fields/Deal', {'name': 'region', 'type': 'Text', 'label': 'Region'})[0] == 201
    status, created, _ = admin('POST', '/api/deal', {'title': 'Payroll', 'amount': '10.00', 'notes': 'n', 'x_region': 'dxb'})
    assert status == 201
    deal = f"/api/deal/{created['id']}"
    # Read: the record, the list, the generated screen's schema, an API key and the local MCP connector.
    seen = viewer('GET', deal)[1]
    assert seen['x_region'] == 'dxb' and 'amount' not in seen and 'notes' not in seen
    assert viewer('GET', '/api/deal')[1]['items'][0]['x_region'] == 'dxb'
    assert field_of(schema_of(viewer, 'deal'), 'x_region')['label'] == 'Region'
    assert field_of(schema_of(editor, 'deal'), 'x_region')['read_only'] is False and field_of(schema_of(editor, 'deal'), 'amount')['read_only'] is True
    viewer_id = next(row['id'] for row in admin('GET', '/api/_controls/users')[1]['items'] if row['username'] == 'viewer')
    _, key = app.business.auth.create_key(viewer_id, 'Script', {'access': 'read', 'models': None})
    assert http(app, 'GET', deal, headers={b'authorization': f'Bearer {key}'.encode(), b'x-evlet-request': b''})[1]['x_region'] == 'dxb'
    with app.database.transaction() as tx:
        # The local MCP connector acts for people with Allow MCP.
        tx.connection.execute("UPDATE evlet_users SET allow_mcp=1 WHERE username='viewer'")
    assert viewer('GET', deal, headers={b'x-evlet-client': b'mcp'})[1]['x_region'] == 'dxb'
    # Write: a role that updates the record writes the field; fields the app keeps from it stay closed.
    assert editor('PATCH', deal, {'x_region': 'auh'})[0] == 200
    assert editor('GET', deal)[1]['x_region'] == 'auh'
    assert editor('PATCH', deal, {'amount': '1.00'})[0] in (403, 422)
    assert viewer('PATCH', deal, {'x_region': 'shj'})[0] == 403
    # The override page shows the field in the file value, so an admin can hide it or make it read only per role.
    overrides = {item['key']: item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'perm'}
    assert overrides['viewer|Deal']['file']['read'] == ['title', 'x_region']
    assert overrides['editor|Deal']['file']['write'] == ['title', 'x_region']
    status, data, _ = save(admin, ('perm', 'viewer|Deal', {'ops': ['read'], 'read': ['title'], 'write': None, 'scope': None}),
                           ('perm', 'editor|Deal', {'ops': ['read', 'create', 'update'], 'read': ['title', 'amount', 'x_region'], 'write': ['title'], 'scope': None}))
    assert status == 200, data
    assert 'x_region' not in viewer('GET', deal)[1] and 'x_region' not in viewer('GET', '/api/deal')[1]['items'][0]
    assert not any(field['name'] == 'x_region' for field in schema_of(viewer, 'deal')['fields'])
    assert editor('GET', deal)[1]['x_region'] == 'auh' and editor('PATCH', deal, {'x_region': 'x'})[0] in (403, 422)
    # A field added later reaches roles whose overrides list fields too, until an admin unticks it there.
    assert admin('POST', '/api/_controls/fields/Deal', {'name': 'zone', 'type': 'Text', 'label': 'Zone'})[0] == 201
    assert admin('PATCH', deal, {'x_zone': 'north'})[0] == 200
    assert viewer('GET', deal)[1]['x_zone'] == 'north' and 'x_region' not in viewer('GET', deal)[1]
    assert editor('PATCH', deal, {'x_zone': 'south'})[0] == 200
    # Customers in the portal get instance fields only when an admin grants them, and later fields stay hidden from them.
    from evlet.auth import User
    customer = User(99, 'buyer', 'portal')
    def fields(user):
        with app.database.activated():
            return app.business.policy.fields(user, Deal)
    assert fields(customer) == {'title'}
    assert overrides['portal|Deal']['file']['read'] == ['title']
    assert save(admin, ('perm', 'portal|Deal', {'ops': ['read'], 'read': ['title', 'x_region'], 'write': None, 'scope': None}))[0] == 200
    assert fields(customer) == {'title', 'x_region'}
    assert admin('POST', '/api/_controls/fields/Deal', {'name': 'cost', 'type': 'Text', 'label': 'Internal cost'})[0] == 201
    assert fields(customer) == {'title', 'x_region'}
    assert 'x_cost' in fields(User(98, 'viewer', 'viewer'))
    # Fields from the app file stay stricter only.
    status, data, _ = save(admin, ('perm', 'viewer|Deal', {'ops': ['read'], 'read': ['title', 'amount'], 'write': None, 'scope': None}))
    assert status == 422 and 'can only hide more' in json.dumps(data)
    status, data, _ = save(admin, ('perm', 'editor|Deal', {'ops': ['read', 'update'], 'read': ['title', 'amount'], 'write': ['amount'], 'scope': None}))
    assert status == 422 and 'can only allow fewer' in json.dumps(data)
    app.database.close()
