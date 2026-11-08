"""How each currency code is shown on this site: its symbol (text or a small image), before or after the amount,
with or without a space (docs/controls.md#currencies).

The setting `display.currencies` holds only the codes an admin changed. Every other code shows as it always has:
the ISO code, a space, then the amount ("AED 25,200.00"). Number formatting (separators, decimals) is never
changed here, and amounts are never converted.

An image symbol is a PNG or WebP of at most 32 KB and 128 x 128 pixels, checked by its bytes, kept in the setting as
a data: URI like the images of a stored print design. SVG is refused: it can carry script.
"""

import base64
import binascii
import hashlib
from html import escape
import re
import struct
import zlib

KEY = 'display.currencies'
CODE = re.compile('[A-Z]{3}')
MAX_CODES = 30
MAX_BYTES = 32 * 1024
MAX_PIXELS = 128
MAX_SYMBOL = 8
POSITIONS = ('prefix', 'suffix')
FIELDS = {'symbol', 'image', 'position', 'space', 'tint'}
DATA_URI = re.compile(r'data:(image/png|image/webp);base64,([A-Za-z0-9+/]+={0,2})')
# Letters of right-to-left scripts: Hebrew, Arabic, Syriac, Thaana, NKo and their presentation forms.
RTL = re.compile('[֐-ࣿיִ-﷿ﹰ-ﻼ]')


def default(code):
    """How a code shows when nobody changed it: the code itself, before the amount, with a space."""
    return {'symbol': code, 'image': None, 'position': 'prefix', 'space': True, 'tint': False}


def image_size(content):
    """(type, width, height) of PNG or WebP bytes, or a ValueError naming what is wrong. Decided by the bytes only."""
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return ('image/png', *_png_size(content))
    if len(content) >= 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return ('image/webp', *_webp_size(content))
    raise ValueError('Use a PNG or WebP image. The file is not one, whatever its name says.')


def _png_size(content):
    # Every chunk must be whole with a correct checksum, IHDR first and IEND last, so a file that only starts like a PNG is refused.
    position, size, seen_data = 8, None, False
    while position < len(content):
        if position + 12 > len(content):
            raise ValueError('This PNG is cut short or damaged.')
        length, kind = struct.unpack('>I4s', content[position:position + 8])
        end = position + 12 + length
        if end > len(content):
            raise ValueError('This PNG is cut short or damaged.')
        data = content[position + 8:end - 4]
        if zlib.crc32(kind + data) != struct.unpack('>I', content[end - 4:end])[0]:
            raise ValueError('This PNG is damaged: a checksum does not match.')
        if size is None:
            if kind != b'IHDR' or length != 13:
                raise ValueError('This PNG is damaged: it does not start with its header.')
            size = struct.unpack('>II', data[:8])
        elif kind == b'IDAT':
            seen_data = True
        elif kind == b'IEND':
            if end != len(content) or not seen_data:
                raise ValueError('This PNG is damaged: data is missing or follows its end.')
            return size
        position = end
    raise ValueError('This PNG is cut short or damaged.')


def _webp_size(content):
    if struct.unpack('<I', content[4:8])[0] + 8 != len(content) or len(content) < 30:
        raise ValueError('This WebP is cut short or damaged.')
    kind, data = content[12:16], content[20:]
    if kind == b'VP8X':
        return 1 + int.from_bytes(data[4:7], 'little'), 1 + int.from_bytes(data[7:10], 'little')
    if kind == b'VP8L' and data[0] == 0x2f:
        bits = int.from_bytes(data[1:5], 'little')
        return 1 + (bits & 0x3fff), 1 + ((bits >> 14) & 0x3fff)
    if kind == b'VP8 ' and data[3:6] == b'\x9d\x01\x2a':
        return struct.unpack('<H', data[6:8])[0] & 0x3fff, struct.unpack('<H', data[8:10])[0] & 0x3fff
    raise ValueError('This WebP is damaged: its header is not one Evlet reads.')


