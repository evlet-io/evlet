"""Workspaces (evlet.workspaces): declarations checked at registration, and each role's view in /api/schema,
on real SQLite."""

import json

import sys

import pytest

from evlet import (Application, Child, Column, Database, Date, Decimal, Link, LocalAuth, Model, NavGroup, Registry,
                   Report, Rule, Select, Shortcut, Slip, State, Table, Text, Workspace)
from test_business import PASSWORD, client, http

EVERYTHING = Rule(('read', 'create', 'update', 'delete'))


class Client(Model):
    permissions = {'admin': EVERYTHING, 'editor': EVERYTHING, 'viewer': Rule(('read',), read_fields=('title', 'region'))}
    title = Text(required=True)
    region = Select(('north', 'south'), default='north')
    notes = Text(default='')


class Deal(Model):
    """A document: a State with a board and child rows."""
    permissions = {'admin': EVERYTHING, 'editor': EVERYTHING, 'viewer': Rule(('read',), read_fields=('title', 'client', 'amount'))}
    title = Text(required=True)
    client = Link(Client)
    amount = Decimal(scale=2, default='0')
    stage = State(
        State.Step('open'),
        State.Step('won'),
        State.Step('lost'),
        rules=(
            State.Rule('open', 'won', 'Win', key='won'),
            State.Rule('open', 'lost', 'Lose', key='lost'),
        ))

    class Line(Child):
        item = Text(required=True)

    lines = Table(Line)

    class Meta:
        board = 'stage'


class Payroll(Model):
    """Only admins read it."""
    permissions = {'admin': EVERYTHING, 'editor': Rule(()), 'viewer': Rule(())}
    person = Text(required=True)


class Secret(Model):
    class Meta:
        api = None
    code = Text(default='')


class PointMove(Model):
    client = Link(Client, required=True)
    on_date = Date(required=True)
    points = Decimal(scale=2, required=True)

    class Meta:
        ledger = True


class ClientList(Report):
    title = 'Client list'
    roles = ('admin', 'editor', 'viewer')
    model = Client
    group = 'Sales'
    columns = (Column('title'),)

    def run(self):
        return {'rows': [], 'total': 0}


class PointTotals(Report):
    title = 'Point totals'
    roles = ('admin',)
    model = PointMove
    columns = (Column('points', type='decimal'),)

    def run(self):
        return {'rows': [], 'total': 0}


class Overview(Report):
    title = 'Overview'
    roles = ('admin', 'editor')
    columns = (Column('label'),)

    def run(self):
        return {'rows': [], 'total': 0}


class QuickDeal(Slip):
    title = 'Quick deal'
    menu = True
    becomes = Deal
    title_text = Text(required=True)


class DealNote(Slip):
    source = Deal
    note = Text()

    def hand_in(self):
        return None


MODELS = (Client, Deal, Payroll, Secret, PointMove)
REPORTS = (ClientList, PointTotals, Overview)
SLIPS = (QuickDeal, DealNote)

SALES = Workspace('Sales', icon='receipt', groups=(
    NavGroup('Clients', icon='users', items=(
        'client',
        Shortcut(Client, filters={'region': 'north'}, label='North clients'),
        Shortcut('client', filters={'notes__icontains': 'vip'}, label='VIP clients'),
    )),
    NavGroup('Deals', items=(
        'deal',
        Shortcut('deal', view='board'),
        Shortcut('deal', filters=[['amount', 'gt', '1000']], label='Large deals'),
        Shortcut(slip=QuickDeal),
    )),
    NavGroup('Reports', items=(Shortcut(report=ClientList), Shortcut(report='PointTotals'), Shortcut(url='https://example.com/help', label='Help'))),
))
PEOPLE = Workspace('People', key='hr', groups=(NavGroup('Payroll', items=('payroll',)),))


def build(database, workspaces=(SALES, PEOPLE)):
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(database, auth=auth, reports=REPORTS, slips=SLIPS, workspaces=workspaces)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    for role in ('editor', 'viewer'):
        assert admin('POST', '/api/auth/users', {'username': role, 'password': PASSWORD, 'role': role})[0] == 201
    return app


@pytest.fixture
def app(tmp_path):
    registry = Registry(*MODELS)
    database = Database(tmp_path / 'workspaces.sqlite3', registry)
    return build(database)


def workspaces(app, role):
    status, schema, _ = client(app, role)('GET', '/api/schema')
    assert status == 200
    return schema['workspaces']


def shape(found):
    """{workspace: {group: [item keys]}}"""
    return {workspace['key']: {group['key']: [item['key'] for item in group['items']] for group in workspace['groups']} for workspace in found}


