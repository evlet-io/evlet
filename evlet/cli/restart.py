"""evlet restart: check an instance, then restart the systemd service that evlet setup --host --apply installed.

Every check only reads. When one fails, the running service is left alone, so a working site is never
restarted into a broken one. Nothing runs with sudo without being shown first.
"""

from contextlib import closing
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import time

from .project import child, instance_guard

SERVICE_NAME = re.compile(r'[A-Za-z0-9@._:-]+')
JOURNAL_LINES = 20
BY_HAND = ('Restart it by hand instead: stop the server and start it again with evlet serve {name}. '
           'evlet dev reloads by itself.')


class Failed(Exception):
    """A check found a problem; its message is one line in plain words."""


def service_name(name, service=None):
    """The unit to restart: evlet-<instance>, or --service with or without .service."""
    value = service or f'evlet-{name}'
    if value.endswith('.service'):
        value = value[:-len('.service')]
    if not SERVICE_NAME.fullmatch(value):
        raise ValueError('--service takes a systemd unit name, such as evlet-demo')
    return value


def unit_options(text):
    """(instance, project, port) from the unit's ExecStart when it runs evlet serve, else None."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith('ExecStart='):
            continue
        try:
            words = shlex.split(line[len('ExecStart='):].lstrip('-@+!:'))
        except ValueError:
            return None
        # python -m evlet serve NAME, or the evlet script itself.
        if 'serve' not in words or not any(word.rsplit('/', 1)[-1] == 'evlet' for word in words[:words.index('serve')]):
            continue
        after = words[words.index('serve') + 1:]
        instance = after[0] if after and not after[0].startswith('-') else None

        def option(name, default=None):
            for index, word in enumerate(after):
                if word == name and index + 1 < len(after):
                    return after[index + 1]
                if word.startswith(name + '='):
                    return word.split('=', 1)[1]
            return default
        try:
            port = int(option('--port', '8000'))
        except ValueError:
            return None
        return instance, option('--project'), port
    return None


def check_instance(root, name, state):
    from ..instances import read_instance, read_routes
    path = child(root, 'instances', name)
    if not path.is_dir():
        raise Failed(f'there is no instance named {name} in {root}')
    config = read_instance(path)
    read_routes(path)
    state['config'] = config
    return 'ok'


def check_apps(root, name, state):
    """The installed apps match apps.lock and import, without building the instance."""
    from ..builds import prepare
    from ..instances import load_definitions, require_consistent
    from ..packages import portable_lock_entry, resolve_packages
    path = child(root, 'instances', name)
    if 'config' not in state:
        raise Failed('skipped until the instance loads')
    locked = require_consistent(path)
    if not locked['apps']:
        raise Failed(f'no apps are installed. Run evlet install APP --instance {name}')
    packages = resolve_packages(root, sorted(locked['apps']))
    if {package.name for package in packages} != set(locked['apps']):
        raise Failed(f'app dependencies changed since the last migrate. Run evlet migrate {name} first')
    for package in packages:
        if package.locked() != portable_lock_entry(package.name, locked['apps'][package.name]):
            raise Failed(f'{package.name} differs from apps.lock. Run evlet migrate {name} first')
    pinned, manifest = prepare(root, packages)
    load_definitions(pinned, modules=manifest['modules'])
    state['locked'], state['packages'] = locked, packages
    return 'ok (' + ', '.join(f'{package.name} {package.version}' for package in packages) + ')'


def check_routes(root, name, state):
    """Build the instance as evlet serve would, which resolves its route table."""
    from ..instances import build_instance
    if 'packages' not in state:
        raise Failed('skipped until the apps load')
    path = child(root, 'instances', name)
    state['app'], _ = build_instance(state['config'], path, state['packages'], port=state.get('port') or 8000)
    routes = state['app'].routes
    routes.text()
    return f'ok (primary host {routes.primary})' if routes.primary else 'ok (no domain)'


def framework_pending(path, locked):
    """Names of framework steps the database has not run, read without writing."""
    from .. import framework
    database = path / 'data/database.sqlite3'
    applied = 0
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_framework_migrations'").fetchone():
                applied = connection.execute('SELECT COUNT(*) FROM evlet_framework_migrations').fetchone()[0]
    return [step.name for step in framework.STEPS[applied:]]


def check_migrations(root, name, state):
    """No framework or app migration is waiting. This never migrates."""
    from .. import framework
    if 'locked' not in state:
        raise Failed('skipped until the apps load')
    pending = framework_pending(child(root, 'instances', name), state['locked'])
    if 'app' in state:
        app = state['app']
        result = app.database.plan(app.database.migrations)
        pending += [migration.name for migration in result.pending]
    if pending:
        raise Failed('pending: ' + ', '.join(pending) + f'. Run evlet migrate {name} first')
    recorded = state['locked'].get('evlet', {}).get('version', 'an older release')
    if state['locked'].get('evlet') != framework.state():
        raise Failed(f'Evlet {framework.version()} is installed but {name} was migrated with Evlet {recorded}. Run evlet migrate {name} first')
    if 'app' not in state:
        raise Failed('framework migrations are current; app migrations are checked once the routes load')
    if result.differences:
        raise Failed(f'the models differ from their migrations. See evlet migrate {name} --plan')
    return 'ok (none pending)'


def check_service(root, name, state, *, service, unit_dir, which):
    if which('systemctl') is None:
        raise Failed('this machine has no systemd. ' + BY_HAND.format(name=name))
    unit = Path(unit_dir) / f'{service}.service'
    try:
        text = unit.read_text(encoding='utf-8')
    except FileNotFoundError:
        raise Failed(f'{unit} is not installed. Install it with evlet setup --host --apply, or pass --service. '
                     + BY_HAND.format(name=name)) from None
    except (OSError, UnicodeError) as exc:
        raise Failed(f'cannot read {unit}: {exc}') from None
    options = unit_options(text)
    if options is None:
        raise Failed(f'{unit} does not run evlet serve, so it is not an Evlet unit. '
                     f'Restart it by hand with sudo systemctl restart {service}')
    instance, project, port = options
    if instance != name:
        raise Failed(f'{unit} serves instance {instance}, not {name}. Pass --service with the unit of {name}')
    if project and Path(project).resolve() != Path(root).resolve():
        raise Failed(f'{unit} serves the project in {project}, not {root}. Run evlet restart there, or pass --project')
    state['port'] = port
    return f'ok ({unit}, port {port})'


def _busy(name):
    raise Failed(f'another command, such as evlet migrate {name}, is changing it. Wait for it to finish, then try again')


def run_checks(root, name, *, service, unit_dir, which, out):
    """Run every check, print one line each, and return (passed, state)."""
    from contextlib import ExitStack, nullcontext
    import shutil
    state = {}

    def attempt(check):
        try:
            return True, check()
        except Failed as exc:
            return False, str(exc)
        except Exception as exc:  # any error loading trusted project code means: do not restart
            return False, str(exc).strip() or type(exc).__name__
    # The unit is read first, since the instance loads with the port it serves on; its line comes last.
    service_ok, service_line = attempt(lambda: check_service(root, name, state, service=service, unit_dir=unit_dir,
                                                             which=which or shutil.which))
    checks = (
        ('Instance', lambda: check_instance(root, name, state)),
        ('Apps', lambda: check_apps(root, name, state)),
        ('Routes', lambda: check_routes(root, name, state)),
        ('Migrations', lambda: check_migrations(root, name, state)),
    )
    passed = service_ok
    path = child(root, 'instances', name)
    try:
        with ExitStack() as stack:
            try:
                # A shared lock, like a running server: an evlet migrate cannot start while the checks read.
                stack.enter_context(instance_guard(path, serving=True) if path.is_dir() else nullcontext())
            except ValueError:
                checks = (('Instance', lambda: _busy(name)),)
            for label, check in checks:
                ok, line = attempt(check)
                passed = passed and ok
                out(f'{label}: {line}')
    finally:
        app = state.get('app')
        if app is not None:
            app.database.close()
    out(f'Service: {service_line}')
    return passed, state


def health(port, host, fetch=None):
    """Whether http://127.0.0.1:port/health answers 200, sending host as Host when given."""
    import json
    import urllib.request
    if fetch is not None:
        return fetch(port, host)
    request = urllib.request.Request(f'http://127.0.0.1:{port}/health', headers={'Host': host} if host else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=2) as response:
            return response.status == 200 and json.loads(response.read() or b'{}').get('status') == 'ok'
    except (OSError, ValueError):
        return False


