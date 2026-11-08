"""Workflow storage and evaluator (phase 2): definitions, routing and quorums on real SQLite.

The check_* functions take an application, so tests/test_postgres.py runs the same checks on PostgreSQL.
"""
import json
import sqlite3

import pytest

from evlet import (Application, Boolean, Child, ConflictError, Currency, Database, Fact, Hierarchy, Link, LocalAuth, Model,
                   Policy, Registry, Rule, Select, Table, Text, ValidationError, WorkflowOptions, action)
from evlet.auth import AccessError, User
from evlet.calc import F
from evlet.workflow_definition import DefinitionError, evaluate, Undecided
from evlet.workflow_engine import WorkflowError

PEOPLE = Hierarchy('wfperson', parent='manager')
ROLES = {'finance': 'editor', 'procurement': 'editor', 'it': 'editor', 'ops': 'editor', 'director': 'editor', 'clerk': 'viewer', 'buyer': 'editor'}
REVIEW = Rule(('read', 'review'))


class WfPerson(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read',)), 'viewer': Rule(('read',))}
    full_name = Text(required=True)
    manager = Link('wfperson', index=True)
    department = Select(('it', 'finance', 'operations'))


class WfLine(Child):
    amount = Currency(default='0')
    urgent = Boolean(default=False)


class WfOrder(Model):
    confirmable = True

    class Meta:
        workflow = WorkflowOptions(
            protects=('confirm', 'cancel'), executes=('confirm',), unmatched='block',
            facts={'lines.count': Fact.count('lines', label='Number of lines'), 'lines.total': Fact.sum('lines', 'amount'),
                   'lines.urgent': Fact.any('lines', F('urgent') == True),  # noqa: E712 - a finite condition, not a test
                   'risk': Fact(Select(('low', 'high')), 'Supplier risk', read='risk_rating')},
            people=PEOPLE, assignable=('approver',), harmless=('remarks',))

    title = Text(required=True)
    kind = Select(('equipment', 'services', 'other'), default='other')
    total = Currency(default='0')
    approver = Link(WfPerson)
    remarks = Text(default='')
    risk_note = Text(default='')
    lines = Table(WfLine)
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'confirm', 'cancel')),
                   'editor': Rule(('read', 'create', 'update', 'confirm')), 'viewer': REVIEW,
                   'finance': REVIEW, 'procurement': REVIEW, 'it': REVIEW, 'ops': REVIEW, 'director': REVIEW, 'clerk': Rule(('read',)),
                   # Buyers send orders and review each other's.
                   'buyer': Rule(('read', 'create', 'update', 'review'))}

    def risk_rating(self):
        return self.risk_note or None


MODELS = (WfPerson, WfOrder)
ADMIN = User(1, 'admin', 'admin')


def build(db, **options):
    app = Application(db, auth=LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False),
                      policy=Policy(db.registry, roles=ROLES), **options)
    app.initialize()
    auth, people = app.business.auth, {}
    # Nobody signs in here, so accounts get a placeholder instead of a slow password hash.
    with db.transaction() as tx:
        for name, role in (('admin', 'admin'), ('rita', 'editor'), ('mona', 'viewer'), ('dora', 'director'), ('fay', 'finance'),
                           ('fred', 'finance'), ('pia', 'procurement'), ('ivan', 'it'), ('otto', 'ops'), ('cleo', 'clerk'), ('sam', 'editor'),
                           ('bob', 'buyer'), ('bea', 'buyer')):
            user_id = tx.connection.execute('INSERT INTO evlet_users (username, password, role) VALUES (?, ?, ?)', (name, '!', role)).lastrowid
            people[name] = User(user_id, name, role)
    assert people.pop('admin').id == ADMIN.id
    with db.transaction() as tx:
        dora = tx.create(WfPerson, full_name='Dora', department='operations').id
        mona = tx.create(WfPerson, full_name='Mona', manager=dora, department='finance').id
        rita = tx.create(WfPerson, full_name='Rita', manager=mona, department='it').id
        sam = tx.create(WfPerson, full_name='Sam', department='it').id
    for name, record in (('dora', dora), ('mona', mona), ('rita', rita), ('sam', sam)):
        auth.link_user(people[name].id, WfPerson, record)
    app.people = {name: User(user.id, user.username, user.role, ((('wfperson', {'dora': dora, 'mona': mona, 'rita': rita, 'sam': sam}[name]),)
                                                                  if name in ('dora', 'mona', 'rita', 'sam') else ()))
                  for name, user in people.items()}
    app.persons = {'dora': dora, 'mona': mona, 'rita': rita, 'sam': sam}
    return app


@pytest.fixture
def app(tmp_path):
    return build(Database(tmp_path / 'workflow.sqlite3', Registry(*MODELS)))


# Definition helpers

def go(stage):
    return {'kind': 'stage', 'stage': stage}


def first(rows, otherwise):
    return {'kind': 'first_match', 'rows': [{'id': rid, 'when': when, 'stage': stage} for rid, when, stage in rows], 'otherwise': otherwise}


def row(rid, field, op, value):
    return {'id': rid, 'field': field, 'op': op, 'value': value}


def act(aid, kind, to=None, **extra):
    labels = {'submit': 'Send for review', 'approve': 'Approve', 'reject': 'Reject', 'request_changes': 'Request changes', 'move': 'Move'}
    return {'id': aid, 'label': labels[kind], 'kind': kind, 'go_to': to, **extra}


DRAFT = 'stg_draft1'
APPROVED, REJECTED = 'stg_apprv1', 'stg_rejct1'
ENDS = [{'id': APPROVED, 'label': 'Approved', 'kind': 'end', 'outcome': 'approved'},
        {'id': REJECTED, 'label': 'Rejected', 'kind': 'end', 'outcome': 'rejected'}]


