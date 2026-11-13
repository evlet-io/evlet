"""The state manager (evlet/statemanager.py): steps, rules, remarks, asks and the four hooks, on real SQLite
through the ASGI routes, Context, and the Eva and MCP tool route."""

import json

import pytest

from evlet import Child, Database, Integer, Link, Model, Registry, Rule, State, Table, Text, ValidationError, release, state, validate
from evlet.identity import RecordId
from evlet.auth import User
from evlet.calc import F
from test_business import client
from test_lines import Counting, counted
from test_rules_types import add_user, start

ADMIN = User(1, 'admin', 'admin')
ORDER, FAIL = [], set()
ROLES = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
         'viewer': Rule(('read',)), 'manager': Rule(('read', 'update'))}


class Note(Model):
    """A record the hooks write, so a rollback can be seen."""
    permissions = ROLES
    text = Text(default='')


class Ticket(Model):
    permissions = ROLES
    title = Text(default='')
    approver = Integer()
    discount = Integer(default=0)
    # Where the ticket is in its process, so it may change while the ticket is locked.
    reason = Text(versioned=False)
    status = State(
        State.Step('draft', 'Draft'),
        State.Step('review', 'In review', color='amber', lock=True),
        State.Step('done', 'Done', color='green', lock=True),
        State.Step('void', 'Void', color='rose', lock=True, final=True),
        rules=(
            State.Rule('draft', 'review', 'Submit', key='submit',
                       remark=State.Remark('optional', label='Note', required_when=F('discount') > 15)),
            State.Rule('review', 'done', 'Approve', key='approve', by=('manager', F('approver')), primary=True),
            State.Rule('review', 'draft', 'Send back', key='back', remark='required', ask=('reason',)),
            State.Rule(('draft', 'review', 'done'), 'void', 'Void', key='void', by=('admin',), remark='required'),
            State.Rule('done', 'draft', 'Reopen', key='reopen', by=()),
        ))

    def hook(self, kind):
        ORDER.append((kind, self.status, self.before.status if self.before else None, self.is_new))
        if kind in FAIL:
            if kind != 'validate':
                self.context.session.create(Note, text=f'written by {kind}')
            raise ValidationError({'_form': f'{kind} refused'})

    @state.exit('review')
    def leave_review(self):
        self.hook('exit')

    @state('done')
    def enter_done(self):
        self.hook('enter')
        self.title = self.title + ' (done)'

    @validate
    def check(self):
        if self.status in ('review', 'done') or 'validate' in FAIL:
            self.hook('validate')
        if self.title == 'read only':
            self.title = 'changed'


@pytest.fixture
def app(tmp_path):
    ORDER.clear()
    FAIL.clear()
    app = start(Counting(tmp_path / 'tickets.sqlite3', Registry(Note, Ticket)))
    assert client(app)('POST', '/api/_controls/roles', {'id': 'manager', 'base': 'editor'})[0] == 201
    return app


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


def move(c, url, key, body=None):
    return c('POST', f'{url}/moves/{key}', body or {}, headers=etag(c, url))


def ticket(c, **values):
    status, body, _ = c('POST', '/api/ticket', {'title': 'Printer', **values})
    assert status == 201, body
    return f"/api/ticket/{body['id']}"


def test_rules_check_by_when_and_remarks_and_log_the_rule(app):
    c, editor, manager = client(app), add_user(app, 'editor'), add_user(app, 'manager')
    url = ticket(c)
    # A remark is optional on Submit unless the discount is over 15, then required.
    assert move(editor, url, 'submit', {'remark': 'x' * 1001})[1]['fields'] == {'remark': 'Write a remark of at most 1000 characters'}
    with app.business.context(ADMIN) as context, context._tx.maintenance():
        context._tx.update(Ticket, url.rsplit('/', 1)[1], discount=20)
    assert move(editor, url, 'submit')[1]['fields'] == {'remark': 'Submit needs a remark'}
    status, body, _ = move(editor, url, 'submit', {'remark': '  Big discount  '})
    assert status == 200 and body['status'] == 'review', body
    entry = editor('GET', url + '/journal')[1]['items'][0]
    assert (entry['kind'], entry['rule'], entry['remark'], entry['data']['to']) == ('moved', 'submit', 'Big discount', 'review')
    # by: a custom role or the user a field names; the editor is neither.
    assert move(editor, url, 'approve')[:2] == (403, {'error': 'Your role cannot use Approve'})
    assert [(item['key'], item['allowed'], item.get('primary')) for item in editor('GET', url)[1]['_moves']] == [
        ('approve', False, True), ('back', True, None), ('void', False, None)]
    # A rule with no remark box refuses a remark; a rule that requires one refuses none.
    assert move(manager, url, 'approve', {'remark': 'ok'})[1]['fields'] == {'remark': 'Approve takes no remark'}
    assert move(editor, url, 'back', {'reason': 'Wrong model'})[1]['fields'] == {'remark': 'Send back needs a remark'}
    status, body, _ = move(editor, url, 'back', {'remark': 'Please check', 'reason': 'Wrong model'})
    assert status == 200 and (body['status'], body['reason']) == ('draft', 'Wrong model'), body
    # The user named by a field may use the rule.
    with app.database.transaction() as tx:
        editor_id = tx.connection.execute("SELECT id FROM evlet_users WHERE username='editor'").fetchone()[0]
    with app.business.context(ADMIN) as context, context._tx.maintenance():
        context._tx.update(Ticket, url.rsplit('/', 1)[1], approver=editor_id)
    assert move(editor, url, 'submit', {'remark': 'Again'})[0] == 200
    assert move(editor, url, 'approve')[1]['status'] == 'done'