def wait_for(port, host, timeout, *, fetch=None, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while True:
        if health(port, host, fetch):
            return True
        if clock() >= deadline:
            return False
        sleep(0.5)


def journal(service, run, out):
    command = ['journalctl', '-u', service, '-n', str(JOURNAL_LINES), '--no-pager']
    out(f'Last {JOURNAL_LINES} lines of its journal:')
    out('$ ' + shlex.join(command))
    try:
        result = run(command, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        out(f'Could not read the journal ({exc}). Run sudo {shlex.join(command)}')
        return
    text = (result.stdout or '').rstrip()
    if result.returncode != 0 or not text:
        out(f'Could not read the journal. Run sudo {shlex.join(command)}')
    else:
        out(text)


def restart(root, name, *, service=None, check_only=False, yes=False, timeout=30.0, unit_dir=None, run=None,
            which=None, fetch=None, sleep=time.sleep, clock=time.monotonic, interactive=None, ask=None, out=print):
    """Check the instance, then restart its service and wait for /health. Returns True on success."""
    from . import site
    service = service_name(name, service)
    root = Path(root)
    passed, state = run_checks(root, name, service=service, unit_dir=unit_dir or site.SYSTEMD_DIR, which=which, out=out)
    if not passed:
        out(f'Checks failed. {service} was not restarted and keeps running as it was.')
        return False
    if check_only:
        out(f'All checks passed. Run evlet restart {name} to restart {service}.')
        return True
    if not yes:
        interactive = sys.stdin.isatty() if interactive is None else interactive
        if not interactive:
            out(f'No terminal to confirm the restart. Run evlet restart {name} --yes to restart without asking.')
            return False
        answer = (ask or input)(f'Restart {service} now? [y/N] ').strip().lower()
        if answer not in ('y', 'yes'):
            out('Nothing was restarted.')
            return False
    run = run or subprocess.run
    command = ['sudo', 'systemctl', 'restart', service]
    out('$ ' + shlex.join(command))
    try:
        run(command, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        out(f'{shlex.join(command)} failed: {exc}')
        journal(service, run, out)
        return False
    config, port = state['config'], state['port']
    primary = state['app'].routes.primary if state.get('app') else None
    host = primary or config.domain
    if not wait_for(port, host, timeout, fetch=fetch, sleep=sleep, clock=clock):
        seconds = f'{timeout:g}'
        out(f'{service} did not answer http://127.0.0.1:{port}/health within {seconds} seconds.')
        journal(service, run, out)
        return False
    out(f'{name} is back up at https://{host}' if host else f'{name} is back up on 127.0.0.1:{port}')
    return True
