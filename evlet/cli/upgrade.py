"""Stage upgrades outside the live environment and migrate every instance sharing the release."""

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import shlex
import shutil
import subprocess
import sys

from packaging.version import InvalidVersion, Version

from .project import child, atomic_json, instance_guard, sync_directory
from ..writes import file_lock, require_space, sync_file
from .restart import Failed, check_service, service_name
from .setup import environment_source, environment_version, python_in
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


# Run the staged release directly: invoking its CLI would delegate back to the live project .venv.
PLAN = """from pathlib import Path
import sys
from evlet.cli.install import plan
from evlet.framework import version
result, suggestions, packages, steps = plan(Path(sys.argv[1]), sys.argv[2])
print('Planned with Evlet ' + version())
print(result.text(suggestion=False))
for step in steps:
    print(step.name + ': ' + step.summary)
"""

# With a dedicated service account, the deploy user builds the apps for the staged release first: the builds
# are code, which the service account only reads.
BUILD = """from pathlib import Path
import sys
from evlet.builds import prepare
from evlet.instances import read_lock
from evlet.packages import resolve_packages
root = Path(sys.argv[1])
prepare(root, resolve_packages(root, sorted(read_lock(root / 'instances' / sys.argv[2])['apps'])))
"""


def owned(owner, command):
    """command as owner, through sudo -u, when a dedicated service account owns the instance's data.

    Its plan, backup, migration and recovery then run as that account (each command is shown first);
    stopping and restarting the service stays with the deploy user's sudo."""
    return ['sudo', '-u', owner, *command] if owner else command


def upgrade(root, name, *, to=None, service=None, yes=False, all_instances=False, unit_dir=None, run=None, which=None,
            interactive=None, ask=None, out=print):
    """Stage a release, plan and back up all affected instances, then publish it while services are stopped."""
    root = Path(root).absolute()
    with file_lock(root / '.evlet-upgrade.lock'):
        return _upgrade(root, name, to=to, service=service, yes=yes, all_instances=all_instances,
                        unit_dir=unit_dir, run=run, which=which, interactive=interactive, ask=ask, out=out)


