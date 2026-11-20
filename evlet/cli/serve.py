"""Serve packaged browser assets with the existing ASGI application."""

import asyncio
from contextlib import closing
import logging
import os
from pathlib import Path

from uvicorn import Server
from uvicorn.supervisors.basereload import BaseReload

from ..instances import load_runtime
from ..tenancy import TenantApplication
from .project import child, instance_guard


class ManagedRuntime:
    """The instance application behind ASGI lifespan: opens it at startup, closes it at shutdown.

    With development=True (evlet dev only) a failed start does not end the server: every page answers 503 with the
    error and the commands that fix it, and the start is tried again once `retry_key()` changes (evlet migrate
    rewrote the database, for instance). Source edits restart the whole process through the reloader."""

    def __init__(self, factory, guard=None, *, jobs=False, debug=False, development=False, retry_key=None):
        self.factory, self.guard, self.jobs, self.debug = factory, guard, jobs, debug
        self.development, self.retry_key = development, retry_key
        self.app = None
        self.stopped = None
        self.failure = None
        self.resources = None
        self.worker = None
        self._failed_key = None
        self._opening = None

    def stop(self):
        if self.stopped is not None:
            self.stopped.set()
            jobs = getattr(getattr(self.app, 'business', None), 'jobs', None)
            if jobs is not None:
                jobs.wake()
        if self.app is not None:
            self.app.stop()

    async def open(self):
        """Take the instance lock, load and initialize the application. On failure everything is released."""
        from contextlib import ExitStack
        from ..instances import activate_generation
        stack = ExitStack()
        try:
            if self.guard:
                stack.enter_context(self.guard())
            app = self.factory()
            stack.callback(app.database.close)
            stack.enter_context(activate_generation(app))
            await asyncio.to_thread(app.initialize)
        except BaseException:
            try:
                stack.close()
            except Exception:
                # Keep the start failure as the reported error; a failed log flush is logged beside it.
                logging.getLogger(__name__).exception('Closing the database after a failed start')
            raise
        self.app, self.resources, self.failure = app, stack, None
        if self.jobs and self.stopped is not None and self.app.business.jobs:
            self.worker = asyncio.create_task(self.work(self.stopped))

    def report(self, exc):
        logger = logging.getLogger(__name__)
        if self.debug or not isinstance(exc, (ValueError, OSError)):
            logger.exception('Instance lifecycle failed')
        else:
            logger.error('%s', exc)

    def fail(self, exc):
        """evlet dev: keep serving an error page instead of ending the server."""
        import traceback
        self.report(exc)
        from ..builds import source_path_text
        detail = ''.join(traceback.format_exception(exc)) if self.debug or not isinstance(exc, (ValueError, OSError)) else ''
        detail = source_path_text(detail)
        message = f'{type(exc).__name__}: {exc}' if not isinstance(exc, (ValueError, OSError)) else str(exc)
        self.failure = (source_path_text(message), detail)
        self._failed_key = self.retry_key() if self.retry_key else None
        logging.getLogger(__name__).warning('evlet dev keeps running and shows this error in the browser. Fix the source to reload, '
                                            'or run the command above; the page retries by itself.')

    async def retry(self):
        """Start again after a failure when what the start depends on outside the source changed."""
        if self._opening is None:
            self._opening = asyncio.Lock()
        async with self._opening:
            if self.app is not None or self.retry_key is None or self.retry_key() == self._failed_key:
                return
            try:
                await self.open()
            except Exception as exc:
                self.fail(exc)
            else:
                logging.getLogger(__name__).warning('Instance started after the earlier error')

    async def error_page(self, scope, send):
        import html
        message, detail = self.failure
        if scope['type'] != 'http':
            await send({'type': 'websocket.close', 'code': 1011})
            return
        accept = dict(scope.get('headers') or ()).get(b'accept', b'')
        if b'text/html' in accept:
            body = (ERROR_PAGE.format(message=html.escape(message), detail=f'<pre>{html.escape(detail)}</pre>' if detail else '')).encode()
            kind = b'text/html; charset=utf-8'
        else:
            import json
            body = json.dumps({'error': message}).encode()
            kind = b'application/json'
        await send({'type': 'http.response.start', 'status': 503,
                    'headers': [(b'content-type', kind), (b'cache-control', b'no-store'), (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'lifespan':
            if self.app is None and self.failure is not None:
                await self.retry()
            if self.app is None:
                if self.failure is not None:
                    await self.error_page(scope, send)
                    return
                raise RuntimeError('Run the instance server with ASGI lifespan enabled')
            await self.app(scope, receive, send)
            return
        await receive()
        started = False
        try:
            self.stopped = asyncio.Event()
            try:
                await self.open()
            except Exception as exc:
                if not self.development:
                    raise
                self.fail(exc)
            try:
                started = True
                await send({'type': 'lifespan.startup.complete'})
                await receive()
            finally:
                self.stop()
                if self.worker:
                    await self.worker
                if self.resources is not None:
                    self.resources.close()
            await send({'type': 'lifespan.shutdown.complete'})
        except Exception as exc:
            self.report(exc)
            await send({'type': 'lifespan.shutdown.failed' if started else 'lifespan.startup.failed', 'message': str(exc)})

    async def work(self, stopped):
        while not stopped.is_set():
            try:
                await asyncio.to_thread(self.app.business.jobs.run_one, active=lambda: not stopped.is_set())
            except Exception:
                logging.getLogger(__name__).exception('Job pass failed')
            # Half a second between passes, or less: a committed transaction that queued a job wakes it
            # (Context.enqueue), and so does stopping.
            await asyncio.to_thread(self.app.business.jobs.wait, 0.5)


ERROR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="2">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Evlet dev: start failed</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:60rem;padding:0 1rem;color:#222;background:#fff}}
pre{{white-space:pre-wrap;background:#f4f4f4;padding:1rem;border-radius:6px;overflow:auto}}
@media (prefers-color-scheme:dark){{body{{color:#eee;background:#161616}}pre{{background:#262626}}}}</style></head>
<body><h1>The instance did not start</h1><pre>{message}</pre>{detail}
<p>evlet dev is still running. Save a fix in your app and this page reloads, or run the command named above in
another terminal; the page checks again every two seconds.</p></body></html>"""


class InstanceServer(Server):
    async def shutdown(self, sockets=None):
        # Uvicorn drains HTTP connections before sending lifespan.shutdown.
        app = self.config.loaded_app
        while app is not None:
            if isinstance(app, (ManagedRuntime, TenantApplication)):
                app.stop()
                break
            app = getattr(app, 'app', None)
        await super().shutdown(sockets=sockets)


class SourceReload(BaseReload):
    """Watch all build inputs, including additions and deletions, without extra dependencies."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reloader_name = 'Evlet source watcher'
        self.snapshot = self.inputs()

    def inputs(self):
        result = {}
        excluded = {'.git', '.venv', '.evlet', '__pycache__', 'node_modules', 'dist', 'build'}
        for root in self.config.reload_dirs:
            for path in Path(root).rglob('*'):
                if any(part in excluded for part in path.relative_to(root).parts): continue
                if path.suffix in ('.py', '.toml', '.json', '.ts', '.tsx', '.js', '.jsx', '.css') and path.is_file():
                    stat = path.stat()
                    result[path] = (stat.st_mtime_ns, stat.st_size)
        return result

    def should_restart(self):
        self.pause()
        current = self.inputs()
        changes = [path for path in current.keys() | self.snapshot.keys() if current.get(path) != self.snapshot.get(path)]
        self.snapshot = current
        return changes or None


def run_server(application, **options):
    from uvicorn import Config
    config = Config(application, **options)
    server = InstanceServer(config)
    if config.should_reload:
        sock = config.bind_socket()
        try:
            SourceReload(config, target=server.run, sockets=[sock]).run()
        finally:
            sock.close()
    else:
        server.run()
        if not server.started:
            raise SystemExit(3)


def development_factory():
    root = Path(os.environ['EVLET_CLI_PROJECT'])
    name = os.environ['EVLET_CLI_INSTANCE']
    port = int(os.environ['EVLET_CLI_PORT'])
    from ..devlink import DevSignIn
    path = child(root, 'instances', name)
    show_source_paths()
    runtime = ManagedRuntime(lambda: load_runtime(root, name, port=port, development=True),
                             lambda: instance_guard(path, serving=True), jobs=True, debug=os.environ.get('EVLET_CLI_DEBUG') == '1',
                             development=True, retry_key=lambda: data_state(path))
    # Development only: one-time sign-in links at /_dev/signin. serve() below never adds them.
    return DevSignIn(runtime, announce=os.environ.get('EVLET_CLI_LINK') == '1', user=os.environ.get('EVLET_CLI_LINK_USER') or None)


def data_state(path):
    """What changes when evlet migrate, restore or install rewrites the instance's database, for retrying a failed start."""
    result = []
    for name in ('data/database.sqlite3', 'data/database.sqlite3-wal', 'apps.lock', '.install-journal.json'):
        try:
            stat = (path / name).stat()
            result.append((name, stat.st_ino, stat.st_mtime_ns, stat.st_size))
        except OSError:
            result.append((name, None))
    return tuple(result)


def show_source_paths():
    """evlet dev: tracebacks name the app's own files, not the build generation copies the server imports."""
    from ..builds import SOURCES, source_path_text
    previous = logging.getLogRecordFactory()
    if getattr(previous, 'evlet_source_paths', False):
        return
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if record.exc_info and record.exc_info[0] is not None and SOURCES:
            record.exc_text = source_path_text(logging.Formatter().formatException(record.exc_info))
        return record
    factory.evlet_source_paths = True
    logging.setLogRecordFactory(factory)


def framework_source():
    """The Evlet package folder when it runs from a source checkout or editable install, else None."""
    package = Path(__file__).resolve().parent.parent
    if {'site-packages', 'dist-packages'} & set(package.parts):
        return None
    return package


def declares_primary(path):
    """Whether instances/<name>/routes.py assigns primary, read without running it or importing apps."""
    import ast
    target = path / 'routes.py'
    if not target.is_file() or target.is_symlink():
        return False
    for node in ast.parse(target.read_text(encoding='utf-8')).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(item, ast.Name) and item.id == 'primary' for item in targets):
            return True
    return False


def development_dirs(root, path):
    """Apps and the instance folder; plus Evlet itself when edited in place, so framework changes reload too."""
    dirs = [str(root / 'apps'), str(path)]
    if (package := framework_source()) is not None:
        dirs.append(str(package))
    return dirs


def proxy_warning(root, name, domain, port=8000):
    """One line when the Caddy site evlet setup installed for this instance refuses a public /_/ route, else None.

    It only reads the installed file, and only when it can; it never changes it."""
    import shlex
    from ..app import PUBLIC_PATHS
    from ..instances import read_lock
    from . import proxy, site
    target = site.installed(name)[1]
    try:
        text = target.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None
    refused = proxy.caddy_refuses(text, PUBLIC_PATHS)
    if not refused:
        return None
    try:
        apps = ','.join(read_lock(child(root, 'instances', name))['apps']) or 'APP'
    except (OSError, ValueError, KeyError):
        apps = 'APP'
    command = ['evlet', 'setup', str(root), '--instance', name, '--app', apps, '--host', domain or 'HOST', *(['--port', str(port)] if port != 8000 else []), '--apply']
    return (f'{target} blocks {", ".join(refused)}, so MCP clients and mailbox sign-in cannot reach this instance. '
            f'Run {shlex.join(command)} to update it (add --ip with the public address if DNS checks fail).')


def administrator_hint(path, name):
    """One line when the built-in Administrator has no password yet (or will not, on a new database), else None.
    evlet serve prints it instead of a sign-in link; it only reads the database."""
    from .. import qb
    from ..adapter import DatabaseError
    from ..writes import read_file
    database = path / 'data/database.sqlite3'
    if database.is_file():
        try:
            with closing(read_file(database)) as connection:
                if not connection.table_exists('evlet_users'):
                    row = None
                else:
                    row = connection.run(qb.select('evlet_users', 'password')
                                         .where(qb.in_('username', ('admin', 'administrator')), qb.eq('role', 'admin'))
                                         .order_by(qb.desc(qb.eq('username', 'admin')))).one()
                    if row is None:
                        return None  # A database from before the Administrator; evlet migrate adds it.
        except DatabaseError:
            return None
        if row is not None and row[0] != '':
            return None
    return f'The built-in Administrator has no password yet. Set one with: evlet users set-password {name} admin'


LOOPBACK = ('127.0.0.1', 'localhost', '::1')


def plain_http_warning(host, port, domain):
    """The warning evlet serve prints when it listens beyond loopback over plain HTTP."""
    return (f'WARNING: evlet serve listens on {host}:{port} over plain HTTP. Passwords, session cookies and two-step secrets '
            f'cross the network unencrypted unless an HTTPS proxy in front is the only way in. Prefer --host 127.0.0.1 behind '
            f'the Caddy site evlet setup --host {domain or "HOSTNAME"} writes, or pass --ssl-certfile and --ssl-keyfile.')


def serve(root, name, *, host='127.0.0.1', port=8000, development=False, jobs=False, debug=False, link=True, user=None,
          ssl_certfile=None, ssl_keyfile=None):
    from ..instances import read_instance
    path = child(root, 'instances', name)
    config = read_instance(path)
    if not development and (warning := proxy_warning(root, name, config.domain, port)):
        logging.getLogger(__name__).warning('%s', warning)
    primary = declares_primary(path)
    if development and (host not in LOOPBACK or config.domain or primary):
        raise ValueError('Development serving requires a local instance and loopback host')
    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise ValueError('Pass both --ssl-certfile and --ssl-keyfile, or neither')
    if development and ssl_certfile:
        raise ValueError('evlet dev serves loopback over plain HTTP; --ssl-certfile is for evlet serve')
    public = host not in LOOPBACK
    if public and not config.domain and not primary:
        raise ValueError('Configure an HTTPS domain before binding an instance beyond loopback')
    if public and not ssl_certfile:
        import sys
        print(plain_http_warning(host, port, config.domain), file=sys.stderr, flush=True)
        logging.getLogger(__name__).warning('Serving beyond loopback over plain HTTP; see the warning above')
    if development:
        values = {'EVLET_CLI_PROJECT': str(root), 'EVLET_CLI_INSTANCE': name, 'EVLET_CLI_PORT': str(port), 'EVLET_CLI_DEBUG': '1' if debug else '0',
                  'EVLET_CLI_LINK': '1' if link else '0', 'EVLET_CLI_LINK_USER': user or ''}
        previous = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        try:
            run_server('evlet.cli.serve:development_factory', factory=True, host=host, port=port,
                        reload=True, reload_dirs=development_dirs(root, path), lifespan='on', proxy_headers=False)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    else:
        if (hint := administrator_hint(path, name)) is not None:
            print(hint, flush=True)
        application = ManagedRuntime(lambda: load_runtime(root, name, port=port, public=public),
                                     lambda: instance_guard(path, serving=True), jobs=jobs, debug=debug)
        tls = {'ssl_certfile': str(ssl_certfile), 'ssl_keyfile': str(ssl_keyfile)} if ssl_certfile else {}
        run_server(application, host=host, port=port, lifespan='on', proxy_headers=False, **tls)
