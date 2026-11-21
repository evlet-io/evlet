"""Period locks (Meta.period): closed periods refuse every writer, mirror rows move to the first open date, on real SQLite."""

from datetime import date, datetime, timezone

import pytest

from evlet import (Application, Boolean, Currency, Database, Date, DateTime, Link, Model, Reference,
                   Registry, Rule, State, Text, ValidationError, release)
from evlet.auth import AccessError, LocalAuth, User
from evlet.periods import set_lock
from finite_fixtures import FiniteMeta
from support import PASSWORD, client, http
from test_rules_types import ALL

FULL = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'))}
ADMIN = User(1, 'admin', 'admin')
CLOSED = 'This date is in a closed period (locked up to {})'


class Company(Model):
    permissions = FULL
    code = Text(default='')


class Booking(Model):
    class Meta:
        period = 'booked_on'
        period_scope = 'company'
    permissions = FULL
    company = Link(Company)
    booked_on = Date()
    memo = Text(default='')


class Stamp(Model):
    """A plain model dated by a DateTime, read in the instance timezone."""
    class Meta:
        period = 'at'
    at = DateTime()
    memo = Text(default='')


class Posting(Model):
    class Meta:
        period = 'posted_on'
    permissions = {'admin': Rule(('read',))}
    posted_on = Date(required=True)
    amount = Currency(default=0)
    source = Reference(index=True)
    reversal = Boolean(default=False)


def voucher_meta(**extra):
    return type('Meta', (FiniteMeta,), extra)


class Posts:
    """Post one row dated by the document when it is posted, and its reversal when it leaves."""
    @release
    def post(self):
        self.context.session.create(Posting, posted_on=self.dated, amount=self.amount, source=(self.NAME, self.id))

    @release.exit
    def unpost(self):
        self.context.session.create(Posting, posted_on=self.dated, amount=-self.amount, source=(self.NAME, self.id),
                                    reversal=True)


def status():
    return State(State.Step('draft'), State.Step('posted', lock=True, release=True), State.Step('void', lock=True, final=True),
                 rules=(State.Rule('draft', 'posted', 'Post', key='posted'), State.Rule('posted', 'draft', 'Reopen', key='reopen', remark='required'),
                        State.Rule('posted', 'void', 'Void', key='void', remark='required')))


CORRECTING = {'admin': Rule(('read', 'create', 'update', 'delete'))}


class Voucher(Posts, Model):
    """Posts rows dated by its own date, without a period of its own."""
    NAME = 'voucher'
    Meta = voucher_meta()
    permissions = CORRECTING
    dated = Date()
    amount = Currency(default=0)
    docstate = status()


class Receipt(Posts, Model):
    """A posting document with its own period."""
    NAME = 'receipt'
    Meta = voucher_meta(period='dated')
    permissions = CORRECTING
    dated = Date()
    amount = Currency(default=0)
    docstate = status()


MODELS = (Company, Booking, Stamp, Posting, Voucher, Receipt)


@pytest.fixture
def books(tmp_path):
    registry = Registry(*MODELS)
    database = Database(tmp_path / 'periods.sqlite3', registry)
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app, client(app)


def lock(app, value, scope=''):
    with app.database.transaction() as tx:
        set_lock(tx.connection, scope, value, ADMIN)


def etag(c, url):
    return {b'if-match': c('GET', url)[2][b'etag']}


# Registration

@pytest.mark.parametrize('meta, message', [
    ({'period': 'missing'}, "Meta.period names a stored Date or DateTime field \\('missing' is not one\\)"),
    ({'period': 'memo'}, "Meta.period names a stored Date or DateTime field \\('memo' is not one\\)"),
    ({'period': 'day', 'period_scope': 'memo'}, "Meta.period_scope names a stored Link field \\('memo' is not one\\)"),
    ({'period_scope': 'company'}, 'Meta.period_scope needs Meta.period'),
])
def test_period_declarations_are_checked(meta, message):
    model = type('Dated', (Model,), {'Meta': type('Meta', (), meta), 'day': Date(), 'memo': Text(), 'company': Link(Company)})
    with pytest.raises(ValueError, match=message):
        Registry(Company, model)


# Enforcement over HTTP

