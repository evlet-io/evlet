"""self.context.session: a Move run method or an @action handler writes through the move or action's own
transaction (tx.lock, tx.insert_many, tx.reserve_ids, tx.aggregate), so the State, its Sequence number and
every row written commit or roll back together. Every other context refuses it, including everything Eva,
assistants, tools, MCP and the connector reach. Real SQLite and PostgreSQL, through the ASGI routes."""

import pytest

from evlet import (Application, Column, Database, Integer, Link, Model, Move, Registry, Report, Rule, Sequence, State, Sum, Text,
                   ValidationError, action, api)
from evlet.auth import AccessError, KeyAccess, LocalAuth, User
from evlet.connector import ConnectorError
from evlet.postgres import PostgreSQLDatabase
from evlet.services import Endpoint
from evlet.tools import ApprovalNeeded
from test_business import PASSWORD, client, http
from test_postgres import pg_dsn  # noqa: F401 - fixture

FULL = {'admin': Rule(('read', 'create', 'update', 'delete'))}
# Contexts a handler gave away (kept by app code past its return); each must be closed afterwards.
KEPT = []


class Transfer(Model):
    """Moves a quantity of an item from one place to another. Sending posts two movement rows."""
    permissions = FULL
    item = Text(max_length=40, required=True)
    origin = Text(max_length=40, default='store')
    destination = Text(max_length=40, default='site')
    quantity = Integer(default=1)
    note = Text(default='', editable_when_locked=True)
    number = Sequence('TR{n:04}', assign_on='sent')
    status = State(('draft', 'sent'), default='draft', moves=(Move('draft', 'sent', 'Send', run='post_movements'),),
                   locked=('sent',), final=('sent',))

    def post_movements(self):
        tx = self.context.session
        KEPT.append(self.context)
        tx.lock(('place', self.item, self.origin), ('place', self.item, self.destination))
        held = on_hand(tx, self.item, self.origin)
        tx.insert_many(Movement, [
            {'transfer': self.id, 'item': self.item, 'place': self.origin, 'quantity': -self.quantity},
            {'transfer': self.id, 'item': self.item, 'place': self.destination, 'quantity': self.quantity},
        ])
        # The rows just written are visible to the same transaction.
        if on_hand(tx, self.item, self.origin) != held - self.quantity:
            raise AssertionError('tx.aggregate did not see this transaction')
        if held < self.quantity:
            raise ValidationError({'quantity': f'Only {held} on hand'})
        self.note = f'Posted; {held - self.quantity} left at {self.origin}'

    @action('Count', inputs={'quantity': Integer(required=True)})
    def count(self, quantity):
        """Record a counted quantity at the origin: one adjustment row, or none when it already matches."""
        tx = self.context.session
        KEPT.append(self.context)
        tx.lock(('place', self.item, self.origin))
        held = on_hand(tx, self.item, self.origin)
        first, = tx.reserve_ids(Movement, 1)
        tx.insert_many(Movement, [{'id': first, 'transfer': self.id, 'item': self.item, 'place': self.origin, 'quantity': quantity - held}])
        if quantity < 0:
            raise ValidationError({'quantity': 'A count is never negative'})
        self.note = f'Counted {quantity}'
        return {'adjustment': quantity - held, 'row': first}

    @action('Probe')
    def probe(self):
        """What the handler sees: its own context opens the session, rows keep the caller's context."""
        rows = self.context.session.count(Movement)
        return {'rows': rows}

    def changed(self, change):
        if change.operation == 'move' and self.number and change.before.note == 'fail after numbering':
            raise ValidationError({'note': 'Refused after the number was assigned'})


class Movement(Model):
    transfer = Link(Transfer)
    item = Text(max_length=40, required=True)
    place = Text(max_length=40, required=True)
    quantity = Integer(required=True)

    class Meta:
        ledger = True
        append_only = True
        indexes = [('item', 'place')]


def on_hand(tx, item, place):
    rows = tx.aggregate(Movement, measures={'quantity': Sum('quantity')}, filters={'item': item, 'place': place})
    return (rows[0]['quantity'] if rows else None) or 0


def refused(context):
    try:
        context.session
    except AccessError as exc:
        return exc.status
    return 'open'


@api(method='POST', permission=(Transfer, 'read'), tool=True)
def session_probe(context):
    """Report whether this service's context opens the transaction session."""
    return {'session': refused(context)}


class SessionReport(Report):
    title = 'Session probe'
    permissions = ('admin',)
    columns = (Column('session'),)

    def run(self):
        return {'rows': [{'session': str(refused(self.context))}], 'total': 1}


