"""Moves within the release: process steps declared release=True (an invoice unpaid, overdue, paid) change the
State without making a release, taking a snapshot or recalculating, on real SQLite through the ASGI routes."""

from datetime import date

import pytest

from evlet import Database, Date, Decimal, Link, Model, Registry, State, Text, ValidationError, release, state, validate
from evlet.calc import F
from evlet.periods import save_lock
from evlet.auth import User
from evlet.identity import RecordId
from support import client
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
        save_lock(tx.connection, '', date(2026, 1, 31), ADMIN)
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


def test_a_move_within_the_release_may_not_change_versioned_content(bills):
    app, c, url, record_id = bills
    assert move(c, url, 'overdue')[0] == 200
    CALLS.clear()
    status, body, _ = move(c, url, 'adjust', {'qty': '5'})
    # Posted entries keep matching the document: reopen it to change what it says.
    assert (status, body) == (409, {'error': 'qty is part of the overdue document; reopen it to change it'})
    assert releases(app, record_id) == [(0, 0)] and c('GET', url)[1]['docstate'] == 'overdue'
    # A Restore there is refused too: the step is locked.
    status, body, _ = c('POST', url + '/restore', {'version': 0, 'remark': 'x'}, headers=etag(c, url))
    assert status == 409 and 'editable state' in body['error']


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


def test_recompute_keeps_released_records_without_any_effect(tmp_path):
    from evlet import AddIndex, Migration, Recompute
    path, base = tmp_path / 'r.sqlite3', Migration('0000_base', AddIndex('bill', 'paid_note'))
    app = start(Database(path, Registry(Bill), migrations=[base]))
    c = client(app)
    released, draft = (c('POST', '/api/bill', {'qty': '2'})[1]['id'] for _ in range(2))
    assert move(c, f'/api/bill/{released}', 'post')[0] == 200
    for target in (released, draft):
        store_double(app, target, 990000)
    app.database.close()
    steps = [base, Migration('0001_recompute', Recompute('bill', ('double',)))]
    db = Database(path, Registry(Bill), migrations=steps)
    applied = db.migrate(db.migrations)
    # The released bill keeps its saved result, without any effect; the draft one is recalculated.
    assert applied == ['0001_recompute'] and applied.skipped == 1
    with db._connection() as connection:
        assert [row[0] for row in connection.execute('SELECT double FROM bill ORDER BY id')] == [990000, 40000]


# ---------- @validate runs once per save, before @release (docs/states-actions.md) ----------

class Invoice(Model):
    permissions = ALL
    total = Decimal(scale=2, default=0)
    paid = Decimal(scale=2, default=0)


class Payment(Model):
    """Its check reads what the invoice still owes; posting it settles that amount on the invoice."""
    permissions = ALL
    invoice = Link(Invoice, required=True)
    amount = Decimal(scale=2, required=True)
    reference = Text(required=True)
    docstate = State(State.Step('draft'), State.Step('posted', release=True, lock=True),
                     rules=(State.Rule('draft', 'posted', 'Post', key='post'),))

    @validate
    def within_outstanding(self):
        CALLS.append(('validate', self.docstate))
        invoice = self.context.get(Invoice, self.invoice)
        if self.amount > invoice.total - invoice.paid:
            raise ValidationError({'amount': 'The amount is more than the outstanding'})

    @release
    def settle_invoice(self):
        invoice = self.context.get(Invoice, self.invoice)
        invoice.paid = invoice.paid + self.amount
        invoice.save()
        if self.reference == 'clear me':
            self.reference = None


@pytest.fixture
def payments(tmp_path):
    CALLS.clear()
    app = start(Database(tmp_path / 'payments.sqlite3', Registry(Invoice, Payment)))
    with app.business.context(ADMIN) as context:
        app.invoice = context.create(Invoice, total='100.00').id
    yield app
    app.database.close()


def pay(app, amount, reference='bank'):
    with app.business.context(ADMIN) as context:
        payment = context.create(Payment, invoice=app.invoice, amount=amount, reference=reference)
        CALLS.clear()
        payment.move('post')
        return context.get(Invoice, app.invoice).paid


def test_validate_runs_once_before_release(payments):
    assert pay(payments, '100.00') == Decimal().validate('100.00')
    assert CALLS == [('validate', 'posted')]
    # A second payment over what is left is refused by the same check.
    with pytest.raises(ValidationError) as error:
        pay(payments, '1.00')
    assert error.value.errors == {'amount': 'The amount is more than the outstanding'}


def test_field_checks_still_run_after_release(payments):
    with pytest.raises(ValidationError) as error:
        pay(payments, '40.00', reference='clear me')
    assert error.value.errors == {'reference': 'This field is required'}
    with payments.business.context(ADMIN, readonly=True) as context:
        assert context.get(Invoice, payments.invoice).paid == Decimal().validate('0')
