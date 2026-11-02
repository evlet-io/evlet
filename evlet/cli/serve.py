"""Serve packaged browser assets with the existing ASGI application."""

import asyncio
import logging
import os
from pathlib import Path

from uvicorn import Server
from uvicorn.supervisors.basereload import BaseReload

from ..instances import load_runtime
from ..tenancy import TenantApplication
from .project import child, instance_guard


class ManagedRuntime:
    def __init__(self, factory, guard=None, *, jobs=False, debug=False):
        self.factory, self.guard, self.jobs, self.debug = factory, guard, jobs, debug
        self.app = None
        self.stopped = None

    def stop(self):
        if self.stopped is not None:
            self.stopped.set()
        if self.app is not None:
            self.app.stop()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'lifespan':
            if self.app is None:
                raise RuntimeError('Run the instance server with ASGI lifespan enabled')
            await self.app(scope, receive, send)
            return
        from contextlib import nullcontext
        await receive()
        started = False
        try:
            with self.guard() if self.guard else nullcontext():
                self.app = self.factory()
                from ..instances import activate_generation
                generation = activate_generation(self.app)
                generation.__enter__()
                try:
                    await asyncio.to_thread(self.app.initialize)
                except Exception:
                    generation.__exit__(None, None, None)
                    self.app.database.close()
                    raise
                self.stopped = asyncio.Event()
                worker = asyncio.create_task(self.work(self.stopped)) if self.jobs and self.app.business.jobs else None
                try:
                    started = True
                    await send({'type': 'lifespan.startup.complete'})
                    await receive()
                finally:
                    self.stop()
                    if worker:
                        await worker
                    self.app.database.close()
                    generation.__exit__(None, None, None)
                await send({'type': 'lifespan.shutdown.complete'})
        except Exception as exc:
            logger = logging.getLogger(__name__)
            if self.debug or not isinstance(exc, (ValueError, OSError)):
                logger.exception('Instance lifecycle failed')
            else:
                logger.error('%s', exc)
            await send({'type': 'lifespan.shutdown.failed' if started else 'lifespan.startup.failed', 'message': str(exc)})

    async def work(self, stopped):
        while not stopped.is_set():
            try:
                await asyncio.to_thread(self.app.business.jobs.run_one, active=lambda: not stopped.is_set())
            except Exception:
                logging.getLogger(__name__).exception('Job pass failed')
            try:
                await asyncio.wait_for(stopped.wait(), timeout=0.5)
            except TimeoutError:
                pass


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
    return ManagedRuntime(lambda: load_runtime(root, name, port=port, development=True),
                          lambda: instance_guard(child(root, 'instances', name), serving=True), jobs=True, debug=os.environ.get('EVLET_CLI_DEBUG') == '1')


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


def serve(root, name, *, host='127.0.0.1', port=8000, development=False, jobs=False, debug=False):
    from ..instances import read_instance
    path = child(root, 'instances', name)
    config = read_instance(path)
    primary = declares_primary(path)
    if development and (host not in ('127.0.0.1', 'localhost') or config.domain or primary):
        raise ValueError('Development serving requires a local instance and loopback host')
    public = host not in ('127.0.0.1', 'localhost')
    if public and not config.domain and not primary:
        raise ValueError('Configure an HTTPS domain before binding an instance beyond loopback')
    if development:
        values = {'EVLET_CLI_PROJECT': str(root), 'EVLET_CLI_INSTANCE': name, 'EVLET_CLI_PORT': str(port), 'EVLET_CLI_DEBUG': '1' if debug else '0'}
        previous = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        try:
            run_server('evlet.cli.serve:development_factory', factory=True, host=host, port=port,
                        reload=True, reload_dirs=[str(root / 'apps'), str(path)], lifespan='on', proxy_headers=False)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    else:
        application = ManagedRuntime(lambda: load_runtime(root, name, port=port, public=public),
                                     lambda: instance_guard(path, serving=True), jobs=jobs, debug=debug)
        run_server(application, host=host, port=port, lifespan='on', proxy_headers=False)
