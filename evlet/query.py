"""Bounded, parameterized queries over registered fields, Link paths and child tables."""

from copy import copy
from typing import NamedTuple

from .fields import Integer, Link, LongText, Reference, RichText, Tags, Text
from .identity import RecordId, path_id
from . import qb
from .models import ValidationError, identifier

OPERATORS = {'eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'nin', 'isnull',
             'contains', 'icontains', 'nicontains', 'startswith'}
# One query takes at most this many conditions, counting those inside child groups.
MAX_CONDITIONS = 50
# Most values one `in` or `nin` condition takes from HTTP, Eva, tools and permission-aware Context.
MAX_IN_VALUES = 100
# Most `in` and `nin` values one trusted session query takes in all (tx.list, tx.count, tx.aggregate).
# Each value is one bound parameter, so this stays below SQLite's 32,766 parameters per statement (and the
# 65,535 of common server databases) with room for the query's other values.
TRUSTED_IN_VALUES = 30_000
MATCHES = ('any', 'none')
_TEXT = ('eq', 'ne', 'in', 'nin', 'contains', 'icontains', 'nicontains', 'startswith', 'isnull')
_ORDERED = ('eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'isnull')
_NUMBER = (*_ORDERED, 'in', 'nin')
_CHOICE = ('eq', 'ne', 'in', 'nin', 'isnull')
# The operators each field kind takes; the schema sends them as each field's "operators".
KIND_OPERATORS = {
    'text': _TEXT, 'email': _TEXT, 'url': _TEXT, 'phone': _TEXT, 'longtext': _TEXT, 'sequence': _TEXT,
    'select': _CHOICE, 'state': _CHOICE, 'link': _CHOICE,
    'integer': _NUMBER, 'decimal': _NUMBER, 'currency': _NUMBER, 'percent': _NUMBER, 'duration': _NUMBER,
    'date': _ORDERED, 'datetime': _ORDERED, 'time': _ORDERED,
    'boolean': ('eq', 'ne', 'isnull'), 'tags': ('contains', 'isnull'), 'multiselect': ('contains', 'isnull'),
    'reference': ('eq', 'ne', 'isnull'),
    # Rich text filters and searches its plain-text extract (the <name>_text column).
    'richtext': ('contains', 'icontains', 'nicontains', 'isnull'),
    # An image is there or not.
    'image': ('isnull',),
}


class Condition(NamedTuple):
    """One condition: label names it in errors (the filter key, or "table.field" in a child group)."""
    label: str
    path: str
    operator: str
    value: object


class ChildGroup(NamedTuple):
    """Conditions one row of a child table must all meet: any row (EXISTS) or no row (NOT EXISTS)."""
    label: str
    child: str
    match: str
    where: tuple


class RecordIdFilter(Integer):
    """The id column in filters: compared as an integer, given as a text id (or an int from Python)."""

    def validate(self, value):
        try:
            return RecordId(value)
        except ValueError:
            raise ValueError('Use a record id') from None


ID_FIELD = RecordIdFilter(minimum=1)


class RichTextColumn(LongText):
    """A RichText field's plain-text column in filters and search: values are compared as plain text."""
    kind = 'richtext'


RICH_TEXT_COLUMN = RichTextColumn(max_length=1000)


def operators_for(model, name):
    """The operators a filter may use on a field of model: empty when the field is not stored."""
    field = ID_FIELD if name == 'id' else model.__fields__.get(name)
    return list(KIND_OPERATORS.get(field.kind, ())) if field is not None else []


def child_tables(registry, children=()):
    """{(parent model, table name): ChildTable} for Table declarations and the given ChildTables."""
    return {(child.parent, child.name): child for child in (*registry.children, *children)}


def _condition(entry, label):
    if not isinstance(entry, (list, tuple)) or len(entry) != 3 or not isinstance(entry[0], str) or not isinstance(entry[1], str):
        raise ValidationError({label: 'Use [field, operator, value] conditions'})
    if entry[1] not in OPERATORS:
        raise ValidationError({entry[0]: f'Unknown operator: {entry[1]}'})
    return Condition(entry[0], entry[0], entry[1], entry[2])


def conditions(filters):
    """Filters as a list of Condition and ChildGroup, in order.

    A filter object maps "field__operator" to a value (eq when the operator is left out). A filter
    list holds [field, operator, value] conditions and child groups
    {"child": table, "match": "any" | "none", "where": [[field, operator, value], ...]};
    ["table.field", operator, value] is a group of one condition that any row meets."""
    if filters is None:
        return []
    items = []
    if isinstance(filters, dict):
        for key, value in filters.items():
            if not isinstance(key, str):
                raise ValidationError({str(key): 'Filter names must be strings'})
            parts = key.split('__')
            operator = parts.pop() if len(parts) > 1 and parts[-1] in OPERATORS else 'eq'
            items.append(Condition(key, '__'.join(parts), operator, value))
    elif isinstance(filters, (list, tuple)):
        for index, entry in enumerate(filters):
            label = f'filters.{index}'
            if isinstance(entry, dict):
                where = entry.get('where')
                if set(entry) - {'child', 'match', 'where'} or not isinstance(entry.get('child'), str) \
                        or not isinstance(where, (list, tuple)) or not where or entry.get('match', 'any') not in MATCHES:
                    raise ValidationError({label: 'Use {"child": table, "match": "any" or "none", "where": [conditions]}'})
                table = entry['child']
                inner = tuple(Condition(f'{table}.{item.path}', item.path, item.operator, item.value)
                              for item in (_condition(item, f'{label}.where.{position}') for position, item in enumerate(where)))
                items.append(ChildGroup(table, table, entry.get('match', 'any'), inner))
                continue
            item = _condition(entry, label)
            if '.' in item.path:
                table, _, path = item.path.partition('.')
                items.append(ChildGroup(item.label, table, 'any', (Condition(item.label, path, item.operator, item.value),)))
            else:
                items.append(item)
    else:
        raise ValidationError({'filters': 'Use a filter object or a list of conditions'})
    if sum(len(item.where) if isinstance(item, ChildGroup) else 1 for item in items) > MAX_CONDITIONS:
        raise ValidationError({'filters': f'Use at most {MAX_CONDITIONS} conditions'})
    return items


class SqlQuery:
    """Turns filters, search and ordering into query builder conditions and orders (evlet/qb.py), for the
    records of one model under the alias "record". Not the `evlet.Query` field source.

    children: ChildTables (beyond the registry's Table declarations) that child groups may name.
    trusted: the query comes from trusted Python code in a Session (tx.list, tx.count, tx.aggregate), so
    `in` and `nin` take up to TRUSTED_IN_VALUES values in all instead of MAX_IN_VALUES per condition.
    Permission-aware callers (HTTP, Eva, tools, MCP, Connector and Context) never set it."""

    def __init__(self, registry, model, *, children=(), trusted=False):
        self.registry = registry
        self.model = model
        self.alias = 0
        self.trusted = trusted
        self.in_values = 0
        self.children = child_tables(registry, children)
        registry.name_for(model)

    def reference(self, path, model=None, alias='record'):
        """(expression, field) for a field path; a Reference gives its two columns as a tuple. A Link path
        reads the target's field with a subquery, at most three hops."""
        model = self.model if model is None else model
        parts = path.split('__')
        if not 1 <= len(parts) <= 4:
            raise ValueError('Use at most three relationship hops')
        name = parts[0]
        field = ID_FIELD if name == 'id' else model.__fields__.get(name)
        if field is None:
            raise ValueError(f'Unknown field: {name}')
        identifier(name)
        if isinstance(field, Reference):
            if len(parts) > 1:
                raise ValueError(f'{name} is a Reference; filter it by value instead of following it')
            # A reference has two columns: the model name and the record ID.
            return tuple(qb.col(f'{alias}.{column}') for column, _ in field.columns(name)), field
        if isinstance(field, RichText):
            if len(parts) > 1:
                raise ValueError(f'{name} is rich text, not a Link')
            return qb.col(f'{alias}.{name}_text'), RICH_TEXT_COLUMN
        column = qb.col(f'{alias}.{name}')
        if len(parts) == 1:
            return column, field
        if not isinstance(field, Link):
            raise ValueError(f'{name} is not a Link')
        self.alias += 1
        target_alias = f'link{self.alias}'
        target_column, target_field = self.reference('__'.join(parts[1:]), field.target, target_alias)
        table = qb.table(self.registry.name_for(field.target), target_alias)
        return qb.select(table, target_column).where(qb.eq(f'{target_alias}.id', column)), target_field

    @staticmethod
    def loose(field):
        """A copy of field for query values: they need only type validation, not a model's business limits."""
        field = copy(field)
        field.required = False
        field.validator = None
        if hasattr(field, 'minimum'):
            field.minimum = None
        if hasattr(field, 'maximum'):
            field.maximum = None
        return field

    @staticmethod
    def value(field, value):
        field = SqlQuery.loose(field)
        return field.to_storage(field.clean(value))

    def where(self, filters, search="", *, search_fields=None):
        """One condition (qb.TRUE when there is nothing to check) for the filters and the search text."""
        clauses = []
        for item in conditions(filters):
            try:
                if isinstance(item, ChildGroup):
                    clauses.append(self.child_clause(item))
                else:
                    clauses.append(self.condition(item))
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValidationError({item.label: str(exc)}) from exc
        try:
            if not isinstance(search, str) or len(search) > 200:
                raise ValueError('Use search text of at most 200 characters')
            search.encode('utf-8')
            search = search.strip()
            if search:
                if '\x00' in search:
                    raise ValueError('Search must not contain NUL characters')
                from .ui.metadata import view_for
                matches = []
                searched_id = path_id(search.removeprefix('#'))
                if searched_id is not None:
                    matches.append(qb.eq('record.id', searched_id))
                for name in (view_for(self.model)['search_fields'] if search_fields is None else search_fields):
                    column, _ = self.reference(name)
                    matches.append(qb.contains_ci(column, search))
                clauses.append(qb.or_(*matches))
        except (ValueError, UnicodeError) as exc:
            raise ValidationError({'q': str(exc)}) from exc
        return qb.and_(*clauses)

    def child_clause(self, group):
        """EXISTS (any row) or NOT EXISTS (no row) over the child table, correlated on its parent Link:
        one row must meet every condition, and parent rows are never repeated."""
        child = self.children.get((self.model, group.child))
        if child is None:
            raise ValueError(f'Unknown child table: {group.child}')
        self.alias += 1
        alias = f'child{self.alias}'
        clauses = [qb.eq(f'{alias}.{child.link}', qb.col('record.id'))]
        for item in group.where:
            try:
                clauses.append(self.condition(item, child.model, alias))
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValidationError({item.label: str(exc)}) from exc
        rows = qb.select(qb.table(self.registry.name_for(child.model), alias), qb.col(f'{alias}.id')).where(*clauses)
        return qb.not_exists(rows) if group.match == 'none' else qb.exists(rows)

    def condition(self, item, model=None, alias='record'):
        model = self.model if model is None else model
        path, operator, value = item.path, item.operator, item.value
        if operator not in OPERATORS:
            raise ValueError(f'Unknown operator: {operator}')
        column, field = self.reference(path, model, alias)
        if operator not in KIND_OPERATORS.get(field.kind, ()):
            raise ValueError(f'{field.kind.title()} fields do not take {operator}')
        if isinstance(column, tuple):
            return self.reference_filter(column, field, operator, value)
        if operator == 'contains' and isinstance(field, Tags):
            # Tags are a JSON array of quoted tags without quotes inside, so a quoted tag matches one whole tag.
            return qb.contains(column, Tags.encode(Tags.tag(value)))
        if operator == 'isnull':
            if type(value) is not bool:
                raise ValueError('isnull expects true or false')
            return qb.is_null(column) if value else qb.not_null(column)
        if value is None and operator in ('eq', 'ne'):
            return qb.is_null(column) if operator == 'eq' else qb.not_null(column)
        if operator in ('in', 'nin'):
            if not isinstance(value, (list, tuple)) or not self.trusted and len(value) > MAX_IN_VALUES:
                raise ValueError(f'{operator} expects at most {MAX_IN_VALUES} values')
            self.in_values += len(value)
            if self.trusted and self.in_values > TRUSTED_IN_VALUES:
                raise ValueError(f'A trusted query takes at most {TRUSTED_IN_VALUES} in and nin values in all; '
                                 'split longer lists with itertools.batched')
            if any(item is None for item in value):
                raise ValueError('Use isnull to query null values')
            loose = self.loose(field)
            stored = [loose.to_storage(loose.clean(item)) for item in value]
            if operator == 'in':
                return qb.in_(column, stored)
            # Not in also matches empty values, as "is not one of these" reads.
            return qb.or_(qb.is_null(column), qb.not_in(column, stored)) if stored else qb.TRUE
        if operator == 'between':
            if not isinstance(value, (list, tuple)) or len(value) != 2 or None in value:
                raise ValueError('between expects [first, last]')
            return qb.between(column, self.value(field, value[0]), self.value(field, value[1]))
        if operator in ('contains', 'icontains', 'nicontains', 'startswith'):
            if not isinstance(field, Text) or not isinstance(value, str):
                raise ValueError('Text matching requires a text field and string')
            value.encode('utf-8')
            if '\x00' in value:
                raise ValueError('Text matching must not contain NUL characters')
            match = {'contains': qb.contains, 'icontains': qb.contains_ci, 'nicontains': qb.excludes_ci,
                     'startswith': qb.starts_with}[operator]
            return match(column, value)
        if value is None:
            raise ValueError('Use isnull to query null values')
        compare = {'eq': qb.eq, 'ne': qb.ne, 'gt': qb.gt, 'gte': qb.ge, 'lt': qb.lt, 'lte': qb.le}[operator]
        return compare(column, self.value(field, value))

    @staticmethod
    def reference_filter(columns, field, operator, value):
        model_column, id_column = columns
        if operator == 'isnull' or operator in ('eq', 'ne') and value is None:
            if operator == 'isnull' and type(value) is not bool:
                raise ValueError('isnull expects true or false')
            empty = value if operator == 'isnull' else operator == 'eq'
            return qb.is_null(id_column) if empty else qb.not_null(id_column)
        if operator != 'eq':
            raise ValueError('Reference fields support equality and isnull filters')
        target, record_id = field.validate(value)
        return qb.and_(qb.eq(model_column, target), qb.eq(id_column, record_id))

    def ordering(self, order_by):
        """The qb orders for order_by: empty values first going up and last going down, then by id."""
        if order_by is None:
            order_by = ('-id',)
        if isinstance(order_by, str):
            order_by = (order_by,)
        if not isinstance(order_by, (tuple, list)) or not 1 <= len(order_by) <= 4:
            raise ValidationError({'order_by': 'Use between one and four field names'})
        orders = []
        try:
            for key in order_by:
                if not isinstance(key, str):
                    raise ValueError('Order fields must be strings')
                descending = key.startswith('-')
                column, _ = self.reference(key[1:] if descending else key)
                if isinstance(column, tuple):
                    raise ValueError('Reference fields cannot be used for ordering')
                orders.append(qb.desc(column, nulls='last') if descending else qb.asc(column, nulls='first'))
            if not any(key.lstrip('-') == 'id' for key in order_by):
                orders.append(qb.asc('record.id'))
        except ValueError as exc:
            raise ValidationError({'order_by': str(exc)}) from exc
        return tuple(orders)


# Earlier name, kept so existing imports of evlet.query.Query keep working.
Query = SqlQuery
