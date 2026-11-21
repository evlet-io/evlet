"""Test an app the way an instance runs it: real SQLite, the real ASGI application and its permission checks.

    from evlet.testing import TestApp

    def test_editor_creates_an_item(tmp_path):
        with TestApp('apps/notes', directory=tmp_path) as app:
            editor = app.client(role='editor')
            response = editor.post('/api/notes_item', {'title': 'First'})
            assert response.status == 201

TestApp builds a temporary instance from an app folder (or a project's apps) the same way `evlet install`
does: the app's models, migrations, reports, APIs, permissions and workspaces, on a new SQLite database
in a temporary directory. Nothing in the project or its instances is written. See docs/testing.md.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
import shutil
import tempfile

from . import qb
from .auth import User

__all__ = ['TestApp', 'Client', 'TestResponse']

ORIGIN = 'http://127.0.0.1:8000'
HOST = b'127.0.0.1:8000'
# Test accounts sign in without two-step; an instance= config keeps its other rules.
TEST_CONFIG = '''from evlet import Instance, Security

instance = Instance(name={name!r})
security = Security(password_min=8, admin_two_step=False, two_step=False)
'''


@dataclass
class TestResponse:
    """One HTTP answer: the status, the decoded JSON body (None when empty or not JSON) and the headers."""
    __test__ = False  # Not a pytest test class.
    status: int
    data: object
    headers: dict = field(repr=False)
    body: bytes = field(default=b'', repr=False)

    def json(self):
        return self.data


class Client:
    """Sends JSON requests through the real ASGI application, as a browser would.

    A signed-in client carries its session cookie, and writes carry the request protection header
    (X-Evlet-Request) and the session's CSRF token, so every account, row, field and action check runs."""

    def __init__(self, application, *, cookie=None, csrf=None, user=None):
        self.application, self.cookie, self.csrf, self.user = application, cookie, csrf, user

    def request(self, method, path, data=None, *, headers=()):
        method = method.upper()
        sent = [(b'host', HOST), (b'origin', ORIGIN.encode()), (b'user-agent', b'evlet-testing')]
        if self.cookie:
            sent.append((b'cookie', self.cookie.encode()))
        if method not in ('GET', 'HEAD'):
            sent.append((b'x-evlet-request', b'1'))
            if self.csrf:
                sent.append((b'x-csrf-token', self.csrf.encode()))
        body = b'' if data is None else json.dumps(data).encode()
        if data is not None:
            sent.append((b'content-type', b'application/json'))
        sent.extend((name.lower().encode() if isinstance(name, str) else name, value.encode() if isinstance(value, str) else value)
                    for name, value in (headers.items() if isinstance(headers, dict) else headers))
        route, _, query = path.partition('?')
        messages = []
        events = [{'type': 'http.request', 'body': body, 'more_body': False}]

        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}

        async def send(event):
            messages.append(event)

        async def run():
            await self.application({'type': 'http', 'http_version': '1.1', 'method': method, 'scheme': 'http', 'path': route,
                                    'raw_path': route.encode(), 'query_string': query.encode(), 'headers': sent,
                                    'client': ('127.0.0.1', 50000), 'server': ('127.0.0.1', 8000)}, receive, send)

        asyncio.run(run())
        start = next(message for message in messages if message['type'] == 'http.response.start')
        raw = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
        response_headers = {}
        for name, value in start.get('headers', []):
            response_headers.setdefault(name.decode('latin1').lower(), value.decode('latin1'))
        try:
            decoded = json.loads(raw) if raw else None
        except ValueError:
            decoded = None
        return TestResponse(start['status'], decoded, response_headers, raw)

    def get(self, path, *, headers=()):
        return self.request('GET', path, headers=headers)

    def post(self, path, data=None, *, headers=()):
        return self.request('POST', path, {} if data is None else data, headers=headers)

    def put(self, path, data=None, *, headers=()):
        return self.request('PUT', path, {} if data is None else data, headers=headers)

    def patch(self, path, data=None, *, headers=()):
        return self.request('PATCH', path, {} if data is None else data, headers=headers)

    def delete(self, path, *, headers=()):
        return self.request('DELETE', path, headers=headers)

    def sign_in(self, username, password):
        """Sign in through POST /api/auth/login, as the sign-in page does. Raises AssertionError when refused."""
        answer = self.request('POST', '/api/auth/login', {'username': username, 'password': password})
        if answer.status != 200 or not isinstance(answer.data, dict) or 'csrf' not in answer.data:
            raise AssertionError(f'Sign-in as {username} failed: {answer.status} {answer.data}')
        cookie = answer.headers.get('set-cookie', '')
        self.cookie, self.csrf = cookie.split(';')[0], answer.data['csrf']
        self.user = answer.data['user']['username']
        return self


def forget_source_imports(packages):
    """Drop app modules imported straight from the app folder, such as the app's __init__.py that pytest imports
    while collecting apps/NAME/tests. The instance imports its own build of the same source."""
    import sys
    for package in packages:
        source = Path(package.path).resolve()
        for name in list(sys.modules):
            if name == package.name or name.startswith(package.name + '.'):
                origin = getattr(sys.modules[name], '__file__', None)
                if origin and Path(origin).resolve().is_relative_to(source):
                    del sys.modules[name]