def test_admin_sees_every_declared_item_with_labels_and_list_filters(app):
    found = workspaces(app, 'admin')
    assert shape(found) == {
        'sales': {'clients': ['model:client', 'model:client:north_clients', 'model:client:vip_clients'],
                  'deals': ['model:deal', 'model:deal:board', 'model:deal:large_deals', 'slip:quick_deal'],
                  'reports': ['report:ClientList', 'report:PointTotals', 'url:help']},
        'hr': {'payroll': ['model:payroll']}}
    sales = found[0]
    assert {key: sales[key] for key in ('key', 'label', 'icon', 'app', 'source')} == {
        'key': 'sales', 'label': 'Sales', 'icon': 'receipt', 'app': 'app', 'source': 'declared'}
    clients, deals, reports = sales['groups']
    assert clients == {'key': 'clients', 'label': 'Clients', 'icon': 'users', 'items': [
        {'key': 'model:client', 'kind': 'model', 'label': 'Clients', 'model': 'client', 'view': 'list', 'filters': None},
        {'key': 'model:client:north_clients', 'kind': 'model', 'label': 'North clients', 'model': 'client', 'view': 'list',
         'filters': [['region', 'eq', 'north']]},
        {'key': 'model:client:vip_clients', 'kind': 'model', 'label': 'VIP clients', 'model': 'client', 'view': 'list',
         'filters': [['notes', 'icontains', 'vip']]}]}
    assert deals['icon'] is None
    assert deals['items'][1] == {'key': 'model:deal:board', 'kind': 'model', 'label': 'Deals', 'model': 'deal', 'view': 'board', 'filters': None}
    assert deals['items'][3] == {'key': 'slip:quick_deal', 'kind': 'slip', 'label': 'Quick deal', 'slip': 'quick_deal'}
    assert reports['items'] == [{'key': 'report:ClientList', 'kind': 'report', 'label': 'Client list', 'report': 'ClientList'},
                                {'key': 'report:PointTotals', 'kind': 'report', 'label': 'Point totals', 'report': 'PointTotals'},
                                {'key': 'url:help', 'kind': 'url', 'label': 'Help', 'url': 'https://example.com/help'}]


def test_editor_and_viewer_see_only_what_they_can_open(app):
    # The editor reads clients and deals but not payroll; the admin-only report is left out. The People
    # workspace's only group holds payroll, so the group and then the workspace are hidden.
    assert shape(workspaces(app, 'editor')) == {
        'sales': {'clients': ['model:client', 'model:client:north_clients', 'model:client:vip_clients'],
                  'deals': ['model:deal', 'model:deal:board', 'model:deal:large_deals', 'slip:quick_deal'],
                  'reports': ['report:ClientList', 'url:help']}}
    # The viewer can't filter on notes (unreadable), can't read the State the board follows, can't hand in the
    # slip (roles) and can't run the point report.
    assert shape(workspaces(app, 'viewer')) == {
        'sales': {'clients': ['model:client', 'model:client:north_clients'],
                  'deals': ['model:deal', 'model:deal:large_deals'],
                  'reports': ['report:ClientList', 'url:help']}}


def test_a_group_and_a_workspace_left_empty_are_hidden(tmp_path):
    only_admin = Workspace('Admin desk', groups=(NavGroup('Payroll', items=('payroll',)), NavGroup('Points', items=(Shortcut(report=PointTotals),))))
    mixed = Workspace('Mixed', groups=(NavGroup('Payroll', items=('payroll',)), NavGroup('Clients', items=('client',))))
    app = build(Database(tmp_path / 'empty.sqlite3', Registry(*MODELS)), (only_admin, mixed))
    assert shape(workspaces(app, 'admin')) == {'admin_desk': {'payroll': ['model:payroll'], 'points': ['report:PointTotals']},
                                               'mixed': {'payroll': ['model:payroll'], 'clients': ['model:client']}}
    assert shape(workspaces(app, 'editor')) == {'mixed': {'clients': ['model:client']}}


