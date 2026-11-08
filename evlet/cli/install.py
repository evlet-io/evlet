"""Stage installation, preserve a backup, and recover the database/lock boundary."""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from ..instances import (read_instance, read_lock, require_consistent, database_state,
                         build_instance, register_schedules)
from ..packages import resolve_packages, portable_lock_entry
from .project import child, instance_guard, atomic_json, json_text, sync_directory


def snapshot(source, target):
    with closing(sqlite3.connect(source)) as old, closing(sqlite3.connect(target)) as new:
        old.backup(new)
    os.chmod(target, 0o600)


def recover(path):
    journal = path / '.install-journal.json'
    if not journal.exists():
        return
    value = json.loads(journal.read_text())
    if not isinstance(value, dict) or set(value) != {'before', 'after'}:
        raise ValueError('Invalid installation recovery record; manual recovery is required')
    actual = database_state(path / 'data/database.sqlite3')
    if actual == value['after']:
        atomic_json(path / 'apps.lock', value['after'])
    elif actual == value['before']:
        atomic_json(path / 'apps.lock', value['before'])
    else:
        raise ValueError('Interrupted installation cannot be recovered automatically; database does not match either recorded state')
    journal.unlink()
    sync_directory(path)


def install(root, name, *, app_name=None, app_names=(), trust=False, confirm=None, recompute_posted=False, allow_destructive=False,
            allow_maintenance=False):
    path = child(root, 'instances', name)
    if not path.is_dir():
        raise ValueError(f'Instance does not exist: {name}')
    with instance_guard(path):
        # Configuration is local trusted Python; downloaded apps are not imported yet.
        config = read_instance(path)
        recover(path)
        before = require_consistent(path)
        names = sorted(set(before['apps']) | ({app_name} if app_name else set()) | set(app_names))
        if not names:
            raise ValueError('No installed apps to migrate')
        packages = resolve_packages(root, names, check_installed=False)
        from ..framework import state, upgrade
        after = {'apps': {package.name: package.locked() for package in packages}, 'evlet': state()}
        changed = [package.name for package in packages if package.name not in before['apps'] or portable_lock_entry(package.name, before['apps'][package.name]) != after['apps'][package.name]]
        if changed and not trust:
            if confirm is None or not confirm('Trust and execute these local Python apps: ' + ', '.join(changed) + '?'):
                raise ValueError('Installation requires explicit trust. Review the apps, then use --trust for non-interactive installation')
        from .app import prepare_dependencies
        packages = prepare_dependencies(root, names)
        if before == after:
            app, _ = build_instance(config, path, packages)
            try:
                app.initialize()
            finally:
                app.database.close()
            return {'apps': [package.name for package in packages], 'migrations': [], 'framework': [], 'backup': None}
        data = path / 'data'
        data.mkdir(mode=0o700, exist_ok=True)
        if data.is_symlink():
            raise ValueError('Instance data directory cannot be a symbolic link')
        database = data / 'database.sqlite3'
        if database.is_symlink():
            raise ValueError('Instance database cannot be a symbolic link')
        backup = None
        with tempfile.TemporaryDirectory(prefix='.install-', dir=data) as directory:
            stage = Path(directory) / 'database.sqlite3'
            if database.exists():
                backups = data / 'backups'
                backups.mkdir(mode=0o700, exist_ok=True)
                backup = backups / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
                snapshot(database, backup)
                snapshot(backup, stage)
            app, schedules = build_instance(config, path, packages, database_path=stage, storage_path=Path(directory) / 'private')
            # A backup of the current database is written above before any migration runs, including DropField.
            applied = app.database.migrate(app.database.migrations, recompute_posted=recompute_posted, allow_destructive=allow_destructive,
                                           allow_maintenance=allow_maintenance)
            # Framework steps run on the same staged copy, after the backup, before the additive startup tables.
            framework = upgrade(app.database)
            app.initialize()
            register_schedules(app, schedules)
            with app.database.transaction() as tx:
                tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_installed_apps (id INTEGER PRIMARY KEY CHECK(id=1), manifest TEXT NOT NULL)')
                tx.connection.execute('INSERT OR REPLACE INTO evlet_installed_apps VALUES (1,?)', (json_text(after),))
            app.database.close()
            # Detect source edits during registration before publishing the result.
            if {'apps': {package.name: package.locked() for package in packages}, 'evlet': state()} != after:
                raise ValueError('App sources changed during installation; retry with stable sources')
            with closing(sqlite3.connect(stage)) as connection:
                connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                connection.execute('PRAGMA journal_mode=DELETE')
            os.chmod(stage, 0o600)
            with stage.open('rb') as stream:
                os.fsync(stream.fileno())
            if database.exists():
                with closing(sqlite3.connect(database)) as connection:
                    busy, _, _ = connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                    if busy:
                        raise ValueError('Database is busy; stop all writers before installation')
                    connection.execute('PRAGMA journal_mode=DELETE').fetchone()
            atomic_json(path / '.install-journal.json', {'before': before, 'after': after})
            os.replace(stage, database)
            sync_directory(data)
            atomic_json(path / 'apps.lock', after)
            (path / '.install-journal.json').unlink()
            sync_directory(path)
        return {'apps': [package.name for package in packages], 'migrations': applied, 'framework': framework, 'backup': str(backup) if backup else None,
                'posted': getattr(applied, 'posted', 0)}


