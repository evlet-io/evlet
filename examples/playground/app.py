"""Run from the checkout with uvicorn examples.playground.app:create_app --factory.

The customer portal (portal/) is a static frontend served at http://portal.localhost:8000.
Portal users sign in there only, and see the customer they are linked to and its invoices:

    user = app.business.auth.create_user(username='acme-buyer', password=..., role='portal')
    app.business.auth.link_user(user.id, Customer, customer_id)
"""

import os
from pathlib import Path

from evlet import (AI, Action, ApiScope, AppDefinition, Application, ChildTable, DevSignIn, EventStore, Frontend, Host, JobQueue, LocalAuth,
                   Policy, Rule, Security, Task, ValidationError, own_customer)
from evlet import api
from evlet.routing import resolve_routes
from evlet.services import Endpoint
from .models import Customer, Invoice, Project, ProjectTask, registry
from .migrate import configured_database
from .settings import ProjectPreferences, ProjectSettings
from .prints import PRINTS, REPORTS
from .slips import SLIPS
from .ui import playground_ui

portal = Frontend('portal', kind='static', dist='portal', path='/portal', title='Customer portal', auth='portal', api=('playground.portal',))
PORTAL_RULES = {
    ('portal', Customer): Rule(('read',), read_fields=('name', 'email'), scope=own_customer('id')),
    ('portal', Invoice): Rule(('read',), read_fields=('customer', 'number', 'reference', 'total', 'status', 'posting_date'), scope=own_customer()),
    # Admin prints whatever it reads; editors print invoices because their Rule names 'print'.
    ('editor', Invoice): Rule(('read', 'create', 'update', 'delete', 'print')),
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
                     realms=routes.realms(), secure_cookies=False, allow_setup=True,
                     # The sign-in rules this instance starts from (the defaults); Controls can only make them stricter.
                     security=Security(session_hours=12, lockout=5, password_min=15))
    return build_app(database, auth, routes=routes, ai=playground_ai())


def playground_ai():
    """Eva, when EVLET_EVA_MODEL names an OpenAI model (for example gpt-4.1-mini). Then turn Eva on in Controls,
    add the OpenAI key under Connectors and give people Allow Eva in Users. Without it, Eva doesn't exist here."""
    model = os.environ.get('EVLET_EVA_MODEL')
    return AI(enabled=True, models={'Standard': model}) if model else None


def create_dev_app():
    """scripts/playground.sh: create_app with one-time development sign-in links at /_dev/signin. Never serve this."""
    return DevSignIn(create_app())


def refresh_progress(tx, job, payload):
    project = tx.get(Project, payload['project_id'])
    tasks = tx.list(ProjectTask, filters={'project': project.id}, limit=100)
    progress = round(100 * sum(task.done for task in tasks) / len(tasks)) if tasks else 0
    tx.update(Project, project.id, progress=progress)


@api(method='GET', permission=(Project, 'read'), tool=True)
def project_overview(context, project: int):
    """One project at a glance: its status, progress, budget and how many of its tasks are done."""
    record = context.get(Project, project)
    tasks = getattr(record, 'tasks', ())
    return {'project': record.name, 'status': record.status, 'progress': record.progress, 'budget': getattr(record, 'budget', None),
            'tasks': len(tasks), 'done': sum(1 for task in tasks if task.done)}


# Business services offered to AI assistants through the Tool Engine (list_services and call_service).
SERVICES = (Endpoint('playground.project_overview', project_overview, 'GET', (Project, 'read'), tool=True),)


def build_app(database, auth, *, storage=None, tenant='local', routes=None, ai=None):
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
                      attachments=(Project,), slips=SLIPS, apis=SERVICES, ai=ai, reports=REPORTS, prints=PRINTS)
    # Settings > Projects: people who can read projects see it; editors and admins change it.
    app.business.settings.register('projects', ProjectSettings, ProjectPreferences, (Project,))
    return app
