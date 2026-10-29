"""An operator-managed tenant catalog and an ASGI router with explicit hosts."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import secrets
import shlex
import sqlite3
import subprocess
import threading
import time

from .app import Application
from .storage import LocalFileStorage
from .object_storage import S3Storage

logger = logging.getLogger(__name__)


def hostname(value):
    if not isinstance(value, str) or len(value) > 253 or not value.isascii():
        raise ValueError('Use an ASCII DNS hostname')
    value = value.lower()
    labels = value.split('.')
    if len(labels) < 2 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels) or labels[-1].isdigit():
        raise ValueError('Use an exact DNS hostname without a port or wildcard')
    return value


def dns_txt(name):
    """Read TXT records through the server's configured DNS resolver using dig."""
    result = subprocess.run(['dig', '+short', '+tcp', '+time=5', '+tries=1', 'TXT', name], capture_output=True, text=True, timeout=8, check=True)
    return tuple(''.join(shlex.split(line)) for line in result.stdout.splitlines() if line.startswith('"'))


@dataclass(frozen=True)
class Tenant:
    slug: str
    label: str
    database_path: Path
    files_path: Path
    hosts: tuple
    development: bool
    ports: tuple

    @property
    def origins(self):
        if self.development:
            return tuple(f'http://{host}:{port}' for host in self.hosts for port in self.ports)
        return tuple(f'https://{host}' for host in self.hosts)

    def public(self):
        return {'slug': self.slug, 'label': self.label}


