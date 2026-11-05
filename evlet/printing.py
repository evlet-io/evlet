"""Print designs: records and reports rendered to PDF through WeasyPrint.

A design is ordered blocks in five regions (header, main, footnote, footer, watermark). Text in
any block is a Jinja template filled in a sandbox with autoescape on, and any block can be
switched to HTML. WeasyPrint and Jinja2 come with the optional extra: pip install evlet[print].
"""

from dataclasses import dataclass, field as dataclass_field, fields as dataclass_fields
from datetime import date as Date, datetime as DateTime
from decimal import Decimal
from html import escape
from importlib import resources
import re

from .auth import AccessError
from .fields import RichText
from .models import Model, ValidationError
from .richtext import sanitise

INSTALL = 'Printing is not installed on this server (pip install evlet[print])'
# Report designs read the report page by page, up to this many rows.
MAX_REPORT_ROWS = 5000
SIZES = {'A3': ('297mm', '420mm'), 'A4': ('210mm', '297mm'), 'A5': ('148mm', '210mm'),
         'Letter': ('8.5in', '11in'), 'Legal': ('8.5in', '14in')}
LENGTH = re.compile(r'(0|[0-9]{1,4}(\.[0-9]{1,3})?)(mm|cm|in|pt|px)')
FONT_SIZE = re.compile(r'[0-9]{1,2}(\.[0-9])?pt')
PLACEHOLDER = re.compile(r'\{(page|pages|0|1)\}')
# The only base URL WeasyPrint sees; the fetcher answers it from the bundled fonts and the design's assets.
BASE = 'https://print.evlet.invalid/'
FONTS = ('NotoSans-Regular', 'NotoSans-Bold', 'NotoSansArabic-Regular', 'NotoSansArabic-Bold')


def available():
    """Whether WeasyPrint and Jinja2 can be imported (the print extra and pango are installed)."""
    try:
        import jinja2  # noqa: F401
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def length(value, name):
    if not isinstance(value, str) or not LENGTH.fullmatch(value):
        raise ValueError(f'{name} takes a length such as "12mm", "0.5in" or "10pt"')
    return value


# ---- blocks ----------------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class Block:
    """Options every block shares. html= replaces the block's markup with a Jinja template."""
    html: str | None = None
    show_if: str | None = None
    align: str | None = None
    size: str | None = None
    weight: str | None = None
    space_before: str | None = None
    space_after: str | None = None
    break_before: str | None = None

    def __post_init__(self):
        if self.html is not None and (not isinstance(self.html, str) or len(self.html) > 20000):
            raise ValueError('html takes a template of at most 20000 characters')
        if self.show_if is not None and (not isinstance(self.show_if, str) or not self.show_if.strip() or len(self.show_if) > 500):
            raise ValueError('show_if takes an expression such as "doc.status == \'draft\'"')
        if self.align not in (None, 'left', 'center', 'right', 'justify'):
            raise ValueError('align is left, center, right or justify')
        if self.size is not None and not FONT_SIZE.fullmatch(str(self.size)):
            raise ValueError('size takes points such as "9pt"')
        if self.weight not in (None, 'normal', 'bold'):
            raise ValueError('weight is normal or bold')
        for name in ('space_before', 'space_after'):
            if getattr(self, name) is not None:
                length(getattr(self, name), name)
        if self.break_before not in (None, 'page'):
            raise ValueError('break_before is "page" or None')

    def style(self):
        rules = [('text-align', self.align), ('font-size', self.size), ('font-weight', self.weight),
                 ('margin-top', self.space_before), ('margin-bottom', self.space_after),
                 ('break-before', self.break_before)]
        return '; '.join(f'{key}: {value}' for key, value in rules if value)


@dataclass(frozen=True, kw_only=True)
class Heading(Block):
    text: str = ''

    def __init__(self, text='', **options):
        object.__setattr__(self, 'text', text)
        Block.__init__(self, **options)


@dataclass(frozen=True, kw_only=True)
class Text(Block):
    text: str = ''

    def __init__(self, text='', **options):
        object.__setattr__(self, 'text', text)
        Block.__init__(self, **options)


def items(values, name):
    """Field names, or (name, label) pairs, as a tuple of pairs."""
    result = []
    for value in values:
        if isinstance(value, str):
            value = (value, None)
        if not (isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) and value[0]
                and (value[1] is None or isinstance(value[1], str))):
            raise ValueError(f'{name} takes field names or (name, label) pairs')
        result.append(value)
    return tuple(result)


@dataclass(frozen=True, kw_only=True)
class Fields(Block):
    """Label and value pairs. On a report design they show the applied filters."""
    names: tuple = ()
    columns: int = 2

    def __init__(self, *names, columns=2, **options):
        object.__setattr__(self, 'names', items(names, 'Fields'))
        object.__setattr__(self, 'columns', columns)
        Block.__init__(self, **options)
        if type(columns) is not int or not 1 <= columns <= 4:
            raise ValueError('Fields columns is 1 to 4')


@dataclass(frozen=True)
class Column:
    name: str
    label: str | None = None
    width: str | None = None
    align: str | None = None
    wrap: bool | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError('Column takes a field name')
        if self.width is not None and not re.fullmatch(r'[0-9]{1,3}%', str(self.width)):
            length(self.width, 'width')
        if self.align not in (None, 'left', 'center', 'right'):
            raise ValueError('Column align is left, center or right')


@dataclass(frozen=True, kw_only=True)
class Table(Block):
    """Rows of a child table (documents) or "rows" (reports). The header repeats on every page."""
    source: str = ''
    columns: tuple = ()

    def __init__(self, source, columns=(), **options):
        object.__setattr__(self, 'source', source)
        object.__setattr__(self, 'columns', tuple(Column(item) if isinstance(item, str) else item for item in columns))
        Block.__init__(self, **options)
        if not isinstance(source, str) or not source:
            raise ValueError('Table takes a child table name, or "rows" on a report design')
        if any(not isinstance(item, Column) for item in self.columns):
            raise ValueError('Table columns are Column(...) or field names')


@dataclass(frozen=True, kw_only=True)
class Totals(Block):
    """Closing figures, right aligned; the last one is emphasized. It never starts a page alone."""
    names: tuple = ()

    def __init__(self, *names, **options):
        object.__setattr__(self, 'names', items(names, 'Totals'))
        Block.__init__(self, **options)
        if not self.names:
            raise ValueError('Totals takes at least one field')


@dataclass(frozen=True, kw_only=True)
class Image(Block):
    src: str = ''
    width: str | None = None

    def __init__(self, src, width=None, **options):
        object.__setattr__(self, 'src', src)
        object.__setattr__(self, 'width', width)
        Block.__init__(self, **options)
        if not isinstance(src, str) or not src:
            raise ValueError('Image takes the name of a design asset or a data: URI')
        if width is not None and not re.fullmatch(r'[0-9]{1,3}%', str(width)):
            length(width, 'width')


@dataclass(frozen=True, kw_only=True)
class Spacer(Block):
    height: str = '6mm'

    def __init__(self, height='6mm', **options):
        object.__setattr__(self, 'height', length(height, 'Spacer height'))
        Block.__init__(self, **options)


@dataclass(frozen=True, kw_only=True)
class PageBreak(Block):
    def __init__(self, **options):
        Block.__init__(self, **options)


@dataclass(frozen=True, kw_only=True)
class PageNumber(Block):
    """A page number as a format: "Page {page} of {pages}", "{page}/{pages}"; {0} and {1} also work."""
    format: str = 'Page {page} of {pages}'

    def __init__(self, format='Page {page} of {pages}', **options):
        object.__setattr__(self, 'format', format)
        Block.__init__(self, **options)
        if not isinstance(format, str) or len(format) > 120 or not re.search(r'\{(page|0)\}', format):
            raise ValueError('PageNumber takes a format with {page}, such as "Page {page} of {pages}"')


