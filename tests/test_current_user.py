"""default=CurrentUser fills a field from the signed-in user on create, on real SQLite."""

import pytest

from evlet import CurrentUser, Database, Email, Integer, Link, Model, Registry, Rule, Text
from evlet.auth import User
from test_business import client
from test_rules_types import ALL, add_user, start


class Note(Model):
    permissions = {**ALL, 'editor': Rule(('read', 'create', 'update'), scope=lambda user: {'owner': user.username})}
    title = Text(default='')
    owner = Text(required=True, default=CurrentUser)
    author_id = Integer(default=CurrentUser)


@pytest.fixture
def notes(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Note)))
    return app, client(app)


def test_current_user_fills_the_field_on_create_and_fits_a_row_scope(notes):
    app, admin = notes
    editor = add_user(app, 'editor')
    me = editor('GET', '/api/auth/session')[1]['user']
    status, note, _ = editor('POST', '/api/note', {'title': 'Mine'})
    assert status == 201, note
    assert (note['owner'], note['author_id']) == ('editor', me['id'])
    # A value sent by the caller wins, and the row scope still applies to it.
    assert admin('POST', '/api/note', {'title': 'For editor', 'owner': 'editor'})[1]['owner'] == 'editor'
    assert editor('POST', '/api/note', {'title': 'Not mine', 'owner': 'someone'})[0] in (403, 404)
    # Only on create: an update keeps the saved owner.
    url = f"/api/note/{note['id']}"
    changed = admin('PATCH', url, {'title': 'Renamed'}, headers={b'if-match': admin('GET', url)[2][b'etag']})[1]
    assert changed['owner'] == 'editor'
    fields = {field['name']: field for field in next(m for m in admin('GET', '/api/schema')[1]['models'] if m['name'] == 'note')['fields']}
    assert fields['owner']['default_kind'] == 'user' and 'default' not in fields['owner']
    with app.business.context(User(1, 'admin', 'admin')) as context:
        assert context.create(Note, title='From Python')['owner'] == 'admin'


def test_current_user_is_for_text_and_whole_number_fields():
    for field in (Email, Link):
        with pytest.raises(ValueError, match='CurrentUser'):
            field(Note, default=CurrentUser) if field is Link else field(default=CurrentUser)


def test_a_context_without_a_user_refuses_with_a_plain_message(notes):
    app, _ = notes
    with app.business.context(None) as context:
        for call in (lambda: context.create(Note, title='x'), lambda: context.get(Note, 1), lambda: context.list(Note)):
            with pytest.raises(ValueError, match='needs a User'):
                call()


class Locked(Model):
    permissions = ALL
    title = Text(default='')
    owner = Text(read_only=True, default=CurrentUser)


def test_a_read_only_current_user_field_is_filled_but_not_rewritten(tmp_path):
    app = start(Database(tmp_path / 'db.sqlite3', Registry(Locked)))
    editor = add_user(app, 'editor')
    status, note, _ = editor('POST', '/api/locked', {'title': 'Mine'})
    assert status == 201 and note['owner'] == 'editor'
    url = f"/api/locked/{note['id']}"
    assert editor('PATCH', url, {'owner': 'admin'}, headers={b'if-match': editor('GET', url)[2][b'etag']})[0] in (403, 422)
    assert editor('GET', url)[1]['owner'] == 'editor'
