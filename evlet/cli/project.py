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
    with os.fdopen(fd, 'r+b') as stream:
        try:
            if os.name == 'nt':
                unlock = _windows_lock(stream, exclusive=not serving)
            else:
                import fcntl
                fcntl.flock(stream, (fcntl.LOCK_SH if serving else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Instance is in use. Stop its server or other lifecycle command before changing it') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                unlock()
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _windows_lock(stream, *, exclusive):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                    ('hEvent', wintypes.HANDLE)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(stream.fileno())
    overlapped = Overlapped()
    if not kernel.LockFileEx(handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())
    def unlock():
        if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())
    return unlock