def test_unknown_wrong_state_final_and_application_rules(app):
    c = client(app)
    url = ticket(c)
    assert move(c, url, 'nothing')[:2] == (404, {'error': 'Move not found'})
    assert move(c, url, 'approve')[:2] == (409, {'error': 'This record cannot move from draft to done'})
    # by=() is for application code only: never offered and refused over HTTP, allowed in trusted Python.
    with app.business.context(ADMIN) as context, context._tx.maintenance():
        context._tx.update(Ticket, url.rsplit('/', 1)[1], status='done')
    assert 'reopen' not in [item['key'] for item in c('GET', url)[1]['_moves']]
    assert 'reopen' not in [rule['key'] for rule in next(m for m in c('GET', '/api/schema')[1]['models'] if m['name'] == 'ticket')['state']['rules']]
    assert move(c, url, 'reopen')[:2] == (403, {'error': 'Reopen is made by the application only'})
    with app.business.context(ADMIN) as context:
        assert context.move(Ticket, url.rsplit('/', 1)[1], 'reopen')['status'] == 'draft'
        context.move(Ticket, url.rsplit('/', 1)[1], 'void', remark='Duplicate')
    assert move(c, url, 'reopen')[:2] == (409, {'error': 'This record is void, which is final; it has no moves'})


def test_the_schema_publishes_steps_and_rules(app):
    entry = next(m for m in client(app)('GET', '/api/schema')[1]['models'] if m['name'] == 'ticket')
    state_schema = entry['state']
    assert [(step['key'], step['label'], step['color'], step['lock'], step['final']) for step in state_schema['steps']] == [
        ('draft', 'Draft', None, False, False), ('review', 'In review', 'amber', True, False), ('done', 'Done', 'green', True, False),
        ('void', 'Void', 'rose', True, True)]
    rules = {rule['key']: rule for rule in state_schema['rules']}
    assert rules['submit']['remark'] == {'mode': 'optional', 'label': 'Note'} and rules['back']['asks'] == ['reason']
    assert rules['approve']['by'] == ['manager'] and rules['approve']['primary'] and rules['void']['from'] == ['draft', 'review', 'done']
    field = next(item for item in entry['fields'] if item['name'] == 'status')
    assert field['state'] and field['colors'] == {'review': 'amber', 'done': 'green', 'void': 'rose'}


def test_hooks_run_in_order_and_see_the_document_before_and_after(app):
    c, manager = client(app), add_user(app, 'manager')
    url = ticket(c)
    assert move(c, url, 'submit')[0] == 200
    ORDER.clear()
    status, body, _ = move(manager, url, 'approve')
    assert status == 200 and body['title'] == 'Printer (done)'
    # @state.exit sees the old state, @state the new one, and @validate the final values after them.
    assert ORDER == [('exit', 'review', 'review', False), ('enter', 'done', 'review', False), ('validate', 'done', 'review', False)]


@pytest.mark.parametrize('kind', ['exit', 'enter', 'validate'])
def test_a_refusal_from_any_hook_rolls_back_the_move_and_what_hooks_wrote(app, kind):
    c, manager = client(app), add_user(app, 'manager')
    url = ticket(c)
    assert move(c, url, 'submit')[0] == 200
    entries = c('GET', url + '/journal')[1]['total']
    FAIL.add(kind)
    status, body, _ = move(manager, url, 'approve')
    assert (status, body['fields']) == (422, {'_form': f'{kind} refused'})
    assert c('GET', url)[1]['status'] == 'review' and app.database.count(Note) == 0
    assert c('GET', url + '/journal')[1]['total'] == entries
    # Python callers get the same error, and their context carries on.
    with app.business.context(ADMIN) as context:
        with pytest.raises(ValidationError, match=f'{kind} refused'):
            context.move(Ticket, url.rsplit('/', 1)[1], 'approve')
        assert context.get(Ticket, url.rsplit('/', 1)[1]).status == 'review'


def test_validate_runs_on_every_save_read_only_with_is_new(app):
    c = client(app)
    FAIL.add('validate')
    assert c('POST', '/api/ticket', {'title': 'x'})[1]['fields'] == {'_form': 'validate refused'}
    assert ORDER[-1] == ('validate', 'draft', None, True)
    FAIL.clear()
    url = ticket(c)
    FAIL.add('validate')
    assert c('PATCH', url, {'title': 'y'}, headers=etag(c, url))[1]['fields'] == {'_form': 'validate refused'}
    assert ORDER[-1] == ('validate', 'draft', 'draft', False)
    FAIL.clear()
    # Assigning in @validate is refused: it only checks.
    status, body, _ = c('PATCH', url, {'title': 'read only'}, headers=etag(c, url))
    assert status == 422 and 'read only' in body['fields']['_form']


def test_locked_steps_keep_versioned_fields_and_take_unversioned_ones(app):
    c = client(app)
    url = ticket(c)
    assert move(c, url, 'submit')[0] == 200
    assert c('PATCH', url, {'title': 'New'}, headers=etag(c, url))[1]['fields'] == {'title': 'This record is review, so it is locked'}
    status, body, _ = c('PATCH', url, {'reason': 'Waiting for the part'}, headers=etag(c, url))
    assert status == 200 and body['reason'] == 'Waiting for the part' and body['title'] == 'Printer'
    assert c('DELETE', url, headers=etag(c, url))[0] == 409


