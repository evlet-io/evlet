"""Each currency code on this site: how many decimal places its amounts have, and how it is shown: its symbol
(text or a small image), before or after the amount, with or without a space (docs/controls.md#currencies).

The setting `display.currencies` holds only the codes an admin changed or added. Every other code has its ISO 4217
minor units (AED 2, KWD 3, JPY 0; a code ISO does not list has 2) and shows as it always has: the ISO code, a space,
then the amount ("AED 25,200.00"). Amounts are never converted.

Amounts are stored exactly at MONEY_SCALE (6) decimal places whatever their currency, so changing a currency's
decimals never rewrites stored values: raising them is free, and lowering them is refused while a stored amount in
that currency has more digits (round_stored rounds them first, as an explicit step).

An image symbol is a PNG or WebP of at most 32 KB and 128 x 128 pixels, checked by its bytes, kept in the setting as
a data: URI like the images of a stored print design. SVG is refused: it can carry script.
"""

import base64
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
import binascii
import hashlib
from html import escape
import re
import struct
import zlib

from . import qb

KEY = 'display.currencies'
CODE = re.compile('[A-Z]{3}')
MAX_CODES = 30
MAX_BYTES = 32 * 1024
MAX_PIXELS = 128
MAX_SYMBOL = 8
POSITIONS = ('prefix', 'suffix')
FIELDS = {'symbol', 'image', 'position', 'space', 'tint', 'decimals'}
# Every Currency amount is stored as an integer count of millionths, whatever its currency (docs/model-engine.md).
MONEY_SCALE = 6
MAX_DECIMALS = 6
DEFAULT_DECIMALS = 2
# ISO 4217 minor units of the active codes that do not have 2 (the published list, without funds and metals).
ISO_DECIMALS = {
    **dict.fromkeys(('BIF', 'CLP', 'DJF', 'GNF', 'ISK', 'JPY', 'KMF', 'KRW', 'PYG', 'RWF', 'UGX', 'UYI', 'VND', 'VUV',
                     'XAF', 'XOF', 'XPF'), 0),
    **dict.fromkeys(('BHD', 'IQD', 'JOD', 'KWD', 'LYD', 'OMR', 'TND'), 3),
    **dict.fromkeys(('CLF', 'UYW'), 4),
}
DATA_URI = re.compile(r'data:(image/png|image/webp);base64,([A-Za-z0-9+/]+={0,2})')
# Letters of right-to-left scripts: Hebrew, Arabic, Syriac, Thaana, NKo and their presentation forms.
RTL = re.compile('[֐-ࣿיִ-﷿ﹰ-ﻼ]')


def default(code):
    """A code nobody changed: its ISO decimals, and the code itself shown before the amount, with a space."""
    return {'symbol': code, 'image': None, 'position': 'prefix', 'space': True, 'tint': False,
            'decimals': ISO_DECIMALS.get(code, DEFAULT_DECIMALS)}


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
            raise ValueError(f'{code}: send symbol, image, position, space, tint and decimals.')
        merged = {**default(code), **entry}
        symbol = merged['symbol']
        if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > MAX_SYMBOL or any(ord(char) < 32 or char in '<>' for char in symbol):
            raise ValueError(f'{code}: the symbol is 1 to {MAX_SYMBOL} characters, without < or >.')
        if merged['position'] not in POSITIONS:
            raise ValueError(f'{code}: the position is prefix or suffix.')
        if type(merged['space']) is not bool or type(merged['tint']) is not bool:
            raise ValueError(f'{code}: space and tint are true or false.')
        if type(merged['decimals']) is not int or not 0 <= merged['decimals'] <= MAX_DECIMALS:
            raise ValueError(f'{code}: decimals is a whole number from 0 to {MAX_DECIMALS}.')
        image = None if merged['image'] is None else check_image(merged['image'])
        normalized = {'symbol': symbol.strip(), 'image': image, 'position': merged['position'], 'space': merged['space'],
                      'tint': merged['tint'] and image is not None, 'decimals': merged['decimals']}
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


def decimals_table(displays):
    """{code: decimals} for every code whose decimals are not 2: the ISO list with the site's changes and additions.
    /api/schema sends it as `currency_decimals`; a code not in it has 2."""
    table = dict(ISO_DECIMALS)
    for code, entry in (displays or {}).items():
        if isinstance(entry, dict) and 'decimals' in entry:
            table[code] = entry['decimals']
    return {code: places for code, places in sorted(table.items()) if places != DEFAULT_DECIMALS}


def _site():
    """The display.currencies setting of the database in use (the request, job or transaction), or None."""
    from .fields import ACTIVE
    current = ACTIVE.get()
    business = getattr(current[0], 'business', None) if current is not None else None
    controls = getattr(business, 'controls', None)
    return getattr(controls, 'settings', {}).get(KEY) if controls is not None else None