@dataclass(frozen=True)
class Footnote:
    """Blocks snapped to the bottom of the last page, or of every page (height reserves their space)."""
    blocks: tuple = ()
    place: str = 'last_page'
    height: str | None = None

    def __init__(self, *blocks, place='last_page', height=None):
        object.__setattr__(self, 'blocks', blocks)
        object.__setattr__(self, 'place', place)
        object.__setattr__(self, 'height', height)
        if not blocks or any(not isinstance(block, Block) for block in blocks):
            raise ValueError('Footnote takes one or more blocks')
        if place not in ('last_page', 'every_page'):
            raise ValueError('Footnote place is "last_page" or "every_page"')
        if place == 'every_page':
            length(height or '', 'An every-page footnote height')
        elif height is not None:
            raise ValueError('height reserves an every-page footnote; a last-page footnote takes its own height')


@dataclass(frozen=True)
class Watermark:
    """Text, an image (asset name or data: URI) or HTML behind the content of every page.

    size is the width in percent of the page; opacity is 0.01 to 1; rotate is in degrees.
    """
    text: str | None = None
    image: str | None = None
    html: str | None = None
    size: int = 60
    opacity: float = 0.1
    rotate: int = -30
    show_if: str | None = None

    def __post_init__(self):
        if sum(value is not None for value in (self.text, self.image, self.html)) != 1:
            raise ValueError('Watermark takes one of text, image or html')
        if any(value is not None and (not isinstance(value, str) or not value or len(value) > 20000) for value in (self.text, self.image, self.html)):
            raise ValueError('Watermark text, image and html are nonempty text')
        if type(self.size) is not int or not 5 <= self.size <= 100:
            raise ValueError('Watermark size is 5 to 100 (percent of the page width)')
        if not isinstance(self.opacity, (int, float)) or isinstance(self.opacity, bool) or not 0.01 <= self.opacity <= 1:
            raise ValueError('Watermark opacity is 0.01 to 1')
        if type(self.rotate) is not int or not -90 <= self.rotate <= 90:
            raise ValueError('Watermark rotate is -90 to 90 degrees')
        if self.show_if is not None and (not isinstance(self.show_if, str) or not self.show_if.strip()):
            raise ValueError('show_if takes an expression')


# ---- designs ---------------------------------------------------------------------------------

class PrintDesign:
    """How one model or report prints. Subclass and set model or report, then the regions."""
    model = None
    report = None
    title = ''
    default = False
    roles = ('admin',)
    size = 'A4'
    orientation = 'portrait'
    margins = ('30mm', '14mm', '20mm', '14mm')
    first_margin_top = None
    font_size = '9pt'
    currency = None
    date_format = '%d-%m-%Y'
    header = ()
    header_first = None
    main = ()
    footnote = None
    footer = ()
    watermark = None
    assets = {}

    @classmethod
    def check(cls, registry, business):
        """Validate the design against the application; raises ValueError naming the design."""
        name = cls.__name__
        def fail(message):
            raise ValueError(f'{name}: {message}')
        if (cls.model is None) == (cls.report is None):
            fail('set model or report, not both')
        if not isinstance(cls.title, str) or len(cls.title) > 80:
            fail('title is text of at most 80 characters')
        if type(cls.default) is not bool:
            fail('default is True or False')
        if cls.size not in SIZES and not (isinstance(cls.size, tuple) and len(cls.size) == 2):
            fail('size is ' + ', '.join(SIZES) + ' or a (width, height) pair')
        if isinstance(cls.size, tuple):
            for value in cls.size:
                length(value, 'size')
        if cls.orientation not in ('portrait', 'landscape'):
            fail('orientation is portrait or landscape')
        if not isinstance(cls.margins, tuple) or len(cls.margins) != 4:
            fail('margins is (top, right, bottom, left)')
        for value in (*cls.margins, *((cls.first_margin_top,) if cls.first_margin_top else ())):
            length(value, 'margin')
        if not FONT_SIZE.fullmatch(cls.font_size):
            fail('font_size takes points such as "9pt"')
        if cls.currency is not None and not re.fullmatch('[A-Z]{3}', cls.currency):
            fail('currency is an ISO 4217 code such as "AED"')
        if not isinstance(cls.date_format, str) or not re.fullmatch(r'[%dmyYbBHMS/ .:,-]{2,24}', cls.date_format):
            fail('date_format uses strftime codes such as "%d-%m-%Y"')
        for region in ('header', 'main', 'footer'):
            blocks = getattr(cls, region)
            if not isinstance(blocks, tuple) or any(not isinstance(block, Block) for block in blocks):
                fail(f'{region} is a tuple of blocks')
        if cls.header_first is not None and (not isinstance(cls.header_first, tuple) or any(not isinstance(b, Block) for b in cls.header_first)):
            fail('header_first is a tuple of blocks')
        if cls.footnote is not None and not isinstance(cls.footnote, Footnote):
            fail('footnote is a Footnote(...)')
        if cls.watermark is not None and not isinstance(cls.watermark, Watermark):
            fail('watermark is a Watermark(...)')
        if not isinstance(cls.assets, dict) or any(not isinstance(key, str) or not isinstance(value, bytes) for key, value in cls.assets.items()):
            fail('assets maps names to file content (bytes)')
        if cls.model is not None:
            model = registry.resolve(cls.model)
            if model in business.child_models:
                fail('a child table prints with its document')
            tables = {child.name: child.model for child in business.children.values() if child.parent is model}
            fields = set(model.__declared_fields__)
            for block in cls.blocks():
                if isinstance(block, Table):
                    if block.source not in tables:
                        fail(f'Table names {block.source!r}, which is not a child table of {model.__name__}')
                    columns = set(tables[block.source].__declared_fields__)
                    for column in block.columns:
                        if column.name not in columns:
                            fail(f'Table column {column.name!r} is not a field of {block.source}')
                elif isinstance(block, (Fields, Totals)):
                    for path, _ in block.names:
                        if path.split('.')[0] not in fields | {'id'}:
                            fail(f'{type(block).__name__} names {path!r}, which is not a field of {model.__name__}')
        else:
            reports = getattr(getattr(business, 'forms', None), 'reports', {})
            if cls.report not in reports.values():
                fail('report is not registered with the application (reports=...)')
            if not isinstance(cls.roles, tuple) or not cls.roles or set(cls.roles) - set(cls.report.roles):
                fail("roles must be among the report's roles")
            columns = {column.name for column in cls.report.columns}
            for block in cls.blocks():
                if isinstance(block, Table) and (block.source != 'rows' or any(column.name not in columns for column in block.columns)):
                    fail('a report Table takes "rows" and the report\'s columns')
                elif isinstance(block, Fields) and any(path not in cls.report.__declared_fields__ for path, _ in block.names):
                    fail('report Fields name the report\'s filter fields')
                elif isinstance(block, Totals) and any(path not in columns for path, _ in block.names):
                    fail('report Totals name report columns')

    @classmethod
    def blocks(cls):
        return (*cls.header, *(cls.header_first or ()), *cls.main, *cls.footer, *(cls.footnote.blocks if cls.footnote else ()))


