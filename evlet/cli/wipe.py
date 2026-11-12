"""evlet remove and evlet reset: delete a whole project or wipe one instance back to a fresh install.

Both show exactly what goes first, offer a backup, and go ahead only when the project or instance
name is typed back (or given with --yes NAME). They delete only what Evlet made: the project's
apps/, instances/, deploy/ and .venv, and the systemd unit and Caddy site that evlet setup --host
--apply installed for this project. Symbolic links are removed as links and never followed.
"""

from contextlib import ExitStack, closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from . import site
from .project import atomic_json, child, instance_guard, project_root, valid_name

# What evlet setup creates in a project, in the order evlet remove deletes it.
PROJECT_PARTS = ('deploy', '.evlet', 'apps', '.venv', 'instances')
# What evlet reset keeps in an instance: its configuration, its lock file and data/backups.
INSTANCE_KEPT = ('config.py', 'routes.py', 'apps.lock', '.evlet.lock', '.reset.json', 'data')
RESET_MARKER = '.reset.json'


def terminal():
    return sys.stdin.isatty()


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def confirm_name(kind, expected, yes, ask=input):
    """Go ahead only when the name is typed back in a terminal, or --yes gives the same name."""
    if yes is not None:
        if yes != expected:
            raise ValueError(f'--yes {yes} does not match the {kind} name {expected}. Nothing was changed')
        return
    if not terminal():
        raise ValueError(f'Nothing was changed. Run this in a terminal to confirm, or pass --yes with the {kind} name')
    typed = ask(f'Type the {kind} name, {expected}, to confirm: ').strip()
    if typed != expected:
        raise ValueError(f'"{typed}" is not {expected}. Nothing was changed')


def want_backup(choice, ask=input):
    """--backup or --no-backup decide; otherwise ask in a terminal (yes by default) and back up without one."""
    if choice is not None:
        return choice
    if not terminal():
        return True
    return ask('Write a backup first? [Y/n] ').strip().lower() not in ('n', 'no')


def size(path):
    """Bytes under path, counting symbolic links as links."""
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    total = 0
    for folder, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(folder) / name).lstat().st_size
            except OSError:
                pass
    return total


def human(count):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if count < 1024 or unit == 'GB':
            return f'{count} {unit}' if unit == 'bytes' else f'{count:.1f} {unit}'
        count /= 1024


def file_count(path):
    if not path.is_dir() or path.is_symlink():
        return 0
    return sum(len(files) for _, _, files in os.walk(path))


def accounts(database):
    try:
        # immutable: a dry run leaves no -shm or -wal file behind.
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_users'").fetchone():
                return connection.execute('SELECT COUNT(*) FROM evlet_users').fetchone()[0]
    except sqlite3.Error:
        pass
    return None


def instance_summary(path):
    """One line saying what an instance holds: its database, private files, secret key and backups."""
    database = path / 'data/database.sqlite3'
    parts = []
    if database.is_file() and not database.is_symlink():
        users = accounts(database)
        parts.append(f'database {human(size(database))}' + (f' with {users} account' + ('s' if users != 1 else '') if users is not None else ''))
    else:
        parts.append('no database yet')
    files = file_count(path / 'files')
    if files:
        parts.append(f'{files} private file' + ('s' if files != 1 else '') + f' ({human(size(path / "files"))})')
    if (path / 'secret.key').is_file():
        parts.append('secret.key')
    backups = path / 'data/backups'
    if backups.is_dir() and not backups.is_symlink():
        count = sum(1 for _ in backups.iterdir())
        if count:
            parts.append(f'{count} backup' + ('s' if count != 1 else ''))
    return ', '.join(parts)


def instance_names(root):
    folder = root / 'instances'
    names = []
    for path in sorted(folder.iterdir()):
        try:
            valid_name(path.name)
        except ValueError:
            continue
        if path.is_dir() and not path.is_symlink():
            names.append(path.name)
    return names


def in_use(path):
    """Whether a server or another lifecycle command holds the instance lock. Creates no lock file."""
    if not (path / '.evlet.lock').exists():
        return False
    try:
        with instance_guard(path):
            return False
    except ValueError:
        return True


def owns_unit(root, unit):
    """Whether an installed evlet-NAME.service runs this project. Another project may use the same instance name."""
    if not unit.is_file() or unit.is_symlink():
        return False
    try:
        return f'WorkingDirectory={root}\n' in unit.read_text(encoding='utf-8')
    except OSError:
        return False


