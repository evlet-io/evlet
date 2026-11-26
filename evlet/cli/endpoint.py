"""The project endpoint: an Application over project.db that runs the project's instances (docs/project-endpoint.md).

`evlet setup --endpoint` makes .evlet/project.sqlite3 (project.db) with the owner account, and `evlet endpoint`
serves it: the portal at /, the framework's sign-in, Settings (two-step sign-in), OAuth and the MCP endpoint at /_/mcp,
the operate tools as @api(tool=True) services, and the supervisor that runs each instance as a child process. project.db
holds credentials and logs only; instances, apps and hosts are read from the files that hold them.

Development binds 127.0.0.1:7999 and prints a one-time sign-in link; two-step sign-in is optional. Production serves
`--endpoint-host` behind Caddy over HTTPS with password and two-step sign-in, and Docs off.
"""

import asyncio
from contextlib import closing
import json
from pathlib import Path
import re
from types import SimpleNamespace

from .oplog import database_path, exists, registry
from .project import atomic_json

SETTINGS = Path('.evlet/endpoint.json')
KEY = Path('.evlet/project-secret.key')
DEV_PORT = 7999
FIRST_INSTANCE_PORT = 8000
UNIT = 'evlet-project'
OWNER = 'admin'  # The owner is the framework's built-in Administrator of project.db.


# ---------- settings: .evlet/endpoint.json ----------

def read_settings(root):
    """{'port', 'host', 'instances': {name: port}, 'never_ask': [tool names]}; host is None in development.

    never_ask lists project tools whose calls run without the owner's approval; it is edited on the server only, and
    the tools that always ask (project_tools.ALWAYS_ASK in production) ignore it."""
    path = Path(root) / SETTINGS
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'port': DEV_PORT, 'host': None, 'instances': {}, 'never_ask': []}
    except (OSError, ValueError):
        raise ValueError(f'{path} is not valid JSON. Fix it, or remove it and run evlet setup --endpoint again') from None
    if not isinstance(value, dict) or type(value.get('port')) is not int or not isinstance(value.get('instances', {}), dict) \
            or value.get('host') is not None and not isinstance(value['host'], str):
        raise ValueError(f'{path} must hold {{"port": a number, "host": a hostname or null, "instances": {{name: port}}}}')
    never = value.get('never_ask', [])
    if not isinstance(never, list) or not all(isinstance(item, str) for item in never):
        raise ValueError(f'{path}: never_ask must be a list of tool names')
    settings = {'port': value['port'], 'host': value.get('host'), 'instances': dict(value.get('instances', {})), 'never_ask': never}
    if 'caddy_admin' in value:
        settings['caddy_admin'] = value['caddy_admin']  # docs/project-endpoint.md#caddy; checked by caddy.admin_address
    return settings


def save_settings(root, value):
    (Path(root) / '.evlet').mkdir(exist_ok=True)
    atomic_json(Path(root) / SETTINGS, value)


def instance_names(root):
    folder = Path(root) / 'instances'
    return sorted(path.name for path in folder.iterdir() if path.is_dir() and not path.name.startswith('.')
                  and (path / 'config.py').is_file()) if folder.is_dir() else []


def instance_ports(root):
    """The port of each instance, kept in endpoint.json: the port of its evlet setup --host unit when it has one,
    else the next free port from 8000. New instances get theirs here and keep it."""
    from .restart import unit_options
    settings = read_settings(root)
    ports = {name: port for name, port in settings['instances'].items() if name in instance_names(root)}
    changed = ports != settings['instances']
    for name in instance_names(root):
        if name in ports:
            continue
        port = None
        try:
            options = unit_options((Path(root) / 'deploy' / f'evlet-{name}.service').read_text(encoding='utf-8'))
            port = options[2] if options else None
        except (OSError, UnicodeError):
            pass
        taken = {*ports.values(), settings['port']}
        if port is None or port in taken:
            port = FIRST_INSTANCE_PORT
            while port in taken:
                port += 1
        ports[name], changed = port, True
    if changed:
        save_settings(root, {**settings, 'instances': ports})
    return ports


def url(root):
    settings = read_settings(root)
    return f'https://{settings["host"]}' if settings['host'] else f'http://127.0.0.1:{settings["port"]}'


def running(root):
    """Whether the endpoint answers /health on its port."""
    from .restart import health
    settings = read_settings(root)
    return health(settings['port'], settings['host'])


