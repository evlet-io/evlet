"""Build, seed and serve testCRM (examples/testcrm) in a disposable project.

testCRM is installed like any app: copied into a fresh project's apps/, installed into an instance
named crm, and served from that instance. Nothing here touches examples/playground or any other
database. The data is fictional.

    .venv/bin/python -m scripts.testcrm build DIRECTORY    # project, instance, install and seed
    EVLET_TESTCRM_PROJECT=DIRECTORY uvicorn scripts.testcrm:create_app --factory

scripts/testcrm.sh does both and starts the UI development server.
"""

import argparse
from datetime import date, timedelta
import os
from pathlib import Path
import secrets
import shutil
import sys

SOURCE = Path(__file__).resolve().parent.parent / 'examples' / 'testcrm'
INSTANCE = 'crm'
TODAY = date(2026, 11, 1)

# Fictional people. Sales people use the editor role and own their deals by username.
USERS = (('admin', 'admin'), ('sara', 'editor'), ('omar', 'editor'), ('rahul', 'editor'), ('vera', 'viewer'))

ORGANIZATIONS = (
    ('Al Sahra Trading LLC', 'Trading', 'Dubai', 'alsahra-trading.ae', '+971 4 418 2290', 30),
    ('Marina Steel Fabrication', 'Manufacturing', 'Sharjah', 'marinasteel.ae', '+971 6 771 4408', 45),
    ('Gulf Crest Logistics', 'Logistics', 'Dubai', 'gulfcrest.ae', '+971 4 645 1180', 30),
    ('Noor Interiors and Fitout', 'Fit-out', 'Dubai', 'noorinteriors.ae', '+971 4 990 3341', 60),
    ('Bayan Medical Supplies', 'Healthcare', 'Abu Dhabi', 'bayanmedical.ae', '+971 2 118 6620', 30),
    ('Desert Line Signage', 'Services', 'Ajman', 'desertline.ae', '+971 6 337 5129', 15),
    ('Zahra Foods FZE', 'FMCG', 'Ras Al Khaimah', 'zahrafoods.ae', '+971 7 880 4416', 45),
    ('Horizon Courier Services', 'Logistics', 'Dubai', 'horizoncourier.ae', '+971 4 471 0083', 30),
    ('Qasr Building Contracting', 'Construction', 'Abu Dhabi', 'qasrcontracting.ae', '+971 2 264 7735', 90),
    ('Falcon Auto Parts', 'Retail', 'Sharjah', 'falconautoparts.ae', '+971 6 902 1146', 30),
)

CONTACTS = (
    ('Khalid Al Mansoori', 'Finance Director', 0), ('Priya Menon', 'Operations Manager', 0),
    ('Youssef Kassem', 'General Manager', 1), ('Fatima Rahman', 'Head of Procurement', 2),
    ('Daniel Pereira', 'IT Manager', 2), ('Mariam Saeed', 'Managing Partner', 3), ('Ahmed Fawzi', 'CFO', 4),
    ('Nikhil Rao', 'Owner', 5), ('Hessa Al Ali', 'Head of Supply Chain', 6), ('Tariq Aziz', 'Operations Director', 7),
    ('Leena Joseph', 'Project Controls Lead', 8), ('Imran Siddiqui', 'Purchase Manager', 9),
)

PRODUCTS = (
    ('ERP-CORE', 'ERP core licence', 'licence', '45000.00', True),
    ('ERP-USER', 'Named user licence', 'subscription', '1200.00', True),
    ('IMPL-DAY', 'Implementation day', 'service', '3200.00', True),
    ('TRAIN-DAY', 'Training day', 'service', '2400.00', True),
    ('DATA-MIG', 'Data migration', 'service', '8500.00', True),
    ('AMC', 'Annual maintenance', 'subscription', '18000.00', True),
    ('WPS-PAY', 'WPS payroll module', 'licence', '9500.00', True),
    ('EINV', 'E-invoicing readiness review', 'service', '6000.00', False),
)