class Prints:
    """The application's registered designs, by name (the class name)."""

    def __init__(self, business, designs=()):
        self.business = business
        # The app's designs; Controls adds stored designs on top (current()).
        self.designs = {}
        defaults = set()
        for design in designs:
            if not isinstance(design, type) or not issubclass(design, PrintDesign) or design is PrintDesign:
                raise ValueError('Register PrintDesign subclasses')
            if design.__name__ in self.designs:
                raise ValueError('Duplicate print design: ' + design.__name__)
            design.check(business.registry, business)
            owner = design.model or design.report
            if design.default:
                if owner in defaults:
                    raise ValueError(f'{design.__name__}: another design is already the default for {owner.__name__}')
                defaults.add(owner)
            self.designs[design.__name__] = design
        self.stored, self.version = {}, None
        self.designer = Designer(self)

    def initialize(self):
        with self.business.database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_print_designs (name TEXT PRIMARY KEY, definition TEXT NOT NULL, '
                                  'changed_by TEXT, changed_at INTEGER)')
        self.reload()

    def stored_rows(self):
        try:
            with self.business.database._connection() as connection:
                rows = connection.execute('SELECT name, definition, changed_by, changed_at FROM evlet_print_designs ORDER BY name').fetchall()
        except Exception:
            return {}
        return {row['name']: {'definition': row['definition'], 'changed_by': row['changed_by'], 'changed_at': row['changed_at']} for row in rows}

    def reload(self):
        """Read the stored designs again. A stored design that no longer fits the app is left out and logged."""
        import json, logging
        stored = {}
        for name, row in self.stored_rows().items():
            if name in self.designs:
                continue
            try:
                stored[name] = design_from(name, json.loads(row['definition']), self.business)
            except (ValueError, TypeError):
                logging.getLogger('evlet.printing').warning('Stored print design %s no longer fits the app and is not offered', name)
        self.stored = stored
        self.version = getattr(getattr(self.business, 'controls', None), 'version', None)

    def current(self):
        """The app's designs and the stored ones, read again when Controls saved a change in any process."""
        version = getattr(getattr(self.business, 'controls', None), 'version', None)
        if version != self.version:
            self.reload()
        stored = {name: design for name, design in self.stored.items()
                  if not (design.default and any(other.default and (other.model or other.report) is (design.model or design.report)
                                                  for other in self.designs.values()))}
        return {**self.designs, **stored}

    def for_model(self, user, model):
        policy = self.business.policy
        if not policy.allows(user, model, 'print'):
            return []
        return self._listed(design for design in self.current().values() if design.model is not None
                            and self.business.registry.resolve(design.model) is model)

    def for_report(self, user, report):
        return self._listed(design for design in self.current().values()
                            if design.report is report and user.role in design.roles and user.role in report.roles)

    @staticmethod
    def _listed(designs):
        ordered = sorted(designs, key=lambda design: (not design.default, design.title or design.__name__))
        return [{'name': design.__name__, 'title': design.title or design.__name__, 'default': design.default} for design in ordered]

    def catalog(self, user):
        models = [{'model': self.business.registry.name_for(model), **entry}
                  for model in self.business.registry.models.values() for entry in self.for_model(user, model)]
        reports = getattr(getattr(self.business, 'forms', None), 'reports', {})
        return {'available': available(), 'models': models,
                'reports': [{'report': name, **entry} for name, report in reports.items() for entry in self.for_report(user, report)]}

    # ---- routes ----------------------------------------------------------------------------

    def design(self, name, *, model=None, report=None):
        design = self.current().get(name)
        if design is None or (model is not None and (design.model is None or self.business.registry.resolve(design.model) is not model)) \
                or (report is not None and design.report is not report):
            raise AccessError(404, 'Print design not found')
        return design

    def record_pdf(self, scope, user, model, record_id, tail, method):
        """GET {design}.pdf renders a record; POST {design}/file keeps a copy as an attachment."""
        if len(tail) == 1 and tail[0].endswith('.pdf') and method == 'GET':
            name, filing = tail[0].removesuffix('.pdf'), False
        elif len(tail) == 2 and tail[1] == 'file' and method == 'POST':
            name, filing = tail[0], True
        else:
            raise AccessError(404 if method in ('GET', 'POST') else 405, 'Print route not found')
        design = self.design(name, model=model)
        if not available():
            raise AccessError(503, INSTALL)
        from urllib.parse import parse_qs
        query = parse_qs(scope.get('query_string', b'').decode('ascii', 'replace'), max_num_fields=4)
        if set(query) - {'download'}:
            raise ValidationError({'query': 'Use download=1 or nothing'})
        business = self.business
        with business.database.transaction() as tx:
            record = business.policy.record(tx, user, model, record_id, 'update' if filing else 'read')
            business.policy.require(user, model, 'print')
            context = document_context(business, tx, user, model, record)
            content = render(design, context, business)
            filename = safe_filename(context['doc'].get('name') or f'{model.__name__}-{record_id}') + '.pdf'
            if filing:
                return self.file_copy(tx, user, model, record_id, filename, content)
        disposition = 'attachment' if query.get('download') == ['1'] else 'inline'
        return pdf_response(content, filename, disposition)

    def file_copy(self, tx, user, model, record_id, filename, content):
        business = self.business
        if model not in business.attachments:
            raise AccessError(409, 'This record does not keep attachments')
        from .business import size_text
        from .documents import Context
        from .states import check_attachments
        check_attachments(Context(business, tx, user, readonly=True), model, tx.get(model, record_id))
        limits = business.attachment_limits[model]
        if len(content) > limits.max_size:
            raise ValidationError({'attachment': f'The PDF is larger than {size_text(limits.max_size)}'})
        name = business.registry.name_for(model)
        if tx.connection.execute("SELECT count(*) FROM evlet_attachments WHERE model=? AND record_id=? AND (purpose IS NULL OR purpose <> 'image')", (name, record_id)).fetchone()[0] >= limits.max_files:
            raise ValidationError({'attachment': f'Use at most {limits.max_files} attachment{"s" if limits.max_files > 1 else ""} per record'})
        file_id = business.storage.add(tx, name, record_id, filename, content)
        from .journal import file
        file(business, tx, user, model, record_id, 'added', {'id': file_id, 'name': filename, 'size': len(content)})
        business.changed(tx, model, record_id)
        return 201, {'id': file_id, 'name': filename, 'size': len(content)}, []

    def report_pdf(self, report_name, design_name, body, user):
        reports = getattr(getattr(self.business, 'forms', None), 'reports', {})
        report = reports.get(report_name)
        if report is None or user.role not in report.roles:
            raise AccessError(403, 'Report is unavailable')
        design = self.design(design_name, report=report)
        if user.role not in design.roles:
            raise AccessError(403, 'You do not have permission for this operation')
        if not available():
            raise AccessError(503, INSTALL)
        if set(body) - {'filters'}:
            raise ValidationError({'_form': 'Unknown report argument'})
        with self.business.context(user, readonly=True) as context:
            data = report_context(context, report, body.get('filters', {}))
        content = render(design, data, self.business)
        return pdf_response(content, safe_filename(report.title or report_name) + '.pdf', 'inline')


def safe_filename(value):
    cleaned = re.sub(r'[^\w .()-]+', '-', str(value), flags=re.UNICODE).strip(' .-')
    return cleaned[:120] or 'print'


def pdf_response(content, filename, disposition):
    from .business import Download
    from urllib.parse import quote
    # An inline PDF may show in the application's own preview frame; browsers do not show a sandboxed PDF.
    policy = b"default-src 'none'; frame-ancestors 'self'" if disposition == 'inline' else b"default-src 'none'; sandbox"
    return 200, Download(content), [(b'content-type', b'application/pdf'),
                                    (b'content-disposition', f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}".encode()),
                                    (b'content-security-policy', policy)]


# ---- data ------------------------------------------------------------------------------------

def python_value(field, value):
    if value is None:
        return None
    try:
        return field.clean(value)
    except (ValueError, TypeError, ArithmeticError, ValidationError):
        return value


def shown_fields(business, user, model):
    """Fields that reach a template: readable for this person and not hidden."""
    readable = business.policy.fields(user, model)
    return {name: field for name, field in model.__declared_fields__.items()
            if name in readable and not field.hidden and not name.startswith('evlet_')}


def document_context(business, tx, user, model, record):
    """The template context for one record, built from the same permission-filtered detail as a read."""
    from .fields import Link
    from .ui.metadata import view_for
    detail = business.detail(tx, user, record, row_limit=None)
    linked = {}

    def follow(target, record_id):
        key = (target, record_id)
        if key not in linked:
            try:
                found = business.policy.record(tx, user, target, record_id)
            except (AccessError, LookupError):
                linked[key] = None
            else:
                values = business.policy.serialize(tx, user, found)
                linked[key] = values_of(target, values, follow_links=False)
        return linked[key]

    def values_of(definition, values, follow_links=True):
        fields = shown_fields(business, user, definition)
        result = {'id': values.get('id')}
        for name, field in fields.items():
            if name not in values:
                continue
            value = values[name]
            if isinstance(field, Link) and value is not None and follow_links:
                value = follow(business.registry.resolve(field.target), value)
            elif isinstance(field, RichText) and value is not None:
                # Sanitised again here, so a template's {{ doc.brief }} is safe markup, never raw HTML; the
                # record's own images are embedded as data: URIs (evlet/images.py).
                from markupsafe import Markup
                from .images import embed
                value = Markup(embed(business, tx, definition, values.get('id'), sanitise(value)[0])) \
                    if isinstance(value, str) and values.get('id') is not None else Markup(sanitise(value)[0]) if isinstance(value, str) else None
            else:
                value = python_value(field, value)
            result[name] = value
        title_field = view_for(definition).get('title_field', 'id')
        title = result.get(title_field) if title_field in fields else None
        result.setdefault('title', title if title is not None else values.get('id'))
        result.setdefault('name', values.get('id'))
        return result

    def tree(definition, values):
        result = values_of(definition, values)
        for child in business.children.values():
            if child.parent is definition:
                rows = values.get('children', {}).get(child.name)
                if rows is not None:
                    result[child.name] = [tree(child.model, row) for row in rows]
        return result

    return {'doc': tree(model, detail), 'model': model, 'user': user}


