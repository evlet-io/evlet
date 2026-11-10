"""The compact record page's framework half: Meta.cards, the Main layout block and Section descriptions.

Checked when the Registry is built and sent in /api/schema, cut per role, on real SQLite."""

import pytest

from evlet import (Child, Count, Currency, Database, Integer, Link, LongText, Main, Model, Registry, Related, Rule, Section, Select,
                   Sum, Table, Text)
from evlet.calc import F
from evlet.ui.metadata import ui_schema, view_for
from test_business import client
from test_meaning import serve
from test_rules_types import ALL, add_user


def job_model(**meta):
    """A job with stored, calculated and fetched fields, and per-record and model-level measures."""
    class Client(Model):
        permissions = ALL
        client_name = Text(required=True)
        rate = Currency(default='0.00', currency='AED')

    class Job(Model):
        permissions = {**ALL, 'viewer': Rule(('read',), read_fields=('client', 'title', 'status', 'hours', 'client_rate'))}
        client = Link(Client)
        title = Text(required=True)
        status = Select(['open', 'done'], default='open')
        hours = Integer(default=0)
        budget = Currency(default='0.00', currency='AED')
        notes = LongText(default='')
        # Display only: calculated or fetched when read, never stored.
        cost = Currency(compute='hours * 10', stored=False, currency='AED')
        client_rate = Currency(fetch='client.rate', stored=False, currency='AED')
        secret = Integer(default=0, hidden=True)
        left = Currency(compute='budget - hours * 10', stored=False, currency='AED', hidden=True)

        class Line(Child):
            item = Text()

        lines = Table(Line)

        class Meta:
            measures = {'visits': Count('visit', link='job'), 'minutes': Sum('minutes', model='visit', link='job'), 'jobs': Count()}

    class Visit(Model):
        permissions = {**ALL, 'viewer': Rule(('read',), read_fields=('job', 'note'))}
        job = Link(Job, required=True)
        note = Text()
        minutes = Integer(default=0)

    for key, value in meta.items():
        setattr(Job.Meta, key, value)
    return Client, Job, Visit


def schema_entry(registry, name='job'):
    return next(item for item in ui_schema(registry)['models'] if item['name'] == name)


# ---------- Meta.cards ----------

def test_cards_name_derived_fields_and_per_record_measures():
    _, job, _ = models = job_model(cards=('cost', 'client_rate', 'visits', 'minutes'))
    registry = Registry(*models)
    assert registry.cards[job] == ('cost', 'client_rate', 'visits', 'minutes')
    # None and up to five are allowed; without Meta.cards there is no entry.
    assert Registry(*job_model()).cards == {}
    _, job, _ = models = job_model(cards=())
    assert Registry(*models).cards[job] == ()


@pytest.mark.parametrize('cards, message', [
    (('cost', 'client_rate', 'visits', 'minutes', 'cost'), r'cost is named more than once'),
    (('cost', 'client_rate', 'visits', 'minutes', 'nothing', 'more'), r'name up to 5 derived fields or per-record measures'),
    ('cost', r'name up to 5 derived fields or per-record measures'),
    (('cost', 3), r'name up to 5 derived fields or per-record measures'),
    (('nothing',), r"Job has no field or measure 'nothing'"),
    (('hours',), r'hours is not derived; a card shows a field with compute=, fetch= or query=, or a per-record measure'),
    (('lines',), r"Job has no field or measure 'lines'"),
    (('left',), r'left is hidden'),
    (('jobs',), r'jobs is a model-level measure; a card shows a per-record measure'),
])
def test_card_mistakes_fail_registration(cards, message):
    with pytest.raises(ValueError, match=r'Job\.Meta\.cards: ' + message):
        Registry(*job_model(cards=cards))


def test_cards_are_for_root_models_and_a_name_cannot_be_both():
    class Order(Model):
        number = Text()

        class Line(Child):
            amount = Integer(default=0)
            double = Integer(compute='amount * 2', stored=False)

            class Meta:
                cards = ('double',)

        lines = Table(Line)
    with pytest.raises(ValueError, match=r'Line\.Meta\.cards: cards are for root models, not Table rows'):
        Registry(Order)

    class Box(Model):
        size = Integer(default=0)
        size_twice = Integer(compute='size * 2', stored=False)

        class Meta:
            measures = {'size_twice': Count('crate', link='box')}
            cards = ('size_twice',)

    class Crate(Model):
        box = Link(Box)
    with pytest.raises(ValueError, match=r'size_twice is both a field and a measure'):
        Registry(Box, Crate)


