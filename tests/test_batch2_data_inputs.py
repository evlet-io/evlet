"""Backend input checks: strict decimal text, image dimensions read from the header, and write errors that
name the refused fields (evlet/fields.py, evlet/images.py, evlet/permissions.py)."""

import struct
import zlib

import pytest

from evlet import Currency, Decimal, Image, Model, Rule, Text
from evlet.calc import NUMBER
from evlet.fields import DECIMAL_TEXT
from evlet.images import MAX_PIXELS, check_dimensions, dimensions
from evlet.models import ValidationError
from test_business import client
from test_rules_types import add_user, application

ALL = ('read', 'create', 'update', 'delete')


# ---------- decimal text ----------

@pytest.mark.parametrize('text', ['1_000.50', '١٠٠', ' 12.5 ', '12.5 ', '1e2', '1E2', '+5', '.5', '5.', '007',
                                  'NaN', 'Infinity', '-', '', '１'])
def test_decimal_refuses_text_outside_the_calculation_grammar(text):
    for field in (Decimal(scale=2), Currency()):
        with pytest.raises(ValueError, match='decimal string'):
            field.validate(text)


@pytest.mark.parametrize('text', ['0', '-0', '12', '-12.5', '1000.50', '0.25'])
def test_decimal_accepts_plain_decimal_text(text):
    assert str(Decimal(scale=2).validate(text)) == format(Decimal(scale=2).validate(text), '.2f')


def test_decimal_grammar_matches_calc():
    assert DECIMAL_TEXT.pattern == NUMBER.pattern


# ---------- image dimensions ----------

def png(width, height):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IEND', b'')


def jpeg(width, height):
    app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00' + b'\x00' * 9
    sof = b'\xff\xc2' + struct.pack('>HBHHB', 11, 8, height, width, 1) + b'\x01\x11\x00'
    return b'\xff\xd8' + app0 + b'\xff\xff' + sof + b'\xff\xd9'


def gif(width, height):
    return b'GIF89a' + struct.pack('<HH', width, height) + b'\x00' * 3 + b';'


def webp(chunk, payload):
    body = b'WEBP' + chunk + struct.pack('<I', len(payload)) + payload
    return b'RIFF' + struct.pack('<I', len(body)) + body


def test_dimensions_are_read_from_each_header():
    assert dimensions(png(640, 480)) == (640, 480)
    assert dimensions(jpeg(800, 600)) == (800, 600)
    assert dimensions(gif(32, 16)) == (32, 16)
    assert dimensions(webp(b'VP8 ', b'\x00\x00\x00\x9d\x01\x2a' + struct.pack('<HH', 300, 200) + b'\x00' * 4)) == (300, 200)
    bits = (1000 - 1) | ((500 - 1) << 14)
    assert dimensions(webp(b'VP8L', b'\x2f' + bits.to_bytes(4, 'little') + b'\x00' * 4)) == (1000, 500)
    assert dimensions(webp(b'VP8X', b'\x00' * 4 + (4000 - 1).to_bytes(3, 'little') + (3000 - 1).to_bytes(3, 'little'))) == (4000, 3000)
    assert dimensions(b'\x89PNG\r\n\x1a\n' + b'\x00' * 40) is None
    assert dimensions(b'\xff\xd8\xff\xe0\x00') is None
    assert dimensions(b'not an image') is None


def test_a_decompression_bomb_is_refused_from_its_header():
    assert check_dimensions(png(8000, 5000)) == (8000, 5000)
    for bomb in (png(13000, 13000), jpeg(65000, 65000), gif(65535, 65535)):
        with pytest.raises(ValidationError, match='megapixels'):
            check_dimensions(bomb)
    assert 13000 * 13000 > MAX_PIXELS
    for damaged in (png(0, 10), b'\x89PNG\r\n\x1a\n' + b'\x00' * 40):
        with pytest.raises(ValidationError, match='damaged'):
            check_dimensions(damaged)


class Card(Model):
    permissions = {'admin': Rule(ALL), 'editor': Rule(('read', 'create', 'update'), read_fields=('title', 'locked'),
                                                       write_fields=('title',))}
    title = Text(required=True)
    locked = Text(default='')
    secret = Text(default='')
    picture = Image()


@pytest.fixture
def app(tmp_path):
    return application(tmp_path, Card)


def test_an_image_upload_over_the_pixel_limit_is_refused(app):
    import base64
    c = client(app)
    def upload(content):
        return c('POST', '/api/card/_images', {'name': 'p.png', 'content': base64.b64encode(content).decode(), 'purpose': 'image'})
    status, error, _ = upload(png(13000, 13000))
    assert status == 422 and 'megapixels' in error['fields']['image']
    status, reply, _ = upload(png(64, 64))
    assert status in (200, 201) and reply['type'] == 'image/png'


# ---------- write errors name the fields ----------

def test_write_errors_name_the_refused_fields(app):
    admin = client(app)
    status, error, _ = admin('POST', '/api/card', {'title': 'x', 'titel': 'y'})
    assert (status, error) == (403, {'error': 'Unknown field or not yours to write: titel'})
    editor = add_user(app, 'editor')
    # A field the editor reads but may not change is a permission error that names it.
    status, error, _ = editor('POST', '/api/card', {'title': 'x', 'locked': 'y'})
    assert status == 403 and error['error'] == 'You cannot change locked'
    # A field the editor cannot read gets the answer a misspelt one gets: the reply never tells it exists.
    hidden = editor('POST', '/api/card', {'title': 'x', 'secret': 'y'})[:2]
    unknown = editor('POST', '/api/card', {'title': 'x', 'nothing': 'y'})[:2]
    assert hidden == (403, {'error': 'Unknown field or not yours to write: secret'})
    assert unknown == (403, {'error': 'Unknown field or not yours to write: nothing'})
