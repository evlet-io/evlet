"""evlet backup and evlet restore: an instance's four SQLite files as one set (docs/cli.md)."""

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

from ..writes import STORES, check_file, copy_file, copy_set, data_files, files, release_idle, require_space, settle, side_path
from ..instances import database_state
from .project import atomic_json, child, instance_guard, sync_directory


def _instance(root, name):
    path = child(root, 'instances', name)
    if not path.is_dir() or not (path / 'config.py').is_file():
        raise ValueError(f'Instance does not exist: {name}')
    data = path / 'data'
    if data.is_symlink() or any(item.is_symlink() for item in data_files(path)):
        raise ValueError('Instance data files cannot be symbolic links')
    return path, data


def backup(root, name, directory=None):
    """Write the instance's four SQLite files as one set into data/backups (or directory). Returns their paths."""
    path, data = _instance(root, name)
    main = data / 'database.sqlite3'
    if not main.is_file():
        raise ValueError(f'Instance {name} has no database yet')
    target_dir = Path(directory) if directory else data / 'backups'
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    size = sum(item.stat().st_size for item in files(main) if item.exists())
    require_space(target_dir, size + 16 * 2**20, 'write the backup')
    target = target_dir / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
    return copy_set(main, target)


def restore(root, name, source):
    """Replace the instance's SQLite files with a backup set, with the server stopped.

    source is the main file of a set written by evlet backup or evlet migrate. An older single-file backup
    restores too: its sessions, logs and Eva are in that file, so the side files are cleared and the next
    evlet migrate moves them out. The current files are backed up first. Returns (safety backup, split pending)."""
    path, data = _instance(root, name)
    source = Path(source)
    if not source.is_file() or source.is_symlink():
        raise ValueError(f'No backup file at {source}')
    problem, moved = check_file(source)
    if problem:
        raise ValueError(f'The backup {source.name} is damaged: {problem}')
    single = bool(moved & {table for tables in STORES.values() for table in tables})
    main = data / 'database.sqlite3'
    with instance_guard(path):
        safety = None
        size = sum(item.stat().st_size for item in files(source) if item.exists())
        current = sum(item.stat().st_size for item in files(main) if item.exists())
        require_space(data, 2 * size + current + 16 * 2**20, 'restore this backup')
        if main.exists():
            backups = data / 'backups'
            backups.mkdir(mode=0o700, exist_ok=True)
            safety = copy_set(main, backups / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-before-restore.sqlite3'))[0]
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=data) as directory:
            stage = Path(directory) / 'database.sqlite3'
            pairs = list(zip(files(source), files(stage)))
            for old, new in pairs:
                if old.exists() and not (single and old != source):
                    copy_file(old, new)
            release_idle()
            for item in files(main):
                if item.exists():
                    settle(item)
            # Side files first, then the main file: the set is in place once the main file is.
            for store in STORES:
                staged, live = side_path(stage, store), side_path(main, store)
                if staged.exists():
                    os.replace(staged, live)
                else:
                    for leftover in (live, live.with_name(live.name + '-wal'), live.with_name(live.name + '-shm')):
                        leftover.unlink(missing_ok=True)
            os.replace(stage, main)
            sync_directory(data)
        # apps.lock follows the restored database's own installation record, so evlet migrate can bring an older
        # backup up to the installed apps and Evlet.
        state = database_state(main)
        if 'evlet' in state or state.get('apps'):
            atomic_json(path / 'apps.lock', state)
            sync_directory(path)
    return safety, single