def test_the_schema_sends_cards_cut_to_what_each_role_reads(tmp_path):
    app = serve(Database(tmp_path / 'cards.sqlite3', Registry(*job_model(cards=('cost', 'client_rate', 'visits', 'minutes')))))
    admin, viewer = client(app), add_user(app, 'viewer')

    def cards(c):
        return next(item for item in c('GET', '/api/schema')[1]['models'] if item['name'] == 'job').get('cards')
    assert cards(admin) == ['cost', 'client_rate', 'visits', 'minutes']
    # The viewer cannot read cost, nor the minutes a measure adds up: those cards are left out, like key fields.
    assert cards(viewer) == ['client_rate', 'visits']
    # A model without Meta.cards has no cards entry.
    assert 'cards' not in next(item for item in admin('GET', '/api/schema')[1]['models'] if item['name'] == 'visit')


def test_card_values_come_from_the_record_and_its_measures(tmp_path):
    app = serve(Database(tmp_path / 'values.sqlite3', Registry(*job_model(cards=('cost', 'visits')))))
    admin = client(app)
    rate = admin('POST', '/api/client', {'client_name': 'Acme', 'rate': '12.50'})[1]['id']
    job = admin('POST', '/api/job', {'client': rate, 'title': 'Survey', 'hours': 3})[1]['id']
    for minutes in (20, 40):
        assert admin('POST', '/api/visit', {'job': job, 'minutes': minutes})[0] == 201
    # Display-only fields are calculated, never stored: the record page reads them from the draft preview.
    status, preview, _ = admin('POST', '/api/_draft/job', {'id': job, 'values': {}})
    assert status == 200, preview
    assert preview['values']['cost'] == '30.00'
    assert admin('GET', f'/api/job/{job}/measures')[1]['measures'] == {'visits': 2, 'minutes': 60}


# ---------- Main and Section descriptions ----------

def test_main_is_the_primary_area_and_sections_carry_descriptions():
    _, job, _ = models = job_model(layout=(
        Main(('title', 'client', 'status'), 'lines'),
        Section('Effort', ('hours', 'cost'), description='Time spent and what it costs.'),
        Related('visit', label='Visits'),
        Section('Money', ('budget', 'client_rate'), collapsed=True)))
    entry = schema_entry(Registry(*models))
    assert entry['layout'][:3] == [
        # Main has no heading; the model's label names it where a name is needed.
        {'label': 'Job', 'fields': ['title', 'client', 'status'], 'collapsible': False, 'collapsed': False, 'flow': 'rows', 'main': True},
        {'label': 'Effort', 'fields': ['hours', 'cost'], 'collapsible': True, 'collapsed': False, 'flow': 'rows',
         'description': 'Time spent and what it costs.'},
        {'label': 'Money', 'fields': ['budget', 'client_rate'], 'collapsible': True, 'collapsed': True, 'flow': 'rows'}]
    assert entry['record_layout'] == {'blocks': [
        {'kind': 'section', 'label': 'Job', 'main': True}, {'kind': 'table', 'name': 'lines', 'main': True},
        {'kind': 'section', 'label': 'Effort'},
        {'kind': 'related', 'model': 'visit', 'link': 'job', 'label': 'Visits', 'filters': [], 'fields': [], 'limit': 5, 'order': ['-id']},
        {'kind': 'section', 'label': 'Money'},
        # Fields no section lists follow as Other, last.
        {'kind': 'section', 'label': 'Other'}]}
    assert repr(Main(('title',), 'lines')) == "Main(('title',), 'lines')"
    assert repr(Section('Effort', ('hours',), description='Time.')) == \
        "Section('Effort', ('hours',), collapsible=True, collapsed=False, description='Time.')"


def test_main_with_only_tables_and_a_layout_without_main_is_unchanged():
    entry = schema_entry(Registry(*job_model(layout=(Main((), 'lines'), Section('Job', ('title',))))))
    assert entry['record_layout']['blocks'][:2] == [{'kind': 'table', 'name': 'lines', 'main': True}, {'kind': 'section', 'label': 'Job'}]
    assert not any(group.get('main') for group in entry['layout'])
    plain = schema_entry(Registry(*job_model(layout=(Section('Job', ('title',)), 'lines'))))
    assert plain['record_layout'] == {'blocks': [{'kind': 'section', 'label': 'Job'}, {'kind': 'table', 'name': 'lines'},
                                                 {'kind': 'section', 'label': 'Other'}]}


