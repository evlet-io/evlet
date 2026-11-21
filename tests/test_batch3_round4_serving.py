"""Serving: no framework version before sign-in (SECW-07), and an image-origin setting for the CSP (SECW-08)."""

import json
import re

import pytest

from evlet.ui import serving
from support import client, http


def config(page):
    return json.loads(re.search(rb'<script id="evlet-config" type="application/json">(.*?)</script>', page)[1])


@pytest.fixture
def shell(tmp_path, monkeypatch):
    # A stand-in for the built index.html, so this runs without the frontend build.
    (tmp_path / 'index.html').write_text('<!doctype html><html lang="en"><head><title>Evlet</title></head><body></body></html>')
    monkeypatch.setattr(serving, 'STATIC', tmp_path)
    monkeypatch.setattr(serving, 'version', lambda: '9.8.7')


def test_the_version_field_is_empty_before_sign_in_and_real_after(secured, shell):
    for path in ('/login', '/docs'):
        status, page, headers = http(secured, 'GET', path)
        assert status == 200
        assert config(page)['version'] == '' and b'9.8.7' not in page
        assert not any(b'9.8.7' in value for value in headers.values())
    c = client(secured, 'editor')
    for path in ('/docs', '/app/customer', '/settings/appearance'):
        status, page, _ = c('GET', path)
        assert status == 200 and config(page)['version'] == '9.8.7'


def test_the_config_block_still_fills_in_the_version_when_a_caller_leaves_it_out(shell):
    assert json.loads(serving.config_block({'base': '/'})[len(b'<script id="evlet-config" type="application/json">'):-len(b'</script>')])['version'] == '9.8.7'


def test_image_sources_default_to_https_and_can_be_limited_to_listed_origins():
    assert "img-src 'self' https: data:;" in serving.CSP
    assert serving.content_policy() == "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; connect-src 'self'; frame-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    assert "img-src 'self' data:;" in serving.content_policy('')
    limited = serving.content_policy('https://cdn.example.com  https://img.example.com:8443')
    assert "img-src 'self' https://cdn.example.com https://img.example.com:8443 data:;" in limited and 'https:;' not in limited
    assert "img-src 'self' https://cdn.example.com data:;" in serving.content_policy(['https://cdn.example.com'])
    for bad in ('http://cdn.example.com', 'https://*.example.com', 'https://cdn.example.com/x', "'unsafe-inline'", 'https:', 'https://CDN.example.com'):
        with pytest.raises(ValueError):
            serving.content_policy(bad)
