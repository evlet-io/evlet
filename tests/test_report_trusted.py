"""Report.trusted and evlet.trusted: a report's query reads a ledger the person cannot open, while the report's own
roles still decide who runs it, on real SQLite."""

import pytest

import evlet
from evlet import Application, Column, Currency, Database, Date, Link, Model, Registry, Report, Rule, Sum, Text
from evlet.auth import LocalAuth, User
from evlet.permissions import CALLER, calling
from support import PASSWORD, client, http
from test_rules_types import ALL


class Account(Model):
    permissions = ALL
    code = Text(default='')


class Movement(Model):
    """A ledger only admins may open."""
    class Meta:
        ledger = True
    permissions = {'admin': Rule(('read',))}
    account = Link(Account, required=True)
    on = Date(required=True)
    amount = Currency(required=True)


def balances(session):
    return session.aggregate(Movement, measures={'amount': Sum('amount')}, group_by=('account',))


class Balances(Report):
    title = 'Balances'
    roles = ('admin', 'editor')
    trusted = True
    columns = (Column('account', type='link', link='account'), Column('amount', type='currency', total=True, unit='AED'))

    def run(self):
        rows = balances(self.context.session)
        return {'rows': rows[self.context.offset:self.context.offset + self.context.limit], 'total': len(rows)}


class Untrusted(Report):
    """The same query without Report.trusted: the person's rules apply, so an editor cannot read the ledger."""
    title = 'Untrusted'
    roles = ('admin', 'editor')
    columns = Balances.columns

    def run(self):
        rows = self.context.aggregate(Movement, measures={'amount': Sum('amount')}, group_by=('account',))
        return {'rows': rows, 'total': len(rows)}


class Peek(Report):
    """Without Report.trusted, run() gets no session."""
    title = 'Peek'
    roles = ('admin', 'editor')
    columns = Balances.columns

    def run(self):
        return {'rows': balances(self.context.session), 'total': 0}


@pytest.fixture
def ledger(tmp_path):
    database = Database(tmp_path / 'ledger.sqlite3', Registry(Account, Movement))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      reports=(Balances, Untrusted, Peek))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    for name, role in (('editor', 'editor'), ('viewer', 'viewer')):
        assert admin('POST', '/api/auth/users', {'username': name, 'password': PASSWORD, 'role': role})[0] == 201
    with app.database.transaction() as tx:
        cash = tx.create(Account, code='cash').id
        tx.create(Movement, account=cash, on='2026-03-01', amount='10.00')
        tx.create(Movement, account=cash, on='2026-03-02', amount='2.50')
    return app, cash


def test_a_trusted_report_reads_a_ledger_its_runner_cannot_open(ledger):
    app, cash = ledger
    editor = client(app, 'editor')
    status, page, _ = editor('POST', '/api/_reports/Balances', {'filters': {}})
    assert status == 200, page
    assert page['rows'] == [{'account': str(cash), 'amount': '12.50'}]
    # The report's roles still decide who runs it.
    assert client(app, 'viewer')('POST', '/api/_reports/Balances', {'filters': {}})[0] == 403
    # Without Report.trusted the editor's own rules refuse the ledger, and run() has no session.
    assert editor('POST', '/api/_reports/Untrusted', {'filters': {}})[0] == 403
    assert editor('POST', '/api/_reports/Peek', {'filters': {}})[0] == 403
    # The export runs the same way.
    status, body, _ = editor('POST', '/api/_reports/Balances/export.csv', {'filters': {}})
    assert status == 200 and b'12.50' in body


def test_report_trusted_is_a_boolean(tmp_path):
    database = Database(tmp_path / 'wrong.sqlite3', Registry(Account, Movement))
    Wrong = type('Wrong', (Report,), {'title': 'Wrong', 'roles': ('admin',), 'trusted': 'yes', 'columns': (Column('a'),),
                                      'run': lambda self: {'rows': [], 'total': 0}})
    with pytest.raises(ValueError, match='trusted is True or False'):
        Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False), reports=(Wrong,))


def test_trusted_is_exported_and_lifts_the_caller_surface():
    assert evlet.trusted is __import__('evlet.permissions', fromlist=['trusted']).trusted and 'trusted' in evlet.__all__
    with calling():
        assert CALLER.get() is True
        with evlet.trusted():
            assert CALLER.get() is False
        assert CALLER.get() is True
    assert User(1, 'admin', 'admin').role == 'admin'