def plan(root, name, *, trust=False, confirm=None, label='changes'):
    """What `migrate` would do to an instance, worked out on a copy of its database.

    Returns (MigrationPlan, {app: (suggested migration name, Python or '')}, packages, pending framework
    steps). Nothing in the
    instance is written: the apps are built against a temporary copy that is deleted afterwards."""
    path = child(root, 'instances', name)
    if not path.is_dir():
        raise ValueError(f'Instance does not exist: {name}')
    with instance_guard(path):
        config = read_instance(path)
        if (path / '.install-journal.json').exists():
            raise ValueError('An interrupted installation needs recovery first; run evlet migrate')
        before = require_consistent(path)
        names = sorted(before['apps'])
        if not names:
            raise ValueError('No installed apps to migrate')
        packages = resolve_packages(root, names, check_installed=False)
        changed = [package.name for package in packages
                   if portable_lock_entry(package.name, before['apps'][package.name]) != package.locked()]
        if changed and not trust:
            if confirm is None or not confirm('Trust and execute these local Python apps: ' + ', '.join(changed) + '?'):
                raise ValueError('Planning imports the apps and requires explicit trust. Review the apps, then use --trust')
        from .app import prepare_dependencies
        packages = prepare_dependencies(root, names)
        database = path / 'data/database.sqlite3'
        with tempfile.TemporaryDirectory(prefix='evlet-plan-') as directory:
            stage = Path(directory) / 'database.sqlite3'
            if database.exists():
                snapshot(database, stage)
            app, _ = build_instance(config, path, packages, database_path=stage, storage_path=Path(directory) / 'private')
            try:
                result = app.database.plan(app.database.migrations)
                from ..framework import pending
                steps = pending(app.database)
            finally:
                app.database.close()
        from ..migrations import suggest
        suggestions = {}
        for package in packages:
            prefix = f'app{len(package.name)}_{package.name}__'
            number = sum(1 for migration in app.database.migrations if migration.name.startswith(prefix)) + 1
            tables = {table for table, model in app.registry.models.items()
                      if getattr(model, '__resource_id__', '').startswith(package.name + '.')}
            migration = f'{number:04d}_{label}'
            code = suggest(app.registry, result.planned, migration, tables) if result.differences and result.planned and tables else ''
            suggestions[package.name] = (migration, code)
        return result, suggestions, packages, steps


def makemigration(root, app_name, instance, *, name='changes', trust=False, confirm=None):
    """Write the migration `plan` suggests for one flat app into its migrations/ folder.

    Returns (file, whether it needs editing before migrate)."""
    import re
    if not re.fullmatch('[a-z][a-z0-9_]{0,40}', name):
        raise ValueError('Use lowercase letters, digits and underscores for the migration name')
    result, suggestions, packages, _ = plan(root, instance, trust=trust, confirm=confirm, label=name)
    package = next((item for item in packages if item.name == app_name), None)
    if package is None:
        raise ValueError(f'{app_name} is not installed on {instance}')
    migration, code = suggestions[app_name]
    if not code:
        raise ValueError(f'{app_name}: the migrations already produce the current models')
    if package.layout != 'flat':
        raise ValueError(f'{app_name} lists its migrations in migrations/__init__.py; add the suggested migration there '
                         f'(evlet migrate {instance} --plan prints it)')
    folder = package.source_root / 'migrations'
    if (package.source_root / 'migrations.py').exists():
        raise ValueError(f'{app_name} keeps its migrations in migrations.py; add the suggested migration there')
    target = folder / f'm{migration}.py'
    if target.exists():
        raise FileExistsError(f'{target} already exists')
    folder.mkdir(exist_ok=True)
    if not (folder / '__init__.py').exists():
        (folder / '__init__.py').write_text('')
    target.write_text(f'"""{migration}: written by evlet makemigration. Review it, then keep it unchanged once applied."""\n\n' + code)
    # True when the file has placeholders (...) or notes for changes it could not write.
    return target, '...' in code or '\n# ' in code