def test_http_writes_in_a_closed_period_are_refused(books):
    app, c = books
    lock(app, date(2026, 3, 31))
    status, payload, _ = c('POST', '/api/booking', {'booked_on': '2026-03-31'})
    assert status == 422 and payload['fields'] == {'booked_on': CLOSED.format('31-03-2026')}
    status, open_one, _ = c('POST', '/api/booking', {'booked_on': '2026-04-01', 'memo': 'open'})
    assert status == 201, open_one
    url = f"/api/booking/{open_one['id']}"
    # Moving a date into a closed period.
    status, payload, _ = c('PATCH', url, {'booked_on': '2026-03-01'}, headers=etag(c, url))
    assert status == 422 and payload['fields'] == {'booked_on': CLOSED.format('31-03-2026')}
    with app.database.transaction() as tx, tx.unlocked_periods('Load the March bookings'):
        closed = tx.create(Booking, booked_on=date(2026, 3, 10), memo='filed').id
    url = f'/api/booking/{closed}'
    # Changing, moving out of, and deleting a record in a closed period.
    for method, body in (('PATCH', {'memo': 'changed'}), ('PATCH', {'booked_on': '2026-04-02'}), ('DELETE', None)):
        status, payload, _ = c(method, url, body, headers=etag(c, url))
        assert status == 422 and payload['fields'] == {'booked_on': CLOSED.format('31-03-2026')}, (method, payload)
    assert c('GET', url)[1]['memo'] == 'filed'
    # A record without a date is in no period.
    assert c('POST', '/api/booking', {'memo': 'undated'})[0] == 201


def test_datetime_periods_follow_the_instance_timezone(books):
    app, c = books
    lock(app, date(2026, 3, 31))
    assert c('POST', '/api/stamp', {'at': '2026-03-31T19:30:00Z'})[1]['fields'] == {'at': CLOSED.format('31-03-2026')}
    app.database.timezone = 'Asia/Dubai'
    # 20:30 UTC on 31 March is 00:30 on 1 April in Dubai.
    assert c('POST', '/api/stamp', {'at': '2026-03-31T20:30:00Z'})[0] == 201
    assert c('POST', '/api/stamp', {'at': '2026-03-31T19:30:00Z'})[0] == 422


def test_scope_locks_combine_with_the_instance_lock(books):
    app, c = books
    with app.database.transaction() as tx:
        north, south, east = (tx.create(Company, code=code).id for code in ('north', 'south', 'east'))
    lock(app, date(2026, 3, 31))
    lock(app, date(2026, 4, 30), f'company:{north}')
    lock(app, date(2026, 1, 31), f'company:{east}')
    def book(company, day):
        return c('POST', '/api/booking', {'company': str(company), 'booked_on': day})
    assert book(north, '2026-04-15')[1]['fields'] == {'booked_on': CLOSED.format('30-04-2026')}
    assert book(south, '2026-04-15')[0] == 201
    # A scope lock closes more, never less: the instance lock still applies to east.
    assert book(east, '2026-03-15')[1]['fields'] == {'booked_on': CLOSED.format('31-03-2026')}
    assert book(east, '2026-04-01')[0] == 201


# Trusted Python

