"""Declared record layouts: Tabs holding sections, Table fields, Related, Html and Chart blocks (Meta.layout).

Checked when the Registry is built, sent in /api/schema as record_layout, cut per role, and served by the
existing list and aggregate routes on real SQLite (and PostgreSQL in test_postgres.py)."""

import json
from decimal import Decimal as Exact
from urllib.parse import quote

import pytest

from evlet import (Chart, Child, Count, Database, Decimal, Html, Integer, Link, LongText, Model, Ref, Registry, Related, Rule,
                   Section, Select, Sum, Tab, Table, Text)
from evlet.ui.metadata import ui_schema, view_for
from test_business import client
from test_meaning import serve
from test_rules_types import ALL, add_user

FULL = Rule(('read', 'create', 'update', 'delete'))


class Account(Model):
    # Viewers read only northern accounts.
    permissions = {**ALL, 'viewer': Rule(('read',), scope=lambda user: {'region': 'north'})}
    account_name = Text(required=True)
    region = Select(['north', 'south'], default='north')
    notes = LongText(default='')

    class Contact(Child):
        person = Text(required=True)

    contacts = Table(Contact)

    class Meta:
        layout = (
            Tab('Overview', (
                Section('Account', ('account_name', 'region')),
                Html('<p>Work for <strong>{account_name}</strong> in {region}. Use {{braces}} freely.</p>'),
                Chart('stat', y=('hours', 'jobs'), model='job', label='Totals'),
                Chart('bar', x='status', y='hours', model='job', label='Hours by status'),
            )),
            Tab('Work', (
                Related('job', label='Open jobs', filters={'status__ne': 'done'}, fields=('title', 'status', 'hours'), limit=2,
                        order='-hours'),
                Related('job', label='Same region', filters={'region': Ref('region')}),
                'contacts',
            )),
        )


class Job(Model):
    # Editors see only their own jobs; viewers cannot read hours.
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'), scope=lambda user: {'owner': user.id}),
                   'viewer': Rule(('read',), read_fields=('account', 'title', 'status', 'region'))}
    account = Link(Account, required=True)
    title = Text(required=True)
    status = Select(['open', 'done'], default='open')
    region = Select(['north', 'south'], default='north')
    hours = Decimal(scale=1, default='0')
    owner = Integer(default=0)

    class Meta:
        measures = {'hours': Sum('hours'), 'jobs': Count()}


class Plain(Model):
    title = Text()
    body = LongText()

    class Meta:
        layout = (Section('Main', ('title',)), ('More', ('body',)))


MODELS = (Account, Job)


def schema_entry(registry, name):
    return next(item for item in ui_schema(registry)['models'] if item['name'] == name)


def test_the_schema_sends_tabs_and_blocks_as_a_neutral_record_layout():
    layout = schema_entry(Registry(*MODELS), 'account')['record_layout']
    assert layout == {'tabs': [
        {'name': 'overview', 'label': 'Overview', 'blocks': [
            {'kind': 'section', 'label': 'Account'},
            {'kind': 'html', 'html': '<p>Work for <strong>{account_name}</strong> in {region}. Use {{braces}} freely.</p>'},
            {'kind': 'chart', 'type': 'stat', 'model': 'job', 'link': 'account', 'x': None, 'y': ['hours', 'jobs'], 'label': 'Totals', 'filters': []},
            {'kind': 'chart', 'type': 'bar', 'model': 'job', 'link': 'account', 'x': 'status', 'y': ['hours'], 'label': 'Hours by status', 'filters': []},
            # Fields no section lists follow as Other, last on the first Tab.
            {'kind': 'section', 'label': 'Other'}]},
        {'name': 'work', 'label': 'Work', 'blocks': [
            {'kind': 'related', 'model': 'job', 'link': 'account', 'label': 'Open jobs', 'filters': [['status', 'ne', 'done']],
             'fields': ['title', 'status', 'hours'], 'limit': 2, 'order': ['-hours']},
            {'kind': 'related', 'model': 'job', 'link': 'account', 'label': 'Same region',
             'filters': [['region', 'eq', {'$expr': 'ref', 'args': ['region']}]], 'fields': [], 'limit': 5, 'order': ['-id']},
            {'kind': 'table', 'name': 'contacts'}]}]}
    # The sections keep their usual schema entry, so every other client reads them as before.
    assert [group['label'] for group in view_for(Account)['layout']] == ['Account', 'Other']


