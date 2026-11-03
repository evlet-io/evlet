"""The Playground team demo: a manager, a team member and an outsider see their part of the hierarchy."""
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
