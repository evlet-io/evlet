"""The original standalone starter created by `evlet new`."""

import argparse
from pathlib import Path
import sys


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
python -m uvicorn app:create_app --factory --host 127.0.0.1 --port 8000 --reload
```

Open http://127.0.0.1:8000. Create your administrator when prompted, then sign in.
Docs opens first; Items is in the sidebar. The installed package includes the
browser UI, so ordinary application development needs only Python. No default
account or password is provided.

When running from a source checkout, build the bundled browser UI once with
`npm --prefix frontend ci && npm --prefix frontend run build`.

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
    args = parser.parse_args(argv)
    try:
        print(f'Application created in {scaffold(args.directory)}')
    except (KeyboardInterrupt, BrokenPipeError):
        return
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
