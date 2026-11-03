"""Print designs: rendering through WeasyPrint on real SQLite records, the print permission and escaping."""

import pytest

from evlet import (Application, ChildTable, Column as ReportColumn, Currency, Database, Date, Decimal, Link, LocalAuth, Model,
                   Policy, PrintDesign, Registry, Report, Rule, Select, Text)
from evlet.printing import (Column, Fields, Footnote, Heading, Image, PageBreak, PageNumber, Prints, Table, Text as Words,
                            Totals, Watermark, available, document, document_context, fetcher)
from tests.test_business import PASSWORD, hashed, http, secured  # noqa: F401 (fixtures)

renders = pytest.mark.skipif(not available(), reason='needs the print extra: pip install evlet[print]')
PX_TO_MM = 25.4 / 96


class Customer(Model):
    name = Text(required=True)
    note = Text(default='')


class SalesInvoice(Model):
    class Meta:
        title_field = 'number'
    number = Text(required=True)
    customer = Link(Customer, required=True)
    posting_date = Date()
    status = Select(['draft', 'issued'], default='draft')
    grand_total = Currency(currency='AED', default='0')
    cost = Currency(currency='AED', default='0')


class InvoiceLine(Model):
    invoice = Link(SalesInvoice, required=True)
    item = Text(required=True, max_length=600)
    qty = Decimal(scale=2, default='1')
    amount = Currency(currency='AED', default='0')


class InvoiceTotals(Report):
    title = 'Invoices by customer'
    roles = ('admin', 'editor')
    status = Select(['draft', 'issued'], default='draft')
    columns = (ReportColumn('number'), ReportColumn('grand_total', type='currency', total=True, unit='AED'))

    def run(self):
        return self.context.list(SalesInvoice, filters={'status': self.status}, limit=self.context.limit, offset=self.context.offset)


class TaxInvoicePrint(PrintDesign):
    model = SalesInvoice
    title = 'Tax invoice'
    default = True
    header = (Words('Sample Trading LLC · {{ doc.name }}', weight='bold'),)
    main = (Heading('Tax Invoice'), Fields(('number', 'Invoice no.'), 'posting_date', 'customer', 'cost'),
            Table('lines', columns=(Column('item', width='60%'), Column('qty'), Column('amount'))),
            Totals(('grand_total', 'Total')))
    footnote = Footnote(Words('Received by ____________'), Words('Signature ____________'))
    footer = (PageNumber('Page {page} of {pages}', align='right'),)
    watermark = Watermark(text='DRAFT', size=50, show_if="doc.status == 'draft'")


class ShortPrint(PrintDesign):
    model = SalesInvoice
    title = 'Short'
    main = (Words('Customer: {{ doc.customer.title }} {{ doc.customer.note }}'),
            Words(html='<p class="x">{{ doc.customer.name }} {{ doc.cost }}</p>'),
            Words('Hidden', show_if='doc.status == "issued"'))
    footer = (PageNumber('{0}/{1}'),)


class ReportPrint(PrintDesign):
    report = InvoiceTotals
    roles = ('admin', 'editor')
    main = (Heading('{{ report.title }}'), Fields('status'), Table('rows'))
    footer = (PageNumber('Page {page}'),)


DESIGNS = [TaxInvoicePrint, ShortPrint, ReportPrint]


def make_app(tmp_path, designs=DESIGNS, rules=None):
    registry = Registry(Customer, SalesInvoice, InvoiceLine)
    database = Database(tmp_path / 'print.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, allow_setup=True)
    policy = Policy(registry, rules=rules if rules is not None else {
        ('editor', SalesInvoice): Rule(('read', 'create', 'update', 'print'), read_fields=('number', 'customer', 'posting_date', 'status', 'grand_total')),
        ('editor', InvoiceLine): Rule(('read', 'create', 'update')),
        ('viewer', SalesInvoice): Rule(('read',)),
    })
    app = Application(database, auth=auth, policy=policy, reports=(InvoiceTotals,), prints=designs, attachments=(SalesInvoice,),
                      children=(ChildTable(SalesInvoice, 'lines', InvoiceLine, 'invoice', 'Lines'),))
    app.initialize()
    http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})
    for role in ('editor', 'viewer'):
        app.business.auth.create_user(username=role, password=PASSWORD, role=role)
    return app