def test_a_layout_of_sections_only_is_unchanged():
    entry = schema_entry(Registry(Plain), 'plain')
    assert 'record_layout' not in entry
    assert [(group['label'], group['fields'], group['collapsible']) for group in entry['layout']] == [('Main', ['title'], True), ('More', ['body'], False)]


def test_blocks_without_tabs_place_a_table_inside_the_form():
    class Order(Model):
        number = Text()
        note = Text()

        class Line(Child):
            item = Text()

        lines = Table(Line)

        class Meta:
            layout = (Section('Order', ('number',)), 'lines', Html('<p>Thanks.</p>'), Section('Notes', ('note',)))
    entry = schema_entry(Registry(Order), 'order')
    assert entry['record_layout'] == {'blocks': [{'kind': 'section', 'label': 'Order'}, {'kind': 'table', 'name': 'lines'},
                                                 {'kind': 'html', 'html': '<p>Thanks.</p>'}, {'kind': 'section', 'label': 'Notes'}]}


def test_meta_tabs_default_tab_and_panel_name_layout_tabs():
    class Desk(Model):
        title = Text()
        body = LongText()

        class Meta:
            layout = (Tab('Main', (Section('Main', ('title',)),)), Tab('Long text', (Section('Body', ('body',)),), name='body_tab', panel=False))
            tabs = ('body_tab', 'main', 'activity')
            default_tab = 'body_tab'
    registry = Registry(Desk)
    entry = schema_entry(registry, 'desk')
    assert entry['tabs'] == ['body_tab', 'main', 'activity'] and entry['default_tab'] == 'body_tab'
    assert [tab['name'] for tab in entry['record_layout']['tabs']] == ['main', 'body_tab']
    assert entry['panel'] == {'tabs': ['body_tab']}

    class Kept(Model):
        title = Text()
        body = LongText()

        class Meta:
            layout = (Tab('Main', (Section('Main', ('title',)),)), Tab('Extra', (Section('Body', ('body',)),)))
            panel = ('extra', 'title')
    # A named Tab keeps every section on it; a named field keeps its Tab, in the order Meta.panel names them.
    panel = view_for(Kept)['panel']
    assert panel['order'] == ['extra', 'main'] and 'extra' not in panel.get('tabs', []) and 'main' not in panel.get('tabs', [])
    assert 'sections' not in panel


def account_with(*blocks, tabs=False, models=()):
    """A Registry of a fresh Owner model whose Meta.layout holds blocks, with Job-like Work rows pointing to it."""
    class Owner(Model):
        name = Text()
        kind = Select(['a', 'b'])
        secret = Text(hidden=True)

        class Row(Child):
            text = Text()

        rows = Table(Row)

        class Meta:
            layout = (Tab('Main', blocks),) if tabs else blocks

    class Work(Model):
        owner = Link(Owner)
        title = Text()
        kind = Select(['a', 'b'])
        hours = Decimal(scale=1, default='0')
        due = Text()
        note = Text()
        code = Text()

        class Meta:
            measures = {'hours': Sum('hours')}

    class Owner2(Model):
        owner = Link(Owner)
        twice = Link(Owner)
    return Registry(Owner, Work, Owner2, *models)