# title, organization, contact, owner, source, stage, close (days from today), lines (product, qty, discount)
DEALS = (
    ('ERP rollout, 3 entities', 0, 0, 'sara', 'Referral', 'negotiation', 7, (('ERP-CORE', 3, 5), ('ERP-USER', 40, 10), ('IMPL-DAY', 30, 0))),
    ('Production planning module', 1, 2, 'sara', 'Website', 'proposal', 19, (('ERP-CORE', 1, 0), ('IMPL-DAY', 12, 0))),
    ('Fleet and warehouse ERP', 2, 3, 'omar', 'Event', 'qualified', 44, (('ERP-CORE', 2, 0), ('DATA-MIG', 1, 0))),
    ('Project costing and BOQ', 3, 5, 'sara', 'Referral', 'proposal', 11, (('ERP-CORE', 1, 0), ('TRAIN-DAY', 4, 0))),
    ('Batch and expiry tracking', 4, 6, 'rahul', 'LinkedIn', 'negotiation', 4, (('ERP-CORE', 2, 0), ('IMPL-DAY', 6, 0))),
    ('Job card workflow', 5, 7, 'omar', 'Google Ads', 'lead', 29, (('IMPL-DAY', 10, 0),)),
    ('Distributor portal', 6, 8, 'sara', 'Partner', 'qualified', 47, (('IMPL-DAY', 20, 0), ('ERP-USER', 15, 0))),
    ('Shipment tracking integration', 7, 9, 'omar', 'Website', 'lead', 39, ()),
    ('Progress billing and retention', 8, 10, 'rahul', 'Referral', 'proposal', 24, (('ERP-CORE', 3, 0), ('IMPL-DAY', 8, 0))),
    ('POS and inventory sync', 9, 11, 'omar', 'Website', 'won', -4, (('ERP-CORE', 1, 0),)),
    ('AMC renewal 2027', 0, 1, 'sara', 'Referral', 'won', -2, (('AMC', 1, 0),)),
    ('HR and WPS payroll', 1, 2, 'sara', 'Website', 'qualified', 21, (('WPS-PAY', 1, 0), ('TRAIN-DAY', 2, 0))),
    ('VAT and e-invoicing readiness', 4, 6, 'rahul', 'LinkedIn', 'lost', 13, (('EINV', 1, 0),)),
    ('Multi-branch accounting', 6, 8, 'omar', 'Event', 'negotiation', -2, (('ERP-CORE', 2, 10), ('ERP-USER', 25, 0))),
    ('Subcontractor management', 8, 10, 'sara', 'Referral', 'lead', 34, ()),
    ('Customer self-service app', 7, 9, 'rahul', 'Partner', 'won', -10, (('IMPL-DAY', 15, 0), ('AMC', 1, 0))),
)
STAGE_PATH = ('lead', 'qualified', 'proposal', 'negotiation')

LEADS = (
    ('Rashid Al Hammadi', 'Emirates Glass Works', 'rashid@emiratesglass.ae', '+971 50 128 4471', 'Website', 82, 'sara', 'new'),
    ('Anjali Varma', 'Bluewave Pharmacy Group', 'anjali@bluewavepharmacy.ae', '+971 55 640 2218', 'LinkedIn', 64, 'sara', 'qualified'),
    ('Samir Haddad', 'Coastline Marine Services', 'samir@coastlinemarine.ae', '+971 50 772 9013', 'Referral', 91, 'omar', 'qualified'),
    ('Grace Thomas', 'Petal and Stem Florists', 'grace@petalandstem.ae', '+971 52 304 6650', 'Website', 38, 'rahul', 'new'),
    ('Majid Obaid', 'Al Waha Furniture', 'majid@alwahafurniture.ae', '+971 56 118 3392', 'Event', 57, 'sara', 'new'),
    ('Vivek Pillai', 'Metro Cold Stores', 'vivek@metrocoldstores.ae', '+971 55 230 9184', 'Cold call', 22, 'omar', 'new'),
    ('Noura Al Shamsi', 'Lumen Lighting Trading', 'noura@lumenlighting.ae', '+971 50 663 0417', 'Website', 69, 'rahul', 'new'),
    ('Elena Kovacs', 'Verde Packaging FZE', 'elena@verdepackaging.ae', '+971 56 405 8831', 'LinkedIn', 71, 'sara', 'new'),
)

