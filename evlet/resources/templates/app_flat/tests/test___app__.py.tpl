"""Tests for {{title}}: a temporary instance on real SQLite, through the real HTTP API and its permission checks.

Run from the project folder: .venv/bin/python -m pytest apps/{{name}}/tests
See the testing guide (`evlet docs`, "Testing an app").
"""

from pathlib import Path

from evlet.testing import TestApp
import pytest

APP = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    with TestApp(APP, directory=tmp_path) as running:
        yield running


def test_an_editor_creates_an_item(app):
    editor = app.client(role='editor')
    created = editor.post('/api/{{table}}', {'title': 'First item'})
    assert created.status == 201
    assert editor.get('/api/{{table}}/' + created.data['id']).data['title'] == 'First item'


def test_a_viewer_reads_but_cannot_create(app):
    with app.context(role='admin') as ctx:
        ctx.create(app.model('{{Class}}Item'), title='Shared')
    viewer = app.client(role='viewer')
    assert [item['title'] for item in viewer.get('/api/{{table}}').data['items']] == ['Shared']
    assert viewer.post('/api/{{table}}', {'title': 'Not allowed'}).status == 403


def test_a_title_is_required(app):
    refused = app.as_admin().post('/api/{{table}}', {})
    assert refused.status == 422 and 'title' in refused.data['fields']