@pytest.mark.parametrize('blocks, tabs, message', [
    ((Tab('A', (Section('A', ('name',)),)), Section('B', ('kind',))), False, 'put every block inside a Tab'),
    ((Tab('A', (Tab('B', ()),)),), False, 'cannot hold another Tab'),
    ((Section('A', ('name',)), 'nope'), False, "'nope' is not a Table field"),
    ((Section('A', ('name',)), 'rows', 'rows'), False, 'placed more than once'),
    ((Section('A', ('name',)), 42), False, 'use Section'),
    ((Section('A', ('name',)), Section('A', ('kind',)), 'rows'), False, 'give sections different titles'),
    ((Tab('Activity', ()),), False, "name 'activity' is taken"),
    ((Tab('Rows', ()),), False, "name 'rows' is taken"),
    ((Tab('A', ()), Tab('A', ())), False, "name 'a' is taken"),
    ((Related('missing'),), True, 'Unknown model: missing'),
    ((Related('work', 'title'),), True, 'Work.title is not a visible Link to Owner'),
    ((Related('owner2'),), True, 'has 2 Links to Owner (owner, twice); name one as link='),
    ((Related('work', fields=('nope',)),), True, 'unknown or hidden on Work: nope'),
    ((Related('work', fields=('title', 'kind', 'hours', 'due', 'owner', 'note', 'code')),), True, 'shows at most 6 fields'),
    ((Related('work', filters={'nope': 1}),), True, "filter 'nope' names no stored, visible field of Work"),
    ((Related('work', filters={'owner__title': 'x'}),), True, 'not a path or child rows'),
    ((Related('work', filters={'kind__gt': 'a'}),), True, 'select fields do not take gt'),
    ((Related('work', filters={'kind': 'zzz'}),), True, "filter 'kind'"),
    ((Related('work', filters={'kind': Ref('nope')}),), True, "Ref('nope') is not a visible field of Owner"),
    ((Related('work', filters={'kind__in': Ref('kind')}),), True, "Ref('field') compares with one field"),
    ((Related('work', filters={'kind': Ref('secret')}),), True, "Ref('secret') is not a visible field"),
    ((Related('work', order='nope'),), True, 'Related order must name stored fields'),
    ((Related('work', limit=21),), True, 'shows 1 to 20 rows'),
    ((Related('work', hidden=True),), True, 'hidden=True belongs in Meta.relations'),
    ((Chart('stat', y='nope', model='work'),), True, 'Chart y names declared measures of Work'),
    ((Chart('bar', x='nope', y='hours', model='work'),), True, "Chart x groups by a stored, visible field of Work, not 'nope'"),
    ((Chart('bar', x='title:month', y='hours', model='work'),), True, 'after a Date or DateTime field'),
    ((Chart('bar', x='kind', y='hours'),), True, 'a layout Chart names the model'),
    ((Html('<p>{nope}</p>'),), True, 'Html shows unknown or hidden fields: nope'),
    ((Html('<p>{secret}</p>'),), True, 'Html shows unknown or hidden fields: secret'),
    ((Html('<p>a { b</p>'),), True, 'write {field} to show a field'),
    ((Html('<p><a href="{name}">x</a></p>'),), True, 'not in attributes'),
])
def test_layouts_are_checked_when_the_registry_is_built(blocks, tabs, message):
    with pytest.raises(ValueError, match=__import__('re').escape(message)):
        account_with(*blocks, tabs=tabs)


@pytest.mark.parametrize('call, message', [
    (lambda: Html('<p>a</p><script>alert(1)</script>'), 'no scripts'),
    (lambda: Html('<p onclick="go()">a</p>'), 'event attributes'),
    (lambda: Html('<a href="javascript:go()">a</a>'), 'no scripts'),
    (lambda: Html(''), 'content as text'),
    (lambda: Chart('stat', x='kind', y='hours', model='work'), 'a stat chart has no x'),
    (lambda: Chart('bar', y='hours', model='work'), 'groups by a field'),
    (lambda: Chart('pie', x='kind', y='hours', model='work'), 'bar, line or stat'),
    (lambda: Chart('stat', y='hours'), 'model, link and filters are for record layout charts'),
    (lambda: Tab('', ()), 'Give every Tab a title'),
    (lambda: Tab('A', (), name='Bad Name'), 'lowercase letters'),
    (lambda: Tab('A', (), panel=1), 'panel must be True or False'),
    (lambda: Related('work', fields=(1,)), 'distinct field names'),
    (lambda: Related('work', filters='status'), 'list filter syntax'),
])
def test_block_arguments_are_checked(call, message):
    with pytest.raises(ValueError, match=__import__('re').escape(message)):
        call()


