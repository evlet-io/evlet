"""Create apps, manage isolated instances, and use the bundled development tools."""

import argparse
from pathlib import Path
import sqlite3
import subprocess
import sys

from .project import project_root


def confirm(message):
    return sys.stdin.isatty() and input(message + ' [y/N] ').strip().lower() in ('y', 'yes')


def parser():
    result = argparse.ArgumentParser(prog='evlet', description=__doc__)
    result.add_argument('--project', help='Project path; also accepted after the command')
    commands = result.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('setup', help='Create apps/, instances/ and an isolated .venv')
    setup.add_argument('directory')
    setup.add_argument('--framework', help='Reviewed local framework wheel or source for offline/development setup')
    app = commands.add_parser('app', help='Create, fetch or list reusable apps')
    app.add_argument('name', nargs='?')
    app.add_argument('--legacy', action='store_true', help='Create the supported nested AppDefinition scaffold')
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
    migrate = commands.add_parser('migrate', help='Back up and migrate a selected instance; recover interrupted installation')
    migrate.add_argument('instance')
    migrate.add_argument('--trust', action='store_true', help='Trust changed app source without prompting')
    for name in ('serve', 'dev'):
        command = commands.add_parser(name, help='Serve an instance' if name == 'serve' else 'Develop with Python reloads and the bundled UI')
        if name == 'serve':
            command.add_argument('instance')
            command.add_argument('--jobs', action='store_true', help='Run the existing short-task queue in this process')
        else:
            command.add_argument('--instance', help='Required when the project has several instances')
            command.add_argument('--watch', action='store_true', help='Rebuild and reload when app source or assets change')
        command.add_argument('--debug', action='store_true', help='Show tracebacks for expected startup errors')
        command.add_argument('--host', default='127.0.0.1')
        command.add_argument('--port', type=int, default=8000)
    routes = commands.add_parser('routes', help='Print the resolved hosts and paths of an instance')
    routes.add_argument('instance')
    routes.add_argument('--port', type=int, default=8000, help='Development port shown in loopback origins')
    effects = commands.add_parser('effects', help='Check the rows records post to other models')
    effects_commands = effects.add_subparsers(dest='effects_command', required=True)
    effects_check = effects_commands.add_parser('check', help='Recompute effect rows and report differences without writing')
    effects_check.add_argument('instance')
    effects_check.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    journal = commands.add_parser('journal', help='Check record journals')
    journal_commands = journal.add_subparsers(dest='journal_command', required=True)
    verify = journal_commands.add_parser('verify', help='Report breaks in the hash chains of tamper-evident journals')
    verify.add_argument('instance')
    verify.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    for name, port in (('playground', 8000), ('docs', 8001)):
        command = commands.add_parser(name, help='Launch bundled ' + name + ' without a project')
        command.add_argument('--port', type=int, default=port)
    for command in commands.choices.values():
        command.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    commands.add_parser('new', help='Create the original standalone starter', add_help=False)
    from .connector import add_commands
    add_commands(commands)
    return result


def main(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == 'new':
        from .legacy import main as legacy
        return legacy(values)
    command_parser = parser()
    args = command_parser.parse_args(values)
    if args.command in ('login', 'logout', 'describe', 'mcp'):
        from .connector import run
        return run(args)
    try:
        if hasattr(args, 'port') and not 1 <= args.port <= 65535:
            raise ValueError('Use a port between 1 and 65535')
        if args.command == 'setup':
            from .setup import setup
            directory = Path(args.project) / args.directory if args.project else args.directory
            print(f'Project created: {setup(directory, framework=args.framework)}')
            return
        if args.command in ('docs', 'playground'):
            if args.command == 'docs':
                from .docs import docs
                docs(port=args.port)
            else:
                from .playground import playground
                playground(port=args.port)
            return
        root = project_root(args.project)
        if argv is None:
            from .setup import delegate
            delegate(root, values)
        if args.command == 'app':
            from .app import create_app, fetch_app
            if args.name and (args.get or args.list):
                raise ValueError('Choose an app name, --get, or --list')
            if args.list:
                from ..packages import read_package
                for path in sorted((root / 'apps').iterdir()):
                    if path.is_dir() and not path.name.startswith('.'):
                        package = read_package(path)
                        print(f'{package.name}\t{package.version}\t{package.title}')
            elif args.get:
                package = fetch_app(root, args.get, return_package=True)
                print(f'Fetched {package.name} {package.version} at {package.locked()["commit"]}; no app code has run')
                from .app import prepare_dependencies
                from ..builds import prepare
                names = [package.name]
                if not args.trust and not confirm('Trust and prepare ' + package.name + ' from ' + args.get + ' and its declared dependencies?'):
                    raise ValueError('Fetched but not built: review source, then run evlet build --trust')
                _, manifest = prepare(root, prepare_dependencies(root, names))
                print('Built generation: ' + manifest['fingerprint'])
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
        elif args.command in ('install', 'migrate'):
            from .install import install
            result = install(root, args.instance, app_name=args.name if args.command == 'install' else None, trust=args.trust, confirm=confirm)
            print(f'Instance {args.instance}: installed ' + ', '.join(result['apps']))
            print('Applied migrations: ' + (', '.join(result['migrations']) or 'none; schema is current'))
            if result['backup']:
                print('Backup: ' + result['backup'])
        elif args.command == 'routes':
            from ..instances import load_runtime
            app = load_runtime(root, args.instance, port=args.port)
            try:
                print(app.routes.text())
            finally:
                app.database.close()
        elif args.command == 'effects':
            from .effects import check_instance
            from ..effects import describe
            differences = check_instance(root, args.instance)
            for item in differences:
                print(describe(item))
            if differences:
                print(f'{len(differences)} difference' + ('' if len(differences) == 1 else 's'), file=sys.stderr)
                raise SystemExit(1)
            print('No differences')
        elif args.command == 'journal':
            from .journal import verify_instance
            checked, breaks = verify_instance(root, args.instance)
            for entry, model, record_id, message in breaks:
                print(f'Entry {entry} ({model} {record_id}): {message}')
            if breaks:
                print(f'{len(breaks)} break{"s" if len(breaks) > 1 else ""} in {checked} hashed entries', file=sys.stderr)
                raise SystemExit(1)
            print(f'Journal verified: {checked} hashed entries, no breaks')
        elif args.command in ('serve', 'dev'):
            from .serve import serve
            name = args.instance
            if not name:
                names = sorted(path.name for path in (root / 'instances').iterdir() if path.is_dir() and (path / 'config.py').is_file())
                if len(names) == 1:
                    name = names[0]
                elif names and sys.stdin.isatty():
                    name = input('Choose an instance (' + ', '.join(names) + '): ').strip()
                else:
                    raise ValueError('Select an instance with evlet dev --instance NAME. Create and install an app first')
            serve(root, name, host=args.host, port=args.port, development=args.command == 'dev', jobs=getattr(args, 'jobs', False), debug=args.debug)
    except (KeyboardInterrupt, BrokenPipeError):
        return
    except (ValueError, TypeError, SyntaxError, OSError, RuntimeError, ImportError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
