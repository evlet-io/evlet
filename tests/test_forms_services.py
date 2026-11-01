from datetime import datetime, timezone
import json

import pytest

from evlet import (Model, Text, Integer, Rule, Dialog, Report, Column, Registry, Database,
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
    assert http(app, 'GET', '/api/auth/session')[1]['application'] == {'name': 'Evlet Playground', 'playground': playground}


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