def owns_site(root, caddy, unit_ours):
    """Whether an installed evlet-NAME.caddy belongs to this project: its unit is ours, or it matches deploy/."""
    if not caddy.is_file() or caddy.is_symlink():
        return False
    return unit_ours or not site.differs(root / 'deploy' / caddy.name, caddy)


def system_files(root, names):
    """(ours, others): [(name, unit or None, caddy or None)] this project installed, and installed paths left alone."""
    ours, others = [], []
    for name in names:
        unit, caddy = site.installed(name)
        unit_ours = owns_unit(root, unit)
        caddy_ours = owns_site(root, caddy, unit_ours)
        if unit_ours or caddy_ours:
            ours.append((name, unit if unit_ours else None, caddy if caddy_ours else None))
        others += [path for path, mine in ((unit, unit_ours), (caddy, caddy_ours)) if not mine and (path.exists() or path.is_symlink())]
    return ours, others


def stop_steps(ours):
    return [['sudo', 'systemctl', 'disable', '--now', f'evlet-{name}'] for name, unit, _ in ours if unit]


def cleanup_steps(ours):
    steps = []
    for _, unit, caddy in ours:
        if unit:
            steps.append(['sudo', 'rm', '-f', str(unit)])
        if caddy:
            steps += [('unimport', str(caddy)), ['sudo', 'rm', '-f', str(caddy)]]
    if any(unit for _, unit, _ in ours):
        steps.append(['sudo', 'systemctl', 'daemon-reload'])
    if any(caddy for _, _, caddy in ours):
        steps.append(['sudo', 'systemctl', 'reload', 'caddy'])
    return steps


def describe(step):
    if step[0] == 'unimport':
        return f"sudo sed -i '\\|^import {step[1]}$|d' {site.CADDYFILE}"
    return site.describe(step)


def run_steps(steps, run=None):
    """Run sudo steps one by one, each shown first. sudo asks for the password itself."""
    run = run or subprocess.run
    if not shutil.which('systemctl') or not shutil.which('sudo'):
        raise ValueError('--sudo needs systemd and sudo. Run the steps shown above yourself, then run the command again without --sudo')
    for step in steps:
        print('$ ' + describe(step))
        if step[0] == 'unimport':
            caddyfile, line = Path(site.CADDYFILE), 'import ' + step[1]
            try:
                lines = caddyfile.read_text(encoding='utf-8').splitlines()
            except FileNotFoundError:
                continue
            if line in lines:
                kept = '\n'.join(item for item in lines if item != line).rstrip('\n') + '\n'
                run(['sudo', 'tee', str(caddyfile)], input=kept, text=True, check=True, stdout=subprocess.DEVNULL)
        else:
            run(step, check=True)


def delete(root, target):
    """Delete target inside root. A symbolic link is removed as a link; nothing outside root is touched."""
    if target.is_symlink():
        target.unlink()
        return
    if not target.exists():
        return
    if not target.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'{target} is outside {root}; nothing there was deleted')
    if target.is_dir():
        shutil.rmtree(target)  # removes links inside without following them
    else:
        target.unlink()


def backup_instance(path, target):
    """Copy what makes an instance: a consistent database snapshot, config, lock, secret key and private files."""
    from .install import snapshot
    target.mkdir(mode=0o700, parents=True)
    database = path / 'data/database.sqlite3'
    if database.is_file() and not database.is_symlink():
        snapshot(database, target / 'database.sqlite3')
    for name in ('config.py', 'routes.py', 'apps.lock', 'secret.key'):
        source = path / name
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, target / name)
    if (target / 'secret.key').exists():
        os.chmod(target / 'secret.key', 0o600)
    files = path / 'files'
    if files.is_dir() and not files.is_symlink():
        shutil.copytree(files, target / 'files', symlinks=True)
    return target


