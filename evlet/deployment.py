"""Render a single-server Caddy/systemd reference configuration for review."""


def render_deployment(catalog, *, separate_worker=False):
    """Render reference files for the catalog's verified active hosts.

    The units start the example tenant app (`examples.playground.tenants`, and with
    separate_worker=True `examples.playground.worker`) from a source checkout in /opt/evlet. An
    installed project must adapt the ExecStart commands and paths before use.
    """
    if catalog.development:
        raise ValueError('Deployment requires a production catalog')
    inventory = catalog.inventory()
    active = {row['slug'] for row in inventory['tenants'] if row['active']}
    hosts = [row['hostname'] for row in inventory['domains'] if row['tenant'] in active and row['active'] and row['verified_at'] is not None]
    if not hosts:
        raise ValueError('There are no verified active tenant hosts')
    return deployment_files(hosts, separate_worker=separate_worker)


def deployment_files(hosts, *, separate_worker=False):
    """Return a Caddyfile and systemd units for the example tenant app; review before use.

    The API unit runs the tenants' jobs in its own process (EVLET_TENANT_JOBS=1, TenantApplication(jobs=...)),
    so job steps take the same write queue as requests, as evlet serve --jobs does for one instance.
    separate_worker=True renders the older layout instead: the API without jobs plus evlet-worker.service, a
    second process writing the same SQLite files outside that queue, so long job steps slow people's saves
    (docs/tenancy-deployment.md)."""
    from .tenancy import hostname
    from .ui.serving import CSP
    hosts = sorted({hostname(host) for host in hosts})
    if not hosts or any(host.endswith('.localhost') for host in hosts):
        raise ValueError('Use exact production hostnames')
    sites = []
    for host in hosts:
        sites.append(f'''{host} {{
    encode zstd gzip
    @backend path /api /api/* /health
    handle @backend {{
        reverse_proxy 127.0.0.1:8000
    }}
    handle {{
        root * /opt/evlet/frontend/dist
        try_files {{path}} /index.html
        file_server
    }}
    header X-Content-Type-Options nosniff
    header Content-Security-Policy "{CSP}"
    header Strict-Transport-Security "max-age=31536000"
}}
''')
    service = '''[Unit]
Description=Evlet tenant application
After=network.target

[Service]
Type=simple
User=evlet
Group=evlet
WorkingDirectory=/opt/evlet
Environment=EVLET_TENANT_ROOT=/var/lib/evlet
Environment=EVLET_TENANT_JOBS=JOBS
EnvironmentFile=-/etc/evlet/server.env
ExecStart=/opt/evlet/.venv/bin/python -m uvicorn examples.playground.tenants:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5
UMask=0077
StateDirectory=evlet
StateDirectoryMode=0700
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/evlet

[Install]
WantedBy=multi-user.target
'''
    files = {'Caddyfile': '\n'.join(sites), 'evlet.service': service.replace('EVLET_TENANT_JOBS=JOBS', 'EVLET_TENANT_JOBS=' + ('0' if separate_worker else '1'))}
    if not separate_worker:
        return files
    worker = files['evlet.service'].replace('Description=Evlet tenant application', 'Description=Evlet tenant job worker').replace(' -m uvicorn examples.playground.tenants:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1', ' -m examples.playground.worker')
    return {**files, 'evlet-worker.service': worker}
