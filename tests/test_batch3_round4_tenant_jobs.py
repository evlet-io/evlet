"""Tenant jobs run inside the API process (PERF-05): job steps share each tenant's write queue with requests."""

import asyncio
import json
import time

import pytest

from evlet import Task, TenantApplication, User
from evlet.deployment import deployment_files
from examples.playground.models import Project, ProjectTask
from examples.playground.tenants import tenant_app
from tests.support import PASSWORD
from tests.test_tenancy import tenants  # noqa: F401 (fixture)


async def call(router, host, method, path, data=None, *, cookie='', csrf=''):
    raw = json.dumps({} if data is None else data).encode()
    events = [{'type': 'http.request', 'body': raw}]
    sent = []

    async def receive():
        return events.pop(0) if events else {'type': 'http.disconnect'}

    async def send(event):
        sent.append(event)
    headers = [(b'host', host.encode()), (b'content-type', b'application/json'), (b'x-evlet-request', b'1'),
               (b'cookie', cookie.encode()), (b'x-csrf-token', csrf.encode())]
    await router({'type': 'http', 'method': method, 'path': path, 'query_string': b'', 'headers': headers, 'client': ('127.0.0.1', 1)}, receive, send)
    status, response_headers = sent[0]['status'], dict(sent[0]['headers'])
    body = sent[1]['body']
    return status, json.loads(body) if body else None, response_headers


class Lifespan:
    """Drive a router's ASGI lifespan the way Uvicorn does."""

    def __init__(self, router):
        self.router, self.inbox, self.outbox = router, asyncio.Queue(), asyncio.Queue()

    async def __aenter__(self):
        self.task = asyncio.create_task(self.router({'type': 'lifespan'}, self.inbox.get, self.outbox.put))
        await self.inbox.put({'type': 'lifespan.startup'})
        assert (await self.outbox.get())['type'] == 'lifespan.startup.complete'
        return self

    async def __aexit__(self, *exc):
        await self.inbox.put({'type': 'lifespan.shutdown'})
        assert (await asyncio.wait_for(self.outbox.get(), 10))['type'] == 'lifespan.shutdown.complete'
        await self.task


async def until(check, seconds=10):
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, 'timed out'
        await asyncio.sleep(0.05)


def queue_progress(catalog, slug):
    app = tenant_app(catalog.get(slug)); app.initialize()
    project = app.database.create(Project, title=slug)
    app.database.create(ProjectTask, project=project.id, title=slug, done=True)
    with app.database.transaction() as tx:
        app.business.jobs.enqueue(tx, 'project_progress', {'project_id': project.id}, user=User(1, 'admin', 'admin'), target=(Project, project.id))
    return app, project


def test_a_queued_job_runs_in_the_api_process_for_each_active_tenant_only(tenants):
    catalog, _ = tenants
    alpha, alpha_project = queue_progress(catalog, 'alpha')
    beta, beta_project = queue_progress(catalog, 'beta')
    catalog.set_active('beta', False)
    router = TenantApplication(catalog, tenant_app, jobs=True)

    async def run():
        async with Lifespan(router):
            await until(lambda: alpha.database.get(Project, alpha_project.id).progress == 100)
            await asyncio.sleep(0.3)
            # A disabled tenant's job stays queued.
            assert beta.business.jobs.recent()[0]['state'] == 'queued'
        assert router._worker is None
    asyncio.run(run())


def test_jobs_follow_the_environment_unless_given(tenants, monkeypatch):
    catalog, _ = tenants
    monkeypatch.delenv('EVLET_TENANT_JOBS', raising=False)
    assert TenantApplication(catalog, tenant_app).jobs is False
    monkeypatch.setenv('EVLET_TENANT_JOBS', '1')
    assert TenantApplication(catalog, tenant_app).jobs is True
    assert TenantApplication(catalog, tenant_app, jobs=False).jobs is False
    with pytest.raises(ValueError):
        TenantApplication(catalog, tenant_app, jobs='yes')


def test_long_job_steps_and_api_saves_share_the_write_queue_without_busy_errors(tenants):
    catalog, _ = tenants
    steps = []

    def slow(tx, job, payload):
        # Holds the tenant's write turn, as a heavy job step would.
        tx.update(Project, payload['project_id'], progress=payload['n'])
        time.sleep(0.25)
        steps.append(payload['n'])

    def factory(tenant):
        app = tenant_app(tenant)
        app.business.jobs.tasks['slow'] = Task('slow', slow, max_attempts=1)
        return app
    setup, project = queue_progress(catalog, 'alpha')
    setup.business.jobs.tasks['slow'] = Task('slow', slow, max_attempts=1)
    with setup.database.transaction() as tx:
        for n in range(1, 5):
            setup.business.jobs.enqueue(tx, 'slow', {'project_id': project.id, 'n': n}, user=User(1, 'admin', 'admin'), target=(Project, project.id))
    router = TenantApplication(catalog, factory, jobs=True)
    host = 'alpha.localhost:8000'

    async def run():
        async with Lifespan(router):
            status, session, headers = await call(router, host, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
            assert status == 200
            auth = {'cookie': headers[b'set-cookie'].decode().split(';')[0], 'csrf': session['csrf']}
            await until(lambda: bool(steps))
            saves = await asyncio.gather(*(call(router, host, 'POST', '/api/project', {'title': f'save {n}'}, **auth) for n in range(8)))
            assert [status for status, _, _ in saves] == [201] * 8, saves
            await until(lambda: len(steps) == 4)
    asyncio.run(run())
    assert {row['state'] for row in setup.business.jobs.recent()} == {'succeeded'}
    assert sorted(steps) == [1, 2, 3, 4]


def test_the_generated_api_unit_runs_jobs_and_the_worker_is_opt_in():
    files = deployment_files(['app.example.com'])
    assert set(files) == {'Caddyfile', 'evlet.service'}
    assert 'Environment=EVLET_TENANT_JOBS=1' in files['evlet.service'] and '--workers 1' in files['evlet.service']
    split = deployment_files(['app.example.com'], separate_worker=True)
    assert 'Environment=EVLET_TENANT_JOBS=0' in split['evlet.service']
    assert ' -m examples.playground.worker' in split['evlet-worker.service'] and 'uvicorn' not in split['evlet-worker.service']
