import os
import subprocess
import sys

from evlet import User
from examples.playground.models import Project, ProjectTask
from examples.playground.tenants import tenant_app
from tests.test_business import hashed
from tests.test_tenancy import tenants, login, call


def test_worker_and_event_routes_keep_tenant_identity_and_disable_state(tenants):
    catalog, router = tenants
    apps, jobs, projects = {}, {}, {}
    for slug in ('alpha', 'beta'):
        app = apps[slug] = tenant_app(catalog.get(slug)); app.initialize()
        project = projects[slug] = app.database.create(Project, name=slug)
        app.database.create(ProjectTask, project=project.id, title=slug, done=True)
        with app.database.transaction() as tx:
            jobs[slug] = app.business.jobs.enqueue(tx, 'project_progress', {'project_id': project.id}, user=User(1, 'admin', 'admin'), target=(Project, project.id))
    alpha = login(router, 'alpha'); beta = login(router, 'beta')
    catalog.set_active('beta', False)
    env = {**os.environ, 'EVLET_TENANT_ROOT': str(catalog.root)}
    subprocess.run([sys.executable, '-m', 'examples.playground.worker', '--once'], env=env, check=True, capture_output=True, timeout=10)
    assert apps['alpha'].database.get(Project, projects['alpha'].id).progress == 100
    assert apps['beta'].database.get(Project, projects['beta'].id).progress == 0
    assert apps['beta'].business.jobs.recent()[0]['state'] == 'queued'
    assert call(router, 'alpha.localhost:8000', path='/api/_events?cursor=0', **beta)[0] == 401
    assert call(router, 'alpha.localhost:8000', path='/api/_jobs/1', **beta)[0] == 401
    assert call(router, 'alpha.localhost:8000', path='/api/_events?cursor=0', **alpha)[1]['items'] == [{'model': 'project', 'id': str(projects['alpha'].id), 'deleted': False}]
    catalog.set_active('beta', True)
    subprocess.run([sys.executable, '-m', 'examples.playground.worker', '--once'], env=env, check=True, capture_output=True, timeout=10)
    assert apps['beta'].database.get(Project, projects['beta'].id).progress == 100
    assert apps['alpha'].business.jobs.recent()[0]['attempts'] == 1


def test_router_rejects_a_queue_with_another_tenant_namespace(tenants):
    catalog, _ = tenants
    from evlet import TenantApplication
    def factory(tenant):
        app = tenant_app(tenant)
        app.business.jobs.tenant = 'other'
        return app
    router = TenantApplication(catalog, factory)
    assert call(router, 'alpha.localhost:8000')[0] == 503
