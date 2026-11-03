"""Measures: exact aggregates over records, declared in Meta.measures or requested ad hoc.

One query per call. Sums add stored scaled integers, so Decimal and Currency totals are
exact; a sum outside the signed 64-bit range is an error, never a float. A measure's
`where=` is a finite-v1 Boolean expression compiled to parameterized SQL. The SQL avoids
dialect differences so SQLite and PostgreSQL return the same results; groups are sorted
in Python for the same reason.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
import math
import re
from urllib.parse import parse_qs

from .auth import AccessError
from .fields import Boolean, Date, DateTime, Decimal, Integer, Link, LongText, Reference, Tags
from .models import Model, ValidationError, identifier
from .query import SqlQuery

FUNCTIONS = ('count', 'count_distinct', 'sum', 'min', 'max')
MAX_GROUP_FIELDS, MAX_MEASURES, MAX_GROUPS = 4, 8, 500
MAX_STORED, MIN_STORED = 2**63 - 1, -2**63
# Sums add high and low halves of each value separately, so no running total can overflow
# and the result depends only on the final total, never on row order.
SPLIT = 2**32
NAME = re.compile(r'[a-z][a-z0-9_]{0,62}')
FIELD = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,62}')
UNAVAILABLE = 'Measure field is unavailable'
OVERFLOW = 'A sum is outside the exact range of stored numbers; narrow the filters'
# Field kinds each function accepts; see kind().
ACCEPTS = {'count_distinct': {'link', 'boolean', 'number', 'date', 'value'}, 'sum': {'number'},
           'min': {'number', 'date'}, 'max': {'number', 'date'}}
FLIPPED = {'eq': 'eq', 'ne': 'ne', 'lt': 'gt', 'lte': 'gte', 'gt': 'lt', 'gte': 'lte'}
SYMBOLS = {'lt': '<', 'lte': '<=', 'gt': '>', 'gte': '>='}


def kind(field):
    """Measure kind of a stored field, or None when it cannot be counted, summed or grouped."""
    if isinstance(field, (Tags, Reference, LongText)):
        return None
    if isinstance(field, Link):
        return 'link'
    if isinstance(field, Boolean):
        return 'boolean'
    if isinstance(field, (Integer, Decimal)):
        return 'number'
    if isinstance(field, (Date, DateTime)):
        return 'date'
    return 'value'


class Measure:
    """An aggregate over records: a function, a field, an optional where= and, for a
    per-record measure, the related model and its Link back to the declaring model."""
    function = None
    __slots__ = ('field', 'where', 'model', 'link')

    def __init__(self, field, *, where=None, model=None, link=None):
        name = type(self).__name__
        if field is not None and (not isinstance(field, str) or not FIELD.fullmatch(field)):
            raise ValueError(f'{name}() takes one field name')
        if (model is None) != (link is None):
            raise ValueError(f'{name}() over a related model needs both model= and link=')
        if link is not None and (not isinstance(link, str) or not FIELD.fullmatch(link)):
            raise ValueError(f'{name}() link= takes the name of a Link field on the related model')
        if model is not None and not isinstance(model, str) and not (isinstance(model, type) and issubclass(model, Model)):
            raise ValueError(f'{name}() model= takes a model class or registered model name')
        self.field, self.where, self.model, self.link = field, where, model, link

    def __repr__(self):
        parts = [repr(self.field)] if self.field is not None else []
        parts += [f'{key}={getattr(self, key)!r}' for key in ('where', 'model', 'link') if getattr(self, key) is not None]
        return f'{type(self).__name__}({", ".join(parts)})'


class Count(Measure):
    """Count(): records. Count(Payment, link="invoice"): related records per record."""
    function = 'count'
    __slots__ = ()

    def __init__(self, model=None, /, *, link=None, where=None):
        if model is not None and link is None:
            raise ValueError('Count() counts records; pass a related model with link=, or use CountDistinct(field)')
        super().__init__(None, where=where, model=model, link=link)


class CountDistinct(Measure):
    """Distinct non-null values of a field."""
    function = 'count_distinct'
    __slots__ = ()


class Sum(Measure):
    """Exact sum of a number field; no values give zero."""
    function = 'sum'
    __slots__ = ()


class Min(Measure):
    """Smallest non-null number or date."""
    function = 'min'
    __slots__ = ()


class Max(Measure):
    """Largest non-null number or date."""
    function = 'max'
    __slots__ = ()


class DatePart:
    """Group by part of a Date field."""
    part = None
    __slots__ = ('field',)

    def __init__(self, field):
        if not isinstance(field, str) or not FIELD.fullmatch(field):
            raise ValueError(f'{type(self).__name__}() takes one Date field name')
        self.field = field

    def __repr__(self):
        return f'{type(self).__name__}({self.field!r})'


class Day(DatePart):
    """The date itself."""
    part = 'day'
    __slots__ = ()


class Month(DatePart):
    """"YYYY-MM"."""
    part = 'month'
    __slots__ = ()


class Year(DatePart):
    """The year as an integer."""
    part = 'year'
    __slots__ = ()


CLASSES = {cls.function: cls for cls in (Count, CountDistinct, Sum, Min, Max)}
PARTS = {cls.part: cls for cls in (Day, Month, Year)}


@dataclass(frozen=True)
class Compiled:
    name: str
    function: str
    owner: type           # the model the measure is about
    target: type          # the model whose rows are aggregated (owner, or the related model)
    field: str | None
    link: str | None      # Link on target back to owner, for per-record measures
    where: dict | None    # finite-v1 IR
    reads: frozenset      # target fields the where expression reads
    sql: tuple            # aggregate columns over the alias "record"
    values: tuple         # parameters, in column order


@dataclass(frozen=True)
class Today:
    """A where= parameter decided when the query runs: today in the zone, moved by `days`, or the first
    day of its month."""
    days: int = 0
    month_start: bool = False

    def value(self, zone):
        day = current_date(zone)
        return (day.replace(day=1) if self.month_start else day + timedelta(days=self.days)).isoformat()


def current_date(zone):
    from zoneinfo import ZoneInfo
    return datetime.now(timezone.utc if zone in (None, 'UTC') else ZoneInfo(zone)).date()


@dataclass(frozen=True)
class Group:
    key: str
    field: str
    part: str | None
    sql: str


def compile_measure(registry, owner, name, measure):
    """Check and compile one measure. Raises ValueError with a message naming the problem."""
    if not isinstance(measure, Measure) or measure.function not in FUNCTIONS:
        raise ValueError('Use Count, CountDistinct, Sum, Min or Max')
    target, link = owner, None
    if measure.link is not None:
        target = registry.resolve(measure.model)
        field = target.__fields__.get(measure.link)
        if not isinstance(field, Link) or field.target is not owner:
            raise ValueError(f'{target.__name__}.{measure.link} must be a Link to {owner.__name__}')
        link = measure.link
    if measure.field is not None:
        field = target.__fields__.get(measure.field)
        if field is None:
            raise ValueError(f'{target.__name__} has no stored field {measure.field}')
        if kind(field) not in ACCEPTS[measure.function]:
            accepted = {'sum': 'a number field', 'min': 'a number or date field', 'max': 'a number or date field',
                        'count_distinct': 'a field other than tags, references and long text'}[measure.function]
            raise ValueError(f'{measure.function} needs {accepted}; {measure.field} is a {field.kind} field')
    ir, reads, predicate, values = compile_where(registry, target, measure.where)
    column = None if measure.field is None else f'"record".{identifier(measure.field)}'
    if measure.function == 'count':
        sql = ('count(*)' if predicate is None else f'count(CASE WHEN {predicate} = 1 THEN 1 END)',)
    else:
        value = column if predicate is None else f'CASE WHEN {predicate} = 1 THEN {column} END'
        if measure.function == 'sum':
            # Integer division and remainder truncate toward zero in SQLite and PostgreSQL alike.
            sql = (f'sum(({value}) / {SPLIT})', f'sum(({value}) % {SPLIT})')
        else:
            sql = ({'count_distinct': 'count(DISTINCT {})', 'min': 'min({})', 'max': 'max({})'}[measure.function].format(value),)
    return Compiled(name, measure.function, owner, target, measure.field, link, ir, reads, sql, tuple(values) * len(sql))


def compile_where(registry, model, where):
    """(finite IR, fields read, SQL giving 1, 0 or NULL for undecided, parameters)."""
    if where is None:
        return None, frozenset(), None, []
    from .calc import CalcError, Expr, _Context, compile_expression, lift
    try:
        expression = where if isinstance(where, Expr) else lift(where)
        ir, result, _ = compile_expression(expression, _Context(model, (), None, {}, registry=registry, measure=True), 'where')
    except CalcError as exc:
        raise ValueError(exc.message) from None
    if result != 'boolean':
        raise ValueError('where: use a Boolean condition such as F("status") == "issued"')
    values, reads = [], set()
    sql = predicate(model, ir, values, reads)
    return ir, frozenset(reads), sql, values


def predicate(model, node, values, reads):
    """SQL for a finite Boolean: 1 true, 0 false, NULL when finite-v1 leaves it undecided
    (an ordered comparison or Boolean field with an empty value). Only true rows count."""
    op = node['$expr']
    if op == 'literal' and node['type'] == 'boolean':
        return '1' if node['value'] else '0'
    if op == 'ref':
        column, _ = stored(model, node, reads)
        return f'(CASE WHEN {column} IS NULL THEN NULL WHEN {column} = 0 THEN 0 ELSE 1 END)'
    if op in ('and', 'or', 'not'):
        parts = [predicate(model, arg, values, reads) for arg in node['args']]
        # Arithmetic keeps NULL (undecided) whenever an operand is undecided, as finite-v1 does.
        if op == 'not':
            return f'(1 - {parts[0]})'
        return f'({parts[0]} * {parts[1]})' if op == 'and' else f'(({parts[0]} + {parts[1]} + 1) / 2)'
    if op in FLIPPED:
        return comparison(model, op, *node['args'], values, reads)
    if op == 'hop':
        raise ValueError('where: Link paths are not supported in measures; compare fields of the measured model')
    raise ValueError(f'where: {op} is not supported in measures; use comparisons of fields and values with &, | and ~')


def stored(model, node, reads):
    if node.get('scope') != 'self':
        raise ValueError('where: compare fields of the measured model')
    name = node['field']
    field = model.__fields__.get(name)
    if field is None:
        raise ValueError(f'where: {name} is not stored, so measures cannot read it')
    reads.add(name)
    return f'"record".{identifier(name)}', field


def today_value(node):
    """A Today parameter for today(), today() + or - days(n) and month_start(); None for anything else."""
    from .calc import MAX_DAY, parse
    if node['$expr'] == 'today':
        return Today()
    if node['$expr'] == 'month_start':
        return Today(month_start=True)
    if node['$expr'] in ('date_add', 'date_sub') and node['args'][0]['$expr'] == 'today' and node['args'][1]['$expr'] == 'literal' \
            and node['args'][1]['type'] == 'decimal':
        number = parse(node['args'][1]['value'])
        if number.scale == 0:
            count = number.coef if node['$expr'] == 'date_add' else -number.coef
            if abs(count) >= MAX_DAY:
                raise ValueError('where: days() moves today() past the supported dates')
            return Today(days=count)
    return None


def comparison(model, op, left, right, values, reads):
    if left['$expr'] != 'ref' and right['$expr'] == 'ref':
        left, right, op = right, left, FLIPPED[op]
    if left['$expr'] == 'literal' and right['$expr'] == 'literal':
        raise ValueError('where: compare a field with a value or another field')
    when = today_value(right)
    if left['$expr'] == 'ref' and when is not None:
        a, field = stored(model, left, reads)
        if not isinstance(field, Date):
            raise ValueError('where: compare a Date field with today()')
        values.append(when)
        if op == 'eq':
            return f'(CASE WHEN {a} IS NULL THEN 0 WHEN {a} = ? THEN 1 ELSE 0 END)'
        if op == 'ne':
            return f'(CASE WHEN {a} IS NULL THEN 1 WHEN {a} = ? THEN 0 ELSE 1 END)'
        return f'(CASE WHEN {a} IS NULL THEN NULL WHEN {a} {SYMBOLS[op]} ? THEN 1 ELSE 0 END)'
    if left['$expr'] != 'ref' or right['$expr'] not in ('ref', 'literal'):
        if 'hop' in (left['$expr'], right['$expr']):
            raise ValueError('where: Link paths are not supported in measures; compare fields of the measured model')
        raise ValueError('where: comparisons in measures take fields and values, not calculations')
    a, field = stored(model, left, reads)
    if right['$expr'] == 'ref':
        b, other = stored(model, right, reads)
        if kind(field) == 'number' and getattr(field, 'scale', 0) != getattr(other, 'scale', 0):
            raise ValueError('where: compare number fields with the same scale')
        if op in ('eq', 'ne'):
            same = f'(CASE WHEN {a} IS NULL AND {b} IS NULL THEN 1 WHEN {a} IS NULL OR {b} IS NULL THEN 0 WHEN {a} = {b} THEN 1 ELSE 0 END)'
            return same if op == 'eq' else f'(1 - {same})'
        return f'(CASE WHEN {a} IS NULL OR {b} IS NULL THEN NULL WHEN {a} {SYMBOLS[op]} {b} THEN 1 ELSE 0 END)'
    literal, value = right['type'], right['value']
    if literal == 'null':
        return f'(CASE WHEN {a} IS NULL THEN {int(op == "eq")} ELSE {int(op == "ne")} END)'
    if literal == 'boolean':
        value = int(value)
    elif literal == 'decimal':
        from .calc import parse
        number = parse(value)
        exact = Fraction(number.coef, 10 ** number.scale) * 10 ** getattr(field, 'scale', 0)
        if exact.denominator != 1:
            # No stored value equals a number with more places than the field keeps.
            if op in ('eq', 'ne'):
                return f'(CASE WHEN {a} IS NULL THEN {int(op == "ne")} ELSE {int(op == "ne")} END)'
            op, exact = ('lte', math.floor(exact)) if op in ('lt', 'lte') else ('gte', math.ceil(exact))
        value = int(exact)
        if not MIN_STORED <= value <= MAX_STORED:
            raise ValueError('where: the number is outside the range of stored values')
    values.append(value)
    if op == 'eq':
        return f'(CASE WHEN {a} IS NULL THEN 0 WHEN {a} = ? THEN 1 ELSE 0 END)'
    if op == 'ne':
        return f'(CASE WHEN {a} IS NULL THEN 1 WHEN {a} = ? THEN 0 ELSE 1 END)'
    return f'(CASE WHEN {a} IS NULL THEN NULL WHEN {a} {SYMBOLS[op]} ? THEN 1 ELSE 0 END)'


def compile_declared(registry, models):
    """{model: {name: Compiled}} for every registered model with Meta.measures."""
    result = {}
    for model in models:
        declared = getattr(getattr(model, 'Meta', None), 'measures', None)
        if declared is None:
            continue
        if not isinstance(declared, dict) or not 1 <= len(declared) <= MAX_MEASURES:
            raise ValueError(f'{model.__name__}.Meta.measures must be a dict of one to {MAX_MEASURES} measures')
        compiled = {}
        for name, measure in declared.items():
            if not isinstance(name, str) or not NAME.fullmatch(name):
                raise ValueError(f'{model.__name__}.Meta.measures: names use lowercase letters, digits and underscores')
            try:
                compiled[name] = compile_measure(registry, model, name, measure)
            except ValueError as exc:
                raise ValueError(f'{model.__name__}.Meta.measures[{name!r}]: {exc}') from None
        result[model] = compiled
    return result


def declared(registry, model):
    """Declared measures of a model, in declaration order."""
    return list(getattr(registry, 'measures', {}).get(model, {}).values())


def group_items(group_by):
    """The group entries as a tuple; a ValidationError for the wrong shape or too many entries."""
    if group_by is None:
        return ()
    if isinstance(group_by, (str, DatePart)):
        group_by = (group_by,)
    shape = 'Use a field name, Day(), Month() or Year(), or a tuple or list of them'
    if not isinstance(group_by, (tuple, list)) or any(not isinstance(item, (str, DatePart)) for item in group_by):
        raise ValidationError({'group_by': shape})
    if len(group_by) > MAX_GROUP_FIELDS:
        raise ValidationError({'group_by': f'Use at most {MAX_GROUP_FIELDS} group fields'})
    return tuple(group_by)


def groups_for(model, group_by):
    result = []
    for item in group_items(group_by):
        name = item.field if isinstance(item, DatePart) else item
        field = model.__fields__.get(name)
        if field is None:
            raise ValidationError({'group_by': f'Unknown field: {name}'})
        column = f'"record".{identifier(name)}'
        if isinstance(item, DatePart):
            if not isinstance(field, Date):
                raise ValidationError({'group_by': f'{type(item).__name__}() needs a Date field; {name} is a {field.kind} field'})
            sql = {'day': column, 'month': f'substr({column}, 1, 7)', 'year': f'substr({column}, 1, 4)'}[item.part]
            result.append(Group(f'{name}__{item.part}', name, item.part, sql))
        else:
            if kind(field) is None:
                raise ValidationError({'group_by': f'{name} is a {field.kind} field and cannot be grouped'})
            result.append(Group(name, name, None, column))
    if len({group.key for group in result}) != len(result):
        raise ValidationError({'group_by': 'Group fields must be distinct'})
    return result


def prepare(registry, model, measures, group_by):
    """Compiled measures and groups for an aggregate over `model`; ValidationError on bad input."""
    if not isinstance(measures, dict) or not 1 <= len(measures) <= MAX_MEASURES:
        raise ValidationError({'measures': f'Use a dict of one to {MAX_MEASURES} measures'})
    known = getattr(registry, 'measures', {}).get(model, {})
    compiled = []
    for name, measure in measures.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValidationError({'measures': 'Measure names use lowercase letters, digits and underscores'})
        if isinstance(measure, str):
            item = known.get(measure)
            if item is None or item.link is not None:
                raise ValidationError({name: 'Use a declared model-level measure'})
            compiled.append(replace(item, name=name))
            continue
        if isinstance(measure, Measure) and measure.link is not None:
            raise ValidationError({name: 'A related measure is per record; read it with measures(model, record_id)'})
        try:
            compiled.append(compile_measure(registry, model, name, measure))
        except ValidationError:
            raise
        except ValueError as exc:
            raise ValidationError({name: str(exc)}) from None
    groups = groups_for(model, group_by)
    if {group.key for group in groups} & set(measures):
        raise ValidationError({'measures': 'Measure names must differ from group names'})
    return compiled, groups


def combine(where, values, extra, extra_values):
    if not extra:
        return where, list(values)
    return where + (' AND ' if where else ' WHERE ') + extra.removeprefix(' WHERE '), [*values, *extra_values]


def execute(connection, registry, model, compiled, groups, where, values, zone='UTC'):
    """Run one aggregate statement and convert the results. Groups are sorted with empty keys first.
    today() in a where= is the date in `zone` when the statement runs."""
    columns = [group.sql for group in groups] + [column for item in compiled for column in item.sql]
    parameters = [value.value(zone) if isinstance(value, Today) else value for item in compiled for value in item.values]
    sql = (f'SELECT {", ".join(f"{column} AS {identifier(f"c{index}")}" for index, column in enumerate(columns))} '
           f'FROM {identifier(registry.name_for(model))} AS "record"{where}')
    parameters += values
    if groups:
        sql += ' GROUP BY ' + ', '.join(str(index + 1) for index in range(len(groups))) + ' LIMIT ?'
        parameters.append(MAX_GROUPS + 1)
    rows = connection.execute(sql, parameters).fetchall()
    if len(rows) > MAX_GROUPS:
        raise ValidationError({'group_by': f'The result has more than {MAX_GROUPS} groups; add filters or group by fewer fields'})
    result = []
    for row in rows:
        item = {}
        for index, group in enumerate(groups):
            raw = row[index]
            field = model.__fields__[group.field]
            item[group.key] = None if raw is None else int(raw) if group.part == 'year' else raw if group.part == 'month' else field.from_storage(raw)
        index = len(groups)
        for measure in compiled:
            item[measure.name] = value(measure, [row[index + offset] for offset in range(len(measure.sql))])
            index += len(measure.sql)
        result.append(item)
    result.sort(key=lambda item: tuple((item[group.key] is not None, item[group.key]) for group in groups))
    return result


def value(measure, raw):
    if measure.function in ('count', 'count_distinct'):
        return int(raw[0] or 0)
    field = measure.target.__fields__[measure.field]
    if measure.function == 'sum':
        # Exact halves (integers, or PostgreSQL numerics) recombined in Python; only the final total is checked.
        high, low = (0 if part is None else part for part in raw)
        if isinstance(high, float) or isinstance(low, float) or int(high) != high or int(low) != low:
            raise ValidationError({'measures': OVERFLOW})
        raw = [int(high) * SPLIT + int(low)]
        if not MIN_STORED <= raw[0] <= MAX_STORED:
            raise ValidationError({'measures': OVERFLOW})
    return None if raw[0] is None else field.from_storage(raw[0])


def aggregate(tx, model, measures, group_by=(), filters=None):
    """Trusted aggregate in a session: [{group keys..., measure names...}, ...]."""
    model = tx.registry.resolve(model)
    compiled, groups = prepare(tx.registry, model, measures, group_by)
    where, values = SqlQuery(tx.registry, model).where(filters)
    return execute(tx.connection, tx.registry, model, compiled, groups, where, values)


def values_for(tx, model, items, record_id=None, filters=None, scope_for=None, zone='UTC', search=('', None), children=()):
    """Declared measure values: model-level over the filtered (and searched) records, or per record over related
    rows. `search` is the list search text and the fields it looks in (None: the model's search fields)."""
    scope_for = scope_for or (lambda target: ('', ()))
    if record_id is None:
        # Filters are checked even when no measure is available.
        where, values = SqlQuery(tx.registry, model, children=children).where(filters, search[0], search_fields=search[1])
        if not items:
            return {}
        where, values = combine(where, values, *scope_for(model))
        return execute(tx.connection, tx.registry, model, items, [], where, values, zone)[0]
    if filters:
        raise ValidationError({'filters': 'Per-record measures take no filters'})
    result = {}
    for target in dict.fromkeys(item.target for item in items):
        for link in dict.fromkeys(item.link for item in items if item.target is target):
            chosen = [item for item in items if item.target is target and item.link == link]
            where, values = combine(f' WHERE "record".{identifier(link)} = ?', [record_id], *scope_for(target))
            result.update(execute(tx.connection, tx.registry, target, chosen, [], where, values, zone)[0])
    return {item.name: result[item.name] for item in items}


def measures(tx, model, record_id=None, filters=None):
    """Trusted declared measures: model-level when record_id is None, else per record."""
    model = tx.registry.resolve(model)
    items = [item for item in declared(tx.registry, model) if (item.link is None) == (record_id is None)]
    if record_id is not None:
        tx.get(model, record_id)
    return values_for(tx, model, items, record_id, filters)


# Permission-aware use: Context and HTTP.

def visible(policy, user, model, name, readable):
    """Whether a field may feed a measure: readable, and not a Link to a model this role reads under a row
    scope (counting or comparing such links would reveal linked records the role cannot see)."""
    field = model.__fields__.get(name)
    return name in readable and not (isinstance(field, Link) and policy.rule(user, field.target).scope is not None)


def allowed(policy, user, item):
    """Whether this role may read every field a measure reads: its field, its link and its where inputs.

    The link of a per-record measure only matches the one record the caller may read, so a row scope on
    that record's model does not hide anything; it needs only to be readable."""
    if not policy.allows(user, item.target, 'read'):
        return False
    readable = policy.fields(user, item.target)
    names = {*item.reads, *([item.field] if item.field is not None else [])}
    return all(visible(policy, user, item.target, name, readable) for name in names) and (item.link is None or item.link in readable)


def expression_names(node):
    """Field names an authoring expression refers to (the head of a Link path)."""
    from .calc import Expr
    from .fields import Field
    if isinstance(node, Field):
        return {node.name}
    if not isinstance(node, Expr):
        return set()
    names = set()
    if node.op == 'ref':
        field = node.meta.get('field')
        names.add(field.name if field is not None else (node.meta.get('name') or '').split('.')[0])
    for child in (*node.args, node.meta.get('where')):
        names |= expression_names(child)
    return names


def searched(business, user, model, search):
    """The list search for this role: the text and the model's search fields it may read, as the list uses."""
    from .ui.metadata import view_for
    readable = business.policy.fields(user, model)
    return search, [name for name in view_for(model)['search_fields'] if name in readable]


def scoped_aggregate(business, tx, user, model, measures, group_by=(), filters=None, search=''):
    policy, registry = business.policy, business.registry
    policy.require(user, model, 'read')
    readable = policy.fields(user, model)
    # Names are checked before anything is compiled, so unknown and unreadable fields, and unknown and
    # unreadable declared measures, give the same 403.
    for measure in (measures.values() if isinstance(measures, dict) else ()):
        if isinstance(measure, str):
            item = getattr(registry, 'measures', {}).get(model, {}).get(measure)
            if item is None or item.link is not None or not allowed(policy, user, item):
                raise AccessError(403, UNAVAILABLE)
        elif isinstance(measure, Measure) and measure.link is None:
            names = expression_names(measure.where) | ({measure.field} if measure.field is not None else set())
            if not all(visible(policy, user, model, name, readable) for name in names):
                raise AccessError(403, UNAVAILABLE)
    for item in group_items(group_by):
        # Group keys of a Link to a row-scoped model would list records this role may not see.
        if not visible(policy, user, model, item.field if isinstance(item, DatePart) else item, readable):
            raise AccessError(403, UNAVAILABLE)
    compiled, groups = prepare(registry, model, measures, group_by)
    if any(not allowed(policy, user, item) for item in compiled):
        raise AccessError(403, UNAVAILABLE)
    children = getattr(business, 'children', {}).values()
    policy.query_fields(user, model, filters, (), children=children)
    text, fields = searched(business, user, model, search)
    where, values = SqlQuery(registry, model, children=children).where(filters, text, search_fields=fields)
    where, values = combine(where, values, *policy.scope_sql(user, model))
    return execute(tx.connection, registry, model, compiled, groups, where, values, getattr(business, 'timezone', None) or 'UTC')


def available(business, user, model, per_record):
    return [item for item in declared(business.registry, model)
            if (item.link is not None) == per_record and allowed(business.policy, user, item)]


def scoped_measures(business, tx, user, model, record_id=None, filters=None, search=''):
    """Declared measures this role may read; others are left out. Row scopes apply to every model."""
    policy = business.policy
    policy.require(user, model, 'read')
    items = available(business, user, model, record_id is not None)
    if record_id is None:
        policy.query_fields(user, model, filters, (), children=getattr(business, 'children', {}).values())
    else:
        policy.record(tx, user, model, record_id)
    return values_for(tx, model, items, record_id, filters, lambda target: policy.scope_sql(user, target),
                      getattr(business, 'timezone', None) or 'UTC', searched(business, user, model, search), getattr(business, 'children', {}).values())


def schema(business, user, model):
    """Schema entries for declared measures this role may read."""
    return [{'name': item.name, 'function': item.function, 'field': item.field, 'where': item.where,
             'per_record': item.link is not None,
             'model': business.registry.name_for(item.target) if item.link is not None else None, 'link': item.link}
            for item in available(business, user, model, False) + available(business, user, model, True)]


# HTTP: GET /api/{model}/aggregate, GET /api/{model}/measures, GET /api/{model}/{id}/measures.

ENTRY = re.compile(r'([a-z][a-z0-9_]{0,62})(?::([a-z_]+)(?::([A-Za-z][A-Za-z0-9_]{0,62}))?)?')
GROUP = re.compile(r'([A-Za-z][A-Za-z0-9_]{0,62})(?::(day|month|year))?')


def parameters(scope, allowed_keys):
    raw = scope.get('query_string', b'')
    try:
        if len(raw) > 8192:
            raise ValueError()
        query = parse_qs(raw.decode('ascii'), keep_blank_values=True, max_num_fields=len(allowed_keys))
        if set(query) - allowed_keys or any(len(items) != 1 for items in query.values()):
            raise ValueError()
        query = {key: items[0] for key, items in query.items()}
        filters = json.loads(query.get('filters', '{}'))
        if not isinstance(filters, (dict, list)):
            raise ValueError()
    except (ValueError, UnicodeError, RecursionError):
        raise ValidationError({'query': 'Invalid query parameters'}) from None
    return query, filters


def parse_measures(text):
    result = {}
    for entry in text.split(',') if text else ():
        match = ENTRY.fullmatch(entry)
        if match is None or match[1] in result:
            raise ValidationError({'measures': 'Use distinct name or name:function[:field] entries separated by commas'})
        name, function, field = match.groups()
        if function is None:
            result[name] = name
        elif function not in FUNCTIONS or (function == 'count') != (field is None):
            raise ValidationError({'measures': 'Functions are count, count_distinct, sum, min and max; count takes no field, the others need one'})
        else:
            result[name] = Count() if function == 'count' else CLASSES[function](field)
    return result


def parse_groups(text):
    result = []
    for entry in text.split(',') if text else ():
        match = GROUP.fullmatch(entry)
        if match is None:
            raise ValidationError({'group_by': 'Use field or field:day, field:month or field:year entries separated by commas'})
        result.append(PARTS[match[2]](match[1]) if match[2] else match[1])
    return result


def route(business, scope, user, model, parts):
    """GET-only measure routes on a model."""
    from .database import Session
    if scope['method'] != 'GET':
        raise AccessError(405, 'Method not allowed')
    record_id = None
    if len(parts) == 5:
        if not re.fullmatch(r'[1-9][0-9]{0,18}', parts[3]) or int(parts[3]) > MAX_STORED:
            raise AccessError(404, 'Record not found')
        record_id = int(parts[3])
    business.policy.require(user, model, 'read')
    if parts[3] == 'aggregate':
        query, filters = parameters(scope, {'measures', 'group_by', 'filters', 'q'})
        requested, group_by = parse_measures(query.get('measures', '')), parse_groups(query.get('group_by', ''))
    else:
        query, filters = parameters(scope, set() if record_id else {'filters', 'q'})
    with business.database._connection() as connection:
        connection.execute('BEGIN')
        tx = Session(connection, business.registry)
        if parts[3] == 'aggregate':
            rows = scoped_aggregate(business, tx, user, model, requested, group_by, filters, query.get('q', ''))
            compiled, groups = prepare(business.registry, model, requested, group_by)
            items = [{**{group.key: encode_group(model, group, row[group.key]) for group in groups},
                      **{item.name: encode(item, row[item.name]) for item in compiled}} for row in rows]
            return 200, {'group_by': [group.key for group in groups], 'measures': list(requested), 'items': items}, []
        result = scoped_measures(business, tx, user, model, record_id, filters, query.get('q', ''))
    items = {item.name: item for item in declared(business.registry, model)}
    return 200, {'measures': {name: encode(items[name], value) for name, value in result.items()}}, []


def encode_group(model, group, value):
    if value is None or group.part in ('month', 'year'):
        return value
    return model.__fields__[group.field].serialize(value)


def encode(item, value):
    """JSON value of a measure result: counts are integers, other results use their field's format."""
    if value is None or item.function in ('count', 'count_distinct'):
        return value
    return item.target.__fields__[item.field].serialize(value)
