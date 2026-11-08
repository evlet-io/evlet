"""Site workspace overrides (Controls): every kind of change and reset, the audit log, who may change them, and
that permission filtering still applies after them. On real SQLite and PostgreSQL."""

import json
import sqlite3

import pytest

from evlet import Database, Registry
from evlet.controls import ensure_schema
from evlet.postgres import PostgreSQLDatabase
from test_business import PASSWORD, client
from test_postgres import pg_dsn  # noqa: F401 - fixture
from test_workspaces import MODELS, build, shape

ROUTE = '/api/_controls/workspaces'


@pytest.fixture(params=['sqlite', 'postgresql'])
def app(request, tmp_path):
    registry = Registry(*MODELS)
    if request.param == 'sqlite':
        database = Database(tmp_path / 'overrides.sqlite3', registry)
    else:
        database = PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry)
    app = build(database)
    admin = client(app)
    # planner: an editor-based role that may customize workspaces; clerk: the same role without it.
    for role in ('planner', 'clerk'):
        assert admin('POST', '/api/_controls/roles', {'id': role, 'base': 'editor'})[0] == 201
        assert admin('POST', '/api/auth/users', {'username': role, 'password': PASSWORD, 'role': role})[0] == 201
    status, roles, _ = admin('PATCH', '/api/_controls/roles/planner', {'customize_workspaces': True})
    assert status == 200, roles
    assert {row['id']: row['customize_workspaces'] for row in roles['items']} == {
        'admin': True, 'editor': False, 'viewer': False, 'portal': False, 'clerk': False, 'planner': True}
    return app


def seen(app, role):
    return shape(client(app, role)('GET', '/api/schema')[1]['workspaces'])


def labels(app, role, key):
    [workspace] = [entry for entry in client(app, role)('GET', '/api/schema')[1]['workspaces'] if entry['key'] == key]
    return workspace['label'], {group['key']: (group['label'], [item['label'] for item in group['items']]) for group in workspace['groups']}


def test_reorder_hide_rename_and_add(app):
    admin = client(app)
    override = {'label': 'Sales desk', 'icon': 'handshake', 'groups': [
        {'key': 'deals', 'label': 'Pipeline', 'items': [
            {'key': 'model:deal:large_deals', 'label': 'Big deals'},
            {'key': 'model:client'},                                   # moved here from Clients
            {'key': 'slip:quick_deal', 'hidden': True},
            {'shortcut': {'model': 'deal', 'filters': {'stage': 'won'}, 'label': 'Won deals'}},
        ]},
        {'key': 'reports', 'hidden': True},
        {'key': 'later', 'label': 'Later', 'items': [{'shortcut': {'url': '/docs', 'label': 'Docs'}}]},
    ]}
    status, listing, _ = admin('PUT', ROUTE + '/sales', override)
    assert status == 200, listing
    # Listed groups first in the override's order, then declared groups it leaves out; moved and hidden items
    # leave their own group; the rest of a group's declared items follow its listed ones.
    assert seen(app, 'admin')['sales'] == {
        'deals': ['model:deal:large_deals', 'model:client', 'model:deal:won_deals', 'model:deal', 'model:deal:board'],
        'later': ['url:docs'],
        'clients': ['model:client:north_clients', 'model:client:vip_clients']}
    label, groups = labels(app, 'admin', 'sales')
    assert label == 'Sales desk' and groups['deals'] == ('Pipeline', ['Big deals', 'Clients', 'Won deals', 'Deals', 'Deals'])
    [sales] = [item for item in listing['items'] if item['key'] == 'sales']
    assert sales['override'] == override and sales['changed_by'] == 'admin' and sales['source'] == 'declared'
    assert shape([sales['effective']]) == {'sales': seen(app, 'admin')['sales']} and sales['effective']['icon'] == 'handshake'
    assert 'reports' in shape([sales['declared']])['sales']
    # Reset goes back to the declared workspace.
    assert admin('DELETE', ROUTE + '/sales')[0] == 200
    assert seen(app, 'admin')['sales']['reports'] == ['report:ClientList', 'report:PointTotals', 'url:help']
    assert labels(app, 'admin', 'sales')[0] == 'Sales'


