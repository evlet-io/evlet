"""Create lightweight customer instance directories."""

import os
from pathlib import Path
import tempfile

from .project import child, atomic_json
from ..instances import Instance


def config_text(name, domain=None, extra=None):
    """The config.py that evlet instance writes, optionally with a domain and one more Instance argument."""
    return f'from evlet import Instance\n\ninstance = Instance(name={name!r}, domain={domain!r}' + (', ' + extra if extra else '') + ')\n'


def set_domain(root, name, domain, extra=None):
    """Give a new instance its public host. Returns False when config.py already has it.

    Only the untouched config.py that evlet instance wrote is rewritten; an edited one is left for its owner."""
    path = child(root, 'instances', name)
    config = path / 'config.py'
    text = config.read_text(encoding='utf-8')
    from ..instances import read_instance
    if read_instance(path).domain == domain:
        return False
    if text != config_text(name):
        raise ValueError(f'instances/{name}/config.py was edited. Set domain={domain!r} there yourself, then run the same command again')
    Instance(name, domain=domain)
    target = config.with_name('.config.py.new')
    target.write_text(config_text(name, domain, extra), encoding='utf-8')
    os.replace(target, config)
    return True


def create_instance(root, name):
    Instance(name)
    target = child(root, 'instances', name)
    if target.exists():
        raise ValueError(f'Instance already exists: {name}')
    with tempfile.TemporaryDirectory(prefix='.create-', dir=root / 'instances') as directory:
        stage = Path(directory) / name
        stage.mkdir(mode=0o700)
        (stage / 'config.py').write_text(config_text(name), encoding='utf-8')
        atomic_json(stage / 'apps.lock', {'apps': {}})
        os.rename(stage, target)
    return target
