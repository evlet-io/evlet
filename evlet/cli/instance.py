"""Create lightweight customer instance directories."""

import os
from pathlib import Path
import tempfile

from .project import child, atomic_json
from ..instances import Instance


def create_instance(root, name):
    Instance(name)
    target = child(root, 'instances', name)
    if target.exists():
        raise ValueError(f'Instance already exists: {name}')
    with tempfile.TemporaryDirectory(prefix='.create-', dir=root / 'instances') as directory:
        stage = Path(directory) / name
        stage.mkdir(mode=0o700)
        (stage / 'config.py').write_text(f'from evlet import Instance\n\ninstance = Instance(name={name!r}, database="sqlite", storage="local", domain=None)\n', encoding='utf-8')
        atomic_json(stage / 'apps.lock', {'apps': {}})
        os.rename(stage, target)
    return target
