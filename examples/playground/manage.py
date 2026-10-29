"""Trusted tenant administration. No HTTP tenant-management routes are exposed."""

import argparse
import getpass
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from evlet import TenantRegistry
from evlet.deployment import render_deployment
from .tenants import tenant_app


def provision(catalog, slug, username, password):
    """Initialize or resume a disabled tenant, then activate after creating its admin."""
    tenant = catalog.get(slug)
    inventory = catalog.inventory()
    if next(row['active'] for row in inventory['tenants'] if row['slug'] == slug):
        raise ValueError('Disable the tenant before provisioning')
    tenant.database_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not tenant.database_path.exists():
        with tenant.database_path.open('xb'):
            tenant.database_path.chmod(0o600)
    app = tenant_app(tenant)
    app.initialize()
    if not app.business.auth.setup_needed():
        raise ValueError('This tenant already has accounts; enable it explicitly')
    app.business.auth.create_user(username=username, password=password, role='admin')
    catalog.set_active(slug, True)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Private tenant state directory')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--development', action='store_true')
    init.add_argument('--base-domain', action='append', default=[])
    init.add_argument('--ports', default='8000,5173,4173', help='Development ports, comma separated')
    create = commands.add_parser('create')
    create.add_argument('slug'); create.add_argument('label'); create.add_argument('hostname')
    setup = commands.add_parser('provision')
    setup.add_argument('slug'); setup.add_argument('--username', required=True)
    for name in ('enable', 'disable', 'collect-files'):
        commands.add_parser(name).add_argument('slug')
    domain = commands.add_parser('add-domain')
    domain.add_argument('slug'); domain.add_argument('hostname')
    for name in ('challenge', 'verify-domain', 'enable-domain', 'disable-domain'):
        commands.add_parser(name).add_argument('hostname')
    commands.add_parser('list')
    commands.add_parser('render').add_argument('--output', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command == 'init':
            TenantRegistry.create(args.root, managed_domains=args.base_domain, development=args.development,
                                  ports=tuple(int(port) for port in args.ports.split(',')))
            print('Tenant registry created.')
            return
        catalog = TenantRegistry(args.root)
        if args.command == 'create':
            catalog.create_tenant(args.slug, args.label, args.hostname)
            print('Tenant registered and disabled. Provision its administrator before enabling it.')
        elif args.command == 'provision':
            password = getpass.getpass('Administrator password (15 to 128 characters): ')
            if password != getpass.getpass('Confirm password: '):
                raise ValueError('Passwords do not match')
            provision(catalog, args.slug, args.username, password)
            print('Tenant provisioned and enabled.')
        elif args.command in ('enable', 'disable'):
            if args.command == 'enable':
                app = tenant_app(catalog.get(args.slug)); app.initialize()
                if app.business.auth.setup_needed():
                    raise ValueError('Provision an administrator before enabling this tenant')
            catalog.set_active(args.slug, args.command == 'enable')
            print('Tenant state updated.')
        elif args.command == 'collect-files':
            app = tenant_app(catalog.get(args.slug)); app.initialize()
            print(f'Reclaimed {app.business.storage.collect()} unreferenced files.')
        elif args.command == 'add-domain':
            print(json.dumps(catalog.add_domain(args.slug, args.hostname), indent=2))
        elif args.command == 'challenge':
            print(json.dumps(catalog.challenge(args.hostname), indent=2))
        elif args.command == 'verify-domain':
            catalog.verify_domain(args.hostname)
            print('Domain verified. Render and review the server configuration before applying it.')
        elif args.command in ('enable-domain', 'disable-domain'):
            catalog.set_domain_active(args.hostname, args.command == 'enable-domain')
            print('Domain state updated.')
        elif args.command == 'list':
            print(json.dumps(catalog.inventory(), indent=2))
        elif args.command == 'render':
            files = render_deployment(catalog)
            target = Path(args.output)
            target.mkdir(parents=True, exist_ok=False)
            for name, content in files.items():
                (target / name).write_text(content)
            print('Configuration written for review. No service was changed.')
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
