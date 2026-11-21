"""evlet serve and evlet dev: run an instance, for service or for development."""

import sys

from . import INSTANCE_HELP, add_instance_option, add_link_options, choose_instance, require_instance


def add_serve(commands):
    command = commands.add_parser('serve', help='Serve an instance')
    command.add_argument('instance', nargs='?', help=INSTANCE_HELP + ' (default: the only one)')
    command.add_argument('--jobs', action='store_true', help='Run the existing short-task queue in this process')
    command.add_argument('--debug', action='store_true', help='Show tracebacks for expected startup errors')
    command.add_argument('--host', default='127.0.0.1')
    command.add_argument('--ssl-certfile', help='Serve HTTPS directly with this certificate (PEM); needs --ssl-keyfile')
    command.add_argument('--ssl-keyfile', help='The private key for --ssl-certfile')
    command.add_argument('--port', type=int, default=8000)


def add_dev(commands):
    command = commands.add_parser('dev', help='Develop with Python reloads and the bundled UI')
    add_instance_option(command)
    command.add_argument('--watch', action='store_true', help='Accepted for compatibility; evlet dev always reloads app source')
    add_link_options(command)
    command.add_argument('--debug', action='store_true', help='Show tracebacks for expected startup errors')
    command.add_argument('--host', default='127.0.0.1')
    command.add_argument('--port', type=int, default=8000)


def run_serve(args, context):
    from ..serve import serve
    root = context.root
    if args.command == 'serve':
        require_instance(root, args)
    name = args.instance or choose_instance(root, 'dev')
    development = args.command == 'dev'
    if getattr(args, 'watch', False):
        print('evlet dev always reloads app source; --watch is no longer needed.', file=sys.stderr)
    serve(root, name, host=args.host, port=args.port, development=development, jobs=getattr(args, 'jobs', False), debug=args.debug,
          link=development and not args.no_link, user=args.user if development else None,
          ssl_certfile=getattr(args, 'ssl_certfile', None), ssl_keyfile=getattr(args, 'ssl_keyfile', None))


COMMANDS = {'serve': (add_serve, run_serve), 'dev': (add_dev, run_serve)}