def draft(to):
    return {'id': DRAFT, 'label': 'Draft', 'kind': 'work', 'assign': {'kind': 'requester'}, 'actions': [act('act_submit', 'submit', go(to))]}


def single(sid, label, assign, approve_to=APPROVED, *, extra=None, **stage):
    return {'id': sid, 'label': label, 'kind': 'review', 'assign': assign, **stage,
            'actions': [act('act_' + sid[4:] + 'ok', 'approve', go(approve_to)), act('act_' + sid[4:] + 'no', 'reject', go(REJECTED)),
                        act('act_' + sid[4:] + 'ch', 'request_changes', go(DRAFT)), *(extra or [])]}


def group(sid, label, items, *, on_complete=None, **settings):
    return {'id': sid, 'label': label, 'kind': 'review', 'reviews': {
        'run': 'together', 'complete': {'kind': 'all'}, 'rejection': 'veto', 'when_none': 'block',
        'on_complete': on_complete or go(APPROVED), 'on_reject': go(REJECTED), 'on_changes': go(DRAFT), 'items': items, **settings},
        'actions': [act('act_' + sid[4:] + 'ok', 'approve'), act('act_' + sid[4:] + 'no', 'reject'), act('act_' + sid[4:] + 'ch', 'request_changes')]}


def review(rid, label, assign, when=None, required=None):
    return {'id': rid, 'label': label, 'assign': assign, 'when': when, 'required': required or {'kind': 'any'}, 'stages': None}


def role(name):
    return {'kind': 'role', 'role': name}


def definition(stages, *, protects='confirm', applies_when=None, name='Order approval', **settings):
    return {'schema': 'evlet.workflow/1', 'name': name, 'model': 'wforder',
            'protects': None if protects is None else {'kind': 'lifecycle', 'operation': protects},
            'applies_when': applies_when, 'start': DRAFT, 'settings': settings, 'notes': '', 'stages': stages}


MANAGER = definition([draft('stg_mngr01'), single('stg_mngr01', 'Manager Review', {'kind': 'fact', 'fact': 'requester.manager'}), *ENDS])


def publish(app, value):
    engine = app.business.workflows
    with app.database.transaction() as tx:
        saved = engine.save_draft(tx, ADMIN, WfOrder, value)
        return saved['id'], engine.publish(tx, ADMIN, saved['id'], revision=saved['revision'])


def problems(app, value):
    with app.database.transaction() as tx:
        with pytest.raises(DefinitionError) as caught:
            app.business.workflows.check(tx, WfOrder, value)
    return caught.value.problems


def order(app, **values):
    lines = values.pop('lines', [])
    with app.business.context(ADMIN) as context:
        return context.create(WfOrder, title='Order', **values, children={'lines': lines})['id']


def decide(app, name, record, action=None, **options):
    options.setdefault('protects', 'confirm')
    with app.database.transaction() as tx:
        return app.business.workflows.decide(tx, app.people[name], WfOrder, record, action, **options)


def status(app, record, name='rita', protects='confirm'):
    with app.database.transaction() as tx:
        return app.business.workflows.status(tx, app.people[name], WfOrder, record, protects=protects)


def tasks(found):
    """{review id, or the stage id of a single task: status} for the current attempt."""
    stages = {item['id']: item['stage'] for item in found['executions']}
    return {task['review'] or stages[task['execution']]: task['status'] for task in found['tasks']}


# Registration and the catalogue

def test_registration_checks_name_the_model_and_argument():
    def model(**options):
        class Bad(Model):
            confirmable = True
            class Meta:
                workflow = WorkflowOptions(**{'protects': ('confirm',), 'unmatched': 'block', **options})
            title = Text()
        return Bad
    cases = [({'unmatched': None}, 'unmatched=...) is required'), ({'protects': ('approve',)}, "'approve' is not confirm"),
             ({'protects': ('move:issued',)}, 'move:issued is not a state'), ({'protects': ('action:ship',)}, 'action:ship is not a model action'),
             ({'people': 'people'}, 'people=...) is a Hierarchy'), ({'facts': {'title': Fact(Select(('a',)), 'A', read='title')}}, 'collides'),
             ({'facts': {'lines.count': Fact.count('lines')}}, 'lines is not a Table'), ({'harmless': ('nothing',)}, 'names nothing'),
             ({'protects': 'confirm'}, 'is a tuple of operation names')]
    for options, message in cases:
        with pytest.raises(ValueError, match=message.replace('(', r'\(').replace(')', r'\)').replace('.', r'\.')):
            Registry(model(**options))

    class Plain(Model):
        title = Text()
        class Meta:
            workflow = WorkflowOptions(protects=('cancel',), unmatched='allow')
    with pytest.raises(ValueError, match='cancel needs a confirmable model'):
        Registry(Plain)

    class Unopted(Model):
        title = Text()
        permissions = {'viewer': Rule(('read', 'review'))}
    with pytest.raises(ValueError, match="Rule names 'review'"):
        Policy(Registry(Unopted))


