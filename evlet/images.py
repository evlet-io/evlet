"""Images in rich text and Image fields: private attachments that RichText values and Image fields refer to
(docs/internal/rich-text.md, docs/model-engine.md).

An image is a row of evlet_attachments with purpose 'image'. The HTML holds only its id
(`<img data-attachment="12">`); the browser loads it through the record's permission-checked download
route, so whoever cannot read the record cannot load its images.

- An Image field holds the id of one such image; Meta.image names the one shown as the record's avatar.
- Uploading needs write access to a RichText or Image field of the model. A saved record's image is attached to it
  at once; an unsaved record's image goes to a holding area (record_id 0) owned by the person.
- Saving a record links each held image its rich text or Image fields refer to, refuses an image of another
  record, and deletes the record's images nothing refers to any more (a replaced or cleared Image too), all in
  the save transaction. Deleting a record deletes its images.
- Held images nobody saved are purged after a day: on each upload, and by an hourly framework schedule
  when the application runs jobs.
"""

import base64
import binascii
from contextlib import contextmanager
from contextvars import ContextVar
import re
import time

from .fields import Image, RichText
from .models import ValidationError

HOLDING = 0
HOLD_SECONDS = 24 * 3600
PURGE_TASK = 'evlet_image_purge'
# PNG, JPEG, GIF and WebP only, decided by the bytes, never by the name or a declared type. SVG is refused.
SIGNATURES = (
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
)
TYPES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
EXTENSIONS = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}
DEFAULT_LIMITS = (2048 * 1024, 50)

# The person a request writes for and the image limits, set by the HTTP layer. Trusted Python has none.
WRITER = ContextVar('evlet_image_writer', default=None)


@contextmanager
def writing(user_id, limits):
    token = WRITER.set((user_id, limits))
    try:
        yield
    finally:
        WRITER.reset(token)


def image_type(content):
    """The image type of the bytes, or None when they are not PNG, JPEG, GIF or WebP."""
    for signature, kind in SIGNATURES:
        if content.startswith(signature):
            return kind
    if len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return 'image/webp'
    return None


def rich_fields(model):
    return [name for name, field in model.__fields__.items() if isinstance(field, RichText) and field.stored]


def image_fields(model):
    """The model's stored Image fields."""
    return [name for name, field in model.__fields__.items() if isinstance(field, Image) and field.stored]


def holders(model):
    """The fields whose values refer to images: RichText and Image fields."""
    return rich_fields(model) + image_fields(model)


def readable(business, user, model, record, image_id):
    """Whether the person may see this image of the record: an image an Image field holds needs read access to
    one such field. Rich text images follow the record (they are referred to inside the text)."""
    holding = [name for name in image_fields(model) if record._values.get(name) == image_id]
    if not holding:
        return True
    fields = business.policy.fields(user, model)
    return any(name in fields for name in holding)


def limits(business):
    """(bytes per image, images per record) from Controls (files.image_max_kb, files.images_per_record)."""
    try:
        settings = business.controls.settings
        return settings['files.image_max_kb'] * 1024, settings['files.images_per_record']
    except (AttributeError, KeyError, TypeError):
        return DEFAULT_LIMITS


def size_text(size):
    return f'{size // (1024 * 1024)} MiB' if size % (1024 * 1024) == 0 else f'{size // 1024} KiB'


def initialize(database):
    """Columns for images on evlet_attachments (created by the storage adapter first)."""
    with database.transaction() as tx:
        columns = database.columns(tx.connection, 'evlet_attachments')
        for name, kind in (('purpose', 'TEXT'), ('owner', 'INTEGER'), ('created_at', 'INTEGER')):
            if name not in columns:
                tx.connection.execute(f'ALTER TABLE evlet_attachments ADD COLUMN {name} {kind}')
        tx.connection.execute('CREATE INDEX IF NOT EXISTS evlet_attachment_held ON evlet_attachments(purpose,record_id,created_at)')


