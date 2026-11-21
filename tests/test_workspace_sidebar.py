"""The sidebar's sections, parents, Home and badges (evlet.workspaces), on real SQLite: declarations checked when
written, the schema each role gets, and the bounded badge counts of GET /api/_nav/badges."""

import pytest

from evlet import (Application, Database, Decimal, LocalAuth, Model, NavGroup, NavSection, Registry, Rule, Select,
                   Shortcut, Text, Workspace)
from evlet.workspaces import BADGE_CAP, BADGE_WINDOW
from test_business import PASSWORD, client, http

EVERYTHING = Rule(('read', 'create', 'update', 'delete'))


class Ticket(Model):
    # The viewer reads only southern tickets (a row scope) and can't read or filter by the priority.
    permissions = {'admin': EVERYTHING, 'editor': EVERYTHING,
                   'viewer': Rule(('read',), read_fields=('title', 'region', 'status'), scope=lambda user: {'region': 'south'})}
    title = Text(required=True)
    region = Select(('north', 'south'), default='north')
    status = Select(('open', 'closed'), default='open')
    priority = Select(('low', 'high'), default='low')


class Invoice(Model):
    permissions = {'admin': EVERYTHING, 'editor': Rule(()), 'viewer': Rule(())}
    number = Text(required=True)
    amount = Decimal(scale=2, default='0')


DESK = Workspace('Desk', icon='inbox', home=True, groups=(
    NavSection('Build', items=(
        NavGroup('Support', icon='life-buoy', color='teal', items=(
            Shortcut('ticket', icon='ticket', label='All tickets'),
            Shortcut('ticket', filters={'status': 'open'}, label='Open tickets', badge={'region': 'south'}),
            Shortcut('ticket', filters={'status': 'open'}, label='Urgent', badge={'priority': 'high'}),
        )),
        Shortcut('invoice', icon='receipt', color='amber'),
    )),
    NavSection('Account', items=(
        Shortcut(url='https://docs.example.com', label='Docs', icon='file-text', color='blue'),
        Shortcut(url='/reports', label='Reports', new_tab=True),
    )),
    NavGroup('Loose', items=(Shortcut(url='/reports?x=1', label='Same tab'),)),
))


def build(database, workspaces=(DESK,)):
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth, workspaces=workspaces)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    for role in ('editor', 'viewer'):
        assert admin('POST', '/api/auth/users', {'username': role, 'password': PASSWORD, 'role': role})[0] == 201
    return app


@pytest.fixture
def database(tmp_path):
    return Database(tmp_path / 'sidebar.sqlite3', Registry(Ticket, Invoice))


@pytest.fixture
def app(database):
    return build(database)


def desk(app, role='admin'):
    status, schema, _ = client(app, role)('GET', '/api/schema')
    assert status == 200
    return next(workspace for workspace in schema['workspaces'] if workspace['key'] == 'desk')


def badges(app, role='admin', workspace='desk'):
    return client(app, role)('GET', f'/api/_nav/badges?workspace={workspace}')


def add_tickets(database, count, **values):
    with database.transaction() as tx:
        for index in range(count):
            tx.create(Ticket, title=f'Ticket {index}', **values)


def test_sections_parents_links_and_home_in_the_schema(app):
    found = desk(app)
    assert found['home'] == {'key': 'home', 'kind': 'home', 'label': 'Home', 'icon': None}
    build_section, account = {'key': 'build', 'label': 'Build'}, {'key': 'account', 'label': 'Account'}
    support, invoices, docs, reports, loose = found['groups']
    assert {key: support[key] for key in ('key', 'label', 'icon', 'color', 'section', 'link')} == {
        'key': 'support', 'label': 'Support', 'icon': 'life-buoy', 'color': 'teal', 'section': build_section, 'link': False}
    assert [(item['key'], item['icon'], item['badge']) for item in support['items']] == [
        ('model:ticket:all_tickets', 'ticket', False), ('model:ticket:open_tickets', None, True), ('model:ticket:urgent', None, True)]
    # A parent link is a group of its one item, labelled by it (the model's plural here).
    assert {key: invoices[key] for key in ('key', 'label', 'icon', 'color', 'section', 'link')} == {
        'key': 'model_invoice', 'label': 'Invoices', 'icon': 'receipt', 'color': 'amber', 'section': build_section, 'link': True}
    assert docs['link'] and docs['section'] == account and docs['color'] == 'blue'
    # An http(s) address opens in a new tab by default; an app path only when new_tab says so.
    assert docs['items'][0]['new_tab'] is True and reports['items'][0]['new_tab'] is True
    assert loose['section'] is None and loose['items'][0]['new_tab'] is False
    # The badge filter itself never reaches the browser.
    assert 'region' not in str(support['items'][1])


def test_each_role_sees_its_own_parents_and_badges(app):
    editor = desk(app, 'editor')
    # The editor can't read invoices: the parent link is left out, not shown empty.
    assert [group['key'] for group in editor['groups']] == ['support', 'url_docs', 'url_reports', 'loose']
    viewer = desk(app, 'viewer')
    # The viewer can't filter by priority: the Urgent item stays, without its badge.
    assert [(item['key'], item['badge']) for item in viewer['groups'][0]['items']] == [
        ('model:ticket:all_tickets', False), ('model:ticket:open_tickets', True), ('model:ticket:urgent', False)]
    assert viewer['home']['kind'] == 'home'


