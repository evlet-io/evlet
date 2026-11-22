"""evlet link-user, evlet controls, evlet users and evlet secrets: accounts and their keys from the server shell."""

import argparse
import os

from ...identity import from_argument
from . import INSTANCE_HELP


def add_link_user(commands):
    link = commands.add_parser('link-user', help='Link an account to a record, such as a portal user to their customer')
    link.add_argument('instance', help=INSTANCE_HELP)
    link.add_argument('username')
    link.add_argument('model')
    link.add_argument('record_id', type=from_argument)
    link.add_argument('--remove', action='store_true', help='Remove the link instead')


def run_link_user(args, context):
    from ...instances import load_runtime
    from ...auth import AccessError
    app = load_runtime(context.root, args.instance)
    try:
        auth = app.business.auth
        auth.initialize()
        from ... import qb
        with app.database._connection() as connection:
            row = connection.run(qb.select('evlet_users', 'id').where(username=args.username.lower())).one()
        if row is None:
            raise ValueError(f'No account named {args.username}')
        (auth.unlink_user if args.remove else auth.link_user)(row['id'], args.model, args.record_id, actor='cli')
        print(f'{"Unlinked" if args.remove else "Linked"} {args.username} {"from" if args.remove else "to"} {args.model} {args.record_id}')
    except AccessError as exc:
        raise ValueError(exc.message) from None
    finally:
        app.database.close()


def add_controls(commands):
    controls = commands.add_parser('controls', help='Restore access to Controls from the server shell')
    controls_commands = controls.add_subparsers(dest='controls_command', required=True)
    controls_grant = controls_commands.add_parser('grant', help='Make a user an active admin; audited as cli')
    controls_grant.add_argument('instance', help=INSTANCE_HELP)
    controls_grant.add_argument('username')
    controls_grant.add_argument('--clear-allowlist', action='store_true',
                                help='Also clear the allowed networks set in Controls')
    controls_grant.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')


def run_controls(args, context):
    from ..project import child
    from ...controls import grant, shell_address, within
    from ...instances import read_instance, read_routes
    path = child(context.root, 'instances', args.instance)
    read_instance(path)
    networks = grant(path / 'data/database.sqlite3', args.username, clear_allowlist=args.clear_allowlist)
    print(f'{args.username} is now an active admin.')
    if networks and args.clear_allowlist:
        print(f'Allowed networks set in Controls were cleared: {", ".join(map(str, networks))}.')
    elif networks:
        listed = ", ".join(map(str, networks))
        address = shell_address(os.environ)
        if address is None:
            print(f'Controls still opens only from {listed} (allowed networks set in Controls). '
                  f'If you open it from another address you are still locked out: run this again with --clear-allowlist.')
        elif within(address, networks):
            print(f'Controls still opens only from {listed} (allowed networks set in Controls). '
                  f'Your SSH address {address} is in them.')
        else:
            print(f'Controls still opens only from {listed} (allowed networks set in Controls), '
                  f'and your SSH address {address} is not in them, so you are still locked out. '
                  f'Run this again with --clear-allowlist to clear them.')
    try:
        limits = getattr((read_routes(path) or {}).get('controls'), 'allow_ips', ())
    except Exception:
        limits = ('the networks in its ControlsAccess, if any',)
    if limits:
        print(f'routes.py still limits Controls to {", ".join(limits)}. Edit instances/{args.instance}/routes.py to open it from elsewhere.')


def add_users(commands):
    users = commands.add_parser('users', help='Account recovery from the server shell')
    users_commands = users.add_subparsers(dest='users_command', required=True)
    reset_two_step = users_commands.add_parser('reset-two-step', help="Clear a user's two-step sign-in; they set it up again at next sign-in. Audited as cli")
    reset_two_step.add_argument('instance', help=INSTANCE_HELP)
    reset_two_step.add_argument('username')
    reset_two_step.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    set_password = users_commands.add_parser('set-password', help="Set a user's password; asks twice without echo. Ends their sessions. Audited as cli")
    set_password.add_argument('instance', help=INSTANCE_HELP)
    set_password.add_argument('username')
    set_password.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    set_email = users_commands.add_parser('set-email', help="Set a user's email address for notifications. Audited as cli")
    set_email.add_argument('instance', help=INSTANCE_HELP)
    set_email.add_argument('username')
    set_email.add_argument('email')
    set_email.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')


def run_users(args, context):
    from ..project import child
    from ...controls import reset_two_step_from_shell
    from ...instances import read_instance
    path = child(context.root, 'instances', args.instance)
    read_instance(path)
    from ..site import as_state_owner
    if as_state_owner(context.root, args.instance, context.values):
        return
    if args.users_command == 'set-password':
        import getpass
        from ...controls import set_password_from_shell
        config = read_instance(path)
        first = getpass.getpass(f'New password for {args.username}: ')
        if first != getpass.getpass('Repeat the password: '):
            raise ValueError('The two passwords differ. Nothing was changed.')
        set_password_from_shell(path / 'data/database.sqlite3', args.username, first, config.security.password_min)
        print(f'Password set for {args.username}. Their other sessions, AI assistants and API keys ended.')
    elif args.users_command == 'set-email':
        from ...controls import set_email_from_shell
        address = set_email_from_shell(path / 'data/database.sqlite3', args.username, args.email)
        print(f'Email for {args.username} is now {address}.')
    else:
        reset_two_step_from_shell(path / 'data/database.sqlite3', args.username)
        print(f'Two-step sign-in is reset for {args.username}. They set it up again at their next sign-in.')


def add_secrets(commands):
    keys = commands.add_parser('secrets', help='Manage the key that seals connector secrets, two-step secrets and backup codes')
    keys_commands = keys.add_subparsers(dest='secrets_command', required=True)
    keys_rotate = keys_commands.add_parser('rotate', help='Re-encrypt saved secrets under a new secret.key')
    keys_rotate.add_argument('instance', help=INSTANCE_HELP)
    keys_rotate.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')


def run_secrets(args, context):
    from ..project import child, instance_guard
    from ...instances import read_instance
    from ...secrets import SecretKey, rotate
    path = child(context.root, 'instances', args.instance)
    read_instance(path)
    from ..site import as_state_owner
    if as_state_owner(context.root, args.instance, context.values):
        return
    with instance_guard(path, serving=True):
        # Recovery replays retained files sealed with the current keys, so they must not be pruned first.
        if (path / '.install-journal.json').exists():
            raise ValueError(f'Interrupted installation needs recovery before rotating the secret key: run evlet migrate {args.instance}')
        count, failed = rotate(path / 'data/database.sqlite3', SecretKey(path / 'secret.key'))
    print(f'New secret key written to instances/{args.instance}/secret.key. Re-encrypted {count} saved secret' + ('' if count == 1 else 's') + '.')
    if failed:
        print(f"Could not read {', '.join(failed)}. They were left as they are and the old key was kept. Enter connector secrets again in Controls, "
              'reset two-step sign-in with evlet users reset-two-step, then rotate again.')
    print('Running instances use the new key straight away.')


COMMANDS = {
    'link-user': (add_link_user, run_link_user),
    'controls': (add_controls, run_controls),
    'users': (add_users, run_users),
    'secrets': (add_secrets, run_secrets),
}