def signed_in(app, role='admin'):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': role, 'password': PASSWORD})
    assert status == 200, data
    cookie, csrf = headers[b'set-cookie'].decode().split(';')[0], data['csrf']
    return lambda method, path, body=None: http(app, method, path, body, cookie=cookie, csrf=csrf)


def invoice(call, lines=3, *, name='Gulf Horizon', status='draft', item='Item {i}'):
    status_code, customer, _ = call('POST', '/api/customer', {'name': name, 'note': 'private'})
    assert status_code == 201, customer
    status_code, record, _ = call('POST', '/api/salesinvoice', {
        'number': 'INV-2026-00042', 'customer': customer['id'], 'posting_date': '2026-11-03', 'status': status,
        'grand_total': '5616.35', 'cost': '1200.00',
        'children': {'lines': [{'item': item.format(i=i), 'qty': '2', 'amount': '10.00'} for i in range(lines)]}})
    assert status_code == 201, record
    return record


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


# ---- layout measurement ------------------------------------------------------------------------

def walk(box):
    yield box
    for child in getattr(box, 'children', ()) or ():
        yield from walk(child)


def classes(box):
    element = getattr(box, 'element', None)
    return (element.get('class') or '').split() if element is not None and hasattr(element, 'get') else []


def page_text(page):
    return ' '.join(box.text for box in walk(page._page_box) if hasattr(box, 'text') and isinstance(box.text, str)).split()


def joined(page):
    """The page's text with no spaces, so text split across boxes still compares."""
    return ''.join(page_text(page))


def outermost(page, name):
    """The outermost layout box of each element with a class, once per page."""
    found, seen = [], set()
    for box in walk(page._page_box):
        element = getattr(box, 'element', None)
        if getattr(box, 'element_tag', '') == 'div' and name in classes(box) and type(box).__name__ != 'LineBox' and id(element) not in seen:
            seen.add(id(element))
            found.append(box)
    return found


def user(app, role='admin'):
    from evlet import User
    with app.database.transaction() as tx:
        row = tx.connection.execute('SELECT id, username, role FROM evlet_users WHERE username=?', (role,)).fetchone()
    return User(row['id'], row['username'], row['role'])


def rendered(app, design, record_id, role='admin'):
    business, person = app.business, user(app, role)
    with business.database.transaction() as tx:
        record = business.policy.record(tx, person, SalesInvoice, record_id)
        return document(design, document_context(business, tx, person, SalesInvoice, record), business)


# ---- routes, permissions and escaping ----------------------------------------------------------

@renders
def test_invoice_prints_as_pdf_with_headers(app):
    call = signed_in(app)
    record = invoice(call)
    status, content, headers = call('GET', f"/api/salesinvoice/{record['id']}/prints/TaxInvoicePrint.pdf")
    assert status == 200 and content.startswith(b'%PDF')
    assert headers[b'content-type'] == b'application/pdf'
    assert headers[b'content-disposition'] == b"inline; filename*=UTF-8''INV-2026-00042.pdf"
    # Inline PDFs may show only in the application's own frame; downloads stay sandboxed.
    assert headers[b'content-security-policy'] == b"default-src 'none'; frame-ancestors 'self'"
    status, _, headers = call('GET', f"/api/salesinvoice/{record['id']}/prints/TaxInvoicePrint.pdf?download=1")
    assert status == 200 and headers[b'content-disposition'].startswith(b'attachment;')
    assert headers[b'content-security-policy'] == b"default-src 'none'; sandbox"


def test_catalog_and_schema_list_designs_by_permission(app):
    admin, editor, viewer = signed_in(app), signed_in(app, 'editor'), signed_in(app, 'viewer')
    names = [entry['name'] for entry in admin('GET', '/api/_prints')[1]['models']]
    assert names == ['TaxInvoicePrint', 'ShortPrint']  # the default first
    assert [entry['name'] for entry in editor('GET', '/api/_prints')[1]['models']] == ['TaxInvoicePrint', 'ShortPrint']
    assert viewer('GET', '/api/_prints')[1]['models'] == []
    entry = next(item for item in viewer('GET', '/api/schema')[1]['models'] if item['name'] == 'salesinvoice')
    assert 'prints' not in entry
    entry = next(item for item in admin('GET', '/api/schema')[1]['models'] if item['name'] == 'salesinvoice')
    assert entry['prints'][0] == {'name': 'TaxInvoicePrint', 'title': 'Tax invoice', 'default': True}
    assert 'print' not in entry['permissions']
    report = next(item for item in editor('GET', '/api/schema')[1]['reports'] if item['name'] == 'InvoiceTotals')
    assert report['prints'] == [{'name': 'ReportPrint', 'title': 'ReportPrint', 'default': False}]


