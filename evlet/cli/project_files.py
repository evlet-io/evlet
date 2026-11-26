"""The project files the develop tools may touch (docs/project-endpoint.md#develop-tools).

Only files inside apps/, and the config.py and routes.py of an instance. Never .venv, .evlet, an instance's data/,
secret.key or control.key, and never a link: a path is relative, uses / between parts, names no part that starts
with a dot (.git, .env) and no part that is a link on disk. Files are UTF-8 text of at most FILE_BYTES.
"""

import os
from pathlib import Path, PurePosixPath
import re
import stat

FILE_BYTES = 1024 * 1024
LIST_LIMIT = 2000
INSTANCE_FILES = ('config.py', 'routes.py')
NEVER = {'secret.key', 'control.key', '__pycache__'}
PART = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.+-]{0,127}')


def inside(root, relative, *, roots=('apps', 'instances')):
    """The absolute path of relative, a project path the develop tools may use, or ValueError saying why not."""
    root = Path(root)
    if not isinstance(relative, str) or not relative or len(relative) > 500 or '\\' in relative or '\0' in relative:
        raise ValueError('Name a path relative to the project, such as apps/notes/notes/models/__init__.py')
    pure = PurePosixPath(relative)
    parts = pure.parts
    if pure.is_absolute() or str(pure) != relative or any(not PART.fullmatch(part) for part in parts):
        raise ValueError(f'{relative}: use a relative path whose parts are letters, digits and _ . + - and start '
                         'with no dot; .venv, .evlet, .git and other dot folders are not reachable')
    if parts[0] not in roots or any(part in NEVER for part in parts):
        raise ValueError(f'{relative}: the develop tools reach files in apps/ and the config.py and routes.py of an instance')
    if parts[0] == 'instances' and (len(parts) != 3 or parts[2] not in INSTANCE_FILES):
        raise ValueError(f'{relative}: in an instance only config.py and routes.py are reachable')
    path = root
    for part in parts:
        path = path / part
        try:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                raise ValueError(f'{relative}: {path.relative_to(root)} is a link; links are not followed')
        except FileNotFoundError:
            break
    if parts[0] == 'instances' and not (root / 'instances' / parts[1] / 'config.py').is_file():
        raise ValueError(f'There is no instance named {parts[1]}')
    return path


def listing(root, relative='apps'):
    """The files and folders under relative (apps/ by default; 'instances' lists each instance's two files)."""
    root = Path(root)
    if relative == 'instances':
        from .endpoint import instance_names
        found = [f'instances/{name}/{item}' for name in instance_names(root) for item in INSTANCE_FILES
                 if (root / 'instances' / name / item).is_file()]
        return {'path': relative, 'files': found, 'folders': [], 'truncated': False}
    top = inside(root, relative, roots=('apps',))
    if not top.is_dir():
        raise ValueError(f'{relative} is not a folder')
    files, folders = [], []
    for current, names, filenames in os.walk(top, followlinks=False):
        names[:] = sorted(name for name in names if PART.fullmatch(name) and name not in NEVER
                          and not os.path.islink(os.path.join(current, name)))
        base = Path(current).relative_to(root).as_posix()
        folders += [f'{base}/{name}' for name in names]
        files += [f'{base}/{name}' for name in sorted(filenames) if PART.fullmatch(name) and name not in NEVER
                  and not os.path.islink(os.path.join(current, name))]
        if len(files) + len(folders) > LIST_LIMIT:
            return {'path': relative, 'files': files[:LIST_LIMIT], 'folders': folders, 'truncated': True}
    return {'path': relative, 'files': files, 'folders': folders, 'truncated': False}


def read(root, relative):
    path = inside(root, relative)
    if not path.is_file():
        raise ValueError(f'{relative} is not a file')
    size = path.stat().st_size
    if size > FILE_BYTES:
        return {'path': relative, 'bytes': size, 'text': None, 'note': f'Larger than {FILE_BYTES} bytes; not read'}
    try:
        return {'path': relative, 'bytes': size, 'text': path.read_bytes().decode('utf-8')}
    except UnicodeDecodeError:
        return {'path': relative, 'bytes': size, 'text': None, 'note': 'Not UTF-8 text; not read'}


def write(root, relative, text):
    """Write text to the file, making its folders inside apps/; atomic. Returns its size."""
    path = inside(root, relative)
    data = text.encode('utf-8') if isinstance(text, str) else None
    if data is None or len(data) > FILE_BYTES:
        raise ValueError(f'Send the file as text of at most {FILE_BYTES} bytes')
    if path.is_dir():
        raise ValueError(f'{relative} is a folder')
    if relative.startswith('instances/') and not path.parent.is_dir():
        raise ValueError(f'There is no instance named {relative.split("/")[1]}')
    if not relative.startswith('instances/'):
        path.parent.mkdir(parents=True, exist_ok=True)
        inside(root, relative)  # nothing on the way became a link meanwhile
    temporary = path.with_name(f'.{path.name}.evlet-write')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o644)
    with os.fdopen(descriptor, 'wb') as file:
        file.write(data)
    os.replace(temporary, path)
    return {'path': relative, 'bytes': len(data)}


def delete(root, relative):
    path = inside(root, relative)
    if relative.startswith('instances/') and relative.endswith('/config.py'):
        raise ValueError('An instance keeps its config.py; delete the instance with instance_delete')
    if not path.is_file():
        raise ValueError(f'{relative} is not a file')
    path.unlink()
    return {'path': relative, 'deleted': True}