def test_eva_and_mcp_moves_go_through_the_same_checks(app):
    from evlet.auth import KeyAccess
    from evlet.connector import ConnectorError
    from evlet.tools import ApprovalNeeded
    c = client(app)
    add_user(app, 'editor')
    url = ticket(c)
    record_id = url.rsplit('/', 1)[1]
    with app.database.transaction() as tx:
        editor_id = tx.connection.execute("SELECT id FROM evlet_users WHERE username='editor'").fetchone()[0]
    tools = app.business.tools

    def run(caller, arguments):
        """Run a tool call, approving it once as the person when it asks."""
        try:
            return tools.execute(caller, 'change_state', arguments)
        except ApprovalNeeded as asked:
            assert asked.approval['summary']['operation'] == arguments['rule']
            tools.decide(User(editor_id, 'editor', 'editor'), asked.approval['id'], True)
            return tools.execute(caller, 'change_state', arguments)
    for origin in ('eva', 'mcp'):
        caller = User(editor_id, 'editor', 'editor', access=KeyAccess(True, None, None, None, origin))
        # A rule for other roles is refused without asking anyone.
        with pytest.raises(ConnectorError) as refused:
            tools.execute(caller, 'change_state', {'model': 'ticket', 'id': record_id, 'rule': 'void', 'remark': 'x'})
        assert refused.value.status == 403
        run(caller, {'model': 'ticket', 'id': record_id, 'rule': 'submit'})
        assert c('GET', url)[1]['status'] == 'review'
        # Through the tool the remark rules hold too, and the move is logged as the person, via the assistant.
        with pytest.raises(ConnectorError) as refused:
            run(caller, {'model': 'ticket', 'id': record_id, 'rule': 'back', 'values': {'reason': 'x'}})
        assert refused.value.status == 422 and refused.value.fields == {'remark': 'Send back needs a remark'}
        run(caller, {'model': 'ticket', 'id': record_id, 'rule': 'back', 'remark': 'Recheck', 'values': {'reason': 'x'}})
        entry = c('GET', url + '/journal')[1]['items'][0]
        assert (entry['rule'], entry['remark'], entry['actor'], entry.get('via')) == ('back', 'Recheck', editor_id, 'Eva' if origin == 'eva' else entry.get('via'))
        assert c('GET', url)[1]['status'] == 'draft'


def test_a_move_and_its_moves_cost_a_bounded_number_of_statements(app):
    c = client(app)
    url = ticket(c)
    (status, body, _), reading = counted(c, 'GET', url)
    assert status == 200 and [item['key'] for item in body['_moves']] == ['submit', 'void']
    headers = etag(c, url)
    (status, _, _), moving = counted(c, 'POST', url + '/moves/submit', {}, headers=headers)
    assert status == 200
    # The detail reads the rules from the registry; the move adds its rule check, write and one journal entry.
    # Measured 11 and 23 on 13-11-2026; the bound leaves room for small changes, not for a statement per rule.
    assert reading <= 14 and moving <= 28, (reading, moving)


def test_hooks_and_rules_are_checked_when_the_model_is_defined():
    with pytest.raises(ValueError, match='need a State'):
        type('Hooked', (Model,), {'title': Text(), 'go': state('x')(lambda self: None)})
    with pytest.raises(ValueError, match='unknown state'):
        type('Hooked', (Model,), {'status': State(State.Step('a'), State.Step('b')), 'go': state.exit('c')(lambda self: None)})
    with pytest.raises(ValueError, match='take only self'):
        validate(lambda self, other: None)
    with pytest.raises(ValueError, match='at least one state'):
        state()
    for removed in ('before_close', 'changed', 'check_lifecycle'):
        with pytest.raises(ValueError, match='removed in 0.4.6'):
            type('Old', (Model,), {'title': Text(), removed: lambda self, *args: None})
    with pytest.raises(ValueError, match='removed in 0.4.6'):
        type('Old', (Model,), {'title': Text(), 'validate': lambda self: None})
    with pytest.raises(ValueError, match='removed in 0.4.6'):
        type('Old', (Model,), {'title': Text(), 'confirmable': True})
    # A subclass replaces a hook by name, in the base's place.
    class Base(Model):
        status = State(State.Step('a'), State.Step('b'), rules=(State.Rule('a', 'b'),))

        @state('b')
        def first(self): pass

        @state('b')
        def second(self): pass

    class Sub(Base):
        def first(self): pass

    assert [name for _, _, name in Base.__hooks__] == ['first', 'second'] and [name for _, _, name in Sub.__hooks__] == ['second']


def test_field_permissions_hide_rule_conditions_and_reasons(app):
    viewer = add_user(app, 'viewer')
    url = ticket(client(app))
    moves = viewer('GET', url)[1]['_moves']
    assert all(not item['allowed'] for item in moves)
    assert {item['reason'] for item in moves} <= {'Your role cannot use this move', 'You cannot change this record'}


def test_a_journal_from_before_the_state_manager_gains_its_columns(tmp_path):
    import sqlite3
    from evlet import framework, journal
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE TABLE evlet_journal (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, record_id INTEGER NOT NULL, '
                    'kind TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL, prev_hash TEXT, hash TEXT)')
        raw.execute("INSERT INTO evlet_journal (model, record_id, kind, actor, at, data) VALUES ('ticket', 1, 'created', '1', 'now', '{}')")
    database = Database(path, Registry(Note, Ticket))
    with database.schema_transaction() as connection:
        framework._state_manager(connection, database.registry)
        assert set(journal.SCALARS) <= set(connection.columns('evlet_journal'))
        # The step is idempotent, and the old entry keeps its values.
        framework._state_manager(connection, database.registry)
        assert connection.run(journal.qb.select('evlet_journal', 'kind', 'rule')).all()[0][:] == ('created', None)


# Approval levels

APPROVERS = {**ROLES, 'manager': Rule(('read', 'create', 'update')), 'cfo': Rule(('read', 'update')), 'director': Rule(('read', 'update'))}
Level = State.Level


def levels():
    return (Level(by=('manager',), pending='Pending manager approval'),
            Level(by=('cfo', 'manager'), when=F('total') >= 10000, pending='Pending CFO approval'),
            Level(by=('director',), when=F('total') >= 50000, pending='Pending director approval'))


