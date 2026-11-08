"""List page tabs (Meta.list_tabs, Meta.list_tab): validation, schema, and counts on real SQLite and PostgreSQL."""

import json
from urllib.parse import quote

import pytest

from evlet import Database, Link, Model, Move, PostgreSQLDatabase, Registry, Rule, Select, State, Text
from evlet.ui.metadata import view_for
from test_business import client
from test_postgres import pg_dsn  # noqa: F401 - fixture
from test_rules_types import add_user, start

ALL = Rule(('read', 'create', 'update', 'delete'))


class Team(Model):
    permissions = {'admin': ALL, 'viewer': Rule(('read',)), 'editor': Rule(('read',), scope=lambda user: {'title': 'Sales'})}
    title = Text(default='')


class Member(Model):
    permissions = {'admin': ALL,
                   # The viewer reads its own members (row scope) and not their owner field.
                   'viewer': Rule(('read',), read_fields=('title', 'status', 'team'), scope=lambda user: {'owner': 'mine'}),
                   # Teams are row-scoped for the editor: counting by team would reveal teams it cannot see.
                   'editor': Rule(('read',))}
    title = Text(default='')
    status = Select(('active', 'left', 'on_leave'), default='active')
    team = Link(Team)
    owner = Text(default='')

    class Meta:
        list_tabs = 'team'


class Ticket(Model):
    """The State drives the tabs when Meta.list_tabs is not set; list_tab picks the one it opens on."""
    permissions = {'admin': ALL}
    title = Text(default='')
    stage = State(('open', 'closed', 'void'), moves=(Move('open', 'closed', 'Close'),), locked=('closed', 'void'),
                  final=('void',), voidable=('closed',))
    kind = Select(('bug', 'idea'), default='bug')

    class Meta:
        list_tab = 'open'


class Note(Model):
    permissions = {'admin': ALL, 'viewer': Rule(('read',), read_fields=('title',)), 'editor': Rule(('read',))}
    title = Text(default='')
    status = Select(('active', 'left'), default='active')

    class Meta:
        list_tabs = 'status'
        list_tab = 'active'


MODELS = (Team, Member, Ticket, Note)


@pytest.fixture(params=['sqlite', 'postgresql'])
def app(request, tmp_path):
    registry = Registry(*MODELS)
    if request.param == 'sqlite':
        database = Database(tmp_path / 'tabs.sqlite3', registry)
    else:
        database = PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry)
    application = start(database)
    for role in ('viewer', 'editor'):
        add_user(application, role)
    return application


def model(meta, **fields):
    namespace = {'title': Text(default=''), 'status': Select(('active', 'left'), default='active'), 'team': Link(Team),
                 'note': Text(default=''), **fields, 'Meta': meta}
    return type('Probe', (Model,), namespace)


# Validation and schema

def test_meta_options_and_schema_entry():
    assert view_for(Note)['list_tabs'] == {'field': 'status', 'kind': 'select', 'default': 'active'}
    assert view_for(Ticket)['list_tabs'] == {'field': 'stage', 'kind': 'state', 'default': 'open'}
    assert view_for(Member)['list_tabs'] == {'field': 'team', 'kind': 'link', 'default': 'all'}
    # No State and no Meta.list_tabs: no tabs, and no key.
    assert 'list_tabs' not in view_for(Team)

    class NoTabs:
        list_tabs = None
    assert 'list_tabs' not in view_for(model(NoTabs))

    class LinkDefault:
        list_tabs, list_tab = 'team', 'Sales'
    # A Link's default names the linked record's title; the page looks it up when it loads.
    assert view_for(model(LinkDefault))['list_tabs'] == {'field': 'team', 'kind': 'link', 'default': 'Sales'}


@pytest.mark.parametrize('options, message', [
    ({'list_tabs': 'note'}, 'name a State, Select or Link field of Probe'),
    ({'list_tabs': 'missing'}, 'name a State, Select or Link field of Probe'),
    ({'list_tabs': 3}, 'name a State, Select or Link field of Probe'),
    ({'list_tabs': 'status', 'list_tab': 'gone'}, "'gone' is not a value of status"),
    ({'list_tabs': 'status', 'list_tab': ''}, 'name the tab the list opens on'),
    ({'list_tabs': 'status', 'list_tab': None}, 'name the tab the list opens on'),
    ({'list_tab': 'active'}, 'needs Meta.list_tabs'),
    ({'list_tabs': None, 'list_tab': 'active'}, 'needs Meta.list_tabs'),
])
def test_meta_options_are_checked_at_definition(options, message):
    meta = type('Meta', (), options)
    with pytest.raises(ValueError, match=message):
        Registry(Team, model(meta))


def test_schema_follows_field_permissions(app):
    def tabs(role, name):
        entry = next(item for item in client(app, role)('GET', '/api/schema')[1]['models'] if item['name'] == name)
        return entry.get('list_tabs')
    assert tabs('admin', 'member') == {'field': 'team', 'kind': 'link', 'default': 'all'}
    assert tabs('admin', 'ticket') == {'field': 'stage', 'kind': 'state', 'default': 'open'}
    assert tabs('viewer', 'member') == {'field': 'team', 'kind': 'link', 'default': 'all'}
    assert tabs('editor', 'note') == {'field': 'status', 'kind': 'select', 'default': 'active'}
    # Teams are row-scoped for the editor, so it gets no tabs by team; the viewer can't read Note.status.
    assert tabs('editor', 'member') is None
    assert tabs('viewer', 'note') is None
    assert client(app, 'editor')('GET', '/api/member/list_tabs')[0] == 403
    assert client(app, 'viewer')('GET', '/api/note/list_tabs')[0] == 403


