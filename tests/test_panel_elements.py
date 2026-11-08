"""What a record shows when a list opens it in its side panel: panel=False flags, Meta.panel and the Controls toggles.

Display only: hiding never changes what a role may read or write."""

import pytest

from evlet import Child, LongText, Model, Registry, Section, Table, Text
from evlet.ui.metadata import ui_schema, view_for
from test_business import client, hashed  # noqa: F401
from test_controls import add_user, password, save  # noqa: F401
from test_customization import build, models, schema_of
from test_rules_types import ALL


class Line(Child):
    item = Text(required=True)


def flagged():
    class Ticket(Model):
        permissions = ALL
        subject = Text(required=True)
        owner = Text()
        notes = LongText(panel=False)
        audit_note = Text()
        lines = Table(Line, panel=False)

        class Meta:
            open_in = 'panel'
            layout = (Section('Ticket', ('subject', 'owner', 'notes')), Section('Internal', ('audit_note',), panel=False))
    return Ticket


def listed():
    class Ticket(Model):
        permissions = ALL
        subject = Text(required=True)
        owner = Text()
        notes = LongText(panel=False)
        audit_note = Text()
        lines = Table(Line)

        class Meta:
            open_in = 'panel'
            layout = (Section('Ticket', ('subject', 'owner', 'notes')), Section('Internal', ('audit_note',)))
            panel = ('owner', 'activity', 'files', 'Internal')
    return Ticket


def test_flags_hide_sections_fields_and_tables_in_the_side_panel():
    view = view_for(flagged())
    assert view['panel'] == {'sections': ['Internal'], 'fields': ['notes'], 'tabs': ['lines']}
    # The layout itself is unchanged: the full page shows everything.
    assert [group['label'] for group in view['layout']] == ['Ticket', 'Internal']
    assert all('panel' not in group for group in view['layout'])


def test_a_model_without_flags_has_no_panel_key():
    class Plain(Model):
        subject = Text()
    assert 'panel' not in view_for(Plain)


def test_meta_panel_keeps_what_it_names_in_order_and_wins_over_flags():
    view = view_for(listed())
    # notes has panel=False and is not named; owner is named, so Ticket shows with only owner (subject is
    # hidden here, and the record page still shows a required field while it is empty on a new record).
    assert view['panel'] == {'fields': ['notes', 'subject'], 'tabs': ['lines', 'postings', 'related', 'revisions'],
                             'blocks': ['info', 'key_fields', 'summary'], 'order': ['details', 'activity', 'files']}


def test_meta_panel_details_keeps_every_section():
    class Ticket(Model):
        subject = Text()
        notes = LongText(panel=False)

        class Meta:
            panel = ('details',)
    assert view_for(Ticket)['panel'] == {'tabs': ['activity', 'postings', 'related', 'revisions'],
                                         'blocks': ['files', 'info', 'key_fields', 'summary'], 'order': ['details']}


@pytest.mark.parametrize('value, message', [
    (('nothing',), 'names unknown elements: nothing'),
    ('owner', 'list what the side panel shows once each'),
    (('owner', 'owner'), 'list what the side panel shows once each'),
    ((1,), 'list what the side panel shows once each'),
])
def test_meta_panel_names_are_checked_when_the_model_is_defined(value, message):
    class Ticket(Model):
        subject = Text()
        owner = Text()

        class Meta:
            panel = value
    with pytest.raises(ValueError, match=message):
        Registry(Ticket)


@pytest.mark.parametrize('make', [lambda: Text(panel='no'), lambda: Table(Line, panel=0),
                                  lambda: Section('A', ('x',), panel=None).check('M')])
def test_panel_flags_must_be_booleans(make):
    with pytest.raises(ValueError, match='panel must be True or False'):
        make()


def test_a_field_named_like_a_block_is_the_field():
    class Ticket(Model):
        subject = Text()
        info = Text()

        class Meta:
            panel = ('info',)
    panel = view_for(Ticket)['panel']
    assert 'info' in panel['blocks'] and panel['fields'] == ['subject']


def test_the_schema_carries_panel_only_when_used(tmp_path):
    registry = Registry(flagged())
    entry = ui_schema(registry)['models'][0]
    assert entry['panel'] == {'sections': ['Internal'], 'fields': ['notes'], 'tabs': ['lines']}
    plain = Registry(*models())
    assert all('panel' not in entry for entry in ui_schema(plain)['models'])


def test_hiding_is_display_only(tmp_path, hashed):
    app = build(tmp_path, flagged())
    add_user(app, 'editor', 'editor')
    editor = client(app, 'editor')
    entry = schema_of(editor, 'ticket')
    assert entry['panel']['fields'] == ['notes']
    # The hidden field is still in the schema, readable and writable by the same rules as before.
    assert 'notes' in [field['name'] for field in entry['fields']]
    status, record = editor('POST', '/api/ticket', {'subject': 'A', 'notes': 'kept', 'audit_note': 'x'})[:2]
    assert status == 201 and record['notes'] == 'kept' and record['audit_note'] == 'x'


@pytest.fixture
def app(tmp_path, hashed):
    app = build(tmp_path, *models())
    for role in ('admin', 'editor'):
        add_user(app, role, role)
    return app


SECTIONS = [{'title': 'Lead', 'fields': [{'name': 'title', 'width': 6}, {'name': 'source', 'width': 6}, {'name': 'value', 'width': 6}]},
            {'title': 'Notes', 'fields': [{'name': 'notes', 'width': 12}]}]