def test_print_needs_its_own_permission_and_read_access(app):
    admin, viewer = signed_in(app), signed_in(app, 'viewer')
    record = invoice(admin)
    # The viewer reads the invoice but its Rule does not name 'print'.
    assert viewer('GET', f"/api/salesinvoice/{record['id']}")[0] == 200
    status, data, _ = viewer('GET', f"/api/salesinvoice/{record['id']}/prints/TaxInvoicePrint.pdf")
    assert status == 403 and data == {'error': 'You do not have permission for this operation'}
    # Unknown designs, designs of another model and missing records answer 404.
    assert admin('GET', f"/api/salesinvoice/{record['id']}/prints/Nope.pdf")[0] == 404
    assert admin('GET', f"/api/salesinvoice/{record['id']}/prints/ReportPrint.pdf")[0] == 404
    assert admin('GET', '/api/salesinvoice/999/prints/TaxInvoicePrint.pdf')[0] == 404
    assert admin('DELETE', f"/api/salesinvoice/{record['id']}/prints/TaxInvoicePrint.pdf")[0] == 405


def test_policy_print_rules():
    registry = Registry(Customer, SalesInvoice, InvoiceLine)
    policy = Policy(registry, rules={('viewer', SalesInvoice): Rule(('read', 'print')), ('editor', SalesInvoice): Rule(('read', 'update'))})
    from evlet import User
    assert policy.allows(User(1, 'a', 'admin'), SalesInvoice, 'print')
    assert policy.allows(User(2, 'v', 'viewer'), SalesInvoice, 'print')
    assert not policy.allows(User(3, 'e', 'editor'), SalesInvoice, 'print')
    with pytest.raises(ValueError, match='Write permissions require read access'):
        Policy(registry, rules={('viewer', SalesInvoice): Rule(('print',))})


@renders
def test_unreadable_fields_never_reach_the_template(app):
    admin, editor = signed_in(app), signed_in(app, 'editor')
    record = invoice(admin)
    page = rendered(app, ShortPrint, record['id'], 'editor').pages[0]
    words = page_text(page)
    # The editor cannot read cost; the admin can. Hidden through the Link too: note is readable for both here.
    assert '1,200' not in ' '.join(words) and 'Hidden' not in words
    admin_words = ' '.join(page_text(rendered(app, ShortPrint, record['id']).pages[0]))
    assert '1200.00' in admin_words or '1200' in admin_words
    status, content, _ = editor('GET', f"/api/salesinvoice/{record['id']}/prints/ShortPrint.pdf")
    assert status == 200 and content.startswith(b'%PDF')


@renders
def test_values_are_escaped_in_text_and_html_blocks(app):
    call = signed_in(app)
    record = invoice(call, name='<b>Acme</b> <img src="file:///etc/hosts">')
    from evlet.printing import Compiler
    business, person = app.business, user(app)
    with business.database.transaction() as tx:
        context = document_context(business, tx, person, SalesInvoice, business.policy.record(tx, person, SalesInvoice, record['id']))
    html = Compiler(ShortPrint, context, business).html()
    assert '<b>Acme</b>' not in html and '&lt;b&gt;Acme&lt;/b&gt;' in html
    assert '<img src="file' not in html
    assert '<p class="x">' in html  # the block's own markup stays
    words = ' '.join(page_text(rendered(app, ShortPrint, record['id']).pages[0]))
    assert '<b>Acme</b>' in words


def test_safe_filter_is_unavailable():
    from evlet.printing import environment
    from jinja2.exceptions import TemplateAssertionError
    with pytest.raises(TemplateAssertionError):
        environment().from_string('{{ value | safe }}')


def test_fetcher_refuses_files_and_network():
    if not available():
        pytest.skip('needs the print extra')
    fetch = fetcher(TaxInvoicePrint)
    for url in ('file:///etc/hosts', 'http://127.0.0.1:8000/api/schema', 'https://example.com/a.png', 'https://print.evlet.invalid/fonts/../x'):
        with pytest.raises(ValueError):
            fetch.fetch(url)
    assert fetch.fetch('https://print.evlet.invalid/fonts/NotoSans-Regular.woff2').read(4) == b'wOF2'