def test_catalogue_lists_declared_facts_people_and_reviewing_roles(app):
    from evlet.workflow import catalogue
    found = catalogue(app.business, WfOrder)
    assert found['protects'] == [{'kind': 'lifecycle', 'operation': 'confirm', 'label': 'Confirm'},
                                 {'kind': 'lifecycle', 'operation': 'cancel', 'label': 'Cancel'}]
    assert found['unmatched'] == 'block'
    facts = found['facts']
    assert facts['total'] == {'label': 'Total', 'type': 'money', 'source': 'field'}
    assert facts['kind']['type'] == 'enum' and facts['kind']['choices'][0] == ['equipment', 'Equipment']
    assert facts['lines.count'] == {'label': 'Number of lines', 'type': 'number', 'source': 'rows'}
    assert facts['lines.urgent']['type'] == 'boolean' and facts['fact.risk']['source'] == 'lookup'
    assert facts['requester.department']['type'] == 'enum' and facts['approver']['type'] == 'link'
    assert found['people'] == [{'fact': 'requester.manager', 'label': "Requester's manager"}]
    reviewers = {item['role'] for item in found['roles'] if item['review']}
    # Admin reviews by default because it reads the model; the clerk reads but cannot review.
    assert reviewers == {'admin', 'viewer', 'finance', 'procurement', 'it', 'ops', 'director', 'buyer'}


def test_review_is_a_capability_that_needs_read_and_grants_no_update(app):
    policy = app.business.policy
    assert policy.allows(app.people['fay'], WfOrder, 'review') and not policy.allows(app.people['fay'], WfOrder, 'update')
    assert not policy.allows(app.people['cleo'], WfOrder, 'review') and not policy.allows(app.people['rita'], WfOrder, 'review')
    assert policy.allows(ADMIN, WfOrder, 'review')
    from evlet.auth import KeyAccess
    reader = User(app.people['fay'].id, 'fay', 'finance', access=KeyAccess(write=False))
    assert not policy.allows(reader, WfOrder, 'review')
    # A Controls override can take review away, never add it.
    narrowed = Policy(app.database.registry, roles=ROLES, overrides={('finance', WfOrder): Rule(('read',))})
    assert not narrowed.allows(app.people['fay'], WfOrder, 'review')


# Definitions

def test_publication_refuses_broken_definitions_by_id(app):
    bad = definition([draft('stg_mngr01'), single('stg_mngr01', 'Manager', role('clerk')), *ENDS,
                      {'id': 'stg_orphan', 'label': 'Orphan', 'kind': 'operational', 'assign': role('ops'), 'actions': []}])
    found = {(item['id'], item['message'].split(';')[0]) for item in problems(app, bad)}
    assert ('stg_mngr01', 'The role clerk cannot review wforder records') in found
    assert ('stg_orphan', 'A stage has 1 to 20 actions') in found
    assert ('stg_orphan', 'No action leads to this stage') in found
    # Conditions: unknown facts, operators that do not apply, values of the wrong type, missing Otherwise.
    stages = [draft('stg_mngr01'), single('stg_mngr01', 'Manager', role('viewer'), approve_to=None), *ENDS]
    stages[1]['actions'][0]['go_to'] = {'kind': 'first_match', 'rows': [
        {'id': 'rte_big001', 'when': row('cnd_r00001', 'total', 'gt', 100000.5), 'stage': APPROVED},
        {'id': 'rte_big002', 'when': row('cnd_r00002', 'secret', 'eq', 'x'), 'stage': APPROVED},
        {'id': 'rte_big003', 'when': row('cnd_r00003', 'kind', 'gt', 'equipment'), 'stage': APPROVED}], 'otherwise': 'stg_nowher'}
    found = {(item['id'], item['message']) for item in problems(app, definition(stages))}
    assert ('cnd_r00001', 'Use a money value') in found
    assert ('cnd_r00002', "'secret' is not a fact of wforder") in found
    assert any(ident == 'cnd_r00003' and message.startswith("'gt' does not apply to enum") for ident, message in found)
    assert any(message == 'Choose the Otherwise destination' for _, message in found)
    # Groups: a count above the number of reviews, branch rejection with All, reserved nested reviews.
    items = [review('rev_finan1', 'Finance', role('finance')), review('rev_procu1', 'Procurement', role('procurement'))]
    items[1]['stages'] = [draft('stg_mngr01')]
    found = {item['message'] for item in problems(app, definition([draft('stg_group1'), group('stg_group1', 'Department', items,
                                                                                         complete={'kind': 'count', 'count': 3}), *ENDS]))}
    assert 'Complete when all, any or a number of reviews (at most the number of reviews) approve' in found
    assert 'Nested reviews are not available yet; leave stages empty' in found
    found = {item['message'] for item in problems(app, definition([draft('stg_group1'), group('stg_group1', 'Department', items[:1], rejection='branch'), *ENDS]))}
    assert 'When all reviews must approve, a rejection is a veto' in found
    # Operations the app does not list, a cycle of automatic steps, duplicate ids and the size limit.
    looping = [draft('stg_groupa'),
               group('stg_groupa', 'A', [review('rev_aaaaaa', 'A', role('finance'), row('cnd_never1', 'total', 'lt', '0'))],
                     on_complete=go('stg_groupb'), when_none='continue'),
               group('stg_groupb', 'B', [review('rev_bbbbbb', 'B', role('finance'), row('cnd_never2', 'total', 'lt', '0'))],
                     on_complete=go('stg_groupa'), when_none='continue'), *ENDS]
    found = {item['message'] for item in problems(app, definition(looping, protects='reopen'))}
    assert any(message.startswith('wforder lets a workflow protect confirm, cancel') for message in found)
    assert 'These stages pass to each other automatically forever: stg_groupa → stg_groupb → stg_groupa' in found
    twice = definition([draft('stg_mngr01'), single('stg_mngr01', 'Manager', role('viewer')), *ENDS, ENDS[0]])
    assert {'id': APPROVED, 'message': 'This id is used twice'} in problems(app, twice)
    huge = definition([draft('stg_mngr01'), single('stg_mngr01', 'M' * 300_000, role('viewer')), *ENDS])
    assert problems(app, huge) == [{'id': 'definition', 'message': 'A definition is at most 256 KB'}]
    deep = row('cnd_deep01', 'total', 'gt', '1')
    for level in range(5):
        deep = {'id': f'cnd_group{level}', 'match': 'all', 'items': [deep]}
    stages = [draft('stg_mngr01'), single('stg_mngr01', 'Manager', role('viewer'), require={'when': deep, 'message': 'No'}), *ENDS]
    assert any('nest at most 4' in item['message'] for item in problems(app, definition(stages)))


