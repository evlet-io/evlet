"""evlet playground, evlet docs and evlet dev-link: the bundled servers that need no project, and sign-in links."""

from . import add_instance_option, add_link_options


def add_playground(commands):
    command = commands.add_parser('playground', help='Launch bundled playground without a project')
    command.add_argument('--port', type=int, default=8000)
    add_link_options(command)


def run_playground(args, context):
    from ..playground import playground
    playground(port=args.port, link=not args.no_link, user=args.user)


def add_docs(commands):
    command = commands.add_parser('docs', help='Launch bundled docs without a project')
    command.add_argument('--port', type=int, default=8001)


def run_docs(args, context):
    from ..docs import docs
    docs(port=args.port)


def add_dev_link(commands):
    dev_link = commands.add_parser('dev-link', help='Print a fresh one-time sign-in link for a running evlet dev or evlet playground')
    dev_link.add_argument('--user', help='Sign in as this user (default: the built-in Administrator)')
    add_instance_option(dev_link)
    dev_link.add_argument('--port', type=int, help='The development server port (default: 8000, or the running Playground)')
    dev_link.add_argument('--playground', action='store_true', help='Use the running evlet playground even inside a project')


def run_dev_link(args, context):
    from ..devlink import dev_link
    print(dev_link(args.project, playground=args.playground, instance=args.instance, port=args.port, user=args.user))


COMMANDS = {
    'playground': (add_playground, run_playground),
    'docs': (add_docs, run_docs),
    'dev-link': (add_dev_link, run_dev_link),
}
