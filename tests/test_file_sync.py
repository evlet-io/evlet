"""Files are flushed to disk through a descriptor with write access, as Windows requires (evlet/writes.py sync_file).

Windows flushes a file (FlushFileBuffers) only through a handle opened for writing; an fsync on a read-only
handle fails there. On POSIX every fsync is checked to use a writable descriptor, so a regression shows on Linux;
on Windows the real fsync shows it.
"""

from contextlib import closing
import errno
import json
import os
import sqlite3
import stat

import pytest

from evlet import Database, Registry
from evlet.cli.install import publish_set
from evlet.cli.upgrade import sync_environment
from evlet.instances import database_state
from evlet.writes import copy_set, files, sync_directory, sync_file


@pytest.fixture(autouse=True)
def writable_fsync(monkeypatch):
    """Refuse an fsync of a regular file opened read only, as Windows does."""
    synced = []
    try:
        import fcntl
    except ImportError:  # Windows: its own fsync refuses read-only handles.
        yield synced
        return
    original = os.fsync

    def fsync(fd):
        if not hasattr(fd, '__index__'):
            fd = fd.fileno()
        # Directories open read only everywhere; sync_directory skips them on Windows.
        if stat.S_ISREG(os.fstat(fd).st_mode) and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
            raise OSError(errno.EBADF, 'fsync needs write access on Windows')
        synced.append(fd)
        return original(fd)

    monkeypatch.setattr(os, 'fsync', fsync)
    yield synced


def database(path, manifest=None):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('CREATE TABLE evlet_installed_apps(id INTEGER PRIMARY KEY, manifest TEXT)')
        connection.execute('INSERT INTO evlet_installed_apps(id, manifest) VALUES (1, ?)', (json.dumps(manifest or {'apps': {}}),))
        connection.commit()


def test_the_check_refuses_a_read_only_fsync(tmp_path):
    path = tmp_path / 'file'
    path.write_bytes(b'data')
    with path.open('rb') as stream, pytest.raises(OSError):
        os.fsync(stream.fileno())


def test_sync_file_flushes_without_changing_the_file(tmp_path, writable_fsync):
    path = tmp_path / 'file.bin'
    path.write_bytes(b'\x00\r\n contents')
    sync_file(path)
    assert path.read_bytes() == b'\x00\r\n contents'
    assert writable_fsync or os.name == 'nt'
    sync_directory(tmp_path)  # A no-op on Windows, where directories cannot be flushed.


def test_backup_sets_flush_every_copied_file(tmp_path, writable_fsync):
    source = tmp_path / 'live' / 'database.sqlite3'
    source.parent.mkdir()
    live = Database(source, Registry())
    live.initialize()
    live.close()
    target = tmp_path / 'backup' / 'database.sqlite3'
    target.parent.mkdir()
    copied = copy_set(source, target)
    assert len(copied) == len(files(source)) and all(path.is_file() for path in copied)
    assert len(writable_fsync) > len(copied) or os.name == 'nt'  # Every copy, then the manifest.


def test_installation_publish_flushes_staged_and_live_files(tmp_path):
    instance = tmp_path / 'instance'
    (instance / 'data').mkdir(parents=True)
    stage = tmp_path / 'stage' / 'database.sqlite3'
    stage.parent.mkdir()
    after = {'apps': {'example': {'version': '1'}}}
    database(stage, after)
    publish_set(instance, stage, after)
    assert database_state(files(instance / 'data' / 'database.sqlite3')[0]) == after
    assert json.loads((instance / 'apps.lock').read_text()) == after
    assert not (instance / '.install-journal.json').exists()


def test_upgrade_flushes_every_file_of_a_prepared_environment(tmp_path, writable_fsync):
    release = tmp_path / 'release'
    (release / 'lib' / 'package').mkdir(parents=True)
    (release / 'lib' / 'package' / 'module.py').write_text('value = 1\n')
    (release / 'pyvenv.cfg').write_text('home = /usr\n')
    sync_environment(release)
    assert (release / 'pyvenv.cfg').read_text() == 'home = /usr\n'
    assert len(writable_fsync) >= 2 or os.name == 'nt'