class Purchase(Model):
    permissions = APPROVERS
    total = Integer(default=0)
    status = State(
        State.Step('draft'), State.Step('review', 'In review', lock=True), State.Step('approved', lock=True, final=True),
        rules=(State.Rule('draft', 'review', 'Submit', key='submit', by=('editor', 'manager')),
               State.Rule('review', 'approved', 'Approve', key='approve', levels=levels(), remark='optional'),
               State.Rule('review', 'draft', 'Reject', key='reject', reject=True, remark='required'),
               State.Rule('review', 'draft', 'Withdraw', key='withdraw', by=('editor',))))


class Expense(Model):
    permissions = APPROVERS
    total = Integer(default=0)
    status = State(State.Step('draft'), State.Step('approved', final=True),
                   rules=(State.Rule('draft', 'approved', 'Approve', key='approve', self_approve=True,
                                     levels=(Level(by=('manager',), pending='Pending manager approval'),)),))


@pytest.fixture
def approvals(tmp_path):
    app = start(Counting(tmp_path / 'approvals.sqlite3', Registry(Purchase, Expense)))
    admin = client(app)
    for role in ('manager', 'cfo', 'director'):
        assert admin('POST', '/api/_controls/roles', {'id': role, 'base': 'editor'})[0] == 201
    return app, {role: add_user(app, role) for role in ('editor', 'manager', 'cfo', 'director')}


def submitted(people, total, by='editor'):
    status, body, _ = people[by]('POST', '/api/purchase', {'total': total})
    assert status == 201, body
    url = f"/api/purchase/{body['id']}"
    assert move(people[by], url, 'submit')[0] == 200
    return url


def approval(user, url):
    return next(item for item in user('GET', url)[1]['_moves'] if item['key'] == 'approve')


def test_levels_run_in_order_skip_what_does_not_apply_and_take_one_person_each(approvals):
    app, people = approvals
    url = submitted(people, 20000)
    # The CFO waits for the manager; the manager sees level 1 with its pending text.
    assert approval(people['cfo'], url) == {'key': 'approve', 'to': 'approved', 'label': 'Approve', 'allowed': False,
                                            'reason': 'Waiting for another approver', 'remark': {'mode': 'optional', 'label': None, 'required': False},
                                            'level': 1, 'pending': 'Pending manager approval', 'can_approve': False}
    assert move(people['cfo'], url, 'approve')[0] == 403
    status, body, _ = move(people['manager'], url, 'approve', {'remark': 'Within budget'})
    assert status == 200 and body['status'] == 'review'
    entry = people['manager']('GET', url + '/journal')[1]['items'][0]
    assert (entry['kind'], entry['rule'], entry['level'], entry['remark']) == ('approved', 'approve', 0, 'Within budget')
    # Level 2 names managers too, but the manager approved level 1 already.
    assert approval(people['manager'], url)['level'] == 2 and approval(people['manager'], url)['can_approve'] is False
    assert move(people['manager'], url, 'approve')[0] == 403
    assert approval(people['cfo'], url)['pending'] == 'Pending CFO approval'
    # The director level does not apply under 50,000: the CFO's approval is the last, and moves the record.
    status, body, _ = move(people['cfo'], url, 'approve')
    assert status == 200 and body['status'] == 'approved', body
    moved = people['cfo']('GET', url + '/journal')[1]['items'][0]
    assert (moved['kind'], moved['rule'], moved['level']) == ('moved', 'approve', 1)
    # All three levels when the total is high enough.
    url = submitted(people, 60000)
    for role in ('manager', 'cfo'):
        assert move(people[role], url, 'approve')[1]['status'] == 'review'
    assert approval(people['director'], url)['pending'] == 'Pending director approval'
    assert move(people['director'], url, 'approve')[1]['status'] == 'approved'


def test_levels_are_decided_on_entry_and_approvals_expire_on_leaving(approvals):
    app, people = approvals
    url = submitted(people, 20000)
    record_id = url.rsplit('/', 1)[1]
    assert move(people['manager'], url, 'approve')[0] == 200
    # A change after entry (outside the engine) does not add the director level to this stay.
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(Purchase, record_id, total=90000)
    assert approval(people['cfo'], url)['level'] == 2
    # Withdrawing ends the stay: resubmitted, it starts again at level 1, with levels decided anew.
    assert move(people['editor'], url, 'withdraw')[1]['status'] == 'draft'
    assert move(people['editor'], url, 'submit')[0] == 200
    assert approval(people['manager'], url)['level'] == 1
    for role in ('manager', 'cfo'):
        assert move(people[role], url, 'approve')[1]['status'] == 'review'
    assert approval(people['director'], url)['level'] == 3


def test_only_the_waiting_level_rejects_and_one_rejection_ends_the_round(approvals):
    app, people = approvals
    url = submitted(people, 20000)
    assert move(people['editor'], url, 'reject', {'remark': 'No'})[0] == 403
    assert move(people['cfo'], url, 'reject', {'remark': 'No'})[0] == 403
    reject = next(item for item in people['cfo']('GET', url)[1]['_moves'] if item['key'] == 'reject')
    assert reject['allowed'] is False and reject['reason'] == 'Only the approvers of the waiting level can use this move'
    assert move(people['manager'], url, 'reject')[1]['fields'] == {'remark': 'Reject needs a remark'}
    assert move(people['manager'], url, 'reject', {'remark': 'Quote missing'})[1]['status'] == 'draft'
    assert move(people['editor'], url, 'submit')[0] == 200 and approval(people['manager'], url)['level'] == 1


