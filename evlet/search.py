"""Record search for Link dropdowns, record pickers and the search bar: GET /api/{model}/search.

    GET /api/item/search?q=bx&link=order_line.item&values={"warehouse":"0ABC..."}&limit=8
    -> {"items": [{"id": "...", "title": "Box 40 x 40", "subtext": "In stock: 12 box"}, ...]}

Matching. A model that declares Meta.search_fields is searched in its title and those fields, fuzzily: one
query reads at most CANDIDATES records whose searched text holds the typed characters in order (a LIKE
'%b%x%' prefilter, qb.subsequence_ci), prefix matches first, then text matches, then the rest. Python ranks
that bounded set: the whole value, then a value starting with the text, then a word starting with it, then the
text inside a word, then the characters in order with the tightest span; the title before the other fields,
shorter values first. The model's own text fields the option shows with F("field") are searched too, so what
a dropdown shows can be typed; they rank after every match in the title and Meta.search_fields. A model
without Meta.search_fields is searched as the list's search box searches it (the text in its default search
fields and the option's fields), in the list's default order.

Link context. link=<model>.<field> names the Link the dropdown belongs to, on a model or a Table's row model.
Its filters= apply as declared: a literal value as it is, a Ref("path") with the value the form sent in
values= for that path. values= takes only the paths the Link reads (its filters and its option's where=), each
readable by this person on that form. A Ref whose field the person can't read is skipped, as the list route
always did; the save still checks the Link. A Ref the form left empty matches nothing.

Options. The title and subtext come from the Link's option=, else the target's Meta.option (declarations.Option).
The record query returns the id and the parts' columns; a F("link.field") part reads the linked records in one
more query, and each Count, Sum, Min or Max part in one grouped query over the related model, keyed by the
options' ids and its where=. Every query applies this person's row scope on its model; a part that reads a
field, model or form value the person can't read leaves the subtext out. Python joins the parts, so exact
decimals keep their value. hook= is trusted app code and runs last.
"""

from decimal import Decimal as PyDecimal
from datetime import date, datetime
import json
import re
from urllib.parse import parse_qs

from . import qb
from .auth import AccessError
from .calc import Expr
from .database import Session
from .declarations import Ref
from .fields import Boolean, Link, RichText, Text
from .identity import RecordId
from .models import ValidationError

LIMIT, MAX_LIMIT = 8, 20
# Records read and ranked in Python for one fuzzy search, at most.
CANDIDATES = 200
MAX_TEXT, MAX_VALUE = 100, 200
WORD = re.compile(r'[^\W_]+', re.UNICODE)


def route(business, scope, user, model):
    """GET /api/{model}/search: up to `limit` options this person may read, ranked by the typed text."""
    if scope['method'] not in ('GET', 'HEAD'):
        raise AccessError(405, 'Method not allowed')
    text, link, values, limit = parameters(scope)
    policy = business.policy
    policy.require(user, model, 'read')
    field, source = (None, None) if link is None else link_field(business, user, model, link)
    if field is not None:
        values = form_values(business, user, source, field, values)
    elif values:
        raise ValidationError({'values': 'Send values only with link='})
    filters = None if field is None else link_filters(business, user, source, field, values)
    if filters is None and field is not None:
        return 200, {'items': []}, []
    children = business.children.values()
    if filters:
        policy.query_fields(user, model, filters, [], database=business.database, children=children)
    option = field.option if field is not None and field.option is not None else getattr(getattr(model, 'Meta', None), 'option', None)
    from .query import SqlQuery
    from .ui.metadata import view_for
    view = view_for(model)
    readable = policy.fields(user, model)
    title = view['title_field'] if view['title_field'] in readable else 'id'
    compiler = SqlQuery(business.registry, model, children=children)
    where = [item for item in (compiler.where(filters or {}), policy.scope(user, model)) if not qb.always(item)]
    table = qb.table(business.registry.name_for(model), 'record')
    declared = getattr(getattr(model, 'Meta', None), 'search_fields', None)
    with business.database._connection() as connection:
        connection.snapshot()
        shown = [name for name in shown_fields(model, option) if name in readable]
        if text and declared is not None:
            columns = [name for name in dict.fromkeys((title, *declared)) if name != 'id' and name in readable]
            found = ranked(connection, model, table, where, columns, text, limit,
                           extra=[name for name in shown if name not in columns])
        else:
            if text:
                searched = list(dict.fromkeys(name for name in (*view['search_fields'], *shown) if name in readable))
                where.append(compiler.where({}, text, search_fields=searched))
                where = [item for item in where if not qb.always(item)]
            order = compiler.ordering(business.controls.custom.default_order(policy, user, model).split(','))
            found = [(row[0], None) for row in connection.run(qb.select(table, 'record.id').where(*where).order_by(*order).limit(limit)).all()]
        tx = Session(connection, business.registry)
        items = options(business, tx, user, model, title, option, found, values)
    return 200, {'items': items}, []


