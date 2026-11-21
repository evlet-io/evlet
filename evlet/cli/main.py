"""Create apps, manage isolated instances, and use the bundled development tools."""

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from ..identity import from_argument, text as text_id
from .project import project_root


def confirm(message):
    return sys.stdin.isatty() and input(message + ' [y/N] ').strip().lower() in ('y', 'yes')


def add_link_options(command):
    command.add_argument('--no-link', action='store_true', help='Do not print a one-time sign-in link at startup')
    command.add_argument('--user', help='Sign the startup link in as this user (default: the built-in Administrator)')


def choose_instance(root, command):
    names = sorted(path.name for path in (root / 'instances').iterdir() if path.is_dir() and (path / 'config.py').is_file())
    if len(names) == 1:
        return names[0]
    if names and sys.stdin.isatty():
        return input('Choose an instance (' + ', '.join(names) + '): ').strip()
    raise ValueError(f'Select an instance with evlet {command} --instance NAME. Create and install an app first')


class ShowVersion(argparse.Action):
    """evlet --version: this evlet, and the Evlet in the project's .venv when run inside a project."""

    def __init__(self, option_strings, dest, **values):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, help='Print the Evlet version and exit')

    def __call__(self, parser, namespace, values, option_string=None):
        from ..framework import version
        from .setup import environment_version
        lines = [f'evlet {version()}']
        try:
            root = project_root(getattr(namespace, 'project', None))
        except (ValueError, OSError):
            root = None
        found = environment_version(root) if root is not None else None
        if found:
            lines.append(f'Project {root}: Evlet {found} in .venv')
        print('\n'.join(lines))
        parser.exit()


def check_framework_source(framework):
    """Refuse, in one line, to set up a project from an Evlet source checkout whose browser UI is not built yet;
    pip would fail with a long build traceback that ends in the same advice."""
    if framework is None:
        source = Path(__file__).resolve().parents[2]
    elif Path(framework).is_dir():
        source = Path(framework)
    else:
        return  # A wheel, an archive or a requirement: pip reports its own problems.
    if (source / 'pyproject.toml').is_file() and (source / 'frontend/package.json').is_file() \
            and not (source / 'evlet/ui/static/index.html').is_file():
        raise ValueError(f'The Evlet source checkout at {source} has no built browser UI, so it cannot be installed into a project. '
                         f'Build it first: npm --prefix {source / "frontend"} ci && npm --prefix {source / "frontend"} run build')


