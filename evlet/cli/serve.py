"""Serve packaged browser assets with the existing ASGI application."""

import asyncio
import logging
import os
from pathlib import Path

from ..instances import load_runtime
from .project import child, instance_guard


class ManagedRuntime:
    def __init__(self, factory, guard=None, *, jobs=False):
        self.factory, self.guard, self.jobs = factory, guard, jobs
        self.app = None

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'lifespan':
            if self.app is None:
                raise RuntimeError('Run the instance server with ASGI lifespan enabled')
            await self.app(scope, receive, send)
            return
        from contextlib import nullcontext
        with self.guard() if self.guard else nullcontext():
            try:
                self.app = self.factory()
                await asyncio.to_thread(self.app.initialize)
            except Exception as exc:
                logging.getLogger(__name__).exception('Instance startup failed')
                await receive()
                await send({'type': 'lifespan.startup.failed', 'message': str(exc)})
                return
            stopped = asyncio.Event()
            worker = asyncio.create_task(self.work(stopped)) if self.jobs and self.app.business.jobs else None
            try:
                await receive()
                await send({'type': 'lifespan.startup.complete'})
                await receive()
            finally:
                stopped.set()
                if worker:
                    await worker
                self.app.database.close()
            await send({'type': 'lifespan.shutdown.complete'})

    async def work(self, stopped):
        while not stopped.is_set():
            try:
                await asyncio.to_thread(self.app.business.jobs.run_one)
            except Exception:
                logging.getLogger(__name__).exception('Job pass failed')
            try:
                await asyncio.wait_for(stopped.wait(), timeout=0.5)
            except TimeoutError:
                pass


def development_factory():
    root = Path(os.environ['EVLET_CLI_PROJECT'])
    name = os.environ['EVLET_CLI_INSTANCE']
    port = int(os.environ['EVLET_CLI_PORT'])
    return ManagedRuntime(lambda: load_runtime(root, name, port=port, development=True),
                          lambda: instance_guard(child(root, 'instances', name), serving=True), jobs=True)


def serve(root, name, *, host='127.0.0.1', port=8000, development=False, jobs=False):
    import uvicorn
    from ..instances import read_instance
    path = child(root, 'instances', name)
    config = read_instance(path)
    if development and (host not in ('127.0.0.1', 'localhost') or config.domain):
        raise ValueError('Development serving requires a local instance and loopback host')
    if host not in ('127.0.0.1', 'localhost') and not config.domain:
        raise ValueError('Configure an HTTPS domain before binding an instance beyond loopback')
    if development:
        values = {'EVLET_CLI_PROJECT': str(root), 'EVLET_CLI_INSTANCE': name, 'EVLET_CLI_PORT': str(port)}
        previous = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        try:
            uvicorn.run('evlet.cli.serve:development_factory', factory=True, host=host, port=port,
                        reload=True, reload_dirs=[str(root / 'apps'), str(path)], lifespan='on', proxy_headers=False)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    else:
        application = ManagedRuntime(lambda: load_runtime(root, name, port=port),
                                     lambda: instance_guard(path, serving=True), jobs=jobs)
        uvicorn.run(application, host=host, port=port, lifespan='on', proxy_headers=False)