def report_context(context, report, filters):
    """Applied filters and every row (page by page, up to MAX_REPORT_ROWS) of a report."""
    from .documents import settle
    from .fields import Boolean, Currency, Date as DateField, Decimal as DecimalField, Integer, Text as TextField
    from .forms import form_draft
    from .ui.metadata import label_for
    draft = form_draft(report, context, filters)
    errors, _ = settle(context, draft, complete=True)
    if errors:
        raise ValidationError(errors)
    object.__setattr__(draft, '_readonly', True)
    types = {'text': TextField, 'integer': Integer, 'decimal': DecimalField, 'currency': Currency, 'date': DateField, 'boolean': Boolean, 'link': Integer}
    rows, offset, total = [], 0, None
    while total is None or offset < min(total, MAX_REPORT_ROWS):
        context.limit, context.offset = 100, offset
        result = draft.run()
        if not isinstance(result, dict) or not isinstance(result.get('rows'), list) or type(result.get('total')) is not int:
            raise ValueError('Reports return a bounded rows/total result from context.list')
        total = result['total']
        if not result['rows']:
            break
        allowed = {column.name for column in report.columns}
        for row in result['rows']:
            values = row if isinstance(row, dict) else row.to_dict()
            rows.append({column.name: python_value(types[column.type](), values.get(column.name)) for column in report.columns
                         if column.name in allowed})
        offset += len(result['rows'])
    totals = {column.name: sum((row[column.name] for row in rows if isinstance(row[column.name], (int, Decimal))), Decimal(0))
              for column in report.columns if column.total}
    applied = [{'name': name, 'label': field.label or label_for(name), 'value': draft._values.get(name), 'field': field}
               for name, field in report.__declared_fields__.items() if draft._values.get(name) not in (None, '', ())]
    return {'report': {'title': report.title or report.__name__, 'filters': {item['name']: item['value'] for item in applied},
                       'applied': applied, 'rows': rows, 'columns': report.columns, 'totals': totals, 'total': total or 0,
                       'truncated': (total or 0) > len(rows)}, 'definition': report}


# ---- formatting ------------------------------------------------------------------------------

class Formats:
    """The helpers templates use: money, date, number, ar. Each isolates text direction."""

    def __init__(self, design, markup):
        self.design, self.markup = design, markup

    def ltr(self, text):
        return self.markup('<bdi dir="ltr">') + text + self.markup('</bdi>')

    def money(self, value, currency=None):
        if value is None or value == '':
            return ''
        amount = Decimal(str(value))
        code = currency or self.design.currency
        return self.ltr(f'{code} {amount:,.2f}' if code else f'{amount:,.2f}')

    def number(self, value, places=None):
        if value is None or value == '':
            return ''
        if isinstance(value, bool):
            return 'Yes' if value else 'No'
        amount = Decimal(str(value))
        if places is None:
            # Whole numbers print without decimals; fractions with at least two, and more only when they carry digits.
            exponent = amount.normalize().as_tuple().exponent
            places = 0 if amount == amount.to_integral() else max(2, -exponent if isinstance(exponent, int) else 2)
        return self.ltr(f'{amount:,.{int(places)}f}')

    def date(self, value):
        if value is None or value == '':
            return ''
        if isinstance(value, str):
            try:
                value = DateTime.fromisoformat(value) if 'T' in value else Date.fromisoformat(value)
            except ValueError:
                return value
        text = value.strftime(self.design.date_format + (' %H:%M' if isinstance(value, DateTime) else ''))
        return self.ltr(text)

    def ar(self, text):
        return self.markup('<span lang="ar" dir="rtl">') + ('' if text is None else str(text)) + self.markup('</span>')


def currency_code(field, row, business, design):
    source = getattr(field, 'currency', None)
    if source is None:
        return design.currency
    if re.fullmatch('[A-Z]{3}', source):
        return source
    if source.startswith('settings.'):
        _, app, key = source.split('.')
        try:
            return business.settings.values.get(app, key) or design.currency
        except Exception:
            return design.currency
    head, _, rest = source.partition('.')
    value = row.get(head)
    if rest:
        value = value.get(rest) if isinstance(value, dict) else None
    return value if isinstance(value, str) and re.fullmatch('[A-Z]{3}', value) else design.currency


def display(field, value, row, formats, business, design):
    """A value as print text, formatted by its field type (escaped by the caller's markup)."""
    from .fields import Boolean, Currency, Date as DateField, DateTime as DateTimeField, Decimal as DecimalField, Integer, Link, Percent
    if value is None or value == '':
        return ''
    if isinstance(field, RichText):
        from markupsafe import Markup
        # Markup came from document_context: sanitised there, with the record's images embedded.
        body = str(value) if isinstance(value, Markup) else sanitise(str(value))[0]
        return Markup('<div class="evlet-rich">' + body + '</div>')
    if isinstance(field, Link) or isinstance(value, dict):
        return '' if not isinstance(value, dict) else str(value.get('title') or '')
    if isinstance(field, Currency):
        return formats.money(value, currency_code(field, row, business, design))
    if isinstance(field, Boolean) or isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(field, (DateTimeField, DateField)) or isinstance(value, (Date, DateTime)):
        return formats.date(value)
    if isinstance(field, (DecimalField, Integer)) or isinstance(value, (Decimal, int)):
        text = formats.number(value)
        suffix = getattr(field, 'suffix', '') or ('%' if isinstance(field, Percent) else '')
        return text + suffix if suffix else text
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item) for item in value)
    return str(value)


def numeric(field):
    from .fields import Currency, Decimal as DecimalField, Integer, Link
    return isinstance(field, (Currency, DecimalField, Integer)) and not isinstance(field, Link)


# ---- compiling and rendering -----------------------------------------------------------------

def environment():
    from jinja2 import ChainableUndefined
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    env = ImmutableSandboxedEnvironment(autoescape=True, undefined=ChainableUndefined)
    # Values stay escaped: a template cannot turn record data back into markup.
    env.filters.pop('safe', None)
    return env


