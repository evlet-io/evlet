"""Section rows of a Table declared with sections=True: headings that group the rows below them.

A section row has an id, a label and a place among the rows, and none of the row model's fields.
It is stored in the framework table evlet_sections, never in the row table, so row queries, reports,
sums, derive(), postings and validation see only real rows. See docs/model-engine.md#section-rows.
"""

from decimal import Decimal
from uuid import uuid4

from .identity import RecordId, new_id, sequential
from .models import ValidationError
from . import qb
from .adapter import Column, TableDefinition

STORE = 'evlet_sections'
LABEL_LENGTH = 200
# Section rows one collection may hold, apart from its max_rows of real rows.
MAX_SECTIONS = 100
KEYS = frozenset({'id', 'section', '_key'})


class SectionRow:
    """One heading of a Table: its id (None until saved), label and place.

    `at` is the number of real rows before it, so it sits just above the row at that index.
    `_values` holds the stored position, so row ordering helpers treat it like a row.
    """
    __slots__ = ('id', 'label', 'at', 'key', '_values', '_placed')

    def __init__(self, label, *, id=None, at=0, position=None, key=None, placed=False):
        self.id, self.label, self.at = id, label, at
        self.key = key or (str(id) if id is not None else uuid4().hex)
        self._values = {'evlet_position': position}
        self._placed = placed

    def copy(self):
        return SectionRow(self.label, id=self.id, at=self.at, position=self._values['evlet_position'], key=self.key)

    def __repr__(self):
        return f'SectionRow({self.label!r}, id={self.id!r}, at={self.at})'


class TableRows(list):
    """The real rows of a Table with sections; iterating it never yields a section row.

    `sections()` gives the grouping as (label, rows) pairs, in order. Rows above the first section come
    first with the label None (only when there are any), and a section with no rows has an empty tuple.
    """
    __slots__ = ('_sections',)

    def __init__(self, rows, sections):
        super().__init__(rows)
        self._sections = sections

    def sections(self):
        return groups(list(self), self._sections)


def enabled(model, name):
    """Whether Table `name` of `model` (a declared or generated model class) holds section rows."""
    table = getattr(model, '__tables__', {}).get(name)
    return bool(getattr(table, 'sections', False))


def label_of(value, where):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > LABEL_LENGTH:
        raise ValidationError({where: f'A section label is text of 1 to {LABEL_LENGTH} characters'})
    return value.strip()


def is_entry(value):
    """Whether a submitted row is a section row: an object with `section` (and only id and _key besides)."""
    return isinstance(value, dict) and 'section' in value


def check_entry(value, where):
    if set(value) - KEYS:
        raise ValidationError({where: 'A section row takes only id, section and _key; '
                                      'it has none of the row fields'})


def groups(rows, sections):
    """(label, rows) pairs in order: rows before the first section under None (when there are any), then each
    section with the rows up to the next one, empty sections included."""
    result, ordered = [], sorted(sections, key=lambda item: item.at)
    start = min(ordered[0].at, len(rows)) if ordered else len(rows)
    if start:
        result.append((None, tuple(rows[:start])))
    for index, section in enumerate(ordered):
        begin = min(section.at, len(rows))
        end = min(ordered[index + 1].at, len(rows)) if index + 1 < len(ordered) else len(rows)
        result.append((section.label, tuple(rows[begin:max(begin, end)])))
    return result


def combined(rows, sections):
    """Rows and section rows in one list, in display order."""
    result, ordered, index = [], sorted(sections, key=lambda item: item.at), 0
    for section in ordered:
        while index < min(section.at, len(rows)):
            result.append(rows[index])
            index += 1
        result.append(section)
    return result + list(rows[index:])


def split(items):
    """(rows, sections) from a mixed list, each section placed before the rows that follow it."""
    rows, found = [], []
    for item in items:
        if isinstance(item, SectionRow):
            item.at = len(rows)
            found.append(item)
        else:
            rows.append(item)
    return rows, found