TASKS = (
    ('Call Khalid about final pricing', 'call', 0, 'sara', 0, False),
    ('Send revised proposal to Noor Interiors', 'email', 3, 'sara', 0, False),
    ('Production planning walkthrough', 'meeting', 1, 'sara', 1, False),
    ('Discovery workshop at Gulf Crest', 'meeting', 2, 'omar', 3, False),
    ('Chase VAT readiness questionnaire', 'todo', 12, 'rahul', -2, False),
    ('Share reference client list', 'email', 6, 'sara', -1, False),
    ('Book site visit at Qasr head office', 'meeting', 8, 'rahul', 0, False),
    ('Prepare data migration checklist', 'todo', 0, 'sara', 2, True),
)


def build(project, *, frontend_port=5173):
    """Create a project with testCRM installed into the instance crm (Gulf Standard Time)."""
    from evlet.cli.install import install
    from evlet.cli.instance import create_instance
    project = Path(project)
    (project / 'apps').mkdir(parents=True)
    (project / 'instances').mkdir()
    shutil.copytree(SOURCE, project / 'apps/testcrm', ignore=shutil.ignore_patterns('__pycache__', '*.egg-info', 'build', 'dist'))
    path = create_instance(project, INSTANCE)
    (path / 'config.py').write_text("from evlet import Instance\n\n"
                                    f"instance = Instance(name={INSTANCE!r}, domain=None, timezone='Asia/Dubai')\n", encoding='utf-8')
    install(project, INSTANCE, app_name='testcrm', trust=True)
    return load(project, frontend_port=frontend_port)


def load(project, *, frontend_port=5173):
    """The instance's application. Its origin is the UI development server, which proxies /api here."""
    from evlet.instances import load_runtime
    return load_runtime(Path(project), INSTANCE, port=frontend_port, development=True)


def models(app):
    """The installed testCRM classes by name."""
    return {model.__name__: model for model in app.database.registry.models.values()}


def seed(app, password, *, big_lines=150):
    """Fictional users and data. Deals reach their stage through their moves, so won deals post their
    bookings and create their kick-off tasks exactly as the app does. Returns {username: User}."""
    m = models(app)
    users = {name: app.business.auth.create_user(username=name, password=password, role=role) for name, role in USERS}
    admin = users['admin']
    with app.business.context(admin) as context:
        organizations = [context.create(m['Organization'], name=name, industry=industry, emirate=emirate,
                                        website=f'https://{site}', phone=phone, payment_days=terms,
                                        trn=f'1000{index:011d}')['id']
                         for index, (name, industry, emirate, site, phone, terms) in enumerate(ORGANIZATIONS)]
        contacts = [context.create(m['Contact'], name=name, job_title=title, organization=organizations[org],
                                   email=name.split()[0].lower() + '@' + ORGANIZATIONS[org][3],
                                   phone=f'+971 50 {400 + index:03d} {2290 + index:04d}')['id']
                    for index, (name, title, org) in enumerate(CONTACTS)]
        products = {code: context.create(m['Product'], code=code, name=name, kind=kind, price=price, taxable=taxable)['id']
                    for code, name, kind, price, taxable in PRODUCTS}
    deals = []
    for title, org, contact, owner, source, stage, close, lines in DEALS:
        # Each deal is created and moved by its owner, inside the owner's row scope.
        with app.business.context(users[owner]) as context:
            rows = [{'_key': f'r{index}', 'product': products[code], 'qty': str(qty), 'discount': str(discount),
                     'recurring': code in ('AMC', 'ERP-USER')} for index, (code, qty, discount) in enumerate(lines)]
            deal = context.create(m['Deal'], title=title, organization=organizations[org], contact=contacts[contact],
                                  owner=owner, lead_source=source, expected_close=(TODAY + timedelta(days=close)).isoformat(),
                                  children={'lines': rows})['id']
            target = 'negotiation' if stage == 'won' else stage
            for step in STAGE_PATH[1:STAGE_PATH.index(target) + 1] if target in STAGE_PATH else ():
                context.move(m['Deal'], deal, step)
            if stage == 'won':
                context.move(m['Deal'], deal, stage)
            elif stage == 'lost':
                # Mark lost asks for the reason.
                context.move(m['Deal'], deal, stage, lost_reason='Chose to wait for the e-invoicing rules')
            deals.append(deal)
    with app.business.context(admin) as context:
        context.update(m['Deal'], deals[0], internal_notes='Partner sign-off needed for any discount above 5%.')
    for name, company, email, phone, source, score, owner, status in LEADS:
        with app.business.context(users[owner]) as context:
            lead = context.create(m['Lead'], name=name, company=company, email=email, phone=phone, lead_source=source,
                                  score=score, owner=owner)['id']
            if status != 'new':
                context.move(m['Lead'], lead, status)
    for title, kind, deal, owner, due, done in TASKS:
        with app.business.context(users[owner]) as context:
            context.create(m['Task'], title=title, kind=kind, deal=deals[deal], assignee=owner, done=done,
                           due_on=(TODAY + timedelta(days=due)).isoformat(), **({'at': '10:30', 'length': 3600} if kind == 'meeting' else {}))
    if big_lines:
        big_deal(app, users['sara'], products, organizations[2], contacts[3], big_lines)
    return users