class Compiler:
    """One render: design + context -> HTML and CSS."""

    def __init__(self, design, context, business):
        from markupsafe import Markup
        self.design, self.context, self.business = design, context, business
        self.markup = Markup
        self.env = environment()
        self.formats = Formats(design, Markup)
        self.variables = {**{key: value for key, value in context.items() if key in ('doc', 'report')},
                          'money': self.formats.money, 'date': self.formats.date, 'number': self.formats.number, 'ar': self.formats.ar,
                          'title': design.title}
        report = context.get('definition')
        self.fields = report.__declared_fields__ if report is not None else context['model'].__declared_fields__
        self.report = report

    def fill(self, template):
        return self.markup(self.env.from_string(template).render(self.variables))

    def shows(self, block):
        if not block.show_if:
            return True
        return bool(self.env.compile_expression(block.show_if, undefined_to_none=True)(**self.variables))

    def field(self, path, fields=None, row=None):
        """The field and value at a path such as "customer" or "customer.email"."""
        fields = self.fields if fields is None else fields
        row = self.context['doc'] if row is None else row
        head, _, rest = path.partition('.')
        value, field = row.get(head), fields.get(head)
        if rest and isinstance(value, dict):
            target = getattr(field, 'target', None)
            target_fields = self.business.registry.resolve(target).__declared_fields__ if target is not None else {}
            return target_fields.get(rest), value.get(rest), value
        return field, value, row

    def label(self, path, label, fields=None):
        from .ui.metadata import label_for
        if label is not None:
            return label
        field = (self.fields if fields is None else fields).get(path.split('.')[0])
        return (getattr(field, 'label', None) or label_for(path.split('.')[-1])) if field is not None else label_for(path)

    def text(self, value):
        return self.fill(value).replace('\n', self.markup('<br>'))

    def block(self, block):
        if not self.shows(block):
            return ''
        kind = type(block).__name__.lower()
        if block.html is not None:
            inner = self.fill(block.html)
        else:
            inner = getattr(self, 'render_' + kind)(block)
        style = block.style()
        if isinstance(block, Spacer):
            style = '; '.join(part for part in (style, f'height: {block.height}') if part)
        attributes = f' style="{escape(style)}"' if style else ''
        return f'<div class="evlet-block evlet-{kind}"{attributes}>{inner}</div>'

    def render_heading(self, block):
        return f'<h1>{self.text(block.text)}</h1>'

    def render_text(self, block):
        return f'<p>{self.text(block.text)}</p>'

    def render_spacer(self, block):
        return ''

    def render_pagebreak(self, block):
        return ''

    def render_image(self, block):
        width = f' style="width: {escape(block.width)}"' if block.width else ''
        return f'<img src="{escape(asset_url(block.src))}" alt=""{width}>'

    def render_pagenumber(self, block):
        parts, position = [], 0
        for match in PLACEHOLDER.finditer(block.format):
            parts.append(escape(block.format[position:match.start()]))
            parts.append('<span class="evlet-page"></span>' if match[1] in ('page', '0') else '<span class="evlet-pages"></span>')
            position = match.end()
        parts.append(escape(block.format[position:]))
        return '<p>' + ''.join(parts) + '</p>'

    def render_fields(self, block):
        cells = []
        if self.report is not None:
            values = {item['name']: item for item in self.context['report']['applied']}
            for path, label in block.names:
                field = self.fields[path]
                value = values.get(path, {}).get('value')
                cells.append((self.label(path, label), display(field, value, {}, self.formats, self.business, self.design)))
        else:
            for path, label in block.names:
                field, value, row = self.field(path)
                cells.append((self.label(path, label), display(field, value, row, self.formats, self.business, self.design)))
        width = 100 / block.columns
        rows = []
        for start in range(0, len(cells), block.columns):
            chunk = cells[start:start + block.columns]
            rows.append('<tr>' + ''.join(f'<td style="width: {width:.2f}%"><span class="evlet-label">{escape(label)}</span>'
                                         f'<span class="evlet-value">{escape(value) if not hasattr(value, "__html__") else value}</span></td>'
                                         for label, value in chunk) + '</tr>')
        return '<table class="evlet-fields">' + ''.join(rows) + '</table>'

    def table_spec(self, block):
        """(columns as (name, label, field, column)), rows, row fields."""
        if self.report is not None:
            from .fields import Boolean, Currency, Date as DateField, Decimal as DecimalField, Integer, Text as TextField
            types = {'text': TextField, 'integer': Integer, 'decimal': DecimalField, 'currency': Currency, 'date': DateField, 'boolean': Boolean, 'link': TextField}
            declared = {column.name: column for column in self.report.columns}
            wanted = block.columns or tuple(Column(name) for name in declared)
            fields = {}
            for column in wanted:
                report_column = declared[column.name]
                fields[column.name] = types[report_column.type](**({'currency': report_column.unit} if report_column.type == 'currency' and report_column.unit and re.fullmatch('[A-Z]{3}', report_column.unit) else {}))
            labels = {name: (column.label or None) for name, column in declared.items()}
            return [(column.name, column.label or labels[column.name] or self.label(column.name, None, {}), fields[column.name], column)
                    for column in wanted], self.context['report']['rows'], fields
        child = next(child for child in self.business.children.values()
                     if child.parent is self.context['model'] and child.name == block.source)
        fields = shown_fields(self.business, self.context['user'], child.model)
        wanted = block.columns or tuple(Column(name) for name in fields if name != child.link)
        columns = [(column.name, self.label(column.name, column.label, child.model.__declared_fields__), fields.get(column.name), column)
                   for column in wanted if column.name in fields]
        return columns, self.context['doc'].get(block.source, []), fields

    def render_table(self, block):
        columns, rows, _ = self.table_spec(block)
        widths = any(column.width for _, _, _, column in columns)
        head = []
        for name, label, field, column in columns:
            classes = self.cell_classes(field, column)
            style = f' style="width: {escape(column.width)}"' if column.width else ''
            head.append(f'<th class="{classes}"{style}>{escape(label)}</th>')
        body = []
        for row in rows:
            cells = []
            for name, label, field, column in columns:
                value = display(field, row.get(name), row, self.formats, self.business, self.design)
                cells.append(f'<td class="{self.cell_classes(field, column)}">{value if hasattr(value, "__html__") else escape(value)}</td>')
            body.append('<tr>' + ''.join(cells) + '</tr>')
        if self.report is not None and self.context['report']['totals'] and any(name in self.context['report']['totals'] for name, *_ in columns):
            cells = []
            for index, (name, label, field, column) in enumerate(columns):
                total = self.context['report']['totals'].get(name)
                value = '' if total is None else display(field, total, {}, self.formats, self.business, self.design)
                cells.append(f'<td class="{self.cell_classes(field, column)}">{value if hasattr(value, "__html__") else escape(value or ("Total" if index == 0 else ""))}</td>')
            body.append('<tr class="evlet-total">' + ''.join(cells) + '</tr>')
        layout = ' style="table-layout: fixed"' if widths else ''
        note = ''
        if self.report is not None and self.context['report']['truncated']:
            note = f'<p class="evlet-note">Showing the first {len(rows)} of {self.context["report"]["total"]} rows.</p>'
        return f'<table class="evlet-table"{layout}><thead><tr>{"".join(head)}</tr></thead><tbody>{"".join(body)}</tbody></table>{note}'

    @staticmethod
    def cell_classes(field, column):
        align = column.align or ('right' if field is not None and numeric(field) else 'left')
        wrap = column.wrap if column.wrap is not None else not (field is not None and numeric(field))
        return f'evlet-{align}' + ('' if wrap else ' evlet-nowrap')

    def render_totals(self, block):
        rows = []
        for path, label in block.names:
            if self.report is not None:
                declared = {column.name: column for column in self.report.columns}
                field = self.table_field(declared[path])
                value, row = self.context['report']['totals'].get(path), {}
                label = label or declared[path].label or self.label(path, None, {})
            else:
                field, value, row = self.field(path)
                label = self.label(path, label)
            shown = display(field, value, row, self.formats, self.business, self.design)
            rows.append(f'<tr><td>{escape(label)}</td><td class="evlet-right evlet-nowrap">{shown if hasattr(shown, "__html__") else escape(shown)}</td></tr>')
        return '<table class="evlet-totals">' + ''.join(rows) + '</table>'

    @staticmethod
    def table_field(column):
        from .fields import Currency, Decimal as DecimalField, Integer
        kinds = {'currency': Currency, 'decimal': DecimalField, 'integer': Integer}
        kind = kinds.get(column.type, DecimalField)
        return kind(currency=column.unit) if kind is Currency and column.unit and re.fullmatch('[A-Z]{3}', column.unit) else kind()

    def region(self, blocks):
        return ''.join(str(self.block(block)) for block in blocks)

    def watermark(self):
        mark = self.design.watermark
        if mark is None or (mark.show_if and not bool(self.env.compile_expression(mark.show_if, undefined_to_none=True)(**self.variables))):
            return ''
        if mark.image is not None:
            inner = f'<img src="{escape(asset_url(mark.image))}" alt="" style="width: 100%">'
        elif mark.html is not None:
            inner = str(self.fill(mark.html))
        else:
            text = str(self.fill(mark.text))
            # The text spans about size% of the page width: one em per character is roughly 0.55 of its width.
            width_mm = page_size_mm(self.design)[0] * mark.size / 100
            inner = f'<span style="font-size: {width_mm / max(len(self.fill(mark.text).striptags()), 1) / 0.55 * 2.835:.1f}pt">{text}</span>'
        style = f'width: {mark.size}%; opacity: {mark.opacity}; transform: translate(-50%, -50%) rotate({mark.rotate}deg)'
        return f'<div class="evlet-watermark" aria-hidden="true" style="{style}">{inner}</div>'

    def html(self):
        design = self.design
        header = f'<div class="evlet-header">{self.region(design.header)}</div>'
        first = f'<div class="evlet-header-first">{self.region(design.header_first)}</div>' if design.header_first is not None else ''
        footnote, footer_extra = '', ''
        if design.footnote is not None:
            content = self.region(design.footnote.blocks)
            if design.footnote.place == 'last_page':
                # The call sits in a line at the end of the flow; footnote-policy: block moves that line
                # (and the block before it) to the next page with the footnote when it does not fit.
                footnote = f'<div class="evlet-end">​<div class="evlet-footnote">{content}</div></div>'
            else:
                footer_extra = f'<div class="evlet-footnote-every">{content}</div>'
        footer = f'<div class="evlet-footer">{footer_extra}{self.region(design.footer)}</div>'
        title = escape(str(self.fill(design.title)) if design.title else design.__name__)
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head><body>'
                f'{header}{first}{footer}{self.watermark()}<main class="evlet-main">{self.region(design.main)}{footnote}</main></body></html>')

    def css(self):
        return stylesheet(self.design)


