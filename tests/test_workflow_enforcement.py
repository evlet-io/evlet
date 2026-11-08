"""Workflow enforcement (phase 3): the protected-operation gate, stage edit rules, approval invalidation,
required feedback, receipts and the HTTP routes, on real SQLite and through the ASGI application."""
import base64
import json
import threading

import pytest

from evlet import Child, ConflictError, Database, Integer, Model, Move, Registry, Rule, State, Table, Text, User, WorkflowOptions, action
from evlet.auth import AccessError, password_hash
from evlet.models import ValidationError
from evlet.workflow_engine import WorkflowError
from test_business import PASSWORD, http
from test_workflow import (ADMIN, APPROVED, DRAFT, ENDS, MANAGER, MODELS, REJECTED, REVIEW, WfOrder, act, build, decide, definition,
                           draft, go, group, order, publish, review, role, single, status)


class WfCode(Child):
    code = Text(default='')


class WfItem(Child):
    label = Text(default='')
    codes = Table(WfCode)


class WfTicket(Model):
    class Meta:
        calculation_language = 'finite-v1'
        workflow = WorkflowOptions(protects=('move:issued', 'action:ship'), unmatched='allow', harmless=('note',))

    title = Text(default='')
    note = Text(default='')
    status = State(('draft', 'issued', 'closed'), moves=(Move('draft', 'issued', 'Issue', roles=('admin', 'editor')),
                                                          Move('draft', 'closed', 'Close', roles=('admin', 'editor'))))
    items = Table(WfItem)
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')), 'editor': Rule(('read', 'create', 'update')),
                   'viewer': REVIEW, 'finance': REVIEW, 'procurement': REVIEW, 'it': REVIEW, 'ops': REVIEW, 'director': REVIEW,
                   'clerk': Rule(('read',)), 'buyer': Rule(('read', 'create', 'update', 'review'))}

    @action('Ship', roles=('admin', 'editor'))
    def ship(self):
        self.title = self.title + ' (shipped)'

    @action('Retitle', roles=('admin', 'editor'), inputs={'title': Text(required=True)})
    def retitle(self, title):
        self.title = title

    @action('Confirm an order', roles=('admin', 'editor'), inputs={'order': Integer(required=True)})
    def confirm_order(self, order):
        # A custom action that tries to confirm a protected document on its own.
        self.context.confirm(WfOrder, order)


@pytest.fixture(scope='module')
def hashed():
    return password_hash(PASSWORD)


@pytest.fixture
def app(tmp_path, hashed):
    app = build(Database(tmp_path / 'enforcement.sqlite3', Registry(*MODELS, WfTicket)), attachments=(WfOrder, WfTicket))
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_users SET password=?', (hashed,))
    return app


def login(app, name):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': name, 'password': PASSWORD})
    assert status == 200, data
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']

    def call(method, path, body=None, **options):
        return http(app, method, path, body, cookie=cookie, csrf=csrf, **options)
    call.cookie, call.csrf = cookie, csrf
    return call


def etag(c, path):
    status, body, headers = c('GET', path)
    assert status == 200, body
    return headers[b'etag']


def send(c, path, action, body, *, key='key-00000001', token=None, match=None):
    """POST a workflow action with a fresh ETag and token unless given."""
    record = path.rsplit('/workflow', 1)[0] if '/workflow' in path else path
    token = token if token is not None else c('GET', record + '/workflow')[1]['token']
    headers = {b'if-match': match if match is not None else etag(c, record)}
    return c('POST', f'{record}/workflow/{action}', {'key': key, 'token': token, **body}, headers=headers)


def confirm(app, name, record):
    with app.business.context(app.people[name] if name != 'admin' else ADMIN) as context:
        return context.confirm(WfOrder, record)


def required(call, code='WORKFLOW_REQUIRED'):
    with pytest.raises(WorkflowError) as caught:
        call()
    assert caught.value.code == code, caught.value.message
    return caught.value.message


def approved_order(app, **values):
    publish(app, MANAGER)
    record = order(app, **values)
    decide(app, 'rita', record, kind='submit')
    decide(app, 'mona', record, kind='approve')
    return record


def journal_kinds(app, record, model=WfOrder):
    with app.database.transaction() as tx:
        return [row[0] for row in tx.connection.execute('SELECT kind FROM evlet_journal WHERE model=? AND record_id=? ORDER BY id',
                                                        (app.database.registry.name_for(model), record)).fetchall()]


def counts(app):
    with app.database.transaction() as tx:
        return {table: tx.connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                for table in ('evlet_journal', 'evlet_workflow_decisions', 'evlet_workflow_runs', 'evlet_workflow_attempts', 'evlet_operations')}


# The gate

