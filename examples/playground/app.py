"""Run from the checkout with uvicorn examples.playground.app:create_app --factory.

The customer portal (portal/) is a static frontend served at http://portal.localhost:8000.
Portal users sign in there only, and see the customer they are linked to and its invoices:

    user = app.business.auth.create_user(username='acme-buyer', password=..., role='portal')
    app.business.auth.link_user(user.id, Customer, customer_id)

The public website (site/) is served at http://site.localhost:8000 with routing="pages": about/index.html
is /about/, and a missing page gets site/404.html. That host serves nothing else of the instance.
"""

import json
import os
from pathlib import Path

from evlet import (AI, ApiScope, AppDefinition, Application, ChildTable, DevSignIn, EventStore, Frontend, Host, JobQueue, LocalAuth,
                   Policy, RecordId, Rule, Security, Sum, Task, own_customer, set_currency_display)
from evlet import api, sites
from evlet.routing import resolve_routes
from evlet.services import Endpoint
from .models import Customer, Invoice, PointBalance, PointMove, Project, ProjectTask, registry
from .migrate import configured_database
from .settings import ProjectPreferences, ProjectSettings
from .prints import PRINTS, REPORTS as PRINTED_REPORTS
from .reports import REPORTS
from .prompts import quick_invoice
from .workspaces import WORKSPACES

portal = Frontend('portal', kind='static', dist='portal', path='/portal', title='Customer portal', auth='portal', api=('playground.portal',))
# A public website: auth=None makes its host strict, routing="pages" serves one page per folder,
# and suggested_host is what evlet install offers on an instance with a domain (www.<domain>).
website = Frontend('site', kind='static', dist='site', path='/site', title='Website', auth=None, routing='pages', suggested_host='www')
PORTAL_RULES = {
    ('portal', Customer): Rule(('read',), read_fields=('customer_name', 'title', 'email'), scope=own_customer('id')),
    ('portal', Invoice): Rule(('read',), read_fields=('customer', 'name', 'title', 'reference', 'total', 'status', 'posting_date'), scope=own_customer()),
    # Admin prints whatever it reads; editors print invoices because their Rule names 'print'.
    ('editor', Invoice): Rule(('read', 'create', 'update', 'delete', 'print')),
    # The news site's "Finished projects" pages (NEWS, _site/collections.json) read as the public role: finished
    # projects only, and only these fields. Budget, customer, brief and notes never reach a public page.
    ('public', Project): Rule(('read',), read_fields=('title', 'docstate', 'starts_on', 'progress'), scope=lambda user: {'docstate': 'done'}),
}


def playground_routes(port=8000):
    """The portal on its own host, and at /portal on the main host too; the public website on its own host only.

    More hosts need no restart: in Controls, Routing, Add host www.localhost serving playground.site, then Check.
    It is a row of evlet_hosts (evlet/hosts.py), served within a second at http://www.localhost:8000."""
    definition = AppDefinition(frontends=(portal, website), api_scopes=(ApiScope('portal', models=(Customer, Invoice)),))
    hosts = (Host('portal.localhost', root=portal, also_on_primary=True), Host('site.localhost', root='playground.site'))
    return resolve_routes([('playground', definition)], None, {'hosts': hosts},
                          port=port, roots={'playground': Path(__file__).parent})


def create_app():
    database = configured_database()
    routes = playground_routes(8000)
    auth = LocalAuth(database, origins=(*routes.origins(), 'http://127.0.0.1:5173', 'http://localhost:5173',
                                       'http://127.0.0.1:4173', 'http://localhost:4173'),
                     realms=routes.realms(), secure_cookies=False, allow_setup=True,
                     # The sign-in rules this instance starts from (the defaults). Admin accounts follow them only; Controls can
                     # change the password minimum and two-step sign-in for everyone else. password_min=1 allows any password.
                     security=Security(session_hours=12, lockout=5, password_min=15, admin_two_step=True, two_step=False, admin_sign_in=False))
    return build_app(database, auth, routes=routes, ai=playground_ai())