def build(database):
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      reports=(SessionReport,), apis=(Endpoint('test.session_probe', session_probe, 'POST', (Transfer, 'read'), tool=True),))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app, client(app)


@pytest.fixture(params=['sqlite', 'postgresql'])
def app(request, tmp_path):
    KEPT.clear()
    registry = Registry(Transfer, Movement)
    if request.param == 'sqlite':
        return build(Database(tmp_path / 'handler.sqlite3', registry))
    return build(PostgreSQLDatabase(request.getfixturevalue('pg_dsn'), registry))


def stock(database, item='bolt', place='store', quantity=10):
    with database.transaction() as tx:
        tx.insert_many(Movement, [{'item': item, 'place': place, 'quantity': quantity}])


def transfer(admin, **values):
    status, body, _ = admin('POST', '/api/transfer', {'item': 'bolt', **values})
    assert status == 201, body
    return body['id']


def post(admin, record_id, what, body=None):
    """A move (moves/<to>) or an action (actions/<name>) on a saved transfer, at its current revision."""
    url = f'/api/transfer/{record_id}'
    return admin('POST', f'{url}/{what}', body or {}, headers={b'if-match': admin('GET', url)[2][b'etag']})


def saved(database, record_id):
    with database.transaction() as tx:
        row = tx.get(Transfer, record_id)
        rows = tx.list(Movement, filters={'transfer': row.id}, order_by=('id',), limit=100)
        return row, [(item.place, item.quantity) for item in rows]


def counter(database):
    with database.transaction() as tx:
        return tx.connection.execute('SELECT count(*) FROM evlet_sequence').fetchone()[0]


# A Move's run method

def test_a_move_posts_ledger_rows_and_takes_its_number_in_one_transaction(app):
    app, admin = app
    stock(app.database)
    record_id = transfer(admin, quantity=4)
    status, body, _ = post(admin, record_id, 'moves/sent', {})
    assert status == 200, body
    assert body['status'] == 'sent' and body['number'] == 'TR0001' and body['note'] == 'Posted; 6 left at store'
    row, rows = saved(app.database, record_id)
    assert row.status == 'sent' and rows == [('store', -4), ('site', 4)]
    with app.database.transaction() as tx:
        assert on_hand(tx, 'bolt', 'store') == 6 and on_hand(tx, 'bolt', 'site') == 4


def test_a_failure_in_run_rolls_back_the_state_the_number_and_the_rows(app):
    app, admin = app
    stock(app.database, quantity=3)
    record_id = transfer(admin, quantity=5)
    status, body, _ = post(admin, record_id, 'moves/sent', {})
    assert status == 422 and body['fields'] == {'quantity': 'Only 3 on hand'}
    row, rows = saved(app.database, record_id)
    assert row.status == 'draft' and row.number is None and rows == [] and row.note == ''
    with app.database.transaction() as tx:
        assert tx.count(Movement) == 1
    # No number was used up: the next successful move takes the first one.
    smaller = transfer(admin, quantity=2)
    assert post(admin, smaller, 'moves/sent', {})[1]['number'] == 'TR0001'


def test_a_failure_after_numbering_rolls_back_the_number_and_the_rows_too(app):
    app, admin = app
    stock(app.database)
    record_id = transfer(admin, quantity=1, note='fail after numbering')
    status, body, _ = post(admin, record_id, 'moves/sent', {})
    assert status == 422 and body['fields'] == {'note': 'Refused after the number was assigned'}
    row, rows = saved(app.database, record_id)
    assert row.status == 'draft' and row.number is None and rows == []
    assert counter(app.database) == 0
    other = transfer(admin, quantity=1)
    assert post(admin, other, 'moves/sent', {})[1]['number'] == 'TR0001'


def test_python_moves_get_the_session_too_and_a_caught_failure_still_rolls_back(app):
    app, admin = app
    stock(app.database, quantity=1)
    first, second = transfer(admin, quantity=1), transfer(admin, quantity=5)
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.move(Transfer, first, 'sent')['number'] == 'TR0001'
        with pytest.raises(ValidationError):
            context.move(Transfer, second, 'sent')
        # The caller's own context never opens the session, before, during or after a move.
        assert refused(context) == 403
    assert saved(app.database, first)[1] == [('store', -1), ('site', 1)]
    row, rows = saved(app.database, second)
    assert row.status == 'draft' and row.number is None and rows == []


# An @action handler