def check_versions_are_immutable(app):
    workflow_id, version = publish(app, MANAGER)
    assert version == 1
    engine = app.business.workflows
    with app.database.transaction() as tx:
        stored = engine.definition(tx, workflow_id, 1)
        assert stored['id'] == workflow_id and stored['version'] == 1 and stored['settings']['self_approval'] == 'block'
        # The editor's revision guards concurrent saves.
        saved = engine.save_draft(tx, ADMIN, WfOrder, {**MANAGER, 'name': 'Renamed'}, workflow_id=workflow_id, revision=2)
        with pytest.raises(WorkflowError) as caught:
            engine.save_draft(tx, ADMIN, WfOrder, MANAGER, workflow_id=workflow_id, revision=2)
        assert caught.value.code == 'WORKFLOW_CHANGED'
        assert engine.publish(tx, ADMIN, workflow_id, revision=saved['revision']) == 2
        assert engine.definition(tx, workflow_id, 1)['name'] == 'Order approval'
    postgres = getattr(app.database, 'driver', None) is not None
    for sql in ("UPDATE evlet_workflow_versions SET definition='{}'", 'DELETE FROM evlet_workflow_versions',
                *(() if postgres else ("INSERT OR REPLACE INTO evlet_workflow_versions (workflow_id, version, definition, hash, published_at) "
                                       f"VALUES ('{workflow_id}', 1, '{{}}', 'x', 'now')",))):
        with pytest.raises((ConflictError, sqlite3.OperationalError)):
            with app.database.transaction() as tx:
                tx.connection.execute(sql)
    with app.database.transaction() as tx:
        assert engine.definition(tx, workflow_id, 1)['name'] == 'Order approval'


def test_versions_are_immutable(app):
    check_versions_are_immutable(app)


def test_a_second_workflow_must_split_on_one_choice_field(app):
    publish(app, MANAGER)
    with pytest.raises(DefinitionError, match='already applies to these records'):
        publish(app, {**MANAGER, 'name': 'Second'})


def test_split_workflows_select_by_their_values(app):
    equipment = {**MANAGER, 'name': 'Equipment', 'applies_when': row('cnd_equip1', 'kind', 'in', ['equipment', 'other'])}
    services = {**MANAGER, 'name': 'Services', 'applies_when': {'id': 'cnd_group1', 'match': 'all', 'items': [row('cnd_servc1', 'kind', 'eq', 'services')]}}
    first_id, _ = publish(app, equipment)
    second_id, _ = publish(app, services)
    with pytest.raises(DefinitionError, match='already applies'):
        publish(app, {**MANAGER, 'name': 'Overlap', 'applies_when': row('cnd_overl1', 'kind', 'eq', 'other')})
    with pytest.raises(DefinitionError, match='already applies'):
        publish(app, {**MANAGER, 'name': 'Other field', 'applies_when': row('cnd_total1', 'total', 'gt', '5')})
    assert decide(app, 'rita', order(app, kind='services'), kind='submit')['workflow'] == second_id
    assert decide(app, 'rita', order(app, kind='equipment'), kind='submit')['workflow'] == first_id


def test_unmatched_block_paused_and_operational_selection(app):
    record = order(app)
    with pytest.raises(WorkflowError) as caught:
        decide(app, 'rita', record, kind='submit')
    assert caught.value.code == 'WORKFLOW_NONE'
    workflow_id, _ = publish(app, MANAGER)
    with app.database.transaction() as tx:
        app.business.workflows.set_paused(tx, ADMIN, workflow_id, True)
    with pytest.raises(WorkflowError) as caught:
        decide(app, 'rita', record, kind='submit')
    assert caught.value.code == 'WORKFLOW_PAUSED'
    with app.database.transaction() as tx:
        app.business.workflows.set_paused(tx, ADMIN, workflow_id, False)
    assert decide(app, 'rita', record, kind='submit')['stage']['id'] == 'stg_mngr01'
    # An operational workflow tracks stages without protecting anything, beside the approval run.
    inspection = definition([{'id': DRAFT, 'label': 'Received', 'kind': 'work', 'assign': {'kind': 'requester'},
                              'actions': [act('act_submit', 'submit', go('stg_inspct'))]},
                             {'id': 'stg_inspct', 'label': 'Inspection', 'kind': 'operational', 'assign': role('ops'),
                              'actions': [act('act_passed', 'move', go('stg_closed'))]},
                             {'id': 'stg_closed', 'label': 'Closed', 'kind': 'end', 'outcome': 'closed'}], protects=None, name='Inspection')
    publish(app, inspection)
    decide(app, 'rita', record, kind='submit', protects='')
    with pytest.raises(AccessError):
        decide(app, 'fay', record, 'act_passed', protects='')
    found = decide(app, 'otto', record, 'act_passed', protects='')
    assert found['status'] == 'closed' and found['protects'] is None
    assert status(app, record)['protects'] == {'kind': 'lifecycle', 'operation': 'confirm'}


# Routing

