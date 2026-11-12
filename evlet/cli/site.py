"""evlet setup in one go: project, apps, instance, install, then evlet dev or a Caddy site.

Every step calls the code of its own command and skips work that is already done, so
running the same command again resumes after a failure.
"""

from contextlib import contextmanager
import ipaddress
import json
from pathlib import Path
import shlex
import sqlite3
import subprocess

from .project import child, valid_name

CADDYFILE = '/etc/caddy/Caddyfile'
CADDY_DIR = '/etc/caddy'
SYSTEMD_DIR = '/etc/systemd/system'
# This server's public addresses, saved by the first DNS check that passed with --ip.
SERVER_FILE = 'server.json'
STEP_OPTIONS = ('app', 'get', 'instance', 'dev', 'host', 'trust', 'apply', 'ip')


def wanted(args):
    """Whether evlet setup was given any one-go option; without them it keeps its original behaviour."""
    return any(getattr(args, name, None) for name in STEP_OPTIONS) or getattr(args, 'port', 8000) != 8000


def split_list(values):
    """Comma lists from one or more --app/--get options, in order and without repeats."""
    result = []
    for value in values or ():
        for item in value.split(','):
            item = item.strip()
            if not item:
                raise ValueError('Leave no empty item in a comma list, such as notes,,crm')
            if item not in result:
                result.append(item)
    return result


def check(args):
    """Refuse unsafe or incomplete combinations before anything is created."""
    from ..packages import validate_url
    if args.dev and args.host:
        raise ValueError('Choose --dev or --host, not both. Development servers stay on loopback')
    apps, urls = split_list(args.app), split_list(args.get)
    for name in apps:
        valid_name(name)
    for url in urls:
        validate_url(url)
    if args.instance:
        valid_name(args.instance)
    if (args.dev or args.host) and not (args.instance and (apps or urls)):
        raise ValueError('--dev and --host need --instance and at least one --app or --get')
    if (args.apply or args.ip) and not args.host:
        raise ValueError('--apply and --ip only work with --host')
    if args.port != 8000 and not (args.dev or args.host):
        raise ValueError('--port only works with --dev or --host')
    if args.host:
        from ..tenancy import hostname
        try:
            host = hostname(args.host)
        except ValueError:
            host = None
        if host != args.host or host.endswith('.localhost'):
            raise ValueError('--host takes a public lowercase hostname, such as erp.example.com, without a scheme or port')
        import os
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            raise ValueError('Run evlet setup --host as the user that will run the service, not as root. It asks before using sudo')
    return apps, urls


@contextmanager
def step(label):
    try:
        yield
    except (ValueError, TypeError, SyntaxError, OSError, RuntimeError, ImportError, sqlite3.Error, subprocess.SubprocessError) as exc:
        raise ValueError(f'Step "{label}" failed: {exc}\nFix it, then run the same command again. Finished steps are skipped.') from None


def project(directory, framework=None):
    """Create the project, or reuse it when this command already made it."""
    from .project import project_root
    from .setup import setup
    target = Path(directory).absolute()
    with step('project'):
        if target.exists():
            root = project_root(target)
            import os
            if os.environ.get('EVLET_DELEGATED_PROJECT') != str(root):  # the re-run inside .venv stays quiet
                print(f'Project exists: {root}')
            return root
        root = setup(target, framework=framework).resolve()
        print(f'Project created: {root}')
        return root