def test_an_action_writes_ledger_rows_with_its_changes_in_one_transaction(app):
    app, admin = app
    stock(app.database, quantity=7)
    record_id = transfer(admin)
    status, body, _ = post(admin, record_id, 'actions/count', {'quantity': 9})
    assert status == 200, body
    assert body['note'] == 'Counted 9' and body['_outcome']['adjustment'] == 2
    row, rows = saved(app.database, record_id)
    assert rows == [('store', 2)]
    with app.database.transaction() as tx:
        assert on_hand(tx, 'bolt', 'store') == 9 and tx.get(Movement, body['_outcome']['row']).quantity == 2


def test_a_failure_in_an_action_rolls_back_its_changes_and_rows(app):
    app, admin = app
    stock(app.database, quantity=7)
    record_id = transfer(admin)
    status, body, _ = post(admin, record_id, 'actions/count', {'quantity': -1})
    assert status == 422 and body['fields'] == {'quantity': 'A count is never negative'}
    row, rows = saved(app.database, record_id)
    assert row.note == '' and rows == []
    with app.database.transaction() as tx:
        assert on_hand(tx, 'bolt', 'store') == 7


def test_the_backend_still_decides_whether_the_move_or_action_may_run(app):
    app, admin = app
    stock(app.database)
    record_id = transfer(admin)
    with app.business.context(User(2, 'clerk', 'viewer')) as context:
        with pytest.raises(AccessError):
            context.action(Transfer, record_id, 'count', quantity=1)
        with pytest.raises(AccessError):
            context.move(Transfer, record_id, 'sent')
    assert saved(app.database, record_id)[1] == []


# Where the session is refused

def test_the_handler_context_closes_when_the_handler_returns(app):
    app, admin = app
    stock(app.database)
    record_id = transfer(admin)
    post(admin, record_id, 'actions/count', {'quantity': 3})
    post(admin, record_id, 'moves/sent', {})
    assert len(KEPT) == 2 and [refused(kept) for kept in KEPT] == [403, 403]


def test_ordinary_contexts_refuse_the_session(app):
    app, _ = app
    user = User(1, 'admin', 'admin')
    with app.business.context(user) as context:
        assert refused(context) == 403 and refused(context.read_context()) == 403
    with app.business.context(user, readonly=True) as context:
        assert refused(context) == 403


def test_services_and_reports_refuse_the_session_over_http(app):
    app, admin = app
    assert admin('POST', '/api/_services/test.session_probe', {})[1] == {'session': 403}
    status, body, _ = admin('POST', '/api/_reports/SessionReport', {})
    assert status == 200 and body['rows'] == [{'session': '403'}], body


def as_origin(origin):
    """The admin as Eva, a scheduled Eva task, a remote MCP client, the local connector or an API key sees it."""
    access = {'eva': KeyAccess(True, None, None, None, 'eva'), 'schedule': KeyAccess(True, None, None, None, 'schedule'),
              'mcp': KeyAccess(True, None, None, 7), 'connector': KeyAccess(True, None, None, 0),
              'api': KeyAccess(True, None, 1)}[origin]
    return User(1, 'admin', 'admin', access=access)


def run_tool(app, origin, name, values):
    """One tool call through the Tool Engine, approved by the person when the origin asks for approval."""
    tools, user = app.business.tools, as_origin(origin)
    try:
        return tools.execute(user, name, values)
    except ApprovalNeeded as exc:
        tools.decide(User(1, 'admin', 'admin'), exc.approval['id'], True)
        return tools.execute(user, name, values)


@pytest.mark.parametrize('origin', ['eva', 'mcp', 'connector', 'api'])
def test_eva_tools_mcp_and_the_connector_never_reach_the_session(app, origin):
    app, admin = app
    assert admin('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'mcp.allow_writes', 'value': True}]})[0] == 200
    assert run_tool(app, origin, 'call_service', {'service': 'test.session_probe', 'arguments': {}}) == {'session': 403}
    report = run_tool(app, origin, 'run_report', {'report': 'SessionReport'})
    assert report['rows'] == [{'session': '403'}]
    # An action they may run still runs the app's own handler, which alone gets its transaction session.
    stock(app.database)
    record_id = transfer(admin)
    result = run_tool(app, origin, 'run_action', {'model': 'transfer', 'id': record_id, 'action': 'probe'})
    assert result['_outcome'] == {'rows': 1}


def test_a_scheduled_eva_task_cannot_even_ask(app):
    app, admin = app
    with pytest.raises(ConnectorError, match='needs approval'):
        app.business.tools.execute(as_origin('schedule'), 'call_service', {'service': 'test.session_probe', 'arguments': {}})
    assert run_tool(app, 'schedule', 'run_report', {'report': 'SessionReport'})['rows'] == [{'session': '403'}]