@renders
def test_file_a_copy_keeps_the_pdf_as_an_attachment(app):
    call = signed_in(app)
    record = invoice(call)
    status, data, _ = call('POST', f"/api/salesinvoice/{record['id']}/prints/TaxInvoicePrint/file", {})
    assert status == 201 and data['name'] == 'INV-2026-00042.pdf' and data['size'] > 1000
    items = call('GET', f"/api/salesinvoice/{record['id']}/attachments")[1]['items']
    assert [item['name'] for item in items] == ['INV-2026-00042.pdf']
    assert call('GET', f"/api/salesinvoice/{record['id']}/attachments/{items[0]['id']}")[1].startswith(b'%PDF')


def test_printing_not_installed_answers_503(app, monkeypatch):
    import evlet.printing
    monkeypatch.setattr(evlet.printing, 'available', lambda: False)
    call = signed_in(app)
    record = invoice(call)
    status, data, _ = call('GET', f"/api/salesinvoice/{record['id']}/prints/TaxInvoicePrint.pdf")
    assert status == 503 and data == {'error': 'Printing is not installed on this server (pip install evlet[print])'}
    assert call('GET', '/api/_prints')[1]['available'] is False
    assert call('GET', '/api/schema')[1]['printing'] == {'available': False}


# ---- reports -----------------------------------------------------------------------------------

@renders
def test_report_prints_filters_rows_and_totals(app):
    admin, editor, viewer = signed_in(app), signed_in(app, 'editor'), signed_in(app, 'viewer')
    invoice(admin, lines=1)
    status, content, headers = editor('POST', '/api/_reports/InvoiceTotals/prints/ReportPrint', {'filters': {'status': 'draft'}})
    assert status == 200 and content.startswith(b'%PDF'), content
    assert headers[b'content-disposition'] == b"inline; filename*=UTF-8''Invoices%20by%20customer.pdf"
    assert viewer('POST', '/api/_reports/InvoiceTotals/prints/ReportPrint', {'filters': {}})[0] == 403
    assert editor('POST', '/api/_reports/InvoiceTotals/prints/TaxInvoicePrint', {})[0] == 404
    assert editor('POST', '/api/_reports/InvoiceTotals/prints/ReportPrint', {'filters': {'status': 'lost'}})[0] == 422
    assert editor('GET', '/api/_reports/InvoiceTotals/prints/ReportPrint')[0] == 405


@renders
def test_report_context_reads_every_page(app):
    call = signed_in(app)
    status, customer, _ = call('POST', '/api/customer', {'name': 'Bulk'})
    with app.database.transaction() as tx:
        for index in range(230):
            tx.create(SalesInvoice, number=f'B-{index:04}', customer=customer['id'], status='issued', grand_total='1.50')
    from evlet.printing import report_context
    with app.business.context(user(app), readonly=True) as context:
        data = report_context(context, InvoiceTotals, {'status': 'issued'})
    assert len(data['report']['rows']) == 230 and data['report']['totals'] == {'grand_total': pytest.approx(345)}
    assert data['report']['filters'] == {'status': 'issued'}


# ---- layout ------------------------------------------------------------------------------------

def footnote_boxes(doc):
    found = []
    for number, page in enumerate(doc.pages, 1):
        page_box = page._page_box
        bottom = page_box.content_box_y() + page_box.height
        for box in outermost(page, 'evlet-footnote'):
            found.append((number, box.border_box_y(), box.border_box_y() + box.border_height(), bottom))
    return found


def content_bottom(page):
    """The lowest table row or totals block on a page."""
    lows = [box.border_box_y() + box.border_height() for box in walk(page._page_box)
            if getattr(box, 'element_tag', '') == 'tr' or 'evlet-totals' in classes(box)]
    return max(lows, default=0)