def test_confirm_needs_approval_and_uses_the_evidence_once(app):
    publish(app, MANAGER)
    record = order(app, total='5')
    assert 'Send this wf order for review first' in required(lambda: confirm(app, 'rita', record))
    decide(app, 'rita', record, kind='submit')
    assert 'awaiting review' in required(lambda: confirm(app, 'rita', record))
    decide(app, 'mona', record, kind='approve')
    with app.business.context(app.people['rita']) as context:
        assert context.workflow(WfOrder, record).status()['evidence'] == 'valid'
        assert context.confirm(WfOrder, record)['_lifecycle']['state'] == 'confirmed'
        found = context.workflow(WfOrder, record).status()
    assert found['evidence'] == 'used' and found['status'] == 'approved'
    # The confirmation snapshot names the run, attempt and decisions that approved it, not a journal entry.
    with app.business.context(ADMIN, readonly=True) as context:
        approval = context.snapshot(WfOrder, record, revision=1)['approval']
    assert approval['workflow']['run'] == found['run'] and approval['workflow']['attempt'] == 1
    assert len(approval['workflow']['decisions']) == 2
    # Approval for confirm is never reused for another protected operation.
    with pytest.raises(WorkflowError) as caught:
        with app.business.context(ADMIN) as context:
            context.cancel(WfOrder, record, reason='Wrong supplier')
    assert caught.value.code in ('WORKFLOW_NONE', 'WORKFLOW_REQUIRED')


def test_a_failed_confirm_rolls_back_and_keeps_the_evidence(app):
    record = approved_order(app, total='5')
    with app.database.transaction() as tx:
        tx.connection.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON evlet_document_revisions BEGIN SELECT RAISE(ABORT, 'posting failed'); END")
    with pytest.raises(ConflictError):
        confirm(app, 'rita', record)
    assert status(app, record)['status'] == 'approved'
    with app.database.transaction() as tx:
        assert tx.connection.execute('SELECT evidence_used_at FROM evlet_workflow_attempts').fetchall()[-1][0] is None
        tx.connection.execute('DROP TRIGGER fail_revision')
    assert confirm(app, 'rita', record)['_lifecycle']['state'] == 'confirmed'


def test_old_record_approval_events_grant_nothing(app):
    publish(app, MANAGER)
    record = order(app)
    with app.business.context(ADMIN) as context:
        context.record_approval(WfOrder, record, approved=True, reason='Approved by email')
    required(lambda: confirm(app, 'admin', record))


def test_a_custom_action_cannot_confirm_a_protected_document(app):
    publish(app, MANAGER)
    record = order(app)
    with app.business.context(ADMIN) as context:
        ticket = context.create(WfTicket, title='T')['id']
    with pytest.raises(WorkflowError) as caught:
        with app.business.context(ADMIN) as context:
            context.action(WfTicket, ticket, 'confirm_order', order=int(record))
    assert caught.value.code == 'WORKFLOW_REQUIRED'
    with app.business.context(ADMIN) as context:
        assert context.get(WfTicket, ticket).title == 'T'
        assert context.get(WfOrder, record)._lifecycle.state == 'draft'
    # Raw session writes to a workflow model are refused; maintenance stays the audited boundary.
    with pytest.raises(ValueError, match='write it through Context'):
        with app.database.transaction() as tx:
            tx.update(WfTicket, ticket, title='raw')


def test_scheduled_and_system_contexts_meet_the_gate(app):
    publish(app, MANAGER)
    record = order(app)
    for source in ('Scheduler', 'System', 'Integration'):
        with pytest.raises(WorkflowError) as caught:
            with app.business.context(User(0, 'schedule', 'admin'), source=source) as context:
                context.confirm(WfOrder, record)
        assert caught.value.code == 'WORKFLOW_REQUIRED'


def ticket_definition(protects, stages, **settings):
    kind, _, name = protects.partition(':')
    return {'schema': 'evlet.workflow/1', 'name': f'Ticket {protects}', 'model': 'wfticket',
            'protects': {'kind': 'move', 'to': name} if kind == 'move' else {'kind': 'action', 'name': name},
            'applies_when': None, 'start': DRAFT, 'settings': settings, 'notes': '', 'stages': stages}


def publish_ticket(app, value):
    engine = app.business.workflows
    with app.database.transaction() as tx:
        saved = engine.save_draft(tx, ADMIN, WfTicket, value)
        return engine.publish(tx, ADMIN, saved['id'], revision=saved['revision'])


def ticket_decide(app, name, record, protects, **options):
    with app.database.transaction() as tx:
        return app.business.workflows.decide(tx, app.people[name], WfTicket, record, protects=protects, **options)


