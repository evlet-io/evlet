import json
import shutil
import subprocess

import pytest

from evlet import TenantRegistry
from evlet.deployment import render_deployment
from examples.playground.manage import main


def test_render_only_active_verified_domains_and_fixed_private_service_paths(tmp_path):
    catalog = TenantRegistry.create(tmp_path, managed_domains=('example.test',))
    for slug in ('alpha', 'disabled'):
        tenant = catalog.create_tenant(slug, slug.title(), slug + '.example.test')
        tenant.database_path.parent.mkdir(parents=True); tenant.database_path.touch()
    catalog.set_active('alpha', True)
    proof = catalog.add_domain('alpha', 'customer.test')
    catalog.add_domain('alpha', 'pending.test')
    catalog.verify_domain('customer.test', resolver=lambda name: (proof['value'],))
    files = render_deployment(catalog)
    caddy, service = files['Caddyfile'], files['evlet.service']
    assert 'alpha.example.test {' in caddy and 'customer.test {' in caddy
    assert 'pending.test' not in caddy and 'disabled.example.test' not in caddy
    assert 'on_demand' not in caddy and '*.example.test' not in caddy
    assert 'root * /opt/evlet/frontend/dist' in caddy
    assert 'reverse_proxy 127.0.0.1:8000' in caddy
    assert 'User=evlet' in service and 'UMask=0077' in service
    assert '--forwarded-allow-ips=127.0.0.1' in service and '--workers 1' in service
    assert 'ReadWritePaths=/var/lib/evlet' in service
    worker = files['evlet-worker.service']
    assert ' -m examples.playground.worker' in worker and 'uvicorn' not in worker
    assert 'User=evlet' in worker and 'ReadWritePaths=/var/lib/evlet' in worker
    assert 'EnvironmentFile=-/etc/evlet/server.env' in service and 'EnvironmentFile=-/etc/evlet/server.env' in worker
    # Rendering is local output only. No service or DNS changes are performed.
    target = tmp_path / 'review'
    main(['--root', str(tmp_path), 'render', '--output', str(target)])
    assert (target / 'Caddyfile').read_text() == caddy
    if shutil.which('caddy'):
        result = subprocess.run(['caddy', 'adapt', '--config', str(target / 'Caddyfile'), '--adapter', 'caddyfile'], capture_output=True, text=True, check=True)
        config = json.loads(result.stdout)
        assert config['apps']['http']['servers']
    catalog.set_domain_active('customer.test', False)
    assert 'customer.test' not in render_deployment(catalog)['Caddyfile']
    catalog.set_active('alpha', False)
    with pytest.raises(ValueError): render_deployment(catalog)


def test_cli_init_create_and_no_secret_inventory(tmp_path, capsys):
    main(['--root', str(tmp_path), 'init', '--development'])
    main(['--root', str(tmp_path), 'create', 'alpha', 'Alpha', 'alpha.localhost'])
    main(['--root', str(tmp_path), 'list'])
    assert 'alpha.localhost' in capsys.readouterr().out
    with pytest.raises(SystemExit): main(['--root', str(tmp_path), 'render', '--output', str(tmp_path / 'deploy')])
    assert not (tmp_path / 'deploy').exists()
