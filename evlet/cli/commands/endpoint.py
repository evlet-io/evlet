"""evlet endpoint: serve the project endpoint, which runs the instances, or print a new sign-in link for it."""


def add_endpoint(commands):
    endpoint = commands.add_parser('endpoint', help="Serve the project endpoint: the portal, the project MCP and every instance")
    actions = endpoint.add_subparsers(dest='endpoint_command', metavar='ACTION')
    actions.add_parser('link', help='Print a new one-time sign-in link for the owner (development mode)')
    endpoint.add_argument('--no-link', action='store_true', help='Do not print a one-time sign-in link at startup')


def run_endpoint(args, context):
    from .. import endpoint
    root = context.root
    if args.endpoint_command == 'link':
        if endpoint.mode(root) != 'development':
            raise ValueError('Sign-in links are for development. In production sign in with the password and two-step sign-in')
        if not endpoint.exists(root):
            raise ValueError('This project has no endpoint. Add one with: evlet setup --endpoint')
        if endpoint.read_settings(root)['host']:
            raise ValueError('This endpoint is served on a public host, where sign-in links do not work. Sign in with the owner password')
        from ...devlink import issue, link
        app = endpoint.build(root)
        try:
            token, username = issue(app.database, endpoint.OWNER)
        finally:
            app.database.close()
        print(f'Sign in as {username} (works once, for 10 minutes):\n  {link(endpoint.url(root), token)}')
        return
    endpoint.serve(root, link=not args.no_link)


COMMANDS = {'endpoint': (add_endpoint, run_endpoint)}