def test_python_sessions_and_contexts_are_refused_too(books):
    app, _ = books
    lock(app, date(2026, 3, 31))
    with pytest.raises(ValidationError) as caught:
        with app.database.transaction() as tx:
            tx.create(Stamp, at=datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert caught.value.errors == {'at': CLOSED.format('31-03-2026')}
    with pytest.raises(ValidationError):
        with app.business.context(ADMIN) as context:
            context.create(Booking, booked_on='2026-03-02')
    with app.database.transaction() as tx:
        with tx.unlocked_periods('Repair a mistyped date'):
            stamp = tx.create(Stamp, at=datetime(2026, 3, 1, tzinfo=timezone.utc), memo='kept').id
        for write in (lambda: tx.update(Stamp, stamp, memo='changed'), lambda: tx.delete(Stamp, stamp)):
            with pytest.raises(ValidationError):
                write()
    with app.business.context(ADMIN) as context, context.unlocked_periods('Repair through the document pipeline'):
        context.create(Booking, booked_on='2026-03-02')
    with pytest.raises(ValueError, match='Give the reason'):
        with app.database.transaction() as tx, tx.unlocked_periods(' '):
            pass
    with app.database.transaction() as tx:
        audit = [tuple(row) for row in tx.connection.execute("SELECT actor, area, detail FROM evlet_audit WHERE area = 'Periods' ORDER BY id")]
    assert audit[-2:] == [('python', 'Periods', 'Locks bypassed: Repair a mistyped date'),
                          ('python', 'Periods', 'Locks bypassed: Repair through the document pipeline')]


# Postings in release hooks

def voucher(c, model, day, amount='10.00'):
    status, record, _ = c('POST', f'/api/{model}', {'dated': day, 'amount': amount})
    assert status == 201, record
    url = f"/api/{model}/{record['id']}"
    status, moved, _ = c('POST', f'{url}/moves/posted', {}, headers=etag(c, url))
    assert status == 200, moved
    return url


def postings(app):
    with app.database.transaction() as tx:
        # Stored millionths, shown here as hundredths.
        return [(row['posted_on'], row['amount'] // 10000, bool(row['reversal'])) for row in
                tx.connection.execute('SELECT posted_on, amount, reversal FROM posting ORDER BY id').fetchall()]


def test_postings_into_a_closed_period_and_closed_documents_are_refused(books):
    app, c = books
    open_receipt = voucher(c, 'receipt', '2026-03-05')
    lock(app, date(2026, 3, 31))
    # A voucher dated in an open period posts; one whose rows land in a closed period does not.
    voucher(c, 'voucher', '2026-04-02')
    status, record, _ = c('POST', '/api/voucher', {'dated': '2026-03-20', 'amount': '1.00'})
    url = f"/api/voucher/{record['id']}"
    status, payload, _ = c('POST', f'{url}/moves/posted', {}, headers=etag(c, url))
    assert status == 422 and payload['fields'] == {'posted_on': CLOSED.format('31-03-2026')}, payload  # The posting's own write refuses it.
    # A document dated in a closed period is neither voided nor reopened: its reversal, dated like it, is refused first.
    for operation in ('void', 'reopen'):
        status, payload, _ = c('POST', f'{open_receipt}/moves/{operation}', {'remark': 'Late fix'}, headers=etag(c, open_receipt))
        assert status == 422 and payload['fields'] == {'posted_on': CLOSED.format('31-03-2026')}, operation
    assert c('GET', open_receipt)[1]['docstate'] == 'posted'
    assert [row for row in postings(app) if row[2]] == []


# Controls

def test_controls_sets_locks_for_admin_only_and_keeps_their_history(books):
    app, admin = books
    with app.database.transaction() as tx:
        north = tx.create(Company, code='north').id
    status, page, _ = admin('POST', '/api/_controls/periods', {'scope': None, 'date': '2026-03-31'})
    assert status == 200, page
    status, page, _ = admin('POST', '/api/_controls/periods', {'scope': {'model': 'company', 'id': str(north)}, 'date': '2026-04-30'})
    assert status == 200, page
    assert [(lock['scope'], lock['date'], lock['changed_by']) for lock in page['locks']] == [('', '2026-03-31', 'admin'),
                                                                                         (f'company:{north}', '2026-04-30', 'admin')]
    assert {model['name']: model['scope'] for model in page['models']}['booking'] == {'field': 'company', 'model': 'company', 'label': 'Company'}
    assert [(row['actor'], row['detail']) for row in page['history']][:2] == [('admin', 'Locked up to 30-04-2026'), ('admin', 'Locked up to 31-03-2026')]
    assert admin('POST', '/api/booking', {'booked_on': '2026-03-30'})[0] == 422
    for body, field in (({'scope': None, 'date': '31-03-2026'}, 'date'), ({'scope': {'model': 'booking', 'id': str(north)}, 'date': '2026-03-31'}, 'scope'),
                        ({'scope': None}, '_form')):
        status, payload, _ = admin('POST', '/api/_controls/periods', body)
        assert status == 422 and field in payload['fields'], payload
    assert admin('POST', '/api/_controls/periods', {'scope': None, 'date': None})[1]['locks'][0]['scope'] == f'company:{north}'
    assert admin('POST', '/api/booking', {'booked_on': '2026-03-30'})[0] == 201
    with pytest.raises(AccessError, match='Only an admin'):
        app.business.controls.set_period(User(2, 'manager', 'editor'), {'scope': None, 'date': '2026-01-31'})
    assert admin('POST', '/api/auth/users', {'username': 'editor', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    assert client(app, 'editor')('POST', '/api/_controls/periods', {'scope': None, 'date': '2026-12-31'})[0] == 404


# Playground

def test_playground_posts_invoices_to_a_read_only_ledger_and_locks_periods(secured):
    app = secured
    admin, editor = client(app), client(app, 'editor')
    customer = admin('POST', '/api/customer', {'customer_name': 'Acme'})[1]['id']
    status, invoice, _ = editor('POST', '/api/invoice', {'customer': customer, 'reference': 'PO-1', 'total': '100.00', 'posting_date': '2026-03-10'})
    assert status == 201, invoice
    url = f"/api/invoice/{invoice['id']}"
    assert editor('POST', f'{url}/actions/post', {}, headers=etag(editor, url))[0] == 200
    entries = admin('GET', '/api/ledgerentry')[1]['items']
    assert [(entry['posting_date'], entry['amount'], entry['memo']) for entry in entries] == [('2026-03-10', '100.00', 'PO-1')]
    assert admin('POST', '/api/ledgerentry', {'invoice': invoice['id'], 'customer': customer, 'posting_date': '2026-03-10', 'amount': '1.00'})[0] == 403
    assert admin('POST', f'{url}/actions/post', {}, headers=etag(admin, url))[1]['fields'] == {'_form': 'This invoice is posted already'}
    assert admin('POST', '/api/_controls/periods', {'scope': None, 'date': '2026-03-31'})[0] == 200
    status, payload, _ = editor('PATCH', url, {'reference': 'PO-1b'}, headers=etag(editor, url))
    assert status == 422 and payload['fields'] == {'posting_date': CLOSED.format('31-03-2026')}
    status, late, _ = editor('POST', '/api/invoice', {'customer': customer, 'reference': 'PO-2', 'total': '5.00', 'posting_date': '2026-03-30'})
    assert status == 422 and late['fields'] == {'posting_date': CLOSED.format('31-03-2026')}