def test_an_app_without_workspaces_gets_one_grouped_as_the_sidebar(tmp_path):
    app = build(Database(tmp_path / 'auto.sqlite3', Registry(*MODELS)), ())
    [found] = workspaces(app, 'admin')
    assert {key: found[key] for key in ('key', 'label', 'icon', 'app', 'source')} == {
        'key': 'app', 'label': 'Evlet', 'icon': 'layers', 'app': 'app', 'source': 'automatic'}
    # Documents have a State or child rows; Records are the rest; Reports without a group come first.
    assert shape([found]) == {'app': {'documents': ['model:deal'], 'records': ['model:client', 'model:payroll'],
                                      'reports': ['report:PointTotals', 'report:Overview', 'report:ClientList']}}
    assert found['groups'][0] == {'key': 'documents', 'label': 'Documents', 'icon': None, 'items': [
        {'key': 'model:deal', 'kind': 'model', 'label': 'Deals', 'model': 'deal', 'view': 'list', 'filters': None}]}
    # The viewer can't read the deal's State, so, as in the sidebar, the deal is a record for it (it has lines).
    assert shape(workspaces(app, 'viewer')) == {'app': {'documents': ['model:deal'], 'records': ['model:client'], 'reports': ['report:ClientList']}}
    assert shape(workspaces(app, 'editor')) == {'app': {'documents': ['model:deal'], 'records': ['model:client'],
                                                        'reports': ['report:Overview', 'report:ClientList']}}


def test_reports_say_when_their_source_is_a_ledger_model(app):
    reports = {entry['name']: entry for entry in client(app)('GET', '/api/schema')[1]['reports']}
    assert {name: entry['source_kind'] for name, entry in reports.items()} == {'ClientList': 'model', 'PointTotals': 'ledger', 'Overview': None}
    # The catalog still never names the ledger model.
    assert reports['PointTotals']['model'] is None
    assert {entry['name']: entry['source_kind'] for entry in client(app)('GET', '/api/_reports')[1]['reports']} == {
        'ClientList': 'model', 'PointTotals': 'ledger', 'Overview': None}


def test_ledger_and_hidden_models_are_not_in_the_schema(app):
    schema = client(app)('GET', '/api/schema')[1]
    names = {entry['name'] for entry in schema['models']}
    assert 'pointmove' not in names and 'secret' not in names and {'client', 'deal', 'payroll'} <= names
    assert 'pointmove' not in json.dumps(schema['workspaces'])


def registered(*groups, key=None, workspaces=None, tmp_path=None):
    database = Database(tmp_path / 'checks.sqlite3', Registry(*MODELS))
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    return Application(database, auth=auth, reports=REPORTS, slips=SLIPS,
                       workspaces=workspaces if workspaces is not None else (Workspace('Desk', key=key, groups=groups),))


@pytest.mark.parametrize('item, message', [
    ('nothing', "Workspace 'Desk', group 'Main', item 'nothing': unknown model 'nothing'"),
    ('secret', "item 'secret': Secret has no screens"),
    ('pointmove', "item 'pointmove': PointMove has no screens"),
    ('deal_lines', 'holds child rows'),
    (Shortcut('client', view='board'), r"item Shortcut\(model='client', view='board'\): Client has no board"),
    (Shortcut('client', filters={'missing': 1}, label='X'), r"filters missing: Unknown field: missing"),
    (Shortcut('client', filters={'region__gt': 'north'}, label='X'), 'filters region__gt: Select fields do not take gt'),
    (Shortcut('client', filters={'region': 'east'}, label='X'), 'filters region'),
    (Shortcut('deal', filters={'client__title': 'Acme'}, label='X'), 'name fields of Deal itself'),
    (Shortcut('client', filters=[['title', 'like', 'x']], label='X'), 'Unknown operator: like'),
    (Shortcut(report='Missing'), "unknown report 'Missing'"),
    (Shortcut(slip='missing'), "unknown slip 'missing'"),
    (Shortcut(slip=DealNote), 'only a slip without a source can be a workspace item'),
])
def test_items_are_checked_against_the_application(tmp_path, item, message):
    with pytest.raises(ValueError, match=message):
        registered(NavGroup('Main', items=(item,)), tmp_path=tmp_path)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'JavaScript:alert(1)', 'data:text/html,hi', 'vbscript:x', 'mailto:a@b.c',
                                 '//evil.example', 'reports', 'ftp://example.com', 'https://', '/a b', '/a\\b', 'http:/x', '', 7])
def test_urls_are_app_paths_or_http_addresses(url):
    with pytest.raises(ValueError, match=r"Workspace 'Desk', group 'Links', item Shortcut\(.*a URL is an app path"):
        Workspace('Desk', groups=(NavGroup('Links', items=(Shortcut(url=url, label='Link'),)),))


def test_good_urls_are_kept():
    for url in ('/reports', '/app/client?f=[]', 'https://example.com/a?b=c', 'HTTP://example.com'):
        Workspace('Desk', groups=(NavGroup('Links', items=(Shortcut(url=url, label='Link'),)),))