NEWS = {
    'index.html': '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Playground news</title>'
                  '<link rel="stylesheet" href="/assets/news-1a2b3c4d.css"></head><body><h1>Playground news</h1>'
                  '<p>An instance site: published from Python, with no app package.</p><a href="/about/">About</a> <a href="/projects/">Finished projects</a></body></html>',
    'about/index.html': '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>About</title>'
                        '<link rel="stylesheet" href="/assets/news-1a2b3c4d.css"></head><body><h1>About</h1><a href="/">Home</a></body></html>',
    'assets/news-1a2b3c4d.css': 'body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto; }\n',
    # Pages made from records (docs/sites.md#collections): one page per finished project, a list of them and a JSON
    # file, rendered at publish and again shortly after a project changes. _site/ is published, never served.
    '_site/collections.json': json.dumps({'collections': [{
        'name': 'projects', 'model': 'project', 'order_by': 'title', 'limit': 100,
        'page': '/projects/{id}.html', 'template': 'project.html',
        'index': {'path': '/projects/index.html', 'template': 'projects.html'}, 'json': '/data/projects.json'}]}),
    '_site/partials/head.html': '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{{title}}</title>'
                                '<link rel="stylesheet" href="/assets/news-1a2b3c4d.css"></head><body>',
    '_site/project.html': '{{> head}}<h1>{{title}}</h1>{{#if starts_on}}<p>Started {{starts_on}}</p>{{/if}}'
                          '<p><a href="/projects/">All finished projects</a></p></body></html>',
    '_site/projects.html': '{{> head}}<h1>Finished projects</h1><ul>{{#each items}}<li><a href="{{_url}}">{{title}}</a></li>'
                           '{{else}}<li>None yet</li>{{/each}}</ul><a href="/">Home</a></body></html>',
}


def publish_news_site(app):
    """An instance site (docs/sites.md): the news site, created and published once, kept by this database.

    Serve it at http://news.localhost:8000 from Controls, Routing: Add host news.localhost serving news, then
    Check. Or from a shell, after the server has started once (so the database is migrated):
    .venv/bin/python -c "from examples.playground.app import *; app = create_app(); app.initialize(); publish_news_site(app)"
    """
    business = app.business
    if 'news' not in {site['name'] for site in sites.list_sites(business)}:
        sites.create_site(business, 'news', actor='playground', routing='pages')
    if sites.get_site(business, 'news')['current_release'] is None:
        sites.write_files(business, 'news', NEWS, actor='playground')
        sites.publish(business, 'news', actor='playground', note='First edition')
    return sites.get_site(business, 'news')


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
    return {'project': record.title, 'status': record.docstate, 'progress': record.progress, 'budget': record.get('budget'),
            'tasks': len(tasks), 'done': sum(1 for task in tasks if task.done)}


# Business services offered to AI assistants through the Tool Engine (list_services and call_service).
SERVICES = (Endpoint('playground.project_overview', project_overview, 'GET', (Project, 'read'), tool=True),
            Endpoint('playground.quick_invoice', quick_invoice, 'POST', (Invoice, 'create')))


def show_currencies(app):
    """The demo adds XCR, a client's own credit unit with 5 decimal places (Controls, Currencies). AED keeps the
    default display, its code as text before the amount ("AED 1,234.50"); an image symbol, such as the official
    UAE dirham symbol from the Central Bank of the UAE, can be uploaded in Controls, Currencies. KWD keeps its
    ISO 3 places."""
    return set_currency_display(app, {'XCR': {'symbol': 'CR', 'position': 'suffix', 'decimals': 5}}, actor='playground')


def build_app(database, auth, *, storage=None, tenant='local', routes=None, ai=None):
    events = EventStore(database)
    policy = Policy(registry, rules=PORTAL_RULES)
    jobs = JobQueue(database, (Task('project_progress', refresh_progress), Task('rebuild_points', rebuild_points, resumable=True)),
                    tenant=tenant, events=events, policy=policy)
    app = Application(database, name="Evlet Playground", playground=True, auth=auth, policy=policy, routes=routes,
                      storage=storage, jobs=jobs, events=events, children=(ChildTable(Project, 'tasks', ProjectTask, 'project', 'Tasks'),),
                      attachments=(Project,), reports=(*REPORTS, *PRINTED_REPORTS), apis=SERVICES, ai=ai, prints=PRINTS,
                      workspaces=WORKSPACES)
    # Settings > Projects: people who can read projects see it; editors and admins change it.
    app.business.settings.register('projects', ProjectSettings, ProjectPreferences, (Project,))
    return app