def test_the_creator_approves_only_with_self_approve(approvals):
    app, people = approvals
    url = submitted(people, 100, by='manager')
    assert approval(people['manager'], url)['can_approve'] is False and move(people['manager'], url, 'approve')[0] == 403
    expense = people['manager']('POST', '/api/expense', {'total': 5})[1]
    expense_url = f"/api/expense/{expense['id']}"
    assert move(people['manager'], expense_url, 'approve')[1]['status'] == 'approved'


def test_awaiting_my_approval_lists_what_each_approver_may_approve_now(approvals):
    app, people = approvals
    first, second = submitted(people, 20000), submitted(people, 100)
    move(people['manager'], first, 'approve')

    def waiting(user):
        return sorted(item['id'] for item in user('GET', '/api/purchase?awaiting=me')[1]['items'])
    assert waiting(people['manager']) == [second.rsplit('/', 1)[1]]
    assert waiting(people['cfo']) == [first.rsplit('/', 1)[1]]
    assert waiting(people['director']) == [] and waiting(people['editor']) == []
    assert people['cfo']('GET', '/api/purchase?awaiting=you')[0] == 422


def test_the_schema_lists_levels_and_bad_declarations_are_refused():
    entry = next(m for m in State.Rule('a', 'b', levels=levels()).levels if m.pending.startswith('Pending CFO'))
    assert entry.roles == ('cfo', 'manager')
    for bad in (lambda: Level(by=(), pending='x'), lambda: Level(by=('cfo',)), lambda: State.Rule('a', 'b', self_approve=True),
                lambda: State.Rule('a', 'b', levels=levels(), reject=True), lambda: State.Rule('a', 'b', levels=levels(), by=('cfo',)),
                lambda: State.Rule('a', 'b', levels=('cfo',))):
        with pytest.raises(ValueError):
            bad()
    quiet = type('Quiet', (Model,), {'Meta': type('Meta', (), {'journal': None}),
                                     'status': State(State.Step('a'), State.Step('b'), rules=(State.Rule('a', 'b', levels=levels()[:1]),))})
    with pytest.raises(ValueError, match='approval levels are kept in the journal'):
        Registry(quiet)


def test_moves_with_an_approval_waiting_cost_a_bounded_number_of_statements(approvals):
    app, people = approvals
    url = submitted(people, 60000)
    move(people['manager'], url, 'approve')
    (status, body, _), reading = counted(people['cfo'], 'GET', url)
    assert status == 200 and approval(people['cfo'], url)['can_approve']
    # Measured 15 on 13-11-2026: the record's stay in the journal is one statement, the creator one more, for
    # all three rules out of the state together.
    assert reading <= 17, reading


# Releases, lazy snapshots and versions

RELEASED = []


class BillLine(Child):
    item = Text(default='')
    qty = Integer(default=1)


class Bill(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'release')), 'editor': Rule(('read', 'create', 'update')),
                   'viewer': Rule(('read',), read_fields=('title', 'status'))}
    title = Text(default='')
    secret = Text(default='')
    note = Text(versioned=False)
    lines = Table(BillLine)
    status = State(
        State.Step('draft'), State.Step('issued', lock=True, release=True), State.Step('void', lock=True, final=True),
        rules=(State.Rule('draft', 'issued', 'Issue', key='issue'), State.Rule('issued', 'draft', 'Reopen', key='reopen', remark='required'),
               State.Rule('issued', 'void', 'Void', key='void', remark='required')))

    @release
    def stamp(self):
        RELEASED.append((self.release.number, self.release.previous))
        self.note = f'released v{self.release.number}'


@pytest.fixture
def bills(tmp_path):
    RELEASED.clear()
    app = start(Database(tmp_path / 'bills.sqlite3', Registry(Bill)))
    c = client(app)
    status, body, _ = c('POST', '/api/bill', {'title': 'Desk', 'secret': 's1', 'children': {'lines': [{'item': 'a'}]}})
    assert status == 201, body
    return app, c, f"/api/bill/{body['id']}"


def rows(app, url, snapshot=None):
    with app.database.transaction() as tx:
        query = 'SELECT number, snapshot, drifted, data FROM evlet_releases WHERE record_id=?' + ('' if snapshot is None else f' AND snapshot={snapshot}')
        return [tuple(row) for row in tx.connection.execute(query + ' ORDER BY number, snapshot', (int(RecordId(url.rsplit('/', 1)[1])),))]


def test_entering_a_release_step_releases_and_snapshots_only_on_leaving(bills):
    app, c, url = bills
    body = move(c, url, 'issue')[1]
    # v0: the @release hook ran with no previous version and set an unversioned field.
    assert RELEASED == [(0, None)] and body['note'] == 'released v0'
    assert body['_version'] == {'number': 0, 'released': True, 'state': 'issued'}
    assert [row[:2] for row in rows(app, url)] == [(0, 0)]
    issued = c('GET', url + '/journal')[1]['items'][0]
    assert issued['data']['release'] == 0 and 'base' not in issued
    # Leaving the release snapshots it, once; the content is unchanged, so it still reads as v0.
    body = move(c, url, 'reopen', {'remark': 'Price'})[1]
    assert [row[:2] for row in rows(app, url)] == [(0, 0), (0, 1)] and body['_version']['released'] is True
    assert [item['kind'] for item in c('GET', url + '/journal')[1]['items'][:2]] == ['moved', 'snapshot']
    # Issued again unchanged: no new release, no @release.
    assert move(c, url, 'issue')[1]['_version'] == {'number': 0, 'released': True, 'state': 'issued'} and RELEASED == [(0, None)]
    move(c, url, 'reopen', {'remark': 'Again'})
    assert len(rows(app, url, snapshot=1)) == 1
    # An unversioned change does not make the document modified; a versioned one does.
    assert c('PATCH', url, {'note': 'checked'}, headers=etag(c, url))[1]['_version']['released'] is True
    assert c('PATCH', url, {'title': 'Desk 2'}, headers=etag(c, url))[1]['_version'] == {'number': 0, 'released': False, 'state': 'issued'}
    body = move(c, url, 'issue')[1]
    assert body['_version'] == {'number': 1, 'released': True, 'state': 'issued'}
    assert RELEASED[-1][0] == 1 and RELEASED[-1][1]['title'] == 'Desk' and 'note' not in RELEASED[-1][1] and 'status' not in RELEASED[-1][1]
    # A document with a release cannot be deleted.
    move(c, url, 'reopen', {'remark': 'x'})
    assert c('DELETE', url, headers=etag(c, url))[0] == 409