def anchored(rows, sections):
    """Section rows placed by stored positions among saved rows: each before the first row stored after it."""
    result = []
    for item in sections:
        position = item._values['evlet_position']
        item.at = sum(1 for row in rows if (row._values.get('evlet_position') or 0, row.id) < (position, item.id))
        result.append(item)
    return result


def reanchor(old_rows, new_rows, sections):
    """Sections kept when a Python handler assigns a Table's rows without naming its sections: each stays
    above the first of its following rows that is still there, else at the end."""
    index = {id(row): position for position, row in enumerate(new_rows)}
    result, floor = [], 0
    for section in sorted(sections, key=lambda item: item.at):
        following = [index[id(row)] for row in old_rows[section.at:] if id(row) in index]
        at = max(floor, following[0] if following else len(new_rows))
        section.at, floor = at, at
        result.append(section)
    return result


def ordered_positions(items):
    """Whether stored positions increase along a mixed list (rows and sections)."""
    values = [item._values.get('evlet_position') for item in items]
    return all(type(value) is int for value in values) and all(a < b for a, b in zip(values, values[1:]))


def total_fields(model, name):
    """Row fields the Table's total line sums: the parent's unfiltered finite sums over the Table
    (total = lines.sum("amount")), in declaration order. Section rows show the same sums for their rows."""
    from .calc import Expr
    table = getattr(model, '__tables__', {}).get(name)
    result = []
    for field in getattr(model, '__declared_fields__', {}).values():
        compute = field.compute
        if isinstance(compute, Expr) and compute.op == 'aggregate' and compute.meta.get('kind') == 'sum' \
                and compute.meta.get('table') is table and compute.meta.get('where') is None \
                and compute.meta.get('field') and compute.meta['field'] not in result:
            result.append(compute.meta['field'])
    return tuple(result)


def sums(rows, fields):
    """{field: Decimal} of each field over rows (dicts or drafts); a row without a value adds nothing."""
    result = {}
    for name in fields:
        total = Decimal(0)
        for row in rows:
            value = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
            if value not in (None, '') and not isinstance(value, bool):
                total += Decimal(str(value))
        result[name] = total
    return result


# Storage

def create_store(connection):
    connection.ensure_table(TableDefinition(STORE, (
        Column('id', 'INTEGER', primary=True), Column('collection', 'TEXT', required=True), Column('owner', 'INTEGER', required=True),
        Column('position', 'INTEGER', required=True), Column('label', 'TEXT', required=True))))
    connection.ensure_index(f'{STORE}_owner', STORE, ('collection', 'owner', 'position'))


def store_name(registry, child_model):
    return registry.name_for(child_model)


def load(tx, child, owner_ids):
    """{owner id: [SectionRow]} of one collection, in position order (positions as stored, `at` unset)."""
    from .permissions import CHUNK
    if not enabled(child.parent, child.name):
        return {}
    collection, ids, result = store_name(tx.registry, child.model), list(dict.fromkeys(owner_ids)), {}
    for start in range(0, len(ids), CHUNK):
        part = ids[start:start + CHUNK]
        query = qb.select(STORE, 'id', 'owner', 'position', 'label').where(qb.in_('owner', part), collection=collection)
        for row in tx.connection.run(query.order_by('owner', 'position', 'id')):
            result.setdefault(RecordId(row[1]), []).append(SectionRow(row[3], id=RecordId(row[0]), position=row[2]))
    return result


def _new_id(tx, child):
    """A section id no row of the same table has, so ids in a Table's rows never clash."""
    table = store_name(tx.registry, child.model)
    for _ in range(8):
        if sequential():
            last = tx.connection.run(qb.select(STORE, qb.max('id'))).value()
            candidate = RecordId(max(int(last or 0), 2**40) + 1)
        else:
            candidate = new_id()
        if tx.connection.run(qb.select(table, 'id').where(id=candidate)).one() is None:
            return candidate
    raise ValidationError({child.name: 'Could not find a free section id'})


