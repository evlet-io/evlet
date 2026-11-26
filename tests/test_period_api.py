"""evlet.periods.lock_date and set_lock: period locks read and set from trusted code, with the Periods page's rules
and audit rows, on real SQLite."""

from datetime import date, datetime

import pytest

import evlet
from evlet import Application, Database, Date, Link, Model, Registry, Rule, Text, ValidationError, action
from evlet.auth import AccessError, LocalAuth, User
from evlet.periods import lock_date, save_lock, set_lock
from evlet.permissions import calling
from support import PASSWORD, client, http
from test_rules_types import ALL

FULL = {**ALL, 'editor': Rule(('read', 'create', 'update', 'delete'))}
ADMIN, EDITOR = User(1, 'admin', 'admin'), User(2, 'editor', 'editor')


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


class Note(Model):
    """Dated, with one lock for every record."""
    class Meta:
        period = 'on'
    permissions = FULL
    on = Date()


class Closing(Model):
    """Month end: its action closes a company's periods from trusted code, over HTTP."""
    permissions = FULL
    company = Link(Company)
    up_to = Date()

    @action
    def close(self):
        evlet.periods.set_lock(self.context, Booking, self.company, self.up_to, 'Month end closing')


@pytest.fixture
def books(tmp_path):
    database = Database(tmp_path / 'locks.sqlite3', Registry(Company, Booking, Note, Closing))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    with app.database.transaction() as tx:
        north, south = tx.create(Company, code='north').id, tx.create(Company, code='south').id
    return app, north, south


def audit_rows(app):
    with app.database.transaction() as tx:
        return [tuple(row) for row in tx.connection.execute(
            "SELECT actor, target, detail, before, after FROM evlet_audit WHERE area = 'Periods' ORDER BY id")]


def test_set_lock_and_lock_date_follow_the_page_rules(books):
    app, north, south = books
    assert evlet.periods is evlet.periods and 'periods' in evlet.__all__
    with app.business.context(ADMIN) as context:
        assert lock_date(context, Booking, north) is None
        assert set_lock(context, Booking, None, date(2026, 3, 31), 'Close Q1') is None
        assert set_lock(context, Booking, north, date(2026, 4, 30), 'Close April for North') is None
        # The later of the instance lock and the scope's own lock applies.
        assert lock_date(context, Booking, north) == date(2026, 4, 30)
        assert lock_date(context, Booking, south) == date(2026, 3, 31)
        assert lock_date(context, 'booking') == date(2026, 3, 31)
        # The lock applies at once, in the same transaction.
        with pytest.raises(ValidationError, match='closed period'):
            context._tx.create(Booking, company=north, booked_on=date(2026, 4, 15))
        context._tx.create(Booking, company=south, booked_on=date(2026, 4, 15))
    with app.database.transaction() as tx:
        assert lock_date(tx, Booking, north) == date(2026, 4, 30)  # A transaction session reads too.
    assert audit_rows(app) == [('admin', 'All records', 'Locked up to 31-03-2026: Close Q1', None, '"2026-03-31"'),
                               ('admin', f'company:{north}', 'Locked up to 30-04-2026: Close April for North', None, '"2026-04-30"')]
    # The Periods page lists what set_lock saved.
    page = app.business.controls.periods()
    assert [(lock['scope'], lock['date'], lock['changed_by']) for lock in page['locks']] == [('', '2026-03-31', 'admin'),
                                                                                         (f'company:{north}', '2026-04-30', 'admin')]


def test_only_an_admin_moves_a_lock_earlier_or_opens_it(books):
    app, north, _ = books
    with app.business.context(EDITOR) as context:
        set_lock(context, Booking, north, date(2026, 4, 30), 'April')
        assert set_lock(context, Booking, north, date(2026, 5, 31), 'May') == date(2026, 4, 30)  # Forward: anyone trusted.
        assert set_lock(context, Booking, north, date(2026, 5, 31), 'Again') == date(2026, 5, 31)  # Unchanged: no audit row.
        for value in (date(2026, 4, 30), None):
            with pytest.raises(AccessError, match='Only an admin') as caught:
                set_lock(context, Booking, north, value, 'Reopen')
            assert caught.value.status == 403
    with app.business.context(ADMIN) as context:
        assert set_lock(context, Booking, north, date(2026, 4, 30), 'Correct May') == date(2026, 5, 31)
        assert set_lock(context, Booking, north, None, 'Reopen North') == date(2026, 4, 30)
        assert lock_date(context, Booking, north) is None
    assert [row[2] for row in audit_rows(app)] == ['Locked up to 30-04-2026: April', 'Locked up to 31-05-2026: May',
                                                   'Locked up to 30-04-2026: Correct May', 'Opened: Reopen North']


def test_set_lock_checks_its_arguments_and_refuses_callers(books):
    app, north, _ = books
    with app.business.context(ADMIN) as context:
        with pytest.raises(ValueError, match='reason'):
            set_lock(context, Booking, None, date(2026, 3, 31), ' ')
        with pytest.raises(ValueError, match='datetime.date'):
            set_lock(context, Booking, None, datetime(2026, 3, 31), 'Q1')
        with pytest.raises(ValueError, match='has no Meta.period'):
            set_lock(context, Company, None, date(2026, 3, 31), 'Q1')
        with pytest.raises(ValueError, match='has no Meta.period_scope'):
            set_lock(context, Note, north, date(2026, 3, 31), 'Q1')
        with pytest.raises(ValidationError):
            set_lock(context, Booking, '0000000000000', date(2026, 3, 31), 'Q1')
        with pytest.raises(ValidationError):
            lock_date(context, Booking, 'not an id')
        # A caller served over HTTP never sets a lock; trusted code inside it does.
        with calling(), pytest.raises(AccessError, match='trusted code'):
            set_lock(context, Note, None, date(2026, 3, 31), 'Q1')
        with calling(), evlet.trusted():
            set_lock(context, Note, None, date(2026, 3, 31), 'Q1')
        assert lock_date(context, Note) == date(2026, 3, 31)
    with app.business.context(ADMIN, readonly=True) as context, pytest.raises(AccessError, match='read only'):
        set_lock(context, Note, None, date(2026, 4, 30), 'Q1')


def test_an_action_sets_a_lock_over_http(books):
    app, north, _ = books
    admin = client(app)
    assert admin('POST', '/api/auth/users', {'username': 'editor', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    editor = client(app, 'editor')
    status, closing, _ = editor('POST', '/api/closing', {'company': str(north), 'up_to': '2026-06-30'})
    assert status == 201, closing
    url = f"/api/closing/{closing['id']}"
    headers = {b'if-match': editor('GET', url)[2][b'etag']}
    status, payload, _ = editor('POST', f'{url}/actions/close', {}, headers=headers)
    assert status == 200, payload
    assert audit_rows(app)[-1][:3] == ('editor', f'company:{north}', 'Locked up to 30-06-2026: Month end closing')
    assert editor('POST', '/api/booking', {'company': str(north), 'booked_on': '2026-06-30'})[0] == 422


def test_the_old_internal_form_still_saves_with_a_warning(books):
    app, _, _ = books
    with app.database.transaction() as tx, pytest.warns(DeprecationWarning, match='set_lock'):
        set_lock(tx.connection, '', date(2026, 1, 31), ADMIN)
    with app.database.transaction() as tx:
        save_lock(tx.connection, '', date(2026, 2, 28), ADMIN)
        assert lock_date(tx, Note) == date(2026, 2, 28)