def upload(business, tx, user, model, record_id, body):
    """Store one image for a record (or, with record_id None, in the person's holding area). Returns the reply."""
    if not holders(model):
        raise ValidationError({'image': 'This record has no formatted text or image field to put images in'})
    writable = business.policy.fields(user, model, write=True)
    if not any(name in writable for name in holders(model)):
        raise ValidationError({'image': 'You cannot change the images of this record'})
    if set(body) - {'name', 'content', 'purpose'} or body.get('purpose') != 'image':
        raise ValidationError({'image': 'Send name, content and purpose "image"'})
    filename = body.get('name')
    if not isinstance(filename, str) or not 1 <= len(filename) <= 180 or any(ord(c) < 32 or c in '/\\' for c in filename):
        raise ValidationError({'image': 'Use a filename without paths or control characters'})
    try:
        content = base64.b64decode(body.get('content') or '', validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise ValidationError({'image': 'Invalid file encoding'}) from None
    max_size, max_count = limits(business)
    kind = image_type(content)
    if kind is None:
        raise ValidationError({'image': 'Use a PNG, JPEG, GIF or WebP image. SVG and other files are not allowed.'})
    if not 1 <= len(content) <= max_size:
        raise ValidationError({'image': f'Use an image up to {size_text(max_size)}'})
    name = business.registry.name_for(model)
    now = int(time.time())
    purge(tx, now)
    owner_record = HOLDING if record_id is None else int(record_id)
    if record_id is None:
        count = tx.connection.execute("SELECT count(*) FROM evlet_attachments WHERE purpose='image' AND model=? AND record_id=0 AND owner=?",
                                      (name, user.id)).fetchone()[0]
    else:
        count = tx.connection.execute("SELECT count(*) FROM evlet_attachments WHERE purpose='image' AND model=? AND record_id=?",
                                      (name, owner_record)).fetchone()[0]
    if count >= max_count:
        raise ValidationError({'image': f'Use at most {max_count} images per record'})
    stem = re.sub(r'\.[A-Za-z0-9]{1,5}$', '', filename) or 'image'
    image_id = business.storage.add(tx, name, owner_record, f'{stem}.{EXTENSIONS[kind]}', content)
    tx.connection.execute('UPDATE evlet_attachments SET purpose=?, owner=?, created_at=? WHERE id=?', ('image', user.id, now, image_id))
    return {'id': str(image_id), 'type': kind, 'size': len(content), 'held': record_id is None}


def held(business, tx, user, model, image_id):
    """A held image of this person, for showing it before the record is saved."""
    row = tx.connection.execute("SELECT * FROM evlet_attachments WHERE id=? AND purpose='image' AND model=? AND record_id=0 AND owner=?",
                                (image_id, business.registry.name_for(model), user.id)).fetchone()
    if row is None:
        from .auth import AccessError
        raise AccessError(404, 'Image not found')
    return row


def response(business, row):
    """An image served inline, with its sniffed type. Private, never cached by shared caches, and sandboxed."""
    from .business import Download
    content = business.storage.read(row)
    kind = image_type(content) or 'application/octet-stream'
    return 200, Download(content), [(b'content-type', kind.encode()), (b'content-disposition', b'inline'),
                                    (b'cache-control', b'private, max-age=300'), (b'x-content-type-options', b'nosniff'),
                                    (b'content-security-policy', b"default-src 'none'; sandbox")]


def settle(session, model, record_id, values):
    """In the save transaction: link held images the record's rich text refers to, refuse other records'
    images, and delete the record's images no rich text refers to any more."""
    fields = rich_fields(model)
    pictures = image_fields(model)
    if not fields and not pictures:
        return
    from .richtext import image_ids
    referenced = {}
    for name in fields:
        for image_id in image_ids(values.get(name)):
            referenced.setdefault(image_id, name)
    for name in pictures:
        if values.get(name) is not None:
            if values[name] in referenced:
                raise ValidationError({name: 'This image is already used elsewhere in the record. Upload it again.'})
            referenced[values[name]] = name
    # A bare Database (no Application storage) has no attachments, and so no images.
    if not _attachments(session):
        if referenced:
            raise ValidationError({next(iter(referenced.values())): 'Images need the application\'s private file storage'})
        return
    table = session.registry.name_for(model)
    writer = WRITER.get()
    user_id, (_, max_count) = (writer[0], writer[1]) if writer else (None, (None, 200))
    if len(referenced) > max_count:
        raise ValidationError({next(iter(referenced.values())): f'Use at most {max_count} images per record'})
    rows = session.connection.execute("SELECT id FROM evlet_attachments WHERE purpose='image' AND model=? AND record_id=?",
                                      (table, record_id)).fetchall()
    own = {row[0] for row in rows}
    for image_id, field in referenced.items():
        if image_id in own:
            continue
        found = session.connection.execute("SELECT owner, record_id FROM evlet_attachments WHERE id=? AND purpose='image' AND model=?",
                                           (image_id, table)).fetchone()
        if found is None:
            # Purged: images of an unsaved record are held for a day (HOLD_SECONDS).
            raise ValidationError({field: 'This image is no longer available. Upload it again.' if field in pictures else
                                   'An image in this text is no longer available. Remove it and add it again.'})
        # Trusted Python may link any held image; a person only their own. Never another record's image.
        if found[1] != HOLDING or (user_id is not None and found[0] != user_id):
            raise ValidationError({field: 'This image belongs to another record. Upload it again.' if field in pictures else
                                   'An image in this text belongs to another record. Add it again.'})
        session.connection.execute('UPDATE evlet_attachments SET record_id=? WHERE id=?', (record_id, image_id))
    for image_id in own - set(referenced):
        session.connection.execute('DELETE FROM evlet_attachments WHERE id=?', (image_id,))


def clone(business, tx, user, model, values):
    """For a copy of a record (a revision): each Image field's image becomes a new held image of the person,
    which saving the copy links to it. A missing image leaves the field empty."""
    name = business.registry.name_for(model)
    for field in image_fields(model):
        if values.get(field) is None:
            continue
        row = tx.connection.execute("SELECT * FROM evlet_attachments WHERE id=? AND purpose='image' AND model=?",
                                    (values[field], name)).fetchone()
        if row is None:
            values[field] = None
            continue
        image_id = business.storage.add(tx, name, HOLDING, row['name'], business.storage.read(row))
        tx.connection.execute('UPDATE evlet_attachments SET purpose=?, owner=?, created_at=? WHERE id=?',
                              ('image', getattr(user, 'id', None), int(time.time()), image_id))
        values[field] = image_id


def remove(session, model, record_id):
    """When a record is deleted: delete its images. Their files are reclaimed by the storage's collect()."""
    if holders(model) and _attachments(session):
        session.connection.execute("DELETE FROM evlet_attachments WHERE purpose='image' AND model=? AND record_id=?",
                                   (session.registry.name_for(model), int(record_id)))


def _attachments(session):
    sqlite = session.connection.__class__.__module__.startswith('sqlite3')
    row = session.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evlet_attachments'" if sqlite
                                     else "SELECT to_regclass('evlet_attachments')").fetchone()
    return bool(row and row[0])


