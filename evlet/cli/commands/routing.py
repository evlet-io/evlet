"""evlet routes, evlet proxy and evlet host: the hosts and paths an instance answers on."""

import argparse

from . import INSTANCE_HELP


def add_routes(commands):
    routes = commands.add_parser('routes', help='Print the resolved hosts and paths of an instance')
    routes.add_argument('instance', help=INSTANCE_HELP)
    routes.add_argument('--port', type=int, default=8000, help='Development port shown in loopback origins')


def run_routes(args, context):
    from ...instances import load_runtime
    app = load_runtime(context.root, args.instance, port=args.port)
    try:
        print(app.routes.text())
    finally:
        app.database.close()


def add_proxy(commands):
    proxy = commands.add_parser('proxy', help='Print or write a Caddy or nginx configuration for the instance hosts')
    proxy.add_argument('instance', help=INSTANCE_HELP)
    kind = proxy.add_mutually_exclusive_group(required=True)
    kind.add_argument('--caddy', action='store_true')
    kind.add_argument('--nginx', action='store_true')
    proxy.add_argument('--write', metavar='PATH', help='Write the configuration to this file instead of printing it')
    proxy.add_argument('--check', action='store_true', help='Check that each host resolves to this server')
    proxy.add_argument('--ip', action='append', help='This server\'s public address, for an exact DNS match; optional')
    proxy.add_argument('--port', type=int, default=8000, help='Port the instance listens on')


def run_proxy(args, context):
    from ...instances import load_runtime
    from .. import proxy
    app = load_runtime(context.root, args.instance, port=args.port)
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


def add_host(commands):
    host = commands.add_parser('host', help="Serve a frontend on its own host: routes.py, the DNS check and the Caddy site")
    host_commands = host.add_subparsers(dest='host_command', required=True)
    host_add = host_commands.add_parser('add', help='Add a host to routes.py, check its DNS and rewrite the Caddy site')
    host_remove = host_commands.add_parser('remove', help='Remove a host from routes.py and rewrite the Caddy site')
    host_list = host_commands.add_parser('list', help='Print the hosts of an instance with their root frontends')
    for command in (host_add, host_remove, host_list):
        command.add_argument('instance', help=INSTANCE_HELP)
        command.add_argument('--project', default=argparse.SUPPRESS, help='Explicit project path')
    for command in (host_add, host_remove):
        command.add_argument('host')
    host_add.add_argument('--root', required=True, metavar='APP.FRONTEND', help='The static or server frontend the host serves at /')
    host_add.add_argument('--ip', action='append', help="This server's public address, for an exact DNS match; optional")
    for command in (host_add, host_remove):
        command.add_argument('--port', type=int, help='Port the service listens on (default: the one in deploy/, else 8000)')
        command.add_argument('--apply', action='store_true', help='Run the printed sudo steps: install the Caddy site, reload Caddy, restart the service')
        command.add_argument('--routes-only', action='store_true', help='Change routes.py only, for Caddy with on-demand TLS; then run evlet restart')


def run_host(args, context):
    from .. import core
    root = context.root
    if args.host_command == 'list':
        core.host_list(root, args.instance, out=print)
    elif args.apply and args.routes_only:
        raise ValueError('Choose --apply or --routes-only')
    elif args.host_command == 'add':
        core.host_add(root, args.instance, args.host, args.root, apply=args.apply, ips=args.ip, port=args.port,
                      routes_only=args.routes_only, out=print)
    else:
        core.host_remove(root, args.instance, args.host, apply=args.apply, port=args.port, routes_only=args.routes_only, out=print)


COMMANDS = {'routes': (add_routes, run_routes), 'proxy': (add_proxy, run_proxy), 'host': (add_host, run_host)}
