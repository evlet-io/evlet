"""Export/deploy/reset preserves the Controls network boundary on real SQLite and ASGI requests."""

from dataclasses import replace

import pytest

from evlet import ControlsAccess, ValidationError
from evlet.controls_export import export
from tests.test_business import client, hashed, secured


EXPORT = '/api/_controls/overrides/export?kind=setting&key=security.controls_ips'
ADDRESSES = ('127.0.0.1', '127.2.3.4', '10.0.0.1', '192.168.1.1', '::1', '2001:db8::1',
             '2001:db8:1::1', '2001:db9::1', '::ffff:127.0.0.1')


def save(admin, values, address='127.0.0.1'):
    status, result, _ = admin('POST', '/api/_controls/overrides', {
        'changes': [{'kind': 'setting', 'key': 'security.controls_ips', 'value': values}],
    }, client=address)
    assert status == 200, result


@pytest.mark.parametrize('file,saved,address,expected', [
    (('127.0.0.1/32',), ['127.0.0.0/8'], '127.0.0.1', ('127.0.0.1/32',)),
    (('127.0.0.0/8',), ['127.0.0.1'], '127.0.0.1', ('127.0.0.1/32',)),
    (('127.0.0.0/8', '10.0.0.0/8'), ['127.0.0.0/16', '192.168.0.0/16'], '127.0.0.1', ('127.0.0.0/16',)),
    (('127.0.0.0/8', '2001:db8::/32'), ['127.0.0.1/32', '2001:db8:1::/48'], '127.0.0.1',
     ('127.0.0.1/32', '2001:db8:1::/48')),
    (('2001:db8::/32',), ['2001:db8:1::/48'], '2001:db8:1::1', ('2001:db8:1::/48',)),
    ((), ['127.0.0.0/8', '2001:db8::/32'], '127.0.0.1', ('127.0.0.0/8', '2001:db8::/32')),
    (('127.0.0.0/8', '2001:db8::/32'), [], '127.0.0.1', ('127.0.0.0/8', '2001:db8::/32')),
    ((), [], '127.0.0.1', ()),
    (('127.0.0.0/8',), ['127.0.0.0/9', '127.128.0.0/9', '127.0.0.1/32'], '127.0.0.1', ('127.0.0.0/8',)),
])
def test_network_export_preserves_access_after_deploy_and_reset(secured, tmp_path, file, saved, address, expected):
    secured.business.secret_key = tmp_path / 'secret.key'
    admin = client(secured)
    save(admin, saved)
    controls = secured.business.controls
    controls.routes = replace(controls.routes, controls=ControlsAccess(allow_ips=file, require_two_step=True))
    routes = controls.routes
    before = [admin('GET', '/api/_controls/state', client=value)[0] for value in ADDRESSES]
    assert admin('GET', '/api/_controls/state', client=address, headers={b'host': b'portal.localhost:8000'})[0] == 404
    status, blocks, _ = admin('GET', EXPORT, client=address)
    assert status == 200, blocks
    if saved:
        namespace = {'ControlsAccess': ControlsAccess}
        exec(blocks[0]['code'], namespace)
        effective = namespace['controls']
    else:
        # An empty override equals the file default and is removed, so there is no code to migrate.
        assert blocks == []
        effective = routes.controls
    assert effective.allow_ips == expected and effective.require_two_step is True
    controls.routes = replace(routes, controls=effective)
    save(admin, None, address)
    after = [admin('GET', '/api/_controls/state', client=value)[0] for value in ADDRESSES]
    assert after == before
    assert admin('GET', '/api/_controls/state', client=address, headers={b'host': b'portal.localhost:8000'})[0] == 404
    assert controls.routes.primary == routes.primary and controls.routes._subs == routes._subs
    assert next(item['value'] for item in controls.overrides()['items'] if item['key'] == 'security.controls_ips') is None


@pytest.mark.parametrize('file,saved', [
    (('127.0.0.0/8',), ['10.0.0.0/8']),
    (('2001:db8::/32',), ['2001:db9::/32']),
    (('127.0.0.0/8',), ['::1/128']),
])
def test_disjoint_networks_refuse_export_instead_of_becoming_unrestricted(secured, file, saved):
    admin = client(secured)
    save(admin, saved)
    controls = secured.business.controls
    controls.routes = replace(controls.routes, controls=ControlsAccess(allow_ips=file))
    # There is no permitted client address. The server-side exporter must also refuse unsafe code.
    assert all(admin('GET', '/api/_controls/state', client=value)[0] == 404 for value in ADDRESSES)
    with pytest.raises(ValidationError, match='do not overlap'):
        export(controls, {'kind': 'setting', 'key': 'security.controls_ips'})
    assert controls.settings['security.controls_ips'] == saved
    assert controls.routes.controls.allow_ips == file


def test_network_export_keeps_two_step_requirement_for_existing_sessions(secured, tmp_path):
    auth = secured.business.auth
    auth.security = replace(auth.security, admin_two_step=False)
    unverified = client(secured)
    secured.business.secret_key = tmp_path / 'secret.key'
    auth.security = replace(auth.security, admin_two_step=True)
    admin = client(secured)
    auth.security = replace(auth.security, admin_two_step=False)
    save(admin, ['127.0.0.0/8'])
    controls = secured.business.controls
    controls.routes = replace(controls.routes, controls=ControlsAccess(allow_ips=('127.0.0.1/32',), require_two_step=True))
    before = unverified('GET', '/api/_controls/state')[:2]
    assert before[0] == 403
    status, blocks, _ = admin('GET', EXPORT)
    assert status == 200, blocks
    namespace = {'ControlsAccess': ControlsAccess}
    exec(blocks[0]['code'], namespace)
    controls.routes = replace(controls.routes, controls=namespace['controls'])
    save(admin, None)
    assert unverified('GET', '/api/_controls/state')[:2] == before