@pytest.mark.parametrize('layout, meta, message', [
    ((Section('Job', ('title',)), Main(('status',), 'lines')), {}, r'Job\.Meta\.layout: put Main first'),
    ((Main(('title',), 'lines'), Main(('status',))), {}, r'Job\.Meta\.layout: use one Main'),
    ((Main(('title',)),), {}, r'a layout with Main places every Table field, in Main or after it; lines is not placed'),
    ((Main(('title',), 'items'),), {}, r"Main names 'items', which is not a Table field of Job"),
    ((Main(('title', 'secret'), 'lines'),), {}, r'Main names unknown or hidden fields: secret'),
    ((Main(('title',), 'lines'), Section('More', ('title',))), {}, r'Layout lists title more than once'),
    ((Main(('title',), 'lines'), 'lines'), {}, r"Table 'lines' is placed more than once"),
    ((Main(('title',), 'lines'), Section('Job', ('status',))), {}, r'give sections different titles'),
    ((Main(('title',), 'lines'),), {'tabs': ('details', 'activity')}, r'Job\.Meta\.tabs was removed: record pages have no tabs'),
    ((Main(('title',), 'lines'),), {'default_tab': 'activity'}, r'Job\.Meta\.default_tab was removed: record pages have no tabs'),
    ((Main(('title',), 'lines'), Section('More', ('status',), description='')), {}, r"description must be nonempty text, not ''"),
    ((Main(('title',), 'lines'), Section('More', ('status',), description=3)), {}, r'description must be nonempty text, not 3'),
])
def test_compact_layout_mistakes_fail_registration(layout, meta, message):
    with pytest.raises(ValueError, match=message):
        Registry(*job_model(layout=layout, **meta))


def test_main_checks_its_own_arguments():
    for args, message in ((('title',), 'list its fields as a tuple'), (((),), 'needs fields, Table fields or both'),
                          ((('title', 'title'),), 'name each field and Table once'), ((('title',), 3), 'name Table fields after the fields')):
        with pytest.raises(ValueError, match=message):
            Main(*args)


def test_controls_keep_main_and_descriptions_and_export_them(tmp_path):
    from evlet.controls_export import layout_code
    _, job, _ = job_model(layout=(Main(('title', 'client'), 'lines'), Section('Effort', ('hours', 'cost'), description='Time spent.')))
    code = layout_code(job, [{'title': 'Job', 'fields': [{'name': 'client'}, {'name': 'title'}]},
                             {'title': 'Effort', 'fields': [{'name': 'hours'}]}])
    assert "Main(('client', 'title'), 'lines')," in code
    assert "Section('Effort', ('hours',), collapsible=False, description='Time spent.')," in code
    namespace = {}
    exec('from evlet import Main, Section\n' + code, namespace)
    assert [type(block).__name__ for block in namespace['layout']] == ['Main', 'Section']


def test_a_controls_layout_keeps_the_main_mark_and_descriptions(tmp_path):
    models = job_model(layout=(Main(('title', 'client'), 'lines'), Section('Effort', ('hours', 'cost'), description='Time spent.')))
    app = serve(Database(tmp_path / 'controls.sqlite3', Registry(*models)))
    admin = client(app)
    sections = [{'title': 'Job', 'fields': [{'name': 'client', 'width': 6}, {'name': 'title', 'width': 6}]},
                {'title': 'Effort', 'fields': [{'name': 'hours', 'width': 6}, {'name': 'status', 'width': 6}]}]
    status, body, _ = admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'layout', 'key': 'Job', 'value': sections}]})
    assert status == 200, body
    layout = next(item for item in admin('GET', '/api/schema')[1]['models'] if item['name'] == 'job')['layout']
    assert [(group['label'], group.get('main'), group.get('description')) for group in layout][:2] == [
        ('Job', True, None), ('Effort', None, 'Time spent.')]


# ---------- the Playground ----------

def test_the_playground_project_uses_the_compact_record_page():
    from examples.playground.models import Project, registry
    assert registry.cards[Project] == ('invoiced', 'paid', 'tasks', 'open_tasks')
    view = view_for(Project)
    assert [(group['label'], group.get('main', False), 'description' in group) for group in view['layout']] == [
        ('Project', True, False), ('Budget', False, True), ('Schedule', False, True), ('Notes', False, True)]
    assert 'tabs' not in view and 'default_tab' not in view
    assert registry.layouts[Project]['blocks'][0] == {'kind': 'section', 'label': 'Project', 'main': True}