def parameters(scope):
    """(text, link, values, limit) from the query string, or a 400."""
    try:
        raw = scope.get('query_string', b'')
        if len(raw) > 8192:
            raise ValueError()
        query = parse_qs(raw.decode('ascii'), keep_blank_values=True, max_num_fields=4)
        if set(query) - {'q', 'link', 'values', 'limit'} or any(len(items) != 1 for items in query.values()):
            raise ValueError()
        text = query.get('q', [''])[0].strip()
        link = query.get('link', [None])[0]
        values = json.loads(query.get('values', ['{}'])[0])
        limit = query.get('limit', [str(LIMIT)])[0]
        if len(text) > MAX_TEXT or '\x00' in text or not re.fullmatch('[0-9]{1,2}', limit) or not 1 <= int(limit) <= MAX_LIMIT:
            raise ValueError()
        if link is not None and not re.fullmatch(r'[a-z][a-z0-9_]{0,62}\.[A-Za-z][A-Za-z0-9_]{0,62}', link):
            raise ValueError()
        if not isinstance(values, dict) or len(values) > 16 or any(not isinstance(item, (str, int, bool)) and item is not None
                                                                   or isinstance(item, str) and len(item) > MAX_VALUE for item in values.values()):
            raise ValueError()
    except (ValueError, UnicodeError, RecursionError):
        raise ValidationError({'query': f'Use q (at most {MAX_TEXT} characters), link=<model>.<field>, values={{path: value}} '
                                        f'and a limit of 1 to {MAX_LIMIT}'}) from None
    return text, link, values, int(limit)


# ---------- the Link the dropdown belongs to ----------

def link_field(business, user, target, link):
    """(Link field, the model that declares it) for link=<model>.<field>: a Link to target this person can read."""
    name, field_name = link.split('.')
    source = business.registry.models.get(name)
    if source is None:
        source = next((child.model for child in business.children.values() if business.registry.name_for(child.model) == name), None)
    field = None if source is None else source.__app_fields__.get(field_name)
    if not isinstance(field, Link) or field.target is not target:
        raise ValidationError({'link': f'{link} is not a Link to {business.registry.name_for(target)}'})
    if not business.policy.allows(user, source, 'read') or field_name not in business.policy.fields(user, source):
        raise AccessError(404, 'Route not found')
    return field, source


def readable_path(business, user, source, path):
    """Whether this person reads the form field a Ref path names: on the Link's row, or parent.<field> of a Table row."""
    parts = path.split('.')
    if len(parts) == 1:
        return path in business.policy.fields(user, source)
    if len(parts) == 2 and parts[0] == 'parent':
        owner = next((child.parent for child in business.children.values() if child.model is source), None)
        return owner is not None and parts[1] in business.policy.fields(user, owner)
    return False


def form_values(business, user, source, field, values):
    """The form values sent for the paths the Link reads; any other path, or one this person can't read, is a 400."""
    declared = {value.path for value in field.filters.values() if isinstance(value, Ref)} | set(field.option.refs() if field.option else ())
    for path in values:
        if path not in declared or not readable_path(business, user, source, path):
            raise ValidationError({'values': f'{path} is not a form value this Link reads'})
    return {path: value for path, value in values.items() if value not in (None, '')}


