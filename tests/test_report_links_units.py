"""Report columns that drill down to another report (Column(report=, filters=)) and totals whose unit comes from
the report's filters (Column(unit=Ref(...)) or a callable), on real SQLite."""

import pytest

from evlet import Application, Column, Currency, Database, Date, Link, Model, Ref, Registry, Report, Sum, Text
from evlet.auth import LocalAuth
from support import PASSWORD, client, http
from test_rules_types import ALL


class Company(Model):
    permissions = ALL
    code = Text(default='')
    currency = Text(default='AED', max_length=3)


class Account(Model):
    permissions = ALL
    code = Text(default='')


class Entry(Model):
    permissions = ALL
    company = Link(Company, required=True)
    account = Link(Account, required=True)
    on = Date(required=True)
    amount = Currency(required=True)


def entry_filters(report):
    filters = {}
    if report.company: filters['company'] = report.company
    if getattr(report, 'account', None): filters['account'] = report.account
    if report.from_date: filters['on__gte'] = report.from_date
    return filters


class ByAccount(Report):
    title = 'By account'
    roles = ('admin', 'editor', 'viewer')
    autorun = True
    company = Link(Company)
    from_date = Date()
    columns = (Column('account', type='link', link='account', report='AccountLines',
                      filters={'account': 'account', 'company': 'company', 'from_date': 'from_date'}),
               Column('amount', type='currency', total=True, unit=Ref('company.currency')))

    def run(self):
        groups = self.context.aggregate(Entry, measures={'amount': Sum('amount')}, group_by=('account',), filters=entry_filters(self))
        return {'rows': groups[self.context.offset:self.context.offset + self.context.limit], 'total': len(groups)}


def company_currency(report):
    """A callable unit: the report with its filters in, the unit's text out (None: no common unit)."""
    return report.context.get(Company, report.company).currency if report.company else None


class AccountLines(Report):
    title = 'Account lines'
    roles = ('admin', 'editor')
    company = Link(Company)
    account = Link(Account)
    from_date = Date()
    columns = (Column('on', type='date'), Column('amount', type='currency', total=True, unit=company_currency))

    def run(self):
        page = self.context.list(Entry, filters=entry_filters(self), order_by=('on',), limit=self.context.limit, offset=self.context.offset)
        return {'rows': [{'on': row['on'], 'amount': row['amount']} for row in page['rows']], 'total': page['total']}


@pytest.fixture
def books(tmp_path):
    database = Database(tmp_path / 'units.sqlite3', Registry(Company, Account, Entry))
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True),
                      reports=(ByAccount, AccountLines))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    assert client(app)('POST', '/api/auth/users', {'username': 'viewer', 'password': PASSWORD, 'role': 'viewer'})[0] == 201
    with app.database.transaction() as tx:
        north, south = tx.create(Company, code='north', currency='KWD').id, tx.create(Company, code='south', currency='USD').id
        cash = tx.create(Account, code='cash').id
        tx.create(Entry, company=north, account=cash, on='2026-03-01', amount='10.000')
        tx.create(Entry, company=north, account=cash, on='2026-03-05', amount='1.250')
        tx.create(Entry, company=south, account=cash, on='2026-03-02', amount='4.00')
    return app, north, south, cash


def catalog_columns(c, name):
    return {column['name']: column for column in next(item for item in c('GET', '/api/_reports')[1]['reports'] if item['name'] == name)['columns']}


def test_a_total_takes_its_unit_from_the_filters(books):
    app, north, _, cash = books
    admin = client(app)
    columns = catalog_columns(admin, 'ByAccount')
    assert columns['amount']['unit'] is None and columns['amount']['unit_from_filters'] is True
    status, page, _ = admin('POST', '/api/_reports/ByAccount', {'filters': {'company': str(north)}})
    assert status == 200, page
    assert page['units'] == {'amount': 'KWD'} and page['totals']['values'] == {'amount': '11.25'}
    assert page['rows'] == [{'account': str(cash), 'amount': '11.25'}]
    # Every company at once: amounts in different currencies have no common unit, so there is no total.
    status, page, _ = admin('POST', '/api/_reports/ByAccount', {'filters': {}})
    assert status == 200 and page['units'] == {'amount': None} and 'totals' not in page
    # The export names the unit of its run, and keeps the currency's places.
    status, body, _ = admin('POST', '/api/_reports/ByAccount/export.csv', {'filters': {'company': str(north)}})
    assert status == 200 and body.decode('utf-8-sig').splitlines() == ['Account,Amount (KWD)', f'{cash},11.250']
    # A callable unit.
    status, page, _ = admin('POST', '/api/_reports/AccountLines', {'filters': {'company': str(north), 'account': str(cash)}})
    assert status == 200 and page['units'] == {'amount': 'KWD'} and page['totals']['values'] == {'amount': '11.25'}


def test_a_column_drills_down_to_another_report(books):
    app, north, _, cash = books
    admin, viewer = client(app), client(app, 'viewer')
    assert catalog_columns(admin, 'ByAccount')['account']['report'] == 'AccountLines'
    assert catalog_columns(admin, 'ByAccount')['account']['filters'] == {'account': 'account', 'company': 'company', 'from_date': 'from_date'}
    # The viewer cannot run Account lines, so their column links to no report.
    assert 'report' not in catalog_columns(viewer, 'ByAccount')['account']
    # The linked report reads the filters the link carries.
    status, page, _ = admin('POST', '/api/_reports/AccountLines', {'filters': {'company': str(north), 'account': str(cash), 'from_date': '2026-03-02'}})
    assert status == 200 and page['rows'] == [{'on': '2026-03-05', 'amount': '1.25'}]


def build(tmp_path, *reports):
    database = Database(tmp_path / 'wrong.sqlite3', Registry(Company, Account, Entry))
    return Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False), reports=reports)


def report(columns, **filters):
    return type('Wrong', (Report,), {'title': 'Wrong', 'roles': ('admin',), 'columns': columns, 'company': Link(Company), **filters,
                                     'run': lambda self: {'rows': [], 'total': 0}})


@pytest.mark.parametrize('columns, message', [
    ((Column('amount', type='currency', total=True, unit=Ref('missing.currency')),), 'has no field missing'),
    ((Column('amount', type='currency', total=True, unit=Ref('company.code.more')),), 'through code, which is not a Link'),
    ((Column('amount', type='currency', total=True, unit=Ref('company.colour')),), 'Company has no field colour'),
    ((Column('code', report='Nowhere'),), 'unknown report: Nowhere'),
    ((Column('code', report='ByAccount', filters={'colour': 'code'}),), 'ByAccount has no filter colour'),
    ((Column('code', report='ByAccount', filters={'company': 'missing'}),), 'neither a column nor a filter'),
])
def test_wrong_units_and_links_fail_when_the_application_is_built(tmp_path, columns, message):
    with pytest.raises(ValueError, match=message):
        build(tmp_path, ByAccount, AccountLines, report(columns))


def test_column_options_are_checked():
    with pytest.raises(ValueError, match='column unit'):
        Column('amount', type='currency', total=True, unit=3)
    with pytest.raises(ValueError, match='go with report='):
        Column('code', filters={'a': 'b'})
    with pytest.raises(ValueError, match="filter to this report's column"):
        Column('code', report='ByAccount', filters={'a': 1})
    assert hash(Column('code', report='ByAccount', filters={'a': 'b'})) == hash(Column('code', report='ByAccount', filters={'a': 'c'}))