def test_manager_only_approval_and_a_missing_manager_blocks(app):
    publish(app, MANAGER)
    record = order(app)
    found = decide(app, 'rita', record, kind='submit')
    assert found['status'] == 'active' and found['stage']['label'] == 'Manager Review' and found['requester'] == app.people['rita'].id
    manager_task = found['tasks'][-1]
    assert manager_task['people'] == [app.people['mona'].id] and manager_task['status'] == 'pending'
    # Someone else, even a reviewer, is not assigned; the requester cannot decide their own request.
    for name, error in (('fay', AccessError), ('rita', AccessError)):
        with pytest.raises(error):
            decide(app, name, record, kind='approve')
    found = decide(app, 'mona', record, kind='approve', feedback='  Checked against budget  ')
    assert found['status'] == 'approved' and found['attempt_status'] == 'approved' and found['stage']['id'] == APPROVED
    assert [(item['kind'], item['feedback']) for item in found['decisions']] == [('submit', None), ('approve', 'Checked against budget')]
    # Sam's person has no manager: the run blocks with a repair message, never skipping the review.
    other = order(app)
    found = decide(app, 'sam', other, kind='submit')
    assert found['status'] == 'blocked' and 'requester has no manager' in found['blocked']['message']
    with pytest.raises(WorkflowError) as caught:
        decide(app, 'mona', other, kind='approve')
    assert caught.value.code == 'WORKFLOW_BLOCKED'


def test_conditional_route_compares_money_exactly(app):
    route = first([('rte_dirc01', row('cnd_above1', 'total', 'gt', '100000.00'), 'stg_drctr1')], APPROVED)
    stages = [draft('stg_mngr01'), single('stg_mngr01', 'Manager Review', {'kind': 'fact', 'fact': 'requester.manager'}, None),
              single('stg_drctr1', 'Director Review', role('director')), *ENDS]
    stages[1]['actions'][0]['go_to'] = route
    publish(app, definition(stages))
    outcomes = {}
    for total in ('100000.00', '100000.01', '99999.99'):
        record = order(app, total=total)
        decide(app, 'rita', record, kind='submit')
        outcomes[total] = decide(app, 'mona', record, kind='approve')['stage']['id']
    assert outcomes == {'100000.00': APPROVED, '100000.01': 'stg_drctr1', '99999.99': APPROVED}


def test_unknown_facts_block_and_never_count_as_false(app):
    stages = [draft('stg_mngr01'), single('stg_mngr01', 'Manager Review', role('viewer'), None), *ENDS]
    stages[1]['actions'][0]['go_to'] = first([('rte_risky1', row('cnd_risky1', 'fact.risk', 'eq', 'high'), REJECTED)], APPROVED)
    publish(app, definition(stages))
    record = order(app)
    found = decide(app, 'rita', record, kind='submit')
    assert found['status'] == 'blocked'
    assert found['blocked'] == {'stage': 'stg_mngr01', 'message': 'Where Approve goes cannot be decided: Supplier risk has no value or cannot be read.',
                                'causes': ['fact.risk']}
    known = order(app, risk_note='low')
    decide(app, 'rita', known, kind='submit')
    assert decide(app, 'mona', known, kind='approve')['stage']['id'] == APPROVED
    # The evaluator itself: null only satisfies isnull, and a group with an undecided item is undecided.
    types = {'total': 'money', 'kind': 'enum'}
    assert isinstance(evaluate(row('cnd_x00001', 'total', 'ne', '5'), {'total': None}, types), Undecided)
    assert evaluate(row('cnd_x00002', 'total', 'isnull', True), {'total': None}, types) is True
    either = {'id': 'cnd_any001', 'match': 'any', 'items': [row('cnd_x00003', 'kind', 'eq', 'other'), row('cnd_x00004', 'total', 'gt', '1')]}
    assert evaluate(either, {'kind': 'other', 'total': '2'}, types) is True
    assert evaluate(either, {'kind': 'other'}, types).causes == ('total',)
    assert evaluate(row('cnd_x00005', 'total', 'between', ['1', '2.50']), {'total': '2.500'}, types) is True
    assert evaluate(row('cnd_x00006', 'kind', 'nin', ['a', 'b']), {'kind': 'b'}, types) is False


def test_require_and_action_conditions_are_decided_at_stage_entry(app):
    stages = [draft('stg_mngr01'), single('stg_mngr01', 'Manager Review', role('viewer'),
                                          require={'when': row('cnd_kind01', 'kind', 'in', ['equipment', 'services']),
                                                   'message': 'Purchase type must be Equipment or Services.'}), *ENDS]
    stages[1]['actions'][1]['when'] = row('cnd_many01', 'lines.count', 'gt', 1)
    publish(app, definition(stages))
    record = order(app, kind='other')
    found = decide(app, 'rita', record, kind='submit')
    assert found['status'] == 'blocked' and found['blocked']['message'] == 'Purchase type must be Equipment or Services.'
    record = order(app, kind='services', lines=[{'amount': '5'}])
    decide(app, 'rita', record, kind='submit')
    # One line: Reject is not offered at this stage, and the frozen routing says so.
    with pytest.raises(AccessError):
        decide(app, 'mona', record, kind='reject', feedback='No')
    routing = status(app, record)['executions'][-1]['routing']
    assert routing['when'] == {'act_mngr01ok': True, 'act_mngr01no': False, 'act_mngr01ch': True}
    assert routing['facts'] == {'kind': 'services', 'lines.count': 1}