@renders
@pytest.mark.parametrize('lines', [1, 8, 16, 22, 25, 27, 28, 29, 30, 33, 45, 60, 61, 62, 63, 64, 65, 70, 95])
def test_footnote_sits_at_the_bottom_of_the_last_page(app, lines):
    call = signed_in(app)
    item = 'Supply and installation of cable tray, brackets and fixings {i} as per drawing rev. C' if lines % 2 else 'Item {i}'
    record = invoice(call, lines=lines, item=item)
    doc = rendered(app, TaxInvoicePrint, record['id'])
    boxes = footnote_boxes(doc)
    # One footnote, in one piece, on the last page, flush with the bottom of the content area.
    assert len(boxes) == 1, boxes
    page, top, low, bottom = boxes[0]
    assert page == len(doc.pages)
    assert abs(bottom - low) * PX_TO_MM < 0.5
    # Never over the rows or totals of its page, and the totals are on that page too.
    assert content_bottom(doc.pages[-1]) <= top + 0.5
    assert any('evlet-totals' in classes(box) for box in walk(doc.pages[-1]._page_box))
    # No space is reserved on earlier pages. Pages before the last are full; the one before it is free
    # only for what moved on with the footnote (the totals and at most one row), never a reserved band.
    def free(page):
        page_box = page._page_box
        return page_box.content_box_y() + page_box.height - content_bottom(page)
    rows = [box for box in walk(doc.pages[-1]._page_box) if getattr(box, 'element_tag', '') == 'tr']
    totals = next(box for box in walk(doc.pages[-1]._page_box) if 'evlet-totals' in classes(box))
    moved = (low - top) + totals.border_height() + max((row.border_height() for row in rows), default=0)
    for earlier in doc.pages[:-2]:
        assert free(earlier) * PX_TO_MM < 10
    if len(doc.pages) > 1:
        assert free(doc.pages[-2]) < moved + 8 / PX_TO_MM


@renders
def test_every_page_footnote_repeats_on_each_page(app):
    class EveryPage(TaxInvoicePrint):
        default = False
        footnote = Footnote(Words('Goods remain our property until paid'), place='every_page', height='10mm')
    app.business.prints = Prints(app.business, [EveryPage])
    call = signed_in(app)
    record = invoice(call, lines=70)
    doc = rendered(app, EveryPage, record['id'])
    assert len(doc.pages) > 1
    for page in doc.pages:
        assert 'property' in page_text(page)


@renders
@pytest.mark.parametrize('format, expected', [
    ('Page {page} of {pages}', ['Page 1 of 3', 'Page 3 of 3']),
    ('{page}/{pages}', ['1/3', '3/3']),
    ('{0} of {1}', ['1 of 3', '3 of 3']),
    ('Page {page}', ['Page 1', 'Page 3']),
    ('<b>{page}</b> {x}', ['<b>1</b> {x}', '<b>3</b> {x}']),
])
def test_page_number_formats(app, format, expected):
    class Numbered(TaxInvoicePrint):
        footer = (PageNumber(format),)
    call = signed_in(app)
    record = invoice(call, lines=70)
    doc = rendered(app, Numbered, record['id'])
    assert len(doc.pages) == 3
    first, last = joined(doc.pages[0]), joined(doc.pages[-1])
    assert expected[0].replace(' ', '') in first and expected[1].replace(' ', '') in last


def test_page_number_needs_a_page_placeholder():
    with pytest.raises(ValueError, match='PageNumber takes a format'):
        PageNumber('Page')


@renders
def test_watermark_repeats_with_its_size_and_condition(app):
    call = signed_in(app)
    record = invoice(call, lines=70)
    doc = rendered(app, TaxInvoicePrint, record['id'])
    for page in doc.pages:
        marks = outermost(page, 'evlet-watermark')
        assert len(marks) == 1 and 'DRAFT' in page_text(page)
        page_width = page._page_box.width
        assert marks[0].border_width() == pytest.approx(page_width * 0.5, rel=0.02)
    issued = invoice(call, lines=1, status='issued')
    doc = rendered(app, TaxInvoicePrint, issued['id'])
    assert 'DRAFT' not in page_text(doc.pages[0])