class TenantRegistry:
    """Trusted administration API. Runtime opens an existing catalog only."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'registry.sqlite3'
        with self._connection() as db:
            settings = json.loads(db.execute('SELECT value FROM settings WHERE name=?', ('configuration',)).fetchone()[0])
        if settings.get('version') != 1:
            raise ValueError('Unsupported tenant catalog version')
        self.development = settings['development']
        self.managed_domains = tuple(settings['managed_domains'])
        self.ports = tuple(settings['ports'])

    @classmethod
    def create(cls, root, *, managed_domains=(), development=False, ports=(8000, 5173, 4173)):
        bases = tuple(hostname(value) for value in managed_domains)
        if not development and (not bases or any(base.endswith('.localhost') for base in bases)):
            raise ValueError('Configure an owned production base domain')
        if development and bases:
            raise ValueError('Development registries use only .localhost hostnames')
        if not ports or any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
            raise ValueError('Use explicit valid development ports')
        root = Path(root).resolve()
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = root / 'registry.sqlite3'
        # Exclusive creation prevents a repeated initialization replacing a catalog.
        with path.open('xb'):
            path.chmod(0o600)
        with sqlite3.connect(path) as db:
            db.executescript('''
                CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE tenants (slug TEXT PRIMARY KEY, label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE domains (hostname TEXT PRIMARY KEY, tenant TEXT NOT NULL REFERENCES tenants(slug), kind TEXT NOT NULL, token TEXT, expires INTEGER, verified_at INTEGER, active INTEGER NOT NULL DEFAULT 1);
            ''')
            db.execute('INSERT INTO settings VALUES (?,?)', ('configuration', json.dumps({'version': 1, 'development': development, 'managed_domains': bases, 'ports': ports if development else ()})))
        return cls(root)

    @contextmanager
    def _connection(self, *, write=False):
        if self.path.is_symlink():
            raise ValueError('Tenant catalog must not be a symbolic link')
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        finally:
            db.close()

    def _tenant(self, db, slug):
        row = db.execute('SELECT * FROM tenants WHERE slug=?', (slug,)).fetchone()
        if row is None:
            raise ValueError('Unknown tenant')
        directory = self.root / 'tenants' / row['slug']
        if directory.resolve() != directory:
            raise ValueError('Tenant directories must not use symbolic links')
        hosts = tuple(row[0] for row in db.execute('SELECT hostname FROM domains WHERE tenant=? AND verified_at IS NOT NULL AND active=1 ORDER BY hostname', (slug,)))
        return Tenant(row['slug'], row['label'], directory / 'application.sqlite3', directory / 'files', hosts, self.development, self.ports)

    def get(self, slug):
        with self._connection() as db:
            return self._tenant(db, slug)

    def create_tenant(self, slug, label, host):
        if not isinstance(slug, str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}', slug):
            raise ValueError('Use a tenant slug with lowercase letters, digits, and hyphens')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 120 or any(ord(char) < 32 for char in label):
            raise ValueError('Use a tenant label of 1 to 120 characters')
        host = hostname(host)
        if self.development:
            allowed = host.endswith('.localhost')
        else:
            allowed = any(host == base or host.endswith('.' + base) for base in self.managed_domains)
        if not allowed:
            raise ValueError('Managed hostname must be under a configured owned base domain')
        with self._connection(write=True) as db:
            db.execute('INSERT INTO tenants(slug,label) VALUES (?,?)', (slug, label.strip()))
            db.execute('INSERT INTO domains(hostname,tenant,kind,verified_at) VALUES (?,?,?,?)', (host, slug, 'managed', int(time.time())))
            return self._tenant(db, slug)

    def set_active(self, slug, active):
        if type(active) is not bool:
            raise ValueError('Use a boolean active flag')
        tenant = self.get(slug)
        if active and (not tenant.database_path.is_file() or tenant.database_path.is_symlink()):
            raise ValueError('Provision the tenant database before activation')
        with self._connection(write=True) as db:
            db.execute('UPDATE tenants SET active=? WHERE slug=?', (active, slug))

    def add_domain(self, slug, host):
        host = hostname(host)
        if self.development or host.endswith('.localhost'):
            raise ValueError('Custom domains require a production registry')
        self.get(slug)
        with self._connection(write=True) as db:
            db.execute('INSERT INTO domains(hostname,tenant,kind) VALUES (?,?,?)', (host, slug, 'custom'))
        return self.challenge(host)

    def challenge(self, host):
        host = hostname(host)
        token = 'evlet-verification=' + secrets.token_urlsafe(32)
        with self._connection(write=True) as db:
            changed = db.execute("UPDATE domains SET token=?,expires=?,verified_at=NULL WHERE hostname=? AND kind='custom'", (token, int(time.time()) + 86400, host)).rowcount
            if not changed:
                raise ValueError('Unknown custom domain')
        return {'name': '_evlet-challenge.' + host, 'type': 'TXT', 'value': token}

    def verify_domain(self, host, *, resolver=dns_txt):
        host = hostname(host)
        with self._connection() as db:
            row = db.execute("SELECT * FROM domains WHERE hostname=? AND kind='custom'", (host,)).fetchone()
        if row is None or not row['token'] or row['expires'] <= time.time():
            raise ValueError('Request a new domain challenge')
        if row['token'] not in resolver('_evlet-challenge.' + host):
            raise ValueError('The DNS TXT verification record does not match')
        with self._connection(write=True) as db:
            changed = db.execute('UPDATE domains SET verified_at=?,token=NULL,expires=NULL WHERE hostname=? AND token=? AND expires>? AND active=1', (int(time.time()), host, row['token'], int(time.time()))).rowcount
            if not changed:
                raise ValueError('Domain challenge changed, expired, or was disabled')

    def set_domain_active(self, host, active):
        if type(active) is not bool:
            raise ValueError('Use a boolean active flag')
        with self._connection(write=True) as db:
            if not db.execute('UPDATE domains SET active=? WHERE hostname=?', (active, hostname(host))).rowcount:
                raise ValueError('Unknown domain')

    def inventory(self):
        with self._connection() as db:
            return {'tenants': [dict(row) for row in db.execute('SELECT * FROM tenants ORDER BY slug')],
                    'domains': [dict(row) for row in db.execute('SELECT hostname,tenant,kind,verified_at,active FROM domains ORDER BY hostname')]}

    def resolve(self, host):
        with self._connection() as db:
            row = db.execute('SELECT d.tenant FROM domains d JOIN tenants t ON t.slug=d.tenant WHERE d.hostname=? AND d.verified_at IS NOT NULL AND d.active=1 AND t.active=1', (host,)).fetchone()
            return None if row is None else self._tenant(db, row['tenant'])


class TenantApplication:
    """Resolve Host before touching a tenant database. Forwarded hosts are ignored."""

    def __init__(self, catalog, factory):
        self.catalog, self.factory = catalog, factory
        self._apps = {}
        self._lock = threading.Lock()

    def _application(self, tenant):
        with self._lock:
            if not tenant.database_path.is_file() or tenant.database_path.is_symlink():
                raise ValueError('Tenant database has not been provisioned')
            previous = self._apps.get(tenant.slug)
            if previous and previous[0] == tenant:
                return previous[1]
            app = self.factory(tenant)
            if not isinstance(app, Application) or app.database.path.resolve() != tenant.database_path or app.business is None or not app.database.must_exist:
                raise ValueError('Tenant factory must return an authenticated application with its own database')
            auth, storage = app.business.auth, app.business.storage
            if auth.origins != frozenset(tenant.origins) or auth.secure_cookies == tenant.development or auth.allow_setup:
                raise ValueError('Tenant authentication must use its exact origins and disable browser setup')
            if not tenant.development and auth.cookie_name != '__Host-evlet_session':
                raise ValueError('Production tenants require a host-only secure cookie')
            local = isinstance(storage, LocalFileStorage) and storage.root == tenant.files_path
            remote = isinstance(storage, S3Storage) and storage.tenant == tenant.slug
            if not (local or remote):
                raise ValueError('Tenant attachments require their own private storage')
            if app.business.jobs is not None and app.business.jobs.tenant != tenant.slug:
                raise ValueError('Tenant jobs require the matching tenant namespace')
            app.initialize()
            app.business.tenant = tenant.public()
            self._apps[tenant.slug] = (tenant, app)
            return app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        elif scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        elif scope['type'] != 'http':
            raise ValueError('Unsupported ASGI scope')
        try:
            hosts = [value for key, value in scope.get('headers', ()) if key.lower() == b'host']
            if len(hosts) != 1:
                raise ValueError('Use one Host header')
            raw = hosts[0].decode('ascii')
            host, separator, port = raw.partition(':')
            host = hostname(host)
            expected = self.catalog.ports if self.catalog.development else (443,)
            if separator and (not port.isascii() or not port.isdigit() or len(port) > 5 or int(port) not in expected or str(int(port)) != port):
                raise ValueError('Unexpected port')
            if self.catalog.development and not separator:
                raise ValueError('Use the configured development port')
            if not self.catalog.development and scope.get('scheme') != 'https':
                raise ValueError('HTTPS is required')
        except (ValueError, UnicodeError):
            await self._error(send, 421, 'Unrecognized tenant host')
            return
        try:
            tenant = await asyncio.to_thread(self.catalog.resolve, host)
            if tenant is None:
                await self._error(send, 421, 'Unrecognized tenant host')
                return
            app = await asyncio.to_thread(self._application, tenant)
        except Exception:
            logger.exception('Tenant application unavailable')
            await self._error(send, 503, 'Tenant temporarily unavailable')
            return
        normalized = (host + (':' + port if self.catalog.development else '')).encode()
        scope = {**scope, 'evlet.tenant_active': lambda: self.catalog.resolve(host) == tenant, 'headers': [(key, value) for key, value in scope.get('headers', ()) if key.lower() != b'host'] + [(b'host', normalized)]}
        await app(scope, receive, send)

    @staticmethod
    async def _error(send, status, message):
        body = json.dumps({'error': message}).encode()
        await send({'type': 'http.response.start', 'status': status, 'headers': [(b'content-type', b'application/json'), (b'cache-control', b'no-store'), (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})