def describe(root):
    """The Endpoint line of evlet project."""
    if not exists(root):
        return 'none'
    return url(root) + (' (running)' if running(root) else ' (not running; start it with evlet endpoint)')


def two_step_ready(root):
    """Whether the owner has two-step sign-in set up in project.db."""
    from .. import qb
    from ..writes import read_file
    if not exists(root):
        return False
    with closing(read_file(database_path(root))) as connection:
        if not connection.table_exists('evlet_user_profile'):
            return False
        row = connection.run(qb.select(qb.table('evlet_users', 'u'), 'p.two_step_secret')
                             .join(qb.table('evlet_user_profile', 'p'), qb.eq('p.user_id', qb.col('u.id')))
                             .where(qb.eq('u.username', OWNER))).one()
    return row is not None and row['two_step_secret'] is not None


# ---------- the Application ----------

def mode(root):
    from ..instances import read_project
    record = read_project(root)
    return record['mode'] if record else 'development'


def build(root, *, runtime=None):
    """The endpoint Application over project.db. runtime (project_tools.Runtime) supplies the services; without it
    the Application has none (setup uses it to create project.db)."""
    from ..app import Application
    from ..auth import LocalAuth, Security
    from ..database import Database
    from ..routing import Docs, resolve_routes
    from ..secrets import SecretKey
    settings = read_settings(root)
    production = mode(root) == 'production'
    if production and not settings['host']:
        raise ValueError('This project is in production mode, so its endpoint needs a public host: '
                         'evlet setup --endpoint --endpoint-host project.example.com')
    # A host means HTTPS behind Caddy; the mode decides two-step sign-in and Docs.
    host = settings['host']
    routes = resolve_routes(instance_config=SimpleNamespace(name='project', domain=host),
                            routes_module={'docs': Docs(enabled=not production)}, port=settings['port'])
    database = Database(database_path(root), registry())
    auth = LocalAuth(database, origins=routes.origins(), secure_cookies=bool(host),
                     cookie_name='__Host-evlet_session' if host else 'evlet_session',
                     trusted_proxies=('127.0.0.1', '::1') if host else (),
                     security=Security(admin_two_step=production, admin_sign_in=True))
    from .project_tools import endpoints
    app = Application(database, auth=auth, routes=routes, apis=endpoints(runtime) if runtime is not None else (),
                      name='Project ' + Path(root).name)
    app.business.secret_key = SecretKey(Path(root) / KEY)
    return app


def create(root, *, host=None, port=None):
    """Make project.db and endpoint.json; keep both when they exist. Returns whether project.db was made now."""
    root = Path(root)
    settings = read_settings(root)
    settings['host'] = host if host is not None else settings['host']
    settings['port'] = port or settings['port']
    save_settings(root, settings)
    made = not exists(root)
    app = build(root)
    try:
        app.initialize()
    finally:
        app.database.close()
    if made:
        allow_assistants(root)
    instance_ports(root)
    return made


def allow_assistants(root):
    """The owner may connect MCP clients, and they may call the tools that change something (each still waits for
    the owner's approval): the Controls switches an instance's admin would set, set once for the endpoint."""
    from .. import qb
    from ..writes import shell_transaction
    with shell_transaction(database_path(root)) as connection:
        connection.run(qb.update('evlet_users', allow_mcp=1).where(qb.eq('username', OWNER)))
        connection.run(qb.insert('evlet_overrides', kind='setting', key='mcp.allow_writes', value='true')
                       .on_conflict('kind', 'key').do_update(value='true'))


def set_owner_password(root, password):
    from ..controls import set_password_from_shell
    return set_password_from_shell(database_path(root), OWNER, password)


def owner_has_password(root):
    from .. import qb
    from ..writes import read_file
    with closing(read_file(database_path(root))) as connection:
        row = connection.run(qb.select('evlet_users', 'password').where(qb.eq('username', OWNER))).one()
    return row is not None and row['password'] != ''


# ---------- the portal and the supervisor around the Application ----------

PORTAL = Path(__file__).resolve().parent.parent / 'ui' / 'static' / 'project.html'
# The desk's policy (evlet/ui/serving.py): the UI framework positions its layers with inline styles.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


CONTROL = Path('.evlet/control/endpoint.token')
CONTROL_PATHS = {('POST', '/_/project/restart'): 'instance_restart', ('POST', '/_/project/upgrade'): 'evlet_upgrade'}