@renders
def test_watermark_image_and_html(app):
    pixel = ('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
    class Stamped(TaxInvoicePrint):
        watermark = Watermark(image=pixel, size=30, opacity=0.2)
    class Html(TaxInvoicePrint):
        watermark = Watermark(html='<b>{{ doc.name }}</b>', size=40)
    call = signed_in(app)
    record = invoice(call)
    page = rendered(app, Stamped, record['id']).pages[0]
    images = [box for box in walk(page._page_box) if type(box).__name__ == 'InlineReplacedBox']
    assert images
    assert 'INV-2026-00042' in page_text(rendered(app, Html, record['id']).pages[0])


@renders
def test_long_text_wraps_and_numbers_never_wrap(app):
    call = signed_in(app)
    record = invoice(call, lines=2, item='SN-' + 'A7F3' * 40)
    doc = rendered(app, TaxInvoicePrint, record['id'])
    page_box = doc.pages[0]._page_box
    right = page_box.content_box_x() + page_box.width
    for box in walk(page_box):
        if getattr(box, 'element_tag', '') == 'td':
            assert box.border_box_x() + box.border_width() <= right + 1
    amounts = [box for box in walk(page_box) if hasattr(box, 'text') and isinstance(box.text, str) and box.text.startswith('AED')]
    assert amounts and all('AED 10.00' == box.text or box.text.startswith('AED ') for box in amounts)


@renders
def test_header_first_and_page_breaks(app):
    class Letterhead(TaxInvoicePrint):
        header_first = (Words('LETTERHEAD'),)
        first_margin_top = '40mm'
        main = (*TaxInvoicePrint.main, PageBreak(), Words('Terms on their own page'))
    call = signed_in(app)
    record = invoice(call, lines=2)
    doc = rendered(app, Letterhead, record['id'])
    assert len(doc.pages) == 2
    assert 'LETTERHEAD' in page_text(doc.pages[0]) and 'LETTERHEAD' not in page_text(doc.pages[1])
    assert 'Sample' in page_text(doc.pages[1]) and 'Terms' in page_text(doc.pages[1])


@renders
def test_arabic_text_uses_the_bundled_arabic_font(app):
    call = signed_in(app)
    record = invoice(call, name='شركة أفق الخليج للمقاولات')
    content = rendered(app, TaxInvoicePrint, record['id']).write_pdf(uncompressed_pdf=True)
    # The bundled fonts are embedded, so the PDF looks the same on every server.
    assert b'+Noto-Sans-Arabic' in content and b'+Noto-Sans' in content


@renders
def test_context_print_for_trusted_code(app):
    from evlet.auth import AccessError
    call = signed_in(app)
    record = invoice(call)
    with app.business.context(user(app), readonly=True) as context:
        assert context.print(TaxInvoicePrint, record['id']).startswith(b'%PDF')
    with app.business.context(user(app, 'viewer'), readonly=True) as context:
        with pytest.raises(AccessError, match='permission'):
            context.print(TaxInvoicePrint, record['id'])


# ---- definitions ---------------------------------------------------------------------------------

@pytest.mark.parametrize('attributes, message', [
    ({}, 'set model or report'),
    ({'model': SalesInvoice, 'report': InvoiceTotals}, 'set model or report'),
    ({'model': SalesInvoice, 'main': (Table('nope'),)}, "Table names 'nope'"),
    ({'model': SalesInvoice, 'main': (Table('lines', columns=('price',)),)}, "Table column 'price'"),
    ({'model': SalesInvoice, 'main': (Fields('missing'),)}, "Fields names 'missing'"),
    ({'model': SalesInvoice, 'main': [Words('x')]}, 'main is a tuple of blocks'),
    ({'model': SalesInvoice, 'size': 'B9'}, 'size is'),
    ({'model': SalesInvoice, 'margins': ('1mm',)}, 'margins is'),
    ({'model': InvoiceLine}, 'a child table prints with its document'),
    ({'report': InvoiceTotals, 'roles': ('viewer',)}, "roles must be among the report's roles"),
    ({'report': InvoiceTotals, 'main': (Table('rows', columns=('cost',)),)}, 'a report Table'),
])
def test_invalid_designs_fail_at_start(tmp_path, attributes, message):
    design = type('BadPrint', (PrintDesign,), attributes)
    with pytest.raises(ValueError, match=message):
        make_app(tmp_path, designs=[design])


def test_block_options_are_checked():
    with pytest.raises(ValueError, match='align'):
        Words('x', align='middle')
    with pytest.raises(ValueError, match='Spacer height'):
        from evlet.printing import Spacer
        Spacer('tall')
    with pytest.raises(ValueError, match='one of text, image or html'):
        Watermark(text='A', html='B')
    with pytest.raises(ValueError, match='Watermark size'):
        Watermark(text='A', size=200)
    with pytest.raises(ValueError, match='every-page footnote height'):
        Footnote(Words('x'), place='every_page')
    with pytest.raises(ValueError, match='Image takes'):
        Image('')


def test_only_one_default_design_per_model(tmp_path):
    class Other(TaxInvoicePrint):
        pass
    with pytest.raises(ValueError, match='already the default'):
        make_app(tmp_path, designs=[TaxInvoicePrint, Other])


def test_print_designs_require_authentication(tmp_path):
    database = Database(tmp_path / 'plain.sqlite3', Registry(Customer, SalesInvoice, InvoiceLine))
    with pytest.raises(ValueError, match='require authentication'):
        Application(database, prints=[TaxInvoicePrint])


# ---- the Playground ------------------------------------------------------------------------------

@renders
def test_playground_prints_an_invoice_and_a_report(secured):
    from tests.test_business import client
    admin, editor, viewer = client(secured), client(secured, 'editor'), client(secured, 'viewer')
    status, customer, _ = admin('POST', '/api/customer', {'name': 'Gulf Horizon Contracting', 'currency': 'AED'})
    assert status == 201, customer
    status, record, _ = admin('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'PO-55120', 'total': '5616.35',
                                                       'posting_date': '2026-11-03', 'status': 'sent'})
    assert status == 201, record
    status, content, headers = editor('GET', f"/api/invoice/{record['id']}/prints/InvoicePrint.pdf")
    assert status == 200 and content.startswith(b'%PDF')
    assert headers[b'content-disposition'] == f"inline; filename*=UTF-8''{record['number']}.pdf".encode()
    assert viewer('GET', f"/api/invoice/{record['id']}/prints/InvoicePrint.pdf")[0] == 403
    status, content, _ = editor('POST', '/api/_reports/InvoicesByStatus/prints/InvoicesByStatusPrint', {'filters': {'status': 'sent'}})
    assert status == 200 and content.startswith(b'%PDF')
    from examples.playground.models import Invoice
    from examples.playground.prints import InvoicePrint
    business = secured.business
    person = user(secured)
    with business.database.transaction() as tx:
        found = business.policy.record(tx, person, Invoice, record['id'])
        doc = document(InvoicePrint, document_context(business, tx, person, Invoice, found), business)
    words = joined(doc.pages[0])
    assert 'AED5,616.35' in words and 'DRAFT' not in words and 'Page1of1' in words