@pytest.mark.parametrize('build_workspace, message', [
    (lambda: Workspace('', groups=(NavGroup('A', items=('client',)),)), r"Workspace '': label must be text of 1 to 80 characters"),
    (lambda: Workspace('  ', groups=(NavGroup('A', items=('client',)),)), 'label must be text'),
    (lambda: Workspace('x' * 81, groups=(NavGroup('A', items=('client',)),)), 'label must be text'),
    (lambda: Workspace('Desk', groups=(NavGroup('', items=('client',)),)), r"Workspace 'Desk', group '': label must be text"),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('client', label=''),)),)), r"item Shortcut\(model='client', label=''\): label must be text"),
    (lambda: Workspace('Desk', key='Bad Key', groups=(NavGroup('A', items=('client',)),)), "key must be 1 to 64 lowercase letters.*not 'Bad Key'"),
    (lambda: Workspace('مبيعات', groups=(NavGroup('A', items=('client',)),)), 'so give it key='),
    (lambda: Workspace('Desk', icon='Bad Icon', groups=(NavGroup('A', items=('client',)),)), 'icon is an icon name'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', icon='x y', items=('client',)),)), r"group 'A': icon is an icon name"),
    (lambda: Workspace('Desk'), 'groups takes one or more NavGroup'),
    (lambda: Workspace('Desk', groups=('client',)), "groups takes NavGroup, not 'client'"),
    (lambda: Workspace('Desk', groups=(NavGroup('A'),)), r"group 'A': items takes one or more"),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=('client',)), NavGroup('a', items=('deal',)))), "two groups share the key 'a'"),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Client,)),)), 'item Client: an item is a model name or a Shortcut'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut(),)),)), 'name exactly one of model, report, slip or url'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('client', report='x'),)),)), 'name exactly one'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('client', view='table'),)),)), "view is \"list\" or \"board\", not 'table'"),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut(report='x', view='list'),)),)), 'view and filters go with a model'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('client', filters={'region': 'north'}),)),)), 'a shortcut with filters needs a label'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('client', filters={}, label='X'),)),)), 'filters is a filter object'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('client', filters='region', label='X'),)),)), 'filters is a filter object'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut('client', filters={'region': object()}, label='X'),)),)), 'filter values are JSON values'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut(url='/x'),)),)), 'a URL shortcut needs a label'),
    (lambda: Workspace('Desk', groups=(NavGroup('A', items=(Shortcut(model=7),)),)), 'model takes a name or the class'),
])
def test_declarations_are_checked_when_written(build_workspace, message):
    with pytest.raises(ValueError, match=message):
        build_workspace()


def test_duplicate_keys_are_refused(tmp_path):
    with pytest.raises(ValueError, match=r"item 'client': the key 'model:client' is used by 'client'"):
        registered(NavGroup('A', items=('client',)), NavGroup('B', items=('client',)), tmp_path=tmp_path)
    with pytest.raises(ValueError, match=r"item Shortcut\(model=Client, filters=\{'region': 'north'\}, label='North'\): "
                                         r"the key 'model:client:north' is used by Shortcut\(model='client', label='North'\)"):
        registered(NavGroup('A', items=(Shortcut('client', label='North'), Shortcut(Client, filters={'region': 'north'}, label='North'))), tmp_path=tmp_path)
    with pytest.raises(ValueError, match=r"Workspace 'Sales again' in Application\(workspaces=\.\.\.\): the key 'sales' is taken by "
                                         r"workspace 'Sales' in Application\(workspaces=\.\.\.\)"):
        registered(workspaces=(SALES, Workspace('Sales again', key='sales', groups=(NavGroup('A', items=('client',)),))), tmp_path=tmp_path)
    # The same Workspace listed twice is registered once, not refused as a clash with itself.
    plans = registered(workspaces=(SALES, PEOPLE, SALES), tmp_path=tmp_path).business.workspaces.plans
    assert [plan['key'] for plan in plans] == ['sales', 'hr']
    with pytest.raises(ValueError, match='workspaces takes Workspace definitions'):
        registered(workspaces=(NavGroup('A', items=('client',)),), tmp_path=tmp_path)
    with pytest.raises(ValueError, match='Pass workspaces as a list or tuple'):
        registered(workspaces=SALES, tmp_path=tmp_path)


def test_workspaces_need_authentication(tmp_path):
    with pytest.raises(ValueError, match='workspaces'):
        Application(Database(tmp_path / 'plain.sqlite3', Registry(Client)), workspaces=(PEOPLE,))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    (root / 'apps').mkdir(parents=True)
    (root / 'instances').mkdir()
    yield root
    for name in list(sys.modules):
        if name.split('.')[0] in ('notes', 'tasks'):
            del sys.modules[name]