def link_filters(business, user, source, field, values):
    """The Link's filters with the form's values, or None when a Ref the form left empty means nothing can match."""
    filters = {}
    for key, declared in field.filters.items():
        if not isinstance(declared, Ref):
            filters[key] = declared
        elif not readable_path(business, user, source, declared.path):
            continue  # The person can't read the form field; the save still checks the Link.
        elif declared.path in values:
            filters[key] = values[declared.path]
        else:
            return None
    return filters


# ---------- matching ----------

def shown_fields(model, option):
    """The model's own text fields an option shows with F("field"), searched too: what a dropdown shows can be typed."""
    if option is None:
        return []
    parts = (*(option.title or ()), *(option.subtext or ()))
    names = (part.meta['name'] for part in parts if isinstance(part, Expr) and part.op == 'ref')
    return list(dict.fromkeys(name for name in names if '.' not in name and isinstance(model.__fields__.get(name), Text)
                              and not isinstance(model.__fields__[name], (Link, RichText))))


def ranked(connection, model, table, where, columns, text, limit, extra=()):
    """[(id, matched column or None)]: the best `limit` of at most CANDIDATES records holding the text's characters in order.
    extra columns (fields the option shows) match too, ranked after every match in columns."""
    if not columns:
        return []
    primary = len(columns)
    columns = [*columns, *extra]
    squeezed = ''.join(text.split())
    stored = [qb.col(f'record.{name}_text' if isinstance(model.__fields__[name], RichText) else f'record.{name}') for name in columns]
    # Bounded queries without ORDER BY, so SQLite stops at the LIMIT instead of sorting every match on each
    # keystroke. Each kind of match ranks above the next: values starting with the text, values holding it, values
    # holding its characters in order. A query that returns fewer than CANDIDATES rows returned every match of its
    # kind, so the narrower one is asked only when the wider one filled up.
    def candidates(*conditions):
        found = {}
        for condition in conditions:
            for row in connection.run(qb.select(table, 'record.id', *stored).where(*where, condition).limit(CANDIDATES)).all():
                found.setdefault(row[0], row)
        return found

    starting = qb.or_(*(qb.starts_with(qb.lower(column), text.lower()) for column in stored[:primary]))
    holding = qb.or_(*(qb.contains_ci(column, text) for column in stored[:primary]))
    in_order = qb.or_(*(qb.subsequence_ci(column, squeezed) for column in stored))
    found = candidates(in_order)
    if len(found) >= CANDIDATES:
        better = candidates(holding)
        if len(better) >= CANDIDATES:
            better = candidates(starting) | better
        found = better | found
    rows = list(found.values())
    scored = []
    for row in rows:
        best = None
        for at, (name, value) in enumerate(zip(columns, tuple(row)[1:])):
            score = rank(value, text, squeezed)
            if score is not None and at >= primary:
                score = (score[0] + 5, score[1])
            if score is not None and (best is None or (score[0], at, score[1]) < best[:3]):
                best = (score[0], at, score[1], name)
        if best is not None:
            title = tuple(row)[1] if isinstance(tuple(row)[1], str) else ''
            scored.append((best[0], best[1], best[2], title.lower(), row[0], best[3]))
    scored.sort(key=lambda item: (*item[:4], int(item[4])))
    return [(item[4], None if item[5] == columns[0] else item[5]) for item in scored[:limit]]


def rank(value, text, squeezed):
    """(tier, detail), lower first, or None when the value doesn't hold the text: 0 the whole value, 1 a value
    starting with it, 2 a word starting with it, 3 inside a word, 4 the characters in order (detail: their span)."""
    if not isinstance(value, str) or not value:
        return None
    value, text = value.lower(), text.lower()
    if value == text:
        return 0, 0
    if value.startswith(text):
        return 1, len(value)
    words = [match.start() for match in WORD.finditer(value)]
    if any(value.startswith(text, start) for start in words):
        return 2, len(value)
    if text in value:
        return 3, value.index(text)
    if not squeezed:
        return None
    squeezed = squeezed.lower()
    span = subsequence_span(value, squeezed)
    return None if span is None else (4, span)


