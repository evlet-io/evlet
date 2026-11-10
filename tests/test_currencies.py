"""Currency display per site (evlet.currencies): the setting, image checks, the schema, audit, permission and print."""

import base64
import json
import struct
import zlib

import pytest

from evlet.currencies import check, check_image, image_size, money_html
from tests.test_business import client, hashed, secured  # noqa: F401 (fixtures)


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def png(width=16, height=16, extra=b''):
    """A real PNG: one grey pixel row per line, filter byte 0."""
    header = struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)
    pixels = zlib.compress(b''.join(b'\x00' + b'\x80' * width for _ in range(height)))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + (chunk(b'tEXt', extra) if extra else b'') + chunk(b'IDAT', pixels) + chunk(b'IEND', b'')


def webp(width=20, height=12):
    """A lossless WebP header (VP8L) with the given size."""
    bits = (width - 1) | ((height - 1) << 14)
    data = b'\x2f' + bits.to_bytes(4, 'little') + b'\x00' * 8
    body = b'WEBP' + b'VP8L' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(body)) + body


def uri(content, kind='image/png'):
    return f'data:{kind};base64,' + base64.b64encode(content).decode()


def save(c, value):
    return c('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'display.currencies', 'value': value}]})


# ---- image checks ------------------------------------------------------------------------------

def test_png_and_webp_are_read_by_their_bytes():
    assert image_size(png(24, 18)) == ('image/png', 24, 18)
    assert image_size(webp(20, 12)) == ('image/webp', 20, 12)
    # The type comes from the bytes, not from what the data: URI claims.
    assert check_image(uri(webp(), 'image/png')).startswith('data:image/webp;base64,')


@pytest.mark.parametrize('content, kind, message', [
    (b'GIF89a' + b'\x00' * 40, 'image/png', 'Use a PNG or WebP image'),
    (b'\x89PNG\r\n\x1a\n' + b'not really a png at all', 'image/png', 'cut short or damaged'),
    (png()[:-4] + b'\x00\x00\x00\x00', 'image/png', 'checksum'),
    (png() + b'trailing', 'image/png', 'follows its end'),
    (png(129, 16), 'image/png', '129 x 16 pixels'),
    (png(16, 16, extra=b'k\x00' + b'x' * 33 * 1024), 'image/png', 'at most 32 KB'),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 'image/svg+xml', 'SVG is not accepted'),
])
def test_wrong_type_too_large_and_fake_images_are_refused(content, kind, message):
    with pytest.raises(ValueError, match=message):
        check_image(uri(content, kind))


def test_values_are_normalized_and_defaults_drop_out():
    assert check({'USD': {'symbol': '$'}, 'EUR': {'symbol': '€', 'position': 'suffix', 'space': False}, 'INR': {}}) == {
        'EUR': {'symbol': '€', 'image': None, 'position': 'suffix', 'space': False, 'tint': False, 'decimals': 2},
        'USD': {'symbol': '$', 'image': None, 'position': 'prefix', 'space': True, 'tint': False, 'decimals': 2}}
    for bad, message in (({'aed': {}}, 'ISO 4217'), ({'AED': {'symbol': '<b>'}}, 'without < or >'), ({'AED': {'position': 'left'}}, 'prefix or suffix'),
                         ({'AED': {'colour': 'red'}}, 'send symbol'), ({'AED': {'space': 'yes'}}, 'true or false'), ([], 'object keyed')):
        with pytest.raises(ValueError, match=message):
            check(bad)


def test_print_html_embeds_the_image_and_isolates_rtl_symbols():
    image = uri(png())
    html = money_html({'AED': {'symbol': 'AED', 'image': image, 'position': 'prefix', 'space': True, 'tint': True}}, 'AED', '1,234.50')
    assert html == f'<img class="evlet-currency" src="{image}" alt="AED"> 1,234.50'
    html = money_html({'AED': {'symbol': 'د.إ', 'image': None, 'position': 'suffix', 'space': True, 'tint': False}}, 'AED', '1,234.50')
    assert html == '1,234.50 <bdi>د.إ</bdi>'
    assert money_html({}, 'USD', '9.00') == 'USD 9.00'
    assert money_html({'USD': {'symbol': '<x>', 'image': None, 'position': 'prefix', 'space': False, 'tint': False}}, 'USD', '9') == '&lt;x&gt;9'