def test_moves_and_actions_are_gated_when_listed(app):
    rita = app.people['rita']
    with app.business.context(rita) as context:
        first, second, third = (context.create(WfTicket, title=title)['id'] for title in 'ABC')
        # unmatched="allow": with no published workflow the operations run as they do today.
        assert context.move(WfTicket, first, 'issued')['status'] == 'issued'
        assert context.action(WfTicket, first, 'ship')['title'] == 'A (shipped)'
    publish_ticket(app, ticket_definition('move:issued', [draft('stg_mngr01'), single('stg_mngr01', 'Manager', role('viewer')), *ENDS]))
    with pytest.raises(WorkflowError) as caught:
        with app.business.context(rita) as context:
            context.move(WfTicket, second, 'issued')
    assert caught.value.code == 'WORKFLOW_REQUIRED' and caught.value.message.startswith('Moving to issued needs approval')
    # Unlisted moves are untouched.
    with app.business.context(rita) as context:
        assert context.move(WfTicket, third, 'closed')['status'] == 'closed'
    ticket_decide(app, 'rita', second, 'move:issued', kind='submit')
    ticket_decide(app, 'mona', second, 'move:issued', kind='approve')
    with app.business.context(rita) as context:
        assert context.move(WfTicket, second, 'issued')['status'] == 'issued'
    # A protected action: the action's own role check runs first, then the gate.
    publish_ticket(app, ticket_definition('action:ship', [draft('stg_mngr01'), single('stg_mngr01', 'Manager', role('viewer')), *ENDS]))
    with pytest.raises(AccessError):
        with app.business.context(app.people['mona']) as context:
            context.action(WfTicket, second, 'ship')
    with pytest.raises(WorkflowError):
        with app.business.context(rita) as context:
            context.action(WfTicket, second, 'ship')
    ticket_decide(app, 'rita', second, 'action:ship', kind='submit')
    ticket_decide(app, 'mona', second, 'action:ship', kind='approve')
    with app.business.context(rita) as context:
        assert context.action(WfTicket, second, 'ship')['title'] == 'B (shipped)'
        with pytest.raises(WorkflowError):
            context.action(WfTicket, second, 'ship')


def test_the_gate_answers_over_http_with_a_code(app):
    publish(app, MANAGER)
    record = order(app)
    rita = login(app, 'rita')
    path = f'/api/wforder/{record}'
    status_, body, _ = rita('POST', path + '/confirm', {}, headers={b'if-match': etag(rita, path)})
    assert status_ == 409 and body['code'] == 'WORKFLOW_REQUIRED' and 'needs approval' in body['error']
    # Existing errors keep their shape: a stale ETag has no code.
    status_, body, _ = rita('PATCH', path, {'title': 'x'}, headers={b'if-match': b'"stale"'})
    assert status_ == 409 and set(body) == {'error'}


# Stage edit rules

def test_a_review_stage_locks_the_document_for_everyone(app):
    publish(app, MANAGER)
    record = order(app, lines=[{'amount': '5'}])
    decide(app, 'rita', record, kind='submit')
    for name, values in (('rita', {'title': 'Changed'}), ('rita', {'remarks': 'Harmless, but locked here'}),
                         ('rita', {'children': {'lines': []}}), ('admin', {'title': 'Admin too'})):
        user = ADMIN if name == 'admin' else app.people[name]
        with pytest.raises(WorkflowError) as caught:
            with app.business.context(user) as context:
                context.update(WfOrder, record, **values)
        assert caught.value.code == 'WORKFLOW_LOCKED' and 'Manager Review' in caught.value.message
    with pytest.raises(WorkflowError):
        with app.business.context(ADMIN) as context:
            context.delete(WfOrder, record)
    # Attachments over HTTP, and the reviewer still cannot edit (review is not update).
    rita = login(app, 'rita')
    status_, body, _ = rita('POST', f'/api/wforder/{record}/attachments', {'name': 'quote.pdf', 'content': base64.b64encode(b'%PDF').decode()})
    assert status_ == 409 and body['code'] == 'WORKFLOW_LOCKED'
    mona = login(app, 'mona')
    path = f'/api/wforder/{record}'
    assert mona('PATCH', path, {'title': 'x'}, headers={b'if-match': etag(mona, path)})[0] == 403
    assert status(app, record)['status'] == 'active'


def editable(stage_rule):
    stages = [draft('stg_mngr01'), single('stg_mngr01', 'Manager Review', {'kind': 'fact', 'fact': 'requester.manager'}), *ENDS]
    stages[1]['edit'] = stage_rule
    return definition(stages)


def update(app, name, record, **values):
    with app.business.context(app.people[name] if name != 'admin' else ADMIN) as context:
        return context.update(WfOrder, record, **values)


