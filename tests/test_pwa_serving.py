"""Bundled PWA assets keep the offline notice public and root worker scope explicit."""

import pytest

from evlet.ui import serving


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
