"""Worker ownership survives stale metadata and distinguishes contention from storage failures."""

import errno
from pathlib import Path
import subprocess
import sys

import pytest

from evlet import Database, Registry, writes


def test_worker_lease_tracks_real_process_lifetime_and_uses_bounded_slot_files(tmp_path):
    main = tmp_path / 'database.sqlite3'
    db = Database(main, Registry())
    child = subprocess.Popen([sys.executable, '-c', '''
import sys
from evlet import Database, Registry
db=Database(sys.argv[1], Registry())
with db.worker_lease('eva-0') as acquired:
    print(acquired, flush=True)
    sys.stdin.readline()
''', str(main)], text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip() == 'True'
        for _ in range(3):
            with db.worker_lease('eva-0') as acquired:
                assert acquired is False
        with db.worker_lease('eva-1') as acquired:
            assert acquired is True
        other = Database(tmp_path / 'other.sqlite3', Registry())
        with other.worker_lease('eva-0') as acquired:
            assert acquired is True
        other.close()
    finally:
        child.kill()  # The OS releases ownership even when worker cleanup cannot run.
        child.wait(timeout=5)
    for _ in range(3):
        with db.worker_lease('eva-0') as acquired:
            assert acquired is True
    assert len(list((tmp_path / '.worker-leases').iterdir())) == 3
    db.close()


def test_worker_lease_releases_after_exception_and_rejects_unsafe_directory(tmp_path):
    db = Database(tmp_path / 'database.sqlite3', Registry())
    with pytest.raises(RuntimeError), db.worker_lease('eva-0') as acquired:
        assert acquired
        raise RuntimeError('worker failed')
    with db.worker_lease('eva-0') as acquired:
        assert acquired
    for invalid in ('', None, 'x' * 129):
        with pytest.raises(ValueError), db.worker_lease(invalid):
            pass
    redirected = tmp_path / 'redirected'
    redirected.mkdir()
    (redirected / '.worker-leases').symlink_to(tmp_path / '.worker-leases', target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic link'), Database(redirected / 'database.sqlite3', Registry()).worker_lease('eva-0'):
        pass
    db.close()


def test_worker_lease_propagates_filesystem_errors_instead_of_claiming_contention(tmp_path, monkeypatch):
    db = Database(tmp_path / 'database.sqlite3', Registry())
    original = writes.os.open
    def broken(path, flags, *args, **values):
        if Path(path).suffix == '.lock':
            raise OSError(errno.ENOSPC, 'disk full')
        return original(path, flags, *args, **values)
    monkeypatch.setattr(writes.os, 'open', broken)
    with pytest.raises(OSError) as error, db.worker_lease('eva-0'):
        pass
    assert error.value.errno == errno.ENOSPC
    db.close()


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX advisory-lock error injection')
def test_file_lock_does_not_misclassify_unsupported_locking_as_busy(tmp_path, monkeypatch):
    import fcntl
    def unsupported(*args):
        raise OSError(errno.EOPNOTSUPP, 'no locks supported')
    monkeypatch.setattr(fcntl, 'flock', unsupported)
    with pytest.raises(OSError) as error, writes.file_lock(tmp_path / 'worker.lock'):
        pass
    assert error.value.errno == errno.EOPNOTSUPP