def parser():
    result = argparse.ArgumentParser(prog='evlet', description=__doc__)
    result.add_argument('--project', help='Project path; also accepted after the command')
    result.add_argument('--version', action=ShowVersion)
    commands = result.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('setup', help='Create apps/, instances/ and an isolated .venv')
    setup.add_argument('directory')
    setup.add_argument('--framework', help='Reviewed local framework wheel or source for offline/development setup')
    setup.add_argument('--app', action='append', metavar='NAMES', help='Create these new apps; a comma list such as notes,crm')
    setup.add_argument('--get', action='append', metavar='GIT_URLS', help='Fetch these apps from Git like evlet app --get; a comma list')
    setup.add_argument('--instance', metavar='NAME', help='Create this instance and install the apps in it')
    setup.add_argument('--trust', action='store_true', help='Trust the fetched and local app code for this whole run instead of asking')
    mode = setup.add_mutually_exclusive_group()
    mode.add_argument('--dev', action='store_true', help='Then run evlet dev on loopback and print a sign-in link')
    mode.add_argument('--host', metavar='HOSTNAME', help='Then prepare a Caddy site and a systemd unit for this public hostname')
    setup.add_argument('--port', type=int, default=8000, help='Port evlet dev or the service listens on (default: 8000)')
    setup.add_argument('--ip', action='append', help="With --host: this server's public address, for an exact DNS match; optional, kept in deploy/server.json")
    setup.add_argument('--apply', action='store_true', help='With --host: run the printed sudo steps; each is shown first')
    app = commands.add_parser('app', help='Create, fetch or list reusable apps')
    app.add_argument('name', nargs='?')
    app.add_argument('--legacy', action='store_true', help='Create the older nested AppDefinition layout (kept for existing apps)')
    app.add_argument('--trust', action='store_true', help='Trust acquired application and dependency build code')
    group = app.add_mutually_exclusive_group()
    group.add_argument('--get', metavar='GIT_URL', help='Statically fetch, then prepare and build after explicit trust')
    group.add_argument('--list', action='store_true')
    build = commands.add_parser('build', help='Build a versioned application generation without activating or migrating it')
    build.add_argument('--trust', action='store_true', help='Trust application imports and build code')
    instance = commands.add_parser('instance', help='Create or list isolated instances')
    instance.add_argument('name', nargs='?')
    instance.add_argument('--list', action='store_true')
    install = commands.add_parser('install', help='Install a trusted app and its available app dependencies')
    install.add_argument('name')
    install.add_argument('--instance', required=True)
    install.add_argument('--trust', action='store_true', help='Explicitly trust the selected Python apps and dependencies')
    offer = install.add_mutually_exclusive_group()
    offer.add_argument('--yes', action='store_true', help="Serve each new frontend with a suggested host on <label>.<instance domain> without asking")
    offer.add_argument('--no-host', action='store_true', help='Offer no host for frontends with a suggested host')
    migrate = commands.add_parser('migrate', help='Back up and migrate a selected instance; recover interrupted installation')
    migrate.add_argument('instance')
    migrate.add_argument('--trust', action='store_true', help='Trust changed app source without prompting')
    migrate.add_argument('--recompute-posted', action='store_true',
                         help='Let Recompute operations recalculate released records (in a release=True step) too; each changed one gets an Activity entry')
    migrate.add_argument('--allow-destructive', action='store_true',
                         help='Let DropField delete the columns and values of retired fields; a backup is written first')
    migrate.add_argument('--allow-maintenance', action='store_true',
                         help='Let RunData steps declared with maintenance=True write confirmed, tracked and posted records; each is audited')
    migrate.add_argument('--plan', action='store_true',
                         help='Print the pending migrations and how they differ from the models; write nothing')
    makemigration = commands.add_parser('makemigration', help="Write the migration --plan suggests into a flat app's migrations folder")
    makemigration.add_argument('app')
    makemigration.add_argument('--instance', required=True, help='The instance whose database the migration starts from')
    makemigration.add_argument('--name', default='changes', help='Migration name after its number (default: changes)')
    makemigration.add_argument('--trust', action='store_true', help='Trust changed app source without prompting')
    for name in ('serve', 'dev'):
        command = commands.add_parser(name, help='Serve an instance' if name == 'serve' else 'Develop with Python reloads and the bundled UI')
        if name == 'serve':
            command.add_argument('instance')
            command.add_argument('--jobs', action='store_true', help='Run the existing short-task queue in this process')
        else:
            command.add_argument('--instance', help='Required when the project has several instances')
            command.add_argument('--watch', action='store_true', help='Accepted for compatibility; evlet dev always reloads app source')
            add_link_options(command)
        command.add_argument('--debug', action='store_true', help='Show tracebacks for expected startup errors')
        command.add_argument('--host', default='127.0.0.1')
        if name == 'serve':
            command.add_argument('--ssl-certfile', help='Serve HTTPS directly with this certificate (PEM); needs --ssl-keyfile')
            command.add_argument('--ssl-keyfile', help='The private key for --ssl-certfile')
        command.add_argument('--port', type=int, default=8000)
    restart = commands.add_parser('restart', help="Check an instance, then restart its systemd service and wait until it answers")
    restart.add_argument('instance')
    restart.add_argument('--check', action='store_true', help='Run the checks only; restart nothing')
    restart.add_argument('--yes', action='store_true', help='Restart without asking')
    restart.add_argument('--service', metavar='NAME', help='The systemd unit to restart (default: evlet-<instance>)')
    restart.add_argument('--timeout', type=float, default=30.0, help='Seconds to wait for /health after the restart (default: 30)')
    upgrade = commands.add_parser('upgrade', help="Stage a newer Evlet, then plan, stop, back up, migrate and restart its instances")
    upgrade.add_argument('instance')
    upgrade.add_argument('--to', metavar='VERSION', help='Install this version (default: the newest 0.x.* of the current minor, or of the current major from 1.0)')
    upgrade.add_argument('--yes', action='store_true', help='Upgrade without asking')
    upgrade.add_argument('--all-instances', action='store_true', help='Upgrade every installed instance sharing the project environment')
    upgrade.add_argument('--service', metavar='NAME', help='The systemd unit to stop and restart (default: evlet-<instance>)')
    routes = commands.add_parser('routes', help='Print the resolved hosts and paths of an instance')
    routes.add_argument('instance')
    routes.add_argument('--port', type=int, default=8000, help='Development port shown in loopback origins')
    proxy = commands.add_parser('proxy', help='Print or write a Caddy or nginx configuration for the instance hosts')
    proxy.add_argument('instance')
    kind = proxy.add_mutually_exclusive_group(required=True)
    kind.add_argument('--caddy', action='store_true')
    kind.add_argument('--nginx', action='store_true')
    proxy.add_argument('--write', metavar='PATH', help='Write the configuration to this file instead of printing it')
    proxy.add_argument('--check', action='store_true', help='Check that each host resolves to this server')
    proxy.add_argument('--ip', action='append', help='This server\'s public address, for an exact DNS match; optional')
    proxy.add_argument('--port', type=int, default=8000, help='Port the instance listens on')
    host = commands.add_parser('host', help="Serve a frontend on its own host: routes.py, the DNS check and the Caddy site")
    host_commands = host.add_subparsers(dest='host_command', required=True)
    host_add = host_commands.add_parser('add', help='Add a host to routes.py, check its DNS and rewrite the Caddy site')
    host_remove = host_commands.add_parser('remove', help='Remove a host from routes.py and rewrite the Caddy site')
    host_list = host_commands.add_parser('list', help='Print the hosts of an instance with their root frontends')
    for command in (host_add, host_remove, host_list):
        command.add_argument('instance')
        command.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    for command in (host_add, host_remove):
        command.add_argument('host')
    host_add.add_argument('--root', required=True, metavar='APP.FRONTEND', help='The static or server frontend the host serves at /')
    host_add.add_argument('--ip', action='append', help="This server's public address, for an exact DNS match; optional")
    for command in (host_add, host_remove):
        command.add_argument('--port', type=int, help='Port the service listens on (default: the one in deploy/, else 8000)')
        command.add_argument('--apply', action='store_true', help='Run the printed sudo steps: install the Caddy site, reload Caddy, restart the service')
        command.add_argument('--routes-only', action='store_true', help='Change routes.py only, for Caddy with on-demand TLS; then run evlet restart')
    link = commands.add_parser('link-user', help='Link an account to a record, such as a portal user to their customer')
    link.add_argument('instance')
    link.add_argument('username')
    link.add_argument('model')
    link.add_argument('record_id', type=from_argument)
    link.add_argument('--remove', action='store_true', help='Remove the link instead')
    controls = commands.add_parser('controls', help='Restore access to Controls from the server shell')
    controls_commands = controls.add_subparsers(dest='controls_command', required=True)
    controls_grant = controls_commands.add_parser('grant', help='Make a user an active admin; audited as cli')
    controls_grant.add_argument('instance')
    controls_grant.add_argument('username')
    controls_grant.add_argument('--clear-allowlist', action='store_true',
                                help='Also clear the allowed networks set in Controls')
    controls_grant.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    users = commands.add_parser('users', help='Account recovery from the server shell')
    users_commands = users.add_subparsers(dest='users_command', required=True)
    reset_two_step = users_commands.add_parser('reset-two-step', help="Clear a user's two-step sign-in; they set it up again at next sign-in. Audited as cli")
    reset_two_step.add_argument('instance')
    reset_two_step.add_argument('username')
    reset_two_step.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    set_password = users_commands.add_parser('set-password', help="Set a user's password; asks twice without echo. Ends their sessions. Audited as cli")
    set_password.add_argument('instance')
    set_password.add_argument('username')
    set_password.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    set_email = users_commands.add_parser('set-email', help="Set a user's email address for notifications. Audited as cli")
    set_email.add_argument('instance')
    set_email.add_argument('username')
    set_email.add_argument('email')
    set_email.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    keys = commands.add_parser('secrets', help='Manage the key that seals connector secrets, two-step secrets and backup codes')
    keys_commands = keys.add_subparsers(dest='secrets_command', required=True)
    keys_rotate = keys_commands.add_parser('rotate', help='Re-encrypt saved secrets under a new secret.key')
    keys_rotate.add_argument('instance')
    keys_rotate.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    ids = commands.add_parser('ids', help='Check record ids')
    ids_commands = ids.add_subparsers(dest='ids_command', required=True)
    ids_check = ids_commands.add_parser('check', help='Report rows with an impossible id and tables that allow id changes')
    ids_check.add_argument('instance')
    ids_check.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    activity = commands.add_parser('activity', help="Check records' Activity")
    activity_commands = activity.add_subparsers(dest='activity_command', required=True)
    verify = activity_commands.add_parser('verify', help='Report breaks in the hash chains of tamper-evident Activity')
    verify.add_argument('instance')
    verify.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    ledger = commands.add_parser('ledger', help='Review ledger models')
    ledger_commands = ledger.add_subparsers(dest='ledger_command', required=True)
    ledger_list = ledger_commands.add_parser('list', help='Print each ledger model with its table, row count and options')
    ledger_list.add_argument('--instance', help='Required when the project has several instances')
    ledger_list.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    jobs = commands.add_parser('jobs', help='Review background jobs')
    jobs_commands = jobs.add_subparsers(dest='jobs_command', required=True)
    jobs_list = jobs_commands.add_parser('list', help='Print the newest jobs with their state, attempts and progress')
    jobs_list.add_argument('--instance', help='Required when the project has several instances')
    jobs_list.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    backup = commands.add_parser('backup', help="Copy an instance's four database files into data/backups as one set")
    backup.add_argument('--instance', help='Required when the project has several instances')
    backup.add_argument('--to', help='Write the set into this directory instead of data/backups')
    backup.add_argument('--with-files', action='store_true', help="Also archive the instance's files/ folder (uploaded attachments)")
    restore = commands.add_parser('restore', help="Replace an instance's database files with a backup set, with the server stopped")
    restore.add_argument('backup', help='The main file of the set, such as data/backups/20261112T101500000000Z.sqlite3')
    restore.add_argument('--instance', help='Required when the project has several instances')
    restore.add_argument('--yes', metavar='NAME', help='Restore without asking; give the instance name to confirm')
    for name, port in (('playground', 8000), ('docs', 8001)):
        command = commands.add_parser(name, help='Launch bundled ' + name + ' without a project')
        command.add_argument('--port', type=int, default=port)
        if name == 'playground':
            add_link_options(command)
    dev_link = commands.add_parser('dev-link', help='Print a fresh one-time sign-in link for a running evlet dev or evlet playground')
    dev_link.add_argument('--user', help='Sign in as this user (default: the built-in Administrator)')
    dev_link.add_argument('--instance', help='Required when the project has several instances')
    dev_link.add_argument('--port', type=int, help='The development server port (default: 8000, or the running Playground)')
    dev_link.add_argument('--playground', action='store_true', help='Use the running evlet playground even inside a project')
    for command in commands.choices.values():
        command.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    from .connector import add_commands
    add_commands(commands)
    return result


