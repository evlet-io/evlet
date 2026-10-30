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
from ..packages import resolve_packages
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


def install(root, name, *, app_name=None, trust=False, confirm=None):
    path = child(root, 'instances', name)
    if not path.is_dir():
        raise ValueError(f'Instance does not exist: {name}')
    with instance_guard(path):
        # Configuration is local trusted Python; downloaded apps are not imported yet.
        config = read_instance(path)
        recover(path)
        before = require_consistent(path)
        names = sorted(set(before['apps']) | ({app_name} if app_name else set()))
        if not names:
            raise ValueError('No installed apps to migrate')
        packages = resolve_packages(root, names)
        after = {'apps': {package.name: package.locked() for package in packages}}
        changed = [package.name for package in packages if before['apps'].get(package.name) != after['apps'][package.name]]
        if changed and not trust:
            if confirm is None or not confirm('Trust and execute these local Python apps: ' + ', '.join(changed) + '?'):
                raise ValueError('Installation requires explicit trust. Review the apps, then use --trust for non-interactive installation')
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
            applied = app.database.migrate(app.database.migrations)
            app.initialize()
            register_schedules(app, schedules)
            with app.database.transaction() as tx:
                tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_installed_apps (id INTEGER PRIMARY KEY CHECK(id=1), manifest TEXT NOT NULL)')
                tx.connection.execute('INSERT OR REPLACE INTO evlet_installed_apps VALUES (1,?)', (json_text(after),))
            app.database.close()
            # Detect source edits during registration before publishing the result.
            if {'apps': {package.name: package.locked() for package in packages}} != after:
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
        return {'apps': [package.name for package in packages], 'migrations': applied, 'backup': str(backup) if backup else None}
