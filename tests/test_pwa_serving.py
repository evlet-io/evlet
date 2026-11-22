"""Bundled PWA assets keep the offline notice public and root worker scope explicit."""

import json
from pathlib import Path

import pytest

from evlet.ui import serving
from support import client, http


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(serving, 'STATIC', tmp_path)
    folder = tmp_path / 'assets'
    folder.mkdir()
    for name, body in {'evlet-sw.js': b'/* worker */', 'other.js': b'/* ordinary asset */',
                       'evlet-offline.html': b'<html><head></head><body>Offline</body></html>',
                       'evlet.webmanifest': b'{"name":"Evlet"}', 'icon.png': b'\x89PNG'}.items():
        (folder / name).write_bytes(body)
    return folder


def test_only_the_exact_bundled_worker_gets_root_scope_and_revalidation(assets):
    status, content, headers = serving.static_response('/assets/evlet-sw.js')
    assert status == 200 and content.content == b'/* worker */'
    assert dict(headers)[b'service-worker-allowed'] == b'/'
    assert dict(headers)[b'cache-control'] == b'no-cache'
    assert b'service-worker-allowed' not in dict(serving.static_response('/assets/other.js')[2])
    assert serving.static_response('/assets/../secret.js') is None


def test_offline_notice_never_contains_runtime_configuration_or_personal_theme(assets):
    status, content, headers = serving.static_response('/assets/evlet-offline.html',
                                                       {'private_value': 'must-not-cache', 'theme': 'dark'}, theme='dark')
    assert status == 200
    assert content.content == (assets / 'evlet-offline.html').read_bytes()
    assert b'evlet-config' not in content.content and b'data-theme' not in content.content
    assert b'service-worker-allowed' not in dict(headers)


@pytest.mark.parametrize('name,mime', [('evlet.webmanifest', b'application/manifest+json'), ('icon.png', b'image/png')])
def test_manifest_and_generated_icons_have_explicit_types(assets, name, mime):
    assert dict(serving.static_response('/assets/' + name)[2])[b'content-type'] == mime


BROWSER_FETCH = {b'cookie': b'', b'x-evlet-request': b'', b'sec-fetch-site': b'same-origin', b'sec-fetch-mode': b'cors'}


def test_manifest_is_public_and_names_the_instance(secured):
    status, body, headers = http(secured, 'GET', '/assets/evlet.webmanifest', headers={**BROWSER_FETCH, b'sec-fetch-dest': b'manifest'})
    assert status == 200
    assert headers[b'content-type'] == b'application/manifest+json'
    assert headers[b'cache-control'] == b'no-cache'
    assert headers[b'x-content-type-options'] == b'nosniff'
    policy = headers[b'content-security-policy'].decode()
    assert "manifest-src 'self'" in policy and "worker-src 'self'" in policy and "default-src 'self'" in policy
    manifest = json.loads(body)
    assert manifest['name'] == secured.name and manifest['short_name']
    assert {key: manifest[key] for key in ('id', 'start_url', 'scope', 'display')} == {'id': '/', 'start_url': '/', 'scope': '/', 'display': 'standalone'}
    assert (manifest['theme_color'], manifest['background_color']) == ('#2563eb', '#f4f5f8')
    icons = {(icon['sizes'], icon['purpose']): icon['src'] for icon in manifest['icons']}
    assert icons == {('192x192', 'any'): '/assets/evlet-icon-192.png', ('512x512', 'any'): '/assets/evlet-icon-512.png',
                     ('512x512', 'maskable'): '/assets/evlet-icon-maskable-512.png'}
    # Signed in or not, it says the same: nothing about the person or the release.
    assert client(secured)('GET', '/assets/evlet.webmanifest')[1] == body
    assert b'version' not in body and b'@' not in body


def test_manifest_name_is_trimmed_to_a_short_name():
    manifest = json.loads(serving.manifest_response('  Acme   Trading Company  ')[1].content)
    assert manifest['name'] == 'Acme Trading Company' and manifest['short_name'] == 'Acme'
    assert json.loads(serving.manifest_response('')[1].content)['name'] == 'Evlet'


def test_bundled_icons_are_png_at_their_declared_sizes():
    import struct
    folder = Path(__file__).resolve().parents[1] / 'frontend/public/assets'
    for name, size in {'evlet-icon-192.png': 192, 'evlet-icon-512.png': 512, 'evlet-icon-maskable-512.png': 512, 'evlet-apple-touch-icon.png': 180}.items():
        data = (folder / name).read_bytes()
        assert data[:8] == b'\x89PNG\r\n\x1a\n' and struct.unpack('>II', data[16:24]) == (size, size), name


def test_worker_route_is_public_with_root_scope_and_nothing_else_opens(secured, assets):
    status, body, headers = http(secured, 'GET', '/assets/evlet-sw.js', headers={**BROWSER_FETCH, b'sec-fetch-dest': b'serviceworker', b'service-worker': b'script'})
    assert status == 200 and body == b'/* worker */'
    assert headers[b'content-type'].startswith(b'text/javascript')
    assert headers[b'service-worker-allowed'] == b'/' and headers[b'cache-control'] == b'no-cache'
    assert b"worker-src 'self'" in headers[b'content-security-policy']
    assert http(secured, 'GET', '/assets/evlet-offline.html', headers=BROWSER_FETCH)[0] == 200
    # Without a session the API stays closed.
    assert http(secured, 'GET', '/api/customer', headers=BROWSER_FETCH)[0] == 401