def test_stage_rules_allow_selected_fields_rows_and_attachments(app):
    publish(app, editable({'who': 'requester', 'fields': {'mode': 'only', 'names': ['remarks', 'risk_note']},
                           'tables': {'lines': {'edit': True, 'add': False, 'remove': False, 'reorder': False}},
                           'attachments': {'add': True, 'remove': False}}))
    record = order(app, lines=[{'amount': '5'}, {'amount': '6'}])
    decide(app, 'rita', record, kind='submit')
    lines = update(app, 'rita', record, remarks='Harmless note')['children']['lines']
    # A harmless field keeps the attempt; Mona's task is still open.
    found = status(app, record)
    assert found['attempt'] == 1 and found['status'] == 'active'
    first, second = ({'id': row['id'], 'amount': row['amount']} for row in lines)
    for values, message in (({'title': 'New'}, 'Title cannot change during Manager Review'),
                            ({'children': {'lines': [first, second, {'amount': '7'}]}}, 'rows cannot be added'),
                            ({'children': {'lines': [first]}}, 'rows cannot be removed'),
                            ({'children': {'lines': [second, first]}}, 'rows cannot be reordered'),
                            ({'children': {'lines': [{'amount': '5'}, {'amount': '6'}]}}, 'rows cannot be added or removed')):
        with pytest.raises(WorkflowError, match=message):
            update(app, 'rita', record, **values)
    # Somebody other than the requester cannot edit at all, whatever their rights.
    with pytest.raises(WorkflowError, match='You cannot edit at this stage'):
        update(app, 'sam', record, remarks='x')
    # An allowed change to business data ends the attempt: the request returns to Draft in attempt 2.
    update(app, 'rita', record, children={'lines': [{**first, 'amount': '9'}, second]})
    found = status(app, record)
    assert found['status'] == 'draft' and found['attempt'] == 2 and found['stage']['id'] == DRAFT
    assert [task['closed_reason'] for task in found['tasks']] == [None]
    assert journal_kinds(app, record)[-2:] == ['stage', 'changed']
    with app.business.context(ADMIN, readonly=True) as context:
        entry = context.history(WfOrder, record)['items'][1]
    assert entry['kind'] == 'stage' and entry['data']['workflow']['reason'] == 'The wf order changed after it was sent for review.'


def test_who_can_edit_and_nested_rows(app):
    with app.business.context(app.people['rita']) as context:
        ticket = context.create(WfTicket, title='T', children={'items': [{'label': 'a', 'children': {'codes': [{'code': '1'}]}}]})
    record = ticket['id']
    stages = [draft('stg_rview1'), single('stg_rview1', 'Check', role('buyer')), *ENDS]
    stages[1]['edit'] = {'who': 'existing', 'fields': {'mode': 'all'},
                         'tables': {'items': {'edit': True, 'add': False, 'remove': False, 'reorder': False},
                                    'items.codes': {'edit': False, 'add': True, 'remove': False, 'reorder': False}}}
    publish_ticket(app, ticket_definition('move:issued', stages))
    ticket_decide(app, 'rita', record, 'move:issued', kind='submit')
    item = ticket['children']['items'][0]
    code = item['children']['codes'][0]
    with app.business.context(app.people['sam']) as context:
        # Everyone with existing edit access may edit; a nested row may be added but not changed.
        with pytest.raises(WorkflowError, match='Items / codes: rows cannot be changed'):
            context.update(WfTicket, record, children={'items': [{'id': item['id'], 'label': 'a', 'children': {'codes': [{**code, 'code': '9'}]}}]})
        context.update(WfTicket, record, children={'items': [{'id': item['id'], 'label': 'a', 'children': {'codes': [code, {'code': '2'}]}}]})
    # That change ended the attempt: back in Draft, only the requester edits.
    with app.database.transaction() as tx:
        found = app.business.workflows.status(tx, app.people['rita'], WfTicket, record, protects='move:issued')
    assert found['stage']['id'] == DRAFT and found['attempt'] == 2
    # A custom action is an edit too: it cannot change a locked stage's fields, while the stage allows them here.
    stages[1]['edit'] = {'who': 'requester', 'fields': {'mode': 'only', 'names': ['note']}}
    stages[1]['actions'][0]['go_to'] = go(APPROVED)
    publish_ticket(app, {**ticket_definition('action:ship', stages), 'name': 'Ship check'})
    with app.business.context(app.people['rita']) as context:
        other = context.create(WfTicket, title='U')['id']
    ticket_decide(app, 'rita', other, 'action:ship', kind='submit')
    with pytest.raises(WorkflowError, match='You cannot edit at this stage'):
        with app.business.context(app.people['sam']) as context:
            context.action(WfTicket, other, 'retitle', title='Sneaky')
    with pytest.raises(WorkflowError, match='Title cannot change during Check'):
        with app.business.context(app.people['rita']) as context:
            context.action(WfTicket, other, 'retitle', title='Sneaky')
    with app.business.context(app.people['rita']) as context:
        assert context.get(WfTicket, other).title == 'U'


def test_payloads_cannot_choose_a_stage(app):
    publish(app, MANAGER)
    record = order(app)
    decide(app, 'rita', record, kind='submit')
    rita = login(app, 'rita')
    path = f'/api/wforder/{record}'
    for body in ({'_workflow': {'stage': {'id': APPROVED}}}, {'stage_id': APPROVED}, {'evlet_workflow_stage': APPROVED}):
        assert rita('PATCH', path, body, headers={b'if-match': etag(rita, path)})[0] in (403, 409, 422)
    mona = login(app, 'mona')
    status_, body, _ = send(mona, path, 'actions/act_mngr01ok', {'stage': APPROVED, 'outcome': 'approved'})
    assert status_ == 422 and body['code'] == 'VALIDATION_FAILED' and set(body['fields']) == {'stage', 'outcome'}
    assert status(app, record)['stage']['id'] == 'stg_mngr01'


