"""Run from the checkout with uvicorn examples.playground.app:create_app --factory.

The customer portal (portal/) is a static frontend served at http://portal.localhost:8000.
Portal users sign in there only, and see the customer they are linked to and its invoices:

    user = app.business.auth.create_user(username='acme-buyer', password=..., role='portal')
    app.business.auth.link_user(user.id, Customer, customer_id)
"""

import os
from pathlib import Path

from evlet import (AI, Action, ApiScope, AppDefinition, Application, ChildTable, DevSignIn, EventStore, Frontend, Host, JobQueue, LocalAuth,
                   Policy, RecordId, Rule, Security, Sum, Task, ValidationError, own_customer, set_currency_display)
from evlet import api
from evlet.routing import resolve_routes
from evlet.services import Endpoint
from .models import Customer, Invoice, LedgerEntry, PointBalance, PointMove, Project, ProjectTask, registry
from .migrate import configured_database
from .settings import ProjectPreferences, ProjectSettings
from .prints import PRINTS, REPORTS as PRINTED_REPORTS
from .reports import REPORTS
from .slips import SLIPS

portal = Frontend('portal', kind='static', dist='portal', path='/portal', title='Customer portal', auth='portal', api=('playground.portal',))
PORTAL_RULES = {
    ('portal', Customer): Rule(('read',), read_fields=('customer_name', 'title', 'email'), scope=own_customer('id')),
    ('portal', Invoice): Rule(('read',), read_fields=('customer', 'name', 'title', 'reference', 'total', 'status', 'posting_date'), scope=own_customer()),
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


def post_invoice(tx, user, invoice):
    """Post to ledger: the only writer of ledger entries, since LedgerEntry has Meta.api = "read". An
    invoice or entry dated in a closed period (Controls, Periods) is refused with the lock date."""
    if invoice.posting_date is None:
        raise ValidationError({'posting_date': 'Set the posting date before posting'})
    if tx.count(LedgerEntry, filters={'invoice': invoice.id}):
        raise ValidationError({'_form': 'This invoice is posted already'})
    tx.create(LedgerEntry, invoice=invoice.id, customer=invoice.customer, posting_date=invoice.posting_date,
              amount=invoice.total, memo=invoice.reference)
    tx.update(Invoice, invoice.id, status='sent')


def award_points(tx, user, customer):
    """Award points: one movement in the points ledger and the customer's total in the balance cache, in the
    same transaction. The lock keeps two awards for one customer from reading the same total."""
    from datetime import date
    tx.lock(('points', customer.id))
    tx.create(PointMove, customer=customer.id, on_date=date.today(), reason='Awarded from the customer page', points=10)
    [balance] = tx.list(PointBalance, filters={'customer': customer.id}) or [None]
    if balance is None:
        tx.create(PointBalance, customer=customer.id, points=10)
    else:
        tx.update(PointBalance, balance.id, points=balance.points + 10)


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


# Customers each step of Rebuild points balances handles. Small, so the Playground shows several steps.
REBUILD_BATCH = 20


def rebuild_points(tx, job, payload):
    """Rebuild points balances: a resumable job over the points ledger. Each step runs in its own transaction,
    recomputes the balances of the next REBUILD_BATCH customers from their movements and returns the last
    customer done. That checkpoint commits with the step, so a stopped worker resumes after it."""
    customers = tx.list(Customer, filters={'id__gt': job.checkpoint} if job.checkpoint else None, order_by='id', limit=REBUILD_BATCH)
    ids = [customer.id for customer in customers]
    if ids:
        tx.lock(*(('points', customer_id) for customer_id in ids))
    # One set-based pass: the movement totals of these customers, a page of groups at a time. A trusted
    # session's `in` takes long lists and its paged aggregate walks any number of groups (docs/measures.md).
    totals, after = {}, None
    while ids and (page := tx.aggregate(PointMove, measures={'points': Sum('points')}, group_by='customer',
                                        filters={'customer__in': ids}, limit=500, after=after)):
        totals.update((row['customer'], row['points']) for row in page)
        after = page[-1]
    balances = {balance.customer: balance for balance in tx.list(PointBalance, filters={'customer__in': ids}, limit=REBUILD_BATCH)} if ids else {}
    for customer_id in ids:
        points, balance = totals.get(customer_id, 0), balances.get(customer_id)
        if balance is None and points:
            tx.create(PointBalance, customer=customer_id, points=points)
        elif balance is not None and balance.points != points:
            tx.update(PointBalance, balance.id, points=points)
    done = tx.count(Customer, filters={'id__lte': customers[-1].id}) if customers else tx.count(Customer)
    job.progress(done, tx.count(Customer), f'Up to customer {customers[-1].id}' if customers else 'All customers')
    return str(customers[-1].id) if len(customers) == REBUILD_BATCH else None


@api(method='GET', permission=(Project, 'read'), tool=True)
def project_overview(context, project: RecordId):
    """One project at a glance: its status, progress, budget and how many of its tasks are done."""
    record = context.get(Project, project)
    tasks = getattr(record, 'tasks', ())
    return {'project': record.title, 'status': record.status, 'progress': record.progress, 'budget': getattr(record, 'budget', None),
            'tasks': len(tasks), 'done': sum(1 for task in tasks if task.done)}


# Business services offered to AI assistants through the Tool Engine (list_services and call_service).
SERVICES = (Endpoint('playground.project_overview', project_overview, 'GET', (Project, 'read'), tool=True),)


# A placeholder image symbol for the demo: a plain coin drawn for Evlet, not any currency's real symbol.
# Upload the official UAE dirham symbol from the Central Bank of the UAE in Controls, Currencies.
DEMO_SYMBOL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAABIElEQVR42s2Xyw2DMAyGO0CVEcgsGYER2IIzrJBT7izAFKirZAIkpNYHV0LIBqehsg//JSTxF79IHk3TPDRVuqAFRdACyqANlXEs4pxbARxoRCNvoTKucbUAXaFhCqT7FSBWGD4qlgJMFxu+QAk0oBKOna2ZpADcyVc05k+85nHOKvUEFXNq4XxhmAKZmb06DsAxCZcq6jwxiekogJE5eW2zoTwxUgCZiLm/AcATOZGPAC1BOVxsHEA9KlzMHYj92z0Alfnc6Z+MW2f8xnmBrIjvhIWo85KYSnLm2CeWPUAWZn4QdL0grIi8B9iE8e8FAL0wDzZTAOohUE9C9TJUb0TqrdjEz0j9d6x+ITFxJTNxKTVxLTfxMDHxNDPxOP2rPhI9DX76cVPMAAAAAElFTkSuQmCC'


def show_currencies(app):
    """The demo shows AED with an image symbol before the amount, in the text colour (Controls, Currencies)."""
    return set_currency_display(app, {'AED': {'image': DEMO_SYMBOL, 'position': 'prefix', 'space': True, 'tint': True}}, actor='playground')


def build_app(database, auth, *, storage=None, tenant='local', routes=None, ai=None):
    events = EventStore(database)
    policy = Policy(registry, rules=PORTAL_RULES)
    jobs = JobQueue(database, (Task('project_progress', refresh_progress), Task('rebuild_points', rebuild_points, resumable=True)),
                    tenant=tenant, events=events, policy=policy)
    def queue_progress(tx, user, project):
        return jobs.enqueue(tx, 'project_progress', {'project_id': project.id}, user=user, target=(Project, project.id))
    def queue_rebuild(tx, user, customer):
        # Rebuilds every customer's balance; Controls > Jobs shows its progress.
        return jobs.enqueue(tx, 'rebuild_points', {}, user=user)
    app = Application(database, name="Evlet Playground", playground=True, auth=auth, policy=policy, routes=routes,
                      storage=storage, jobs=jobs, events=events, children=(ChildTable(Project, 'tasks', ProjectTask, 'project', 'Tasks'),),
                      actions=(Action(Project, 'start', 'Start project', start_project),
                               Action(Project, 'complete', 'Complete project', complete_project),
                               Action(Project, 'refresh_progress', 'Recalculate progress', queue_progress),
                               Action(Invoice, 'post', 'Post to ledger', post_invoice),
                               Action(Customer, 'award_points', 'Award 10 points', award_points),
                               Action(Customer, 'rebuild_points', 'Rebuild all points balances', queue_rebuild, roles=('admin',))),
                      attachments=(Project,), slips=SLIPS, reports=(*REPORTS, *PRINTED_REPORTS), apis=SERVICES, ai=ai, prints=PRINTS)
    # Settings > Projects: people who can read projects see it; editors and admins change it.
    app.business.settings.register('projects', ProjectSettings, ProjectPreferences, (Project,))
    return app
