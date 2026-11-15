"""Moves within the release: process steps declared release=True (an invoice unpaid, overdue, paid) change the
State without making a release, taking a snapshot or recalculating, on real SQLite through the ASGI routes."""

from datetime import date

import pytest

from evlet import Database, Date, Decimal, Model, Registry, State, Text, release, state
from evlet.calc import F
from evlet.periods import set_lock
from evlet.auth import User
from evlet.identity import RecordId
from test_business import client
from test_rules_types import ALL, start
from test_states import etag

CALLS = []


class Bill(Model):
    class Meta:
        calculation_language = 'finite-v1'
        numeric_profile = 'finite40-hu18-v1'
        period = 'billed_on'

    permissions = ALL
    billed_on = Date(default=date(2026, 1, 15))
    qty = Decimal(default=1)
    double = Decimal(compute=F('qty') * 2)
    # Where the bill is in its process: it may change while the bill is released.
    paid_note = Text(default='', versioned=False)
    docstate = State(
        State.Step('draft'),
        State.Step('unpaid', release=True, lock=True, color='amber'),
        State.Step('overdue', release=True, lock=True, color='rose'),
        State.Step('paid', release=True, lock=True, color='green'),
        State.Step('cancelled', lock=True, final=True),
        rules=(
            State.Rule('draft', 'unpaid', 'Post', key='post'),
            State.Rule('unpaid', 'overdue', 'Overdue', key='overdue'),
            State.Rule(('unpaid', 'overdue'), 'paid', 'Paid', key='pay', ask=('paid_note',)),
            State.Rule('overdue', 'unpaid', 'Adjust', key='adjust', ask=('qty',)),
            State.Rule(('unpaid', 'overdue', 'paid'), 'draft', 'Reopen', key='reopen'),
            State.Rule(('draft', 'unpaid', 'overdue'), 'cancelled', 'Cancel', key='cancel'),
        ))

    @state('unpaid', 'overdue', 'paid', 'draft', 'cancelled')
    def entered(self):
        CALLS.append(('enter', self.docstate))

    @state.exit('unpaid', 'overdue', 'paid')
    def left(self):
        CALLS.append(('exit', self.before.docstate))

    @release
    def released(self):
        CALLS.append(('release', self.release.number))

    @release.exit
    def left_release(self):
        # After the snapshot and @state.exit, before the state changes.
        CALLS.append(('release_exit', self.docstate))


ADMIN = User(1, 'admin', 'admin')


@pytest.fixture
def bills(tmp_path):
    CALLS.clear()
    app = start(Database(tmp_path / 'bills.sqlite3', Registry(Bill)))
    c = client(app)
    record = c('POST', '/api/bill', {'qty': '3'})[1]
    url = f"/api/bill/{record['id']}"
    assert move(c, url, 'post')[0] == 200
    CALLS.clear()
    yield app, c, url, record['id']
    app.database.close()


def move(c, url, key, body=None):
    return c('POST', f'{url}/moves/{key}', body or {}, headers=etag(c, url))


def releases(app, record_id):
    with app.database._connection() as connection:
        return [tuple(row) for row in connection.execute(
            'SELECT number, snapshot FROM evlet_releases WHERE record_id = ? ORDER BY number, snapshot', (int(RecordId(record_id)),)).fetchall()]


def store_double(app, record_id, value):
    # Outside the engine, so a recalculation would be visible: a move that keeps saved results leaves it.
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE bill SET double = ? WHERE id = ?', (value, int(RecordId(record_id))))


def test_moves_within_the_release_make_no_release_snapshot_or_recalculation(bills):
    app, c, url, record_id = bills
    assert releases(app, record_id) == [(0, 0)]
    store_double(app, record_id, 990000)
    for key, body, to in (('overdue', None, 'overdue'), ('pay', {'paid_note': 'bank transfer'}, 'paid')):
        status, detail, _ = move(c, url, key, body)
        assert status == 200 and detail['docstate'] == to, detail
        assert detail['double'] == '99.0000'
    assert detail['paid_note'] == 'bank transfer' and detail['_version']['number'] == 0
    assert releases(app, record_id) == [(0, 0)]
    assert CALLS == [('exit', 'unpaid'), ('enter', 'overdue'), ('exit', 'overdue'), ('enter', 'paid')]


