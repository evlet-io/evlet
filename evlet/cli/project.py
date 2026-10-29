"""Small project directories and atomic local metadata writes."""

from contextlib import contextmanager
import json
import keyword
import os
from pathlib import Path
import re
import tempfile


def valid_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', value) or keyword.iskeyword(value):
        raise ValueError('Use a lowercase name starting with a letter, with at most 48 letters, digits or underscores')
    return value


def project_root(path=None):
    start = Path(path or Path.cwd()).resolve()
    for candidate in (start,) if path else (start, *start.parents):
        if all((candidate / name).is_dir() and not (candidate / name).is_symlink() for name in ('apps', 'instances')):
            return candidate
    raise ValueError('No Evlet project found. Run evlet setup NAME or pass --project PATH')


def child(root, category, name):
    target = root / category / valid_name(name)
    if target.is_symlink() or target.resolve().parent != (root / category).resolve():
        raise ValueError(f'{category}/{name} must be inside this project and cannot be a symbolic link')
    return target


def json_text(value):
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'


def sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json_text(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def instance_guard(path, *, serving=False):
    """Cooperate across CLI processes; never swap a database under a running server."""
    lock = path / '.evlet.lock'
    if lock.is_symlink():
        raise ValueError('Instance lock cannot be a symbolic link')
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, (fcntl.LOCK_SH if serving else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Instance is in use. Stop its server or other lifecycle command before changing it') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
