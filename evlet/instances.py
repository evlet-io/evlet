"""Explicit app contributions and per-instance runtime configuration."""

from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
import importlib
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader, SourcelessFileLoader
import json
import re
import runpy
import sqlite3
import sys
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


@dataclass(frozen=True)
class Instance:
    name: str
    database: str = 'sqlite'
    storage: str = 'local'
    domain: str | None = None

    def __post_init__(self):
        from .cli.project import valid_name
        valid_name(self.name)
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

    def __post_init__(self):
        for name in ('models', 'views', 'children', 'attachments', 'migrations', 'tasks', 'schedules'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not callable(self.actions):
            object.__setattr__(self, 'actions', tuple(self.actions))
        object.__setattr__(self, 'permissions', dict(self.permissions))
        if any(not isinstance(schedule, Schedule) for schedule in self.schedules):
            raise ValueError('Register Schedule definitions')


def read_instance(path):
    if not path.is_dir() or not (path / 'config.py').is_file() or (path / 'config.py').is_symlink():
        raise ValueError(f'Instance does not exist or has invalid configuration: {path.name}')
    for relative in ('data', 'data/database.sqlite3', 'data/backups', 'files', 'files/private', 'apps.lock', '.evlet.lock'):
        target = path / relative
        if target.is_symlink() or not target.resolve().is_relative_to(path.resolve()):
            raise ValueError(f'Instance path cannot escape through a symbolic link: {relative}')
    value = runpy.run_path(str(path / 'config.py')).get('instance')
    if not isinstance(value, Instance) or value.name != path.name:
        raise ValueError('config.py must define instance = Instance(name=the_directory_name, ...)')
    return value


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
        self.paths = {package.name: package.path for package in packages}

    def find_spec(self, fullname, path=None, target=None):
        package_name = fullname.split('.')[0]
        root = self.paths.get(package_name)
        if root is None:
            package = sys.modules.get(package_name)
            loader = getattr(getattr(package, '__spec__', None), 'loader', None)
            if not isinstance(loader, _SourceLoader):
                return None
            root = Path(loader.path).parent.parent
        spec = PathFinder.find_spec(fullname, path if path is not None else [str(root)])
        if spec and isinstance(spec.loader, SourceFileLoader):
            spec.loader = _SourceLoader(fullname, spec.origin)
        elif spec and isinstance(spec.loader, SourcelessFileLoader):
            raise ImportError('Application imports require Python source files')
        return spec


# This finder retains no app paths. Loaded package specs identify its scope,
# including helpers first imported from an action or a background task.
_runtime_imports = _AppImports()


def load_definitions(packages):
    # All imports happen only after the caller has selected and trusted these apps.
    for package in packages:
        for name in list(sys.modules):
            if name == package.name or name.startswith(package.name + '.'):
                old = getattr(sys.modules[name], '__file__', None)
                if old and not Path(old).resolve().is_relative_to(package.path):
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
    definitions = load_definitions(packages)
    registry = Registry(*(model for _, definition in definitions for model in definition.models))
    database_path = database_path or path / 'data/database.sqlite3'
    migrations = migration_plan(definitions, database_path)
    database = Database(database_path, registry, migrations=migrations)
    if config.domain:
        origins, secure = ('https://' + config.domain,), True
    else:
        origins, secure = (f'http://127.0.0.1:{port}', f'http://localhost:{port}'), False
    auth = LocalAuth(database, origins=origins, secure_cookies=secure, allow_setup=not secure)
    rules, views, schedules = {}, ViewRegistry(), []
    for name, definition in definitions:
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
    policy = Policy(registry, rules=rules)
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
                      storage=storage, name=config.name.replace('_', ' ').title(), actions=actions, **options)
    return app, tuple(schedules)


def register_schedules(app, schedules):
    for schedule in schedules:
        if not isinstance(schedule, Schedule):
            raise ValueError('Register Schedule definitions')
        with app.database.transaction() as tx:
            row = tx.connection.execute('SELECT task,payload,interval FROM evlet_schedules WHERE name=?', (schedule.name,)).fetchone()
        if row:
            if (row['task'], json.loads(row['payload']), row['interval']) != (schedule.task, schedule.payload, schedule.every):
                raise ValueError(f'Schedule {schedule.name} changed; explicit schedule updates are not supported yet')
        else:
            app.business.jobs.schedule(schedule.name, schedule.task, schedule.payload, every=schedule.every)


def load_runtime(root, name, *, port=8000, development=False):
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
    # Startup verifies schema and history; it never silently applies app migrations.
    return app