class TestApp:
    """A temporary instance of one or more apps on a real SQLite database.

    `path` is an app folder (its pyproject.toml has [tool.evlet] app = true) or a project folder; for a
    project, `apps` names the apps to install (default: all of them). `instance` names an instance of the
    project whose config.py and routes.py are used; its database and files are not touched. `directory`
    holds the temporary instance (default: a new temporary directory, removed by close()).

    Use it as a context manager, or call close() when done."""

    __test__ = False  # Not a pytest test class.

    def __init__(self, path, *, apps=None, instance=None, directory=None):
        from .framework import state, upgrade
        from .instances import INSTALLED_APPS, build_instance, read_instance, register_schedules
        from .migrations import migrate
        from .packages import read_package, resolve_packages
        path = Path(path).resolve()
        self._owned = directory is None
        self.directory = Path(tempfile.mkdtemp(prefix='evlet-test-') if directory is None else directory).resolve()
        try:
            if (path / 'apps').is_dir():
                project = path
                names = list(apps) if apps is not None else sorted(item.name for item in (path / 'apps').iterdir()
                                                                  if item.is_dir() and not item.name.startswith('.'))
                packages = resolve_packages(project, names, check_installed=False)
            else:
                if instance is not None:
                    raise ValueError('instance= needs a project folder, not an app folder')
                package = read_package(path)
                if apps is not None and list(apps) != [package.name]:
                    raise ValueError(f'{path} is the app {package.name}; give a project folder to test several apps')
                project, packages = None, [package]
            name = instance or 'test'
            root = self.directory / 'project'
            target = root / 'instances' / name
            target.mkdir(parents=True, mode=0o700)
            (root / 'apps').mkdir(exist_ok=True)
            if instance is not None:
                source = project / 'instances' / instance
                read_instance(source)
                shutil.copyfile(source / 'config.py', target / 'config.py')
                if (source / 'routes.py').is_file():
                    shutil.copyfile(source / 'routes.py', target / 'routes.py')
            else:
                (target / 'config.py').write_text(TEST_CONFIG.format(name=name), encoding='utf-8')
            config = read_instance(target)
            (target / 'data').mkdir(mode=0o700)
            # What evlet install records: the apps.lock beside the database, and the same manifest inside it.
            manifest = {'apps': {package.name: package.locked() for package in packages}, 'evlet': state()}
            (target / 'apps.lock').write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
            forget_source_imports(packages)
            self.application, schedules = build_instance(config, target, packages)
            self.database = self.application.database
            # The steps evlet install runs on a new database: app migrations, framework steps, then startup.
            migrate(self.database, self.database.migrations)
            upgrade(self.database)
            self.application.initialize()
            register_schedules(self.application, schedules)
            with self.database.transaction() as tx:
                tx.connection.ensure_table(INSTALLED_APPS)
                tx.connection.run(qb.insert('evlet_installed_apps', id=1, manifest=json.dumps(manifest, sort_keys=True)))
            self.business = self.application.business
            self.apps = [package.name for package in packages]
        except BaseException:
            self._remove()
            raise

    def model(self, name):
        """The app's model class by class name (NotesItem) or table (notesitem), as this instance loaded it."""
        models = self.database.registry.models
        found = models.get(name) or next((model for model in models.values() if model.__name__ == name), None)
        if found is None:
            raise KeyError(f'No model {name}; the apps define ' + ', '.join(sorted(model.__name__ for model in models.values())))
        return found

    # Accounts

    def user(self, username=None, *, role='admin', password=None):
        """The account `username` (default: the role's name), created with `role` when it does not exist yet."""
        username = (username or role).lower()
        if len(username) < 3:
            username = username + '_user'
        with self.database._connection() as connection:
            row = connection.run(qb.select('evlet_users', 'id', 'username', 'role').where(username=username)).one()
        if row is not None:
            return User(row['id'], row['username'], row['role'])
        return self.business.auth.create_user(username=username, password=password or secrets.token_urlsafe(18), role=role)

    def client(self, user=None, *, role=None):
        """A Client signed in as `user` (a username or a User) or as an account with `role`.
        Without either, the client is anonymous."""
        if user is None and role is None:
            return Client(self.application)
        account = user if isinstance(user, User) else self.user(user, role=role or 'admin')
        auth = self.business.auth
        with self.database._connection() as connection:
            row = connection.run(qb.select('evlet_users', 'id', 'username', 'role', 'session_generation').where(id=account.id)).one()
        scope = {'type': 'http', 'headers': [(b'host', HOST), (b'user-agent', b'evlet-testing')], 'client': ('127.0.0.1', 50000)}
        with self.database.transaction(store='sessions') as tx:
            answer, cookies = auth.start_session(tx, scope, row, auth.realm(scope), verified=True)
        cookie = dict(cookies)[b'set-cookie'].decode().split(';')[0]
        return Client(self.application, cookie=cookie, csrf=answer['csrf'], user=row['username'])

    def as_admin(self):
        """A Client signed in as an admin account."""
        return self.client(role='admin')

    @contextmanager
    def context(self, user=None, *, role='admin'):
        """A business Context for Python-level tests (ctx.create, ctx.update, actions), with `user`'s permissions."""
        account = user if isinstance(user, User) else self.user(user, role=role)
        with self.business.context(account) as ctx:
            yield ctx

    # Lifetime

    def close(self):
        try:
            self.application.stop()
        finally:
            try:
                self.database.close()
            finally:
                self._remove()

    def _remove(self):
        if self._owned:
            shutil.rmtree(self.directory, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