def test_facts_from_rows_lookups_and_the_requester(app):
    engine = app.business.workflows
    record = order(app, total='12.50', risk_note='high', lines=[{'amount': '2.25', 'urgent': False}, {'amount': '0.75', 'urgent': True}])
    names = {'total', 'lines.count', 'lines.total', 'lines.urgent', 'fact.risk', 'requester', 'requester.role', 'requester.department'}
    with app.database.transaction() as tx:
        facts, unknown = engine.facts(tx, WfOrder, record, app.people['rita'].id, names)
        assert facts == {'total': '12.5', 'lines.count': 2, 'lines.total': '3', 'lines.urgent': True, 'fact.risk': 'high',
                         'requester': app.people['rita'].id, 'requester.role': 'editor', 'requester.department': 'it'} and not unknown
        pass
    empty = order(app)
    with app.database.transaction() as tx:
        facts, unknown = engine.facts(tx, WfOrder, empty, app.people['cleo'].id, {'requester.department', 'fact.risk', 'lines.urgent'})
        assert facts == {'fact.risk': None, 'lines.urgent': False} and set(unknown) == {'requester.department'}


# Parallel reviews and quorums

DEPARTMENT = [review('rev_techn1', 'Technical', role('it'), row('cnd_equip1', 'kind', 'eq', 'equipment')),
              review('rev_opert1', 'Operations', role('ops'), row('cnd_servc1', 'kind', 'eq', 'services')),
              review('rev_finan1', 'Finance', role('finance')), review('rev_procu1', 'Procurement', role('procurement'))]


def department(**settings):
    return definition([draft('stg_deprt1'), group('stg_deprt1', 'Department Review', DEPARTMENT, **settings), *ENDS])


def test_parallel_reviews_wait_only_for_the_branches_that_apply(app):
    publish(app, department())
    record = order(app, kind='equipment')
    found = decide(app, 'rita', record, kind='submit')
    routing = found['executions'][-1]['routing']
    assert routing['activated'] == ['rev_techn1', 'rev_finan1', 'rev_procu1'] and routing['skipped'] == ['rev_opert1']
    assert tasks(found) == {DRAFT: 'done', 'rev_techn1': 'pending', 'rev_finan1': 'pending', 'rev_procu1': 'pending'}
    # Finance has two eligible people; either decides for the review.
    finance = next(task for task in found['tasks'] if task['review'] == 'rev_finan1')
    assert finance['people'] == [app.people['fay'].id, app.people['fred'].id]
    decide(app, 'ivan', record, kind='approve')
    found = decide(app, 'fred', record, kind='approve')
    assert found['status'] == 'active' and tasks(found)['rev_finan1'] == 'approved'
    # Fay's task was decided by Fred: her vote arrives late and is refused, not counted.
    with pytest.raises(WorkflowError) as caught:
        decide(app, 'fay', record, kind='approve')
    assert caught.value.code == 'WORKFLOW_CHANGED'
    found = decide(app, 'pia', record, kind='approve')
    assert found['status'] == 'approved' and found['stage']['id'] == APPROVED


def test_any_completes_on_one_approval_and_closes_the_others(app):
    publish(app, department(complete={'kind': 'any'}, rejection='branch'))
    record = order(app, kind='services')
    decide(app, 'rita', record, kind='submit')
    found = decide(app, 'otto', record, kind='approve')
    assert found['status'] == 'approved'
    assert tasks(found) == {DRAFT: 'done', 'rev_opert1': 'approved', 'rev_finan1': 'closed', 'rev_procu1': 'closed'}
    assert {task['closed_reason'] for task in found['tasks'] if task['status'] == 'closed'} == {'No longer required'}
    with pytest.raises(WorkflowError):
        decide(app, 'pia', record, kind='approve')


def test_count_quorum_and_rejection_veto_or_branch(app):
    publish(app, department(complete={'kind': 'count', 'count': 2}, rejection='branch'))
    record = order(app, kind='services')
    decide(app, 'rita', record, kind='submit')
    # A branch rejection fails only that review; two approvals still complete the stage.
    found = decide(app, 'otto', record, kind='reject', feedback='Not our area')
    assert found['status'] == 'active' and tasks(found)['rev_opert1'] == 'rejected'
    decide(app, 'fay', record, kind='approve')
    assert decide(app, 'pia', record, kind='approve')['status'] == 'approved'
    # Two rejections leave too few reviews to reach the count: the stage is rejected.
    other = order(app, kind='services')
    decide(app, 'rita', other, kind='submit')
    decide(app, 'otto', other, kind='reject', feedback='No')
    found = decide(app, 'fay', other, kind='reject', feedback='No')
    assert found['status'] == 'rejected' and tasks(found)['rev_procu1'] == 'closed'


def test_a_veto_rejects_the_stage(app):
    publish(app, department())
    record = order(app, kind='equipment')
    decide(app, 'rita', record, kind='submit')
    decide(app, 'ivan', record, kind='approve')
    with pytest.raises(ValidationError):
        decide(app, 'fay', record, kind='reject')
    found = decide(app, 'fay', record, kind='reject', feedback='Over budget')
    assert found['status'] == 'rejected' and found['stage']['id'] == REJECTED
    assert tasks(found) == {DRAFT: 'done', 'rev_techn1': 'approved', 'rev_finan1': 'rejected', 'rev_procu1': 'closed'}


