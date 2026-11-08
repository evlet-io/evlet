"""What the browser's Duplicate copies: Meta.duplicate on a model and copy=False on a field or Table.

Display only: a duplicate is a normal create, checked as any other; the options never reach storage."""

import pytest

from evlet import (AddField, Child, CreateModel, Database, Migration, Model, Registry, Sequence, State, Table, Text)
from evlet.controls_export import field_line
from evlet.migrations import field_code, suggest
from evlet.ui.metadata import ui_schema
from test_business import client, hashed  # noqa: F401
from test_controls import add_user, password  # noqa: F401
from test_customization import build, field_of, schema_of
from test_rules_types import ALL


def classes(*, copy_options=True, meta=True):
    """A Ticket with copy=False on a field and a Table, and Meta.duplicate = False; without them when asked."""
    extra = {'copy': False} if copy_options else {}

    class Line(Child):
        item = Text(required=True)
        note = Text(**extra)

    class Ticket(Model):
        permissions = ALL
        subject = Text(required=True)
        reference = Text(max_length=40, **extra)
        lines = Table(Line, **extra)
        if meta:
            class Meta:
                duplicate = False

    return (Ticket,)


@pytest.mark.parametrize('make', [lambda: Text(copy='no'), lambda: Text(copy=0), lambda: Text(copy=None),
                                  lambda: State(('draft', 'done'), copy=1), lambda: Sequence('T-{n:04}', copy='false')])
def test_field_copy_must_be_a_boolean(make):
    with pytest.raises(ValueError, match='copy must be True or False'):
        make()


@pytest.mark.parametrize('value', ['no', 0, None])
def test_table_copy_must_be_a_boolean(value):
    class Line(Child):
        item = Text()
    with pytest.raises(ValueError, match='Table copy must be True or False'):
        Table(Line, copy=value)


@pytest.mark.parametrize('value', ['no', 0, None, 1])
def test_meta_duplicate_must_be_a_boolean_when_the_registry_is_built(value):
    class Ticket(Model):
        subject = Text()

        class Meta:
            duplicate = value
    with pytest.raises(ValueError, match=rf'Ticket.Meta.duplicate must be True or False, not {value!r}'):
        Registry(Ticket)


def test_every_field_kind_and_table_accepts_copy():
    assert Text(copy=False).copy is False and Text().copy is True
    assert State(('draft', 'done'), copy=False).copy is False
    assert Sequence('T-{n:04}', copy=False).copy is False

    class Line(Child):
        item = Text()
    assert Table(Line, copy=False).copy is False and Table(Line).copy is True


def test_the_schema_carries_the_options_only_when_they_are_off():
    entry = next(item for item in ui_schema(Registry(*classes()))['models'] if item['name'] == 'ticket')
    assert entry['duplicate'] is False
    assert field_of(entry, 'reference')['copy'] is False
    assert 'copy' not in field_of(entry, 'subject')
    plain = next(item for item in ui_schema(Registry(*classes(copy_options=False, meta=False)))['models'] if item['name'] == 'ticket')
    assert 'duplicate' not in plain and all('copy' not in field for field in plain['fields'])

    class Explicit(Model):
        subject = Text(copy=True)

        class Meta:
            duplicate = True
    entry = ui_schema(Registry(Explicit))['models'][0]
    assert 'duplicate' not in entry and 'copy' not in field_of(entry, 'subject')


def test_api_schema_shows_the_options_and_create_permission_per_role(tmp_path, hashed):
    app = build(tmp_path, *classes())
    for role in ('editor', 'viewer'):
        add_user(app, role, role)
    editor, viewer = client(app, 'editor'), client(app, 'viewer')
    entry = schema_of(editor, 'ticket')
    assert entry['duplicate'] is False
    assert field_of(entry, 'reference')['copy'] is False and 'copy' not in field_of(entry, 'subject')
    lines = next(child for child in entry['children'] if child['name'] == 'lines')
    assert lines['copy'] is False
    line = schema_of(editor, lines['model'])
    assert field_of(line, 'note')['copy'] is False and 'copy' not in field_of(line, 'item')
    # The browser shows Duplicate only to roles that may create: the schema already says so.
    assert entry['permissions']['create'] is True and schema_of(viewer, 'ticket')['permissions']['create'] is False
    # A duplicate is an ordinary create: the server copies nothing and checks it as any other.
    status, record = editor('POST', '/api/ticket', {'subject': 'Copied', 'reference': 'R-1'})[:2]
    assert status == 201 and record['reference'] == 'R-1'
    assert viewer('POST', '/api/ticket', {'subject': 'No'})[0] == 403


def test_api_schema_leaves_the_options_out_by_default(tmp_path, hashed):
    app = build(tmp_path, *classes(copy_options=False, meta=False))
    add_user(app, 'admin', 'admin')
    entry = schema_of(client(app), 'ticket')
    assert 'duplicate' not in entry and all('copy' not in field for field in entry['fields'])
    assert all('copy' not in child for child in entry['children'])


def test_the_options_leave_the_saved_schema_and_migration_checksums_unchanged(tmp_path):
    plain, flagged = Registry(*classes(copy_options=False, meta=False)), Registry(*classes())
    assert plain.schema() == flagged.schema()
    # A database saved without the options opens with them: nothing to migrate.
    path = tmp_path / 'tickets.sqlite3'
    Database(path, plain, migrations=[]).initialize()
    Database(path, flagged, migrations=[]).initialize()
    plan = Database(path, flagged).plan([])
    assert plan.state == 'baseline' and not plan.differences and not plan.suggestion
    # A migration that adds or creates a field gets the same checksum with or without copy=False.
    added = [Migration('0002_reference', AddField('ticket', 'code', Text(**options))) for options in ({}, {'copy': False})]
    assert added[0].checksum() == added[1].checksum()
    created = [Migration('0001_ticket', CreateModel(classes(copy_options=flag, meta=flag)[0])) for flag in (False, True)]
    assert created[0].checksum() == created[1].checksum()


def test_frozen_migration_copies_leave_copy_out():
    code, _ = field_code(Text(max_length=40, copy=False))
    assert code == 'Text(max_length=40)'
    registry = Registry(*classes())
    written = suggest(registry, {'version': 1, 'models': []})
    assert 'copy' not in written and 'duplicate' not in written


def test_controls_export_keeps_copy_as_written():
    field = Text(max_length=40, copy=False)
    line = field_line('reference', 'Text', *field.arguments, {'label': 'Ref'})
    assert line == "    reference = Text(max_length=40, copy=False, label='Ref')"
