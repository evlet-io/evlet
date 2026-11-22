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
    from ..install import plan
    if args.recompute_posted or args.allow_destructive or args.allow_maintenance:
        raise ValueError('--plan writes nothing; run migrate without --plan to use --recompute-posted, '
                         '--allow-destructive or --allow-maintenance')
    result, suggestions, packages, steps = plan(context.root, args.instance, trust=args.trust, confirm=context.confirm)
    from ...framework import version
    print(f'Instance {args.instance}: plan only; nothing was written')
    print(f'Planned with Evlet {version()}')
    print(result.text(suggestion=False))
    if steps:
        print('Pending framework migrations (run after a backup):')
        print('\n'.join(f'  {step.name}\n    {step.summary}' for step in steps))
    for package in packages:
        migration, code = suggestions[package.name]
        if not code:
            continue
        where = f'apps/{package.path.name}/migrations/m{migration}.py' if package.layout == 'flat' \
            else f'the migrations tuple in apps/{package.path.name}/{package.name}/migrations/__init__.py'
        print(f'Suggested migration for {package.name} (save as {where}; review it and fill in any ...):')
        print('\n'.join(('    ' + line).rstrip() for line in code.splitlines()))
        if package.layout == 'flat':
            print(f'Or write it with: evlet makemigration {package.name} --instance {args.instance}')


def run_makemigration(args, context):
    from ..install import makemigration
    require_instance(context.root, args)
    target, review = makemigration(context.root, args.app, args.instance, name=args.name, trust=args.trust, confirm=context.confirm)
    print(f'Wrote {target}')
    print('Edit the lines marked with ... or # before running evlet migrate.' if review else
          f'Review it, then run evlet migrate {args.instance}.')


def run_install(args, context):
    """evlet install and evlet migrate, which share the installer and its report."""
    root = context.root
    require_instance(root, args)
    from ..site import as_state_owner
    if as_state_owner(root, args.instance, context.values):
        return
    if args.command == 'migrate' and args.plan:
        return run_plan(args, context)
    from ..install import install
    from ...instances import read_lock
    try:
        before = set(read_lock(root / 'instances' / args.instance)['apps'])
    except ValueError:
        before = set()
    migrating = args.command == 'migrate'
    posted = migrating and args.recompute_posted
    result = install(root, args.instance, app_name=None if migrating else args.name, trust=args.trust, confirm=context.confirm,
                     recompute_posted=posted, allow_destructive=migrating and args.allow_destructive,
                     allow_maintenance=migrating and args.allow_maintenance)
    from ..install import migration_label
    print(f'Instance {args.instance}: ' + ('apps ' if migrating else 'installed ') + ', '.join(result['apps']))
    added = [name for name in result['apps'] if name not in before]
    if added:
        # The same rule as a first install: a new app's tables come from its models; later changes need a migration.
        print('Tables created from the models of ' + ', '.join(added) +
              f'. After this, every model change needs a migration: evlet makemigration APP --instance {args.instance}')
    print('Applied migrations: ' + (', '.join(map(migration_label, result['migrations'])) or 'none; schema is current'))
    if result.get('framework') and result['backup']:
        print('Framework migrations: ' + ', '.join(result['framework']))
    elif result.get('framework'):
        print(f'Framework tables: created ({len(result["framework"])} framework migrations recorded)')
    if posted:
        print(f'Posted records recalculated: {result.get("posted", 0)}')
    if result['backup']:
        print('Backup: ' + result['backup'])
    if not migrating:
        from ..hosts import suggest
        suggest(root, args.instance, set(result['apps']) - before, yes=args.yes, skip=args.no_host,
                ask=input if sys.stdin.isatty() else None)


COMMANDS = {
    'install': (add_install, run_install),
    'migrate': (add_migrate, run_install),
    'makemigration': (add_makemigration, run_makemigration),
}