def currency_decimals(code, displays=None):
    """How many decimal places amounts in `code` have on this site: the admin's setting in Controls, Currencies,
    else ISO 4217 (AED 2, KWD 3, JPY 0), else 2. Read inside a request, job, action or transaction it uses that
    site's setting; elsewhere, pass the setting as `displays` or get the ISO value. A missing or invalid code has 2."""
    if not isinstance(code, str) or not CODE.fullmatch(code):
        return DEFAULT_DECIMALS
    entry = ((_site() if displays is None else displays) or {}).get(code)
    if isinstance(entry, dict) and 'decimals' in entry:
        return entry['decimals']
    return ISO_DECIMALS.get(code, DEFAULT_DECIMALS)


def setting_code(name):
    """The currency code an app setting ("app.key") holds on the site in use, or None."""
    from .fields import ACTIVE
    current = ACTIVE.get()
    business = getattr(current[0], 'business', None) if current is not None else None
    settings = getattr(business, 'settings', None)
    if settings is None or not getattr(getattr(business, 'controls', None), 'ready', False):
        return None
    app, _, key = name.partition('.')
    try:
        code = settings.values.get(app, key)
    except (KeyError, LookupError):
        return None
    return code if isinstance(code, str) and CODE.fullmatch(code) else None


def places(value):
    """How many decimal places a Decimal amount carries, ignoring trailing zeros (12.50 has 1)."""
    exponent = value.normalize().as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def field_code(session, model, field, values):
    """The code a Currency field's amount is in, for one record's values (a dict of cleaned values): a fixed code,
    a field of the record, a field of the record a Link points at (read in this transaction) or an app setting.
    None when it has no currency source or the source is empty."""
    source = field.currency_source() if hasattr(field, 'currency_source') else None
    if source is None:
        return None
    if 'code' in source:
        return source['code']
    if 'setting' in source:
        return setting_code(source['setting']['app'] + '.' + source['setting']['key'])
    if 'link' in source:
        target = values.get(source['link'])
        link = model.__declared_fields__.get(source['link'])
        if target is None or link is None:
            return None
        code = session.connection.run(qb.select(session.registry.name_for(link.target), source['field']).where(id=int(target))).value()
    else:
        code = values.get(source['field'])
    return code if isinstance(code, str) and CODE.fullmatch(code) else None


def check_amounts(session, model, values):
    """Refuse a write whose Currency amounts carry more decimal places than their currency has on this site
    (a code-less amount has 2). Calculations round to these places first, so only typed values are refused."""
    from .models import ValidationError
    errors = {}
    for name, field in model.__declared_fields__.items():
        value = values.get(name)
        if getattr(field, 'kind', None) != 'currency' or not field.stored or not isinstance(value, Decimal):
            continue
        digits = places(value)
        if digits == 0:
            continue
        code = field_code(session, model, field, values)
        allowed = currency_decimals(code)
        if digits > allowed:
            errors[name] = f'Use at most {allowed} decimal places' + (f' for {code}' if code else '')
    if errors:
        raise ValidationError(errors)


def lowered(before, after):
    """[(code, places)] of the codes whose decimals the change from setting `before` to `after` lowers."""
    codes = set(before or {}) | set(after or {})
    return [(code, currency_decimals(code, after or {})) for code in sorted(codes)
            if currency_decimals(code, after or {}) < currency_decimals(code, before or {})]


def _scopes(registry, code, setting):
    """(model name, model, column, condition) of every stored Currency column whose amounts can be in `code`:
    a fixed code, a field of the record, a field behind a Link, or an app setting (`setting(app, key)` reads it).
    The condition picks the rows in `code`. Amounts without a currency source have 2 places whatever the code,
    so they are not in any."""
    for name, model in registry.models.items():
        for column, field in model.__declared_fields__.items():
            if getattr(field, 'kind', None) != 'currency' or not field.stored:
                continue
            source = field.currency_source()
            if source is None:
                continue
            if 'code' in source:
                if source['code'] == code:
                    yield name, model, column, qb.TRUE
            elif 'setting' in source:
                if setting is not None and setting(source['setting']['app'], source['setting']['key']) == code:
                    yield name, model, column, qb.TRUE
            elif 'link' in source:
                target = registry.name_for(model.__declared_fields__[source['link']].target)
                yield name, model, column, qb.in_(source['link'], qb.select(target, 'id').where(qb.eq(source['field'], code)))
            else:
                yield name, model, column, qb.eq(source['field'], code)


def _excess(column, factor):
    """The amounts in the column with a remainder when divided by factor."""
    return qb.ne(qb.col(column) % factor, 0)


def stored_excess(connection, registry, code, decimals, setting=None):
    """(count, first) of the stored amounts in `code` with more than `decimals` places: count over every column,
    first as 'Model #id field' or None. Exact integer arithmetic on the stored millionths."""
    from .identity import RecordId
    factor, count, first = 10 ** (MONEY_SCALE - decimals), 0, None
    for name, model, column, scope in _scopes(registry, code, setting):
        found = connection.run(qb.select(name, qb.count(), qb.min('id')).where(_excess(column, factor), scope)).one()
        if found[0]:
            count += found[0]
            first = first or f'{model.__name__} #{RecordId(found[1])} {column}'
    return count, first


