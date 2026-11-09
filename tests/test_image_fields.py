"""Image fields: one private image attachment per field, and Meta.image (evlet/images.py, docs/model-engine.md).

Declaration checks, schema output, upload type and size refusal, linking on save, replace and clear,
no cross-record or cross-person images, field-level read and write checks on the image route, journal,
migrations, and deleting the record with its images. Real SQLite throughout."""
import base64

import pytest

from evlet import (AddField, Application, Database, Image, LocalAuth, Migration, Model, Policy, Registry, RichText, Rule,
                   State, Text, ValidationError)
from evlet.declarations import Move
from evlet.journal import Journal
from tests.test_business import PASSWORD, http

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 20
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'


class Item(Model):
    class Meta:
        image = 'photo'
        journal = Journal()
    label = Text(required=True)
    photo = Image(label='Photo')
    logo = Image()


class Card(Model):
    """A revisable document: a revision gets its own copy of the image."""
    class Meta:
        revisable = True
        image = 'photo'
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete', 'void', 'revise'))}
    label = Text(default='')
    photo = Image()
    status = State(('draft', 'issued', 'void'), moves=(Move('draft', 'issued', 'Issue'),),
                   locked=('issued', 'void'), final=('void',), voidable=('issued',))


class Memo(Model):
    """A document with rich text: its images are linked on save like a plain model's."""
    class Meta:
        journal = Journal()
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    label = Text(default='')
    body = RichText()


class Plain(Model):
    label = Text(required=True)