def command_name(values):
    """The command word of an argument list: the first value that is not an option (or --project's path)."""
    values = iter(values)
    for value in values:
        if value == '--project':
            next(values, None)
        elif not value.startswith('-'):
            return value
    return None


def main(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    command_parser = parser()
    if command_name(values) == 'journal':
        command_parser.error("'evlet journal' was renamed: use 'evlet activity verify INSTANCE'")
    args = command_parser.parse_args(values)
    from . import connector
    if args.command in connector.COMMANDS:
        return connector.run(args)
    try:
        if getattr(args, 'port', None) is not None and not 1 <= args.port <= 65535:
            raise ValueError('Use a port between 1 and 65535')
        if args.command == 'setup':
            from .setup import setup
            from . import site
            directory = Path(args.project) / args.directory if args.project else args.directory
            check_framework_source(args.framework)
            if not site.wanted(args):
                created = setup(directory, framework=args.framework)
                print(f'Project created: {created}')
                print(f'Next: cd {created} && evlet app notes && evlet instance acme && evlet install notes --instance acme '
                      '&& evlet dev --instance acme')
                return
            site.check(args)
            root = site.project(directory, framework=args.framework)
            if argv is None:
                # Continue inside the project's own .venv; the re-run skips the project step.
                from .setup import delegate
                sys.stdout.flush()  # exec discards unwritten output
                delegate(root, values)
            site.run(root, args, confirm=confirm)
            return
        if args.command in ('docs', 'playground'):
            if args.command == 'docs':
                from .docs import docs
                docs(port=args.port)
            else:
                from .playground import playground
                playground(port=args.port, link=not args.no_link, user=args.user)
            return
        if args.command == 'dev-link':
            from .devlink import dev_link
            print(dev_link(args.project, playground=args.playground, instance=args.instance, port=args.port, user=args.user))
            return
        root = project_root(args.project)
        if argv is None:
            from .setup import delegate
            delegate(root, values)
        if args.command == 'app':
            from .app import create_app, get_app
            if args.name and (args.get or args.list):
                raise ValueError('Choose an app name, --get, or --list')
            if args.list:
                from ..packages import read_package
                for path in sorted((root / 'apps').iterdir()):
                    if path.is_dir() and not path.name.startswith('.'):
                        package = read_package(path)
                        print(f'{package.name}\t{package.version}\t{package.title}')
            elif args.get:
                get_app(root, args.get, trust=args.trust, confirm=confirm)
            elif args.name:
                print(f'App created: {create_app(root, args.name, legacy=args.legacy)}')
            else:
                raise ValueError('Provide an app name, --get URL, or --list')
        elif args.command == 'build':
            from .app import prepare_dependencies
            from ..builds import prepare
            names = [path.name for path in sorted((root / 'apps').iterdir()) if path.is_dir() and not path.name.startswith('.')]
            if not args.trust and not confirm('Trust and build these local applications: ' + ', '.join(names) + '?'):
                raise ValueError('Build requires explicit trust; review source, then use --trust')
            packages = prepare_dependencies(root, names)
            _, manifest = prepare(root, packages)
            print('Built generation: ' + manifest['fingerprint'])
        elif args.command == 'instance':
            from .instance import create_instance
            if args.list:
                if args.name:
                    raise ValueError('Choose an instance name or --list')
                from ..instances import read_lock
                for path in sorted((root / 'instances').iterdir()):
                    if path.is_dir() and not path.name.startswith('.'):
                        state = read_lock(path)
                        print(f'{path.name}\t{", ".join(state["apps"]) or "no installed apps"}')
            elif args.name:
                print(f'Instance created: {create_instance(root, args.name)}')
            else:
                raise ValueError('Provide an instance name or --list')
        elif args.command == 'migrate' and args.plan:
            from .install import plan
            if args.recompute_posted or args.allow_destructive or args.allow_maintenance:
                raise ValueError('--plan writes nothing; run migrate without --plan to use --recompute-posted, '
                                 '--allow-destructive or --allow-maintenance')
            result, suggestions, packages, steps = plan(root, args.instance, trust=args.trust, confirm=confirm)
            from ..framework import version
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
        elif args.command == 'makemigration':
            from .install import makemigration
            target, review = makemigration(root, args.app, args.instance, name=args.name, trust=args.trust, confirm=confirm)
            print(f'Wrote {target}')
            print('Edit the lines marked with ... or # before running evlet migrate.' if review else
                  f'Review it, then run evlet migrate {args.instance}.')
        elif args.command in ('install', 'migrate'):
            from .install import install
            from ..instances import read_lock
            try:
                before = set(read_lock(root / 'instances' / args.instance)['apps'])
            except ValueError:
                before = set()
            posted = args.command == 'migrate' and args.recompute_posted
            result = install(root, args.instance, app_name=args.name if args.command == 'install' else None, trust=args.trust, confirm=confirm,
                             recompute_posted=posted, allow_destructive=args.command == 'migrate' and args.allow_destructive,
                             allow_maintenance=args.command == 'migrate' and args.allow_maintenance)
            print(f'Instance {args.instance}: installed ' + ', '.join(result['apps']))
            print('Applied migrations: ' + (', '.join(result['migrations']) or 'none; schema is current'))
            if result.get('framework'):
                print('Framework migrations: ' + ', '.join(result['framework']))
            if posted:
                print(f'Posted records recalculated: {result.get("posted", 0)}')
            if result['backup']:
                print('Backup: ' + result['backup'])
            if args.command == 'install':
                from .hosts import suggest
                suggest(root, args.instance, set(result['apps']) - before, yes=args.yes, skip=args.no_host,
                        ask=input if sys.stdin.isatty() else None)
        elif args.command == 'host':
            from . import hosts
            if args.host_command == 'list':
                print(hosts.listing(root, args.instance))
            elif args.apply and args.routes_only:
                raise ValueError('Choose --apply or --routes-only')
            elif args.host_command == 'add':
                hosts.add(root, args.instance, args.host, args.root, apply=args.apply, ips=args.ip, port=args.port, routes_only=args.routes_only)
            else:
                hosts.remove(root, args.instance, args.host, apply=args.apply, port=args.port, routes_only=args.routes_only)
        elif args.command == 'restart':
            from .restart import restart
            if not 1 <= args.timeout <= 3600:
                raise ValueError('Use a --timeout between 1 and 3600 seconds')
            if not restart(root, args.instance, service=args.service, check_only=args.check, yes=args.yes, timeout=args.timeout):
                raise SystemExit(1)
        elif args.command == 'upgrade':
            from .upgrade import upgrade
            if not upgrade(root, args.instance, to=args.to, service=args.service, yes=args.yes, all_instances=args.all_instances):
                raise SystemExit(1)
        elif args.command == 'routes':
            from ..instances import load_runtime
            app = load_runtime(root, args.instance, port=args.port)
            try:
                print(app.routes.text())
            finally:
                app.database.close()
        elif args.command == 'proxy':
            from ..instances import load_runtime
            from . import proxy
            app = load_runtime(root, args.instance, port=args.port)
            app.database.close()
            trusted = app.business.auth.trusted_proxies
            text = (proxy.caddy if args.caddy else proxy.nginx)(app.routes, args.port, args.instance, trusted)
            if args.write:
                print(f'Wrote {proxy.write(args.write, text)}. Reload the proxy yourself after reviewing it.')
                line = proxy.trust_line(trusted)
                if line:
                    print(f'Add this line to Instance(...) in instances/{args.instance}/config.py, then run evlet restart {args.instance}:\n    {line},')
            else:
                print(text, end='')
            if args.check:
                report, ok = proxy.check(app.routes, args.ip)
                print(report)
                if not ok:
                    raise SystemExit(1)
        elif args.command == 'link-user':
            from ..instances import load_runtime
            from ..auth import AccessError
            app = load_runtime(root, args.instance)
            try:
                auth = app.business.auth
                auth.initialize()
                from .. import qb
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
        elif args.command == 'controls':
            from .project import child
            from ..controls import grant, shell_address, within
            from ..instances import read_instance, read_routes
            path = child(root, 'instances', args.instance)
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
        elif args.command == 'users':
            from .project import child
            from ..controls import reset_two_step_from_shell
            from ..instances import read_instance
            path = child(root, 'instances', args.instance)
            read_instance(path)
            if args.users_command == 'set-password':
                import getpass
                from ..controls import set_password_from_shell
                config = read_instance(path)
                first = getpass.getpass(f'New password for {args.username}: ')
                if first != getpass.getpass('Repeat the password: '):
                    raise ValueError('The two passwords differ. Nothing was changed.')
                set_password_from_shell(path / 'data/database.sqlite3', args.username, first, config.security.password_min)
                print(f'Password set for {args.username}. Their other sessions, AI assistants and API keys ended.')
            elif args.users_command == 'set-email':
                from ..controls import set_email_from_shell
                address = set_email_from_shell(path / 'data/database.sqlite3', args.username, args.email)
                print(f'Email for {args.username} is now {address}.')
            else:
                reset_two_step_from_shell(path / 'data/database.sqlite3', args.username)
                print(f'Two-step sign-in is reset for {args.username}. They set it up again at their next sign-in.')
        elif args.command == 'secrets':
            from .project import child, instance_guard
            from ..instances import read_instance
            from ..secrets import SecretKey, rotate
            path = child(root, 'instances', args.instance)
            read_instance(path)
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
        elif args.command == 'ids':
            from .ids import check_instance
            problems = check_instance(root, args.instance)
            for line in problems:
                print(line)
            if problems:
                print(f'{len(problems)} problem' + ('' if len(problems) == 1 else 's'), file=sys.stderr)
                raise SystemExit(1)
            print('Every model row has a record id, and the database refuses id changes')
        elif args.command == 'activity':
            from .activity import verify_instance
            checked, breaks = verify_instance(root, args.instance)
            for entry, model, record_id, message in breaks:
                print(f'Entry {entry} ({model} {text_id(record_id)}): {message}')
            if breaks:
                print(f'{len(breaks)} break{"s" if len(breaks) > 1 else ""} in {checked} hashed entries', file=sys.stderr)
                raise SystemExit(1)
            print(f'Activity verified: {checked} hashed entries, no breaks')
        elif args.command == 'ledger':
            from .ledger import list_instance
            from ..ledger import table
            print(table(list_instance(root, args.instance or choose_instance(root, 'ledger list'))))
        elif args.command == 'backup':
            from .backup import backup
            name = args.instance or choose_instance(root, 'backup')
            written = backup(root, name, args.to, with_files=args.with_files)
            print('Backup: ' + str(written[0]))
            print('With: ' + ', '.join(item.name for item in written[1:]))
            instance_path = root / 'instances' / name
            if not args.with_files:
                print(f'Not included: {instance_path / "files"} (uploaded attachments); add --with-files to archive it.')
            print(f'Not included: {instance_path / "secret.key"}. Without it a restored database cannot open saved connector, '
                  'mailbox and two-step secrets. Keep a copy stored apart from these backups.')
        elif args.command == 'restore':
            from .backup import restore
            name = args.instance or choose_instance(root, 'restore')
            if args.yes is not None:
                if args.yes != name:
                    raise ValueError(f'--yes must name the instance being restored: {name}')
            elif not sys.stdin.isatty() or input(f'This replaces every database file of {name}. Type the instance name to restore: ').strip() != name:
                raise ValueError('Restore cancelled; nothing was changed')
            safety, single = restore(root, name, args.backup)
            print(f'Instance {name}: restored from {args.backup}')
            if safety:
                print(f'The files it replaced are kept in {safety}')
            if single:
                print(f'This backup is from before sessions, logs and Eva had their own files. Run evlet migrate {name} to move them before serving.')
        elif args.command == 'jobs':
            from .jobs import list_instance
            from ..jobs import table
            print(table(list_instance(root, args.instance or choose_instance(root, 'jobs list'))))
        elif args.command in ('serve', 'dev'):
            from .serve import serve
            name = args.instance or choose_instance(root, 'dev')
            development = args.command == 'dev'
            if getattr(args, 'watch', False):
                print('evlet dev always reloads app source; --watch is no longer needed.', file=sys.stderr)
            serve(root, name, host=args.host, port=args.port, development=development, jobs=getattr(args, 'jobs', False), debug=args.debug,
                  link=development and not args.no_link, user=args.user if development else None,
                  ssl_certfile=getattr(args, 'ssl_certfile', None), ssl_keyfile=getattr(args, 'ssl_keyfile', None))
    except (KeyboardInterrupt, BrokenPipeError):
        return
    except EOFError:
        print('Input ended before the command finished. Nothing was changed.', file=sys.stderr)
        raise SystemExit(1) from None
    except (ValueError, TypeError, SyntaxError, OSError, RuntimeError, ImportError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