def test_flat_apps_declare_workspaces_in_workspaces_py(project):
    from evlet.cli.app import create_app
    from evlet.cli.instance import create_instance
    from evlet.cli.install import install
    from evlet.instances import load_runtime
    notes = create_app(project, 'notes')
    (notes / 'workspaces.py').write_text(
        'from evlet import NavGroup, Shortcut, Workspace\n'
        'from .models.item import NotesItem\n\n'
        "SECOND = Workspace('Writing', icon='pencil', groups=(NavGroup('Notes', items=('notesitem',)),))\n"
        "FIRST = Workspace('Reading', groups=(NavGroup('Library', items=(Shortcut(NotesItem, label='All notes'), Shortcut(url='/docs', label='Docs'))),))\n"
        'FIRST_AGAIN = FIRST\n')
    create_app(project, 'tasks')
    create_instance(project, 'acme')
    install(project, 'acme', app_names=('notes', 'tasks'), trust=True)
    app = load_runtime(project, 'acme')
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    found = client(app)('GET', '/api/schema')[1]['workspaces']
    # The notes app's workspaces, in the order they are defined, then the tasks app's automatic one.
    assert [(item['key'], item['app'], item['source']) for item in found] == [
        ('writing', 'notes', 'declared'), ('reading', 'notes', 'declared'), ('tasks', 'tasks', 'automatic')]
    assert shape(found)['reading'] == {'library': ['model:notesitem:all_notes', 'url:docs']}
    # The automatic workspace holds only its own app's models.
    assert shape(found)['tasks'] == {'records': ['model:tasksitem']}
    assert found[2]['label'] == 'Tasks'


DESK_PY = ("from evlet import NavGroup, Workspace\n\n"
           "DESK = Workspace('Desk', groups=(NavGroup('Notes', items=('{model}',)),))\n")


def installed(project, apps):
    """Installs the apps in one instance and returns its workspaces as an admin sees them."""
    from evlet.cli.instance import create_instance
    from evlet.cli.install import install
    from evlet.instances import load_runtime
    create_instance(project, 'acme')
    install(project, 'acme', app_names=tuple(apps), trust=True)
    app = load_runtime(project, 'acme')
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return [(item['key'], item['app'], item['source']) for item in client(app)('GET', '/api/schema')[1]['workspaces']]


def legacy_app(project, name, listed, extra=''):
    """A legacy app whose hooks.py imports its own workspaces.py and passes AppDefinition(workspaces=listed)."""
    from evlet.cli.app import create_app
    package = create_app(project, name, legacy=True) / name
    (package / 'workspaces.py').write_text(DESK_PY.format(model=name + 'item') + extra)
    hooks = (package / 'hooks.py').read_text().replace('from evlet import AppDefinition',
                                                     'from evlet import AppDefinition\nfrom .workspaces import DESK')
    (package / 'hooks.py').write_text(hooks.replace('migrations=migrations)', f'migrations=migrations, workspaces={listed})'))


def test_a_legacy_apps_appdefinition_list_is_its_only_source_of_workspaces(project):
    # hooks.py imports workspaces.py, which also defines a workspace it does not list: the list wins and
    # workspaces.py is not collected again (a legacy app is never discovered as a flat package).
    # The same Workspace listed twice is registered once.
    legacy_app(project, 'notes', '(DESK, DESK)', extra="UNLISTED = Workspace('Unlisted', groups=(NavGroup('A', items=('notesitem',)),))\n")
    assert installed(project, ['notes']) == [('desk', 'notes', 'declared')]


def test_a_workspace_imported_from_another_app_is_not_registered_again(project):
    from evlet.cli.app import create_app
    (create_app(project, 'notes') / 'workspaces.py').write_text(DESK_PY.format(model='notesitem'))
    # Like a model or report imported from another app, the workspace still belongs to the app that declares it.
    (create_app(project, 'tasks') / 'workspaces.py').write_text('from notes.workspaces import DESK\n')
    assert installed(project, ['notes', 'tasks']) == [('desk', 'notes', 'declared'), ('tasks', 'tasks', 'automatic')]


def test_two_workspaces_with_one_key_name_both_sources(project):
    from evlet.cli.app import create_app
    (create_app(project, 'notes') / 'workspaces.py').write_text(DESK_PY.format(model='notesitem'))
    legacy_app(project, 'tasks', '(DESK,)')
    with pytest.raises(ValueError, match=r"Workspace 'Desk' in app tasks \(AppDefinition\): the key 'desk' is taken by "
                                         r"workspace 'Desk' in app notes \(notes/workspaces.py\)"):
        installed(project, ['notes', 'tasks'])