def page_size_mm(design):
    width, height = SIZES[design.size] if isinstance(design.size, str) else design.size
    def mm(value):
        number, unit = LENGTH.fullmatch(value)[1], LENGTH.fullmatch(value)[3]
        return float(number) * {'mm': 1, 'cm': 10, 'in': 25.4, 'pt': 25.4 / 72, 'px': 25.4 / 96}[unit]
    width, height = mm(width), mm(height)
    return (height, width) if design.orientation == 'landscape' else (width, height)


def stylesheet(design):
    top, right, bottom, left = design.margins
    width, height = SIZES[design.size] if isinstance(design.size, str) else design.size
    size = f'{width} {height}' if design.orientation == 'portrait' else f'{height} {width}'
    if design.footnote is not None and design.footnote.place == 'every_page':
        bottom = f'calc({bottom} + {design.footnote.height})'
    faces = ''.join(f'@font-face {{ font-family: "{"Noto Sans Arabic" if "Arabic" in font else "Noto Sans"}"; '
                    f'font-weight: {700 if "Bold" in font else 400}; src: url("{BASE}fonts/{font}.woff2"); }}\n' for font in FONTS)
    first = ''
    if design.header_first is not None or design.first_margin_top:
        first = '@page :first { ' + (f'margin-top: {design.first_margin_top}; ' if design.first_margin_top else '') + \
                ('@top-center { content: element(evlet-header-first); } ' if design.header_first is not None else '') + '}'
    return faces + f'''
@page {{
  size: {size}; margin: {top} {right} {bottom} {left};
  @top-center {{ content: element(evlet-header); width: 100%; vertical-align: bottom; padding-bottom: 3mm; }}
  @bottom-center {{ content: element(evlet-footer); width: 100%; vertical-align: top; padding-top: 3mm; }}
  @footnote {{ margin-top: 4mm; }}
}}
{first}
html {{ font-family: "Noto Sans", "Noto Sans Arabic", sans-serif; font-size: {design.font_size}; color: #1d1d1f; line-height: 1.4; }}
body {{ margin: 0; }}
p {{ margin: 0; orphans: 3; widows: 3; }}
h1 {{ font-size: 1.6em; margin: 0 0 1mm; }}
[lang="ar"], [dir="rtl"] {{ font-family: "Noto Sans Arabic", "Noto Sans", sans-serif; }}
.evlet-header {{ position: running(evlet-header); }}
.evlet-header-first {{ position: running(evlet-header-first); }}
.evlet-footer {{ position: running(evlet-footer); font-size: 0.85em; color: #555; }}
.evlet-footnote-every {{ height: {design.footnote.height if design.footnote is not None and design.footnote.height else 0}; overflow: hidden; margin-bottom: 2mm; }}
.evlet-page::before {{ content: counter(page); }}
.evlet-pages::before {{ content: counter(pages); }}
.evlet-block {{ margin-bottom: 3mm; }}
.evlet-pagebreak {{ break-before: page; margin: 0; }}
.evlet-spacer {{ margin: 0; }}
.evlet-image img {{ max-width: 100%; }}
.evlet-fields {{ width: 100%; border-collapse: collapse; break-inside: avoid; }}
.evlet-fields td {{ vertical-align: top; padding: 0.6mm 3mm 0.6mm 0; }}
.evlet-label {{ display: block; font-size: 0.8em; color: #666; }}
.evlet-rich > :first-child {{ margin-top: 0; }}
.evlet-rich p, .evlet-rich ul, .evlet-rich ol, .evlet-rich blockquote, .evlet-rich pre {{ margin: 0 0 1.5mm; }}
.evlet-rich h2 {{ font-size: 1.2em; margin: 2mm 0 1mm; }}
.evlet-rich h3 {{ font-size: 1.05em; margin: 2mm 0 1mm; }}
.evlet-rich ul, .evlet-rich ol {{ padding-left: 5mm; }}
.evlet-rich ul[data-type="checklist"] {{ list-style: none; padding-left: 1mm; }}
.evlet-rich ul[data-type="checklist"] > li::before {{ content: "\2610  "; }}
.evlet-rich ul[data-type="checklist"] > li[data-checked="true"]::before {{ content: "\2611  "; }}
.evlet-rich blockquote {{ border-left: 0.8pt solid #999; padding-left: 3mm; color: #444; }}
.evlet-rich pre, .evlet-rich code {{ font-family: monospace; font-size: 0.9em; white-space: pre-wrap; }}
.evlet-rich hr {{ border: 0; border-top: 0.4pt solid #999; }}
.evlet-rich figure {{ margin: 0 0 2mm; break-inside: avoid; }}
.evlet-rich figure img {{ max-width: 100%; }}
.evlet-rich figure.rt-sm img {{ width: 33%; }}
.evlet-rich figure.rt-md img {{ width: 66%; }}
.evlet-rich figure.rt-full img {{ width: 100%; }}
.evlet-rich figcaption {{ font-size: 0.85em; color: #555; }}
.evlet-table {{ width: 100%; border-collapse: collapse; }}
.evlet-table th {{ text-align: left; font-weight: 700; border-bottom: 0.8pt solid #333; padding: 1.2mm 1.5mm; }}
.evlet-table td {{ border-bottom: 0.4pt solid #ccc; padding: 1.1mm 1.5mm; vertical-align: top; overflow-wrap: anywhere; }}
.evlet-table tbody tr {{ break-inside: avoid; }}
.evlet-table tr.evlet-total {{ break-before: avoid; }}
.evlet-table tr.evlet-total td {{ font-weight: 700; border-top: 0.8pt solid #333; }}
.evlet-block .evlet-right {{ text-align: right; }} .evlet-block .evlet-center {{ text-align: center; }} .evlet-block .evlet-left {{ text-align: left; }}
.evlet-nowrap {{ white-space: nowrap; overflow-wrap: normal !important; }}
.evlet-totals {{ margin-left: auto; min-width: 45%; border-collapse: collapse; }}
.evlet-totals td {{ padding: 0.8mm 2mm; }}
.evlet-totals tr:last-child td {{ font-weight: 700; border-top: 0.8pt solid #333; }}
.evlet-block.evlet-totals {{ break-inside: avoid; break-before: avoid; }}
.evlet-note {{ font-size: 0.85em; color: #666; margin-top: 2mm; }}
.evlet-end {{ break-before: avoid; margin: 0; line-height: 1; }}
.evlet-footnote {{ float: footnote; footnote-display: block; footnote-policy: block; }}
.evlet-footnote::footnote-call, .evlet-footnote::footnote-marker {{ content: none; }}
.evlet-watermark {{ position: fixed; top: 50%; left: 50%; text-align: center; z-index: -1; color: #000; white-space: nowrap; }}
'''


