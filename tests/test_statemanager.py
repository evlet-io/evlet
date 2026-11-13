"""The state manager (evlet/statemanager.py): steps, rules, remarks, asks and the four hooks, on real SQLite
through the ASGI routes, Context, and the Eva and MCP tool route."""

import pytest

from evlet import Database, Integer, Model, Registry, Rule, State, Text, ValidationError, state, validate
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
        framework._state_manager(connection)
        assert set(journal.SCALARS) <= set(connection.columns('evlet_journal'))
        # The step is idempotent, and the old entry keeps its values.
        framework._state_manager(connection)
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