def test_relations_take_no_layout_options_and_unknown_tabs_fail():
    class Owner(Model):
        name = Text()

        class Meta:
            relations = (Related('work', 'owner', filters={'title': 'x'}),)

    class Work(Model):
        owner = Link(Owner)
        title = Text()
    with pytest.raises(ValueError, match='take no filters or fields'):
        Registry(Owner, Work)

    class Desk(Model):
        title = Text()

        class Meta:
            layout = (Tab('Main', (Section('Main', ('title',)),)),)
            tabs = ('nope',)
    with pytest.raises(ValueError, match='names unknown tabs: nope'):
        Registry(Desk)


def test_html_keeps_to_the_rich_text_allowlist():
    markup = Html('<h1>Title</h1><p>Hi <em>{name}</em><img src="x.png"><span style="color:red">red</span></p>').markup()
    assert '<img' not in markup and 'style' not in markup and '<h2>Title</h2>' in markup and '<em>{name}</em>' in markup


def test_the_record_layout_is_cut_to_what_each_role_reads(tmp_path):
    app = serve(Database(tmp_path / 'layout.sqlite3', Registry(*MODELS)))
    admin, viewer = client(app), add_user(app, 'viewer')

    def blocks(c):
        entry = next(item for item in c('GET', '/api/schema')[1]['models'] if item['name'] == 'account')
        return [block for tab in entry['record_layout']['tabs'] for block in tab['blocks']]
    assert [block.get('label') for block in blocks(admin) if block['kind'] in ('chart', 'related')] == ['Totals', 'Hours by status', 'Open jobs', 'Same region']
    # The viewer cannot read hours: a chart keeps the measures it may read (or goes when none is left), and the
    # Related block keeps the columns it can read and an order it may use.
    shown = {block.get('label'): block for block in blocks(viewer) if block['kind'] in ('chart', 'related')}
    assert list(shown) == ['Totals', 'Open jobs', 'Same region'] and shown['Totals']['y'] == ['jobs']
    assert shown['Open jobs']['fields'] == ['title', 'status'] and shown['Open jobs']['order'] == ['-id']