def test_zero_branches_block_or_continue(app):
    only = [DEPARTMENT[0], DEPARTMENT[1]]
    blocking = definition([draft('stg_deprt1'), group('stg_deprt1', 'Department Review', only), *ENDS])
    workflow_id, _ = publish(app, blocking)
    found = decide(app, 'rita', order(app, kind='other'), kind='submit')
    assert found['status'] == 'blocked' and found['blocked']['message'].startswith('No review of Department Review applies')
    continuing = definition([draft('stg_deprt1'), group('stg_deprt1', 'Department Review', only, when_none='continue'), *ENDS])
    with app.database.transaction() as tx:
        saved = app.business.workflows.save_draft(tx, ADMIN, WfOrder, continuing, workflow_id=workflow_id, revision=2)
        app.business.workflows.publish(tx, ADMIN, workflow_id, revision=saved['revision'])
    found = decide(app, 'rita', order(app, kind='other'), kind='submit')
    assert found['status'] == 'approved' and found['version'] == 2
    assert [item['outcome'] for item in found['executions']] == ['submit', 'skipped', 'approved']


def test_sequence_starts_each_review_after_the_one_before(app):
    publish(app, department(run='sequence'))
    record = order(app, kind='services')
    found = decide(app, 'rita', record, kind='submit')
    assert tasks(found) == {DRAFT: 'done', 'rev_opert1': 'pending'}
    with pytest.raises(AccessError):
        decide(app, 'fay', record, kind='approve')
    found = decide(app, 'otto', record, kind='approve')
    assert tasks(found) == {DRAFT: 'done', 'rev_opert1': 'approved', 'rev_finan1': 'pending'}
    decide(app, 'fay', record, kind='approve')
    assert decide(app, 'pia', record, kind='approve')['status'] == 'approved'


def test_people_count_once_within_and_across_reviews(app):
    # Mona is assigned twice: directly to Finance and through the approver field to Procurement.
    items = [review('rev_finan1', 'Finance', {'kind': 'user', 'user': None}),
             review('rev_procu1', 'Procurement', {'kind': 'field', 'field': 'approver'})]
    items[0]['assign']['user'] = app.people['mona'].id
    publish(app, definition([draft('stg_deprt1'), group('stg_deprt1', 'Department Review', items), *ENDS]))
    record = order(app, approver=app.persons['mona'])
    decide(app, 'rita', record, kind='submit')
    with pytest.raises(ValidationError):
        decide(app, 'mona', record, kind='approve')
    found = decide(app, 'mona', record, kind='approve', review='rev_finan1')
    # Her vote counts for Finance only, and nobody else can decide Procurement: the run blocks for repair.
    assert found['status'] == 'blocked' and 'Nobody else is eligible to decide Procurement' in found['blocked']['message']
    run = status(app, record)
    with pytest.raises(ConflictError):
        with app.database.transaction() as tx:
            tx.connection.execute("INSERT INTO evlet_workflow_decisions (run_id, attempt_id, execution_id, task_id, action_id, kind, actor, "
                                  "principal, at, version, generation) SELECT run_id, attempt_id, execution_id, ?, action_id, 'approve', actor, "
                                  'principal, at, version, generation FROM evlet_workflow_decisions WHERE id=?',
                                  (run['tasks'][-1]['id'], run['decisions'][-1]['id']))


def test_all_people_of_a_review_must_approve(app):
    items = [review('rev_finan1', 'Finance', role('finance'), required={'kind': 'all'})]
    publish(app, definition([draft('stg_deprt1'), group('stg_deprt1', 'Department Review', items), *ENDS]))
    record = order(app)
    decide(app, 'rita', record, kind='submit')
    found = decide(app, 'fay', record, kind='approve')
    assert found['status'] == 'active' and tasks(found)['rev_finan1'] == 'pending'
    with pytest.raises(WorkflowError):
        decide(app, 'fay', record, kind='approve')
    assert decide(app, 'fred', record, kind='approve')['status'] == 'approved'
    # A count above the people eligible blocks at entry rather than lowering the quorum.
    with app.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_workflows SET status='archived'")
    items = [review('rev_finan1', 'Finance', role('finance'), required={'kind': 'count', 'count': 3})]
    publish(app, definition([draft('stg_deprt1'), group('stg_deprt1', 'Department Review', items), *ENDS]))
    found = decide(app, 'rita', order(app), kind='submit')
    assert found['status'] == 'blocked' and 'needs 3 people to approve, but only 2 are eligible' in found['blocked']['message']


def test_self_approval_and_removed_access(app):
    # Bob sends his own order to the buyers' review: he is left out, and Bea decides it.
    items = [review('rev_buyer1', 'Buyers', role('buyer'))]
    publish(app, definition([draft('stg_deprt1'), group('stg_deprt1', 'Department Review', items), *ENDS]))
    record = order(app)
    found = decide(app, 'bob', record, kind='submit')
    assert found['tasks'][-1]['people'] == [app.people['bea'].id]
    with pytest.raises(AccessError):
        decide(app, 'bob', record, kind='approve')
    # Bea is deactivated after assignment; she can no longer decide, and nobody new is picked.
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_users SET active=0 WHERE id=?', (app.people['bea'].id,))
    with pytest.raises(AccessError):
        decide(app, 'bea', record, kind='approve')
    # A reviewer who can only read cannot send a record for review: sending needs update access.
    other = order(app)
    with pytest.raises(AccessError):
        decide(app, 'fay', other, kind='submit')


def check_rework_starts_a_new_attempt(app):
    publish(app, department())
    record = order(app, kind='services')
    decide(app, 'rita', record, kind='submit')
    decide(app, 'otto', record, kind='approve')
    found = decide(app, 'fay', record, kind='request_changes', feedback='Attach the quote')
    assert found['status'] == 'draft' and found['stage']['id'] == DRAFT and found['attempt'] == 2
    assert tasks(found) == {DRAFT: 'pending'}
    found = decide(app, 'rita', record, kind='submit')
    # Otto's earlier approval belongs to attempt 1 and does not count again.
    assert tasks(found) == {DRAFT: 'done', 'rev_opert1': 'pending', 'rev_finan1': 'pending', 'rev_procu1': 'pending'}
    assert [item['occurrence'] for item in found['executions'] if item['stage'] == 'stg_deprt1'] == [1, 2]
    for name in ('otto', 'fay', 'pia'):
        found = decide(app, name, record, kind='approve')
    assert found['status'] == 'approved' and found['attempt'] == 2
    assert len({item['attempt'] for item in found['decisions']}) == 2
    token = found['token']
    assert token == f'{found["run"]}.7'
    with pytest.raises(WorkflowError):
        decide(app, 'otto', record, kind='approve')