def control_token(root):
    """The token with which the server shell asks the running endpoint to restart or upgrade (evlet restart, evlet
    upgrade): made on first use, 0600, readable only by the account that runs the project."""
    from .channel import _write
    path = Path(root) / CONTROL
    try:
        return path.read_text(encoding='ascii').strip()
    except FileNotFoundError:
        import secrets
        value = secrets.token_urlsafe(32)
        _write(path, value + '\n')
        return value


class Endpoint:
    """ASGI: the portal page at /, the project's one TLS ask at /_/tls-allowed, the shell's control routes, everything
    else by the Application. Starts the supervisor (and the Caddy push, when there is one) once the Application has
    started, and stops them before the Application shuts down."""

    def __init__(self, app, supervisor, caddy=None, host=None, runtime=None):
        from ..app import TLS_CHECK
        self.app, self.supervisor, self.caddy, self.host, self.tls_check = app, supervisor, caddy, host, TLS_CHECK
        self.runtime = runtime

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            async def relay(message):
                if message['type'] == 'lifespan.startup.complete':
                    # Instances with a database; an empty one is started with instance_start once an app is installed.
                    root = self.supervisor.root
                    await asyncio.to_thread(self.supervisor.start_all, [name for name in self.supervisor.children
                                                                         if (root / 'instances' / name / 'data/database.sqlite3').is_file()])
                    if self.caddy is not None:
                        self.caddy.start()
                await send(message)

            async def incoming():
                message = await receive()
                if message['type'] == 'lifespan.shutdown':
                    if self.caddy is not None:
                        await asyncio.to_thread(self.caddy.stop)
                    await asyncio.to_thread(self.supervisor.stop_all)
                return message
            await self.app(scope, incoming, relay)
            return
        if scope['type'] == 'http' and scope['path'] == self.tls_check:
            await self.tls_allowed(scope, send)
            return
        if scope['type'] == 'http' and scope['method'] in ('GET', 'HEAD') and scope['path'] in ('/', '/index.html'):
            await self.portal(scope, send)
            return
        if scope['type'] == 'http' and scope['path'].startswith('/_/project/'):
            await self.control(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def control(self, scope, receive, send):
        """The shell's routes: loopback only, not through a proxy, with the control token; 404 otherwise, like the
        instances' management channel. POST /_/project/restart {instance}, POST /_/project/upgrade {to}, and
        GET /_/project/operation?id=N to follow one."""
        import hmac
        import ipaddress
        from urllib.parse import parse_qs
        headers = dict(scope.get('headers') or ())
        try:
            loopback = ipaddress.ip_address((scope.get('client') or ('',))[0]).is_loopback
        except ValueError:
            loopback = False
        sent = headers.get(b'authorization', b'').decode('latin-1')
        route = (scope['method'], scope['path'])
        allowed = self.runtime is not None and loopback and b'forwarded' not in headers and b'x-forwarded-for' not in headers \
            and sent.startswith('Bearer ') and hmac.compare_digest(sent[7:].encode(), control_token(self.supervisor.root).encode()) \
            and (route in CONTROL_PATHS or route == ('GET', '/_/project/operation'))
        data = bytearray()
        while scope['method'] == 'POST':
            message = await receive()
            data.extend(message.get('body', b''))
            if len(data) > 4096 or not message.get('more_body'):
                break
        status, result = 404, {'error': 'Route not found'}
        if allowed:
            try:
                if route in CONTROL_PATHS:
                    body = json.loads(data or b'{}')
                    arguments = {'instance': str(body.get('instance', ''))} if route[1].endswith('restart') else {'to': str(body.get('to') or '')}
                    status, result = 200, await asyncio.to_thread(self.runtime.call, CONTROL_PATHS[route], arguments)
                else:
                    from .oplog import operation
                    query = parse_qs(scope.get('query_string', b'').decode('ascii', 'replace'))
                    found = operation(self.supervisor.root, int(query.get('id', ['0'])[0]))
                    active = self.runtime.active
                    if found is not None and active is not None and active.id == found['id']:
                        found.update(result='running', output=list(active.lines))
                    status, result = (200, found) if found else (404, {'error': 'No such operation'})
            except Exception as exc:  # a refusal (another operation runs, no such instance) goes back to the shell
                errors = getattr(exc, 'errors', None)
                status, result = 422, {'error': '; '.join(map(str, errors.values())) if isinstance(errors, dict) else str(exc)}
        body = json.dumps(result, default=str).encode()
        await send({'type': 'http.response.start', 'status': status, 'headers': [(b'content-type', b'application/json'),
                    (b'cache-control', b'no-store'), (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})

    async def portal(self, scope, send):
        # The Host check of every page: a request for another host is refused as the Application refuses it.
        host = dict(scope.get('headers') or ()).get(b'host', b'')
        if host not in self.app.business.auth.hosts:
            await self.app(scope, _no_body, send)
            return
        try:
            body, status = PORTAL.read_bytes(), 200
        except OSError:
            body, status = b'The project portal is not built. Run the frontend production build.', 503
        headers = [(b'content-type', b'text/html; charset=utf-8' if status == 200 else b'text/plain; charset=utf-8'),
                   (b'cache-control', b'no-store'), (b'content-security-policy', CSP.encode()), (b'x-content-type-options', b'nosniff'),
                   (b'referrer-policy', b'no-referrer'), (b'x-frame-options', b'DENY'), (b'content-length', str(len(body)).encode())]
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': b'' if scope['method'] == 'HEAD' else body})

    async def tls_allowed(self, scope, send):
        """The project's one on-demand TLS ask: 200 for the endpoint's host and for every host an instance serves now
        (asked at that instance's own /_/tls-allowed), 404 otherwise. Loopback only, as on an instance."""
        import ipaddress
        from urllib.parse import parse_qs
        from . import caddy
        try:
            loopback = ipaddress.ip_address(self.app.business.auth.client_address(scope)).is_loopback
            domain = parse_qs(scope.get('query_string', b'').decode('ascii'), max_num_fields=1).get('domain', [''])[0].lower()
        except (ValueError, UnicodeError):
            loopback, domain = False, ''
        ok = scope['method'] == 'GET' and loopback and await asyncio.to_thread(caddy.allowed, self.supervisor, self.host, domain)
        body = json.dumps({'allowed': domain} if ok else {'error': 'Not served here'}).encode()
        await send({'type': 'http.response.start', 'status': 200 if ok else 404,
                    'headers': [(b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                                (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})


async def _no_body():
    return {'type': 'http.request', 'body': b''}


def application(root, *, link=True):
    """The ASGI application `evlet endpoint` serves, with its runtime (supervisor and operation runner)."""
    from .project_tools import Runtime
    from .supervisor import Supervisor
    root = Path(root)
    if not exists(root):
        raise ValueError(f'This project has no endpoint. Add one with: evlet setup {root} --endpoint')
    development = mode(root) == 'development'
    supervisor = Supervisor(root, instance_ports(root), development=development)
    runtime = Runtime(root, supervisor)
    settings = read_settings(root)
    runtime.caddy = caddy_sync(root, supervisor, settings)
    app = build(root, runtime=runtime)
    runtime.app = app
    served = Endpoint(app, supervisor, runtime.caddy, settings['host'], runtime)
    if development and not settings['host']:
        from ..devlink import DevSignIn
        served = DevSignIn(served, announce=link, user=OWNER)
    return served, runtime


def caddy_sync(root, supervisor, settings):
    """The thread that pushes the instances' hosts to Caddy's admin API (evlet/cli/caddy.py), or None without one."""
    from ..instances import read_project
    from .caddy import CaddySync, admin_address
    address = admin_address(settings)
    return None if address is None else CaddySync(supervisor, read_project(root)['id'], address)


def serve(root, *, link=True):
    """evlet endpoint: serve the endpoint on 127.0.0.1 and its port, and run the instances."""
    from .serve import run_server
    settings = read_settings(root)
    served, runtime = application(root, link=link)
    where = url(root)
    print(f'Project endpoint: {where}' + ('' if settings['host'] is None else f' (Evlet listens on 127.0.0.1:{settings["port"]})'), flush=True)
    for name, port in sorted(runtime.supervisor.children.items()):
        print(f'  instance {name}: http://127.0.0.1:{port.port}', flush=True)
    run_server(served, host='127.0.0.1', port=settings['port'], lifespan='on', proxy_headers=False)


def shell_operation(root, route, body, out=print, *, poll=0.5):
    """evlet restart / evlet upgrade while the endpoint runs: ask it to run the operation, print its output as it
    comes and return whether it ended ok. None when the endpoint does not answer (the CLI then uses systemd)."""
    import time
    import urllib.error
    import urllib.request
    settings = read_settings(root)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def ask(method, path, data=None):
        request = urllib.request.Request(f'http://127.0.0.1:{settings["port"]}{path}', method=method,
                                         data=None if data is None else json.dumps(data).encode(),
                                         headers={'Authorization': 'Bearer ' + control_token(root), 'Content-Type': 'application/json'})
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')
    if not exists(root) or not running(root):
        return None
    status, started = ask('POST', route, body)
    if status != 200:
        raise ValueError(started.get('error') or f'The endpoint answered {status}')
    out(f'The project endpoint runs operation {started["operation"]}; its output:')
    shown = 0
    while True:
        try:
            status, found = ask('GET', f'/_/project/operation?id={started["operation"]}')
        except (urllib.error.URLError, OSError):
            out('The endpoint restarted (an upgrade restarts it on the new Evlet); see evlet project and the operation log.')
            return True
        for line in found.get('output', [])[shown:]:
            out(line)
        shown = len(found.get('output', []))
        if found.get('result') in ('ok', 'failed'):
            if found.get('error'):
                out('Failed: ' + found['error'])
            return found['result'] == 'ok'
        time.sleep(poll)


# ---------- production: the Caddy site and the one systemd unit ----------

def service_files(root, account):
    """The endpoint's systemd unit and Caddy site, for production. The unit runs evlet endpoint, which runs every
    instance; it is the project's only unit."""
    from .setup import python_in
    root = Path(root)
    settings = read_settings(root)
    if not re.fullmatch(r'[A-Za-z0-9._/@+-]+', str(root)) or not re.fullmatch(r'[a-z_][a-z0-9_-]*', account):
        raise ValueError('The project path and user may only use letters, digits and . _ / @ + -; move the project or choose another user')
    home = any(root.absolute().is_relative_to(base) for base in ('/home', '/root', '/run/user'))
    unit = f'''[Unit]
Description=Evlet project endpoint {root}
After=network.target

[Service]
User={account}
WorkingDirectory={root}
ExecStart={python_in(root)} -P -m evlet endpoint --project {root}
Restart=on-failure
UMask=0077
EnvironmentFile=-/etc/evlet/project.env
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
{'' if home else 'ProtectHome=read-only' + chr(10)}ReadWritePaths={root}

[Install]
WantedBy=multi-user.target
'''
    site = f'''{settings["host"]} {{
\tencode zstd gzip
\treverse_proxy 127.0.0.1:{settings["port"]}
}}
'''
    return unit, site


def install_steps(root, unit, site):
    """The privileged steps for production, in the format of evlet.cli.site.run_privileged."""
    from . import site as sites
    from .caddy import global_options
    deploy = Path(root) / 'deploy'
    unit_target = Path(sites.SYSTEMD_DIR) / f'{UNIT}.service'
    site_target = Path(sites.CADDY_DIR) / f'{UNIT}.caddy'
    # Caddy's global options, once: the admin API on loopback and the one on-demand TLS ask (docs/project-endpoint.md#caddy).
    options_target, options = Path(sites.CADDY_DIR) / sites.GLOBAL_FILE, global_options(root)
    return [
        ('install', str(deploy / f'{UNIT}.service'), str(unit_target), unit),
        ('install', str(deploy / f'{UNIT}.caddy'), str(site_target), site),
        ('install', str(deploy / sites.GLOBAL_FILE), str(options_target), options),
        ('import', str(site_target)),
        ('import-first', str(options_target), options),
        ['sudo', 'systemctl', 'daemon-reload'],
        ['sudo', 'systemctl', 'enable', '--now', UNIT],
        ['sudo', 'systemctl', 'reload', 'caddy'],
    ]


def write_service_files(root, account):
    from . import proxy, site as sites
    from .caddy import global_options
    unit, site = service_files(root, account)
    deploy = Path(root) / 'deploy'
    deploy.mkdir(mode=0o755, exist_ok=True)
    proxy.write(deploy / f'{UNIT}.service', unit)
    proxy.write(deploy / f'{UNIT}.caddy', site)
    proxy.write(deploy / sites.GLOBAL_FILE, global_options(root))
    return unit, site