# ---- the setting over HTTP ---------------------------------------------------------------------

def test_settings_round_trip_reach_the_schema_and_the_audit_log(secured):
    admin = client(secured)
    image = uri(png(20, 20))
    status, payload, _ = save(admin, {'AED': {'image': image, 'tint': True}, 'USD': {'symbol': '$', 'position': 'prefix', 'space': False}})
    assert status == 200, payload
    schema = admin('GET', '/api/schema')[1]
    assert schema['currency_display'] == {
        'AED': {'symbol': 'AED', 'image': image, 'position': 'prefix', 'space': True, 'tint': True, 'decimals': 2},
        'USD': {'symbol': '$', 'image': None, 'position': 'prefix', 'space': False, 'tint': False, 'decimals': 2}}
    editor = client(secured, 'editor')
    assert editor('GET', '/api/schema')[1]['currency_display']['USD']['symbol'] == '$'
    state = admin('GET', '/api/_controls/state')[1]
    assert state['settings']['display.currencies']['value']['AED']['image'] == image
    with secured.database._connection() as connection:
        row = connection.execute("SELECT area, target, detail, after FROM evlet_audit WHERE area='Display' ORDER BY id DESC").fetchone()
    assert (row['area'], row['target'], row['detail']) == ('Display', 'display.currencies', 'Changed AED, USD')
    # The audit row names the image by type, size and hash; the bytes stay out of it.
    after = json.loads(row['after'])
    assert after['AED']['image'].startswith('image/png, ') and 'sha256' in after['AED']['image'] and 'base64' not in row['after']
    # Resetting removes the override; every code shows as before.
    assert save(admin, None)[0] == 200
    assert 'currency_display' not in admin('GET', '/api/schema')[1]
    with secured.database._connection() as connection:
        assert connection.execute("SELECT detail FROM evlet_audit WHERE area='Display' ORDER BY id DESC").fetchone()[0] == 'Reset AED, USD to the default'


def test_a_fake_png_is_refused_and_nothing_is_saved(secured):
    admin = client(secured)
    status, payload, _ = save(admin, {'AED': {'image': uri(b'\x89PNG\r\n\x1a\n<script>', 'image/png')}})
    assert status == 422 and 'damaged' in payload['fields']['changes.0']
    status, payload, _ = save(admin, {'AED': {'image': uri(png(200, 40))}})
    assert status == 422 and '200 x 40 pixels' in payload['fields']['changes.0']
    assert 'currency_display' not in admin('GET', '/api/schema')[1]


def test_only_admins_change_currency_display(secured):
    admin = client(secured)
    assert admin('POST', '/api/_controls/roles', {'id': 'ops', 'base': 'editor', 'description': 'Runs the instance'})[0] == 201
    assert admin('PATCH', '/api/_controls/roles/ops', {'opens_controls': True})[0] == 200
    from tests.test_controls import add_user
    add_user.hashed = hashed_password(secured)
    add_user(secured, 'opsuser', 'ops')
    ops = client(secured, 'opsuser')
    status, payload, _ = save(ops, {'USD': {'symbol': '$'}})
    assert status == 403 and payload['error'] == 'Only an admin can change how currencies are shown.'
    # Other settings stay open to roles that open Controls.
    status, _, _ = ops('POST', '/api/_controls/overrides', {'changes': [{'kind': 'setting', 'key': 'unsaved.server_days', 'value': 20}]})
    assert status == 200
    assert client(secured, 'editor')('POST', '/api/_controls/overrides', {'changes': []})[0] == 404
    assert 'currency_display' not in admin('GET', '/api/schema')[1]


def hashed_password(app):
    with app.database._connection() as connection:
        return connection.execute("SELECT password FROM evlet_users WHERE username='editor'").fetchone()[0]


def test_report_csv_keeps_plain_numbers_and_names_the_code(secured):
    admin = client(secured)
    image = uri(png())
    assert save(admin, {'AED': {'image': image}})[0] == 200
    status, customer, _ = admin('POST', '/api/customer', {'customer_name': 'Gulf Horizon Contracting', 'currency': 'AED'})
    assert status == 201, customer
    status, record, _ = admin('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'PO-1', 'total': '5616.35',
                                                       'posting_date': '2026-11-03', 'status': 'sent'})
    assert status == 201, record
    status, content, _ = admin('POST', '/api/_reports/SalesByCustomer/export.csv', {'filters': {'status': 'sent'}})
    assert status == 200, content
    text = content.decode('utf-8-sig')
    assert text.splitlines()[0] == 'Customer,Invoices,Total (AED)'
    assert text.splitlines()[1].endswith(',1,5616.35')
    assert 'base64' not in text and '<img' not in text


