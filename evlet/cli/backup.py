"""evlet backup and evlet restore: an instance's four SQLite files as one set (docs/cli.md)."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from ..writes import STORE_OF, backup_members, check_file, copy_file, copy_set, data_files, files, require_space
from ..instances import database_state
from .project import child, instance_guard
from .install import publish_set, recover


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
    with instance_guard(path, serving=True):
        if (path / '.install-journal.json').exists():
            raise ValueError('Interrupted installation needs recovery before making a backup')
        return copy_set(main, target)


def restore(root, name, source):
    """Replace the instance's SQLite files with a backup set, with the server stopped.

    source is the main file of a set written by evlet backup or evlet migrate. An older single-file backup
    restores too: its sessions, logs and Eva are in that file, so the side files are cleared and the next
    evlet migrate moves them out. The current files are backed up first. Returns (safety backup, split pending)."""
    path, data = _instance(root, name)
    source = Path(source)
    members, single = backup_members(source)
    split_pending = bool(check_file(source)[1] & set(STORE_OF))
    main = data / 'database.sqlite3'
    with instance_guard(path):
        recover(path, restoring=True)
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
            pairs = list(zip(members, files(stage)))
            for old, new in pairs:
                if old.exists() and not (single and old != source):
                    copy_file(old, new)
            publish_set(path, stage, database_state(stage))
    return safety, split_pending