def _upgrade(root, name, *, to, service, yes, all_instances, unit_dir, run, which, interactive, ask, out):
    run = run or subprocess.run
    if not (root / '.venv' / 'evlet-project.json').is_file():
        raise ValueError('This project has no .venv made by evlet setup. Upgrade Evlet with pip in its environment yourself')
    path = child(root, 'instances', name)
    if not path.is_dir():
        raise ValueError(f'There is no instance named {name} in {root}')
    journal = root / '.evlet-upgrade.json'
    if journal.exists():
        raise ValueError(f'An interrupted project upgrade needs recovery first; follow the retained recovery record at {journal}')
    names = sorted({name, *(item.name for item in (root / 'instances').iterdir()
                            if item.is_dir() and (item / 'data/database.sqlite3').is_file())})
    if len(names) > 1 and not all_instances:
        raise ValueError('The project environment is shared by ' + ', '.join(names) +
                         '. Use --all-instances to plan, back up, migrate and restart them together')
    services = {item: service_name(item, service if item == name else None) for item in names}
    before = environment_version(root)
    if before is None:
        raise ValueError('Evlet is not installed in the project .venv. Restore .venv before upgrading')
    source = environment_source(root)
    if source is not None:
        if to is not None:
            raise ValueError(f'Evlet is installed from source at {source[0]}, so --to does not apply. '
                             f'Check out the version you want with git, then run evlet upgrade {name} again')
        return _from_source(root, name, names, source, service=service, yes=yes, unit_dir=unit_dir, run=run,
                            which=which, interactive=interactive, ask=ask, out=out)
    requirement = allowed(before, to)
    units = Path(unit_dir or site.SYSTEMD_DIR)
    for item in names:
        child(root, 'instances', item)
        # A sibling with no installed unit is not served (a dev or test instance): it is backed up and migrated
        # with the others, but there is no service to stop or restart.
        if item != name and not (units / f'{services[item]}.service').exists():
            services[item] = None
            continue
        try:
            check_service(root, item, {}, service=services[item], unit_dir=units, which=which or shutil.which)
        except Failed as exc:
            raise ValueError(f'Service {item}: {exc}') from None
    served = [item for item in names if services[item] is not None]
    if not yes:
        interactive = sys.stdin.isatty() if interactive is None else interactive
        if not interactive:
            out(f'No terminal to confirm the upgrade. Run evlet upgrade {name} --yes to upgrade without asking.')
            return False
    python = str(python_in(root))
    evlet = [python, '-P', '-m', 'evlet', '--project', str(root)]
    step = _stepper(run, out)
    owners = {item: site.state_owner(root, item) for item in names}

    def as_owner(item, command):
        return owned(owners[item], command)

    live = root / '.venv'
    if live.is_symlink():
        raise ValueError('The project environment cannot be a symbolic link')
    size = sum(item.stat().st_size for item in live.rglob('*') if item.is_file() and not item.is_symlink())
    require_space(root, size + 16 * 2**20, 'stage the project environment')
    out(f'Evlet {before} is installed in {live}. A staged environment will be checked first.')
    with tempfile.TemporaryDirectory(prefix='.evlet-upgrade-', dir=root) as temporary:
        staging = Path(temporary)
        staged = staging / '.venv'
        if any(owners.values()):
            os.chmod(staging, 0o755)  # the service account runs the staged plan
        shutil.copytree(live, staged, symlinks=True)
        staged_python = str(python_in(staging))
        if step([staged_python, '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', requirement]) is None:
            out('The staged installation failed. The project environment and services were left unchanged.')
            return False
        after = environment_version(staging)
        if after is None:
            out('The staged environment has no Evlet installation. The project was left unchanged.')
            return False
        if after == before:
            out(f'Evlet {before} is already the newest release allowed by {requirement}. Nothing else was done.')
            return True
        for item in names:
            if owners[item]:
                if step([staged_python, '-P', '-c', BUILD, str(root), item]) is None:
                    out(f'The build for {item} failed. The project environment and services were left unchanged.')
                    return False
                site.share_builds(root)
            if step(as_owner(item, [staged_python, '-P', '-c', PLAN, str(root), item])) is None:
                out(f'The migration plan for {item} failed. The project environment and services were left unchanged.')
                return False
        if not yes and (ask or input)(f'Stop, back up, migrate and restart {", ".join(names)} on Evlet {after}? [y/N] ').strip().lower() not in ('y', 'yes'):
            out('Upgrade cancelled. The project environment and services were left unchanged.')
            return False
        stopped = []
        for item in served:
            if step(['sudo', 'systemctl', 'stop', services[item]]) is None:
                for previous in stopped:
                    step(['sudo', 'systemctl', 'start', services[previous]])
                out('Stopping the services failed. The original environment remains installed.')
                return False
            stopped.append(item)
        backups = {}
        for item in names:
            backup = _backup_line(step(as_owner(item, [*evlet, 'backup', '--instance', item]), capture=True))
            if not backup:
                for previous_name in stopped:
                    step(['sudo', 'systemctl', 'start', services[previous_name]])
                out(f'The backup for {item} failed. The original environment remains installed.')
                return False
            backups[item] = backup
        previous = root / ('.venv-before-' + staging.name.removeprefix('.evlet-upgrade-'))
        undo = [*(f'sudo systemctl stop {services[item]}' for item in served),
                f'if [ -d {shlex.quote(str(previous))} ]; then if [ -e {shlex.quote(str(live))} ]; then mv {shlex.quote(str(live))} {shlex.quote(str(staging.with_name(staging.name + "-failed")))}; fi; mv {shlex.quote(str(previous))} {shlex.quote(str(live))}; fi',
                *(shlex.join(as_owner(item, [*evlet, 'restore', backups[item], '--instance', item, '--yes', item])) for item in names),
                f'rm {shlex.quote(str(journal))}',
                *(f'sudo systemctl start {services[item]}' for item in served)]
        record = {'version': 1, 'before': before, 'after': after, 'previous_environment': str(previous),
                  'staged_environment': str(staged), 'instances': names, 'backups': backups, 'recovery': undo}
        try:
            # The server must actually have released every lifecycle lease before the environment changes.
            with ExitStack() as guards:
                for item in names:
                    guards.enter_context(instance_guard(child(root, 'instances', item)))
                relocate_scripts(staged, live)
                sync_environment(staged)
                atomic_json(journal, record)
                os.replace(live, previous)
                sync_directory(root)
                try:
                    os.replace(staged, live)
                    sync_directory(root)
                except BaseException:
                    os.replace(previous, live)
                    sync_directory(root)
                    journal.unlink()
                    sync_directory(root)
                    raise
            for item in names:
                if step(as_owner(item, [*evlet, 'migrate', item])) is None:
                    raise RuntimeError(f'The migration for {item} failed')
            # Every database matches the new release before any service is allowed back up.
            for item in served:
                restart = [*evlet, 'restart', item, '--yes']
                if services[item] != f'evlet-{item}':
                    restart += ['--service', services[item]]
                if step(restart) is None:
                    raise RuntimeError(f'The restart for {item} failed')
        except (OSError, ValueError, RuntimeError) as exc:
            out(str(exc) + '. The upgrade stopped here.')
            if previous.exists():
                out('The previous environment and all database backups are retained. To recover the project:')
                for line in undo:
                    out('    ' + line)
                out(f'Recovery record: {journal}')
            else:
                journal.unlink(missing_ok=True)
                sync_directory(root)
                for item in stopped:
                    step(['sudo', 'systemctl', 'start', services[item]])
                out('The original environment remains installed.')
            return False
        journal.unlink()
        sync_directory(root)
        shutil.rmtree(previous)
        sync_directory(root)
        out(f'{", ".join(names)} run Evlet {after}. Backups: ' + ', '.join(f'{item}: {backups[item]}' for item in names) + '.')
        unserved = [item for item in names if services[item] is None]
        if unserved:
            out(f'No service runs {", ".join(unserved)}, so ' + ('it was' if len(unserved) == 1 else 'they were') + ' migrated but not started.')
        return True


