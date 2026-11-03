"""Team and hierarchy row scopes: any_of and Hierarchy.team on every surface a row scope covers."""
import json
from urllib.parse import quote

import pytest

from evlet import (Attachments, Boolean, Child, Database, Hierarchy, Integer, Journal, Link, Model, Policy, Registry, Rule,
                   Table, Text, any_of, action)
from evlet.auth import AccessError, User
from evlet.measures import Count, Sum
from evlet.permissions import scope_condition
from test_business import PASSWORD, client
from test_meaning import serve
from test_rules_types import ALL

TEAM = Hierarchy('employee', parent='reports_to')


class Employee(Model):
    permissions = {**ALL, 'viewer': Rule(('read',), scope=TEAM.team())}
    name = Text(required=True)
    reports_to = Link('employee', index=True)


class LeaveDay(Child):
    half = Boolean(default=False)


class Leave(Model):
    """Viewers read their own and their team's leave, and every leave marked public; they may update it."""
    class Meta:
        journal = Journal()
        attachments = Attachments(max_files=3, max_size=1000)
    permissions = {**ALL, 'viewer': Rule(('read', 'update'), scope=any_of(TEAM.team('employee'), {'public': True}))}
    employee = Link(Employee, index=True)
    days = Integer(default=1)
    public = Boolean(default=False)
    note = Text(default='')
    days_taken = Table(LeaveDay)

    @action('Approve', roles=('admin', 'viewer'))
    def approve(self):
        self.note = 'approved'


class Payslip(Model):
    """Only the records below the user: a manager's reports, never their own."""
    permissions = {**ALL, 'viewer': Rule(('read',), scope=TEAM.team('employee', include_self=False))}
    employee = Link(Employee, index=True)
    amount = Integer(default=0)


MODELS = (Employee, Leave, Payslip)


def people(app, reports=150):
    """A chief, a manager with `reports` indirect reports in a chain of teams, a member and an outsider.

    Returns {name: employee id} for chief, manager, member, outsider and deep (the last report)."""
    db = app.database
    with db.transaction() as tx:
        chief = tx.create(Employee, name='Chief').id
        manager = tx.create(Employee, name='Manager', reports_to=chief).id
        outsider = tx.create(Employee, name='Outsider', reports_to=chief).id
        # Five team leads under the manager, each with a chain of reports below it.
        below, ids = [manager], []
        for index in range(reports):
            parent = manager if index < 5 else ids[index - 5]
            ids.append(tx.create(Employee, name=f'Report {index}', reports_to=parent).id)
        for employee in (chief, manager, outsider, *ids):
            tx.create(Leave, employee=employee, days=2)
            tx.create(Payslip, employee=employee, amount=100)
        tx.create(Leave, employee=chief, days=7, public=True)
    return {'chief': chief, 'manager': manager, 'member': ids[0], 'outsider': outsider, 'deep': ids[-1], 'reports': ids}


def sign_in(app, name, employee):
    admin = client(app)
    status, user, _ = admin('POST', '/api/auth/users', {'username': name, 'password': PASSWORD, 'role': 'viewer'})
    assert status == 201, user
    app.business.auth.link_user(user['id'], Employee, employee)
    return client(app, name)


def listed(c, model, filters=None):
    url = f'/api/{model}?limit=100' + ('' if filters is None else '&filters=' + quote(json.dumps(filters)))
    status, page, _ = c('GET', url)
    assert status == 200, page
    return page


