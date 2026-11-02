"""Run from the checkout with uvicorn examples.playground.app:create_app --factory.

The customer portal (portal/) is a static frontend served at http://portal.localhost:8000.
Portal users sign in there only, and see the customer they are linked to and its invoices:

    user = app.business.auth.create_user(username='acme-buyer', password=..., role='portal')
    app.business.auth.link_user(user.id, Customer, customer_id)
"""

from pathlib import Path

from evlet import (Action, ApiScope, AppDefinition, Application, ChildTable, EventStore, Frontend, Host, JobQueue, LocalAuth,
                   Policy, Rule, Task, ValidationError, own_customer)
from evlet.routing import resolve_routes
from .models import Customer, Invoice, Project, ProjectTask, registry
from .migrate import configured_database
from .settings import ProjectPreferences, ProjectSettings
from .ui import playground_ui

portal = Frontend('portal', kind='static', dist='portal', path='/portal', title='Customer portal', auth='portal', api=('playground.portal',))
PORTAL_RULES = {
    ('portal', Customer): Rule(('read',), read_fields=('name', 'email'), scope=own_customer('id')),
    ('portal', Invoice): Rule(('read',), read_fields=('customer', 'reference', 'total', 'status', 'posting_date'), scope=own_customer()),
}


def playground_routes(port=8000):
    """The portal on its own host, and at /portal on the main host too."""
    definition = AppDefinition(frontends=(portal,), api_scopes=(ApiScope('portal', models=(Customer, Invoice)),))
    return resolve_routes([('playground', definition)], None, {'hosts': (Host('portal.localhost', root=portal, also_on_primary=True),)},
                          port=port, roots={'playground': Path(__file__).parent})


def start_project(tx, user, project):
    if project.status != 'planned':
        raise ValidationError({'_form': 'Only a planned project can be started'})
    tx.update(Project, project.id, status='active')


def complete_project(tx, user, project):
    if project.status != 'active':
        raise ValidationError({'_form': 'Start the project before completing it'})
    tasks = tx.list(ProjectTask, filters={'project': project.id}, limit=100)
    if not tasks or any(not task.done for task in tasks):
        raise ValidationError({'_form': 'Complete every task before completing the project'})
    tx.update(Project, project.id, status='done', progress=100)


def create_app():
    database = configured_database()
    routes = playground_routes(8000)
    auth = LocalAuth(database, origins=(*routes.origins(), 'http://127.0.0.1:5173', 'http://localhost:5173',
                                       'http://127.0.0.1:4173', 'http://localhost:4173'),
                     realms=routes.realms(), secure_cookies=False, allow_setup=True)
    return build_app(database, auth, routes=routes)


def refresh_progress(tx, job, payload):
    project = tx.get(Project, payload['project_id'])
    tasks = tx.list(ProjectTask, filters={'project': project.id}, limit=100)
    progress = round(100 * sum(task.done for task in tasks) / len(tasks)) if tasks else 0
    tx.update(Project, project.id, progress=progress)


def build_app(database, auth, *, storage=None, tenant='local', routes=None):
    events = EventStore(database)
    policy = Policy(registry, rules=PORTAL_RULES)
    jobs = JobQueue(database, (Task('project_progress', refresh_progress),), tenant=tenant, events=events, policy=policy)
    def queue_progress(tx, user, project):
        return jobs.enqueue(tx, 'project_progress', {'project_id': project.id}, user=user, target=(Project, project.id))
    app = Application(database, name="Evlet Playground", playground=True, ui=playground_ui(), auth=auth, policy=policy, routes=routes,
                      storage=storage, jobs=jobs, events=events, children=(ChildTable(Project, 'tasks', ProjectTask, 'project', 'Tasks'),),
                      actions=(Action(Project, 'start', 'Start project', start_project),
                               Action(Project, 'complete', 'Complete project', complete_project),
                               Action(Project, 'refresh_progress', 'Recalculate progress', queue_progress)),
                      attachments=(Project,))
    # Settings > Projects: people who can read projects see it; editors and admins change it.
    app.business.settings.register('projects', ProjectSettings, ProjectPreferences, (Project,))
    return app