def changes(old, new, old_rows=(), new_rows=()):
    """{"added", "renamed", "removed", "moved"} between two lists of section rows; {} when they are the same.

    A kept section has moved when it changed places with another section, or when the first row below it that is in
    both versions is another row; rows added or removed around it do not move it."""
    before = {item.id: item for item in old if item.id is not None}
    kept = {item.id for item in new if item.id is not None}
    result = {'added': [{'id': item.id, 'label': item.label} for item in new if item.id not in before],
              'renamed': [{'id': item.id, 'old': before[item.id].label, 'new': item.label} for item in new
                          if item.id in before and before[item.id].label != item.label],
              'removed': [{'id': item.id, 'label': item.label} for item in old if item.id not in kept]}
    result = {key: value for key, value in result.items() if value}
    common = {row.id for row in old_rows if row.id is not None} & {row.id for row in new_rows if row.id is not None}

    def anchor(item, rows):
        return next((row.id for row in rows[item.at:] if row.id in common), None)
    if [(item.id, anchor(item, list(old_rows))) for item in sorted(old, key=lambda s: s.at) if item.id in kept] != \
            [(item.id, anchor(item, list(new_rows))) for item in sorted(new, key=lambda s: s.at) if item.id in before]:
        result['moved'] = True
    return result


def persist(context, child, owner_id, old, new, keys, old_rows=(), new_rows=()):
    """Write one collection's section rows and return changes(old, new).

    Adding a section needs create permission on the row model, renaming or placing one update, and removing one
    delete, like the rows themselves."""
    tx, user, policy = context._tx, context.user, context._business.policy
    collection = store_name(tx.registry, child.model)
    before = {item.id: item for item in old}
    kept = {item.id for item in new if item.id is not None}
    removed = [item.id for item in old if item.id not in kept]
    if removed:
        policy.require(user, child.model, 'delete')
        tx.connection.run(qb.delete(STORE).where(qb.in_('id', removed), collection=collection))
    for item in new:
        position = item._values['evlet_position']
        if item.id is None:
            policy.require(user, child.model, 'create')
            item.id = _new_id(tx, child)
            tx.connection.run(qb.insert(STORE, id=item.id, collection=collection, owner=owner_id, position=position, label=item.label))
            keys[item.key] = item.id
            continue
        previous = before[item.id]
        if previous.label != item.label or previous._values['evlet_position'] != position and item._placed:
            policy.require(user, child.model, 'update')
        if previous.label != item.label or previous._values['evlet_position'] != position:
            tx.connection.run(qb.update(STORE, label=item.label, position=position).where(collection=collection, id=item.id))
    return changes(old, new, old_rows, new_rows)


def forget(connection, registry, model, ids):
    """Delete the section rows of every sectioned Table that records or rows of `model` own."""
    from .permissions import CHUNK
    names = [registry.name_for(child.model) for child in registry.children if child.parent is model and enabled(model, child.name)]
    ids = list(ids)
    if not names or not ids:
        return
    for start in range(0, len(ids), CHUNK):
        part = ids[start:start + CHUNK]
        connection.run(qb.delete(STORE).where(qb.in_('collection', names), qb.in_('owner', part)))


def signature(draft):
    """Every collection's section rows in a document tree as comparable data: {(row path, table): [(id, label, at)]}."""
    from .documents import walk
    result = {}
    for prefix, item in walk(draft):
        for name, found in getattr(item, '_sections', {}).items():
            if found:
                result[prefix, name] = [(section.id, section.label, section.at) for section in sorted(found, key=lambda s: s.at)]
    return result


def payload(rows, sections, *, complete=True):
    """Serialized rows with section rows {"id", "section"} placed by stored position.

    `rows` are (position, row id, serialized row) in order. When `complete` is False only some rows were
    embedded, so sections after the last embedded row are left out."""
    result, ordered, index = [], sorted(sections, key=lambda item: (item._values['evlet_position'], item.id)), 0
    last = rows[-1][:2] if rows else None
    for section in ordered:
        key = (section._values['evlet_position'], section.id)
        while index < len(rows) and rows[index][:2] < key:
            result.append(rows[index][2])
            index += 1
        if not complete and (last is None or key > last):
            break
        result.append({'id': section.id, 'section': section.label})
    return result + [row[2] for row in rows[index:]]
