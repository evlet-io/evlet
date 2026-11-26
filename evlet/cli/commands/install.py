"""evlet install, evlet migrate and evlet makemigration: put apps into an instance and change its schema."""

import sys

from . import INSTANCE_HELP, add_instance_option, require_instance


def add_install(commands):
    install = commands.add_parser('install', help='Install a trusted app and its available app dependencies')
    install.add_argument('name')
    add_instance_option(install, 'The instance to install into (default: the only one)')
    install.add_argument('--trust', action='store_true', help='Explicitly trust the selected Python apps and dependencies')
    offer = install.add_mutually_exclusive_group()
    offer.add_argument('--yes', action='store_true', help="Serve each new frontend with a suggested host on <label>.<instance domain> without asking")
    offer.add_argument('--no-host', action='store_true', help='Offer no host for frontends with a suggested host')


def add_migrate(commands):
    migrate = commands.add_parser('migrate', help='Back up and migrate a selected instance; recover interrupted installation')
    migrate.add_argument('instance', nargs='?', help=INSTANCE_HELP + ' (default: the only one)')
    migrate.add_argument('--trust', action='store_true', help='Trust changed app source without prompting')
    migrate.add_argument('--recompute-posted', action='store_true',
                         help='Let Recompute operations recalculate released records (in a release=True step) too; each changed one gets an Activity entry')
    migrate.add_argument('--allow-destructive', action='store_true',
                         help='Let DropField delete the columns and values of retired fields; a backup is written first')
    migrate.add_argument('--allow-maintenance', action='store_true',
                         help='Let RunData steps declared with maintenance=True write confirmed, tracked and posted records; each is audited')
    migrate.add_argument('--plan', action='store_true',
                         help='Print the pending migrations and how they differ from the models; write nothing')


def add_makemigration(commands):
    makemigration = commands.add_parser('makemigration', help="Write the migration --plan suggests into a flat app's migrations folder")
    makemigration.add_argument('app')
    add_instance_option(makemigration, 'The instance whose database the migration starts from (default: the only one)')
    makemigration.add_argument('--name', default='changes', help='Migration name after its number (default: changes)')
    makemigration.add_argument('--trust', action='store_true', help='Trust changed app source without prompting')


def run_plan(args, context):
    from ..core import migration_plan
    if args.recompute_posted or args.allow_destructive or args.allow_maintenance:
        raise ValueError('--plan writes nothing; run migrate without --plan to use --recompute-posted, '
                         '--allow-destructive or --allow-maintenance')
    migration_plan(context.root, args.instance, trust=args.trust, confirm=context.confirm, out=print)


def run_makemigration(args, context):
    from ..core import make_migration
    require_instance(context.root, args)
    make_migration(context.root, args.app, args.instance, name=args.name, trust=args.trust, confirm=context.confirm, out=print)


def run_install(args, context):
    """evlet install and evlet migrate, which share the installer and its report."""
    from .. import core
    require_instance(context.root, args)
    if args.command == 'migrate' and args.plan:
        return run_plan(args, context)
    if args.command == 'migrate':
        core.migrate(context.root, args.instance, trust=args.trust, confirm=context.confirm, recompute_posted=args.recompute_posted,
                     allow_destructive=args.allow_destructive, allow_maintenance=args.allow_maintenance, out=print)
    else:
        core.install_app(context.root, args.instance, args.name, trust=args.trust, confirm=context.confirm,
                         hosts='take' if args.yes else 'skip' if args.no_host else 'offer',
                         ask=input if sys.stdin.isatty() else None, out=print)


COMMANDS = {
    'install': (add_install, run_install),
    'migrate': (add_migrate, run_install),
    'makemigration': (add_makemigration, run_makemigration),
}