def test_rework_starts_a_new_attempt(app):
    check_rework_starts_a_new_attempt(app)


def check_decisions_are_append_only(app):
    publish(app, MANAGER)
    record = order(app)
    decide(app, 'rita', record, kind='submit')
    decide(app, 'mona', record, kind='approve')
    postgres = getattr(app.database, 'driver', None) is not None
    for sql in ("UPDATE evlet_workflow_decisions SET feedback='changed'", 'DELETE FROM evlet_workflow_decisions',
                *(() if postgres else ('INSERT OR REPLACE INTO evlet_workflow_decisions SELECT * FROM evlet_workflow_decisions',))):
        with pytest.raises((ConflictError, sqlite3.OperationalError)):
            with app.database.transaction() as tx:
                tx.connection.execute(sql)
    assert [item['kind'] for item in status(app, record)['decisions']] == ['submit', 'approve']
    # A run that reached its end frees the record for a new run on the same operation.
    ended = status(app, record)['run']
    assert decide(app, 'rita', record, kind='submit')['run'] == ended + 1


def test_decisions_are_append_only(app):
    check_decisions_are_append_only(app)


def check_routing_is_deterministic(app):
    route = first([('rte_dirc01', row('cnd_above1', 'total', 'gt', '100000.00'), 'stg_drctr1')], APPROVED)
    publish(app, definition([draft('stg_deprt1'), group('stg_deprt1', 'Department Review', DEPARTMENT, on_complete=route),
                             single('stg_drctr1', 'Director Review', role('director')), *ENDS]))
    journeys = []
    for _ in range(2):
        record = order(app, kind='equipment', total='250000.00')
        decide(app, 'rita', record, kind='submit')
        for name in ('pia', 'ivan', 'fred'):
            decide(app, name, record, kind='approve')
        found = decide(app, 'dora', record, kind='approve')
        journeys.append([(item['stage'], item['outcome'], item['routing']['activated'], item['routing']['routes']) for item in found['executions']])
        assert found['status'] == 'approved'
    assert journeys[0] == journeys[1]
    assert [stage for stage, *_ in journeys[0]] == [DRAFT, 'stg_deprt1', 'stg_drctr1', APPROVED]


def test_routing_is_deterministic(app):
    check_routing_is_deterministic(app)


def test_tables_need_no_app_columns_and_existing_lifecycle_is_unchanged(app):
    # Meta.workflow adds no columns to the model. With unmatched="block" and no published workflow, confirm
    # fails closed; cancel and reopen are not protected by any workflow here.
    columns = {row[1] for row in sqlite3.connect(app.database.path).execute('PRAGMA table_info(wforder)')}
    assert not any('workflow' in name for name in columns)
    record = order(app, total='5')
    with pytest.raises(WorkflowError) as caught:
        with app.business.context(ADMIN) as context:
            context.confirm(WfOrder, record)
    assert caught.value.code == 'WORKFLOW_NONE'
    # A reviewer reads but cannot update.
    other = order(app)
    with pytest.raises(AccessError):
        with app.business.context(app.people['fay']) as context:
            context.update(WfOrder, other, remarks='x')


def test_the_playground_order_approval_example(tmp_path):
    from examples.playground.lifecycle.app import APPROVAL, Order, create_app
    app = create_app(tmp_path / 'lifecycle.sqlite3')
    app.initialize()
    engine = app.business.workflows
    with app.database.transaction() as tx:
        ids = {name: tx.connection.execute('INSERT INTO evlet_users (username, password, role) VALUES (?, ?, ?)',
                                           (name, '!', role)).lastrowid for name, role in (('ada', 'admin'), ('eve', 'editor'), ('vic', 'viewer'))}
    ada, eve, vic = (User(ids[name], name, role) for name, role in (('ada', 'admin'), ('eve', 'editor'), ('vic', 'viewer')))
    with app.database.transaction() as tx:
        saved = engine.save_draft(tx, ada, Order, json.loads(APPROVAL.read_text()))
        engine.publish(tx, ada, saved['id'], revision=saved['revision'])
    outcomes, records = [], []
    for number, rate in (('PO-1', '400.00'), ('PO-2', '1200.00')):
        with app.business.context(eve) as context:
            record = context.create(Order, number=number, customer='Acme', children={'lines': [{'description': 'Chairs', 'rate': rate}]})['id']
            context.workflow(Order, record).decide(kind='submit', key=f'send-{number}')
        with app.business.context(vic) as context:
            outcomes.append(context.workflow(Order, record).decide(kind='approve')['stage']['label'])
        records.append(record)
    assert outcomes == ['Approved', 'Second review']
    # The published workflow protects confirm: the approved order confirms once, the other waits for its second review.
    with app.business.context(eve) as context:
        assert context.confirm(Order, records[0])['_lifecycle']['state'] == 'confirmed'
    with pytest.raises(WorkflowError) as caught:
        with app.business.context(eve) as context:
            context.confirm(Order, records[1])
    assert caught.value.code == 'WORKFLOW_REQUIRED'