def test_print_money_uses_the_site_display():
    Markup = pytest.importorskip('markupsafe').Markup
    from evlet.printing import Formats
    from tests.test_printing import TaxInvoicePrint
    image = uri(png())
    formats = Formats(TaxInvoicePrint, Markup, {'AED': {'symbol': 'AED', 'image': image, 'position': 'suffix', 'space': True, 'tint': False}})
    assert str(formats.money('5616.35', 'AED')) == f'<bdi dir="ltr">5,616.35 <img class="evlet-currency" src="{image}" alt="AED"></bdi>'
    # Other codes and number formatting stay as they were.
    assert formats.money('5616.35', 'USD').striptags() == 'USD 5,616.35'


def test_pdf_renders_the_image_symbol(secured):
    """The Playground invoice as a PDF, with AED set to an image: the image is a replaced box in the layout and an image in the file."""
    from tests.test_printing import renders, user, walk
    if renders.args[0]:
        pytest.skip(renders.kwargs['reason'])
    from evlet.printing import document, document_context
    from examples.playground.models import Invoice
    from examples.playground.prints import InvoicePrint
    admin = client(secured)
    assert save(admin, {'AED': {'image': uri(png(24, 24))}})[0] == 200
    status, customer, _ = admin('POST', '/api/customer', {'customer_name': 'Gulf Horizon Contracting', 'currency': 'AED'})
    status, record, _ = admin('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'PO-55120', 'total': '5616.35',
                                                       'posting_date': '2026-11-03', 'status': 'sent'})
    assert status == 201, record
    business, person = secured.business, user(secured)
    with business.database.transaction() as tx:
        found = business.policy.record(tx, person, Invoice, record['id'])
        doc = document(InvoicePrint, document_context(business, tx, person, Invoice, found), business)
    boxes = [box for box in walk(doc.pages[0]._page_box) if type(box).__name__ == 'InlineReplacedBox'
             and 'evlet-currency' in (box.element.get('class') or '')]
    assert boxes and all(box.element.get('alt') == 'AED' for box in boxes)
    # Text height: 0.8em of the surrounding text, not the image's 24 pixels.
    assert all(box.height < 20 for box in boxes)
    pdf = doc.write_pdf()
    assert pdf.startswith(b'%PDF') and b'/Subtype /Image' in pdf
    status, content, _ = admin('GET', f"/api/invoice/{record['id']}/prints/InvoicePrint.pdf")
    assert status == 200 and b'/Subtype /Image' in content


def test_trusted_code_sets_the_display_and_the_playground_shows_aed_as_an_image(secured, tmp_path):
    from evlet import set_currency_display
    path = tmp_path / 'symbol.png'
    path.write_bytes(png(18, 18))
    saved = set_currency_display(secured, {'AED': {'image': path}, 'USD': {'symbol': '$', 'image': png(), 'position': 'suffix'}}, actor='setup')
    assert saved['AED']['image'].startswith('data:image/png;base64,') and saved['USD']['position'] == 'suffix'
    with secured.database._connection() as connection:
        assert connection.execute("SELECT actor FROM evlet_audit WHERE area='Display'").fetchone()[0] == 'setup'
    with pytest.raises(Exception, match='Use a PNG or WebP image'):
        set_currency_display(secured, {'AED': {'image': b'GIF89a' + b'\x00' * 20}})
    assert set_currency_display(secured, None) == {}
    from evlet.cli.playground import playground_app
    app = playground_app(tmp_path)
    shown = app.business.controls.settings['display.currencies']['AED']
    assert shown['image'].startswith('data:image/png;base64,') and shown['tint'] and shown['position'] == 'prefix'
    # The demo's client credit unit has 5 places; its KWD customers keep ISO's 3.
    from evlet import currency_decimals
    displays = app.business.controls.settings['display.currencies']
    assert displays['XCR']['decimals'] == 5 and currency_decimals('KWD', displays) == 3
