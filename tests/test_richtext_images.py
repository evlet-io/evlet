"""Images in rich text: private attachments the HTML refers to by id (evlet/images.py, docs/internal/rich-text.md).

Upload, type and size limits, SVG refused, permission-checked serving, the holding area for unsaved
records, linking on save, deleting unreferenced images in the save transaction, no cross-record
references, and the purge of held images."""
import base64
import time

import pytest

from evlet import Application, Database, LocalAuth, Model, Policy, Registry, RichText, Rule, Text
from evlet.images import HOLD_SECONDS, image_type, purge
from evlet.richtext import sanitise
from tests.test_business import PASSWORD, http

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 40
GIF = b'GIF89a' + b'\x00' * 20
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 20
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'


class Note(Model):
    title = Text(required=True)
    body = RichText()


class Plain(Model):
    title = Text(required=True)


@pytest.fixture
def app(tmp_path):
    registry = Registry(Note, Plain)
    database = Database(tmp_path / 'images.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    policy = Policy(registry, rules={('editor', Note): Rule(('read', 'create', 'update', 'delete')),
                                     ('viewer', Note): Rule(()), ('editor', Plain): Rule(('read', 'create', 'update'))})
    application = Application(database, auth=auth, policy=policy)
    application.initialize()
    http(application, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    for role in ('editor', 'viewer'):
        application.business.auth.create_user(username=role, password=PASSWORD, role=role)
    return application


def signed_in(app, role='admin'):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': role, 'password': PASSWORD})
    assert status == 200, data
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']
    return lambda method, path, body=None, extra=None: http(app, method, path, body, cookie=cookie, csrf=csrf)


def image(content=PNG, name='photo.png'):
    return {'name': name, 'content': base64.b64encode(content).decode(), 'purpose': 'image'}


def figure(image_id, alt='A photo'):
    return f'<figure class="rt-md"><img data-attachment="{image_id}" alt="{alt}"></figure>'


def note(call, body='<p>Hello</p>'):
    status, created, headers = call('POST', '/api/note', {'title': 'N', 'body': body})
    assert status == 201, created
    return created


def rows(app):
    with app.database.transaction() as tx:
        return [dict(row) for row in tx.connection.execute("SELECT id, model, record_id, owner, purpose FROM evlet_attachments ORDER BY id")]


def test_types_are_sniffed_and_svg_is_refused():
    assert image_type(PNG) == 'image/png' and image_type(JPEG) == 'image/jpeg'
    assert image_type(GIF) == 'image/gif' and image_type(WEBP) == 'image/webp'
    assert image_type(SVG) is None and image_type(b'<?xml version="1.0"?><svg/>') is None and image_type(b'') is None


def test_upload_to_a_saved_record_serves_it_inline_to_readers_only(app):
    editor = signed_in(app, 'editor')
    record = note(editor)
    status, added, _ = editor('POST', f'/api/note/{record["id"]}/attachments', image(JPEG, 'scan.png'))
    assert status == 201, added
    assert added['type'] == 'image/jpeg' and added['held'] is False
    status, body, headers = editor('GET', f'/api/note/{record["id"]}/attachments/{added["id"]}')
    assert status == 200 and bytes(body.content if hasattr(body, 'content') else body) == JPEG
    headers = dict(headers)
    assert headers[b'content-type'] == b'image/jpeg' and headers[b'content-disposition'] == b'inline'
    assert headers[b'x-content-type-options'] == b'nosniff' and b'sandbox' in headers[b'content-security-policy']
    # Someone who cannot read the record cannot load its image.
    viewer = signed_in(app, 'viewer')
    assert viewer('GET', f'/api/note/{record["id"]}/attachments/{added["id"]}')[0] in (403, 404)
    # Images are not files: the record's file list leaves them out.
    assert rows(app)[0]['purpose'] == 'image'


def test_limits_type_and_permission(app):
    editor = signed_in(app, 'editor')
    record = note(editor)
    path = f'/api/note/{record["id"]}/attachments'
    status, error, _ = editor('POST', path, image(SVG, 'logo.svg'))
    assert status == 422 and 'SVG' in error['fields']['image']
    status, error, _ = editor('POST', path, image(b'GIF89a' + b'\x00' * (2048 * 1024)))
    assert status == 422 and '2 MiB' in error['fields']['image']
    # The cap comes from Controls (files.image_max_kb), bounded to safe values.
    app.business.controls.settings = {**app.business.controls.settings, 'files.image_max_kb': 64, 'files.images_per_record': 2}
    status, error, _ = editor('POST', path, image(b'GIF89a' + b'\x00' * (65 * 1024)))
    assert status == 422 and '64 KiB' in error['fields']['image']
    assert editor('POST', path, image())[0] == 201 and editor('POST', path, image())[0] == 201
    status, error, _ = editor('POST', path, image())
    assert status == 422 and 'at most 2 images' in error['fields']['image']
    # A model without rich text takes no images; a reader without write access cannot add one.
    status, plain, _ = editor('POST', '/api/plain', {'title': 'P'})
    assert editor('POST', f'/api/plain/{plain["id"]}/attachments', image())[0] == 404
    assert signed_in(app, 'viewer')('POST', path, image())[0] in (403, 404)


def test_settings_bounds():
    from evlet.controls import IMAGE_LIMITS, strictest
    assert IMAGE_LIMITS['files.image_max_kb'] == (64, 8192)
    assert strictest('files.image_max_kb', 99999, ()) == 8192
    assert strictest('files.images_per_record', 'x', ()) == 50


def test_unsaved_record_images_are_held_then_linked_on_save(app):
    editor = signed_in(app, 'editor')
    status, held, _ = editor('POST', '/api/note/_images', image())
    assert status == 201 and held['held'] is True
    # Only the person who added it can see a held image.
    assert editor('GET', f'/api/note/_images/{held["id"]}')[0] == 200
    assert signed_in(app, 'admin')('GET', f'/api/note/_images/{held["id"]}')[0] == 404
    created = note(editor, '<p>Before</p>' + figure(held['id']))
    assert created['body'] == '<p>Before</p>' + figure(held['id'])
    [row] = rows(app)
    assert row['record_id'] == _int_id(app, created['id'])
    # Linked: served through the record's route now, no longer from the holding area.
    assert editor('GET', f'/api/note/{created["id"]}/attachments/{held["id"]}')[0] == 200
    assert editor('GET', f'/api/note/_images/{held["id"]}')[0] == 404


def _int_id(app, text_id):
    from evlet.identity import RecordId
    return int(RecordId(text_id))


def test_another_persons_held_image_or_another_records_image_is_refused(app):
    editor, admin = signed_in(app, 'editor'), signed_in(app, 'admin')
    held = editor('POST', '/api/note/_images', image())[1]
    status, error, _ = admin('POST', '/api/note', {'title': 'Steal', 'body': figure(held['id'])})
    assert status == 422 and 'another record' in error['fields']['body']
    first = note(editor)
    owned = editor('POST', f'/api/note/{first["id"]}/attachments', image())[1]
    status, error, _ = editor('POST', '/api/note', {'title': 'Copy', 'body': figure(owned['id'])})
    assert status == 422 and 'another record' in error['fields']['body']


def test_removing_an_image_from_the_text_deletes_it_on_save(app):
    editor = signed_in(app, 'editor')
    record = note(editor)
    one = editor('POST', f'/api/note/{record["id"]}/attachments', image())[1]
    two = editor('POST', f'/api/note/{record["id"]}/attachments', image(GIF, 'b.gif'))[1]
    with app.database.transaction() as tx:
        tx.update(Note, record['id'], body='<p>x</p>' + figure(one['id']) + figure(two['id']))
    assert len(rows(app)) == 2
    with app.database.transaction() as tx:
        tx.update(Note, record['id'], body='<p>x</p>' + figure(two['id']))
    assert [row['id'] for row in rows(app)] == [int(two['id'])]
    # A failed save keeps the image: the delete is in the same transaction.
    with pytest.raises(RuntimeError):
        with app.database.transaction() as tx:
            tx.update(Note, record['id'], body='<p>none</p>')
            raise RuntimeError('rolled back')
    assert [row['id'] for row in rows(app)] == [int(two['id'])]


def test_held_images_are_purged_after_a_day(app):
    editor = signed_in(app, 'editor')
    held = editor('POST', '/api/note/_images', image())[1]
    with app.database.transaction() as tx:
        assert purge(tx, int(time.time())) == 0
        assert purge(tx, int(time.time()) + HOLD_SECONDS + 1) == 1
    assert rows(app) == []
    assert editor('GET', f'/api/note/_images/{held["id"]}')[0] == 404
    # A draft restored after the purge says the image is gone, rather than storing a dead reference.
    status, error, _ = editor('POST', '/api/note', {'title': 'Late', 'body': figure(held['id'])})
    assert status == 422 and 'no longer available' in error['fields']['body']


def test_the_sanitiser_keeps_only_attachment_references():
    stored, text = sanitise('<p>a</p><img src="https://evil.example/x.png" data-attachment="5" alt="x" onerror="alert(1)">'
                            '<img src="data:image/svg+xml,<svg onload=alert(1)>"><figure class="rt-full huge"><img data-attachment="6"></figure>')
    assert stored == '<p>a</p><figure class="rt-md"><img data-attachment="5" alt="x"></figure><figure class="rt-full"><img data-attachment="6" alt=""></figure>'
    assert 'src' not in stored and text == 'a\nx'


def test_print_embeds_the_records_own_images_only(app):
    from evlet.images import embed
    editor = signed_in(app, 'editor')
    first, second = note(editor), note(editor)
    mine = editor('POST', f'/api/note/{first["id"]}/attachments', image())[1]
    other = editor('POST', f'/api/note/{second["id"]}/attachments', image(GIF, 'g.gif'))[1]
    html = figure(mine['id']) + figure(other['id'])
    with app.database.transaction() as tx:
        printed = embed(app.business, tx, Note, _int_id(app, first['id']), html)
    encoded = base64.b64encode(PNG).decode()
    assert f'<img src="data:image/png;base64,{encoded}" data-attachment="{mine["id"]}"' in printed
    # Another record's image is never pulled into this record's print.
    assert f'<img data-attachment="{other["id"]}" alt="' in printed and 'image/gif' not in printed


def test_email_sends_the_records_images_inline(app, monkeypatch):
    import evlet.connectors as connectors
    from evlet.images import for_email
    editor = signed_in(app, 'editor')
    record = note(editor)
    added = editor('POST', f'/api/note/{record["id"]}/attachments', image())[1]
    with app.database.transaction() as tx:
        html, parts = for_email(app.business, tx, Note, _int_id(app, record['id']), '<p>See</p>' + figure(added['id']))
    assert f'src="cid:image{added["id"]}@evlet"' in html and len(parts) == 1
    sent = []

    class Client:
        def send_message(self, message):
            sent.append(message)

        def quit(self):
            pass
    monkeypatch.setattr(connectors, '_smtp', lambda values: Client())
    connectors.Connectors.deliver({'address': 'desk@example.test'}, ['b@example.test'], 'Note', 'See the note', html=html, inline=parts)
    [message] = sent
    related = [part for part in message.walk() if part.get_content_type() == 'image/png']
    assert len(related) == 1 and related[0]['Content-ID'] == f'<image{added["id"]}@evlet>'
    assert related[0].get_payload(decode=True) == PNG
