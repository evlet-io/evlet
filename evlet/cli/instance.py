"""Create lightweight customer instance directories."""

import os
from pathlib import Path
import tempfile

from .project import child, atomic_json
from ..instances import Instance


SECURITY = """
# Sign-in rules. Admin accounts follow these only. Controls can change the password
# minimum and two-step sign-in for everyone else, in either direction.
#   password_min: 1 to 64 characters. 1 means no rule: even "1" is a password.
#   admin_two_step: admins, the built-in Administrator too, must use two-step sign-in.
#   two_step: everyone else must use it too. Controls can turn this on or off.
#   admin_sign_in: the sign-in page opens with the username admin filled in.
security = Security(password_min=15, admin_two_step=True, two_step=False, admin_sign_in=False)
"""


def config_text(name, domain=None, extra=None):
    """The config.py that evlet instance writes, optionally with a domain and one more Instance argument."""
    return (f'from evlet import Instance, Security\n\ninstance = Instance(name={name!r}, domain={domain!r}' + (', ' + extra if extra else '') + ')\n'
            + SECURITY)


def untouched(text, name):
    """Whether config.py is still what evlet instance wrote: this release's text, or the one before 0.4.2 without Security."""
    return text in (config_text(name), f'from evlet import Instance\n\ninstance = Instance(name={name!r}, domain=None)\n')


def set_domain(root, name, domain, extra=None):
    """Give a new instance its public host. Returns False when config.py already has it.

    Only the untouched config.py that evlet instance wrote is rewritten; an edited one is left for its owner."""
    path = child(root, 'instances', name)
    config = path / 'config.py'
    text = config.read_text(encoding='utf-8')
    from ..instances import read_instance
    if read_instance(path).domain == domain:
        return False
    if not untouched(text, name):
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