def test_custom_workspaces_and_the_automatic_one(tmp_path):
    app = build(Database(tmp_path / 'auto.sqlite3', Registry(*MODELS)), ())
    admin = client(app)
    assert admin('PUT', ROUTE + '/app', {'label': 'Everything', 'groups': [
        {'key': 'records', 'items': [{'key': 'model:payroll', 'label': 'Pay'}]}, {'key': 'reports', 'hidden': True}]})[0] == 200
    assert seen(app, 'admin') == {'app': {'records': ['model:payroll', 'model:client'], 'documents': ['model:deal']}}
    assert admin('PUT', ROUTE + '/desk', {'custom': True, 'label': 'My desk', 'icon': 'star', 'groups': [
        {'key': 'today', 'label': 'Today', 'items': [{'shortcut': {'model': 'deal', 'view': 'board'}},
                                                     {'shortcut': {'report': 'Overview'}}]}]})[0] == 200
    found = client(app)('GET', '/api/schema')[1]['workspaces']
    assert [(entry['key'], entry['source'], entry['app'], entry['label']) for entry in found] == [
        ('app', 'automatic', 'app', 'Everything'), ('desk', 'custom', None, 'My desk')]
    assert shape(found)['desk'] == {'today': ['model:deal:board', 'report:Overview']}
    assert admin('DELETE', ROUTE + '/desk')[0] == 200
    assert [entry['key'] for entry in client(app)('GET', '/api/schema')[1]['workspaces']] == ['app']
    assert admin('DELETE', ROUTE + '/desk')[0] == 404


def test_every_change_is_audited(app):
    admin = client(app)
    admin('PUT', ROUTE + '/sales', {'label': 'Sales desk'})
    admin('PUT', ROUTE + '/sales', {'label': 'Sales desk'})                 # unchanged: no row
    admin('PUT', ROUTE + '/mine', {'custom': True, 'label': 'Mine', 'groups': [{'key': 'a', 'label': 'A', 'items': [{'shortcut': {'model': 'client'}}]}]})
    admin('DELETE', ROUTE + '/sales')
    admin('DELETE', ROUTE + '/mine')
    rows = admin('GET', '/api/_controls/audit?area=Workspaces')[1]['items']
    assert [(row['target'], row['detail'], row['actor']) for row in reversed(rows)] == [
        ('sales', 'Changed', 'admin'), ('mine', 'Added', 'admin'), ('sales', 'Reset to the declared workspace', 'admin'), ('mine', 'Removed', 'admin')]
    with app.database._connection() as connection:
        before, after = connection.execute("SELECT before, after FROM evlet_audit WHERE area='Workspaces' ORDER BY id").fetchone()
    assert before is None and json.loads(after) == {'label': 'Sales desk'}
    # Changing the access flag is audited under Roles.
    roles = admin('GET', '/api/_controls/audit?area=Roles')[1]['items']
    assert any(row['target'] == 'planner' and row['detail'] == 'Customize workspaces: no → yes' for row in roles)


def test_who_may_change_workspaces(app):
    admin = client(app)
    # ops opens Controls but can't customize workspaces.
    assert admin('POST', '/api/_controls/roles', {'id': 'ops', 'base': 'editor'})[0] == 201
    assert admin('PATCH', '/api/_controls/roles/ops', {'opens_controls': True})[0] == 200
    assert admin('POST', '/api/auth/users', {'username': 'ops', 'password': PASSWORD, 'role': 'ops'})[0] == 201
    planner, clerk, editor, viewer, ops = (client(app, role) for role in ('planner', 'clerk', 'editor', 'viewer', 'ops'))
    body = {'label': 'Planned'}
    assert planner('GET', ROUTE)[0] == 200
    assert planner('PUT', ROUTE + '/sales', body)[0] == 200
    assert planner('DELETE', ROUTE + '/sales')[0] == 200
    requests = (('GET', ROUTE, None), ('PUT', ROUTE + '/sales', body), ('DELETE', ROUTE + '/sales', None),
                ('PUT', ROUTE + '/mine', {'custom': True, 'label': 'Mine', 'groups': []}), ('POST', ROUTE, body), ('GET', ROUTE + '/sales', None))
    # Without access the area stays hidden, like the rest of Controls.
    for refused in (clerk, editor, viewer):
        for method, path, data in requests:
            status, payload, _ = refused(method, path, data)
            assert status == 404 and payload == {'error': 'Route not found'}, (method, path, payload)
    # Someone who already opens Controls is told why.
    for method, path, data in requests:
        status, payload, _ = ops(method, path, data)
        assert status == 403 and 'Customize workspaces' in payload['error'], (method, path, payload)
    # The flag alone does not open the rest of Controls, and only an admin can give it.
    assert planner('GET', '/api/_controls/state')[0] == 404
    assert planner('PATCH', '/api/_controls/roles/clerk', {'customize_workspaces': True})[0] == 404
    assert admin('PATCH', '/api/_controls/roles/admin', {'customize_workspaces': False})[0] == 422
    assert admin('PATCH', '/api/_controls/roles/portal', {'customize_workspaces': True})[0] == 422
    assert admin('PATCH', '/api/_controls/roles/clerk', {'customize_workspaces': 'yes'})[0] == 422
    # Taking the flag away takes the routes away at once.
    assert admin('PATCH', '/api/_controls/roles/planner', {'customize_workspaces': False})[0] == 200
    assert planner('GET', ROUTE)[0] == 404
    # The generic overrides route does not take workspace changes.
    status, payload, _ = admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'workspace', 'key': 'sales', 'value': body}]})
    assert status == 422 and payload['fields'] == {'changes.0': 'Choose a known kind of change.'}