# Approval validity

def test_a_change_after_approval_voids_the_unused_evidence(app):
    record = approved_order(app, total='5')
    # A harmless field keeps it.
    update(app, 'rita', record, remarks='Delivery at gate 2')
    assert status(app, record)['status'] == 'approved'
    update(app, 'rita', record, total='6')
    found = status(app, record)
    assert found['status'] == 'ended'
    assert 'Send this wf order for review first' in required(lambda: confirm(app, 'rita', record))
    # The next Send starts a new run, and its approval counts.
    decide(app, 'rita', record, kind='submit')
    decide(app, 'mona', record, kind='approve')
    assert confirm(app, 'rita', record)['_lifecycle']['state'] == 'confirmed'


def check_gate_and_invalidation(app):
    record = approved_order(app, total='5')
    update(app, 'rita', record, total='6')
    assert status(app, record)['status'] == 'ended'
    required(lambda: confirm(app, 'rita', record))
    decide(app, 'rita', record, kind='submit')
    decide(app, 'mona', record, kind='approve')
    assert confirm(app, 'rita', record)['_lifecycle']['state'] == 'confirmed'
    with app.business.context(app.people['rita']) as context:
        assert context.workflow(WfOrder, record).status()['evidence'] == 'used'
    assert journal_kinds(app, record).count('stage') == 5


def test_gate_and_invalidation(app):
    check_gate_and_invalidation(app)


def test_execute_is_refused_at_publication_until_it_is_built(app):
    from evlet.workflow_definition import DefinitionError
    stages = [draft('stg_mngr01'), single('stg_mngr01', 'Manager', role('viewer')), *ENDS]
    stages[1]['actions'][0]['execute'] = {'kind': 'lifecycle', 'operation': 'confirm', 'as': 'actor'}
    with pytest.raises(DefinitionError, match='not available yet'):
        publish(app, definition(stages))


def test_a_maintenance_edit_after_approval_is_caught_by_the_fingerprint(app):
    record = approved_order(app, total='5')
    with app.database.transaction() as tx:
        with tx.maintenance():
            tx.update(WfOrder, record, title='Changed behind the pipeline')
    required(lambda: confirm(app, 'rita', record))


def test_attachments_count_as_changes(app):
    record = approved_order(app)
    rita = login(app, 'rita')
    status_, body, _ = rita('POST', f'/api/wforder/{record}/attachments', {'name': 'quote.pdf', 'content': base64.b64encode(b'%PDF').decode()})
    assert status_ == 201, body
    assert status(app, record)['status'] == 'ended'
    required(lambda: confirm(app, 'rita', record))


# Feedback and atomic history

def test_required_feedback_is_checked_and_stored_as_plain_text(app):
    publish(app, MANAGER)
    record = order(app)
    decide(app, 'rita', record, kind='submit')
    for feedback, message in ((None, 'Enter feedback'), ('   \n\t ', 'Enter feedback'), ('x' * 2001, 'at most 2000 characters'),
                              ('bad\x00byte', 'control characters'), ('bell\x07', 'control characters'), ('\ud800', 'valid text'),
                              (42, 'Feedback is text')):
        with pytest.raises(ValidationError) as caught:
            decide(app, 'mona', record, kind='reject', feedback=feedback)
        assert message in caught.value.errors['feedback']
    with pytest.raises(ValidationError):
        decide(app, 'mona', record, kind='request_changes', feedback='')
    # Arabic, accents in decomposed form (normalized to NFC), emoji and markup are kept as plain text.
    text = '  يرجى إرفاق عرض السعر · Café <b>quote</b> 🙂\nLine two  '
    found = decide(app, 'mona', record, kind='reject', feedback=text)
    assert found['decisions'][-1]['feedback'] == 'يرجى إرفاق عرض السعر · Café <b>quote</b> 🙂\nLine two'
    assert found['status'] == 'rejected'
    mona = login(app, 'mona')
    other = order(app)
    decide(app, 'rita', other, kind='submit')
    raw = json.dumps({'key': 'surrogate-1', 'token': status(app, other)['token'], 'feedback': 'x'}).replace('"x"', '"\\ud800"')
    status_, body, _ = http_raw(app, mona, f'/api/wforder/{other}/workflow/actions/act_mngr01no', raw, etag(mona, f'/api/wforder/{other}'))
    assert status_ == 422 and body['fields'] == {'feedback': 'Feedback must be valid text'}
    status_, body, _ = send(mona, f'/api/wforder/{other}', 'actions/act_mngr01no', {'feedback': 'y' * 17000})
    assert status_ == 413 and body['code'] == 'TOO_LARGE'


