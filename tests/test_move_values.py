"""A move may carry values for the fields its target state makes applicable or required, on real SQLite."""

import pytest

from evlet import Database, Decimal, Model, Registry, Rule, State, Text, ValidationError
from evlet.auth import User
from evlet.calc import F
from test_business import client
from test_rules_types import ALL, add_user, start


class Ticket(Model):
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'), write_fields=('title', 'note'))}
    title = Text(default='')
    reason = Text(applies_when=F('status') == 'closed', required_when=F('status') == 'closed', versioned=False)
    note = Text(applies_when=F('status') == 'closed')
    status = State(
        State.Step('open'),
        State.Step('closed', lock=True),
        rules=(
            State.Rule('open', 'closed', 'Close', key='closed'),
            State.Rule('closed', 'open', 'Reopen', key='open'),
        ))


@pytest.fixture
def tickets(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Ticket)))
    return app, client(app)


def move(c, url, to, body):
    return c('POST', f'{url}/moves/{to}', body, headers={b'if-match': c('GET', url)[2][b'etag']})


def test_a_move_asks_for_the_fields_its_target_state_needs(tickets):
    app, c = tickets
    url = f"/api/ticket/{c('POST', '/api/ticket', {'title': 'Printer'})[1]['id']}"
    schema = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'ticket')
    # Only moves that ask for something list `asks`.
    assert {rule['to']: rule.get('asks') for rule in schema['state']['rules']} == {'closed': ['reason', 'note'], 'open': None}
    assert c('GET', url)[1]['_moves'] == [{'key': 'closed', 'to': 'closed', 'label': 'Close', 'allowed': True, 'asks': ['reason', 'note']}]
    # The required value is checked with the move, and a failed move changes nothing.
    status, body, _ = move(c, url, 'closed', {})
    assert status == 422 and body['fields'] == {'reason': 'This field is required'}
    assert c('GET', url)[1]['status'] == 'open'
    # Values the move does not ask for are refused.
    status, body, _ = move(c, url, 'closed', {'reason': 'Fixed', 'title': 'Renamed'})
    assert status == 422 and body['fields'] == {'title': 'This move does not take this value'}
    status, closed, _ = move(c, url, 'closed', {'reason': 'Fixed', 'note': 'New toner'})
    assert status == 200 and (closed['status'], closed['reason'], closed['note'], closed['title']) == ('closed', 'Fixed', 'New toner', 'Printer')
    # Reopening asks for nothing, so a value is refused; the reason stops applying and is cleared.
    assert move(c, url, 'open', {'reason': 'x'})[1]['fields'] == {'reason': 'This move does not take this value'}
    reopened = move(c, url, 'open', {})[1]
    assert (reopened['status'], reopened['reason'], reopened['_moves'][0]['asks']) == ('open', None, ['reason', 'note'])


def test_a_move_asks_only_for_fields_the_role_may_write(tickets):
    app, c = tickets
    editor = add_user(app, 'editor')
    url = f"/api/ticket/{c('POST', '/api/ticket', {'title': 'Desk'})[1]['id']}"
    assert editor('GET', url)[1]['_moves'][0]['asks'] == ['note']
    # An asked field the role can read but not write is refused by name; an unknown key gets a form error.
    for key, error in (('reason', 'reason'), ('bogus', '_form')):
        assert move(editor, url, 'closed', {key: 'x'})[:2] == (422, {'error': 'Validation failed', 'fields': {error: 'This move does not take this value'}})


def test_context_move_takes_values(tickets):
    app, c = tickets
    record_id = c('POST', '/api/ticket', {'title': 'Chair'})[1]['id']
    with app.business.context(User(1, 'admin', 'admin')) as context:
        with pytest.raises(ValidationError):
            context.move(Ticket, record_id, 'closed')
        assert context.move(Ticket, record_id, 'closed', reason='Replaced')['reason'] == 'Replaced'


class Case(Model):
    permissions = {**ALL, 'viewer': Rule(('read', 'update'), read_fields=('title', 'note'), write_fields=('title', 'note')),
                   'editor': Rule(('read', 'update'), read_fields=('title', 'status', 'note'), write_fields=('title', 'note'))}
    title = Text(default='')
    note = Text(applies_when=F('status') == 'closed')
    secret = Text(applies_when=F('status') == 'closed')
    status = State(
        State.Step('open'),
        State.Step('closed'),
        State.Step('gone'),
        rules=(
            State.Rule('open', 'closed', 'Close', key='closed', by=('admin', 'editor', 'viewer')),
        ))


def test_move_checks_come_before_the_values_so_values_reveal_no_moves(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Case)))
    c = client(app)
    url = f"/api/case/{c('POST', '/api/case', {'title': 'A'})[1]['id']}"
    # Unknown targets and missing moves answer as they do without values.
    assert move(c, url, 'nowhere', {'note': 'x'})[:2] == (404, {'error': 'Move not found'})
    assert move(c, url, 'gone', {'note': 'x'})[:2] == (404, {'error': 'Move not found'})
    # A role that cannot read the State gets one answer for every refused move, values or not.
    viewer = add_user(app, 'viewer')
    for target in ('nowhere', 'gone'):
        assert move(viewer, url, target, {'note': 'x'})[:2] == (409, {'error': 'This record has no such move now'})
    # A hidden asked field is refused like an unknown key, so its name is not confirmed.
    editor = add_user(app, 'editor')
    assert editor('GET', url)[1]['_moves'][0]['asks'] == ['note']
    for key in ('secret', 'bogus'):
        assert move(editor, url, 'closed', {key: 'x'})[:2] == (422, {'error': 'Validation failed', 'fields': {'_form': 'This move does not take this value'}})
    assert c('GET', url)[1]['status'] == 'open'
    status, body, _ = move(viewer, url, 'closed', {'note': 'Done'})
    assert status == 200 and body['note'] == 'Done'


class Fee(Model):
    permissions = ALL
    amount = Decimal(default=1)
    double = Decimal(applies_when=F('status') == 'done', locked_when=F('amount') == 1)
    status = State(
        State.Step('new'),
        State.Step('done'),
        rules=(
            State.Rule('new', 'done', 'Done', key='done'),
        ))


def test_a_move_does_not_ask_for_a_field_locked_on_the_record(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Fee)))
    c = client(app)
    locked = f"/api/fee/{c('POST', '/api/fee', {})[1]['id']}"
    open_ = f"/api/fee/{c('POST', '/api/fee', {'amount': 2})[1]['id']}"
    assert c('GET', locked)[1]['_moves'] == [{'key': 'done', 'to': 'done', 'label': 'Done', 'allowed': True}]
    assert c('GET', open_)[1]['_moves'][0]['asks'] == ['double']
    assert move(c, locked, 'done', {})[0] == 200
    status, body, _ = move(c, open_, 'done', {'double': 4})
    assert status == 200 and float(body['double']) == 4
