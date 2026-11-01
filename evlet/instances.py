"""Explicit app contributions and per-instance runtime configuration."""

from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
import importlib
from importlib.util import spec_from_file_location
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader, SourcelessFileLoader
import json
import re
import runpy
import sqlite3
import sys
from zoneinfo import ZoneInfo
from types import SimpleNamespace

from .app import Application
from .database import Database
from .events import EventStore
from .jobs import JobQueue
from .auth import LocalAuth
from .storage import LocalFileStorage
from .migrations import Migration
from .permissions import Policy
from .models import Registry
from .ui import ViewRegistry
from .routing import resolve_routes


@dataclass(frozen=True)
class Instance:
    name: str
    database: str = 'sqlite'
    storage: str = 'local'
    domain: str | None = None
    timezone: str = 'UTC'
    # Reverse proxies whose X-Forwarded-For is believed, such as ("127.0.0.1", "::1"). Empty: none.
    trusted_proxies: tuple = ()

    def __post_init__(self):
        from .cli.project import valid_name
        from .auth import proxy_networks
        valid_name(self.name)
        object.__setattr__(self, 'trusted_proxies', proxy_networks(self.trusted_proxies))
        ZoneInfo(self.timezone)
        if self.database != 'sqlite':
            raise ValueError('The instance CLI supports isolated SQLite databases; use the existing PostgreSQL API for separately configured databases')
        if self.storage != 'local':
            raise ValueError('The instance CLI supports local private file storage')
        if self.domain is not None and (not isinstance(self.domain, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', self.domain) or '..' in self.domain):
            raise ValueError('Instance domain must be a lowercase hostname without a scheme, path or port')


@dataclass(frozen=True)
class Schedule:
    name: str
    task: str
    every: int
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AppDefinition:
    """Trusted Python registrations, composed using the existing framework APIs."""

    models: tuple = ()
    views: tuple = ()
    actions: object = ()
    children: tuple = ()
    attachments: tuple = ()
    permissions: dict = field(default_factory=dict)
    migrations: tuple = ()
    tasks: tuple = ()
    schedules: tuple = ()
    ui: object = None
    reports: tuple = ()
    apis: tuple = ()
    frontends: tuple = ()
    api_scopes: tuple = ()
    roles: dict = field(default_factory=dict)
    scopes: dict = field(default_factory=dict)

    def __post_init__(self):
        for name in ('models', 'views', 'children', 'attachments', 'migrations', 'tasks', 'schedules', 'reports', 'apis', 'frontends', 'api_scopes'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not callable(self.actions):
            object.__setattr__(self, 'actions', tuple(self.actions))
        object.__setattr__(self, 'permissions', dict(self.permissions))
        from .auth import PORTAL, RESERVED_ROLES, ROLE_ID, ROLES
        object.__setattr__(self, 'roles', dict(self.roles))
        for role, base in self.roles.items():
            if not isinstance(role, str) or not ROLE_ID.fullmatch(role) or role in ROLES or role in RESERVED_ROLES or base not in ('editor', 'viewer', PORTAL):
                raise ValueError(f'roles maps a new lowercase role id to its base, editor, viewer or portal: {role!r}')
        object.__setattr__(self, 'scopes', dict(self.scopes))
        if any(not isinstance(key, str) or not ROLE_ID.fullmatch(key) or not callable(scope) for key, scope in self.scopes.items()):
            raise ValueError('scopes maps lowercase names to callables returning query filters')
        from .services import CalendarSchedule
        if any(not isinstance(schedule, (Schedule, CalendarSchedule)) for schedule in self.schedules):
            raise ValueError('Register Schedule definitions')
        from .routing import ApiScope, Frontend
        if any(not isinstance(frontend, Frontend) for frontend in self.frontends):
            raise ValueError('Register Frontend definitions')
        if any(not isinstance(scope, ApiScope) for scope in self.api_scopes):
            raise ValueError('Register ApiScope definitions')


def read_instance(path):
    if not path.is_dir() or not (path / 'config.py').is_file() or (path / 'config.py').is_symlink():
        raise ValueError(f'Instance does not exist or has invalid configuration: {path.name}')
    for relative in ('data', 'data/database.sqlite3', 'data/backups', 'files', 'files/private', 'apps.lock', '.evlet.lock', 'routes.py'):
        target = path / relative
        if target.is_symlink() or not target.resolve().is_relative_to(path.resolve()):
            raise ValueError(f'Instance path cannot escape through a symbolic link: {relative}')
    value = runpy.run_path(str(path / 'config.py')).get('instance')
    if not isinstance(value, Instance) or value.name != path.name:
        raise ValueError('config.py must define instance = Instance(name=the_directory_name, ...)')
    return value


def read_routes(path):
    """Run the optional trusted instances/<name>/routes.py under the same checks as config.py."""
    target = path / 'routes.py'
    if not target.exists() and not target.is_symlink():
        return None
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(path.resolve()):
        raise ValueError(f'Instance routes must be a regular file: {path.name}/routes.py')
    return runpy.run_path(str(target))


def read_lock(path):
    try:
        value = json.loads((path / 'apps.lock').read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot read {path.name}/apps.lock: {exc}') from None
    if not isinstance(value, dict) or set(value) != {'apps'} or not isinstance(value['apps'], dict):
        raise ValueError('Invalid apps.lock; expected an apps object')
    from .cli.project import valid_name
    for name, entry in value['apps'].items():
        valid_name(name)
        if not isinstance(entry, dict) or set(entry) != {'version', 'source', 'commit', 'sha256'}:
            raise ValueError(f'Invalid lock entry for {name}')
        if any(not isinstance(entry[key], str) or not entry[key] for key in ('version', 'source', 'sha256')) or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError(f'Invalid lock metadata for {name}')
        if entry['commit'] is not None and not isinstance(entry['commit'], str):
            raise ValueError(f'Invalid revision for {name}')
    return value


def database_state(database):
    if not database.exists():
        return {'apps': {}}
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_installed_apps'").fetchone()
        if not exists:
            raise ValueError('Database is not managed by this instance installer; do not adopt existing customer databases implicitly')
        row = connection.execute('SELECT manifest FROM evlet_installed_apps WHERE id=1').fetchone()
        if not row:
            raise ValueError('Instance installation record is missing')
        return json.loads(row[0])


def require_consistent(path):
    if (path / '.install-journal.json').exists():
        raise ValueError('Interrupted installation detected. Run evlet migrate with this instance selected to recover')
    locked = read_lock(path)
    if database_state(path / 'data/database.sqlite3') != locked:
        raise ValueError('Database installation state differs from apps.lock; restore the matching lock or recover the interrupted installation')
    return locked


class _SourceLoader(SourceFileLoader):
    def get_code(self, fullname):
        # Application imports use Python source, not timestamp-based bytecode.
        return self.source_to_code(self.get_data(self.path), self.path)


class _AppImports(MetaPathFinder):
    def __init__(self, packages=()):
        self.paths = {package.name: package.source_root for package in packages}

    def find_spec(self, fullname, path=None, target=None):
        package_name = fullname.split('.')[0]
        root = self.paths.get(package_name)
        if root is None:
            package = sys.modules.get(package_name)
            loader = getattr(getattr(package, '__spec__', None), 'loader', None)
            if not isinstance(loader, _SourceLoader):
                return None
            root = Path(loader.path).parent
        if fullname == package_name:
            spec = spec_from_file_location(fullname, root / '__init__.py', submodule_search_locations=[str(root)])
        else:
            spec = PathFinder.find_spec(fullname, path)
        if spec is None:
            raise ModuleNotFoundError('Application imports require Python source files: ' + fullname, name=fullname)
        if spec and spec.origin and not Path(spec.origin).resolve().is_relative_to(root.resolve()):
            raise ImportError('Application imports cannot escape their package root')
        if spec and isinstance(spec.loader, SourceFileLoader):
            spec.loader = _SourceLoader(fullname, spec.origin)
        elif spec and isinstance(spec.loader, SourcelessFileLoader):
            raise ImportError('Application imports require Python source files')
        return spec


# This finder retains no app paths. Loaded package specs identify its scope,
# including helpers first imported from an action or a background task.
_runtime_imports = _AppImports()

from threading import RLock
from contextlib import contextmanager
_import_lock = RLock()
_active_sources = {}


@contextmanager
def activate_generation(app):
    packages = getattr(app, '_source_packages', ())
    with _import_lock:
        for package in packages:
            active = _active_sources.get(package.name)
            if active and active[0] != package.source_root:
                raise ValueError('Another generation is active; stop its runtime before loading ' + package.name)
        for package in packages:
            path, count = _active_sources.get(package.name, (package.source_root, 0))
            _active_sources[package.name] = (path, count + 1)
    try: yield
    finally:
        with _import_lock:
            for package in packages:
                path, count = _active_sources[package.name]
                if count == 1: del _active_sources[package.name]
                else: _active_sources[package.name] = (path, count - 1)


def load_definitions(packages, *, modules=None):
    with _import_lock:
        for package in packages:
            active = _active_sources.get(package.name)
            if active and active[0] != package.source_root:
                raise ValueError('Another generation is active; stop its runtime before loading ' + package.name)
        return _load_definitions(packages, modules=modules)


def _load_definitions(packages, *, modules=None):
    # All imports happen only after the caller has selected and trusted these apps.
    for package in packages:
        for name in list(sys.modules):
            if name == package.name or name.startswith(package.name + '.'):
                old = getattr(sys.modules[name], '__file__', None)
                loader = getattr(getattr(sys.modules[name], '__spec__', None), 'loader', None)
                if old and Path(old).resolve().is_relative_to(package.path) and isinstance(loader, _SourceLoader):
                    continue
                if old and not Path(old).resolve().is_relative_to(package.path) and not isinstance(loader, _SourceLoader):
                    raise ValueError(f'App {package.name} conflicts with an already imported Python package')
                del sys.modules[name]
    importlib.invalidate_caches()
    if _runtime_imports not in sys.meta_path:
        sys.meta_path.insert(0, _runtime_imports)
    finder = _AppImports(packages)
    sys.meta_path.insert(0, finder)
    try:
        definitions = []
        for package in packages:
            if package.layout == 'flat':
                from .builds import discover
                definitions.append((package.name, discover(package, None if modules is None else modules[package.name])))
                continue
            module = importlib.import_module(package.name + '.hooks')
            if not Path(module.__file__).resolve().is_relative_to(package.path):
                raise ValueError(f'Loaded {package.name} from the wrong source directory')
            definition = getattr(module, 'app', None)
            if not isinstance(definition, AppDefinition):
                raise ValueError(f'{package.name}.hooks must export app = AppDefinition(...)')
            definitions.append((package.name, definition))
        return definitions
    finally:
        sys.meta_path.remove(finder)


def migration_plan(definitions, database):
    available = {}
    for name, definition in definitions:
        for migration in definition.migrations:
            if not isinstance(migration, Migration):
                raise ValueError(f'{name} must register Migration objects')
            key = f'app{len(name)}_{name}__' + migration.name
            if key in available:
                raise ValueError(f'Duplicate migration {key}')
            available[key] = Migration(key, *migration.operations)
    existing = []
    if database.exists():
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_migrations'").fetchone():
                existing = [row[0] for row in connection.execute('SELECT name FROM evlet_migrations ORDER BY position')]
    if set(existing) - available.keys():
        raise ValueError('An installed app removed migration history')
    # Interleaved releases of several apps retain the database's applied prefix.
    for name, definition in definitions:
        prefix = f'app{len(name)}_{name}__'
        declared = [prefix + item.name for item in definition.migrations]
        applied = [key for key in existing if key.startswith(prefix)]
        if declared[:len(applied)] != applied:
            raise ValueError(f'{name} changed the order of applied migrations')
    return tuple(available[key] for key in (*existing, *(key for key in available if key not in existing)))


def build_instance(config, path, packages, *, port=8000, database_path=None, storage_path=None):
    from .builds import prepare
    pinned, manifest = prepare(path.parent.parent, packages)
    definitions = load_definitions(pinned, modules=manifest['modules'])
    registry = Registry(*(model for _, definition in definitions for model in definition.models))
    registry._freeze_ui = True
    database_path = database_path or path / 'data/database.sqlite3'
    migrations = migration_plan(definitions, database_path)
    database = Database(database_path, registry, migrations=migrations)
    routes = resolve_routes(definitions, config, read_routes(path), port=port, roots={package.name: package.source_root for package in pinned})
    secure = routes.primary is not None
    auth = LocalAuth(database, origins=routes.origins(), realms=routes.realms(), secure_cookies=secure, allow_setup=not secure,
                     trusted_proxies=config.trusted_proxies)
    rules, views, schedules, roles = {}, ViewRegistry(), [], {role: role for role in ('admin', 'editor', 'viewer', 'portal')}
    for name, definition in definitions:
        for role, base in definition.roles.items():
            if roles.setdefault(role, base) != base:
                raise ValueError(f'Two apps declare role {role} with different bases')
        if rules.keys() & definition.permissions.keys():
            raise ValueError('Two apps registered the same permission rule')
        rules.update(definition.permissions)
        if definition.ui is not None:
            if not isinstance(definition.ui, ViewRegistry):
                raise ValueError(f'{name}.ui must be a ViewRegistry')
            for view in definition.ui.views.values():
                views.register(view)
            for target, layer, callback, extension in definition.ui.extensions:
                views.extend(target, callback, layer=layer, name=name + '.' + extension)
            # Custom renderers must already be included in the framework build.
            for component in definition.ui.components.catalog():
                existing = {item['name']: item for item in views.components.catalog()}
                if component['name'] not in existing:
                    views.components.register(definition.ui.components.get(component['name']))
                elif existing[component['name']] != component:
                    raise ValueError(f'Conflicting component contract: {component["name"]}')
        for view in definition.views:
            views.register(view)
        schedules.extend(definition.schedules)
    policy = Policy(registry, rules=rules, roles=roles)
    events = EventStore(database)
    jobs = JobQueue(database, [task for _, definition in definitions for task in definition.tasks], tenant=config.name.replace('_', '-'), events=events, policy=policy)
    if len({schedule.name for schedule in schedules}) != len(schedules):
        raise ValueError('Duplicate schedule names across apps')
    storage = LocalFileStorage(storage_path or path / 'files/private')
    services = SimpleNamespace(database=database, auth=auth, policy=policy, jobs=jobs, events=events, storage=storage)
    actions = []
    for _, definition in definitions:
        actions.extend(definition.actions(services) if callable(definition.actions) else definition.actions)
    options = {key: tuple(item for _, definition in definitions for item in getattr(definition, key)) for key in ('children', 'attachments')}
    app = Application(database, auth=auth, policy=policy, ui=views, events=events, jobs=jobs,
                      reports=tuple(report for _, definition in definitions for report in definition.reports),
                      apis=tuple(endpoint for _, definition in definitions for endpoint in definition.apis),
                      storage=storage, name=config.name.replace('_', ' ').title(), actions=actions, routes=routes, **options)
    app.business.timezone = config.timezone
    app.business.controls.load(definitions, pinned)
    app.business.build_manifest = manifest
    app._source_packages = pinned
    app.business.build_directory = path.parent.parent / '.evlet' / 'builds' / manifest['fingerprint']
    return app, tuple(schedules)


def register_schedules(app, schedules):
    from .services import Calendar, CalendarSchedule
    import time
    calendar_names = {schedule.name for schedule in schedules if isinstance(schedule, CalendarSchedule)}
    with app.database.transaction() as tx:
        for row in tx.connection.execute('SELECT name FROM evlet_calendar_schedules').fetchall():
            if row['name'] not in calendar_names:
                # Only an active schedule is retired, so an operator-disabled one stays disabled when restored.
                tx.connection.execute('UPDATE evlet_calendar_schedules SET retired=1 WHERE name=? AND retired=0 AND EXISTS (SELECT 1 FROM evlet_schedules WHERE name=? AND enabled=1)', (row['name'], row['name']))
                tx.connection.execute('UPDATE evlet_schedules SET enabled=0 WHERE name=?', (row['name'],))
    for schedule in schedules:
        if isinstance(schedule, CalendarSchedule):
            zone = schedule.timezone or getattr(app.business, 'timezone', 'UTC')
            calendar = Calendar(schedule.expression, zone)
            with app.database.transaction() as tx:
                old = tx.connection.execute('SELECT * FROM evlet_calendar_schedules WHERE name=?', (schedule.name,)).fetchone()
                existing = tx.connection.execute('SELECT name FROM evlet_schedules WHERE name=?', (schedule.name,)).fetchone()
            if not existing:
                app.business.jobs.schedule(schedule.name, schedule.task, {}, every=60, start_at=calendar.next(int(time.time())),
                                           calendar=(schedule.expression, zone))
                continue
            if old is None:
                raise ValueError('Calendar schedule collides with an interval schedule')
            with app.database.transaction() as tx:
                if old['retired']:
                    tx.connection.execute('UPDATE evlet_schedules SET enabled=1 WHERE name=?', (schedule.name,))
                if old['retired'] or (old['expression'], old['timezone']) != (schedule.expression, zone):
                    tx.connection.execute('UPDATE evlet_calendar_schedules SET expression=?, timezone=?, retired=0 WHERE name=?', (schedule.expression, zone, schedule.name))
                    tx.connection.execute('UPDATE evlet_schedules SET next_run=? WHERE name=?', (calendar.next(int(time.time())), schedule.name))
                tx.connection.execute('UPDATE evlet_schedules SET task=? WHERE name=? AND task<>?', (schedule.task, schedule.name, schedule.task))
            continue
        if not isinstance(schedule, Schedule):
            raise ValueError('Register Schedule definitions')
        with app.database.transaction() as tx:
            row = tx.connection.execute('SELECT task,payload,interval FROM evlet_schedules WHERE name=?', (schedule.name,)).fetchone()
        if row:
            if (row['task'], json.loads(row['payload']), row['interval']) != (schedule.task, schedule.payload, schedule.every):
                raise ValueError(f'Schedule {schedule.name} changed; explicit schedule updates are not supported yet')
        else:
            app.business.jobs.schedule(schedule.name, schedule.task, schedule.payload, every=schedule.every)


def load_runtime(root, name, *, port=8000, development=False, public=False):
    from .cli.project import child
    from .packages import resolve_packages, portable_lock_entry
    path = child(root, 'instances', name)
    config = read_instance(path)
    locked = require_consistent(path)
    if not locked['apps']:
        raise ValueError('Instance has no installed apps. Run evlet install APP --instance ' + name)
    packages = resolve_packages(root, sorted(locked['apps']))
    if {package.name for package in packages} != set(locked['apps']):
        raise ValueError('App dependencies changed; run evlet migrate before serving')
    for package in packages:
        current = package.locked()
        expected = portable_lock_entry(package.name, locked['apps'][package.name])
        if current != expected and (not development or any(current[key] != expected[key] for key in ('version', 'source', 'commit'))):
            raise ValueError(f'{package.name} differs from apps.lock; stop serving and run evlet migrate {name}')
    app, _ = build_instance(config, path, packages, port=port)
    if development and app.routes.primary:
        raise ValueError('Development serving requires a local instance and loopback host')
    if public and not app.routes.primary:
        raise ValueError('Configure an HTTPS domain before binding an instance beyond loopback')
    # Startup verifies schema and history; it never silently applies app migrations.
    return app