def big_deal(app, user, products, organization, contact, count):
    """A deal with many lines, for paging and timing. Rows go in change sets of 100 (64 KiB bodies)."""
    m = models(app)
    codes = list(products)
    with app.business.context(user) as context:
        deal = context.create(m['Deal'], title=f'Group rollout, {count} sites', organization=organization, contact=contact,
                              owner=user.username, lead_source='Partner', expected_close=(TODAY + timedelta(days=60)).isoformat())['id']
    for start in range(0, count, 100):
        rows = [{'_key': f'b{index}', 'product': products[codes[index % len(codes)]], 'qty': str(1 + index % 5),
                 'description': f'Site {index + 1:04d}', 'discount': str(index % 3 * 5)} for index in range(start, min(count, start + 100))]
        with app.business.context(user) as context:
            context.update(m['Deal'], deal, children={'lines': {'create': rows}})
    return deal


def create_app():
    """Uvicorn factory: serve the instance built by `build`, inside its own generation."""
    from evlet.cli.serve import ManagedRuntime
    project = Path(os.environ['EVLET_TESTCRM_PROJECT'])
    port = int(os.environ.get('EVLET_FRONTEND_PORT', '5173'))
    return ManagedRuntime(lambda: load(project, frontend_port=port))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('build',))
    parser.add_argument('project', type=Path, help='a directory that does not exist yet')
    parser.add_argument('--frontend-port', type=int, default=int(os.environ.get('EVLET_FRONTEND_PORT', '5173')))
    parser.add_argument('--lines', type=int, default=150, help='lines on the large deal (0 to skip, at most 1000)')
    args = parser.parse_args()
    if args.project.exists():
        raise SystemExit(f'{args.project} exists. Use a new, disposable directory.')
    from evlet.instances import activate_generation
    app = build(args.project, frontend_port=args.frontend_port)
    password = os.environ.get('EVLET_TESTCRM_PASSWORD') or secrets.token_urlsafe(12)
    with activate_generation(app):
        app.initialize()
        try:
            seed(app, password, big_lines=args.lines)
        finally:
            app.database.close()
    sys.stdout.write(f'testCRM is ready in {args.project}\nUsers: {", ".join(f"{name} ({role})" for name, role in USERS)}\n'
                     f'Password for every user: {password}\n')


if __name__ == '__main__':
    main()