def asset_url(src):
    return src if src.startswith('data:') else BASE + 'assets/' + src


def fetcher(design):
    """A URL fetcher that serves only the bundled fonts, the design's assets and data: URIs."""
    from weasyprint.urls import URLFetcher, URLFetcherResponse
    import mimetypes

    class PrintFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            if url.startswith('data:'):
                return super().fetch(url, headers)
            if url.startswith(BASE + 'fonts/'):
                name = url.removeprefix(BASE + 'fonts/').removesuffix('.woff2')
                if name in FONTS:
                    data = resources.files('evlet.resources').joinpath('print', 'fonts', name + '.woff2').read_bytes()
                    return URLFetcherResponse(url, data, {'Content-Type': 'font/woff2'})
            if url.startswith(BASE + 'assets/'):
                name = url.removeprefix(BASE + 'assets/')
                if name in design.assets:
                    return URLFetcherResponse(url, design.assets[name], {'Content-Type': mimetypes.guess_type(name)[0] or 'application/octet-stream'})
            raise ValueError('Print designs load only their own assets')

    return PrintFetcher(allowed_protocols=('https', 'data'))


def document(design, context, business):
    """The laid-out WeasyPrint document (tests measure it)."""
    import logging
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration
    logging.getLogger('weasyprint').setLevel(logging.ERROR)
    compiler = Compiler(design, context, business)
    url_fetcher, fonts = fetcher(design), FontConfiguration()
    # The bundled @font-face rules register in this configuration; both the stylesheet and the render take it.
    return HTML(string=compiler.html(), base_url=BASE, url_fetcher=url_fetcher).render(
        stylesheets=[CSS(string=compiler.css(), base_url=BASE, url_fetcher=url_fetcher, font_config=fonts)], font_config=fonts)


def render(design, context, business):
    return document(design, context, business).write_pdf()


# ---- stored designs (Controls) ---------------------------------------------------------------

BLOCKS = {'heading': Heading, 'text': Text, 'fields': Fields, 'table': Table, 'totals': Totals, 'image': Image,
          'spacer': Spacer, 'pagebreak': PageBreak, 'pagenumber': PageNumber}
COMMON = ('html', 'show_if', 'align', 'size', 'weight', 'space_before', 'space_after', 'break_before')
NAME = re.compile(r'[A-Z][A-Za-z0-9]{0,63}')
# A stored design's data: URIs and HTML are kept small; the whole definition at most 256 KiB.
MAX_DEFINITION = 262144


def block_to(block):
    """A block as JSON: {"type": ..., its own options, the shared options that are set}."""
    data = {'type': type(block).__name__.lower()}
    if isinstance(block, (Heading, Text)):
        data['text'] = block.text
    elif isinstance(block, (Fields, Totals)):
        data['names'] = [[name, label] for name, label in block.names]
        if isinstance(block, Fields):
            data['columns'] = block.columns
    elif isinstance(block, Table):
        data['source'] = block.source
        data['columns'] = [{key: value for key, value in vars(column).items() if value is not None} for column in block.columns]
    elif isinstance(block, Image):
        data.update(src=block.src, **({'width': block.width} if block.width else {}))
    elif isinstance(block, Spacer):
        data['height'] = block.height
    elif isinstance(block, PageNumber):
        data['format'] = block.format
    data.update({key: getattr(block, key) for key in COMMON if getattr(block, key) is not None})
    return data


def block_from(data):
    if not isinstance(data, dict) or data.get('type') not in BLOCKS:
        raise ValueError('Each block has a type: ' + ', '.join(BLOCKS))
    kind = BLOCKS[data['type']]
    options = {key: data[key] for key in COMMON if data.get(key) not in (None, '')}
    own = set(data) - set(COMMON) - {'type'}
    allowed = {Heading: {'text'}, Text: {'text'}, Fields: {'names', 'columns'}, Totals: {'names'}, Table: {'source', 'columns'},
               Image: {'src', 'width'}, Spacer: {'height'}, PageBreak: set(), PageNumber: {'format'}}[kind]
    if own - allowed:
        raise ValueError(f'A {data["type"]} block does not take ' + ', '.join(sorted(own - allowed)))
    def pairs(values):
        if not isinstance(values, list):
            raise ValueError('names is a list')
        return [value if isinstance(value, str) else tuple(value) if isinstance(value, list) and len(value) == 2 else value for value in values]
    if kind in (Heading, Text):
        if not isinstance(data.get('text', ''), str) or len(data.get('text', '')) > 20000:
            raise ValueError('text is at most 20000 characters')
        return kind(data.get('text', ''), **options)
    if kind is Fields:
        return Fields(*pairs(data.get('names', [])), columns=data.get('columns', 2), **options)
    if kind is Totals:
        return Totals(*pairs(data.get('names', [])), **options)
    if kind is Table:
        columns = data.get('columns', [])
        if not isinstance(columns, list) or any(not isinstance(column, dict) or set(column) - {'name', 'label', 'width', 'align', 'wrap'} for column in columns):
            raise ValueError('Table columns take name, label, width, align and wrap')
        return Table(data.get('source', ''), columns=tuple(Column(**column) for column in columns), **options)
    if kind is Image:
        if not str(data.get('src', '')).startswith('data:image/'):
            raise ValueError('A stored design takes images as data:image/... URIs')
        return Image(data['src'], width=data.get('width'), **options)
    if kind is Spacer:
        return Spacer(data.get('height', '6mm'), **options)
    if kind is PageNumber:
        return PageNumber(data.get('format', 'Page {page} of {pages}'), **options)
    return PageBreak(**options)


def design_to(design, registry):
    """A design as JSON, the shape stored designs keep and the designer edits."""
    footnote, watermark = design.footnote, design.watermark
    return {
        'title': design.title, 'default': design.default,
        **({'model': registry.name_for(registry.resolve(design.model))} if design.model is not None else {'report': getattr(design.report, '__resource_id__', design.report.__name__)}),
        **({'roles': list(design.roles)} if design.report is not None else {}),
        'page': {'size': design.size if isinstance(design.size, str) else list(design.size), 'orientation': design.orientation,
                 'margins': list(design.margins), 'first_margin_top': design.first_margin_top, 'font_size': design.font_size,
                 'currency': design.currency, 'date_format': design.date_format},
        'header': [block_to(block) for block in design.header],
        'header_first': None if design.header_first is None else [block_to(block) for block in design.header_first],
        'main': [block_to(block) for block in design.main],
        'footer': [block_to(block) for block in design.footer],
        'footnote': None if footnote is None else {'place': footnote.place, 'height': footnote.height, 'blocks': [block_to(block) for block in footnote.blocks]},
        'watermark': None if watermark is None else {key: value for key, value in vars(watermark).items() if value is not None},
    }