def subsequence_span(value, text):
    """The shortest stretch of value holding text's characters in order, or None."""
    best = None
    for start in (index for index, character in enumerate(value) if character == text[0]):
        at = start
        for character in text[1:]:
            at = value.find(character, at + 1)
            if at < 0:
                return best
        best = at - start + 1 if best is None else min(best, at - start + 1)
    return best


# ---------- what each option shows ----------

def options(business, tx, user, model, title, option, found, values):
    """[{id, title, subtext?}] for the found (id, matched column) pairs, in their order."""
    if not found:
        return []
    ids = [record_id for record_id, _ in found]
    policy = business.policy
    readable = policy.fields(user, model)
    title_parts = option.title if option is not None and option.title else None
    subtext_parts = option.subtext if option is not None else None
    reader = Parts(business, tx, user, model, ids, values)
    titles = reader.join(title_parts) if title_parts else None
    subtexts = reader.join(subtext_parts) if subtext_parts else None
    base = reader.columns(['id', title, *(name for _, name in found if name is not None)])
    items = []
    for record_id, matched in found:
        row = base.get(record_id)
        if row is None:
            continue
        shown_id = record_id if getattr(model, '__ledger__', False) else RecordId(record_id)
        own = str(shown_id) if title == 'id' else text_of(model.__fields__.get(title), row[title])
        item = {'id': shown_id, 'title': (titles or {}).get(record_id) or own or f'#{shown_id}'}
        shown = matched and matched in readable and not isinstance(model.__fields__.get(matched), RichText)
        subtext = subtexts.get(record_id, '') if subtexts is not None else text_of(model.__fields__.get(matched), row[matched]) if shown else ''
        if subtext:
            item['subtext'] = subtext
        items.append(item)
    if option is not None and option.hook is not None:
        items = hooked(business, tx, user, model, option.hook, items, values)
    return items


def hooked(business, tx, user, model, hook, items, values):
    """Run Option(hook=): trusted app code that may change each option's title and subtext."""
    from .documents import Context
    table = qb.table(business.registry.name_for(model), 'record')
    records = {row.id: row for row in (model._from_row(row) for row in tx.connection.run(
        qb.select(table).where(qb.in_('record.id', [int(item['id']) for item in items]))).all())}
    given = [{**item, 'subtext': item.get('subtext'), 'record': records.get(int(item['id']))} for item in items]
    returned = hook(Context(business, tx, user, readonly=True), given, dict(values))
    changed = {item['id']: item for item in returned or () if isinstance(item, dict) and 'id' in item}
    result = []
    for item in items:
        update = changed.get(item['id'], item)
        title, subtext = update.get('title'), update.get('subtext')
        item = {'id': item['id'], 'title': str(title)[:MAX_VALUE] if title not in (None, '') else item['title']}
        if subtext not in (None, ''):
            item['subtext'] = str(subtext)[:MAX_VALUE]
        result.append(item)
    return result


class Unavailable(Exception):
    """A part reads something this person can't read, or a form value that is empty."""


