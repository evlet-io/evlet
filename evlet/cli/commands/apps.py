"""evlet app, evlet build and evlet instance: the apps and instances of a project."""


def add_app(commands):
    app = commands.add_parser('app', help='Create, fetch or list reusable apps')
    app.add_argument('name', nargs='?')
    app.add_argument('--legacy', action='store_true', help='Create the older nested AppDefinition layout (kept for existing apps)')
    app.add_argument('--trust', action='store_true', help='Trust acquired application and dependency build code')
    group = app.add_mutually_exclusive_group()
    group.add_argument('--get', metavar='GIT_URL', help='Statically fetch, then prepare and build after explicit trust')
    group.add_argument('--list', action='store_true')


def run_app(args, context):
    from ..app import create_app, get_app
    root = context.root
    if args.name and (args.get or args.list):
        raise ValueError('Choose an app name, --get, or --list')
    if args.list:
        from ...packages import read_package
        for path in sorted((root / 'apps').iterdir()):
            if path.is_dir() and not path.name.startswith('.'):
                package = read_package(path)
                print(f'{package.name}\t{package.version}\t{package.title}')
    elif args.get:
        get_app(root, args.get, trust=args.trust, confirm=context.confirm)
    elif args.name:
        print(f'App created: {create_app(root, args.name, legacy=args.legacy)}')
    else:
        raise ValueError('Provide an app name, --get URL, or --list')


def add_build(commands):
    build = commands.add_parser('build', help='Build a versioned application generation without activating or migrating it')
    build.add_argument('--trust', action='store_true', help='Trust application imports and build code')


def run_build(args, context):
    from ..app import prepare_dependencies
    from ...builds import prepare
    root = context.root
    names = [path.name for path in sorted((root / 'apps').iterdir()) if path.is_dir() and not path.name.startswith('.')]
    if not args.trust and not context.confirm('Trust and build these local applications: ' + ', '.join(names) + '?'):
        raise ValueError('Build requires explicit trust; review source, then use --trust')
    packages = prepare_dependencies(root, names)
    _, manifest = prepare(root, packages)
    print('Built generation: ' + manifest['fingerprint'])


def add_instance(commands):
    instance = commands.add_parser('instance', help='Create or list isolated instances')
    instance.add_argument('name', nargs='?')
    instance.add_argument('--list', action='store_true')


def run_instance(args, context):
    from ..instance import create_instance
    root = context.root
    if args.list:
        if args.name:
            raise ValueError('Choose an instance name or --list')
        from ...instances import read_lock
        for path in sorted((root / 'instances').iterdir()):
            if path.is_dir() and not path.name.startswith('.'):
                state = read_lock(path)
                print(f'{path.name}\t{", ".join(state["apps"]) or "no installed apps"}')
    elif args.name:
        print(f'Instance created: {create_instance(root, args.name)}')
    else:
        raise ValueError('Provide an instance name or --list')


COMMANDS = {'app': (add_app, run_app), 'build': (add_build, run_build), 'instance': (add_instance, run_instance)}
