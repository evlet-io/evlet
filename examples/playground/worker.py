"""Optional single-node worker. Each pass checks current tenant activation."""

import argparse
import os
import logging
import signal
import time

from evlet import TenantRegistry
from .app import create_app
from .tenants import tenant_app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='One scheduling/claim pass, then exit')
    args = parser.parse_args(argv)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger = logging.getLogger(__name__)
    root = os.environ.get('EVLET_TENANT_ROOT')
    catalog = TenantRegistry(root) if root else None
    apps = {}
    if catalog is None:
        app = create_app(); app.initialize()
    while running:
        worked = False
        if catalog:
            for entry in catalog.inventory()['tenants']:
                if not running or not entry['active']:
                    continue
                try:
                    tenant = catalog.get(entry['slug'])
                    if not tenant.hosts:
                        continue
                    if apps.get(tenant.slug, (None,))[0] != tenant:
                        selected = tenant_app(tenant); selected.initialize()
                        apps[tenant.slug] = tenant, selected
                    selected = apps[tenant.slug][1]
                    def active():
                        return any(row['slug'] == tenant.slug and row['active'] for row in catalog.inventory()['tenants'])
                    worked = selected.business.jobs.run_one(active=active) or worked
                except Exception:
                    logger.exception("Tenant job pass failed for %s", entry['slug'])
        else:
            worked = app.business.jobs.run_one()
        if args.once:
            break
        if not worked:
            time.sleep(0.5)


if __name__ == '__main__':
    main()