def http_raw(app, c, path, raw, match):
    """POST a body exactly as given (JSON with a lone surrogate escape cannot be built with json.dumps)."""
    import asyncio

    async def run():
        events, sent = [{'type': 'http.request', 'body': raw.encode()}], []

        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}

        async def send_event(event):
            sent.append(event)
        headers = {b'host': b'127.0.0.1:8000', b'content-type': b'application/json', b'x-evlet-request': b'1',
                   b'cookie': c.cookie.encode(), b'x-csrf-token': c.csrf.encode(), b'if-match': match}
        await app({'type': 'http', 'method': 'POST', 'path': path, 'query_string': b'', 'headers': list(headers.items()),
                   'client': ('127.0.0.1', 1)}, receive, send_event)
        return sent[0]['status'], json.loads(sent[1]['body']), dict(sent[0]['headers'])
    return asyncio.run(run())


def test_a_failure_rolls_back_the_decision_and_its_journal_entry_together(app):
    publish(app, MANAGER)
    record = order(app)
    decide(app, 'rita', record, kind='submit')
    before = counts(app)
    token = status(app, record)['token']
    with app.database.transaction() as tx:
        tx.connection.execute("CREATE TRIGGER fail_decision BEFORE INSERT ON evlet_workflow_decisions WHEN NEW.feedback = 'boom' "
                              "BEGIN SELECT RAISE(ABORT, 'decision failed'); END")
    # Trusted code that catches the failure and commits still leaves nothing behind: the handle uses a savepoint.
    with app.business.context(app.people['mona']) as context:
        handle = context.workflow(WfOrder, record)
        with pytest.raises(ConflictError):
            handle.decide(kind='approve', feedback='boom', key='rollback-01')
    assert counts(app) == before and status(app, record)['token'] == token
    # The other way round: the journal refuses, so the decision is not stored either.
    with app.database.transaction() as tx:
        tx.connection.execute('DROP TRIGGER fail_decision')
        tx.connection.execute("CREATE TRIGGER fail_journal BEFORE INSERT ON evlet_journal WHEN NEW.kind = 'decided' "
                              "BEGIN SELECT RAISE(ABORT, 'journal failed'); END")
    mona = login(app, 'mona')
    status_, body, _ = send(mona, f'/api/wforder/{record}', 'actions/act_mngr01ok', {}, key='rollback-02')
    assert status_ == 409 and body['code'] == 'CONFLICT'
    assert counts(app) == before and status(app, record)['status'] == 'active'
    with app.database.transaction() as tx:
        tx.connection.execute('DROP TRIGGER fail_journal')
    status_, body, _ = send(mona, f'/api/wforder/{record}', 'actions/act_mngr01ok', {'feedback': 'Within budget'}, key='rollback-02')
    assert status_ == 200 and body['status'] == 'approved'
    with app.database.transaction() as tx:
        rows = tx.connection.execute('SELECT d.journal_id, d.source, d.client, j.kind FROM evlet_workflow_decisions d '
                                     'JOIN evlet_journal j ON j.id=d.journal_id ORDER BY d.id').fetchall()
    assert [(row['kind'], row['client']) for row in rows] == [('submitted', None), ('decided', 'HTTP')]
    assert journal_kinds(app, record)[-3:] == ['stage', 'decided', 'stage']


def test_history_shows_decisions_with_feedback_to_readers_only(app):
    publish(app, MANAGER)
    record = order(app)
    decide(app, 'rita', record, kind='submit')
    decide(app, 'mona', record, kind='request_changes', feedback='Attach the quote')
    with app.business.context(app.people['cleo'], readonly=True) as context:
        items = context.history(WfOrder, record)['items']
    decided = next(item for item in items if item['kind'] == 'decided')
    assert decided['data']['workflow']['feedback'] == 'Attach the quote' and decided['data']['workflow']['label'] == 'Request changes'
    assert {item['kind'] for item in items} >= {'submitted', 'decided', 'stage'}


# HTTP routes, receipts and concurrency

