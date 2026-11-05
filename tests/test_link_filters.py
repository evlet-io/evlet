"""Link filters that refer to other fields of the same form (`Ref`): the field error names the mismatch."""

import pytest

from evlet import Boolean, Child, Database, Link, Model, Ref, Registry, Rule, Table, Text
from test_business import client
from test_rules_types import add_user, start


def first_company(user):
    return {'title': 'Harbor Trading'}


class Company(Model):
    # Editors see one company only; viewers read companies but not their titles.
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'editor': Rule(('read',), scope=first_company),
                   'viewer': Rule(('read',), read_fields=('code',))}
    title = Text(required=True)
    code = Text(default='')


class Branch(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    title = Text(required=True)
    company = Link(Company, required=True)
    open = Boolean(default=True)


class Floor(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    title = Text(required=True)
    branch = Link(Branch, required=True)


class Visit(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
                   'viewer': Rule(('read', 'create', 'update'))}
    title = Text(default='Visit')
    company = Link(Company)
    branch = Link(Branch, filters={'company': Ref('company')})
    floor = Link(Floor, filters={'branch': Ref('branch')})
    # Two filters: the message stays general.
    open_branch = Link(Branch, label='Open branch', filters={'company': Ref('company'), 'open': True})

    class Stop(Child):
        permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update', 'delete')),
                       'viewer': Rule(('read', 'create', 'update', 'delete'))}
        branch = Link(Branch, filters={'company': Ref('parent.company')})

    stops = Table(Stop)


@pytest.fixture
def world(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Company, Branch, Floor, Visit)))
    admin = client(app)
    ids = {}
    for title in ('Harbor Trading', 'Summit Services'):
        ids[title] = admin('POST', '/api/company', {'title': title})[1]['id']
    for title, company, is_open in (('Port office', 'Harbor Trading', True), ('Hill office', 'Summit Services', True),
                                    ('Old office', 'Harbor Trading', False)):
        ids[title] = admin('POST', '/api/branch', {'title': title, 'company': ids[company], 'open': is_open})[1]['id']
    ids['Port floor 1'] = admin('POST', '/api/floor', {'title': 'Port floor 1', 'branch': ids['Port office']})[1]['id']
    ids['Hill floor 1'] = admin('POST', '/api/floor', {'title': 'Hill floor 1', 'branch': ids['Hill office']})[1]['id']
    return app, admin, ids


def test_the_message_names_both_records(world):
    app, admin, ids = world
    status, body, _ = admin('POST', '/api/visit', {'company': ids['Summit Services'], 'branch': ids['Port office']})
    assert status == 422 and body['fields'] == {'branch': 'Port office is not in Summit Services.'}
    # A chain names each link with its own parent.
    status, body, _ = admin('POST', '/api/visit', {'company': ids['Harbor Trading'], 'branch': ids['Port office'], 'floor': ids['Hill floor 1']})
    assert status == 422 and body['fields'] == {'floor': 'Hill floor 1 is not in Port office.'}
    # Values that fit are kept and saved.
    status, body, _ = admin('POST', '/api/visit', {'company': ids['Harbor Trading'], 'branch': ids['Port office'], 'floor': ids['Port floor 1']})
    assert status == 201, body


def test_an_empty_referenced_field_asks_for_it_first(world):
    _, admin, ids = world
    status, body, _ = admin('POST', '/api/visit', {'branch': ids['Port office']})
    assert status == 422 and body['fields'] == {'branch': 'Choose Company first.'}


def test_several_filters_give_the_general_message(world):
    _, admin, ids = world
    status, body, _ = admin('POST', '/api/visit', {'company': ids['Harbor Trading'], 'open_branch': ids['Old office']})
    assert status == 422 and body['fields'] == {'open_branch': "This Open branch doesn't fit the other values on this form."}
    assert admin('POST', '/api/visit', {'company': ids['Harbor Trading'], 'open_branch': ids['Port office']})[0] == 201


def test_child_rows_read_the_parent(world):
    _, admin, ids = world
    status, body, _ = admin('POST', '/api/visit', {'company': ids['Harbor Trading'], 'children': {'stops': [{'branch': ids['Hill office']}]}})
    assert status == 422 and list(body['fields'].values()) == ['Hill office is not in Harbor Trading.']


def test_the_draft_preview_uses_the_same_message(world):
    _, admin, ids = world
    status, body, _ = admin('POST', '/api/_draft/visit', {'values': {'company': ids['Summit Services'], 'branch': ids['Port office']}})
    assert status == 200 and body['errors'] == {'branch': 'Port office is not in Summit Services.'}


def test_titles_the_role_cannot_read_are_never_shown(world):
    app, _, ids = world
    viewer = add_user(app, 'viewer')
    # Viewers read companies but not their titles.
    status, body, _ = viewer('POST', '/api/visit', {'company': ids['Summit Services'], 'branch': ids['Port office']})
    assert status == 422 and body['fields'] == {'branch': "This Branch doesn't fit the other values on this form."}
    editor = add_user(app, 'editor')
    # Editors cannot read Summit Services at all: the company is refused and its title stays private.
    status, body, _ = editor('POST', '/api/visit', {'company': ids['Summit Services'], 'branch': ids['Port office']})
    assert status in (403, 404, 422) and 'Summit' not in str(body)
