"""writes.file_lock tells contention apart from storage failures."""

import errno
import sys

import pytest

from evlet import writes



@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX advisory-lock error injection')
def test_file_lock_does_not_misclassify_unsupported_locking_as_busy(tmp_path, monkeypatch):
    import fcntl
    def unsupported(*args):
        raise OSError(errno.EOPNOTSUPP, 'no locks supported')
    monkeypatch.setattr(fcntl, 'flock', unsupported)
    with pytest.raises(OSError) as error, writes.file_lock(tmp_path / 'worker.lock'):
        pass
    assert error.value.errno == errno.EOPNOTSUPP
