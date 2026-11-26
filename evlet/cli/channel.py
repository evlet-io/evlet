"""The endpoint's side of the management channel (evlet/manage.py, docs/project-endpoint.md#management-channel).

Each instance gets its own token. The endpoint keeps it in `.evlet/control/<instance>.token` and the instance only its
SHA-256 in `instances/<instance>/control.key`, both 0600 and written atomically. The instance reads control.key on
each call, so a token made while it runs works at once. Calls go to 127.0.0.1 and the instance's own port.
"""

import json
import os
from pathlib import Path
import secrets
import urllib.error
import urllib.request

FOLDER = Path('.evlet/control')
TIMEOUT = 30.0


class ChannelError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def _write(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'w', encoding='ascii') as file:
        file.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def token(root, name):
    """The instance's management token, made with its control.key the first time."""
    from ..manage import digest
    from .project import child
    root = Path(root)
    path = root / FOLDER / f'{name}.token'
    key = child(root, 'instances', name) / 'control.key'
    try:
        value = path.read_text(encoding='ascii').strip()
    except FileNotFoundError:
        value = None
    if value is None or not key.is_file() or key.read_text(encoding='ascii').strip() != digest(value):
        value = value or secrets.token_urlsafe(32)
        _write(path, value + '\n')
        _write(key, digest(value) + '\n')
    return value


def call(root, name, port, method, route, body=None, *, query=None):
    """One management call to the instance on 127.0.0.1:port. Raises ChannelError with the instance's answer."""
    from urllib.parse import urlencode
    address = f'http://127.0.0.1:{port}/_/manage/{route}' + ('?' + urlencode(query) if query else '')
    request = urllib.request.Request(address, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + token(root, name), 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return json.loads(response.read() or b'null')
    except urllib.error.HTTPError as error:
        try:
            data = json.loads(error.read() or b'null') or {}
        except ValueError:
            data = {}
        fields = data.get('fields') if isinstance(data, dict) else None
        message = (next(iter(fields.values())) if isinstance(fields, dict) and fields else None) or \
            (data.get('error') if isinstance(data, dict) else None) or f'HTTP {error.code}'
        raise ChannelError(error.code, f'{name}: {message}') from None
    except (urllib.error.URLError, OSError) as error:
        raise ChannelError(503, f'{name} does not answer on port {port} ({getattr(error, "reason", error)}). Start it with instance_start') from None
