"""evlet upgrade: install a newer Evlet in the project's .venv, then plan, back up, stop, migrate and restart.

pip replaces the files of the code that is running this command. So every step after pip runs the
new code as its own `.venv/bin/python -P -m evlet ...` process, and this process imports nothing
from Evlet once pip has started: no step ever mixes old and new modules.
"""

from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from packaging.version import InvalidVersion, Version

from .project import child
from .restart import Failed, check_service, service_name
from .setup import environment_version, python_in
from . import site


def allowed(current, target=None):
    """The pip requirement for this upgrade. 0.x stays within its minor, 1.0 and later within its major."""
    now = Version(current)
    if target is None:
        upper = f'0.{now.minor + 1}' if now.major == 0 else f'{now.major + 1}'
        return f'evlet>={current},<{upper}'
    try:
        wanted = Version(target)
    except InvalidVersion:
        raise ValueError(f'--to takes a version such as {current}, not {target}') from None
    if wanted < now:
        raise ValueError(f'Evlet {target} is older than {current}. To go back, restore a backup instead; see evlet restore')
    return f'evlet=={target}'


def rollback(root, name, service, before, backup):
    """The commands that put the instance back as it was before the upgrade."""
    python = shlex.quote(str(python_in(root)))
    steps = [f'sudo systemctl stop {service}', f'{python} -m pip install evlet=={before}']
    if backup:
        steps.append(f'evlet restore {shlex.quote(backup)} --instance {name} --yes {name}')
    steps.append(f'sudo systemctl start {service}')
    return steps


def upgrade(root, name, *, to=None, service=None, yes=False, unit_dir=None, run=None, which=None,
            interactive=None, ask=None, out=print):
    """Upgrade Evlet for one instance. Returns True when the site is back up on the new version."""
    root = Path(root).absolute()
    service = service_name(name, service)
    run = run or subprocess.run
    if not (root / '.venv' / 'evlet-project.json').is_file():
        raise ValueError('This project has no .venv made by evlet setup. Upgrade Evlet with pip in its environment yourself')
    path = child(root, 'instances', name)
    if not path.is_dir():
        raise ValueError(f'There is no instance named {name} in {root}')
    before = environment_version(root)
    if before is None:
        raise ValueError('Evlet is not installed in the project .venv. Restore .venv before upgrading')
    requirement = allowed(before, to)
    try:
        check_service(root, name, {}, service=service, unit_dir=unit_dir or site.SYSTEMD_DIR, which=which or shutil.which)
    except Failed as exc:
        raise ValueError(f'Service: {exc}') from None
    if not yes:
        interactive = sys.stdin.isatty() if interactive is None else interactive
        if not interactive:
            out(f'No terminal to confirm the upgrade. Run evlet upgrade {name} --yes to upgrade without asking.')
            return False
    python = str(python_in(root))
    evlet = [python, '-P', '-m', 'evlet', '--project', str(root)]

    def step(command, *, capture=False):
        out('$ ' + shlex.join(command))
        try:
            result = run(command, capture_output=capture, text=True)
        except (OSError, subprocess.SubprocessError) as exc:
            out(f'{shlex.join(command)} failed: {exc}')
            return None
        for stream in (result.stdout, result.stderr) if capture else ():
            if stream:
                out(stream.rstrip())
        return result if result.returncode == 0 else None

    def failed(what, *, after=(), backup=None, stopped=False):
        out(f'{what} failed. The upgrade stopped here.')
        if after:
            out('To put everything back as it was before the upgrade, run:')
            for line in after:
                out('    ' + line)
        if stopped:
            out(f'{service} is stopped.')
        return False

    out(f'Evlet {before} is installed in {root / ".venv"}.')
    if step([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', requirement]) is None:
        out(f'Nothing was changed; {service} keeps running Evlet {before}.')
        return False
    after = environment_version(root)
    undo_pip = [f'{shlex.quote(python)} -m pip install evlet=={before}']
    if after == before:
        out(f'Evlet {before} is already the newest release allowed by {requirement}. Nothing else was done.')
        return True
    out(f'Installed Evlet {after}. {service} keeps running {before} until it restarts.')
    if step([*evlet, 'migrate', name, '--plan']) is None:
        return failed('The migration plan', after=undo_pip)
    if not yes:
        answer = (ask or input)(f'Back up {name}, stop {service}, migrate it and restart it on Evlet {after}? [y/N] ').strip().lower()
        if answer not in ('y', 'yes'):
            out(f'Nothing else was done. {service} keeps running Evlet {before}; the .venv has {after}.')
            out('To go back to the old version in .venv, run:')
            out('    ' + undo_pip[0])
            return False
    saved = step([*evlet, 'backup', '--instance', name], capture=True)
    if saved is None:
        return failed('The backup', after=undo_pip)
    backup = next((line[len('Backup: '):].strip() for line in saved.stdout.splitlines() if line.startswith('Backup: ')), None)
    if step(['sudo', 'systemctl', 'stop', service]) is None:
        return failed(f'Stopping {service}', after=undo_pip)
    undo = rollback(root, name, service, before, backup)
    if step([*evlet, 'migrate', name]) is None:
        return failed('The migration', after=undo, stopped=True)
    restart = [*evlet, 'restart', name, '--yes']
    if service != f'evlet-{name}':
        restart += ['--service', service]
    if step(restart) is None:
        return failed('The restart', after=undo)
    out(f'{name} runs Evlet {after}. The backup from before the upgrade is {backup}.')
    return True
