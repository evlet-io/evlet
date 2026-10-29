"""Tenant Playground factory. Provision its catalog with the management command."""

import os
from evlet import Database, LocalAuth, LocalFileStorage, TenantApplication, TenantRegistry
from .app import build_app
from .models import registry
from .migrate import migrations


def tenant_app(tenant):
    database = Database(tenant.database_path, registry, migrations=migrations, must_exist=True)
    auth = LocalAuth(database, origins=tenant.origins, secure_cookies=not tenant.development,
                     cookie_name='evlet_session' if tenant.development else '__Host-evlet_session')
    return build_app(database, auth, storage=LocalFileStorage(tenant.files_path))


def create_app():
    return TenantApplication(TenantRegistry(os.environ['EVLET_TENANT_ROOT']), tenant_app)