def round_stored_money(application, code, decimals, *, actor='cli'):
    """Trusted code, an explicit step: round every stored amount in `code` to `decimals` places (half up, as Currency
    fields round) in one audited transaction, then set the currency's decimals as an admin does in Controls. Returns
    how many amounts changed. Safe to repeat: rounded amounts already fit, so a second run changes nothing. Rows of append-only ledgers are never changed, so one that would change refuses the step.

    Rounding changes stored totals: a document total no longer equals the sum of its rounded lines until it is
    recalculated. Revisions and the Journal keep the values they recorded."""
    from .auth import User
    if type(decimals) is not int or not 0 <= decimals <= MAX_DECIMALS:
        raise ValueError(f'decimals is a whole number from 0 to {MAX_DECIMALS}')
    if not isinstance(code, str) or not CODE.fullmatch(code):
        raise ValueError(f'{code} is not an ISO 4217 code such as AED')
    business = application.business
    controls, registry = business.controls, application.database.registry
    factor, half, changed = 10 ** (MONEY_SCALE - decimals), 10 ** (MONEY_SCALE - decimals) // 2, 0
    setting = business.settings.values.get if getattr(business, 'settings', None) is not None else None
    with application.database.transaction() as tx:
        for name, model, column, scope in _scopes(registry, code, setting):
            where = (_excess(column, factor), scope)
            if getattr(model, '__append_only__', False) and tx.connection.run(
                    qb.select(name, qb.value(1)).where(*where).limit(1)).one() is not None:
                raise ValueError(f'{model.__name__} is an append-only ledger with {code} amounts of more than {decimals} '
                                 'places; its rows are never changed')
            amount = qb.col(column)
            # Half up, away from zero: a negative amount rounds as its positive opposite does.
            rounded = qb.when((qb.ge(column, 0), ((amount + half) / factor) * factor),
                              otherwise=qb.value(0) - (((qb.value(0) - amount) + half) / factor) * factor)
            changed += tx.connection.run(qb.update(name, **{column: rounded}).where(*where)).rowcount
        controls.audit(tx.connection, actor, 'Display', KEY, f'Rounded {changed} stored {code} amounts to {decimals} places')
    current = dict(controls.settings.get(KEY) or {})
    current[code] = {**{key: value for key, value in display(current, code).items()}, 'decimals': decimals}
    controls.save(User(0, actor, 'admin'), {'changes': [{'kind': 'setting', 'key': KEY, 'value': current}]})
    return changed


def currency_unit(code, displays=None):
    """The smallest amount in `code` as a Decimal: Decimal("0.01") for AED, Decimal("0.001") for KWD, Decimal("1") for
    JPY. Use it to scale tolerances with the currency, for example `abs(difference) <= 5 * currency_unit(code)`."""
    return Decimal(1).scaleb(-currency_decimals(code, displays))


def set_currency_decimals(application, decimals, *, actor='cli'):
    """Trusted code: set the decimal places of the codes in `decimals` ({code: places}), keeping every other code and
    every symbol as it is, as an admin does in Controls, Currencies. This is how an app keeps its own currency list in
    the framework's table. The same checks apply: lowering a code is refused (409) while stored amounts in it have more
    places. Audited under `actor`; returns the saved setting. Call it outside a record transaction (an action, a job or
    a script), since it saves Controls in its own."""
    from .auth import User
    if not isinstance(decimals, dict) or not decimals:
        raise ValueError('Send decimals as {code: places}, for example {"KWD": 3}')
    controls = application.business.controls
    current = {code: dict(entry) for code, entry in (controls.settings.get(KEY) or {}).items()}
    for code, places in decimals.items():
        current[code] = {**(current.get(code) or {}), 'decimals': places}
    controls.save(User(0, actor, 'admin'), {'changes': [{'kind': 'setting', 'key': KEY, 'value': current}]})
    return controls.settings[KEY]


def round_money(value, code, rounding='half_up', displays=None):
    """`value` (a Decimal, int or decimal string) rounded to the decimal places of `code`, as a Decimal.
    Half up by default, as Currency fields round; pass rounding='half_even' to match a field declared so."""
    if rounding not in ('half_up', 'half_even'):
        raise ValueError("rounding must be 'half_up' or 'half_even'")
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise ValueError('Use a Decimal, an int or a decimal string, not a float')
    amount = Decimal(value)
    if not amount.is_finite():
        raise ValueError('Use a finite amount')
    places = Decimal(1).scaleb(-currency_decimals(code, displays))
    return amount.quantize(places, rounding=ROUND_HALF_UP if rounding == 'half_up' else ROUND_HALF_EVEN)


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
