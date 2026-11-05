"""The team demo: a manager, a team member and an outsider, each a viewer linked to their employee.

From the checkout, after the Playground database exists:

    .venv/bin/python -m examples.playground.team

Sign in as manager, member or outsider with the password you type. The manager sees themselves and
everyone below them in Employees, the member sees themselves and their report, the outsider only
themselves. Admins and editors (HR) see everyone.

Each employee's Record info shows when it was created and last changed. The demo writes as a system
user, so Created names no one; change a record as HR and Last modified names you.
"""

import getpass

from evlet import User

from .models import Employee

TEAM = (
    # (name, reports to, account)
    ('Mariam Haddad', None, 'manager'),
    ('Omar Khalil', 'Mariam Haddad', 'member'),
    ('Sara Nasser', 'Omar Khalil', None),
    ('Ravi Menon', 'Mariam Haddad', None),
    ('Priya Shah', None, 'outsider'),
)


def seed_team(app, password):
    """Create the demo employees and the three viewer accounts linked to them; returns {account: employee id}."""
    ids, accounts = {}, {}
    # Employee keeps its history (track_changes), so records are written through a Context, as the demo.
    with app.business.context(User(0, 'team-demo', 'admin')) as context:
        for name, manager, _ in TEAM:
            ids[name] = context.create(Employee, full_name=name, reports_to=ids.get(manager))['id']
    for name, _, account in TEAM:
        if account is not None:
            user = app.business.auth.create_user(username=account, password=password, role='viewer')
            app.business.auth.link_user(user.id, Employee, ids[name], actor='team-demo')
            accounts[account] = ids[name]
    return accounts


def main():
    from .app import create_app
    app = create_app()
    app.initialize()
    password = getpass.getpass('Password for manager, member and outsider (15 to 128 characters): ')
    seed_team(app, password)
    print('Created manager, member and outsider. Sign in as each to compare Employees.')


if __name__ == '__main__':
    main()
