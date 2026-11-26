"""Small project directories and atomic local metadata writes."""

from contextlib import contextmanager
import json
import keyword
import os
from pathlib import Path
import re
import tempfile

from ..writes import file_lock, sync_directory


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
    try:
        with file_lock(path / '.evlet.lock', shared=serving):
            yield
    except ValueError as exc:
        if str(exc).startswith('Resource is in use'):
            raise ValueError('Instance is in use. Stop its server or other lifecycle command before changing it') from None
        raise


# project.json: the project id, its creation date and its mode (evlet.instances.read_project reads it).

def project_record(mode='development'):
    from datetime import datetime, timezone
    from ..identity import encode, new_id
    return {'id': encode(new_id()), 'created': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'mode': mode}


def ensure_project(root):
    """The project's project.json, made on first use: (record, made now). (None, False) when it cannot be
    written, such as a project folder the service account may only read; the next command run by its owner makes it."""
    from ..instances import PROJECT_FILE, read_project
    from ..writes import LockBusy
    root = Path(root)
    found = read_project(root)
    if found is not None:
        return found, False
    try:
        (root / '.evlet').mkdir(exist_ok=True)
        with file_lock(root / '.evlet' / 'project.lock'):
            found = read_project(root)
            if found is not None:
                return found, False
            record = project_record()
            atomic_json(root / PROJECT_FILE, record)
            return record, True
    except (OSError, LockBusy):
        return read_project(root), False


def new_project_id(root):
    """Give the project a new id, keeping its mode (evlet project new-id, for a fork). Returns (old id, record)."""
    from ..instances import PROJECT_FILE, read_project
    root = Path(root)
    (root / '.evlet').mkdir(exist_ok=True)
    with file_lock(root / '.evlet' / 'project.lock'):
        old = read_project(root)
        record = project_record(old['mode'] if old else 'development')
        atomic_json(root / PROJECT_FILE, record)
    return (old or {}).get('id'), record


def set_project_mode(root, mode):
    """Rewrite the mode key of project.json (evlet project mode); the shell only, never an HTTP route.

    With an endpoint, production refuses until the endpoint has a public host and the owner's two-step sign-in is set up."""
    from ..instances import PROJECT_FILE, PROJECT_MODES, read_project
    if mode not in PROJECT_MODES:
        raise ValueError('Use evlet project mode development or evlet project mode production')
    root = Path(root)
    ensure_project(root)
    if mode == 'production':
        from .endpoint import exists, read_settings, two_step_ready
        if exists(root) and not read_settings(root)['host']:
            raise ValueError('The endpoint has no public host yet. Add one with evlet setup --endpoint --endpoint-host HOSTNAME, '
                             'then switch to production')
        if exists(root) and not two_step_ready(root):
            raise ValueError('Production needs the owner\'s two-step sign-in. Sign in to the endpoint as admin and set it up '
                             'in Settings, then run evlet project mode production again')
    with file_lock(root / '.evlet' / 'project.lock'):
        record = read_project(root)
        if record is None:
            raise ValueError(f'{root / PROJECT_FILE} could not be written')
        changed = record['mode'] != mode
        if changed:
            atomic_json(root / PROJECT_FILE, {**record, 'mode': mode})
    return changed