def test_moves_within_the_release_pass_a_closed_period_but_edits_there_are_refused(bills):
    app, c, url, record_id = bills
    with app.database.transaction() as tx:
        set_lock(tx.connection, '', date(2026, 1, 31), ADMIN)
    assert move(c, url, 'overdue')[0] == 200
    assert move(c, url, 'pay', {'paid_note': 'cash'})[0] == 200
    status, body, _ = c('PATCH', url, {'paid_note': 'changed'}, headers=etag(c, url))
    assert status == 422 and 'closed period' in body['fields']['billed_on']
    # Leaving the released set changes what the period protects: refused like any other write there.
    status, body, _ = move(c, url, 'reopen')
    assert status == 422 and 'closed period' in body['fields']['billed_on']


def test_moves_within_the_release_survive_a_formula_change_without_recalculating(bills):
    app, c, url, record_id = bills
    with app.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_calculation_versions SET plan_hash = 'older' WHERE resource = 'bill'")
    store_double(app, record_id, 990000)
    status, detail, _ = move(c, url, 'overdue')
    assert status == 200 and detail['double'] == '99.0000', detail
    with app.database._connection() as connection:
        assert connection.execute("SELECT plan_hash FROM evlet_calculation_versions WHERE resource = 'bill'").fetchone()[0] == 'older'
    # Asking it to recalculate is refused: it keeps its saved results.
    plan = app.business.registry.plan_for(Bill)
    status, body, _ = c('POST', f'{url}/moves/pay', {'paid_note': 'x', '_calculation': {'model_schema_hash': plan['model_schema_hash'],
                                                                                         'recalculate': True}}, headers=etag(c, url))
    assert status == 409 and 'keeps its saved results' in body['error']


def test_a_move_within_the_release_that_changes_content_is_a_real_release(bills):
    app, c, url, record_id = bills
    assert move(c, url, 'overdue')[0] == 200
    CALLS.clear()
    status, detail, _ = move(c, url, 'adjust', {'qty': '5'})
    assert status == 200 and (detail['docstate'], detail['double'], detail['_version']['number']) == ('unpaid', '10.0000', 1), detail
    # The previous release is kept as a snapshot before the new one; @release runs; @release.exit does not.
    assert releases(app, record_id) == [(0, 0), (0, 1), (1, 0)]
    assert ('release', 1) in CALLS and not any(call[0] == 'release_exit' for call in CALLS)
    # In a closed period such a move is refused: it changes content.
    with app.database.transaction() as tx:
        set_lock(tx.connection, '', date(2026, 1, 31), ADMIN)
    assert move(c, url, 'overdue')[0] == 200
    status, body, _ = move(c, url, 'adjust', {'qty': '6'})
    assert status == 422 and 'closed period' in body['fields']['billed_on']


@pytest.mark.parametrize('key, to', [('reopen', 'draft'), ('cancel', 'cancelled')])
def test_leaving_the_release_snapshots_once_and_runs_release_exit(bills, key, to):
    app, c, url, record_id = bills
    assert move(c, url, 'overdue')[0] == 200
    CALLS.clear()
    status, detail, _ = move(c, url, key)
    assert status == 200 and detail['docstate'] == to, detail
    assert releases(app, record_id) == [(0, 0), (0, 1)]
    assert CALLS == [('exit', 'overdue'), ('release_exit', 'overdue'), ('enter', to)]
    if key == 'reopen':
        # Then the usual rules: edit, post again, a new release.
        assert c('PATCH', url, {'qty': '4'}, headers=etag(c, url))[0] == 200
        status, detail, _ = move(c, url, 'post')
        assert status == 200 and detail['_version']['number'] == 1 and detail['double'] == '8.0000'
        assert releases(app, record_id) == [(0, 0), (0, 1), (1, 0)]


def test_release_exit_needs_a_state():
    with pytest.raises(ValueError, match='@release.exit need a State'):
        class Plain(Model):
            title = Text()

            @release.exit
            def gone(self):
                pass