def _stepper(run, out):
    """Run one command, print it, and answer its result, or None when it failed."""
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
    return step


def _backup_line(saved):
    return next((line[len('Backup: '):].strip() for line in saved.stdout.splitlines() if line.startswith('Backup: ')), None) if saved else None


def _from_source(root, name, names, source, *, service, yes, unit_dir, run, which, interactive, ask, out):
    """Evlet runs from source (an editable install of a git clone, say): pip must not replace it, so the
    framework is left to git and only the instance steps run: stop, back up, migrate and restart."""
    place, editable = source
    how = 'Update that code with git' + ('' if editable else f', then reinstall it with pip install {place}')
    out(f'Evlet is installed from source at {place}, so evlet upgrade does not change the framework. {how}. '
        'Backing up and migrating the instances on the code that is there now.')
    units = Path(unit_dir or site.SYSTEMD_DIR)
    services = {}
    for item in names:
        unit = service_name(item, service if item == name else None)
        # A development project usually has no unit: there is nothing to stop or restart.
        if not (item == name and service) and not (units / f'{unit}.service').exists():
            services[item] = None
            continue
        try:
            check_service(root, item, {}, service=unit, unit_dir=units, which=which or shutil.which)
        except Failed as exc:
            raise ValueError(f'Service {item}: {exc}') from None
        services[item] = unit
    served = [item for item in names if services[item] is not None]
    if not yes:
        interactive = sys.stdin.isatty() if interactive is None else interactive
        if not interactive:
            out(f'No terminal to confirm the upgrade. Run evlet upgrade {name} --yes to upgrade without asking.')
            return False
        verbs = 'Stop, back up, migrate and restart' if served else 'Back up and migrate'
        if (ask or input)(f'{verbs} {", ".join(names)}? [y/N] ').strip().lower() not in ('y', 'yes'):
            out('Upgrade cancelled. Nothing was changed.')
            return False
    step = _stepper(run or subprocess.run, out)
    evlet = [str(python_in(root)), '-P', '-m', 'evlet', '--project', str(root)]
    owners = {item: site.state_owner(root, item) for item in names}
    stopped = []

    def start_again():
        for item in stopped:
            step(['sudo', 'systemctl', 'start', services[item]])

    for item in served:
        if step(['sudo', 'systemctl', 'stop', services[item]]) is None:
            start_again()
            out('Stopping the services failed. Nothing was changed.')
            return False
        stopped.append(item)
    backups = {}
    for item in names:
        backups[item] = _backup_line(step(owned(owners[item], [*evlet, 'backup', '--instance', item]), capture=True))
        if not backups[item]:
            start_again()
            out(f'The backup for {item} failed. No database was migrated.')
            return False
    for item in names:
        if step(owned(owners[item], [*evlet, 'migrate', item])) is None:
            out(f'The migration for {item} failed. The upgrade stopped here' + (' with the services stopped' if stopped else '') + '. '
                'To go back, restore the backups: ' + '; '.join(f'evlet restore {backups[each]} --instance {each}' for each in names) + '.')
            return False
    for item in served:
        restart = [*evlet, 'restart', item, '--yes']
        if services[item] != f'evlet-{item}':
            restart += ['--service', services[item]]
        if step(restart) is None:
            out(f'The restart for {item} failed. Every database is migrated; start {services[item]} by hand.')
            return False
    out(f'{", ".join(names)} migrated on Evlet from {place}. Backups: ' + ', '.join(f'{item}: {backups[item]}' for item in names) + '.')
    return True


def relocate_scripts(staged, live):
    """pip entry points contain absolute interpreter paths; keep them valid after publishing the directory."""
    folder = staged / ('Scripts' if os.name == 'nt' else 'bin')
    for path in folder.iterdir() if folder.is_dir() else ():
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as stream:
            header = b''.join(stream.readline(4096) for _ in range(4))
        if header.startswith(b'#!') and str(staged).encode() in header:
            data = path.read_bytes()
            path.write_bytes(header.replace(str(staged).encode(), str(live).encode()) + data[len(header):])


def sync_environment(path):
    """Make the prepared release durable before its directory replaces the live environment."""
    directories = [path]
    for item in path.rglob('*'):
        if item.is_symlink():
            continue
        if item.is_dir():
            directories.append(item)
        elif item.is_file():
            sync_file(item)
    for directory in reversed(directories):
        sync_directory(directory)
    sync_directory(path.parent)