def test_number_formatting():
    from markupsafe import Markup
    from evlet.printing import Formats
    formats = Formats(TaxInvoicePrint, Markup)
    shown = lambda value, *args: formats.number(value, *args).striptags()
    assert [shown('2036.5'), shown('5.0000'), shown('0.125'), shown('1250000'), shown('3.14159', 2)] == ['2,036.50', '5', '0.125', '1,250,000', '3.14']
    assert formats.money('5616.35', 'AED').striptags() == 'AED 5,616.35' and formats.money('5616.35').striptags() == '5,616.35'
    assert str(formats.date('2026-11-03')) == '<bdi dir="ltr">03-11-2026</bdi>'
    assert str(formats.ar('<x>')) == '<span lang="ar" dir="rtl">&lt;x&gt;</span>'


# ---- stored designs and the designer -------------------------------------------------------------

def test_designs_round_trip_through_json(tmp_path):
    from evlet.printing import design_from, design_to
    app = make_app(tmp_path)
    for design in (TaxInvoicePrint, ShortPrint, ReportPrint):
        data = design_to(design, app.business.registry)
        rebuilt = design_from(design.__name__ + 'Copy', {**data, 'default': False}, app.business)
        assert design_to(rebuilt, app.business.registry) == {**data, 'default': False}


@pytest.mark.parametrize('change, message', [
    ({'main': [{'type': 'chart'}]}, 'Each block has a type'),
    ({'main': [{'type': 'text', 'text': 'x', 'colour': 'red'}]}, 'does not take colour'),
    ({'main': [{'type': 'image', 'src': 'https://example.com/logo.png'}]}, 'data:image'),
    ({'watermark': {'image': 'file:///etc/hosts'}}, 'data:image'),
    ({'model': 'nothing'}, 'Choose a model'),
    ({'page': {'size': 'B9'}}, 'size is'),
    ({'footer': [{'type': 'pagenumber', 'format': 'Page'}]}, 'PageNumber takes a format'),
    ({'surprise': 1}, 'Unknown design option'),
])
def test_stored_definitions_are_checked(tmp_path, change, message):
    from evlet.printing import design_from, design_to
    app = make_app(tmp_path)
    data = {**design_to(ShortPrint, app.business.registry), **change}
    with pytest.raises(ValueError, match=message):
        design_from('StoredPrint', data, app.business)


