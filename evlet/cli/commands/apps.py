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
    from .. import core
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
        core.app_fetch(root, args.get, trust=args.trust, confirm=context.confirm, out=print)
    elif args.name:
        core.app_create(root, args.name, legacy=args.legacy, out=print)
    else:
        raise ValueError('Provide an app name, --get URL, or --list')


def add_build(commands):
    build = commands.add_parser('build', help='Build a versioned application generation without activating or migrating it')
    build.add_argument('--trust', action='store_true', help='Trust application imports and build code')


def run_build(args, context):
    from ..core import build
    build(context.root, trust=args.trust, confirm=context.confirm, out=print)


def add_instance(commands):
    instance = commands.add_parser('instance', help='Create or list isolated instances, or give a copied one its own id',
                                   description='evlet instance NAME creates an instance; evlet instance --list lists them with their ids; '
                                               'evlet instance new-id NAME gives an instance a new instance id, such as a copied folder.')
    instance.add_argument('name', nargs='?', help='The instance to create, or new-id')
    instance.add_argument('target', nargs='?', metavar='INSTANCE', help='With new-id: the instance that gets a new id')
    instance.add_argument('--list', action='store_true', help='List the instances, their installed apps and their ids')


def run_instance(args, context):
    from ..core import instance_create
    root = context.root
    if args.name == 'new-id':
        if args.list or not args.target:
            raise ValueError('Name the instance: evlet instance new-id NAME')
        from ..instance import new_id
        old, value = new_id(root, args.target)
        print(f'Instance {args.target} id: {value} (was {old})')
        return
    if args.target:
        raise ValueError('Give one instance name, or evlet instance new-id NAME')
    if args.list:
        if args.name:
            raise ValueError('Choose an instance name or --list')
        from ...instances import instance_identity, read_lock
        for path in sorted((root / 'instances').iterdir()):
            if path.is_dir() and not path.name.startswith('.'):
                state = read_lock(path)
                identity = instance_identity(path)
                print(f'{path.name}\t{", ".join(state["apps"]) or "no installed apps"}\t{identity["id"] if identity else "no id yet"}')
    elif args.name:
        instance_create(root, args.name, out=print)
    else:
        raise ValueError('Provide an instance name or --list')


COMMANDS = {'app': (add_app, run_app), 'build': (add_build, run_build), 'instance': (add_instance, run_instance)}