def check_team_scopes(app):
    found = people(app)
    reports = found['reports']
    manager, member, outsider = (sign_in(app, name, found[name]) for name in ('manager', 'member', 'outsider'))
    admin = client(app)
    # Lists: the manager sees themselves and all 150 indirect reports; HR (admin) sees everyone.
    page = listed(manager, 'employee')
    assert page['total'] == 151
    assert listed(admin, 'employee')['total'] == 153
    # A member of a chain sees themselves and the reports below them; the outsider only themselves.
    chain = {found['member'], *reports[5::5]}
    assert listed(member, 'employee')['total'] == len(chain)
    assert listed(outsider, 'employee')['total'] == 1
    # Own or team leave, OR the public leave; the outsider sees their own and the public one.
    assert listed(manager, 'leave')['total'] == 151 + 1
    assert listed(outsider, 'leave')['total'] == 2
    assert listed(member, 'leave', {'public': True})['total'] == 1
    # include_self=False: the manager's reports, not their own payslip; a leaf sees no payslip.
    assert listed(manager, 'payslip')['total'] == 150
    assert listed(sign_in(app, 'deep', found['deep']), 'payslip')['total'] == 0
    # Aggregates and measures count exactly the team.
    status, body, _ = manager('GET', '/api/leave/aggregate?measures=' + quote('n:count,d:sum:days'))
    assert status == 200, body
    assert body['items'] == [{'n': 152, 'd': 151 * 2 + 7}]
    assert outsider('GET', '/api/payslip/aggregate?measures=' + quote('n:count'))[1]['items'] == [{'n': 0}]
    # Detail, writes, actions, attachments and journal reads follow the same scope.
    with app.database.transaction() as tx:
        rows = {row['employee']: row['id'] for row in tx.connection.execute('SELECT id, employee FROM leave WHERE public = ?', (False,))}
    deep, theirs = f"/api/leave/{rows[found['deep']]}", f"/api/leave/{rows[found['outsider']]}"
    assert manager('GET', deep)[0] == 200 and manager('GET', theirs)[0] == 404
    assert outsider('GET', deep)[0] == 404
    etag = manager('GET', deep)[2][b'etag']
    assert manager('PATCH', deep, {'note': 'seen'}, headers={b'if-match': etag})[0] == 200
    assert outsider('PATCH', deep, {'note': 'no'}, headers={b'if-match': etag})[0] == 404
    etag = manager('GET', deep)[2][b'etag']
    assert manager('POST', deep + '/actions/approve', headers={b'if-match': etag})[0] == 200
    assert outsider('POST', deep + '/actions/approve', headers={b'if-match': etag})[0] == 404
    file = admin('POST', deep + '/attachments', {'name': 'a.txt', 'content': 'YQ=='})[1]
    assert manager('GET', deep + f"/attachments/{file['id']}")[0] == 200
    assert outsider('GET', deep + f"/attachments/{file['id']}")[0] == 404
    assert manager('GET', deep + '/journal')[0] == 200 and outsider('GET', deep + '/journal')[0] == 404
    # The Link filter guard: filtering leave by an employee in the team is allowed, outside it refused.
    assert listed(manager, 'leave', {'employee': found['deep']})['total'] == 1
    status, _, _ = manager('GET', '/api/leave?filters=' + quote(json.dumps({'employee': found['outsider']})))
    assert status == 403
    # Serialized Links to employees outside the scope are empty.
    public = listed(outsider, 'leave', {'public': True})['items'][0]
    assert public['employee'] is None
    # Eva and MCP tools run the same routes in process (Tool Engine), so they see the same team.
    def tool(c, name, **values):
        status, body, _ = c('POST', '/api/_tools/execute', {'tool': name, 'arguments': values})
        return status, body.get('result')
    assert tool(manager, 'list_records', model='employee')[1]['total'] == 151
    assert tool(manager, 'aggregate_records', model='payslip', measures='n:count')[1]['items'] == [{'n': 150}]
    assert tool(manager, 'get_record', model='employee', id=found['outsider'])[0] == 404
    assert tool(outsider, 'record_history', model='leave', id=rows[found['deep']])[0] == 404
    assert tool(outsider, 'list_records', model='leave')[1]['total'] == 2
    # Moving the outsider into the manager's team changes what the manager sees at once.
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE employee SET reports_to = ? WHERE id = ?', (found['deep'], found['outsider']))
    assert listed(manager, 'employee')['total'] == 152
    # Filter lists and child table groups (EXISTS) apply inside the scope: only the team's matching leave.
    admin('POST', '/api/leave', {'employee': found['deep'], 'children': {'days_taken': [{'half': True}]}})
    admin('POST', '/api/leave', {'employee': found['chief'], 'children': {'days_taken': [{'half': True}]}})
    half = [{'child': 'days_taken', 'match': 'any', 'where': [['half', 'eq', True]]}]
    assert listed(admin, 'leave', half)['total'] == 2
    assert listed(manager, 'leave', half)['total'] == 1
    assert listed(outsider, 'leave', half)['total'] == 0
    assert listed(manager, 'leave', [['days', 'gte', 2], ['public', 'eq', False]])['total'] == 152
    status, body, _ = manager('GET', '/api/leave/aggregate?measures=' + quote('n:count') + '&filters=' + quote(json.dumps(half)))
    assert status == 200 and body['items'] == [{'n': 1}], body
    return found


def test_team_scopes_cover_lists_aggregates_records_and_files(tmp_path):
    check_team_scopes(serve(Database(tmp_path / 'team.sqlite3', Registry(*MODELS))))


