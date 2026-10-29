"""Render a single-server Caddy/systemd reference configuration for review."""


def render_deployment(catalog):
    if catalog.development:
        raise ValueError('Deployment requires a production catalog')
    inventory = catalog.inventory()
    active = {row['slug'] for row in inventory['tenants'] if row['active']}
    hosts = [row['hostname'] for row in inventory['domains'] if row['tenant'] in active and row['active'] and row['verified_at'] is not None]
    if not hosts:
        raise ValueError('There are no verified active tenant hosts')
    return deployment_files(hosts)


def deployment_files(hosts):
    from .tenancy import hostname
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
    worker = service.replace('Description=Evlet tenant application', 'Description=Evlet tenant job worker').replace(' -m uvicorn examples.playground.tenants:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1', ' -m examples.playground.worker')
    return {'Caddyfile': '\n'.join(sites), 'evlet.service': service, 'evlet-worker.service': worker}