@renders
def test_controls_saves_previews_and_removes_a_design(secured):
    from tests.test_business import client
    admin, editor = client(secured), client(secured, 'editor')
    status, customer, _ = admin('POST', '/api/customer', {'name': 'Gulf <b>Horizon</b>', 'currency': 'AED'})
    status, invoice_record, _ = admin('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'PO-1', 'total': '10.00', 'status': 'draft'})
    status, listing, _ = admin('GET', '/api/_controls/prints')
    assert status == 200 and listing['available'] is True
    app_design = next(item for item in listing['designs'] if item['name'] == 'InvoicePrint')
    assert app_design['source'] == 'app' and app_design['definition']['model'] == 'invoice'
    assert 'invoice' in [model['name'] for model in listing['models']] and 'table' in listing['blocks']
    definition = {**app_design['definition'], 'title': 'Statement', 'default': False,
                  'main': [{'type': 'heading', 'text': 'Statement'}, {'type': 'text', 'html': '<p class="who">{{ doc.customer.name }}</p>'}]}
    # Preview renders an unsaved definition against a record; the PDF waits under a token for this user only.
    status, preview, _ = admin('POST', '/api/_controls/prints/preview', {'definition': definition, 'record': invoice_record['id']})
    assert status == 200 and preview['url'].startswith('/api/_controls/prints/preview/')
    status, content, headers = admin('GET', preview['url'])
    assert status == 200 and content.startswith(b'%PDF') and headers[b'content-type'] == b'application/pdf'
    assert admin('GET', '/api/_controls/prints/preview/' + 'x' * 24 + '.pdf')[0] == 404
    assert admin('POST', '/api/_controls/prints/preview', {'definition': definition, 'record': 999})[0] == 404
    assert admin('POST', '/api/_controls/prints/preview', {'definition': {**definition, 'main': [{'type': 'nope'}]}, 'record': 1})[0] == 422
    # An app's design cannot be overwritten; a stored one under its own name can.
    assert admin('PUT', '/api/_controls/prints/InvoicePrint', {'definition': definition})[0] == 422
    status, saved, _ = admin('PUT', '/api/_controls/prints/StatementPrint', {'definition': definition})
    assert status == 200 and saved['source'] == 'stored'
    names = [entry['name'] for entry in editor('GET', '/api/_prints')[1]['models']]
    assert names == ['InvoicePrint', 'StatementPrint']
    status, content, _ = editor('GET', f"/api/invoice/{invoice_record['id']}/prints/StatementPrint.pdf")
    assert status == 200 and content.startswith(b'%PDF')
    with secured.database.transaction() as tx:
        rows = tx.connection.execute("SELECT detail FROM evlet_audit WHERE area='Print designs' AND target='StatementPrint'").fetchall()
    assert [row['detail'] for row in rows] == ['Added']
    # Only Controls opens the designer.
    assert editor('GET', '/api/_controls/prints')[0] == 404
    assert editor('PUT', '/api/_controls/prints/StatementPrint', {'definition': definition})[0] == 404
    assert admin('DELETE', '/api/_controls/prints/StatementPrint')[0] == 204
    assert [entry['name'] for entry in editor('GET', '/api/_prints')[1]['models']] == ['InvoicePrint']
    assert admin('DELETE', '/api/_controls/prints/StatementPrint')[0] == 404


@renders
def test_stored_design_values_are_escaped(secured):
    from tests.test_business import client
    from evlet.printing import Compiler, design_from
    admin = client(secured)
    status, customer, _ = admin('POST', '/api/customer', {'name': '<script>x</script>', 'currency': 'AED'})
    status, record, _ = admin('POST', '/api/invoice', {'customer': customer['id'], 'reference': 'PO-2', 'total': '10.00'})
    design = design_from('EscapePrint', {'model': 'invoice', 'main': [{'type': 'text', 'html': '<p>{{ doc.customer.name }}</p>'},
                                                                      {'type': 'text', 'text': '{{ doc.customer.name }}'}]}, secured.business)
    from examples.playground.models import Invoice
    business, person = secured.business, user(secured)
    with business.database.transaction() as tx:
        context = document_context(business, tx, person, Invoice, business.policy.record(tx, person, Invoice, record['id']))
    html = Compiler(design, context, business).html()
    assert '<script>' not in html and html.count('&lt;script&gt;x&lt;/script&gt;') == 2