def design_from(name, data, business):
    """Build and check a PrintDesign class from stored JSON; ValueError names the problem."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError('A design name starts with a capital letter and uses letters and digits, such as TaxInvoicePrint')
    if not isinstance(data, dict):
        raise ValueError('Send the design as an object')
    import json
    if len(json.dumps(data)) > MAX_DEFINITION:
        raise ValueError('A design is at most 256 KiB, images included')
    known = {'title', 'default', 'model', 'report', 'roles', 'page', 'header', 'header_first', 'main', 'footer', 'footnote', 'watermark'}
    if set(data) - known:
        raise ValueError('Unknown design option: ' + ', '.join(sorted(set(data) - known)))
    attributes = {'title': data.get('title', ''), 'default': data.get('default', False), '__module__': __name__, '__stored__': True}
    if data.get('model') is not None:
        if data['model'] not in business.registry.models:
            raise ValueError('Choose a model of this application')
        attributes['model'] = business.registry.models[data['model']]
    if data.get('report') is not None:
        reports = getattr(getattr(business, 'forms', None), 'reports', {})
        if data['report'] not in reports:
            raise ValueError('Choose a report of this application')
        attributes['report'] = reports[data['report']]
        attributes['roles'] = tuple(data.get('roles') or attributes['report'].roles)
    page = data.get('page') or {}
    if not isinstance(page, dict) or set(page) - {'size', 'orientation', 'margins', 'first_margin_top', 'font_size', 'currency', 'date_format'}:
        raise ValueError('page takes size, orientation, margins, first_margin_top, font_size, currency and date_format')
    for key, value in page.items():
        if value is not None:
            attributes[key] = tuple(value) if key in ('margins', 'size') and isinstance(value, list) else value
    def blocks(key):
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, list) or len(value) > 200:
            raise ValueError(f'{key} is a list of at most 200 blocks')
        return tuple(block_from(item) for item in value)
    for key in ('header', 'main', 'footer'):
        attributes[key] = blocks(key) or ()
    attributes['header_first'] = blocks('header_first')
    footnote = data.get('footnote')
    if footnote is not None:
        if not isinstance(footnote, dict) or set(footnote) - {'place', 'height', 'blocks'}:
            raise ValueError('footnote takes place, height and blocks')
        attributes['footnote'] = Footnote(*(block_from(item) for item in footnote.get('blocks') or []),
                                          place=footnote.get('place', 'last_page'), height=footnote.get('height'))
    watermark = data.get('watermark')
    if watermark is not None:
        if not isinstance(watermark, dict) or set(watermark) - {'text', 'image', 'html', 'size', 'opacity', 'rotate', 'show_if'}:
            raise ValueError('watermark takes text, image, html, size, opacity, rotate and show_if')
        if watermark.get('image') is not None and not str(watermark['image']).startswith('data:image/'):
            raise ValueError('A stored design takes images as data:image/... URIs')
        attributes['watermark'] = Watermark(**watermark)
    design = type(name, (PrintDesign,), attributes)
    design.check(business.registry, business)
    return design


class Designer:
    """Controls routes for print designs: list, save and remove stored designs, and preview any definition."""

    def __init__(self, prints):
        self.prints = prints
        self.previews = {}

    def listing(self, user):
        business = self.prints.business
        registry = business.registry
        from .ui.metadata import label_for
        def fields(model):
            return [{'name': name, 'label': field.label or label_for(name), 'kind': field.kind}
                    for name, field in model.__declared_fields__.items() if not name.startswith('evlet_')]
        models = []
        for name, model in registry.models.items():
            if model in business.child_models:
                continue
            tables = [{'name': child.name, 'label': child.label or label_for(child.name), 'fields': fields(child.model)}
                      for child in business.children.values() if child.parent is model]
            models.append({'name': name, 'label': label_for(model.__name__), 'fields': fields(model), 'tables': tables,
                           'attachments': model in business.attachments})
        reports = [{'name': name, 'title': report.title or name, 'roles': list(report.roles), 'fields': fields(report),
                    'columns': [{'name': column.name, 'label': column.label or label_for(column.name), 'kind': column.type} for column in report.columns]}
                   for name, report in getattr(getattr(business, 'forms', None), 'reports', {}).items()]
        stored = self.prints.stored_rows()
        designs = [{'name': design.__name__, 'source': 'stored' if getattr(design, '__stored__', False) else 'app',
                    'definition': design_to(design, registry), **stored.get(design.__name__, {'changed_by': None, 'changed_at': None})}
                   for design in self.prints.current().values()]
        return {'available': available(), 'designs': designs, 'models': models, 'reports': reports, 'blocks': list(BLOCKS)}

    def save(self, user, name, body):
        from .controls import audit, bump
        if not isinstance(body, dict) or set(body) != {'definition'}:
            raise ValidationError({'_form': 'Send the definition.'})
        current = self.prints.current()
        if name in current and not getattr(current[name], '__stored__', False):
            raise ValidationError({'name': 'An app defines this design. Save a copy under another name.'})
        try:
            design = design_from(name, body['definition'], self.prints.business)
            owner = design.model or design.report
            if design.default and any(other.default and (other.model or other.report) is owner and other.__name__ != name for other in current.values()):
                raise ValueError(f'Another design is already the default for {owner.__name__}')
        except (ValueError, TypeError) as exc:
            raise ValidationError({'definition': str(exc)}) from None
        import json, time
        definition = design_to(design, self.prints.business.registry)
        with self.prints.business.database.transaction() as tx:
            before = tx.connection.execute('SELECT definition FROM evlet_print_designs WHERE name=?', (name,)).fetchone()
            tx.connection.execute('INSERT INTO evlet_print_designs(name, definition, changed_by, changed_at) VALUES (?,?,?,?) '
                                  'ON CONFLICT (name) DO UPDATE SET definition=excluded.definition, changed_by=excluded.changed_by, changed_at=excluded.changed_at',
                                  (name, json.dumps(definition, sort_keys=True), user.username, int(time.time())))
            audit(tx.connection, user.username, 'Print designs', name, 'Changed' if before else 'Added',
                  None if before is None else json.loads(before[0]), definition)
            bump(tx.connection)
        self.prints.reload()
        return {'name': name, 'source': 'stored', 'definition': definition}

    def remove(self, user, name):
        from .controls import audit, bump
        import json
        with self.prints.business.database.transaction() as tx:
            row = tx.connection.execute('SELECT definition FROM evlet_print_designs WHERE name=?', (name,)).fetchone()
            if row is None:
                raise AccessError(404, 'Print design not found')
            tx.connection.execute('DELETE FROM evlet_print_designs WHERE name=?', (name,))
            audit(tx.connection, user.username, 'Print designs', name, 'Removed', json.loads(row[0]), None)
            bump(tx.connection)
        self.prints.reload()

    def preview(self, user, body):
        """Render an unsaved definition against a record (or a report's filters); the PDF waits under a token."""
        import secrets, time
        if not isinstance(body, dict) or set(body) - {'definition', 'record', 'filters'} or 'definition' not in body:
            raise ValidationError({'_form': 'Send the definition, and a record or report filters.'})
        if not available():
            raise AccessError(503, INSTALL)
        try:
            design = design_from('PreviewPrint', body['definition'], self.prints.business)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'definition': str(exc)}) from None
        business = self.prints.business
        if design.model is not None:
            from .identity import RecordId
            try:
                record_id = RecordId(body.get('record'))
            except (ValueError, TypeError):
                raise ValidationError({'record': 'Choose a record to preview with.'}) from None
            model = business.registry.resolve(design.model)
            with business.database.transaction() as tx:
                record = business.policy.record(tx, user, model, record_id)
                content = render(design, document_context(business, tx, user, model, record), business)
        else:
            if user.role not in design.report.roles:
                raise AccessError(403, 'Report is unavailable')
            with business.context(user, readonly=True) as context:
                data = report_context(context, design.report, body.get('filters') or {})
            content = render(design, data, business)
        now = time.time()
        for token in [token for token, (_, _, expires) in self.previews.items() if expires < now]:
            self.previews.pop(token, None)
        while len(self.previews) >= 20:
            self.previews.pop(next(iter(self.previews)))
        token = secrets.token_urlsafe(18)
        self.previews[token] = (user.id, content, now + 600)
        return {'url': f'/api/_controls/prints/preview/{token}.pdf', 'size': len(content)}

    def preview_pdf(self, user, token):
        found = self.previews.get(token)
        import time
        if found is None or found[0] != user.id or found[2] < time.time():
            raise AccessError(404, 'The preview has expired; change the design to render it again')
        return pdf_response(found[1], 'Preview.pdf', 'inline')

    def dispatch(self, path, method, body, user):
        if path == '/prints' and method == 'GET':
            return 200, self.listing(user), []
        if path == '/prints/preview' and method == 'POST':
            return 200, self.preview(user, body), []
        match = re.fullmatch(r'/prints/preview/([A-Za-z0-9_-]{24})\.pdf', path)
        if match and method == 'GET':
            return self.preview_pdf(user, match[1])
        match = re.fullmatch(r'/prints/([A-Za-z][A-Za-z0-9]{0,63})', path)
        if match and method == 'PUT':
            return 200, self.save(user, match[1], body), []
        if match and method == 'DELETE':
            self.remove(user, match[1])
            return 204, None, []
        return None
