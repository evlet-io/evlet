from datetime import datetime, timezone
import json

import pytest

from evlet import (Model, Text, Integer, Rule, Dialog, Report, Column, Chart, Sum, Registry, Database,
                   Application, LocalAuth, api, cron, ValidationError)
from evlet.forms import Forms
from evlet.services import Calendar, Endpoint
from test_business import http, client, PASSWORD


class Ticket(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    name = Text(required=True)
    copies = Integer(default=0)
    class Copy(Dialog):
        title = 'Create copies'
        action = 'copy'
        count = Integer(required=True, minimum=1, maximum=3, default=1)
        def submit(self, source):
            rows = []
            for index in range(self.count):
                rows.append(self.context.create(Ticket, name=source.name + ' copy')['id'])
            self.context.update(Ticket, source.id, copies=source.copies + self.count)
            return {'created': rows}


class Tickets(Report):
    title = 'Tickets'
    permissions = ('admin',)
    term = Text(default='')
    columns = (Column('id', type='integer'), Column('name'), Column('copies', type='integer'))
    def run(self):
        return self.context.list(Ticket, filters={'name__contains': self.term} if self.term else {}, limit=self.context.limit, offset=self.context.offset)


@api(method='GET', permission=(Ticket, 'read'))
def count_tickets(context, term: str = ''):
    return {'count': context.count(Ticket, filters={'name': term} if term else {})}


@pytest.fixture
def app(tmp_path):
    db = Database(tmp_path / 'forms.sqlite3', Registry(Ticket))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(db, auth=auth, reports=(Tickets,), apis=(Endpoint('test.count', count_tickets, 'GET', (Ticket, 'read')),))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    return app


def test_dialog_load_submit_retry_and_source_revision(app):
    c = client(app)
    assert c('POST', '/api/ticket', {'name': 'Original'})[0] == 201
    source = c('GET', '/api/ticket/1')
    headers = {b'if-match': source[2][b'etag']}
    status, loaded, _ = c('POST', '/api/_dialogs/ticket/1/copy/load', {}, headers=headers)
    assert status == 200, loaded
    assert loaded['values']['count'] == 1
    assert app.database.count(Ticket) == 1
    data = {'key': 'test-operation-1', 'values': {'count': 2}}
    first = c('POST', '/api/_dialogs/ticket/1/copy/submit', data, headers=headers)
    assert first[0] == 200, first
    assert app.database.count(Ticket) == 3
    retry = c('POST', '/api/_dialogs/ticket/1/copy/submit', data, headers=headers)
    assert retry[:2] == first[:2]
    assert app.database.count(Ticket) == 3
    assert c('POST', '/api/_dialogs/ticket/1/copy/submit', {**data, 'key': 'test-operation-2'}, headers=headers)[0] == 409
    with app.database._connection() as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name IN ('copy','tickets')").fetchall()


def test_report_evaluation_does_not_run_and_results_are_bounded(app, monkeypatch):
    c = client(app)
    c('POST', '/api/ticket', {'name': 'One'})
    original = Tickets.run
    def reject(self): raise AssertionError('A filter edit must not run the report')
    monkeypatch.setattr(Tickets, 'run', reject)
    assert c('POST', '/api/_reports/Tickets/evaluate', {'filters': {}, 'revision': 1})[0] == 200
    monkeypatch.setattr(Tickets, 'run', original)
    status, result, _ = c('POST', '/api/_reports/Tickets', {'filters': {}, 'limit': 1, 'revision': 2})
    assert status == 200, result
    assert result['rows'][0]['name'] == 'One' and result['revision'] == 2
    assert result['filters']['term'] == ''
    assert c('POST', '/api/_reports/Tickets', {'limit': 101})[0] == 422


def test_report_roles_and_permissions_alias_grant_the_same_access(tmp_path):
    class ByRoles(Tickets):
        roles = ('admin',)
    class ViewerOnly(Tickets):
        roles = ('viewer',)
    class ByAlias(Tickets):
        permissions = ['admin']
    assert Tickets.roles == ('admin',) and ByRoles.permissions == ('admin',) and ByAlias.roles == ('admin',)
    with pytest.raises(ValueError, match='alias'):
        type('Conflict', (Tickets,), {'roles': ('admin',), 'permissions': ('viewer',)})
    db = Database(tmp_path / 'roles.sqlite3', Registry(Ticket))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(db, auth=auth, reports=(ByRoles, ViewerOnly, ByAlias))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    c = client(app)
    for name in ('ByRoles', 'ByAlias'):
        assert c('POST', '/api/_reports/' + name, {'filters': {}, 'revision': 1})[0] == 200
    assert c('POST', '/api/_reports/ViewerOnly', {'filters': {}, 'revision': 1})[0] == 403


@pytest.mark.parametrize('playground', [False, True])
def test_playground_mode_is_explicit_not_derived_from_the_name(tmp_path, playground):
    db = Database(tmp_path / 'mode.sqlite3', Registry(Ticket))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    options = {'playground': True} if playground else {}
    app = Application(db, auth=auth, name='Evlet Playground', **options)
    app.initialize()
    assert http(app, 'GET', '/api/auth/session')[1]['application'] == {'name': 'Evlet Playground', 'playground': playground, 'docs': '/docs'}


def test_api_is_registered_typed_and_readonly(app):
    c = client(app)
    assert c('GET', '/api/_services/test.count')[1] == {'count': 0}
    assert c('POST', '/api/_services/test.count')[0] == 405
    assert c('GET', '/api/_services/os.system')[0] == 404
    with app.business.context(app.business.auth.create_user(username='other', password=PASSWORD, role='admin'), readonly=True) as context:
        with pytest.raises(Exception, match='read only'): context.create(Ticket, name='Forbidden')


def test_calendar_dst_and_calendar_month():
    def stamp(value): return int(datetime.fromisoformat(value).timestamp())
    assert Calendar('0 2 1 * *', 'UTC').next(stamp('2026-01-31T23:00:00+00:00')) == stamp('2026-02-01T02:00:00+00:00')
    # New York's spring gap skips 02:30; fall repetition fires at fold=0 only.
    assert Calendar('30 2 * * *', 'America/New_York').next(stamp('2026-03-08T00:00:00-05:00')) == stamp('2026-03-09T02:30:00-04:00')
    calendar = Calendar('30 1 * * *', 'America/New_York')
    first = calendar.next(stamp('2026-11-01T00:00:00-04:00'))
    assert first == stamp('2026-11-01T01:30:00-04:00')
    assert calendar.next(first) == stamp('2026-11-02T01:30:00-05:00')
    for expression in ('* * * *', '70 * * * *', '0 0 * JAN *', '*/0 * * * *'):
        with pytest.raises(ValueError): Calendar(expression)


def test_calendar_reconciliation_preserves_disabled_state_history_and_instance_scope(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from evlet import JobQueue, Task
    from evlet.instances import register_schedules
    from evlet.services import CalendarSchedule
    import time

    now = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(time, 'time', lambda: now)
    instances = []
    for name in ('first', 'second'):
        database = Database(tmp_path / (name + '.sqlite3'), Registry())
        database.initialize()
        queue = JobQueue(database, [Task('calendar_test', lambda tx, context, payload: None)], tenant=name)
        queue.initialize()
        instances.append(SimpleNamespace(database=database, business=SimpleNamespace(jobs=queue, timezone='UTC')))
    app, other = instances
    schedule = CalendarSchedule('calendar_test', 'calendar_test', '0 2 * * *')
    register_schedules(app, [schedule])
    register_schedules(app, [schedule])
    queue = app.business.jobs
    assert queue.recent() == other.business.jobs.recent() == []
    due = Calendar(schedule.expression).next(now)
    assert queue.run_one(now=due)
    assert queue.recent()[0]['state'] == 'succeeded'
    queue.set_schedule_active('calendar_test', False)
    with app.database._connection() as connection:
        before = dict(connection.execute('SELECT * FROM evlet_schedules').fetchone())
    register_schedules(app, [schedule])
    with app.database._connection() as connection:
        assert dict(connection.execute('SELECT * FROM evlet_schedules').fetchone()) == before
    changed = CalendarSchedule('calendar_test', 'calendar_test', '0 4 * * *')
    register_schedules(app, [changed])
    with app.database._connection() as connection:
        row = connection.execute('SELECT * FROM evlet_schedules').fetchone()
        assert row['enabled'] == 0 and row['next_run'] == Calendar(changed.expression).next(now)
    register_schedules(app, [])
    queue.tick(now=now + 86400 * 3)
    assert len(queue.recent()) == 1 and queue.recent()[0]['state'] == 'succeeded'
    with other.database._connection() as connection:
        assert connection.execute('SELECT count(*) FROM evlet_schedules').fetchone()[0] == 0


def test_calendar_schedule_rows_are_written_together(tmp_path):
    from evlet import JobQueue, Task
    database = Database(tmp_path / 'db.sqlite3', Registry())
    database.initialize()
    queue = JobQueue(database, [Task('calendar_test', lambda tx, context, payload: None)])
    queue.initialize()
    # A failing calendar row (NOT NULL expression) must not leave an interval schedule behind.
    with pytest.raises(Exception):
        queue.schedule('nightly', 'calendar_test', {}, every=60, calendar=(None, 'UTC'))
    with database._connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM evlet_schedules').fetchone()[0] == 0
    queue.schedule('nightly', 'calendar_test', {}, every=60, calendar=('0 2 * * *', 'UTC'))
    with database._connection() as connection:
        assert connection.execute('SELECT expression FROM evlet_calendar_schedules WHERE name=?', ('nightly',)).fetchone()[0] == '0 2 * * *'


def test_restored_calendar_schedule_is_enabled_and_task_changes_apply(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import sqlite3, time
    from evlet import JobQueue, Task
    from evlet.instances import register_schedules
    from evlet.services import CalendarSchedule

    now = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(time, 'time', lambda: now)
    path = tmp_path / 'cron.sqlite3'
    database = Database(path, Registry())
    database.initialize()
    with sqlite3.connect(path) as connection:
        # A database created before the retired column existed.
        connection.execute('CREATE TABLE evlet_calendar_schedules (name TEXT PRIMARY KEY, expression TEXT NOT NULL, timezone TEXT NOT NULL)')
    noop = lambda tx, context, payload: None
    queue = JobQueue(database, [Task('nightly', noop), Task('other', noop), Task('paused', noop)])
    queue.initialize()
    queue.initialize()
    app = SimpleNamespace(database=database, business=SimpleNamespace(jobs=queue, timezone='UTC'))
    nightly, paused = CalendarSchedule('nightly', 'nightly', '0 2 * * *'), CalendarSchedule('paused', 'paused', '0 3 * * *')
    register_schedules(app, [nightly, paused])
    queue.set_schedule_active('paused', False)
    register_schedules(app, [])
    later = now + 86400 * 2
    monkeypatch.setattr(time, 'time', lambda: later)
    register_schedules(app, [CalendarSchedule('nightly', 'other', '0 2 * * *'), paused])
    def rows():
        with database._connection() as connection:
            return {row['name']: dict(row) for row in connection.execute('SELECT name,task,enabled,next_run FROM evlet_schedules')}
    assert rows()['nightly'] == {'name': 'nightly', 'task': 'other', 'enabled': 1, 'next_run': Calendar('0 2 * * *').next(later)}
    assert rows()['paused']['enabled'] == 0
    assert queue.tick(now=later + 86400) == 1 and queue.recent()[0]['task'] == 'other'
    # An operator cannot keep an undeclared schedule running: each release disables it again.
    register_schedules(app, [paused])
    queue.set_schedule_active('nightly', True)
    register_schedules(app, [paused])
    assert rows()['nightly']['enabled'] == 0


class Sale(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete')),
                   'viewer': Rule(('read',), read_fields=('customer', 'amount'))}
    customer = Text(required=True)
    amount = Integer(default=0)
    cost = Integer(default=0)


class SalesByCustomer(Report):
    title = 'Sales by customer'
    roles = ('admin', 'viewer')
    group = 'Sales'
    model = Sale
    sortable = ('customer', 'amount')
    chart = Chart('bar', x='customer', y=('amount', 'cost'))
    customer = Text(default='')
    columns = (Column('id', type='integer'), Column('customer', link='sale'),
               Column('amount', type='integer', total=True, unit='AED'),
               Column('cost', type='integer', total=True, unit='AED', source='sale.cost'))

    def run(self):
        filters = {'customer': self.customer} if self.customer else {}
        page = self.context.list(Sale, filters=filters, order_by=self.context.sort or ('id',), limit=self.context.limit, offset=self.context.offset)
        everything = self.context.aggregate(Sale, measures={'amount': Sum('amount')}, filters=filters)
        return {'rows': [{key: row.get(key) for key in ('id', 'customer', 'amount', 'cost')} for row in page['rows']],
                'total': page['total'], 'totals': {'amount': everything[0]['amount'] or 0, 'cost': 0}}


class Unexported(SalesByCustomer):
    export = False


@pytest.fixture
def sales(tmp_path):
    db = Database(tmp_path / 'sales.sqlite3', Registry(Sale))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    app = Application(db, auth=auth, reports=(SalesByCustomer, Unexported))
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    admin = client(app)
    assert admin('POST', '/api/auth/users', {'username': 'viewer', 'password': PASSWORD, 'role': 'viewer'})[0] == 201
    for customer, amount, cost in (('Beta', 30, 3), ('Alpha', 10, 1), ('=SUM(A1)', 5, 0)):
        assert admin('POST', '/api/sale', {'customer': customer, 'amount': amount, 'cost': cost})[0] == 201
    return app


def test_report_catalog_describes_the_view(sales):
    entry = next(item for item in client(sales)('GET', '/api/_reports')[1]['reports'] if item['name'] == 'SalesByCustomer')
    assert entry['group'] == 'Sales' and entry['model'] == 'sale' and entry['export'] is True
    assert entry['sortable'] == ['customer', 'amount']
    assert entry['chart'] == {'type': 'bar', 'x': 'customer', 'y': ['amount', 'cost'], 'label': None}
    assert [column['name'] for column in entry['columns']] == ['id', 'customer', 'amount', 'cost']
    assert entry['columns'][1]['link'] == 'sale' and 'source' not in entry['columns'][3]


def test_report_sort_and_whole_result_totals(sales):
    c = client(sales)
    status, result, _ = c('POST', '/api/_reports/SalesByCustomer', {'filters': {}, 'limit': 2, 'sort': '-amount'})
    assert status == 200, result
    assert [row['amount'] for row in result['rows']] == [30, 10] and result['sort'] == '-amount'
    assert result['totals'] == {'scope': 'all', 'values': {'amount': '45', 'cost': '0'}}
    assert c('POST', '/api/_reports/SalesByCustomer', {'sort': 'cost'})[0] == 422
    assert c('POST', '/api/_reports/SalesByCustomer', {'sort': ['amount']})[0] == 422


def test_report_without_given_totals_keeps_page_totals(app):
    class Totalled(Tickets):
        columns = (Column('id', type='integer'), Column('name'), Column('copies', type='integer', total=True, unit='copies'))
    app.business.forms.reports['Totalled'] = Totalled
    c = client(app)
    c('POST', '/api/ticket', {'name': 'One', 'copies': 2})
    c('POST', '/api/ticket', {'name': 'Two', 'copies': 3})
    result = c('POST', '/api/_reports/Totalled', {'limit': 1})[1]
    assert result['totals'] == {'scope': 'page', 'values': {'copies': '2'}} and result['sort'] is None


def test_report_column_source_hides_unreadable_fields(sales):
    viewer = client(sales, 'viewer')
    entry = next(item for item in viewer('GET', '/api/_reports')[1]['reports'] if item['name'] == 'SalesByCustomer')
    assert [column['name'] for column in entry['columns']] == ['id', 'customer', 'amount']
    assert entry['chart']['y'] == ['amount']
    result = viewer('POST', '/api/_reports/SalesByCustomer', {'filters': {}})[1]
    assert all('cost' not in row for row in result['rows']) and set(result['totals']['values']) == {'amount'}
    status, content, headers = viewer('POST', '/api/_reports/SalesByCustomer/export.csv', {'filters': {}})
    assert status == 200 and b'Cost' not in content and b'Amount' in content


def test_report_export_is_csv_with_the_same_checks(sales):
    c = client(sales)
    status, content, headers = c('POST', '/api/_reports/SalesByCustomer/export.csv', {'filters': {}, 'sort': 'customer'})
    assert status == 200
    assert headers[b'content-type'] == b'text/csv; charset=utf-8'
    assert headers[b'content-disposition'] == b"attachment; filename*=UTF-8''Sales%20by%20customer.csv"
    assert headers[b'x-content-type-options'] == b'nosniff'
    text = content.decode('utf-8')
    assert text.startswith('﻿Id,Customer,Amount,Cost\r\n')
    assert text.splitlines()[1:] == ["3,'=SUM(A1),5,0", '2,Alpha,10,1', '1,Beta,30,3']
    assert c('POST', '/api/_reports/Unexported/export.csv', {'filters': {}})[0] == 403
    assert c('POST', '/api/_reports/SalesByCustomer/export.csv', {'limit': 5})[0] == 422
    assert c('GET', '/api/_reports/SalesByCustomer/export.csv')[0] == 403


def test_report_export_stops_at_the_row_bound(sales, monkeypatch):
    import evlet.forms
    monkeypatch.setattr(evlet.forms, 'EXPORT_ROWS', 2)
    status, content, _ = client(sales)('POST', '/api/_reports/SalesByCustomer/export.csv', {})
    assert status == 200, content
    content = content.decode('utf-8')
    lines = content.splitlines()
    assert len(lines) == 4 and lines[-1] == '1 more rows were left out. Narrow the filters to export them.'


@pytest.mark.parametrize('attributes, message', [
    ({'sortable': ('missing',)}, 'sortable'),
    ({'chart': Chart('line', x='id', y=('customer',))}, 'chart'),
    ({'columns': (Column('id', type='integer', source='sale.missing'),)}, 'source'),
    ({'columns': (Column('id', type='integer', link='Missing'),)}, 'Missing'),
    ({'export': 'yes'}, 'export'),
    ({'group': 3}, 'group'),
])
def test_report_view_declarations_are_checked(tmp_path, attributes, message):
    report = type('Broken', (SalesByCustomer,), {'chart': None, 'sortable': (), **attributes})
    db = Database(tmp_path / 'broken.sqlite3', Registry(Sale))
    auth = LocalAuth(db, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    with pytest.raises(Exception, match=message):
        Application(db, auth=auth, reports=(report,))


def test_csv_cells_guard_text_but_keep_negative_numbers():
    from evlet.forms import csv_cell
    assert csv_cell('-5.00', 'currency') == '-5.00' and csv_cell(-3, 'integer') == '-3'
    assert csv_cell('-5 rows', 'text') == "'-5 rows" and csv_cell('@SUM', 'text') == "'@SUM" and csv_cell(None) == ''
    assert csv_cell(True, 'boolean') == 'Yes'


def test_chart_and_column_source_shapes():
    with pytest.raises(ValueError): Chart('pie', x='a', y=('b',))
    with pytest.raises(ValueError): Chart('bar', x='a', y=())
    assert Chart('line', x='a', y='b').y == ('b',)
    with pytest.raises(ValueError): Column('a', source='nodot')


def test_report_pages_are_served_only_for_known_reports(sales, app):
    from evlet.ui.routes import _serves
    assert _serves(sales, 'evlet.reports', '/reports', '') and _serves(sales, 'evlet.reports', '/reports', '/SalesByCustomer')
    assert not _serves(sales, 'evlet.reports', '/reports', '/Missing')
    app.business.forms.reports.clear()
    assert not _serves(app, 'evlet.reports', '/reports', '')