@pytest.fixture
def app(tmp_path):
    registry = Registry(Item, Card, Memo, Plain)
    database = Database(tmp_path / 'image-fields.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    every = ('read', 'create', 'update', 'delete')
    policy = Policy(registry, roles={'clerk': 'editor', 'guest': 'editor'}, rules={
        ('editor', Item): Rule(every), ('editor', Plain): Rule(every),
        # Reads every field but may change only the name: no image uploads.
        ('clerk', Item): Rule(('read', 'create', 'update'), write_fields=('label',)),
        # Never sees the photo, so never loads it; may change the logo.
        ('guest', Item): Rule(('read', 'update'), read_fields=('label', 'logo'), write_fields=('logo',)),
    })
    application = Application(database, auth=auth, policy=policy)
    application.initialize()
    http(application, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    for role in ('editor', 'clerk', 'guest'):
        application.business.auth.create_user(username=role, password=PASSWORD, role=role)
    return application


def signed_in(app, role='editor'):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': role, 'password': PASSWORD})
    assert status == 200, data
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']

    def call(method, path, body=None, etag=None):
        headers = None
        if etag is not None:
            headers = {b'if-match': http(app, 'GET', etag, cookie=cookie, csrf=csrf)[2][b'etag']}
        elif method in ('PATCH', 'DELETE') and '/attachments' not in path:
            # Documents (Meta.journal) need the revision the person saw.
            headers = {b'if-match': http(app, 'GET', path, cookie=cookie, csrf=csrf)[2][b'etag']}
        return http(app, method, path, body, cookie=cookie, csrf=csrf, headers=headers)
    return call


def upload(content=PNG, name='photo.png'):
    return {'name': name, 'content': base64.b64encode(content).decode(), 'purpose': 'image'}


def images(app):
    with app.database.transaction() as tx:
        return [dict(row) for row in tx.connection.execute("SELECT id, model, record_id FROM evlet_attachments WHERE purpose='image' ORDER BY id")]


def held(call, content=PNG, model='item'):
    status, added, _ = call('POST', f'/api/{model}/_images', upload(content))
    assert status == 201, added
    assert added['held'] is True
    return added['id']


def new_item(call, **values):
    status, record, _ = call('POST', '/api/item', {'label': 'Shelf', **values})
    assert status == 201, record
    return record


def body_of(reply):
    return bytes(reply.content if hasattr(reply, 'content') else reply)


def test_declaration_checks():
    field = Image()
    assert field.kind == 'image' and field.copy is False and field.metadata()['type'] == 'image'
    for options in ({'copy': True}, {'index': True}, {'unique': True}, {'default': 1}):
        with pytest.raises(ValueError):
            Image(**options)
    assert field.validate('12') == 12 and field.validate(12) == 12 and field.serialize(12) == '12'
    for bad in ('x', '0', 0, -1, True, 1.5, 2**63):
        with pytest.raises(ValueError):
            field.validate(bad)
    with pytest.raises(ValueError, match=r'Meta.image: name an Image field of Bad, not .name.; it declares none'):
        type('Bad', (Model,), {'name': Text(), 'Meta': type('Meta', (), {'image': 'name'})})
    with pytest.raises(ValueError, match=r'it has photo'):
        type('Bad', (Model,), {'name': Text(), 'photo': Image(), 'Meta': type('Meta', (), {'image': 'logo'})})
    assert Item.__image__ == 'photo' and Plain.__image__ is None


def test_schema_has_the_image_type_and_the_model_image(app):
    schema = signed_in(app)('GET', '/api/schema')[1]
    item = next(model for model in schema['models'] if model['name'] == 'item')
    photo = next(field for field in item['fields'] if field['name'] == 'photo')
    assert photo['type'] == 'image' and photo['operators'] == ['isnull']
    assert item['image'] == 'photo'
    assert 'image' not in next(model for model in schema['models'] if model['name'] == 'plain')
    # A role that cannot read the photo gets neither the field nor the model's image key.
    guest = next(model for model in signed_in(app, 'guest')('GET', '/api/schema')[1]['models'] if model['name'] == 'item')
    assert 'image' not in guest and [field['name'] for field in guest['fields'] if field['type'] == 'image'] == ['logo']


def test_a_new_record_links_a_held_image_and_serves_it(app):
    editor = signed_in(app)
    image_id = held(editor)
    # Before save, the person who uploaded it can preview it from the holding area.
    status, reply, headers = editor('GET', f'/api/item/_images/{image_id}')
    assert status == 200 and body_of(reply) == PNG and dict(headers)[b'content-type'] == b'image/png'
    record = new_item(editor, photo=image_id)
    assert record['photo'] == image_id and record['logo'] is None
    linked = images(app)
    assert len(linked) == 1 and linked[0]['id'] == int(image_id) and linked[0]['model'] == 'item' and linked[0]['record_id'] != 0
    status, reply, headers = editor('GET', f'/api/item/{record["id"]}/attachments/{image_id}')
    assert status == 200 and body_of(reply) == PNG
    headers = dict(headers)
    assert headers[b'content-disposition'] == b'inline' and b'sandbox' in headers[b'content-security-policy']
    # The image is not one of the record's files.
    assert editor('GET', f'/api/item/{record["id"]}')[1]['photo'] == image_id


def test_type_and_size_are_checked_on_the_server(app):
    editor = signed_in(app)
    status, error, _ = editor('POST', '/api/item/_images', upload(SVG, 'logo.svg'))
    assert status == 422 and 'SVG' in error['fields']['image']
    status, error, _ = editor('POST', '/api/item/_images', upload(b'not an image', 'x.png'))
    assert status == 422 and 'PNG, JPEG, GIF or WebP' in error['fields']['image']
    status, error, _ = editor('POST', '/api/item/_images', upload(b'GIF89a' + b'\x00' * (2048 * 1024)))
    assert status == 422 and '2 MiB' in error['fields']['image']
    app.business.controls.settings = {**app.business.controls.settings, 'files.image_max_kb': 64}
    status, error, _ = editor('POST', '/api/item/_images', upload(b'GIF89a' + b'\x00' * (65 * 1024)))
    assert status == 422 and '64 KiB' in error['fields']['image']
    assert held(editor, WEBP)
    # A field value must be an image id; a model without image or rich text fields takes no uploads.
    status, error, _ = editor('POST', '/api/item', {'label': 'X', 'photo': 'photo.png'})
    assert status == 422 and 'photo' in error['fields']
    assert editor('POST', '/api/plain/_images', upload())[0] == 422


def test_replace_and_clear_delete_the_old_image(app):
    editor = signed_in(app)
    record = new_item(editor, photo=held(editor))
    url = f'/api/item/{record["id"]}'
    first = record['photo']
    status, added, _ = editor('POST', f'{url}/attachments', upload(WEBP, 'new.webp'))
    assert status == 201 and added['held'] is False
    status, updated, _ = editor('PATCH', url, {'photo': added['id']})
    assert status == 200 and updated['photo'] == added['id']
    assert [row['id'] for row in images(app)] == [int(added['id'])]
    assert editor('GET', f'{url}/attachments/{first}')[0] == 404
    # Images are removed by clearing the field and saving, not with DELETE.
    assert editor('DELETE', f'{url}/attachments/{added["id"]}')[0] in (404, 405)
    status, updated, _ = editor('PATCH', url, {'photo': None})
    assert status == 200 and updated['photo'] is None and images(app) == []


def test_images_of_other_records_and_people_are_refused(app):
    editor = signed_in(app)
    one = new_item(editor, photo=held(editor))
    # Duplicate leaves the image out (copy=False); a create that sends another record's image is refused.
    status, error, _ = editor('POST', '/api/item', {'label': 'Copy', 'photo': one['photo']})
    assert status == 422 and 'another record' in error['fields']['photo']
    # The same image in two fields of one record is refused too.
    image_id = held(editor)
    status, error, _ = editor('POST', '/api/item', {'label': 'Twice', 'photo': image_id, 'logo': image_id})
    assert status == 422 and 'logo' in error['fields']
    # Someone else's held image is not theirs to use.
    guest_image = held(signed_in(app, 'admin'))
    status, error, _ = editor('POST', '/api/item', {'label': 'Other', 'photo': guest_image})
    assert status == 422 and 'another record' in error['fields']['photo']
    # An image that does not exist.
    status, error, _ = editor('POST', '/api/item', {'label': 'Gone', 'photo': '999999'})
    assert status == 422 and 'no longer available' in error['fields']['photo']


def test_field_checks_on_read_and_write(app):
    editor = signed_in(app)
    record = new_item(editor, photo=held(editor))
    url = f'/api/item/{record["id"]}'
    # A role that cannot read the photo field gets no value and cannot load the image.
    guest = signed_in(app, 'guest')
    assert 'photo' not in guest('GET', url)[1]
    assert guest('GET', f'{url}/attachments/{record["photo"]}')[0] == 404
    # It may still set the logo it can write.
    status, added, _ = guest('POST', f'{url}/attachments', upload())
    assert status == 201, added
    status, updated, _ = guest('PATCH', url, {'logo': added['id']})
    assert status == 200 and updated['logo'] == added['id']
    assert guest('GET', f'{url}/attachments/{added["id"]}')[0] == 200
    # A role that cannot write any image field cannot upload, and cannot set one.
    clerk = signed_in(app, 'clerk')
    assert clerk('POST', f'{url}/attachments', upload())[0] == 422
    assert clerk('POST', '/api/item/_images', upload())[0] == 422
    assert clerk('PATCH', url, {'photo': None})[0] in (403, 422)
    assert clerk('GET', f'{url}/attachments/{record["photo"]}')[0] == 200


def test_changes_are_journaled(app):
    editor = signed_in(app)
    record = new_item(editor, photo=held(editor))
    url = f'/api/item/{record["id"]}'
    status, added, _ = editor('POST', f'{url}/attachments', upload(WEBP))
    editor('PATCH', url, {'photo': added['id']})
    status, page, _ = editor('GET', f'{url}/journal')
    assert status == 200, page
    # The journal records the image ids, as text, like any other value.
    changed, created = page['items'][0], page['items'][1]
    assert changed['kind'] == 'changed' and changed['data']['fields']['photo'] == {'old': record['photo'], 'new': added['id']}
    assert created['data']['fields']['photo'] == {'old': None, 'new': record['photo']}


def test_deleting_the_record_deletes_its_images(app):
    editor = signed_in(app)
    record = new_item(editor, photo=held(editor), logo=held(editor))
    assert len(images(app)) == 2
    assert editor('DELETE', f'/api/item/{record["id"]}')[0] == 204
    assert images(app) == []
    # Trusted Python deletes go the same way.
    record = new_item(editor, photo=held(editor))
    with app.database.transaction() as tx:
        tx.delete(Item, record['id'])
    assert images(app) == []


def test_trusted_session_writes_follow_the_same_rules(app):
    editor = signed_in(app)
    image_id = held(editor)
    other = new_item(editor, photo=held(editor))
    with app.database.transaction() as tx:
        created = tx.create(Item, label='Trusted', photo=image_id)
    assert created.photo == int(image_id)
    assert [row['record_id'] for row in images(app) if row['id'] == int(image_id)] == [int(created.id)]
    with pytest.raises(ValidationError) as error:
        with app.database.transaction() as tx:
            tx.update(Item, created.id, photo=other['photo'])
    assert 'another record' in error.value.errors['photo']
    with app.database.transaction() as tx:
        tx.update(Item, created.id, photo=None)
    assert int(image_id) not in [row['id'] for row in images(app)]


def test_a_bare_database_has_no_image_storage(tmp_path):
    database = Database(tmp_path / 'bare.sqlite3', Registry(Item, Plain))
    database.initialize()
    assert database.create(Item, label='No photo').photo is None
    with pytest.raises(ValidationError, match='file storage'):
        database.create(Item, label='Photo', photo=1)


def test_adding_an_image_field_migrates(tmp_path):
    Before = type('Product', (Model,), {'name': Text(required=True)})
    path = tmp_path / 'migrate.sqlite3'
    old = Database(path, Registry(Before))
    old.initialize()
    kept = old.create(Before, name='Kept')
    After = type('Product', (Model,), {'name': Text(required=True), 'photo': Image(),
                                      'Meta': type('Meta', (), {'image': 'photo'})})
    new = Database(path, Registry(After))
    assert new.migrate([Migration('0001_photo', AddField('product', 'photo', Image()))]) == ['0001_photo']
    new.initialize()
    assert new.get(After, kept.id).photo is None



def test_a_revision_gets_its_own_copy_of_the_image(app):
    admin = signed_in(app, 'admin')
    status, card, _ = admin('POST', '/api/card', {'label': 'C', 'photo': held(admin, model='card')})
    assert status == 201, card
    url = f'/api/card/{card["id"]}'
    assert admin('POST', f'{url}/moves/issued', {}, etag=url)[0] == 200
    status, copy, _ = admin('POST', f'{url}/revise', {'reason': 'Wrong label'}, etag=url)
    assert status in (200, 201), copy
    assert copy['photo'] and copy['photo'] != card['photo']
    # Both records keep their own image, each served from its own record.
    for record in (card, copy):
        status, reply, _ = admin('GET', f'/api/card/{record["id"]}/attachments/{record["photo"]}')
        assert status == 200 and body_of(reply) == PNG


def test_a_document_links_its_rich_text_images(app):
    admin = signed_in(app, 'admin')
    image_id = held(admin, model='memo')
    html = f'<figure class="rt-md"><img data-attachment="{image_id}" alt="A photo"></figure>'
    status, memo, _ = admin('POST', '/api/memo', {'label': 'M', 'body': html})
    assert status == 201, memo
    assert [row['record_id'] != 0 for row in images(app) if row['id'] == int(image_id)] == [True]
    status, _, _ = admin('PATCH', f'/api/memo/{memo["id"]}', {'body': '<p>No image</p>'})
    assert status == 200 and int(image_id) not in [row['id'] for row in images(app)]