def test_overrides_never_widen_access(app):
    admin = client(app)
    assert admin('PUT', ROUTE + '/sales', {'groups': [{'key': 'clients', 'items': [
        {'shortcut': {'model': 'payroll', 'label': 'Payroll here'}},
        {'shortcut': {'report': 'Overview', 'label': 'Overview here'}},
        {'shortcut': {'model': 'client', 'filters': {'notes': 'vip'}, 'label': 'Notes vip'}}]}]})[0] == 200
    assert admin('PUT', ROUTE + '/hr', {'label': 'People desk'})[0] == 200
    assert admin('PUT', ROUTE + '/payonly', {'custom': True, 'label': 'Pay', 'groups': [
        {'key': 'pay', 'label': 'Pay', 'items': [{'shortcut': {'model': 'payroll'}}]}]})[0] == 200
    assert seen(app, 'admin')['sales']['clients'][:3] == ['model:payroll:payroll_here', 'report:Overview', 'model:client:notes_vip']
    # Permission filtering runs after the override: a model, report or filter the role can't use stays hidden,
    # and so do the groups and workspaces left empty.
    assert seen(app, 'editor')['sales']['clients'] == ['report:Overview', 'model:client:notes_vip', 'model:client', 'model:client:north_clients', 'model:client:vip_clients']
    assert seen(app, 'viewer')['sales']['clients'] == ['model:client', 'model:client:north_clients']
    assert set(seen(app, 'viewer')) == set(seen(app, 'editor')) == {'sales'}
    assert set(seen(app, 'admin')) == {'sales', 'hr', 'payonly'}


@pytest.mark.parametrize('key, body, errors', [
    ('sales', {'title': 'x'}, {'_form': 'Use custom, label, icon and groups'}),
    ('nothing', {'label': 'X'}, {'key': 'No workspace has this key. Send "custom": true to add one'}),
    ('sales', {'custom': True, 'label': 'X', 'groups': [{'key': 'a', 'label': 'A'}]}, {'key': 'The workspace Sales has this key. Choose another key for a new workspace'}),
    ('mine', {'custom': True, 'label': 'X'}, {'_form': 'A new workspace needs a label and at least one group'}),
    ('sales', {'label': ''}, {'label': 'label must be text of 1 to 80 characters'}),
    ('sales', {'icon': 'Not An Icon'}, {'icon': 'icon is an icon name such as "receipt" (lowercase letters, digits and hyphens)'}),
    ('sales', {'groups': {}}, {'groups': 'Send a list of at most 50 groups'}),
    ('sales', {'groups': [{'label': 'A'}]}, {'groups.0': 'Send key, with label, icon, hidden or items'}),
    ('sales', {'groups': [{'key': 'deals'}, {'key': 'deals'}]}, {'groups.1.key': 'Name each group once'}),
    ('sales', {'groups': [{'key': 'new'}]}, {'groups.0.label': 'A new group needs a label'}),
    ('sales', {'groups': [{'key': 'bad key', 'label': 'B'}]}, {'groups.0.key': 'A new group key is 1 to 64 letters, digits or underscores'}),
    ('sales', {'groups': [{'key': 'deals', 'hidden': 'yes'}]}, {'groups.0.hidden': 'Use true or false'}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'key': 'model:payroll'}]}]}, {'groups.0.items.0.key': 'No item of this workspace has this key'}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'key': 'model:deal', 'shortcut': {}}]}]}, {'groups.0.items.0': 'Send key or shortcut, with label or hidden'}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'key': 'model:deal'}, {'key': 'model:deal'}]}]}, {'groups.0.items.1': 'Use each item once (model:deal)'}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'key': 'model:deal', 'label': 'x' * 81}]}]}, {'groups.0.items.0.label': 'label must be text of 1 to 80 characters'}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'shortcut': {'model': 'deal'}}]}]},
     {'groups.0.items.0.shortcut': 'An item of this workspace has the key model:deal; give the shortcut a different label'}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'shortcut': {'url': 'javascript:alert(1)', 'label': 'X'}}]}]},
     {'groups.0.items.0.shortcut': "Workspace 'sales', group 'deals', item Shortcut(label='X', url='javascript:alert(1)'): a URL is an app path starting with / or an http(s) address"}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'shortcut': {'model': 'secret', 'label': 'S'}}]}]},
     {'groups.0.items.0.shortcut': "Workspace 'sales', group 'deals', item Shortcut(model='secret', label='S'): Secret has no screens (a ledger model or Meta.api = None)"}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'shortcut': {'model': 'client', 'filters': {'missing': 1}, 'label': 'M'}}]}]},
     {'groups.0.items.0.shortcut': "Workspace 'sales', group 'deals', item Shortcut(model='client', filters={'missing': 1}, label='M'): filters missing: Unknown field: missing"}),
    ('sales', {'groups': [{'key': 'deals', 'items': [{'shortcut': {'model': 'client', 'colour': 'red'}}]}]},
     {'groups.0.items.0.shortcut': 'Send model, view, filters, label, report, slip or url, as text (filters as JSON)'}),
    ('mine', {'custom': True, 'label': 'X', 'groups': [{'key': 'a', 'label': 'A', 'items': [{'key': 'model:client'}]}]},
     {'groups.0.items.0.key': 'A new workspace holds shortcuts only'}),
])
def test_invalid_overrides_are_refused(app, key, body, errors):
    status, payload, _ = client(app)('PUT', ROUTE + '/' + key, body)
    assert status == 422 and payload['fields'] == errors
    with app.database._connection() as connection:
        assert connection.execute("SELECT count(*) FROM evlet_overrides WHERE kind='workspace'").fetchone()[0] == 0