def test_a_manual_release_and_the_first_edit_after_it(bills):
    app, c, url = bills
    editor = add_user(app, 'editor')
    assert editor('POST', url + '/release', {}, headers=etag(editor, url))[0] == 403
    status, body, _ = c('POST', url + '/release', {'remark': 'Quote sent'}, headers=etag(c, url))
    assert status == 200 and body['_version'] == {'number': 0, 'released': True, 'state': 'draft'} and body['status'] == 'draft'
    entry = c('GET', url + '/journal')[1]['items'][0]
    assert (entry['kind'], entry['base'], entry['remark']) == ('released', 0, 'Quote sent')
    assert c('POST', url + '/release', {}, headers=etag(c, url))[:2] == (409, {'error': 'Nothing changed since the last release'})
    # The first versioned edit keeps v0 first; later edits add nothing.
    c('PATCH', url, {'note': 'n'}, headers=etag(c, url))
    assert rows(app, url, snapshot=1) == []
    c('PATCH', url, {'title': 'Chair'}, headers=etag(c, url))
    c('PATCH', url, {'title': 'Sofa'}, headers=etag(c, url))
    snapshots = rows(app, url, snapshot=1)
    assert len(snapshots) == 1 and json.loads(snapshots[0][3])['title'] == 'Desk'


def test_drift_is_marked_and_logged(bills, caplog):
    app, c, url = bills
    move(c, url, 'issue')
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(Bill, url.rsplit('/', 1)[1], title='Changed outside')
    with caplog.at_level('WARNING', logger='evlet.statemanager'):
        move(c, url, 'reopen', {'remark': 'Fix'})
    assert rows(app, url, snapshot=1)[0][2] == 1 and 'changed outside the engine' in caplog.text
    entry = next(item for item in c('GET', url + '/journal')[1]['items'] if item['kind'] == 'snapshot')
    assert entry['data'] == {'number': 0, 'drifted': True}


def test_versions_list_one_version_and_diff_with_field_permissions(bills):
    app, c, url = bills
    viewer = add_user(app, 'viewer')
    move(c, url, 'issue')
    move(c, url, 'reopen', {'remark': 'x'})
    c('PATCH', url, {'title': 'Desk 2', 'secret': 's2', 'children': {'lines': [{'item': 'b'}]}}, headers=etag(c, url))
    move(c, url, 'issue')
    listed = c('GET', url + '/versions')[1]['items']
    assert [(item['number'], item['state']) for item in listed] == [(1, 'issued'), (0, 'issued')]
    first = c('GET', url + '/versions/0')[1]['document']
    assert (first['title'], first['secret'], [line['item'] for line in first['children']['lines']]) == ('Desk', 's1', ['a'])
    # Field permissions apply to old versions too: the viewer reads the title only.
    assert set(viewer('GET', url + '/versions/0')[1]['document']) == {'id', 'title', 'children'}
    changes = c('GET', url + '/versions/0/diff/1')[1]['changes']
    assert changes['fields'] == {'secret': {'old': 's1', 'new': 's2'}, 'title': {'old': 'Desk', 'new': 'Desk 2'}}
    assert [row['item'] for row in changes['rows']['lines']['added']] == ['b'] and len(changes['rows']['lines']['removed']) == 1
    assert c('GET', url + '/versions/1/diff/current')[1]['changes'] == {}
    assert c('GET', url + '/versions/7')[0] == 404 and c('GET', url + '/versions/x')[0] == 422


def test_the_framework_step_converts_old_rows_and_backfills_v0(tmp_path):
    import sqlite3
    from evlet import framework
    from evlet.migrations import SchemaError
    app = start(Database(tmp_path / 'old.sqlite3', Registry(Bill)))
    c = client(app)
    issued, draft = (c('POST', '/api/bill', {'title': title})[1]['id'] for title in ('Issued before', 'Revised before'))
    with app.database.transaction() as tx, tx.maintenance():
        tx.update(Bill, issued, status='issued')
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as raw:
        # A 0.4.4 database: no releases yet, a confirmable lifecycle row and a revision.
        raw.execute('DROP TABLE evlet_releases')
        raw.execute('CREATE TABLE evlet_document_lifecycle (model TEXT NOT NULL, record_id INTEGER NOT NULL, state TEXT NOT NULL, '
                    'revision INTEGER NOT NULL, confirmation_id TEXT, PRIMARY KEY (model, record_id))')
        raw.execute('CREATE TABLE evlet_document_revisions (model TEXT NOT NULL, record_id INTEGER NOT NULL, revision INTEGER NOT NULL, '
                    'execution_id TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL, '
                    'PRIMARY KEY (model, record_id, revision))')
        raw.execute("INSERT INTO evlet_document_lifecycle VALUES ('bill', ?, 'draft', 1, 'x')", (int(RecordId(draft)),))
        raw.execute("INSERT INTO evlet_document_revisions VALUES ('bill', ?, 1, 'e1', 'now', '1', ?, 'h')",
                    (int(RecordId(draft)), json.dumps({'document': {'id': draft, 'title': 'Revised before', 'secret': '', 'note': 'n', 'children': {}}})))
    with app.database.schema_transaction() as connection:
        framework._state_manager(connection, app.database.registry)
        assert not connection.table_exists('evlet_document_revisions') and not connection.table_exists('evlet_document_lifecycle')
    assert [row[:2] for row in rows(app, f'/api/bill/{issued}')] == [(0, 0)]
    assert [row[:2] for row in rows(app, f'/api/bill/{draft}')] == [(0, 0), (0, 1)]
    assert 'note' not in json.loads(rows(app, f'/api/bill/{draft}', snapshot=1)[0][3])
    assert c('GET', f'/api/bill/{issued}')[1]['_version'] == {'number': 0, 'released': True, 'state': 'issued'}
    # A lifecycle state the model's State does not have is refused, so nothing is lost.
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE TABLE evlet_document_lifecycle (model TEXT, record_id INTEGER, state TEXT, revision INTEGER, confirmation_id TEXT)')
        raw.execute("INSERT INTO evlet_document_lifecycle VALUES ('bill', 1, 'confirmed', 1, 'x')")
    with pytest.raises(SchemaError, match='no State with those steps'):
        with app.database.schema_transaction() as connection:
            framework._state_manager(connection, app.database.registry)