class Parts:
    """Reads the parts of options for a set of record ids, each with this person's row scope and fields."""

    def __init__(self, business, tx, user, model, ids, values):
        self.business, self.tx, self.user, self.model, self.ids, self.values = business, tx, user, model, ids, values
        self.policy, self.registry = business.policy, business.registry

    def columns(self, names):
        """{id: {column: value}} of the model's own columns."""
        names = list(dict.fromkeys(names))
        table = qb.table(self.registry.name_for(self.model), 'record')
        rows = self.tx.connection.run(qb.select(table, *(f'record.{name}' for name in names)).where(qb.in_('record.id', self.ids))).all()
        return {row[0]: dict(zip(names, tuple(row))) for row in rows}

    def join(self, parts):
        """{id: text} with the parts joined, or {} when a part is unavailable to this person."""
        try:
            pieces = [part if isinstance(part, str) else self.part(part) for part in parts]
        except Unavailable:
            return {}
        result = {}
        for record_id in self.ids:
            texts = [piece if isinstance(piece, str) else piece.get(record_id, '') for piece in pieces]
            # Literal text alone says nothing: without a value, no subtext.
            if any(text for piece, text in zip(pieces, texts) if not isinstance(piece, str)):
                result[record_id] = ''.join(texts).strip()
        return result

    def part(self, part):
        """{id: text} of one F() or aggregate part."""
        if isinstance(part, Expr):
            return self.field(part.meta['name'])
        return self.aggregate(part)

    def field(self, path):
        name, _, rest = path.partition('.')
        field = self.model.__fields__.get(name)
        if field is None or name not in self.policy.fields(self.user, self.model):
            raise Unavailable()
        values = {record_id: row[name] for record_id, row in self.columns(['id', name]).items()}
        if not isinstance(field, Link) and not rest:
            return {record_id: text_of(field, value) for record_id, value in values.items()}
        if not isinstance(field, Link) or field.ledger_target:
            raise Unavailable()
        # Through the Link: the linked record's field, or its title.
        target = field.target
        if not self.policy.allows(self.user, target, 'read'):
            raise Unavailable()
        from .ui.metadata import view_for
        shown = rest or view_for(target)['title_field']
        readable = self.policy.fields(self.user, target)
        if shown != 'id' and (shown not in readable or shown not in target.__fields__ or isinstance(target.__fields__[shown], Link)):
            if rest:
                raise Unavailable()
            shown = 'id'
        linked = [value for value in values.values() if value is not None]
        table = qb.table(self.registry.name_for(target), 'record')
        where = [item for item in (qb.in_('record.id', linked or [0]), self.policy.scope(self.user, target)) if not qb.always(item)]
        rows = {row[0]: row[1] for row in self.tx.connection.run(qb.select(table, 'record.id', f'record.{shown}').where(*where)).all()}
        return {record_id: text_of(target.__fields__.get(shown), rows.get(value)) for record_id, value in values.items()}

    def aggregate(self, part):
        """Count, Sum, Min or Max over the related rows of each option, with this person's scope on them."""
        related = self.registry.resolve(part.model)
        readable = self.policy.fields(self.user, related)
        names = {part.link, *(part.where or {}), *([part.field] if part.field else [])}
        if not self.policy.allows(self.user, related, 'read') or not names <= readable:
            raise Unavailable()
        filters = {}
        for key, value in (part.where or {}).items():
            if isinstance(value, Ref):
                if value.path not in self.values:
                    raise Unavailable()
                value = self.values[value.path]
            filters[key] = value
        from .query import SqlQuery
        try:
            condition = SqlQuery(self.registry, related).where(filters)
        except ValueError as exc:
            raise ValidationError({'values': str(exc)}) from None
        link = qb.col(f'record.{part.link}')
        function = {'count': qb.count, 'sum': qb.sum, 'min': qb.min, 'max': qb.max}[part.function]
        value = function() if part.function == 'count' else function(qb.col(f'record.{part.field}'))
        where = [item for item in (qb.in_(link, self.ids), condition, self.policy.scope(self.user, related)) if not qb.always(item)]
        table = qb.table(self.registry.name_for(related), 'record')
        rows = {row[0]: row[1] for row in self.tx.connection.run(qb.select(table, link, value).where(*where).group_by(link)).all()}
        field = None if part.function == 'count' else related.__fields__[part.field]
        empty = 0 if part.function in ('count', 'sum') else None
        return {record_id: text_of(field, rows.get(record_id, empty)) for record_id in self.ids}


def text_of(field, raw):
    """A stored value as option text: numbers without trailing zeros, dates in ISO form, Yes or No."""
    if raw is None or raw == '':
        return ''
    value = raw
    if field is not None and not isinstance(field, Link):
        try:
            value = field.from_storage(raw)
        except (TypeError, ValueError):
            value = raw
    if isinstance(field, Boolean) or isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, PyDecimal):
        value = value.normalize()
        return format(value if value != value.to_integral() else value.quantize(PyDecimal(1)), 'f')
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)[:MAX_VALUE]