def test_http_send_decide_and_their_preconditions(app):
    publish(app, MANAGER)
    record = order(app)
    path = f'/api/wforder/{record}'
    rita, mona, cleo = login(app, 'rita'), login(app, 'mona'), login(app, 'cleo')
    status_, found, _ = rita('GET', path + '/workflow')
    assert status_ == 200 and found['token'] == 'new' and found['run'] is None
    assert [item['id'] for item in found['actions']] == ['act_submit']
    # A reader without update is offered nothing to send.
    assert mona('GET', path + '/workflow')[1]['actions'] == []
    match = etag(rita, path)
    assert rita('POST', path + '/workflow/actions/act_submit', {'token': 'new'}, headers={b'if-match': match})[1]['fields'] == \
        {'key': 'Supply an idempotency key of 8 to 128 characters'}
    status_, body, _ = rita('POST', path + '/workflow/actions/act_submit', {'key': 'send-0001', 'token': 'new'})
    assert (status_, body['code']) == (428, 'PRECONDITION_REQUIRED')
    status_, body, _ = rita('POST', path + '/workflow/actions/act_submit', {'key': 'send-0001'}, headers={b'if-match': match})
    assert (status_, body['code'], body['error']) == (428, 'PRECONDITION_REQUIRED', 'A workflow token is required')
    status_, body, _ = rita('POST', path + '/workflow/actions/act_submit', {'key': 'send-0001', 'token': '9.9'}, headers={b'if-match': match})
    assert (status_, body['code']) == (409, 'WORKFLOW_CHANGED')
    status_, body, _ = rita('POST', path + '/workflow/actions/act_submit', {'key': 'send-0001', 'token': 'new'}, headers={b'if-match': b'"old"'})
    assert (status_, body['code']) == (409, 'RECORD_CHANGED')
    status_, sent, headers = rita('POST', path + '/workflow/actions/act_submit', {'key': 'send-0001', 'token': 'new'}, headers={b'if-match': match})
    assert status_ == 200 and sent['stage']['label'] == 'Manager Review' and sent['token'] != 'new' and headers[b'etag'] == match
    # The response was lost: the same request again returns the same receipt, although the token is old now.
    assert rita('POST', path + '/workflow/actions/act_submit', {'key': 'send-0001', 'token': 'new'}, headers={b'if-match': match})[1] == sent
    status_, body, _ = rita('POST', path + '/workflow/actions/act_submit', {'key': 'send-0001', 'token': sent['token']}, headers={b'if-match': match})
    assert (status_, body['code']) == (409, 'IDEMPOTENCY_MISMATCH')
    # Mona reviews: she can read but not update the order, and she is assigned.
    found = mona('GET', path + '/workflow')[1]
    assert [item['id'] for item in found['actions']] == ['act_mngr01ok', 'act_mngr01no', 'act_mngr01ch']
    assert found['actions'][1]['feedback'] == {'mode': 'required', 'max_length': 2000}
    status_, body, _ = send(cleo, path, 'actions/act_mngr01ok', {})
    assert (status_, body['code']) == (403, 'FORBIDDEN')
    status_, body, _ = send(mona, path, 'actions/act_nothere', {})
    assert (status_, body['code']) == (404, 'NOT_FOUND')
    status_, body, _ = send(mona, path, 'actions/act_mngr01no', {})
    assert (status_, body['code'], body['fields']) == (422, 'VALIDATION_FAILED', {'feedback': 'Enter feedback'})
    status_, body, _ = send(mona, path, 'actions/act_mngr01ok', {'feedback': 'Fine'}, key='approve-01')
    assert status_ == 200 and body['status'] == 'approved' and body['evidence'] == 'valid'
    assert [item['kind'] for item in body['decisions']] == ['submit', 'approve']
    assert 'routing' not in json.dumps(body)
    # Somebody who cannot read the record gets 404 from every workflow route, as from the record.
    assert http(app, 'GET', path + '/workflow')[0] == 401
    status_, body, _ = rita('GET', '/api/wforder/999999/workflow')
    assert (status_, body['code']) == (404, 'NOT_FOUND')
    assert rita('GET', '/api/wfperson/1/workflow')[1]['code'] == 'NOT_FOUND'
    assert rita('PUT', path + '/workflow/withdraw', {})[0] in (404, 405)


def test_http_withdraw(app):
    publish(app, MANAGER)
    record = order(app)
    path = f'/api/wforder/{record}'
    rita, sam = login(app, 'rita'), login(app, 'sam')
    status_, body, _ = send(rita, path, 'withdraw', {})
    assert (status_, body['code']) == (409, 'WORKFLOW_CHANGED')
    send(rita, path, 'actions/act_submit', {}, key='send-0002')
    assert rita('GET', path + '/workflow')[1]['withdraw'] is True
    status_, body, _ = send(sam, path, 'withdraw', {}, key='withdraw-9')
    assert (status_, body['code']) == (403, 'FORBIDDEN')
    status_, body, _ = send(rita, path, 'withdraw', {'feedback': 'Wrong supplier'}, key='withdraw-1')
    assert status_ == 200 and body['stage']['id'] == DRAFT and body['attempt'] == 2 and body['decisions'][-1]['kind'] == 'withdraw'
    assert body['decisions'][-1]['feedback'] == 'Wrong supplier'
    with app.database.transaction() as tx:
        assert tx.connection.execute('SELECT status FROM evlet_workflow_attempts ORDER BY id').fetchall()[0][0] == 'withdrawn'