def test_badges_count_what_each_role_reads(app, database):
    add_tickets(database, 3, region='south', priority='high')
    add_tickets(database, 2, region='north', priority='high')
    add_tickets(database, 4, region='south', status='closed')
    assert badges(app)[:2] == (200, {'badges': {
        'model:ticket:open_tickets': {'count': 3, 'more': False, 'partial': False},
        'model:ticket:urgent': {'count': 5, 'more': False, 'partial': False}}})
    # The viewer's row scope (south only) applies, and the badge it can't filter by is not counted at all.
    assert badges(app, 'viewer')[1] == {'badges': {'model:ticket:open_tickets': {'count': 3, 'more': False, 'partial': False}}}


def test_badges_stop_at_the_cap_and_read_at_most_the_window(app, database):
    add_tickets(database, BADGE_CAP + 20, region='south')
    assert badges(app)[1]['badges']['model:ticket:open_tickets'] == {'count': BADGE_CAP, 'more': True, 'partial': False}
    # Older matches beyond the newest BADGE_WINDOW rows are never read: the count is what the window held.
    add_tickets(database, BADGE_WINDOW, region='north')
    assert badges(app)[1]['badges']['model:ticket:open_tickets'] == {'count': 0, 'more': False, 'partial': True}
    add_tickets(database, 7, region='south')
    assert badges(app)[1]['badges']['model:ticket:open_tickets'] == {'count': 7, 'more': False, 'partial': True}


def test_badges_check_the_request(app):
    assert badges(app, workspace='nothing')[0] == 404
    assert client(app)('GET', '/api/_nav/badges')[0] == 422
    assert client(app)('GET', '/api/_nav/badges?workspace=desk&x=1')[0] == 422
    assert client(app)('POST', '/api/_nav/badges?workspace=desk', {})[0] == 405
    assert http(app, 'GET', '/api/_nav/badges?workspace=desk')[0] == 401


def test_a_home_shortcut_opens_its_target(database):
    app = build(database, (Workspace('Desk', home=Shortcut('ticket', filters={'status': 'open'}, label='Open work'),
                                     groups=(NavGroup('Support', items=('ticket',)),)),))
    assert desk(app)['home'] == {'key': 'home', 'kind': 'model', 'label': 'Open work', 'model': 'ticket', 'view': 'list',
                                 'filters': [['status', 'eq', 'open']], 'icon': None, 'badge': False}


@pytest.mark.parametrize('build_workspace, message', [
    (lambda: Workspace('Home', groups=(NavGroup('A', items=('ticket',)),)), 'the key "home" is reserved'),
    (lambda: Workspace('Desk', groups=(NavSection('Home', items=('ticket',)),)), '"Home" is reserved'),
    (lambda: Workspace('Desk', groups=(NavGroup('Home', items=('ticket',)),)), '"Home" is reserved'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut(url='/', label='Home'),)),)), '"Home" is reserved'),
    (lambda: Workspace('Desk', groups=(NavSection('A', items=(NavSection('B', items=('ticket',)),)),)), 'not another NavSection'),
    (lambda: Workspace('Desk', groups=(NavSection('A', items=()),)), 'items takes one or more'),
    (lambda: Workspace('Desk', groups=(NavSection('A', items=('ticket',)), NavSection('a', items=('invoice',)))), "two sections share the key 'a'"),
    (lambda: Workspace('Desk', groups=(NavGroup('A', color='pink', items=('ticket',)),)), 'color is one of blue'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('ticket', color='blue'),)),)), "takes its parent's colour"),
    (lambda: Workspace('Desk', groups=(NavSection('S', items=(Shortcut('ticket', badge={'status': 'open'}),)),)), 'parents show no badge'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut(url='/x', label='X', badge={'a': 1}),)),)), 'it goes with a model'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('ticket', new_tab=True),)),)), 'new_tab goes with a url'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut(url='/x', label='X', new_tab='yes'),)),)), 'new_tab is True or False'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('ticket', icon='Bad Icon'),)),)), 'icon is an icon name'),
    (lambda: Workspace('Desk', home='', groups=(NavGroup('A', items=('ticket',)),)), 'name a model'),
    (lambda: Workspace('Desk', home=Shortcut('ticket', icon='x'), groups=(NavGroup('A', items=('ticket',)),)), 'Home has its own icon'),
])
def test_declarations_are_checked_when_written(build_workspace, message):
    with pytest.raises(ValueError, match=message):
        build_workspace()


def test_badge_filters_are_checked_against_the_model(database):
    workspace = Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('ticket', label='T', badge={'nothing': 1}),)),))
    with pytest.raises(ValueError, match="item Shortcut.*badge"):
        build(database, (workspace,))


def test_two_parent_links_to_one_target_are_refused(database):
    workspace = Workspace('Desk', groups=(NavSection('S', items=('ticket',)), NavSection('T', items=('ticket',))))
    with pytest.raises(ValueError, match="model:ticket"):
        build(database, (workspace,))


def test_no_model_may_be_named_home(tmp_path):
    class Home(Model):
        title = Text()

    with pytest.raises(ValueError, match='home is reserved'):
        Registry(Home)


def test_controls_may_not_add_a_group_or_workspace_keyed_home(app):
    workspaces = app.business.workspaces
    from evlet.models import ValidationError
    with pytest.raises(ValidationError) as caught:
        workspaces.check_override('desk', {'groups': [{'key': 'home', 'label': 'Mine', 'items': []}]})
    assert 'reserved' in str(caught.value.errors)
    with pytest.raises(ValidationError) as caught:
        workspaces.check_override('home', {'custom': True, 'label': 'Mine', 'groups': [{'key': 'a', 'label': 'A', 'items': []}]})
    assert 'reserved' in caught.value.errors['key']
