"""The Playground team demo: a manager, a team member and an outsider see their part of the hierarchy."""
import json

from examples.playground.team import seed_team
from test_business import PASSWORD, client, hashed, secured  # noqa: F401 (fixtures)


def names(c):
    status, page, _ = c('GET', '/api/employee?limit=100')
    assert status == 200, page
    return sorted(item['name'] for item in page['items'])


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
    assert manager('GET', f"/api/employee/{accounts['member']}")[1]['reports_to'] == accounts['manager']


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
    history = editor('GET', url + '/history')
    assert history[0] == 200, history[1]
    assert 'reports_to' in json.dumps(history[1])
    secured.business.auth.unlink_user(secured.business.auth.users()[-1]['id'], Employee, accounts['outsider'], actor='hr-admin')
    with secured.database._connection() as connection:
        assert tuple(connection.execute("SELECT actor, detail FROM evlet_audit ORDER BY id DESC LIMIT 1").fetchone()) == \
            ('hr-admin', f"Unlinked from employee {accounts['outsider']}")
