"""The Playground team demo: a manager, a team member and an outsider see their part of the hierarchy."""
import json

from examples.playground.team import seed_team
from test_business import PASSWORD, client, hashed, secured  # noqa: F401 (fixtures)


def names(c):
    status, page, _ = c('GET', '/api/employee?limit=100')
    assert status == 200, page
    return sorted(item['full_name'] for item in page['items'])


def test_manager_member_and_outsider_see_their_part_of_the_team(secured):
    accounts = seed_team(secured, PASSWORD)
    manager, member, outsider = (client(secured, name) for name in ('manager', 'member', 'outsider'))
    assert names(manager) == ['Mariam Haddad', 'Omar Khalil', 'Ravi Menon', 'Sara Nasser']
    assert names(member) == ['Omar Khalil', 'Sara Nasser']
    assert names(outsider) == ['Priya Shah']
    # HR (editor and admin) sees everyone; the outsider cannot open the manager's record.
    assert len(names(client(secured, 'editor'))) == 5
    assert outsider('GET', f"/api/employee/{accounts['manager']}")[0] == 404
    # The member sees their own manager link as empty: the manager is outside their scope.
    member_record = member('GET', f"/api/employee/{accounts['member']}")[1]
    assert member_record['reports_to'] is None
    assert manager('GET', f"/api/employee/{accounts['member']}")[1]['reports_to'] == str(accounts['manager'])


def test_team_changes_are_on_record(secured):
    from examples.playground.models import Employee
    accounts = seed_team(secured, PASSWORD)
    with secured.database._connection() as connection:
        logged = [tuple(row) for row in connection.execute("SELECT actor, area, target, detail FROM evlet_audit WHERE area='Users' ORDER BY id")]
    assert logged[-3:] == [('team-demo', 'Users', name, f'Linked to employee {accounts[name]}') for name in ('manager', 'member', 'outsider')]
    # Moving the member under the outsider is journaled with the old and new manager.
    editor = client(secured, 'editor')
    url = f"/api/employee/{accounts['member']}"
    status, _, _ = editor('PATCH', url, {'reports_to': accounts['outsider']}, headers={b'if-match': editor('GET', url)[2][b'etag']})
    assert status == 200
    # Record info: the demo wrote the records as the engine, the editor changed this one.
    audit = editor('GET', url)[1]['_audit']
    assert audit['created_by'] is None and audit['modified_by'] == {'id': 2, 'name': 'editor'}
    assert audit['modified_at'] > audit['created_at']
    history = editor('GET', url + '/history')
    assert history[0] == 200, history[1]
    assert 'reports_to' in json.dumps(history[1])
    secured.business.auth.unlink_user(secured.business.auth.users()[-1]['id'], Employee, accounts['outsider'], actor='hr-admin')
    with secured.database._connection() as connection:
        assert tuple(connection.execute("SELECT actor, detail FROM evlet_audit ORDER BY id DESC LIMIT 1").fetchone()) == \
            ('hr-admin', f"Unlinked from employee {accounts['outsider']}")


def test_recognise_writes_the_ledger_in_the_actions_own_transaction(secured):
    """The Playground's Recognise action writes Recognition rows through self.context.session."""
    from examples.playground.models import RECOGNITION_LIMIT, Recognition
    accounts = seed_team(secured, PASSWORD)
    editor = client(secured, 'editor')
    url = f"/api/employee/{accounts['member']}"

    def recognise(points):
        return editor('POST', url + '/actions/recognise', {'points': points, 'reason': 'Helped a customer'},
                      headers={b'if-match': editor('GET', url)[2][b'etag']})

    status, body, _ = recognise(40)
    assert status == 200 and body['_outcome'] == {'points_this_month': 40}, body
    assert recognise(50)[1]['_outcome'] == {'points_this_month': 90}
    status, body, _ = recognise(20)
    assert status == 422 and body['fields'] == {'points': f'Only {RECOGNITION_LIMIT - 90} points are left this month'}
    with secured.database.transaction() as tx:
        rows = tx.list(Recognition, filters={'employee': accounts['member']}, order_by=('id',))
    assert [row.points for row in rows] == [40, 50]
    # A ledger model has no routes, and viewers may not run the action.
    assert editor('GET', '/api/recognition')[0] == 404
    member = client(secured, 'member')
    assert member('POST', url + '/actions/recognise', {'points': 1, 'reason': 'x'},
                  headers={b'if-match': member('GET', url)[2][b'etag']})[0] == 403


def test_the_employee_record_layout_lists_and_charts_the_team_each_viewer_may_read(secured):
    """Employee.Meta.layout: Related blocks and charts read through the list and aggregate routes, so a viewer's
    team scope applies to them as it does to the Employees list."""
    from urllib.parse import quote
    accounts = seed_team(secured, PASSWORD)
    manager, outsider = client(secured, 'manager'), client(secured, 'outsider')
    entry = next(item for item in manager('GET', '/api/schema')[1]['models'] if item['name'] == 'employee')
    tabs = entry['record_layout']['tabs']
    assert [tab['name'] for tab in tabs] == ['profile', 'team']
    assert [block['kind'] for block in tabs[0]['blocks']] == ['section', 'html', 'table']
    assert [block['kind'] for block in tabs[1]['blocks']] == ['chart', 'chart', 'related', 'related']
    # Nothing brings the automatic Related tab or side block back (the record page leaves them out).
    assert 'related' not in entry['tabs'] and 'side_blocks' not in entry
    reports, same = tabs[1]['blocks'][2], tabs[1]['blocks'][3]

    def listed(c, block, record, department=None):
        filters = [[block['link'], 'eq', record]] + [[field, op, department] for field, op, _ in block['filters']]
        return c('GET', f"/api/employee?order_by={','.join(block['order'])}&filters=" + quote(json.dumps(filters)))

    status, page, _ = listed(manager, reports, accounts['manager'])
    assert status == 200 and [item['full_name'] for item in page['items']] == ['Omar Khalil', 'Ravi Menon']
    assert [item['full_name'] for item in listed(manager, same, accounts['manager'], 'operations')[1]['items']] == ['Omar Khalil']
    chart = f"/api/employee/aggregate?measures=people&group_by=department&filters=" + quote(json.dumps([['reports_to', 'eq', accounts['manager']]]))
    assert manager('GET', chart)[1]['items'] == [{'department': 'finance', 'people': 1}, {'department': 'operations', 'people': 1}]
    # The outsider cannot read the manager's record, so neither its team list nor its chart answers.
    assert listed(outsider, reports, accounts['manager'])[0] == 403
    assert outsider('GET', chart)[0] == 403