def git_notes(app):
    """A warning when an app checkout has work that exists nowhere else."""
    if not (app / '.git').exists():
        return ''
    notes = []
    try:
        changed = subprocess.run(['git', '-C', str(app), 'status', '--porcelain'], capture_output=True, text=True, timeout=20)
        unpushed = subprocess.run(['git', '-C', str(app), 'log', '--branches', '--not', '--remotes', '--oneline'],
                                  capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ''
    if changed.returncode == 0 and changed.stdout.strip():
        notes.append('uncommitted changes')
    if unpushed.returncode == 0 and unpushed.stdout.strip():
        notes.append('commits not pushed')
    return ' (has ' + ' and '.join(notes) + ')' if notes else ''


# evlet remove

def remove_plan(root, names, ours, others, *, keep_data, keep_backups, backup_dir):
    lines = [f'evlet remove deletes the Evlet project at {root}:', '']
    apps = root / 'apps'
    folders = [path for path in sorted(apps.iterdir()) if not path.name.startswith('.')] if apps.is_dir() and not apps.is_symlink() else []
    if folders:
        lines.append(f'  apps/ ({human(size(apps))}):')
        lines += [f'    {path.name}{git_notes(path)}' for path in folders]
    else:
        lines.append('  apps/ (empty)')
    if keep_data:
        lines.append('  instances/ is kept (--keep-data): ' + (', '.join(names) or 'none'))
    else:
        lines.append('  instances/' + (', except each data/backups (--keep-backups):' if keep_backups else ', with all their data, private files and keys:'))
        lines += [f'    {name}: {instance_summary(root / "instances" / name)}' for name in names] or ['    none']
    for part, what in (('.venv', 'the project Python environment'), ('.evlet', 'built app generations'),
                       ('deploy', 'the generated Caddy and service files')):
        target = root / part
        if target.is_symlink():
            lines.append(f'  {part}: a symbolic link; only the link is removed')
        elif target.exists():
            lines.append(f'  {part}/ ({human(size(target))}): {what}')
    kept = sorted(path.name for path in root.iterdir() if path.name not in PROJECT_PARTS)
    if kept:
        lines += ['', '  Kept, not made by Evlet: ' + ', '.join(kept)]
    if ours:
        lines += ['', 'System files this project installed (removed only with --sudo; each sudo step is shown first):']
        lines += ['    ' + describe(step) for step in stop_steps(ours) + cleanup_steps(ours)]
    if others:
        lines += ['', 'Left alone, they belong to another project or were not made by Evlet:']
        lines += [f'    {path}' for path in others]
    lines += ['', f'A backup, if you choose one, goes to {backup_dir}' + (' (instances and apps)' if names else ' (apps)')]
    return '\n'.join(lines)


def remove(target, *, plan=False, keep_data=False, keep_backups=False, backup=None, yes=None, sudo=False, ask=input, run=None):
    """Delete an Evlet project after showing what goes, offering a backup and checking the typed name."""
    root = project_root(Path(target))
    names = instance_names(root)
    ours, others = system_files(root, names)
    backup_dir = root.parent / f'{root.name}-backup-{stamp()}'
    print(remove_plan(root, names, ours, others, keep_data=keep_data, keep_backups=keep_backups, backup_dir=backup_dir))
    if plan:
        print('\nNothing was changed (--plan).')
        return None
    print()
    served = {name for name, unit, _ in ours if unit}
    busy = [name for name in names if name not in served and in_use(root / 'instances' / name)]
    if busy:
        raise ValueError('In use: ' + ', '.join(busy) + '. Stop evlet serve or evlet dev for them first. Nothing was changed')
    if ours and not sudo:
        raise ValueError('This project installed the system files listed above. Run again with --sudo to stop and remove them '
                         '(each step is shown first and sudo asks for your password), or run those steps yourself first. Nothing was changed')
    take_backup = want_backup(backup, ask)
    confirm_name('project', root.name, yes, ask)
    if ours:
        run_steps(stop_steps(ours), run)
    with ExitStack() as stack:
        for name in names:
            try:
                stack.enter_context(instance_guard(root / 'instances' / name))
            except ValueError:
                again = ' '.join(f'evlet-{item}' for item in sorted(served))
                raise ValueError(f'Instance {name} is still in use. Nothing in the project was deleted'
                                 + (f'. The services are stopped; start them again with sudo systemctl start {again}' if served else '')) from None
        written = None
        if take_backup:
            backup_dir.mkdir(mode=0o700)
            for name in names:
                backup_instance(root / 'instances' / name, backup_dir / 'instances' / name)
            if (root / 'apps').is_dir() and not (root / 'apps').is_symlink():
                shutil.copytree(root / 'apps', backup_dir / 'apps', symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
            written = backup_dir
            print(f'Backup written: {backup_dir}')
        if ours:
            run_steps(cleanup_steps(ours), run)
        for part in PROJECT_PARTS[:-1]:
            delete(root, root / part)
        if not keep_data:
            if keep_backups:
                for name in names:
                    path = root / 'instances' / name
                    for item in path.iterdir():
                        if item.name != 'data':
                            delete(root, item)
                    data = path / 'data'
                    if data.is_dir() and not data.is_symlink():
                        for item in data.iterdir():
                            if item.name != 'backups':
                                delete(root, item)
            else:
                delete(root, root / 'instances')
    if not any(root.iterdir()):
        root.rmdir()
        print(f'Removed {root}')
    else:
        print(f'Removed the Evlet parts of {root}. Kept: ' + ', '.join(sorted(path.name for path in root.iterdir())))
    return written


# evlet reset

def read_marker(path):
    """The app list an interrupted reset recorded before wiping, or None."""
    marker = path / RESET_MARKER
    if marker.is_symlink():
        raise ValueError(f'instances/{path.name}/{RESET_MARKER} cannot be a symbolic link')
    if not marker.is_file():
        return None
    value = json.loads(marker.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or not isinstance(value.get('apps'), list):
        raise ValueError(f'instances/{path.name}/{RESET_MARKER} is not valid')
    return [valid_name(item) for item in value['apps']]


def served_by_systemd(root, name, run=None):
    unit, _ = site.installed(name)
    if not owns_unit(root, unit) or not shutil.which('systemctl'):
        return False
    result = (run or subprocess.run)(['systemctl', 'is-active', '--quiet', f'evlet-{name}'])
    return result.returncode == 0


def wipe(root, path):
    """Delete everything an instance gathered while running; keep its configuration and data/backups."""
    for item in path.iterdir():
        if item.name not in INSTANCE_KEPT:
            delete(root, item)
    data = path / 'data'
    if data.is_symlink():
        data.unlink()
    elif data.is_dir():
        for item in data.iterdir():
            if item.name != 'backups':
                delete(root, item)


def reset(root, name, *, plan=False, backup=None, yes=None, trust=False, confirm=None, ask=input, run=None):
    """Wipe one instance and install its apps again, fresh, with a built-in Administrator that has no password."""
    from ..auth import ADMINISTRATOR
    from ..instances import read_instance, read_lock
    path = child(root, 'instances', name)
    instance = read_instance(path)  # also refuses paths that leave the instance through a symbolic link
    resumed = read_marker(path)
    apps = resumed if resumed is not None else sorted(read_lock(path)['apps'])
    lines = [f'evlet reset wipes instance {name} at {path}:', '']
    if resumed is not None:
        lines.append('  An earlier reset stopped after wiping. Running it again installs the apps below.')
    else:
        lines.append(f'  Deleted: {instance_summary(path)}.')
        lines.append('  Every record, account, session, API key, job, attachment and saved secret goes.')
        extra = sorted(item.name for item in path.iterdir() if item.name not in INSTANCE_KEPT + ('files', 'secret.key', '.install-journal.json'))
        if extra:
            lines.append('  Also deleted from the instance folder: ' + ', '.join(extra))
    lines.append('  Kept: config.py, routes.py and data/backups.')
    lines.append('  Installed again: ' + (', '.join(apps) if apps else 'nothing; no apps are installed') + '.')
    lines.append(f'  The built-in Administrator ({ADMINISTRATOR}) starts again with no password.')
    lines.append(f'  A backup, if you choose one, goes to instances/{name}/data/backups/{stamp()}-before-reset')
    print('\n'.join(lines))
    if plan:
        print('\nNothing was changed (--plan).')
        return None
    print()
    if served_by_systemd(root, name, run):
        raise ValueError(f'Instance {name} is being served by systemd. Stop it first with: sudo systemctl stop evlet-{name}. '
                         'Start it again after the reset. Nothing was changed')
    if in_use(path):
        raise ValueError(f'Instance {name} is in use. Stop its evlet serve or evlet dev first. Nothing was changed')
    take_backup = want_backup(backup, ask) if resumed is None else False
    confirm_name('instance', name, yes, ask)
    written = None
    with instance_guard(path):
        if take_backup:
            written = backup_instance(path, path / 'data/backups' / f'{stamp()}-before-reset')
            print(f'Backup written: {written}')
        if resumed is None:
            atomic_json(path / RESET_MARKER, {'apps': apps})
        wipe(root, path)
        atomic_json(path / 'apps.lock', {'apps': {}})
    if apps:
        from .install import install
        try:
            result = install(root, name, app_names=apps, trust=trust, confirm=confirm)
        except ValueError as exc:
            raise ValueError(f'{exc}\nThe data is already wiped. Fix this, then run evlet reset {name} again to install the apps') from None
        print('Installed again: ' + ', '.join(result['apps']))
    (path / RESET_MARKER).unlink(missing_ok=True)
    print(f'Instance {name} is fresh. The built-in Administrator ({ADMINISTRATOR}) has no password.')
    if instance.domain:
        print(f'Set it from the shell: evlet users set-password {name} {ADMINISTRATOR}')
    else:
        print(f'Set it at the first sign-in of evlet dev --instance {name}, or with evlet users set-password {name} {ADMINISTRATOR}')
    return written
