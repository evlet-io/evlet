"""Exercise actual cross-process file locks on POSIX and Windows."""

from pathlib import Path
import subprocess
import sys

import pytest

from evlet.cli.project import instance_guard


def lock_in_child(path, serving):
    code = '''
import sys
from pathlib import Path
from evlet.cli.project import instance_guard
try:
    with instance_guard(Path(sys.argv[1]), serving=sys.argv[2] == 'True'):
        print('acquired')
except ValueError:
    print('busy')
'''
    result = subprocess.run([sys.executable, '-c', code, str(path), str(serving)], check=True, capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


def test_server_locks_share_but_block_lifecycle_changes(tmp_path):
    with instance_guard(tmp_path, serving=True):
        assert lock_in_child(tmp_path, True) == 'acquired'
        assert lock_in_child(tmp_path, False) == 'busy'
    assert lock_in_child(tmp_path, False) == 'acquired'


def test_lifecycle_lock_excludes_servers_and_other_commands(tmp_path):
    with instance_guard(tmp_path):
        assert lock_in_child(tmp_path, True) == 'busy'
        assert lock_in_child(tmp_path, False) == 'busy'
    assert lock_in_child(tmp_path, True) == 'acquired'


def test_repeated_locking_does_not_grow_lock_file(tmp_path):
    for _ in range(20):
        with instance_guard(tmp_path):
            pass
    assert (tmp_path/'.evlet.lock').stat().st_size <= 1