def fetched_from(root, url):
    """The app in apps/ that was fetched from url, if any."""
    for path in sorted((root / 'apps').iterdir()):
        if (path / '.git').is_dir():
            result = subprocess.run(['git', '-C', str(path), 'config', '--get', 'remote.origin.url'], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip() == url:
                return path.name
    return None


def has_users(path):
    """Whether someone can sign in: an account with a password. The built-in Administrator has none at first."""
    database = path / 'data/database.sqlite3'
    if not database.is_file():
        return False
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_users'").fetchone():
            return False
        return connection.execute("SELECT 1 FROM evlet_users WHERE password<>'' LIMIT 1").fetchone() is not None
    finally:
        connection.close()


def ask_admin():
    """Ask for the Administrator's password in a terminal; it is never echoed or shown."""
    import getpass
    import sys
    if not sys.stdin.isatty():
        raise ValueError('The Administrator has no password yet. Run the command in a terminal to set one')
    print('A public site cannot set its first password in the browser. Choose a password for the built-in Administrator now.')
    password = getpass.getpass('Password: ')
    if password != getpass.getpass('Repeat the password: '):
        raise ValueError('The two passwords differ. Nothing was changed')
    return password


def create_admin(root, name, password):
    """Set the built-in Administrator's first password through the instance's own rules, audited as cli."""
    from ..auth import ADMINISTRATOR
    from ..controls import set_password_from_shell
    from ..instances import load_runtime
    from .project import instance_guard
    path = child(root, 'instances', name)
    with instance_guard(path):
        app = load_runtime(root, name)
        try:
            app.initialize()
            if not app.business.auth.setup_needed():
                raise ValueError('An administrator already has a password')
            minimum = app.business.auth.security.password_min
        finally:
            app.database.close()
        set_password_from_shell(path / 'data/database.sqlite3', ADMINISTRATOR, password, minimum)
    return ADMINISTRATOR


def installed(name):
    """Where --apply installs the instance's systemd unit and Caddy site."""
    return Path(SYSTEMD_DIR) / f'evlet-{name}.service', Path(CADDY_DIR) / f'evlet-{name}.caddy'


def differs(source, target):
    """Whether an installed file is missing or not the same as the one in deploy/. Unreadable counts as different."""
    try:
        return Path(target).read_bytes() != Path(source).read_bytes()
    except OSError:
        return True


def privileged_steps(root, name, restart=False):
    """The sudo commands that install the service and the Caddy site. Shown first; run only with --apply.

    Every step runs again on each --apply, so a file that changed since the last install is replaced and
    Caddy reloads it. restart also restarts a service that was already running with an older unit."""
    deploy = root / 'deploy'
    unit, site = installed(name)
    return [
        ['sudo', 'install', '-m', '644', str(deploy / unit.name), str(unit)],
        ['sudo', 'install', '-m', '644', str(deploy / site.name), str(site)],
        ('import', str(site)),
        ['sudo', 'systemctl', 'daemon-reload'],
        ['sudo', 'systemctl', 'enable', '--now', f'evlet-{name}'],
        *([['sudo', 'systemctl', 'restart', f'evlet-{name}']] if restart else []),
        ['sudo', 'systemctl', 'reload', 'caddy'],
    ]


def saved_addresses(root):
    """The public addresses a passing DNS check stored in deploy/server.json, or None."""
    try:
        value = json.loads((root / 'deploy' / SERVER_FILE).read_text(encoding='utf-8')).get('ip')
        if not isinstance(value, list) or not value:
            return None
        return [str(ipaddress.ip_address(item)) for item in value]
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def save_addresses(root, ips):
    """Keep the addresses a passing DNS check used, so later runs on this server need no --ip."""
    from . import proxy
    addresses = sorted({str(ipaddress.ip_address(item)) for item in ips})
    proxy.write(root / 'deploy' / SERVER_FILE, json.dumps({'ip': addresses}, indent=2) + '\n')


def site_answers(host, fetch=None, attempts=4, pause=5.0, sleep=None):
    """One line saying whether https://host answers from outside, after Caddy reloads. Information only, never a failure.

    It tries for about half a minute, since Caddy may still be getting the certificate."""
    import time
    from ..assistants import Assistants
    fetch, sleep = fetch or Assistants.fetch, sleep or time.sleep
    url = f'https://{host}/.well-known/oauth-protected-resource'
    reason = ''
    for attempt in range(attempts):
        if attempt:
            sleep(pause)
        try:
            status, _, _ = fetch('GET', url)
        except Exception as exc:  # any network or TLS failure only means "not yet"
            reason = str(getattr(exc, 'reason', exc))
            continue
        return f'https://{host} answers (status {status}).'
    return (f'https://{host} does not answer yet ({reason}). Caddy keeps trying to get the certificate. '
            'Check the A record and that ports 80 and 443 are open, then open the address again in a few minutes.')


def describe(command):
    if command[0] == 'import':
        line, caddyfile = 'import ' + command[1], CADDYFILE
        return f'grep -qxF {shlex.quote(line)} {caddyfile} || echo {shlex.quote(line)} | sudo tee -a {caddyfile}'
    return shlex.join(command)


def run_privileged(commands, run=None):
    import shutil
    run = run or subprocess.run
    if not shutil.which('systemctl') or not shutil.which('sudo'):
        raise ValueError('--apply needs systemd and sudo. Run the printed steps on the server yourself')
    for command in commands:
        print('$ ' + describe(command))
        if command[0] == 'import':
            caddyfile, line = Path(CADDYFILE), 'import ' + command[1]
            if line not in caddyfile.read_text(encoding='utf-8').splitlines():
                run(['sudo', 'tee', '-a', str(caddyfile)], input='\n' + line + '\n', text=True, check=True, stdout=subprocess.DEVNULL)
        else:
            run(command, check=True)


def public_site(root, name, host, *, port=8000, ips=None, apply=False, ask=None, run=None, user=None):
    """Prepare a Caddy site for host: administrator, domain, files, DNS check, then the privileged steps."""
    import getpass
    from ..auth import trusted_proxies_line
    from ..instances import load_runtime
    from . import proxy
    from .instance import set_domain
    path = child(root, 'instances', name)
    with step('administrator'):
        if has_users(path):
            print(f'Administrator exists. If nobody knows its password, run evlet users set-password {name} admin')
        else:
            password = (ask or ask_admin)()
            print(f'Password set for {create_admin(root, name, password)}')
    with step('domain'):
        changed = set_domain(root, name, host, trusted_proxies_line(proxy.LOCAL))
        print(f'Domain set: {host}' if changed else f'Domain is {host}')
    with step('Caddy and service files'):
        app = load_runtime(root, name, port=port)
        app.database.close()
        trusted = app.business.auth.trusted_proxies
        (root / 'deploy').mkdir(mode=0o755, exist_ok=True)
        site = proxy.write(root / 'deploy' / f'evlet-{name}.caddy', proxy.caddy(app.routes, port, name, trusted))
        unit = proxy.write(root / 'deploy' / f'evlet-{name}.service', proxy.systemd_unit(root, name, port, user or getpass.getuser()))
        print(f'Wrote {site}\nWrote {unit}')
        line = proxy.trust_line(trusted)
        if line:
            print(f'Add this line to Instance(...) in instances/{name}/config.py so Evlet sees client addresses:\n    {line},')
        installed_unit, installed_site = installed(name)
        # An earlier --apply installed older files: say so, and restart the service when its unit changed.
        restart = installed_unit.exists() and differs(unit, installed_unit)
        for source, target in ((site, installed_site), (unit, installed_unit)):
            if target.exists() and differs(source, target):
                print(f'{target} is out of date. ' + ('Installing the new file.' if apply else 'Run the steps below, or --apply, to replace it.'))
    with step('DNS check'):
        saved = None if ips else saved_addresses(root)
        if saved:
            print(f'Using the public address saved in deploy/{SERVER_FILE}: ' + ', '.join(saved))
        report, ok = proxy.check(app.routes, ips or saved)
        print(report)
        if not ok:
            raise ValueError(f'{host} has no usable A or AAAA record yet. Point it at this server, or pass --ip with the public address')
        if ips and saved_addresses(root) != sorted({str(ipaddress.ip_address(item)) for item in ips}):
            save_addresses(root, ips)
            print(f'Saved the public address in deploy/{SERVER_FILE}; later runs on this server need no --ip')
    commands = privileged_steps(root, name, restart=restart)
    if not apply:
        print('Review the two files, then run these steps with sudo:')
        print('\n'.join('    ' + describe(command) for command in commands))
        print('Or run the same command again with --apply to run them. Each one is shown first and sudo asks for your password.')
        return
    with step('install the service and Caddy site'):
        run_privileged(commands, run=run)
    print(f'Serving https://{host} through Caddy. Evlet listens on 127.0.0.1:{port} only.')
    print(site_answers(host))


def run(root, args, *, confirm=None, ask=None):
    """Every step after the project exists. main() calls it in the project's own environment."""
    from .app import create_app, get_app
    from .instance import create_instance
    from .install import install
    apps, urls = check(args)
    names = list(apps)
    for name in apps:
        with step(f'app {name}'):
            if child(root, 'apps', name).exists():
                print(f'App exists: {name}')
            else:
                print(f'App created: {create_app(root, name)}')
    for url in urls:
        with step(f'get {url}'):
            existing = fetched_from(root, url)
            if existing:
                print(f'App exists: {existing} from {url}')
            else:
                existing = get_app(root, url, trust=args.trust, confirm=confirm).name
            if existing not in names:
                names.append(existing)
    if not args.instance:
        print('Next: evlet instance NAME, then evlet install APP --instance NAME')
        return
    name = args.instance
    path = child(root, 'instances', name)
    with step(f'instance {name}'):
        if path.exists():
            print(f'Instance exists: {name}')
        else:
            print(f'Instance created: {create_instance(root, name)}')
    if names:
        from ..instances import read_lock
        with step('install'):
            missing = [item for item in names if item not in read_lock(path)['apps']]
            if missing:
                # One call installs every app with its dependencies in order, behind one trust question and one backup.
                result = install(root, name, app_names=missing, trust=args.trust, confirm=confirm)
                print(f'Instance {name}: installed ' + ', '.join(result['apps']))
                print('Applied migrations: ' + (', '.join(result['migrations']) or 'none; schema is current'))
            else:
                print(f'Instance {name}: ' + ', '.join(names) + ' already installed')
    if args.dev:
        from .serve import serve
        with step('evlet dev'):
            serve(root, name, port=args.port, development=True, link=True)
    elif args.host:
        public_site(root, name, args.host, port=args.port, ips=args.ip, apply=args.apply, ask=ask)
    else:
        where = shlex.quote(str(root))
        print(f'Next: cd {where} && evlet dev --instance {name}\nOr serve it with: evlet serve {name}')