def check_image(uri):
    """A data: URI of a PNG or WebP within the limits, returned with its type taken from the bytes."""
    match = DATA_URI.fullmatch(uri) if isinstance(uri, str) and len(uri) < MAX_BYTES * 2 else None
    if match is None:
        raise ValueError('Send the image as a PNG or WebP data: URI of at most 32 KB. SVG is not accepted.')
    try:
        content = base64.b64decode(match[2], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('The image data is not valid base64.') from None
    if len(content) > MAX_BYTES:
        raise ValueError(f'The image is {len(content) / 1024:.1f} KB. Use one of at most 32 KB.')
    kind, width, height = image_size(content)
    if not (0 < width <= MAX_PIXELS and 0 < height <= MAX_PIXELS):
        raise ValueError(f'The image is {width} x {height} pixels. Use one of at most {MAX_PIXELS} x {MAX_PIXELS}.')
    return f'data:{kind};base64,' + base64.b64encode(content).decode('ascii')


def check(value):
    """The setting's value, normalized: {code: {symbol, image, position, space, tint}}. Raises ValueError."""
    if not isinstance(value, dict):
        raise ValueError('Send currencies as an object keyed by ISO code.')
    if len(value) > MAX_CODES:
        raise ValueError(f'Set at most {MAX_CODES} currencies.')
    result = {}
    for code in sorted(value):
        entry = value[code]
        if not isinstance(code, str) or not CODE.fullmatch(code):
            raise ValueError(f'{code} is not an ISO 4217 code such as AED.')
        if not isinstance(entry, dict) or set(entry) - FIELDS:
            raise ValueError(f'{code}: send symbol, image, position, space and tint.')
        merged = {**default(code), **entry}
        symbol = merged['symbol']
        if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > MAX_SYMBOL or any(ord(char) < 32 or char in '<>' for char in symbol):
            raise ValueError(f'{code}: the symbol is 1 to {MAX_SYMBOL} characters, without < or >.')
        if merged['position'] not in POSITIONS:
            raise ValueError(f'{code}: the position is prefix or suffix.')
        if type(merged['space']) is not bool or type(merged['tint']) is not bool:
            raise ValueError(f'{code}: space and tint are true or false.')
        image = None if merged['image'] is None else check_image(merged['image'])
        normalized = {'symbol': symbol.strip(), 'image': image, 'position': merged['position'], 'space': merged['space'],
                      'tint': merged['tint'] and image is not None}
        if normalized != default(code):
            result[code] = normalized
    return result


def summary(value):
    """The setting for the audit log: each image as its type, size and a short hash, never the bytes."""
    if not isinstance(value, dict):
        return value
    def entry(item):
        image = item.get('image') if isinstance(item, dict) else None
        if not isinstance(image, str):
            return item
        content = base64.b64decode(image.partition(',')[2] or b'')
        kind = image[5:image.index(';')] if ';' in image else 'image'
        return {**item, 'image': f'{kind}, {len(content) / 1024:.1f} KB, sha256 {hashlib.sha256(content).hexdigest()[:12]}'}
    return {code: entry(item) for code, item in value.items()}


def changed_codes(before, after):
    before, after = before or {}, after or {}
    return [code for code in sorted(set(before) | set(after)) if before.get(code) != after.get(code)]


def display(displays, code):
    """How a code shows: the site's setting, or the default."""
    return (displays or {}).get(code) or default(code)


def money_html(displays, code, number):
    """An amount with its symbol as print HTML: escaped text, or the image embedded as a data: URI so PDFs render it.
    `number` is the formatted amount (already plain text); the symbol keeps its own direction."""
    shown = display(displays, code)
    if shown['image']:
        symbol = f'<img class="evlet-currency" src="{escape(shown["image"])}" alt="{escape(code)}">'
    else:
        text = escape(shown['symbol'])
        symbol = f'<bdi>{text}</bdi>' if RTL.search(shown['symbol']) else text
    gap = ' ' if shown['space'] else ''
    number = escape(number)
    return f'{symbol}{gap}{number}' if shown['position'] == 'prefix' else f'{number}{gap}{symbol}'


def set_currency_display(application, value, *, actor='cli'):
    """Trusted code: set how currency codes show on this site, as an admin does in Controls, Currencies.

    `value` maps ISO codes to {symbol, image, position, space, tint}; an image may be a data: URI, bytes or a
    path to a PNG or WebP file. The same checks apply, the change is audited under `actor` and every process picks
    it up. None resets every code to the default. Returns the saved value."""
    from .auth import User
    if value is not None:
        value = {code: {**entry, 'image': _image_uri(entry['image'])} if isinstance(entry, dict) and 'image' in entry else entry
                 for code, entry in value.items()} if isinstance(value, dict) else value
    controls = application.business.controls
    controls.save(User(0, actor, 'admin'), {'changes': [{'kind': 'setting', 'key': KEY, 'value': value}]})
    return controls.settings[KEY]


def _image_uri(image):
    from pathlib import Path
    if isinstance(image, Path):
        image = image.read_bytes()
    if isinstance(image, (bytes, bytearray)):
        kind = image_size(bytes(image))[0]
        return f'data:{kind};base64,' + base64.b64encode(bytes(image)).decode('ascii')
    return image