def test_controls_hides_elements_in_the_side_panel(app):
    admin = client(app)
    value = {'sections': SECTIONS, 'panel': {'sections': ['Notes'], 'fields': ['value'], 'tabs': ['activity'], 'blocks': ['summary', 'info']}}
    assert save(admin, ('layout', 'Lead', value))[0] == 200
    entry = schema_of(admin, 'lead')
    assert entry['panel'] == {'sections': ['Notes'], 'fields': ['value'], 'tabs': ['activity'], 'blocks': ['info', 'summary']}
    saved = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'layout' and item['key'] == 'Lead')
    assert saved['value']['panel'] == {'sections': ['Notes'], 'fields': ['value'], 'tabs': ['activity'], 'blocks': ['info', 'summary']}
    # An empty object shows everything again.
    assert save(admin, ('layout', 'Lead', {'sections': SECTIONS, 'panel': {}}))[0] == 200
    assert 'panel' not in schema_of(admin, 'lead')


def test_controls_panel_overrides_the_app_and_keeps_its_order(tmp_path, hashed):
    app = build(tmp_path, listed())
    add_user(app, 'admin', 'admin')
    admin = client(app)
    file = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'layout' and item['key'] == 'Ticket')
    # The toggles start from what the app hides.
    assert file['file']['panel']['fields'] == ['notes', 'subject']
    sections = file['file']['sections']
    assert save(admin, ('layout', 'Ticket', {'sections': sections, 'panel': {'tabs': ['postings']}}))[0] == 200
    assert schema_of(admin, 'ticket')['panel'] == {'tabs': ['postings'], 'order': ['details', 'activity', 'files']}


@pytest.mark.parametrize('panel, message', [
    ({'fields': ['nope']}, 'Hide fields of Lead in the side panel once each'),
    ({'sections': ['Elsewhere']}, 'Hide sections of Lead in the side panel once each'),
    ({'tabs': ['details']}, 'Hide tabs of Lead in the side panel once each'),
    ({'blocks': ['related']}, 'Hide blocks of Lead in the side panel once each'),
    ({'fields': ['value', 'value']}, 'Hide fields of Lead in the side panel once each'),
    ({'other': []}, 'Send what the side panel hides as sections, fields, tabs, blocks'),
    (['value'], 'Send what the side panel hides as'),
])
def test_controls_panel_values_are_checked(app, panel, message):
    admin = client(app)
    assert message in save(admin, ('layout', 'Lead', {'sections': SECTIONS, 'panel': panel}))[1]['fields']['changes.0']


def test_only_controls_admins_change_the_side_panel(app):
    editor = client(app, 'editor')
    assert save(editor, ('layout', 'Lead', {'sections': SECTIONS, 'panel': {'fields': ['value']}}))[0] in (403, 404)
    assert 'panel' not in schema_of(client(app), 'lead')


def test_the_panel_names_only_fields_the_role_can_read(app):
    admin = client(app)
    assert save(admin, ('layout', 'Lead', {'sections': SECTIONS, 'panel': {'fields': ['value', 'notes']}}),
                ('perm', 'editor|Lead', {'ops': ['read'], 'read': ['title', 'source', 'notes'], 'write': None, 'scope': None}))[0] == 200
    assert schema_of(client(app, 'editor'), 'lead')['panel'] == {'fields': ['notes']}


def test_removing_a_field_drops_it_from_the_side_panel(app):
    admin = client(app)
    assert admin('POST', '/api/_controls/fields/Lead', {'name': 'region', 'type': 'Text', 'label': 'Region'})[0] == 201
    sections = [SECTIONS[0], {'title': 'Extra', 'fields': [{'name': 'x_region', 'width': 6}]}]
    assert save(admin, ('layout', 'Lead', {'sections': sections, 'panel': {'sections': ['Extra'], 'fields': ['x_region', 'value']}}))[0] == 200
    assert admin('DELETE', '/api/_controls/fields/Lead/x_region')[0] == 200
    saved = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'layout' and item['key'] == 'Lead')['value']
    assert saved == {'sections': [SECTIONS[0]], 'panel': {'fields': ['value']}}
    assert admin('GET', '/api/_controls/state')[1]['attention'] == []


def test_the_export_notes_what_the_side_panel_hides(app):
    admin = client(app)
    assert save(admin, ('layout', 'Lead', {'sections': SECTIONS, 'panel': {'fields': ['value']}}))[0] == 200
    code = '\n'.join(item['code'] for item in admin('GET', '/api/_controls/overrides/export')[1])
    assert "# Side panel hides (panel=False or Meta.panel): {'fields': ['value']}" in code


def test_meta_panel_hides_an_application_child_table_it_does_not_name(tmp_path, hashed):
    from evlet import Application, ChildTable, Database, Link, LocalAuth

    class Order(Model):
        reference = Text()

        class Meta:
            panel = ('reference',)

    class OrderNote(Model):
        order = Link(Order, required=True)
        text = Text()

    database = Database(tmp_path / 'notes.sqlite3', Registry(Order, OrderNote))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False),
                      children=(ChildTable(Order, 'notes', OrderNote, 'order', 'Notes'),))
    app.initialize()
    add_user(app, 'admin', 'admin')
    entry = schema_of(client(app), 'order')
    assert [child['name'] for child in entry['children']] == ['notes']
    assert 'notes' in entry['panel']['tabs']