def test_a_stored_override_that_no_longer_applies_fails_closed(tmp_path):
    app = build(Database(tmp_path / 'stale.sqlite3', Registry(*MODELS)))
    admin = client(app)
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_overrides(kind,key,value) VALUES ('workspace','sales',?)",
                              (json.dumps({'label': 'Old', 'groups': [{'key': 'deals', 'items': [{'key': 'model:gone'}]}]}),))
        tx.connection.execute('UPDATE evlet_config SET version=version+1')
    assert labels(app, 'admin', 'sales')[0] == 'Sales'
    listing = admin('GET', ROUTE)[1]
    assert listing['problems'] == [{'kind': 'workspace', 'key': 'sales', 'error': 'groups.0.items.0.key: No item of this workspace has this key. '
                                    'The workspace shows as declared until this is fixed or reset.'}]
    assert admin('DELETE', ROUTE + '/sales')[0] == 200 and admin('GET', ROUTE)[1]['problems'] == []


def test_a_table_made_before_workspaces_takes_their_kind(tmp_path):
    connection = sqlite3.connect(tmp_path / 'old.sqlite3', isolation_level=None)
    connection.execute("CREATE TABLE evlet_overrides (kind TEXT NOT NULL CHECK(kind IN ('perm','field','custom','layout','list','naming','setting')), "
                       'key TEXT NOT NULL, value TEXT NOT NULL, changed_by INTEGER, changed_at INTEGER, PRIMARY KEY (kind, key))')
    connection.execute("INSERT INTO evlet_overrides VALUES ('setting','security.lockout','4',1,2)")
    ensure_schema(connection)
    ensure_schema(connection)
    connection.execute("INSERT INTO evlet_overrides VALUES ('workspace','sales','{}',1,2)")
    assert connection.execute('SELECT kind, key, value, changed_by, changed_at FROM evlet_overrides ORDER BY kind').fetchall() == [
        ('setting', 'security.lockout', '4', 1, 2), ('workspace', 'sales', '{}', 1, 2)]
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO evlet_overrides VALUES ('other','x','{}',1,2)")


def test_a_postgresql_table_made_before_workspaces_takes_their_kind(pg_dsn):
    app = build(PostgreSQLDatabase(pg_dsn, Registry(*MODELS)))
    with app.database.transaction() as tx:
        tx.connection.execute('ALTER TABLE evlet_overrides DROP CONSTRAINT evlet_overrides_kind_check')
        tx.connection.execute("ALTER TABLE evlet_overrides ADD CONSTRAINT evlet_overrides_kind_check "
                              "CHECK(kind IN ('perm','field','custom','layout','list','naming','setting'))")
    with app.database.transaction() as tx:
        ensure_schema(tx.connection)
        ensure_schema(tx.connection)
        tx.connection.execute("INSERT INTO evlet_overrides(kind,key,value) VALUES ('workspace','sales','{}')")
    with pytest.raises(Exception, match='constraint was violated'):
        with app.database.transaction() as tx:
            tx.connection.execute("INSERT INTO evlet_overrides(kind,key,value) VALUES ('other','x','{}')")
