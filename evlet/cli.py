"""Local development and authenticated connector commands."""

import argparse
import getpass
import json
from pathlib import Path
import sys

from .connector import ConnectorError, HTTPClient
from .mcp import MCPServer


STARTER_MODELS = '''from evlet import Boolean, Model, Registry, Text


class Item(Model):
    title = Text(required=True, max_length=160)
    done = Boolean(required=True, default=False)

    class Meta:
        label = 'Item'
        plural = 'Items'
        description = 'A small application starter.'
        title_field = 'title'
        list_fields = ('title', 'done')
        search_fields = ('title',)


registry = Registry(Item)
'''
STARTER_APP = '''import os
from pathlib import Path

from evlet import Application, Database, LocalAuth
from models import registry


def create_app():
    path = os.environ.get('EVLET_DATABASE', str(Path(__file__).parent / '.data' / 'application.sqlite3'))
    database = Database(path, registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000', 'http://localhost:8000',
                                       'http://127.0.0.1:5173', 'http://localhost:5173'),
                     secure_cookies=False, allow_setup=True)
    return Application(database, auth=auth)
'''
STARTER_README = '''# Application starter

Activate an environment containing the framework and Uvicorn, then run from this
folder:

```bash
python -m uvicorn app:create_app --factory --host 127.0.0.1 --port 8000
```

For the generated browser UI, run `npm --prefix frontend run dev` from the
framework checkout, then open http://127.0.0.1:5173. Create your own administrator
when prompted. No default account or password is provided. Both servers must be
on the same machine and these ports must be free.

`models.py` owns the definitions. The authenticated API exposes schema, compact
discovery, and ordinary record routes. Data is stored in `.data/` and survives
restarts. This starter permits local browser setup and insecure cookies on exact
loopback origins only. It is a development application, not deployment configuration.

After adding data, use explicit migrations for model changes. Do not delete or
recreate a working database to avoid writing a migration. See the framework's
model-engine, business-capabilities, and connector guides for the public contracts.

To connect a local tool client, sign in separately and store its session privately:

```bash
python -m evlet login --url http://127.0.0.1:8000 --username owner --session /private/path/session.json
python -m evlet describe --session /private/path/session.json --model item
python -m evlet mcp --session /private/path/session.json
```

The parent directory for the session file must exist. The password is prompted;
only the short-lived session is saved with mode 0600. MCP starts with read tools.
Use `--allow-writes` only when the client may invoke writes with your approval.
Run `python -m evlet logout --session /private/path/session.json` to revoke it.
'''


def scaffold(path):
    target = Path(path)
    target.mkdir(parents=False, exist_ok=False)
    for name, content in {'models.py': STARTER_MODELS, 'app.py': STARTER_APP, 'README.md': STARTER_README,
                          '.gitignore': '.data/\n.venv/\n__pycache__/\n*.session.json\n'}.items():
        (target / name).write_text(content, encoding='utf-8')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(prog='evlet', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('new', help='Create a small authenticated application in a new directory')
    create.add_argument('directory')
    login = commands.add_parser('login', help='Sign in and save a private session file')
    login.add_argument('--url', required=True); login.add_argument('--username', required=True)
    login.add_argument('--session', required=True)
    for name in ('describe', 'mcp', 'logout'):
        command = commands.add_parser(name)
        command.add_argument('--session', required=True)
        if name == 'describe':
            command.add_argument('--model')
        if name == 'mcp':
            command.add_argument('--allow-writes', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'new':
            print(f'Application created in {scaffold(args.directory)}')
            return
        if args.command == 'login':
            if Path(args.session).exists():
                raise ValueError('Session file already exists; sign out or choose a new private file')
            client = HTTPClient(args.url)
            password = getpass.getpass('Application password: ')
            signed_in = client.login(args.username, password)
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
            client.request('POST', '/api/auth/logout', {})
            Path(args.session).unlink()
            print('Session revoked and its local file removed.')
    except (KeyboardInterrupt, BrokenPipeError):
        return
    except (ConnectorError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