def check_layout_data(app):
    """Related blocks and charts read through the list and aggregate routes, with the block's filters and the
    record's id, under each role's row scope."""
    admin, editor, viewer = client(app), add_user(app, 'editor'), add_user(app, 'viewer')
    editor_id = next(user['id'] for user in admin('GET', '/api/auth/users')[1]['items'] if user['username'] == 'editor')
    north = admin('POST', '/api/account', {'account_name': 'North', 'region': 'north'})[1]['id']
    south = admin('POST', '/api/account', {'account_name': 'South', 'region': 'south'})[1]['id']
    for account, title, status, region, hours, owner in (
            (north, 'Survey', 'open', 'north', '3.5', editor_id), (north, 'Design', 'open', 'south', '8.0', 0),
            (north, 'Build', 'done', 'north', '12.0', editor_id), (north, 'Review', 'open', 'north', '1.5', editor_id),
            (south, 'Elsewhere', 'open', 'south', '40.0', editor_id)):
        assert admin('POST', '/api/job', {'account': account, 'title': title, 'status': status, 'region': region, 'hours': hours, 'owner': owner})[0] == 201
    layout = next(item for item in admin('GET', '/api/schema')[1]['models'] if item['name'] == 'account')['record_layout']
    open_jobs, same_region = [block for block in layout['tabs'][1]['blocks'] if block['kind'] == 'related']
    stat, bar = [block for block in layout['tabs'][0]['blocks'] if block['kind'] == 'chart']

    def listed(c, block, record, region=None):
        # What the record page sends: the Link equal to the record, then the filters with Ref values filled in.
        filters = [[block['link'], 'eq', record]] + [[field, op, region if isinstance(value, dict) else value] for field, op, value in block['filters']]
        query = f'limit={block["limit"]}&order_by={",".join(block["order"])}&filters=' + quote(json.dumps(filters))
        status, page, _ = c('GET', f'/api/{block["model"]}?{query}')
        return status, page

    status, page = listed(admin, open_jobs, north)
    assert status == 200 and page['total'] == 3 and [item['title'] for item in page['items']] == ['Design', 'Survey']
    # The editor's row scope applies: only their own open jobs.
    assert [item['title'] for item in listed(editor, open_jobs, north)[1]['items']] == ['Survey', 'Review']
    # A filter that compares with the record's region.
    assert sorted(item['title'] for item in listed(admin, same_region, north, 'north')[1]['items']) == ['Build', 'Review', 'Survey']
    # Viewers read northern accounts only: the filter on a Link to that row-scoped model works for a readable
    # account and is refused for another.
    assert listed(viewer, same_region, north, 'north')[1]['total'] == 3
    assert listed(viewer, same_region, south, 'south')[0] == 403

    def aggregate(c, block, record):
        query = f'measures={",".join(block["y"])}' + (f'&group_by={block["x"]}' if block['x'] else '') + '&filters=' + quote(json.dumps([[block['link'], 'eq', record]]))
        return c('GET', f'/api/{block["model"]}/aggregate?{query}')

    status, result, _ = aggregate(admin, stat, north)
    assert status == 200 and result['items'] == [{'hours': '25.0', 'jobs': 4}]
    # The same numbers as an aggregate in Python over the same records.
    with app.business.database.transaction() as tx:
        [expected] = tx.aggregate(Job, measures={'hours': 'hours', 'jobs': 'jobs'}, filters={'account': north})
    assert (Exact(result['items'][0]['hours']), result['items'][0]['jobs']) == (expected['hours'], expected['jobs'])
    assert aggregate(admin, bar, north)[1]['items'] == [{'status': 'done', 'hours': '12.0'}, {'status': 'open', 'hours': '13.0'}]
    # The editor's chart counts only their own jobs.
    assert aggregate(editor, bar, north)[1]['items'] == [{'status': 'done', 'hours': '12.0'}, {'status': 'open', 'hours': '5.0'}]
    # A viewer may filter by a readable account although accounts are row-scoped for them, but not by another.
    count = {'kind': 'chart', 'model': 'job', 'link': 'account', 'x': 'status', 'y': ['jobs']}
    assert aggregate(viewer, count, north)[1]['items'] == [{'status': 'done', 'jobs': 1}, {'status': 'open', 'jobs': 3}]
    assert aggregate(viewer, count, south)[0] == 403


def test_related_blocks_and_charts_follow_row_scope_and_filters(tmp_path):
    check_layout_data(serve(Database(tmp_path / 'layout.sqlite3', Registry(*MODELS))))


def test_controls_export_writes_the_record_layout(tmp_path):
    from evlet.controls_export import layout_code
    code = layout_code(Account, [{'title': 'Account', 'fields': [{'name': 'region'}, {'name': 'account_name'}]},
                                 {'title': 'Extra', 'fields': [{'name': 'notes'}]}])
    assert code.startswith('layout = (\n    Tab(\'Overview\', (\n        Section(\'Account\', (\'region\', \'account_name\')')
    assert "Html('<p>Work for <strong>{account_name}</strong> in {region}. Use {{braces}} freely.</p>')" in code
    assert "Chart('bar', x='status', y=('hours',), label='Hours by status', model='job')" in code
    assert "Related('job', label='Same region', filters={'region': Ref('region')})" in code
    assert "'contacts'," in code
    # A section the app does not place (here one Controls added) goes last on the first Tab.
    assert code.index("Section('Extra', ('notes',)") < code.index("Tab('Work'")
    namespace = {}
    exec('from evlet import Chart, Html, Ref, Related, Section, Tab\n' + code, namespace)
    assert [type(block).__name__ for block in namespace['layout'][1].blocks] == ['Related', 'Related', 'str']
