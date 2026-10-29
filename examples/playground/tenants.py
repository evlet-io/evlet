"""Tenant Playground factory. Provision its catalog with the management command."""

import os
from evlet import Database, LocalAuth, LocalFileStorage, TenantApplication, TenantRegistry, S3Storage
from .app import build_app
from .models import registry
from .migrate import migrations


def attachment_storage(tenant):
    bucket = os.environ.get('EVLET_OBJECT_BUCKET')
    if not bucket:
        return LocalFileStorage(tenant.files_path)
    import boto3
    from botocore.config import Config
    from urllib.parse import urlsplit
    endpoint = os.environ.get('EVLET_OBJECT_ENDPOINT')
    if endpoint:
        url = urlsplit(endpoint)
        if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('Use an HTTPS object-storage endpoint without credentials or a path')
    client = boto3.client('s3', endpoint_url=endpoint, region_name=os.environ.get('EVLET_OBJECT_REGION', 'us-east-1'),
                          config=Config(connect_timeout=3, read_timeout=5, retries={'max_attempts': 2},
                                        request_checksum_calculation='when_required', response_checksum_validation='when_required'))
    return S3Storage(client, bucket=bucket, prefix=os.environ.get('EVLET_OBJECT_PREFIX', 'evlet'), tenant=tenant.slug)


def tenant_app(tenant):
    database = Database(tenant.database_path, registry, migrations=migrations, must_exist=True)
    auth = LocalAuth(database, origins=tenant.origins, secure_cookies=not tenant.development,
                     cookie_name='evlet_session' if tenant.development else '__Host-evlet_session')
    return build_app(database, auth, storage=attachment_storage(tenant), tenant=tenant.slug)


def create_app():
    return TenantApplication(TenantRegistry(os.environ['EVLET_TENANT_ROOT']), tenant_app)