# Restore

class Clause(Child):
    text = Text(default='')


class Contract(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'restore')), 'editor': Rule(('read', 'create', 'update'))}
    code = Text(unique=True)
    title = Text(default='')
    note = Text(versioned=False)
    days = Integer(default=30)
    clauses = Table(Clause)
    status = State(State.Step('draft'), State.Step('signed', lock=True, release=True),
                   rules=(State.Rule('draft', 'signed', 'Sign', key='sign'), State.Rule('signed', 'draft', 'Reopen', key='reopen')))


class Delivery(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    contract = Link(Contract)


@pytest.fixture
def contracts(tmp_path):
    app = start(Database(tmp_path / 'contracts.sqlite3', Registry(Contract, Delivery)))
    c = client(app)
    status, body, _ = c('POST', '/api/contract', {'code': 'C-1', 'title': 'First', 'note': 'n0',
                                                  'children': {'clauses': [{'text': 'a'}, {'text': 'b'}]}})
    assert status == 201, body
    url = f"/api/contract/{body['id']}"
    move(c, url, 'sign')
    move(c, url, 'reopen')
    return app, c, url


def restore(c, url, body):
    return c('POST', url + '/restore', body, headers=etag(c, url))


def test_restore_moves_forward_with_the_release_content(contracts):
    app, c, url = contracts
    first = c('GET', url)[1]['children']['clauses']
    c('PATCH', url, {'title': 'Second', 'note': 'n1', 'days': 60, 'children': {'clauses': [{'id': first[1]['id']}, {'text': 'c'}]}},
      headers=etag(c, url))
    status, body, _ = restore(c, url, {'version': 0, 'remark': 'Back to the signed terms'})
    assert status == 200, body
    assert (body['title'], body['days'], body['note']) == ('First', 30, 'n1')
    # The clause deleted since comes back under its own id; the one added since is gone.
    assert [(row['id'], row['text']) for row in body['children']['clauses']] == [(first[0]['id'], 'a'), (first[1]['id'], 'b')]
    # A restore is a change after the latest release (v0 here), so the record reads as modified, with v0's content.
    assert body['_restore'] == {'version': 0, 'dropped': [], 'kept': []} and body['_version'] == {'number': 0, 'released': False, 'state': 'signed'}
    entry = c('GET', url + '/journal')[1]['items'][0]
    assert (entry['kind'], entry['remark'], entry['data']['number']) == ('restored', 'Back to the signed terms', 0)
    # Numbers only increase: v0's content signed again is no new release; changed and signed it is v1.
    c('PATCH', url, {'title': 'Third'}, headers=etag(c, url))
    assert move(c, url, 'sign')[1]['_version']['number'] == 1


def test_restore_refusals(contracts):
    app, c, url = contracts
    editor = add_user(app, 'editor')
    assert restore(editor, url, {'version': 0, 'remark': 'x'})[0] == 403
    assert restore(c, url, {'version': 0})[1]['fields'] == {'remark': 'Restore needs a remark'}
    assert restore(c, url, {'version': 4, 'remark': 'x'})[0] == 404
    assert restore(c, url, {'version': 'v0', 'remark': 'x'})[0] == 422
    move(c, url, 'sign')
    assert restore(c, url, {'version': 0, 'remark': 'x'})[:2] == (409, {'error': 'Move back to an editable state first'})
    move(c, url, 'reopen')
    c('POST', '/api/delivery', {'contract': url.rsplit('/', 1)[1]})
    status, body, _ = restore(c, url, {'version': 0, 'remark': 'x'})
    assert status == 409 and body['error'].startswith('Restore is blocked while other records depend on this one: Delivery')


def test_restore_keeps_a_unique_value_taken_since_and_reports_fields(contracts):
    app, c, url = contracts
    c('PATCH', url, {'code': 'C-2'}, headers=etag(c, url))
    c('POST', '/api/contract', {'code': 'C-1'})
    body = restore(c, url, {'version': 0, 'remark': 'x'})[1]
    assert body['code'] == 'C-2' and body['_restore']['kept'] == ['code']
    # A field the release held that the model lost is dropped and listed; one added since takes its default.
    from evlet.statemanager import restorable
    with app.business.context(ADMIN) as context:
        current = context._tx.get(Contract, url.rsplit('/', 1)[1])
        current._rows = {}
        values, dropped = restorable(Contract, {'id': 'x', 'gone': 1, 'title': 'T', 'children': {}}, current, app.database.registry)
    assert dropped == ['gone'] and values['title'] == 'T' and values['days'] == 30 and 'note' not in values


# Rules changed on an instance (evlet_overrides kinds "state" and "rule")

def overrides(c, *changes):
    return c('POST', '/api/_controls/overrides', {'changes': [{'kind': kind, 'key': key, 'value': value} for kind, key, value in changes]})


def schema_state(c):
    return next(m for m in c('GET', '/api/schema')[1]['models'] if m['name'] == 'ticket')['state']


def test_labels_colours_and_code_rules_change_by_key(app):
    c = client(app)
    status, body, _ = overrides(c, ('state', 'Ticket.review', {'label': 'Checking', 'color': 'blue'}),
                                ('rule', 'Ticket.submit', {'label': 'Send for checking', 'remark': 'required'}),
                                ('rule', 'Ticket.void', {'off': True}))
    assert status == 200, body
    state_schema = schema_state(c)
    assert next(step for step in state_schema['steps'] if step['key'] == 'review') == {
        'key': 'review', 'label': 'Checking', 'color': 'blue', 'lock': True, 'release': False, 'final': False}
    assert [rule['key'] for rule in state_schema['rules']] == ['submit', 'approve', 'back']
    url = ticket(c)
    assert [(item['key'], item['label']) for item in c('GET', url)[1]['_moves']] == [('submit', 'Send for checking')]
    assert move(c, url, 'void', {'remark': 'x'})[0] == 404
    assert move(c, url, 'submit')[1]['fields'] == {'remark': 'Send for checking needs a remark'}
    assert move(c, url, 'submit', {'remark': 'Ready'})[0] == 200
    # The move logs the rule key and the rule set version it used.
    with app.database.transaction() as tx:
        version = tx.connection.execute('SELECT version FROM evlet_config').fetchone()[0]
        assert tuple(tx.connection.execute("SELECT rule, rules_version FROM evlet_journal WHERE kind='moved' ORDER BY id DESC").fetchone()) == ('submit', version)
    # Reset to code: delete the overrides.
    overrides(c, ('rule', 'Ticket.void', None), ('rule', 'Ticket.submit', None), ('state', 'Ticket.review', None))
    assert [rule['key'] for rule in schema_state(c)['rules']] == ['submit', 'approve', 'back', 'void']


def test_an_added_rule_with_a_text_condition_and_approval_levels(app):
    c, manager = client(app), add_user(app, 'manager')
    status, body, _ = overrides(c, ('rule', 'Ticket.hold', {'from': ['review'], 'to': 'draft', 'label': 'Hold', 'by': ['manager'],
                                                            'when': 'discount > 10'}),
                                ('rule', 'Ticket.approve', {'levels': [{'by': ['manager'], 'pending': 'Pending manager approval'},
                                                                       {'by': ['admin'], 'when': 'discount >= 50', 'pending': 'Pending admin'}]}))
    assert status == 200, body
    url = ticket(c, discount=5)
    move(c, url, 'submit')
    hold = next(item for item in manager('GET', url)[1]['_moves'] if item['key'] == 'hold')
    assert hold['allowed'] is False and hold['reason'] == 'The condition for this move is not met'
    assert approval(manager, url)['pending'] == 'Pending manager approval'
    # The admin level does not apply under 50, so the manager's approval moves it.
    assert move(manager, url, 'approve')[1]['status'] == 'done'


@pytest.mark.parametrize('change, message', [
    (('rule', 'Ticket.ghost', {'from': ['draft'], 'to': 'nowhere'}), 'unknown state'),
    (('rule', 'Ticket.undo', {'from': ['void'], 'to': 'draft'}), 'leaves a final state'),
    (('rule', 'Ticket.submit', {'by': ['nobody']}), 'not a role of this instance'),
    (('rule', 'Ticket.submit', {'when': 'colour == "red"'}), "colour isn't a field"),
    (('rule', 'Ticket.approve', {'levels': [{'by': ['manager']}]}), 'pending text'),
    (('rule', 'Ticket.missing', {'label': 'x'}), 'names no code rule'),
    (('rule', 'Ticket.submit', {'from': ['review']}), 'change '),
    (('state', 'Ticket.nowhere', {'label': 'x'}), 'not a step'),
    (('state', 'Ticket.review', {'lock': False}), 'label'),
    (('rule', 'Nobody.submit', {'label': 'x'}), 'name a model with a State'),
])
def test_a_rule_set_that_does_not_hold_is_refused_on_save(app, change, message):
    status, body, _ = overrides(client(app), change)
    assert status == 422 and message in body['fields']['changes.0'], body


def test_turning_off_every_rule_out_of_a_state_is_refused(app):
    status, body, _ = overrides(client(app), ('rule', 'Ticket.submit', {'off': True}), ('rule', 'Ticket.void', {'off': True}))
    assert status == 422 and 'no rule leaves draft' in body['fields']['changes.0'], body


def test_orphans_are_ignored_and_other_processes_follow_the_version(app, tmp_path):
    from evlet import Application, LocalAuth
    c = client(app)
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_overrides (kind, key, value) VALUES ('rule', 'Ticket.retired', '{\"label\": \"Old\"}')")
        tx.connection.execute('UPDATE evlet_config SET version = version + 1')
    listed = c('GET', '/api/_controls/overrides')[1]
    assert any(problem['key'] == 'Ticket.retired' and 'orphaned' in problem['error'] for problem in listed['problems'])
    assert [rule['key'] for rule in schema_state(c)['rules']] == ['submit', 'approve', 'back', 'void']
    # A second process on the same database picks up a change on its next request.
    other = Application(app.database, auth=LocalAuth(app.database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    other.initialize()
    assert overrides(c, ('rule', 'Ticket.submit', {'label': 'Propose'}))[0] == 200
    assert next(rule['label'] for rule in schema_state(client(other))['rules'] if rule['key'] == 'submit') == 'Propose'
