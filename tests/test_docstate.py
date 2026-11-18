"""The State field is always named docstate: other names fail at declaration, it shows as "Status", and
RenameField moves a renamed field's stored Controls overrides and workspace shortcuts with its column. On real SQLite."""

import json
import sqlite3

import pytest

from evlet import Child, Database, Model, Registry, Select, State, Text, framework
from evlet.controls import ensure_schema
from evlet.sqlite import SQLiteConnection
from test_business import client, hashed  # noqa: F401 (fixture)

STEPS = (State.Step('open'), State.Step('done'))


@pytest.mark.parametrize('name', ['status', 'stage', 'state', 'Docstate'])
def test_a_state_under_another_name_fails_when_the_class_is_defined(name):
    with pytest.raises(ValueError, match=rf'^Ticket\.{name} is a State: name the State field docstate '
                                         r'\(rename it with a migration; docs/states-actions.md\)$'):
        type('Ticket', (Model,), {name: State(*STEPS)})


@pytest.mark.parametrize('field', [Text(), Select(('open', 'done'))])
def test_docstate_is_only_for_the_state(field):
    with pytest.raises(ValueError, match=r'^Ticket\.docstate is reserved for the State field: declare docstate = State\(...\) '
                                         r'or rename the field'):
        type('Ticket', (Model,), {'docstate': field})


def test_a_row_model_follows_the_same_rule():
    with pytest.raises(ValueError, match=r'Line\.status is a State: name the State field docstate'):
        type('Line', (Child,), {'status': State(*STEPS)})
    with pytest.raises(ValueError, match=r'Line\.docstate is reserved for the State field'):
        type('Line', (Child,), {'docstate': Text()})


def test_the_state_shows_as_status_unless_it_has_a_label():
    plain = type('Ticket', (Model,), {'title': Text(), 'docstate': State(*STEPS)})
    named = type('Order', (Model,), {'title': Text(), 'docstate': State(*STEPS, label='Stage')})
    assert plain.__state__ == named.__state__ == 'docstate'
    assert plain.__declared_fields__['docstate'].label == 'Status'
    assert named.__declared_fields__['docstate'].label == 'Stage'
    labels = lambda model: {field['name']: field['label'] for field in Registry(model).ui_schema()['models'][0]['fields']}
    assert labels(plain)['docstate'] == 'Status' and labels(named)['docstate'] == 'Stage'


# Upgrading the Playground: 0021_project_docstate renames project.status with the settings that name it.

OVERRIDES = [
    ('field', 'Project.status', {'label': 'Stage', 'help_text': 'Where the work is'}),
    ('list', 'Project', {'columns': ['title', 'status', 'budget'], 'page_size': 25, 'sort': '-status',
                         'list_tabs': 'status', 'list_tab': 'done'}),
    ('layout', 'Project', {'sections': [{'title': 'Project', 'fields': [{'name': 'title', 'width': 6}, {'name': 'status', 'width': 6}]},
                                        {'title': 'Budget', 'fields': [{'name': 'budget', 'width': 6}]}],
                           'panel': {'fields': ['budget', 'status']}}),
    ('perm', 'viewer|Project', {'ops': ['read'], 'read': ['title', 'status', 'budget'], 'write': None, 'scope': None}),
    ('workspace', 'projects', {'groups': [{'key': 'projects', 'items': [
        {'shortcut': {'model': 'project', 'filters': {'status': 'done'}, 'label': 'Done projects'}},
        {'shortcut': {'model': 'project', 'filters': [['status', 'ne', 'planned'], ['budget', 'gt', '0']], 'label': 'Started with a budget'}},
        {'shortcut': {'model': 'invoice', 'filters': {'status': 'sent'}, 'label': 'Sent invoices'}}]}]}),
    # Invoice.status is a Select, not a State: it keeps its name.
    ('field', 'Invoice.status', {'label': 'Sending'}),
]


def stored(path):
    with sqlite3.connect(path) as connection:
        return {(kind, key): json.loads(value) for kind, key, value in connection.execute('SELECT kind, key, value FROM evlet_overrides')}


def test_the_playground_upgrade_moves_project_status_and_the_settings_that_name_it(tmp_path, monkeypatch, hashed):
    from examples.playground.app import create_app
    from examples.playground.migrate import legacy_registry, migrations
    from examples.playground.models import registry
    path = tmp_path / 'playground.sqlite3'
    old = Database(path, legacy_registry())
    old.initialize()
    old.close()
    # Controls settings an earlier release saved, naming the project's State by its old name.
    database = Database(path, registry)
    connection = sqlite3.connect(path, isolation_level=None, factory=SQLiteConnection)
    ensure_schema(connection)
    for kind, key, value in OVERRIDES:
        connection.execute('INSERT INTO evlet_overrides (kind, key, value, changed_by, changed_at) VALUES (?, ?, ?, 1, 1)',
                           (kind, key, json.dumps(value, sort_keys=True)))
    version = connection.execute('SELECT version FROM evlet_config').fetchone()[0]
    connection.close()

    assert '0021_project_docstate' in database.migrate(migrations)
    framework.upgrade(database)
    database.close()
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(project)')}
        assert 'docstate' in columns and 'status' not in columns
        assert connection.execute('SELECT version FROM evlet_config').fetchone()[0] == version + 1
    saved = stored(path)
    assert ('field', 'Project.status') not in saved and saved['field', 'Project.docstate'] == OVERRIDES[0][2]
    assert saved['list', 'Project'] == {'columns': ['title', 'docstate', 'budget'], 'page_size': 25, 'sort': '-docstate',
                                        'list_tabs': 'docstate', 'list_tab': 'done'}
    assert [item['name'] for item in saved['layout', 'Project']['sections'][0]['fields']] == ['title', 'docstate']
    assert saved['layout', 'Project']['panel'] == {'fields': ['budget', 'docstate']}
    assert saved['perm', 'viewer|Project']['read'] == ['title', 'docstate', 'budget']
    shortcuts = [item['shortcut'] for item in saved['workspace', 'projects']['groups'][0]['items']]
    assert [shortcut['filters'] for shortcut in shortcuts] == [
        {'docstate': 'done'}, [['docstate', 'ne', 'planned'], ['budget', 'gt', '0']], {'status': 'sent'}]
    assert saved['field', 'Invoice.status'] == {'label': 'Sending'}

    # The upgraded Playground starts with every setting in effect and none listed under Needs attention.
    monkeypatch.setenv('EVLET_DATABASE', str(path))
    app = create_app()
    app.database.initialize()
    app.business.initialize()
    with app.database.transaction() as tx:
        for role in ('admin', 'viewer'):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password',
                                  (role, hashed, role))
    admin = client(app)
    assert admin('GET', '/api/_controls/overrides')[1]['problems'] == []
    assert admin('GET', '/api/_controls/workspaces')[1]['problems'] == []
    project = next(model for model in admin('GET', '/api/schema')[1]['models'] if model['name'] == 'project')
    assert next(field for field in project['fields'] if field['name'] == 'docstate')['label'] == 'Stage'
    viewer = next(model for model in client(app, 'viewer')('GET', '/api/schema')[1]['models'] if model['name'] == 'project')
    assert {field['name'] for field in viewer['fields']} >= {'title', 'docstate', 'budget'}
    app.database.close()