def purge(tx, now=None):
    """Delete held images older than a day. Returns how many went."""
    now = int(time.time()) if now is None else now
    return tx.connection.execute("DELETE FROM evlet_attachments WHERE purpose='image' AND record_id=0 AND created_at<?",
                                 (now - HOLD_SECONDS,)).rowcount


def purge_job(business):
    """The hourly framework task: purge held images, then reclaim unreferenced private files."""
    def run(job, payload):
        with business.database.transaction() as tx:
            removed = purge(tx)
        collect = getattr(business.storage, 'collect', None)
        if removed and collect is not None:
            collect()
        return {'removed': removed}
    return run


IMG = re.compile(r'<img data-attachment="([1-9][0-9]{0,18})" alt="')


def _owned(business, tx, model, record_id, html):
    """{id: (bytes, type)} for the images of html that belong to the record."""
    ids = {int(match) for match in IMG.findall(html or '')}
    if not ids:
        return {}
    name = business.registry.name_for(model)
    found = {}
    for image_id in ids:
        row = tx.connection.execute("SELECT * FROM evlet_attachments WHERE id=? AND purpose='image' AND model=? AND record_id=?",
                                    (image_id, name, int(record_id))).fetchone()
        if row is not None:
            content = business.storage.read(row)
            kind = image_type(content)
            if kind:
                found[image_id] = (content, kind)
    return found


def embed(business, tx, model, record_id, html):
    """Sanitised rich text with the record's images as data: URIs, for Print Design. Images of other
    records, or missing ones, keep no source and print as their alt text's space."""
    found = _owned(business, tx, model, record_id, html)

    def source(match):
        image = found.get(int(match.group(1)))
        if image is None:
            return match.group(0)
        data = base64.b64encode(image[0]).decode()
        return f'<img src="data:{image[1]};base64,{data}" data-attachment="{match.group(1)}" alt="'
    return IMG.sub(source, html)


def for_email(business, tx, model, record_id, html):
    """Sanitised rich text for an email: the record's images become cid: references. Returns the HTML and
    the inline parts [(content id, bytes, type)] to attach with it."""
    found = _owned(business, tx, model, record_id, html)
    parts = {}

    def source(match):
        image_id = int(match.group(1))
        if image_id not in found:
            return match.group(0)
        cid = f'image{image_id}@evlet'
        parts[image_id] = (cid, *found[image_id])
        return f'<img src="cid:{cid}" data-attachment="{image_id}" alt="'
    return IMG.sub(source, html), list(parts.values())
