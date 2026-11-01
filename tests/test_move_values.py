"""A move may carry values for the fields its target state makes applicable or required, on real SQLite."""

import pytest

from evlet import Database, Model, Move, Registry, Rule, State, Text, ValidationError
from evlet.auth import User
from evlet.calc import F
from test_business import client
from test_rules_types import ALL, add_user, start


class Ticket(Model):
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'), write_fields=('title', 'note'))}
    title = Text(default='')
    reason = Text(applies_when=F('status') == 'closed', required_when=F('status') == 'closed', editable_when_locked=True)
    note = Text(applies_when=F('status') == 'closed')
    status = State(('open', 'closed'), moves=(Move('open', 'closed', 'Close'), Move('closed', 'open', 'Reopen')),
                   locked=('closed',))


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
    assert {move['to']: move.get('asks') for move in schema['state']['moves']} == {'closed': ['reason', 'note'], 'open': None}
    assert c('GET', url)[1]['_moves'] == [{'to': 'closed', 'label': 'Close', 'allowed': True, 'asks': ['reason', 'note']}]
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
    assert move(editor, url, 'closed', {'reason': 'x'})[0] in (403, 422)


def test_context_move_takes_values(tickets):
    app, c = tickets
    record_id = c('POST', '/api/ticket', {'title': 'Chair'})[1]['id']
    with app.business.context(User(1, 'admin', 'admin')) as context:
        with pytest.raises(ValidationError):
            context.move(Ticket, record_id, 'closed')
        assert context.move(Ticket, record_id, 'closed', reason='Replaced')['reason'] == 'Replaced'
