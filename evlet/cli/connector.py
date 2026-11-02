"""Authenticated connector commands: login, logout, describe and mcp."""

import getpass
import json
from pathlib import Path
import sys

from ..connector import ConnectorError, HTTPClient
from ..mcp import MCPServer


COMMANDS = ('login', 'logout', 'describe', 'mcp')


def add_commands(commands):
    login = commands.add_parser('login', help='Sign in and save a private connector session file')
    login.add_argument('--url', required=True, help='Application URL, for example http://127.0.0.1:8000')
    login.add_argument('--username', required=True)
    login.add_argument('--session', required=True, help='New private session file; the password is prompted')
    logout = commands.add_parser('logout', help='End a connector session and remove its file')
    describe = commands.add_parser('describe', help='Print authenticated discovery, or one model with --model')
    mcp = commands.add_parser('mcp', help='Run the authenticated MCP connector over stdio')
    for command in (logout, describe, mcp):
        command.add_argument('--session', required=True, help='Session file saved by evlet login')
    describe.add_argument('--model', help='Model name to describe')
    mcp.add_argument('--allow-writes', action='store_true', help='Also expose write tools; server permission checks still apply')


def run(args):
    try:
        if args.command == 'login':
            if Path(args.session).exists():
                raise ValueError('Session file already exists; sign out or choose a new private file')
            client = HTTPClient(args.url)
            password = getpass.getpass('Application password: ')
            signed_in = client.login(args.username, password, code=lambda: getpass.getpass('Two-step code: '))
            try:
                signed_in.save(args.session)
            except Exception:
                try:
                    signed_in.request('POST', '/api/auth/logout', {})
                except ConnectorError:
                    pass
                raise
            print('Signed in. Session saved privately; password was not stored.')
            return
        client = HTTPClient.load(args.session)
        if args.command == 'mcp':
            MCPServer(client, allow_writes=args.allow_writes).serve()
        elif args.command == 'describe':
            result = MCPServer(client).call('describe_model', {'model': args.model}) if args.model else client.get('/api/_discovery')
            print(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
        else:
            try:
                client.request('POST', '/api/auth/logout', {})
            except ConnectorError as exc:
                if exc.status != 401:
                    raise
            Path(args.session).unlink()
            print('Session ended and its local file removed.')
    except (KeyboardInterrupt, BrokenPipeError):
        return
    except (ConnectorError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