def test_cycles_stop_and_a_user_without_a_linked_record_sees_nothing(tmp_path):
    app = serve(Database(tmp_path / 'cycle.sqlite3', Registry(*MODELS)))
    with app.database.transaction() as tx:
        first = tx.create(Employee, name='First').id
        second = tx.create(Employee, name='Second', reports_to=first).id
        tx.connection.execute('UPDATE employee SET reports_to = ? WHERE id = ?', (second, first))
    looped = sign_in(app, 'looped', first)
    assert listed(looped, 'employee')['total'] == 2
    admin = client(app)
    assert admin('POST', '/api/auth/users', {'username': 'unlinked', 'password': PASSWORD, 'role': 'viewer'})[0] == 201
    assert listed(client(app, 'unlinked'), 'employee')['total'] == 0


def test_context_reads_and_aggregates_use_the_team(tmp_path):
    app = serve(Database(tmp_path / 'context.sqlite3', Registry(*MODELS)))
    found = people(app, reports=12)
    user = User(99, 'manager', 'viewer', (('employee', found['manager']),))
    with app.business.context(user, readonly=True) as context:
        assert context.aggregate(Payslip, measures={'n': Count(), 's': Sum('amount')}) == [{'n': 12, 's': 1200}]
        assert context.list(Employee, limit=100)['total'] == 13
        with pytest.raises(AccessError):
            context.measures(Employee, found['outsider'])


def test_declarations_are_checked_when_the_policy_is_built():
    class Badge(Model):
        employee = Link(Employee)

    registry = Registry(*MODELS, Badge)
    with pytest.raises(ValueError, match='team field'):
        Policy(registry, rules={('viewer', Badge): Rule(('read',), scope=TEAM.team('id'))})
    with pytest.raises(ValueError, match='must be a Link to the same model'):
        Policy(registry, rules={('viewer', Badge): Rule(('read',), scope=Hierarchy('employee', parent='name').team())})
    with pytest.raises(ValueError, match='any_of'):
        any_of()
    with pytest.raises(ValueError):
        Hierarchy('employee', parent='')
    # A team inside any_of is checked too, and a Link path to the hierarchy model is accepted.
    with pytest.raises(ValueError, match='team field'):
        Policy(registry, rules={('viewer', Badge): Rule(('read',), scope=any_of({'id': 1}, TEAM.team('nothing')))})
    Policy(registry, rules={('viewer', Badge): Rule(('read',), scope=TEAM.team('employee__reports_to'))})


def test_scope_conditions_compile_to_parameterized_sql():
    registry = Registry(*MODELS)
    team = TEAM.team('employee')(User(1, 'u', 'viewer', (('employee', 4), ('employee', 2), ('customer', 9))))
    sql, values = scope_condition(registry, Leave, team)
    assert 'WITH RECURSIVE' in sql and values == [2, 4]
    assert scope_condition(registry, Leave, {}) == (None, [])
    assert scope_condition(registry, Leave, any_of({}, TEAM.team('employee'))(User(1, 'u', 'viewer'))) == (None, [])
    assert scope_condition(registry, Leave, any_of(TEAM.team('employee'))(User(1, 'u', 'viewer'))) == ('(1=0)', [])
    sql, values = scope_condition(registry, Leave, any_of({'days__gt': 3, 'public': True}, {'note': 'x'})(User(1, 'u', 'viewer')))
    assert sql == '(("record"."days" > ? AND "record"."public" = ?) OR ("record"."note" = ?))'
    assert values[0] == 3 and values[1] in (1, True) and values[2] == 'x'
    with pytest.raises(ValueError, match='filter object'):
        scope_condition(registry, Leave, ['days', 'eq', 1])


def test_sqlite_plan_walks_the_parent_index(tmp_path):
    app = serve(Database(tmp_path / 'plan.sqlite3', Registry(*MODELS)))
    people(app, reports=20)
    user = User(99, 'manager', 'viewer', (('employee', 2),))
    where, values = app.business.policy.scope_sql(user, Payslip)
    with app.database.transaction() as tx:
        plan = [row[-1] for row in tx.connection.execute(f'EXPLAIN QUERY PLAN SELECT count(*) FROM payslip AS record{where}', values)]
    # The team is computed once (not per row), each level is an index lookup on reports_to, and the
    # team's payslips are found through the employee index.
    assert 'MATERIALIZE evlet_team' in plan, plan
    assert any(step.startswith('SEARCH below USING') and 'reports_to' in step for step in plan), plan
    assert any(step.startswith('SEARCH record USING') and 'employee' in step for step in plan), plan