def test_decision_versus_withdrawal_and_simultaneous_final_decisions(app):
    publish(app, MANAGER)
    record = order(app)
    path = f'/api/wforder/{record}'
    rita, mona = login(app, 'rita'), login(app, 'mona')
    send(rita, path, 'actions/act_submit', {}, key='send-0003')
    token = rita('GET', path + '/workflow')[1]['token']
    assert send(rita, path, 'withdraw', {}, key='withdraw-2', token=token)[0] == 200
    status_, body, _ = send(mona, path, 'actions/act_mngr01ok', {}, key='late-0001', token=token)
    assert (status_, body['code']) == (409, 'WORKFLOW_CHANGED')
    # The other order: the final approval wins and the withdrawal is refused.
    send(rita, path, 'actions/act_submit', {}, key='send-0004')
    token = rita('GET', path + '/workflow')[1]['token']
    assert send(mona, path, 'actions/act_mngr01ok', {}, key='final-001', token=token)[0] == 200
    assert send(rita, path, 'withdraw', {}, key='withdraw-3', token=token)[1]['code'] == 'WORKFLOW_CHANGED'
    # Two reviewers decide an Any review at the same moment, in two threads: exactly one decision counts.
    items = [review('rev_finan1', 'Finance', role('finance'))]
    publish(app, {**definition([draft('stg_deprt1'), group('stg_deprt1', 'Department', items), *ENDS], protects='cancel'), 'name': 'Cancel'})
    with app.database.transaction() as tx:
        tx.connection.execute("INSERT INTO evlet_document_lifecycle(model, record_id, state, revision) VALUES ('wforder', ?, 'confirmed', 1)", (record,))
    decide(app, 'rita', record, kind='submit', protects='cancel')
    barrier, results = threading.Barrier(2), {}

    def vote(name):
        barrier.wait()
        try:
            results[name] = decide(app, name, record, kind='approve', protects='cancel')['status']
        except (WorkflowError, AccessError) as exc:
            results[name] = type(exc).__name__
    threads = [threading.Thread(target=vote, args=(name,)) for name in ('fay', 'fred')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results.values()) == ['WorkflowError', 'approved']
    with app.database.transaction() as tx:
        assert tx.connection.execute("SELECT count(*) FROM evlet_workflow_decisions WHERE kind='approve' AND run_id="
                                     "(SELECT max(id) FROM evlet_workflow_runs)").fetchone()[0] == 1


def test_an_edit_and_the_final_confirm_cannot_both_win(app):
    record = approved_order(app, total='5')
    rita = login(app, 'rita')
    path = f'/api/wforder/{record}'
    match = etag(rita, path)
    # The edit commits first: the confirm sent with the old ETag is refused, and with a fresh one it needs approval again.
    assert rita('PATCH', path, {'total': '7'}, headers={b'if-match': match})[0] == 200
    assert rita('POST', path + '/confirm', {}, headers={b'if-match': match})[0] == 409
    status_, body, _ = rita('POST', path + '/confirm', {}, headers={b'if-match': etag(rita, path)})
    assert (status_, body['code']) == (409, 'WORKFLOW_REQUIRED')
    # The confirm commits first: the edit is refused by the lifecycle lock.
    other = approved_order_again(app)
    other_path = f'/api/wforder/{other}'
    match = etag(rita, other_path)
    assert rita('POST', other_path + '/confirm', {}, headers={b'if-match': match})[0] == 200
    assert rita('PATCH', other_path, {'total': '8'}, headers={b'if-match': etag(rita, other_path)})[0] == 409


def approved_order_again(app):
    record = order(app, total='5')
    decide(app, 'rita', record, kind='submit')
    decide(app, 'mona', record, kind='approve')
    return record


def test_the_python_handle_and_its_receipts(app):
    publish(app, MANAGER)
    record = order(app)
    with app.business.context(app.people['rita'], source='Integration', correlation_id='import-7781') as context:
        handle = context.workflow(WfOrder, record)
        first = handle.decide(kind='submit', key='import-7781')
        assert handle.decide(kind='submit', key='import-7781') == first
        with pytest.raises(WorkflowError) as caught:
            handle.decide(kind='submit', key='import-7781', feedback='other')
        assert caught.value.code == 'IDEMPOTENCY_MISMATCH'
        with pytest.raises(ValidationError):
            handle.decide(kind='submit', key='short')
    with app.business.context(app.people['mona'], readonly=True) as context:
        with pytest.raises(AccessError):
            context.workflow(WfOrder, record).decide(kind='approve')
    with app.business.context(app.people['mona']) as context:
        assert context.workflow(WfOrder, record).decide(kind='approve')['status'] == 'approved'
    with app.database.transaction() as tx:
        row = tx.connection.execute("SELECT source, client, correlation_id FROM evlet_workflow_decisions WHERE kind='submit'").fetchone()
    assert tuple(row) == ('Integration', 'Python', 'import-7781')
    with pytest.raises(AccessError):
        with app.business.context(app.people['rita']) as context:
            context.workflow(WfOrder, 999999)


def test_a_rejected_run_grants_nothing(app):
    publish(app, MANAGER)
    record = order(app)
    decide(app, 'rita', record, kind='submit')
    decide(app, 'mona', record, kind='reject', feedback='No budget')
    required(lambda: confirm(app, 'rita', record))
    assert status(app, record)['status'] == 'rejected'