# Counts

def seed(app):
    with app.database.transaction() as tx:
        teams = [tx.create(Team, title=title).id for title in ('Sales', 'Support', 'Ops')]
        for title, status, team, owner in (('Amal', 'active', 0, 'mine'), ('Bilal', 'active', 0, 'mine'), ('Chen', 'left', 1, 'other'),
                                           ('Dana', 'active', 1, 'mine'), ('Eli', 'on_leave', 0, 'other'), ('Fay', 'active', None, 'mine')):
            tx.create(Member, title=title, status=status, team=None if team is None else teams[team], owner=owner)
        for title, status in (('alpha', 'active'), ('beta', 'active'), ('gamma', 'left')):
            tx.create(Note, title=title, status=status)
    return teams


def tabs_url(name, filters=None, q=None):
    url = f'/api/{name}/list_tabs'
    params = [*(['filters=' + quote(json.dumps(filters))] if filters is not None else []), *(['q=' + quote(q)] if q else [])]
    return url + ('?' + '&'.join(params) if params else '')


def ids(app, teams):
    from evlet.identity import RecordId
    return [str(RecordId(team)) for team in teams]


def test_counts_by_select_follow_filters_and_search(app):
    seed(app)
    admin = client(app)
    status, body, _ = admin('GET', tabs_url('note'))
    assert status == 200, body
    assert body == {'field': 'status', 'total': 3, 'items': [{'value': 'active', 'count': 2}, {'value': 'left', 'count': 1}], 'more': False}
    assert admin('GET', tabs_url('note', q='gamma'))[1]['items'] == [{'value': 'left', 'count': 1}]
    assert admin('GET', tabs_url('note', {'title': 'alpha'}))[1] == {'field': 'status', 'total': 1, 'items': [{'value': 'active', 'count': 1}], 'more': False}


def test_counts_by_link_respect_row_scope_and_list_the_most_used(app):
    teams = ids(app, seed(app))
    admin, viewer = client(app), client(app, 'viewer')
    body = admin('GET', tabs_url('member'))[1]
    # Most used first; the member without a team counts in the total only.
    assert body == {'field': 'team', 'total': 6, 'items': [{'value': teams[0], 'count': 3}, {'value': teams[1], 'count': 2}], 'more': False}
    # The viewer reads only its own members (row scope): the counts are over those.
    assert viewer('GET', tabs_url('member'))[1] == {'field': 'team', 'total': 4,
                                                    'items': [{'value': teams[0], 'count': 2}, {'value': teams[1], 'count': 1}], 'more': False}
    assert admin('GET', tabs_url('member', {'status': 'active'}))[1]['items'] == [{'value': teams[0], 'count': 2}, {'value': teams[1], 'count': 1}]


def test_link_tabs_stop_at_the_most_used_values(app):
    with app.database.transaction() as tx:
        teams = [tx.create(Team, title=f'Team {index}').id for index in range(10)]
        for index, team in enumerate(teams):
            for _ in range(index + 1):
                tx.create(Member, title='x', team=team)
    body = client(app)('GET', tabs_url('member'))[1]
    assert [item['count'] for item in body['items']] == [10, 9, 8, 7, 6, 5, 4, 3]
    assert body['items'][0]['value'] == ids(app, teams)[-1]
    assert body['more'] is True and body['total'] == 55


def test_filters_on_unreadable_fields_and_bad_queries_are_refused(app):
    seed(app)
    # The viewer can't read Member.owner, so it can't count by it either.
    assert client(app, 'viewer')('GET', tabs_url('member', {'owner': 'mine'}))[0] == 403
    assert client(app)('GET', '/api/member/list_tabs?x=1')[0] == 422
    assert client(app)('GET', tabs_url('team'))[0] == 403


def test_controls_list_view_sets_the_tabs(app):
    admin = client(app)
    entry = lambda: next(item for item in admin('GET', '/api/schema')[1]['models'] if item['name'] == 'member').get('list_tabs')
    custom = app.business.controls.custom
    base = {'columns': ['title'], 'page_size': 50, 'sort': '-id'}
    assert custom.check_list(Member, None, {**base, 'list_tabs': 'status', 'list_tab': 'active'})['list_tab'] == 'active'
    for bad in ({'list_tabs': 'owner'}, {'list_tabs': 'status', 'list_tab': 'gone'}, {'list_tab': 'active'}):
        with pytest.raises(ValueError, match='list'):
            custom.check_list(Member, None, {**base, **bad})
    custom.lists = {**custom.lists, Member: {**base, 'list_tabs': 'status', 'list_tab': 'active'}}
    assert entry() == {'field': 'status', 'kind': 'select', 'default': 'active'}
    seed(app)
    assert [item['value'] for item in admin('GET', tabs_url('member'))[1]['items']] == ['active', 'left', 'on_leave']
    custom.lists = {**custom.lists, Member: {**base, 'list_tabs': None}}
    assert entry() is None
    assert admin('GET', tabs_url('member'))[0] == 403
    assert custom.file_value('list', Member)['list_tabs'] == 'team'
