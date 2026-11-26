"""evlet backup and evlet restore: an instance's four SQLite files as one set (docs/cli.md)."""

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
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


def backup(root, name, directory=None, *, with_files=False):
    """Write the instance's four SQLite files as one set into data/backups (or directory). Returns their paths.

    with_files=True also writes `<time>-files.tar.gz`, the instance's files/ folder (uploaded attachments) and its
    sites/ folder, as the last path. Without it, an instance with sites gets `<time>-sites.tar.gz`, its sites/ folder
    alone: the database names their current releases (docs/sites.md). secret.key is never included: keep it apart
    from the database copies (docs/cli.md#back-up-and-restore)."""
    path, data = _instance(root, name)
    main = data / 'database.sqlite3'
    if not main.is_file():
        raise ValueError(f'Instance {name} has no database yet')
    target_dir = Path(directory) if directory else data / 'backups'
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    size = sum(item.stat().st_size for item in files(main) if item.exists())
    target = target_dir / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
    sites = path / 'sites'
    with_sites = sites.is_dir() and not sites.is_symlink() and any(sites.iterdir())
    folders = [name for name, wanted in (('files', with_files), ('sites', with_sites)) if wanted]
    for name in folders:
        size += sum(item.stat().st_size for item in (path / name).rglob('*') if item.is_file() and not item.is_symlink())
    require_space(target_dir, size + 16 * 2**20, 'write the backup')
    with instance_guard(path, serving=True):
        if (path / '.install-journal.json').exists():
            raise ValueError('Interrupted installation needs recovery before making a backup')
        written = copy_set(main, target)
        if folders:
            suffix = '-files.tar.gz' if with_files else '-sites.tar.gz'
            written = [*written, _files_archive(path, folders, target.with_name(target.stem + suffix))]
        return written


def _files_archive(path, folders, archive):
    """A gzip tar of the instance folders named (files, sites), readable by its owner only. Symbolic links are left out."""
    import os
    import tarfile
    fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as raw, tarfile.open(fileobj=raw, mode='w:gz') as tar:
        for name in folders:
            folder = path / name
            if folder.is_dir() and not folder.is_symlink():
                tar.add(folder, arcname=name, filter=lambda info: None if info.issym() or info.islnk() else info)
    return archive


def _safety_copy(main, backups):
    """Back up the live set before a restore replaces it. A damaged or incomplete live set, which is often why
    someone restores, cannot pass the backup checks, so its files are kept as they are instead."""
    backups.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        return copy_set(main, backups / (stamp + '-before-restore.sqlite3'))[0]
    except (ValueError, sqlite3.DatabaseError):
        raw = backups / (stamp + '-before-restore-files')
        raw.mkdir(mode=0o700)
        for member in files(main):
            for item in (member, member.with_name(member.name + '-wal')):
                if item.is_file() and not item.is_symlink():
                    shutil.copy2(item, raw / item.name)
        return raw


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
            safety = _safety_copy(main, data / 'backups')
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=data) as directory:
            stage = Path(directory) / 'database.sqlite3'
            pairs = list(zip(members, files(stage)))
            for old, new in pairs:
                if old.exists() and not (single and old != source):
                    copy_file(old, new)
            publish_set(path, stage, database_state(stage))
    return safety, split_pending
