"""Finite calculation language (finite-v1): authoring, typed plans and exact evaluation.

Python declarations build immutable expressions. The compiler turns one root document
and its owned tables into a versioned plan. The same plan runs here (authoritative) and
in the browser (preview). Evaluation is pure: no database, clock, network or callbacks.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal as PyDecimal
import hashlib
import inspect
import json
import re

from .identity import RecordId

LANGUAGE = 'finite-v1'
PROFILE = 'finite40-hu18-v1'
LEGACY_PROFILE = 'legacy-document-v2'
PLAN_VERSION = 1
EXPRESSION_VERSION = 1
COMPILER_VERSION = 'evlet-finite-1'
# collection_rows is the most any owned table may declare (Table max_rows); each plan table carries its own.
LIMITS = {'expression_nodes': 128, 'expression_depth': 32, 'table_depth': 8, 'collection_rows': 1000,
          'document_rows': 5000, 'graph_nodes': 50000, 'graph_edges': 250000,
          'evaluation_steps': 1000000, 'decimal_text': 128}
MAX_DIGITS, MAX_SCALE, DIVISION_SCALE = 40, 36, 18
SAFE_INTEGER = 2**53 - 1
OPERATIONS = {'add', 'sub', 'mul', 'div', 'neg', 'eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'and', 'or', 'not',
              'round', 'coalesce', 'safe_divide', 'bool_to_decimal', 'when', 'days'}
ROUNDING = ('half_up', 'half_even')
MAX_HOPS = 3
MIN_DAY, MAX_DAY = 1, 3652059  # 0001-01-01 and 9999-12-31 as proleptic Gregorian ordinals


class CalcError(ValueError):
    """A finite-language failure with a stable code and closed-path or field causes."""
    def __init__(self, code, message, causes=()):
        self.code, self.message, self.causes = code, message, tuple(causes)
        super().__init__(f'{code}: {message}')


class TruthTestError(CalcError, TypeError):
    """Truth-testing an expression; also a TypeError so sorting fields fails as before."""


# Exact numbers: (coefficient, scale) with value coefficient * 10**-scale.

@dataclass(frozen=True)
class Num:
    coef: int
    scale: int

    def __str__(self):
        return text(self)


def normalize(coef, scale):
    if coef == 0:
        return Num(0, 0)
    while scale > 0 and coef % 10 == 0:
        coef //= 10
        scale -= 1
    if scale > MAX_SCALE or len(str(abs(coef))) > MAX_DIGITS:
        raise CalcError('COMPUTE_RANGE_ERROR', 'Calculation exceeds the supported range')
    return Num(coef, scale)


NUMBER = re.compile(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?')


def integer(value):
    """JSON integers as both runtimes see them: safe whole numbers, never Booleans."""
    if type(value) is float and value.is_integer():
        value = int(value)
    return value if type(value) is int and abs(value) <= SAFE_INTEGER else None


def link_id(value):
    """A Link value as a RecordId: an id from storage, its text form from JSON, or a whole number from
    older fixtures. Booleans and anything else are not ids."""
    if isinstance(value, RecordId):
        return value
    if isinstance(value, str) and value.isdigit() and len(value) < 13:
        value = int(value)
    # A plain JSON number above 2^53 is not exact in a browser, so neither runtime takes it as an id.
    value = integer(value) if not isinstance(value, str) else value
    try:
        return None if value is None else RecordId(value)
    except ValueError:
        return None


def parse(value):
    """Strict numeric input: decimal strings, safe integers or exact Python decimals."""
    if isinstance(value, Num):
        return value
    if type(value) in (int, float):
        if integer(value) is None:
            raise CalcError('COMPUTE_RANGE_ERROR' if type(value) is int or value.is_integer() else 'COMPUTE_TYPE_ERROR',
                            'Use an exact decimal string or a safe integer')
        return normalize(integer(value), 0)
    if isinstance(value, PyDecimal):
        if not value.is_finite():
            raise CalcError('COMPUTE_TYPE_ERROR', 'Use a finite decimal value')
        sign, digits, exponent = value.as_tuple()
        if exponent > MAX_DIGITS or exponent < -(MAX_SCALE + MAX_DIGITS) or len(digits) > 2 * MAX_DIGITS + MAX_SCALE:
            raise CalcError('COMPUTE_RANGE_ERROR', 'Calculation exceeds the supported range')
        coef = int(''.join(map(str, digits)) or '0') * (-1 if sign else 1)
        return normalize(coef * 10**exponent, 0) if exponent >= 0 else normalize(coef, -exponent)
    if isinstance(value, str) and len(value) <= LIMITS['decimal_text'] and NUMBER.fullmatch(value):
        whole, _, fraction = value.lstrip('-').partition('.')
        coef = int(whole + fraction) * (-1 if value.startswith('-') else 1)
        return normalize(coef, len(fraction))
    raise CalcError('COMPUTE_TYPE_ERROR', 'Use an exact decimal string')


def text(value, scale=None):
    """Canonical decimal text; with a scale, fixed to exactly that many places."""
    coef, current = value.coef, value.scale
    if scale is not None and scale > current:
        coef, current = coef * 10**(scale - current), scale
    digits = str(abs(coef)).rjust(current + 1, '0')
    result = digits[:len(digits) - current] + ('.' + digits[len(digits) - current:] if current else '')
    return ('-' if coef < 0 else '') + result


def aligned(a, b):
    scale = max(a.scale, b.scale)
    return a.coef * 10**(scale - a.scale), b.coef * 10**(scale - b.scale), scale


def compare(a, b):
    x, y, _ = aligned(a, b)
    return (x > y) - (x < y)


def add(a, b):
    x, y, scale = aligned(a, b)
    return normalize(x + y, scale)


def sub(a, b):
    x, y, scale = aligned(a, b)
    return normalize(x - y, scale)


def mul(a, b):
    return normalize(a.coef * b.coef, a.scale + b.scale)


def half_up(numerator, denominator):
    """Nearest integer to numerator/denominator, exact ties away from zero."""
    negative = (numerator < 0) != (denominator < 0)
    quotient, remainder = divmod(abs(numerator), abs(denominator))
    if 2 * remainder >= abs(denominator):
        quotient += 1
    return -quotient if negative else quotient


def half_even(numerator, denominator):
    """Nearest integer to numerator/denominator, exact ties to the even neighbour."""
    negative = (numerator < 0) != (denominator < 0)
    quotient, remainder = divmod(abs(numerator), abs(denominator))
    if 2 * remainder > abs(denominator) or 2 * remainder == abs(denominator) and quotient % 2:
        quotient += 1
    return -quotient if negative else quotient


def div(a, b):
    if b.coef == 0:
        raise CalcError('COMPUTE_DIVISION_BY_ZERO', 'Division by zero')
    power = DIVISION_SCALE + b.scale - a.scale
    numerator, denominator = a.coef * 10**max(power, 0), b.coef * 10**max(-power, 0)
    return normalize(half_up(numerator, denominator), DIVISION_SCALE)


def quantize(value, scale, rounding='half_up'):
    if value.scale <= scale:
        return value
    return normalize((half_even if rounding == 'half_even' else half_up)(value.coef, 10**(value.scale - scale)), scale)


def total(values):
    """One exact aggregate: a wide accumulator, bounded only at the final result."""
    if not values:
        return Num(0, 0)
    scale = max(value.scale for value in values)
    return normalize(sum(value.coef * 10**(scale - value.scale) for value in values), scale)


DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')


def day_number(value):
    """An ISO date (YYYY-MM-DD) as its proleptic Gregorian ordinal."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.toordinal()
    if isinstance(value, str) and DATE.fullmatch(value):
        try:
            return date.fromisoformat(value).toordinal()
        except ValueError:
            pass
    raise CalcError('COMPUTE_TYPE_ERROR', 'Use a date in YYYY-MM-DD format')


def day_text(number):
    if not MIN_DAY <= number <= MAX_DAY:
        raise CalcError('COMPUTE_RANGE_ERROR', 'The date is outside 0001-01-01 to 9999-12-31')
    return date.fromordinal(number).isoformat()


def shift(day, count, sign):
    """A date moved by a whole number of days."""
    if count.scale:
        raise CalcError('COMPUTE_TYPE_ERROR', 'days() needs a whole number')
    if abs(count.coef) > MAX_DAY:
        raise CalcError('COMPUTE_RANGE_ERROR', 'The date is outside 0001-01-01 to 9999-12-31')
    return day_text(day_number(day) + sign * count.coef)


def to_decimal(value):
    from decimal import Context
    return PyDecimal(value.coef).scaleb(-value.scale, context=Context(prec=MAX_DIGITS + MAX_SCALE + 2))


# Authoring expressions. They are immutable and only build a closed tree.

class Expr:
    __slots__ = ('op', 'args', 'meta')
    __hash__ = None

    def __init__(self, op, args=(), **meta):
        object.__setattr__(self, 'op', op)
        object.__setattr__(self, 'args', tuple(args))
        object.__setattr__(self, 'meta', meta)

    def __setattr__(self, name, value):
        raise AttributeError('Expressions are immutable')

    def __getattr__(self, name):
        raise CalcError('UNSUPPORTED_EXPRESSION', f'Expressions have no attribute {name!r}; use a named field or relay')

    def __bool__(self):
        raise TruthTestError('EXPRESSION_TRUTH_TEST', 'Calculations cannot use Python if/and/or/not or chained comparisons; use &, |, ~ and safe functions')

    def __repr__(self):
        return f'Expr({self.op!r}, {self.args!r}, {self.meta!r})'

    def __add__(self, other): return Expr('add', (self, lift(other)))
    def __radd__(self, other): return Expr('add', (lift(other), self))
    def __sub__(self, other): return Expr('sub', (self, lift(other)))
    def __rsub__(self, other): return Expr('sub', (lift(other), self))
    def __mul__(self, other): return Expr('mul', (self, lift(other)))
    def __rmul__(self, other): return Expr('mul', (lift(other), self))
    def __truediv__(self, other): return Expr('div', (self, lift(other)))
    def __rtruediv__(self, other): return Expr('div', (lift(other), self))
    def __neg__(self): return Expr('neg', (self,))
    def __eq__(self, other): return Expr('eq', (self, lift(other)))
    def __ne__(self, other): return Expr('ne', (self, lift(other)))
    def __lt__(self, other): return Expr('lt', (self, lift(other)))
    def __le__(self, other): return Expr('lte', (self, lift(other)))
    def __gt__(self, other): return Expr('gt', (self, lift(other)))
    def __ge__(self, other): return Expr('gte', (self, lift(other)))
    def __and__(self, other): return Expr('and', (self, lift(other)))
    def __rand__(self, other): return Expr('and', (lift(other), self))
    def __or__(self, other): return Expr('or', (self, lift(other)))
    def __ror__(self, other): return Expr('or', (lift(other), self))
    def __invert__(self): return Expr('not', (self,))


def lift(value):
    from .fields import Field
    if isinstance(value, Expr):
        return value
    if isinstance(value, Field):
        return Expr('ref', scope='self', field=value)
    if value is None:
        return Expr('literal', type='null', value=None)
    if type(value) is bool:
        return Expr('literal', type='boolean', value=value)
    if type(value) is int or isinstance(value, (PyDecimal, Num)):
        return Expr('literal', type='decimal', value=text(parse(value)))
    if type(value) is str:
        return Expr('literal', type='text', value=value)
    raise CalcError('UNSUPPORTED_EXPRESSION', f'Unsupported calculation operand {type(value).__name__}; use D("1.5") for decimals')


UNARY = object()


def operand_like(value):
    """Whether a value can be lifted into an expression (fields, expressions, literals)."""
    from .fields import Field
    return value is None or isinstance(value, (Expr, Field, PyDecimal, Num, str)) or type(value) in (bool, int)


def operand(op, left, right=UNARY):
    """Field descriptors build expressions through these operators."""
    return Expr(op, (lift(left),) if right is UNARY else (lift(left), lift(right)))


def D(value):
    """An exact decimal literal, e.g. D("0.05"). It never parses code."""
    if type(value) is bool or isinstance(value, float):
        raise CalcError('UNSUPPORTED_EXPRESSION', 'Use a decimal string or integer, not a float or boolean')
    return Expr('literal', type='decimal', value=text(parse(value)))


def F(name):
    """A local field by name, for forward references, or a dotted path through Link fields."""
    if not isinstance(name, str) or len(name) > 256 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*', name):
        raise CalcError('UNSUPPORTED_EXPRESSION', 'F() takes one declared field name or a link path such as "customer.credit_days"')
    return Expr('ref', scope='self', name=name)


class _Scope:
    __slots__ = ('_scope',)

    def __init__(self, scope):
        object.__setattr__(self, '_scope', scope)

    def __getattr__(self, name):
        if name.startswith('_') or name in ('constructor', 'prototype'):
            raise CalcError('UNSUPPORTED_EXPRESSION', f'{self._scope}.{name} is not a field reference')
        return Expr('ref', scope=self._scope, name=name)

    def __setattr__(self, name, value):
        raise AttributeError('Calculation scopes are read only')

    def __bool__(self):
        raise CalcError('EXPRESSION_TRUTH_TEST', 'Calculation scopes cannot be truth-tested')


parent = _Scope('parent')
row = _Scope('row')


def round(value, scale):  # noqa: A001 - the documented finite-language name
    if type(scale) is not int or not 0 <= scale <= MAX_SCALE:
        raise CalcError('UNSUPPORTED_EXPRESSION', 'round() takes a fixed integer scale from 0 to 36')
    return Expr('round', (lift(value), Expr('literal', type='integer', value=scale)))


def coalesce(value, fallback):
    return Expr('coalesce', (lift(value), lift(fallback)))


def when(condition, then, otherwise):
    """`then` when the Boolean condition is true, else `otherwise`; only the chosen branch is evaluated."""
    return Expr('when', (lift(condition), lift(then), lift(otherwise)))


def days(count):
    """A whole number of days to add to or subtract from a date."""
    return Expr('days', (lift(count),))


def today():
    """The current date, supplied by the host (the server uses the instance timezone)."""
    return Expr('today')


def month_start():
    """The first day of the current month, for a measure's where= only (measures.md)."""
    return Expr('month_start')


def safe_divide(numerator, denominator, *, default):
    return Expr('safe_divide', (lift(numerator), lift(denominator), lift(default)))


class Selection:
    """Rows of one owned table, filtered by typed predicates combined with AND."""
    def __init__(self, table, where=None):
        self.table, self.predicate = table, where

    def where(self, predicate=None, **equals):
        parts = [] if self.predicate is None else [self.predicate]
        if predicate is not None:
            if isinstance(predicate, str):
                raise CalcError('UNSUPPORTED_EXPRESSION', 'where() takes a typed predicate such as row.amount > 0, not text')
            parts.append(lift(predicate))
        for name, value in sorted(equals.items()):
            parts.append(Expr('eq', (Expr('ref', scope='row', name=name), lift(value))))
        if not parts:
            raise CalcError('UNSUPPORTED_EXPRESSION', 'where() needs a predicate')
        result = parts[0]
        for part in parts[1:]:
            result = Expr('and', (result, part))
        return Selection(self.table, result)

    def _aggregate(self, kind, field):
        if field is not None and (not isinstance(field, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', field)):
            raise CalcError('UNSUPPORTED_EXPRESSION', f'{kind}() takes one direct row field name; put formulas in a row field')
        return Expr('aggregate', kind=kind, table=self.table, field=field, where=self.predicate)

    def sum(self, field): return self._aggregate('sum', field)
    def min(self, field): return self._aggregate('min', field)
    def max(self, field): return self._aggregate('max', field)
    def count(self): return self._aggregate('count', None)


# Compilation: one plan per editable root document and its owned tables.

def field_type(field):
    from . import fields
    if isinstance(field, fields.Boolean): return 'boolean'
    if isinstance(field, fields.Link): return 'link'
    if isinstance(field, (fields.Decimal, fields.Integer)): return 'decimal'
    if isinstance(field, fields.DateTime): return 'datetime'
    if isinstance(field, fields.Date): return 'date'
    if isinstance(field, fields.Text): return 'text'
    return field.kind


def is_finite(field):
    return isinstance(field.compute, Expr)


KINDS = ('initial', 'applies_when', 'required_when', 'locked_when')
# Field kinds that finite expressions cannot read or produce.
OPAQUE = ('tags', 'multiselect', 'reference', 'time', 'richtext', 'image')


def options(field):
    """(kind, expression) finite options a field declares: compute, then initial and the rules."""
    result = [('compute', field.compute)] if is_finite(field) else []
    for kind in KINDS:
        value = getattr(field, kind, None)
        if value is not None:
            result.append((kind, value if isinstance(value, Expr) else lift(value)))
    return result


def uses_finite(field):
    """Whether a field makes its document a finite-v1 document (finite compute, initial or a rule)."""
    return bool(options(field))


def node_name(entry):
    """Document node name of a plan entry: the field for a calculation, field:kind for initial and rules."""
    return entry['field'] if entry.get('kind') is None else entry['field'] + ':' + entry['kind']


def root_language(model):
    meta = getattr(model, 'Meta', None)
    language = getattr(meta, 'calculation_language', None)
    if language not in (None, LANGUAGE):
        raise CalcError('SCHEMA_VERSION_MISMATCH', f'Unknown calculation language {language!r}')
    profile = getattr(meta, 'numeric_profile', None)
    if profile not in (None, PROFILE):
        raise CalcError('SCHEMA_VERSION_MISMATCH', f'Unknown numeric profile {profile!r}')
    return language


def address(path, field):
    return ''.join(name + '[*].' for name in path) + field


@dataclass
class _Context:
    model: object
    path: tuple
    parent: object
    children: dict
    row: object = None
    table: str = None
    registry: object = None
    measure: bool = False  # a measure's where=, which alone may read month_start()


def compile_expression(expr, context, target):
    """Typed IR plus template reads for one computed field."""
    nodes = [0]
    reads = []

    def fail(code, message):
        raise CalcError(code, f'{address(context.path, target)}: {message}')

    def resolve(node, scope):
        name = node.meta.get('name')
        field = node.meta.get('field')
        owner = {'self': context.model, 'parent': context.parent, 'row': context.row}[scope]
        if field is not None:
            name = field.name
            if not any(candidate is field for candidate in owner.__declared_fields__.values()):
                fail('UNSUPPORTED_EXPRESSION', f'{name} belongs to another model; use parent.{name} or a relay field')
        if name in owner.__tables__:
            fail('UNSUPPORTED_EXPRESSION', f'{name} is a table; use {name}.sum(...) or another aggregate')
        if name not in owner.__declared_fields__ or name in getattr(owner, '__internal_fields__', ()):
            code = 'PARENT_FIELD_INCOMPATIBLE' if scope == 'parent' else 'UNSUPPORTED_EXPRESSION'
            fail(code, f'{scope}.{name} is not a declared field' if scope != 'self' else f'unknown field {name}')
        kind = field_type(owner.__declared_fields__[name])
        if kind in OPAQUE:
            fail('UNSUPPORTED_EXPRESSION', f'{name} is a {kind} field; calculations cannot read it')
        return name, kind

    def link_path(path):
        """A dotted path through Link fields; every step after the first reads a host-supplied lookup."""
        from .fields import Link
        parts = path.split('.')
        if len(parts) - 1 > MAX_HOPS:
            fail('CALCULATION_LIMIT_EXCEEDED', f'{path} follows more than {MAX_HOPS} links')
        first, kind = resolve(Expr('ref', scope='self', name=parts[0]), 'self')
        reads.append(('self', None, first))
        link, steps = context.model.__declared_fields__[first], []
        for index, name in enumerate(parts[1:], 1):
            if kind != 'link':
                fail('COMPUTE_TYPE_ERROR', f'{path}: {parts[index - 1]} is not a Link field')
            target = link.target
            field = target.__declared_fields__.get(name)
            if field is None or name in target.__tables__ or name in getattr(target, '__internal_fields__', ()):
                fail('UNSUPPORTED_EXPRESSION', f'{path}: {target.__name__} has no field {name}')
            if not field.stored or field.query is not None:
                fail('UNSUPPORTED_EXPRESSION', f'{path}: {target.__name__}.{name} is not stored, so it cannot be looked up')
            kind = field_type(field)
            if kind == 'datetime':
                fail('COMPUTE_TYPE_ERROR', f'{path}: {name} is a DateTime; Link paths support Date fields only')
            if kind not in HOP_TYPES:
                fail('UNSUPPORTED_EXPRESSION', f'{path}: {name} is a {kind} field; calculations cannot read it')
            model_name = context.registry.name_for(target)
            steps.append([model_name, name, 'duration' if field.kind == 'duration' else kind])
            reads.append(('lookup', model_name, name))
            link = field if isinstance(field, Link) else None
        return {'$expr': 'hop', 'field': first, 'steps': steps}, kind, False

    def visit(node, depth, scope):
        nodes[0] += 1
        if nodes[0] > LIMITS['expression_nodes'] or depth > LIMITS['expression_depth']:
            raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'{address(context.path, target)}: expression is too large')
        if not isinstance(node, Expr):
            node = lift(node)
        op = node.op
        if op == 'literal':
            kind, value = node.meta['type'], node.meta['value']
            return {'$expr': 'literal', 'type': kind, 'value': value}, 'decimal' if kind == 'integer' else kind, False
        if op == 'today':
            reads.append(('today', None, None))
            return {'$expr': 'today'}, 'date', False
        if op == 'month_start':
            if not context.measure:
                fail('UNSUPPORTED_EXPRESSION', 'month_start() belongs only in a measure where=; use today() and days() here')
            return {'$expr': 'month_start'}, 'date', False
        if op == 'ref' and node.meta['scope'] == 'self' and '.' in (node.meta.get('name') or ''):
            return link_path(node.meta['name'])
        if op == 'ref':
            ref_scope = node.meta['scope']
            if ref_scope == 'parent' and context.parent is None:
                fail('PARENT_CONTEXT_REQUIRED', 'parent is only available in owned table rows')
            if ref_scope == 'row' and scope != 'where':
                fail('UNSUPPORTED_EXPRESSION', 'row is only available inside a table filter')
            name, kind = resolve(node, ref_scope)
            if ref_scope == 'row':
                reads.append(('member', context.table, name))
            else:
                reads.append((ref_scope, None, name))
            return {'$expr': 'ref', 'scope': ref_scope, 'field': name}, kind, ref_scope == 'parent'
        if op == 'aggregate':
            if scope == 'where':
                fail('UNSUPPORTED_EXPRESSION', 'aggregates cannot be nested in a filter; use a named total field')
            table = node.meta['table']
            name = next((key for key, value in context.model.__tables__.items() if value is table), None)
            if name is None:
                fail('UNSUPPORTED_EXPRESSION', 'aggregate a table declared on this model')
            child = context.children[name]
            kind, member_field = node.meta['kind'], node.meta['field']
            reads.append(('membership', name, None))
            ir = {'$expr': 'aggregate', 'kind': kind, 'table': name, 'field': member_field, 'where': None}
            if member_field is not None:
                if member_field in child.__tables__ or member_field not in child.__declared_fields__:
                    fail('UNSUPPORTED_EXPRESSION', f'{name} rows have no scalar field {member_field}')
                if field_type(child.__declared_fields__[member_field]) != 'decimal':
                    fail('COMPUTE_TYPE_ERROR', f'{kind}() requires a numeric row field')
                reads.append(('member', name, member_field))
            if node.meta['where'] is not None:
                saved = (context.row, context.table)
                context.row, context.table = child, name
                predicate, predicate_type, _ = visit(node.meta['where'], depth + 1, 'where')
                context.row, context.table = saved
                if predicate_type != 'boolean':
                    fail('COMPUTE_TYPE_ERROR', 'a table filter must be a Boolean predicate')
                ir['where'] = predicate
            return ir, 'decimal', False
        if op not in OPERATIONS or op == 'bool_to_decimal':
            fail('UNSUPPORTED_EXPRESSION', f'unknown operation {op!r}')
        args = [visit(arg, depth + 1, scope) for arg in node.args]
        kinds = [kind for _, kind, _ in args]
        via_parent = any(flag for _, _, flag in args)
        error = 'PARENT_FIELD_INCOMPATIBLE' if via_parent else 'COMPUTE_TYPE_ERROR'

        def numeric(item):
            ir, kind, _ = item
            if kind == 'boolean':
                return {'$expr': 'bool_to_decimal', 'args': [ir]}
            if kind != 'decimal':
                fail(error, f'{op} requires numeric or Boolean operands, not {kind}')
            return ir
        if 'days' in kinds and op not in ('add', 'sub'):
            fail(error, 'days() can only be added to or subtracted from a date')
        if op == 'days':
            if kinds[0] != 'decimal':
                fail(error, f'days() needs a number, not {kinds[0]}')
            return args[0][0], 'days', via_parent
        if op in ('add', 'sub') and ('date' in kinds or 'days' in kinds):
            if kinds == ['date', 'days']:
                return {'$expr': 'date_' + op, 'args': [args[0][0], args[1][0]]}, 'date', via_parent
            if op == 'add' and kinds == ['days', 'date']:
                return {'$expr': 'date_add', 'args': [args[1][0], args[0][0]]}, 'date', via_parent
            if op == 'sub' and kinds == ['date', 'date']:
                return {'$expr': 'date_diff', 'args': [args[0][0], args[1][0]]}, 'decimal', via_parent
            fail(error, f'cannot {op} {kinds[0]} and {kinds[1]}; use date + days(n), date - days(n) or date - date')
        if op in ('add', 'sub', 'mul', 'div', 'neg', 'safe_divide'):
            return {'$expr': op, 'args': [numeric(item) for item in args]}, 'decimal', via_parent
        if op == 'round':
            return {'$expr': 'round', 'args': [numeric(args[0]), args[1][0]]}, 'decimal', via_parent
        if op in ('eq', 'ne'):
            if 'null' not in kinds and kinds[0] != kinds[1]:
                fail(error, f'cannot compare {kinds[0]} with {kinds[1]}')
            return {'$expr': op, 'args': [item[0] for item in args]}, 'boolean', via_parent
        if op in ('lt', 'lte', 'gt', 'gte'):
            if kinds[0] != kinds[1] or kinds[0] not in ('decimal', 'date'):
                fail(error, 'ordered comparisons require two numbers or two dates')
            return {'$expr': op, 'args': [item[0] for item in args]}, 'boolean', via_parent
        if op in ('and', 'or', 'not'):
            if any(kind != 'boolean' for kind in kinds):
                fail(error, f'{op} requires Boolean predicates')
            return {'$expr': op, 'args': [item[0] for item in args]}, 'boolean', via_parent
        if op == 'coalesce':
            if kinds[0] != 'null' and kinds[1] != 'null' and kinds[0] != kinds[1]:
                fail(error, 'coalesce() operands must share a type')
            return {'$expr': op, 'args': [item[0] for item in args]}, kinds[0] if kinds[0] != 'null' else kinds[1], via_parent
        if op == 'when':
            if kinds[0] != 'boolean':
                fail(error, 'when() needs a Boolean condition')
            if kinds[1] != 'null' and kinds[2] != 'null' and kinds[1] != kinds[2]:
                fail(error, f'when() branches must share a type, not {kinds[1]} and {kinds[2]}')
            return {'$expr': op, 'args': [item[0] for item in args]}, kinds[1] if kinds[1] != 'null' else kinds[2], via_parent
        fail('UNSUPPORTED_EXPRESSION', f'unknown operation {op!r}')

    ir, kind, _ = visit(expr, 0, 'self')
    try:
        # Boolean arithmetic adds conversion nodes, so the emitted IR is what must fit the limits.
        check_node(ir, 0, [0], measure=context.measure)
    except CalcError:
        raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'{address(context.path, target)}: expression is too large') from None
    return ir, kind, reads


def compile_plan(registry, root, *, force=False):
    """Compile the finite plan for a root model, or return None for legacy documents.

    `force` compiles a plan for a legacy root too, so move guards and action availability can be
    evaluated over its saved values; such a plan does not make the document finite-v1.
    """
    from .fields import Decimal as DecimalField, Integer, MISSING
    language = root_language(root)
    tables = {}
    owned = {(child.parent, child.name): child.model for child in registry.children}

    def walk(model, path, owners, parent_model):
        if len(path) > LIMITS['table_depth']:
            raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'{root.__name__}: owned tables nest deeper than {LIMITS["table_depth"]}')
        children = {name: owned[model, name] for name in model.__tables__}
        tables[path] = (model, parent_model, children)
        for name, child in children.items():
            walk(child, path + (name,), owners, model)
    walk(root, (), (), None)
    finite = any(uses_finite(field) for model, _, _ in tables.values() for field in model.__declared_fields__.values())
    if not finite and language is None and not force:
        return None
    # A plan forced for guards over a legacy root reads legacy calculated and queried fields as saved values.
    legacy = force and not finite and language is None
    fields, schema = [], {}
    for path, (model, parent_model, children) in tables.items():
        if not legacy and any(field.compute is not None and not is_finite(field) for field in model.__declared_fields__.values()):
            raise CalcError('UNSUPPORTED_EXPRESSION', f'{root.__name__}: finite-v1 documents cannot also use legacy string computes ({address(path, "")})')
        if not legacy and (model.__sources__ or any(field.query is not None for field in model.__declared_fields__.values())):
            raise CalcError('UNSUPPORTED_EXPRESSION', f'{root.__name__}: finite-v1 documents do not support Query sources yet')
        entry = {}
        for name, field in model.__declared_fields__.items():
            if name in getattr(model, '__internal_fields__', ()):
                continue
            entry[name] = {'type': field_type(field), 'kind': field.kind, 'stored': field.stored, 'computed': is_finite(field),
                           'required': field.required, 'scale': getattr(field, 'scale', None) if isinstance(field, DecimalField) else (0 if isinstance(field, Integer) else None),
                           'minimum': None if getattr(field, 'minimum', None) is None else text(parse(field.minimum)),
                           'maximum': None if getattr(field, 'maximum', None) is None else text(parse(field.maximum)),
                           'default': creation_default(field)}
        schema[address(path, '').rstrip('.') or '$root'] = {'path': list(path), 'model': registry.name_for(model), 'fields': entry,
                                                               'tables': sorted(children)}
        if path:
            declared = parent_model.__tables__[path[-1]]
            schema[address(path, '').rstrip('.')].update(min_rows=declared.min_rows, max_rows=declared.max_rows)
        for name, field in model.__declared_fields__.items():
            for option, source in options(field):
                if option == 'compute' and (field.default is not MISSING or field.fetch is not None or field.query is not None):
                    raise CalcError('UNSUPPORTED_EXPRESSION', f'{address(path, name)}: a computed field has one writer; remove its default/fetch/query')
                if name in getattr(model, '__internal_fields__', ()):
                    raise CalcError('UNSUPPORTED_EXPRESSION', f'{address(path, name)}: framework fields take no expressions')
                target = field_type(field)
                context = _Context(model, path, parent_model, children, registry=registry)
                label = name if option == 'compute' else name + ':' + option
                ir, kind, reads = compile_expression(source, context, label)
                item = {'target': address(path, label), 'path': list(path), 'field': name}
                if option in ('compute', 'initial'):
                    if target in OPAQUE:
                        raise CalcError('UNSUPPORTED_EXPRESSION', f'{address(path, label)}: a {target} field cannot be calculated')
                    if target != kind:
                        raise CalcError('COMPUTE_TYPE_ERROR', f'{address(path, label)}: expression type {kind} does not match {target}')
                elif kind != 'boolean':
                    raise CalcError('COMPUTE_TYPE_ERROR', f'{address(path, label)}: {option} must be a Boolean expression, not {kind}')
                if option != 'compute':
                    item['kind'] = option
                item.update({'result_type': kind, 'target_type': field.kind,
                             'scale': entry[name]['scale'] if option in ('compute', 'initial') else None, 'stored': field.stored,
                             'rounding': getattr(field, 'rounding', None) if kind == 'decimal' and option in ('compute', 'initial') else None,
                             'expression': ir, 'reads': [list(read) for read in reads]})
                if field.kind == 'currency' and option in ('compute', 'initial'):
                    money = money_source(field, context, label)
                    if money is not None:
                        item['currency'] = money
                        item['reads'] += [read for read in money.get('reads', ()) if read not in item['reads']]
                fields.append(item)
        lookups = {}
        for item in fields:
            for scope, model_name, name in item['reads']:
                if scope == 'lookup' and item['path'] == list(path):
                    lookups.setdefault(model_name, set()).add(name)
        if lookups:
            schema[address(path, '').rstrip('.') or '$root']['lookups'] = [{'model': name, 'fields': sorted(lookups[name])} for name in sorted(lookups)]
    order = template_order(fields)
    plan = {'plan_version': PLAN_VERSION, 'language': LANGUAGE, 'numeric_profile': PROFILE,
            'expression_version': EXPRESSION_VERSION, 'compiler_version': COMPILER_VERSION,
            'root_resource': getattr(root, '__resource_id__', registry.name_for(root)), 'limits': LIMITS,
            'tables': schema, 'fields': [fields[index] for index in order]}
    plan['model_schema_hash'] = fingerprint(plan)
    check_plan(plan)
    return plan


def money_source(field, context, label):
    """Where a calculated Currency field finds the currency it rounds to: {'code'}, {'setting'} (a host input), or
    {'expression', 'reads'} reading a field of the row or of a linked record. The reads join the item's reads, so a
    change of currency recalculates the amount; formula_hash leaves them out."""
    source = field.currency_source()
    if source is None:
        return None
    if 'code' in source:
        return {'code': source['code']}
    if 'setting' in source:
        return {'setting': source['setting']['app'] + '.' + source['setting']['key']}
    path = source['link'] + '.' + source['field'] if 'link' in source else source['field']
    ir, _, reads = compile_expression(F(path), context, label)
    return {'expression': ir, 'reads': [list(read) for read in reads]}


def wire_scale(field):
    """The places a plan entry's value travels with: a Currency amount keeps at least 2 (as Currency.serialize)."""
    return 2 if field.get('target_type') == 'currency' else field['scale']


def creation_default(field):
    """Defaults the browser can apply on creation; callables stay server-materialized."""
    from .declarations import Ref
    from .fields import MISSING
    if field.default is MISSING or field.compute is not None:
        return None
    if isinstance(field.default, Ref):
        return {'ref': field.default.path}
    if callable(field.default):
        return {'server': True}
    value = field.default if field.default is None else field.validate(field.default)
    numeric = isinstance(value, (PyDecimal, int)) and type(value) is not bool
    return {'value': text(parse(value)) if numeric else field.serialize(value)}


def template_reads(field):
    """Template node addresses one computed field reads."""
    path = tuple(field['path'])
    result = set()
    for scope, table, name in field['reads']:
        if scope == 'self': result.add(address(path, name))
        elif scope == 'parent': result.add(address(path[:-1], name))
        elif scope == 'member': result.add(address(path + (table,), name))
    return result


def template_order(fields):
    """Deterministic topological order; any value cycle fails with a closed path.

    A field's value is written by its calculation, or by its initial and then its applies_when
    (which may clear it). Readers follow the last writer. An initial waits for its own field's
    lock, so a locked field is not overwritten.
    """
    index = {field['target']: position for position, field in enumerate(fields)}
    writers, own = {}, {}
    for kind in (None, 'initial', 'applies_when'):
        for field in fields:
            if field.get('kind') == kind:
                writers[address(tuple(field['path']), field['field'])] = field['target']
    for field in fields:
        own.setdefault(address(tuple(field['path']), field['field']), {})[field.get('kind')] = field['target']
    edges = {}
    for field in fields:
        reads = {writers[read] for read in template_reads(field) if read in writers}
        mine = own[address(tuple(field['path']), field['field'])]
        if field.get('kind') == 'applies_when' and 'initial' in mine:
            reads.add(mine['initial'])
        if field.get('kind') == 'initial' and 'locked_when' in mine:
            reads.add(mine['locked_when'])
        edges[field['target']] = sorted(reads)
    cycle = find_cycle(edges)
    if cycle:
        raise CalcError('MODEL_DEPENDENCY_CYCLE', ' -> '.join(cycle), cycle)
    order, done = [], set()

    def visit(key):
        if key in done: return
        done.add(key)
        for dependency in edges[key]:
            visit(dependency)
        order.append(index[key])
    for field in fields:
        visit(field['target'])
    return order


def find_cycle(edges):
    """Tarjan strongly connected components; returns one closed path or None."""
    index, low, stack, on_stack, counter = {}, {}, [], set(), [0]
    components = []

    def connect(node):
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for target in edges.get(node, ()):
            if target not in index:
                connect(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.discard(item)
                component.append(item)
                if item == node: break
            components.append(component)
    for node in sorted(edges):
        if node not in index:
            connect(node)
    for component in components:
        members = set(component)
        start = min(component)
        if len(component) == 1 and start not in edges.get(start, ()):
            continue
        path, seen = [start], {start}

        def search(node):
            for target in sorted(edges.get(node, ())):
                if target == start:
                    return True
                if target in members and target not in seen:
                    seen.add(target)
                    path.append(target)
                    if search(target): return True
                    path.pop()
            return False
        search(start)
        return path + [start]
    return None


def fingerprint(plan):
    body = {key: value for key, value in plan.items() if key != 'model_schema_hash'}
    return 'sha256:' + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def default_rounding(kind):
    return 'half_even' if kind == 'decimal' else 'half_up'


def formula_hash(plan):
    """Hash of only what decides calculated results: the formulas and the fields they read or write.

    Saved documents record this hash, so unrelated model edits (a new text field, a changed
    default or label) do not force every saved document to be recalculated. Initial suggestions
    and required or locked rules never change a saved result, so they are left out; applies_when
    can clear a value calculations read, so it counts.
    """
    # Currency fields hash as they did before amounts were stored at 6 places and rounded to their own currency:
    # the currency table is site data, not a formula, so saved documents keep their recorded hash.
    deciding = [legacy_money(field) for field in plan['fields'] if field.get('kind') in (None, 'applies_when')]
    used = {(tuple(field['path']), field['field']) for field in deciding}
    for field in deciding:
        for scope, table, name in field['reads']:
            if scope == 'self':
                used.add((tuple(field['path']), name))
            elif scope == 'parent':
                used.add((tuple(field['path'][:-1]), name))
            elif scope == 'member':
                used.add((tuple(field['path']) + (table,), name))
    keys = ('type', 'kind', 'stored', 'computed', 'scale', 'minimum', 'maximum')
    fields = {}
    for table in plan['tables'].values():
        for name, meta in table['fields'].items():
            if (tuple(table['path']), name) in used:
                fields[address(tuple(table['path']), name)] = {key: 2 if key == 'scale' and meta['kind'] == 'currency' else meta[key] for key in keys}
    body = {key: plan[key] for key in ('plan_version', 'language', 'numeric_profile', 'expression_version', 'compiler_version')}
    # A field's rounding counts only when it differs from its type default, so plans that predate it keep their hash.
    formulas = [{key: value for key, value in field.items() if key != 'rounding' or value not in (None, default_rounding(field['target_type']))}
                for field in deciding]
    body.update(formulas=formulas, fields=fields)
    return 'sha256:' + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def legacy_money(field):
    if field.get('target_type') != 'currency':
        return field
    extra = field.get('currency', {}).get('reads', ())
    original = [read for read in field['reads'] if read not in extra] if extra else field['reads']
    return {**{key: value for key, value in field.items() if key != 'currency'}, 'reads': original,
            'scale': 2 if field.get('scale') is not None else None}


# Evaluation: pure, exact and state-aware.

@dataclass(frozen=True)
class Result:
    state: str
    value: object = None
    code: str = None
    causes: tuple = ()

    def public(self):
        if self.state == 'ready':
            return {'state': 'ready', 'value': wire(self.value)}
        return {'state': self.state, 'code': self.code, 'causes': list(self.causes)}


def wire(value, scale=None):
    if isinstance(value, Num):
        return text(value, scale)
    return value


def ready(value): return Result('ready', value)
def pending(code, causes): return Result('pending', None, code, tuple(sorted(set(causes))))
def error(code, causes): return Result('error', None, code, tuple(sorted(set(causes))))


def blocked(results):
    """Strict operations: errors first, then pending, with deterministic causes."""
    errors = [result for result in results if result.state == 'error']
    if errors:
        return error(min(result.code for result in errors), [cause for result in errors for cause in result.causes])
    waiting = [result for result in results if result.state == 'pending']
    if waiting:
        return pending(min(result.code for result in waiting), [cause for result in waiting for cause in result.causes])
    return None


class Budget:
    def __init__(self, limit=LIMITS['evaluation_steps']):
        self.limit, self.steps = limit, 0

    def charge(self, amount=1):
        self.steps += amount
        if self.steps > self.limit:
            raise CalcError('CALCULATION_LIMIT_EXCEEDED', 'The calculation exceeded its evaluation budget')


def evaluate(node, scope, budget):
    """Evaluate one IR node. `scope` resolves refs and table members for this row."""
    budget.charge()
    op = node['$expr']
    if op == 'literal':
        kind, value = node['type'], node['value']
        return ready(parse(value) if kind == 'decimal' else value)
    if op == 'ref':
        return scope.read(node['scope'], node['field'])
    if op == 'aggregate':
        return aggregate(node, scope, budget)
    if op == 'today':
        return scope.document.today_result()
    if op == 'hop':
        return hop(node, scope)
    if op == 'when':
        condition = evaluate(node['args'][0], scope, budget)
        if condition.state != 'ready':
            return condition
        if condition.value is None:
            return pending('COMPUTE_INPUT_MISSING', scope.causes())
        return evaluate(node['args'][1] if condition.value else node['args'][2], scope, budget)
    if op == 'coalesce':
        first = evaluate(node['args'][0], scope, budget)
        return evaluate(node['args'][1], scope, budget) if first.state == 'ready' and first.value is None else first
    if op in ('and', 'or'):
        results = [evaluate(arg, scope, budget) for arg in node['args']]
        stop = blocked(results)
        if stop: return stop
        if any(result.value is None for result in results):
            return pending('COMPUTE_INPUT_MISSING', scope.causes())
        values = [result.value for result in results]
        return ready(all(values) if op == 'and' else any(values))
    if op == 'safe_divide':
        numerator, denominator = (evaluate(arg, scope, budget) for arg in node['args'][:2])
        stop = blocked([numerator, denominator]) or missing([numerator, denominator], scope)
        if stop: return stop
        if denominator.value.coef == 0:
            return evaluate(node['args'][2], scope, budget)
        return guarded(lambda: div(numerator.value, denominator.value), scope)
    results = [evaluate(arg, scope, budget) for arg in node['args']]
    stop = blocked(results)
    if stop: return stop
    values = [result.value for result in results]
    if op in ('eq', 'ne'):
        a, b = values
        same = a is None and b is None if a is None or b is None else (compare(a, b) == 0 if isinstance(a, Num) else a == b)
        return ready(same if op == 'eq' else not same)
    stop = missing(results, scope)
    if stop: return stop
    if op == 'not': return ready(not values[0])
    if op == 'bool_to_decimal': return ready(Num(1 if values[0] else 0, 0))
    if op in ('lt', 'lte', 'gt', 'gte'):
        a, b = values
        order = compare(a, b) if isinstance(a, Num) else (a > b) - (a < b)
        return ready({'lt': order < 0, 'lte': order <= 0, 'gt': order > 0, 'gte': order >= 0}[op])
    if op == 'neg': return ready(Num(-values[0].coef, values[0].scale))
    if op == 'round': return guarded(lambda: quantize(values[0], values[1]), scope)
    if op == 'date_add': return guarded(lambda: shift(values[0], values[1], 1), scope)
    if op == 'date_sub': return guarded(lambda: shift(values[0], values[1], -1), scope)
    if op == 'date_diff': return guarded(lambda: normalize(day_number(values[0]) - day_number(values[1]), 0), scope)
    return guarded(lambda: {'add': add, 'sub': sub, 'mul': mul, 'div': div}[op](*values), scope)


def hop(node, scope):
    """Follow a Link path through host-supplied lookups. A null link is a ready null at that step."""
    current = scope.read('self', node['field'])
    lookups = scope.document.lookups
    for model, name, kind in node['steps']:
        if current.state != 'ready' or current.value is None:
            return current
        record_id = link_id(current.value)
        if record_id is None:
            return error('COMPUTE_TYPE_ERROR', scope.causes())
        values = lookups.get(model, {}).get(record_id)
        if values is None or name not in values:
            scope.misses.add((model, record_id))
            return pending('COMPUTE_INPUT_MISSING', [f'@{model}.{record_id}.{name}'])
        current = source_value({'type': kind}, values[name], f'@{model}.{record_id}.{name}')
    return current


def missing(results, scope):
    if any(result.value is None for result in results):
        return pending('COMPUTE_INPUT_MISSING', scope.causes())
    return None


def guarded(operation, scope):
    try:
        return ready(operation())
    except CalcError as exc:
        return error(exc.code, scope.causes())


def aggregate(node, scope, budget):
    kind, field, condition = node['kind'], node['field'], node['where']
    selected, problems = [], []
    for member in scope.members(node['table']):
        budget.charge()
        if condition is not None:
            keep = evaluate(condition, member, budget)
            if keep.state != 'ready':
                problems.append(keep)
                continue
            if keep.value is None:
                problems.append(pending('COMPUTE_INPUT_MISSING', member.causes()))
                continue
            if not keep.value:
                continue
        if field is None:
            selected.append(None)
            continue
        value = member.read('row', field)
        if value.state == 'ready' and value.value is None:
            value = pending('COMPUTE_INPUT_MISSING', [member.address(field)])
        if value.state != 'ready':
            problems.append(value)
            continue
        selected.append(value.value)
    stop = blocked(problems)
    if stop: return stop
    if kind == 'count': return ready(Num(len(selected), 0))
    if kind == 'sum': return guarded(lambda: total(selected), scope)
    if not selected: return ready(None)
    best = selected[0]
    for value in selected[1:]:
        if (compare(value, best) < 0) == (kind == 'min'):
            best = value
    return ready(best)


def convert(result, field, at, meta=None, places=None):
    """Target assignment: quantize to the field's scale (a Currency amount: its currency's `places`) with its
    rounding; the declared bounds hold."""
    if result.state != 'ready' or result.value is None or field['result_type'] != 'decimal':
        return result
    value, scale = result.value, field['scale']
    target = scale if places is None else places
    if target is not None and value.scale > target:
        value = quantize(value, target, field.get('rounding') or 'half_up')
        result = ready(value)
    if field['target_type'] == 'integer' and abs(value.coef) > SAFE_INTEGER:
        return error('COMPUTE_RANGE_ERROR', [at])
    if scale is not None and abs(value.coef * 10**(scale - value.scale)) > 2**63 - 1:
        return error('COMPUTE_RANGE_ERROR', [at])
    if meta is not None and (meta.get('minimum') is not None and compare(value, parse(meta['minimum'])) < 0
                             or meta.get('maximum') is not None and compare(value, parse(meta['maximum'])) > 0):
        return error('COMPUTE_RANGE_ERROR', [at])
    return result


def source_value(meta, raw, at):
    """Interpret one submitted source value for evaluation."""
    if isinstance(raw, dict) and raw.get('$state') == 'pending':
        return pending('COMPUTE_INPUT_MISSING', [at])
    if raw is None or raw == '' and meta['type'] != 'text':
        return ready(None)
    try:
        if meta['type'] == 'duration' or meta.get('kind') == 'duration':
            return ready(duration(raw))
        if meta['type'] == 'decimal':
            return ready(parse(raw))
        if meta['type'] == 'date':
            return ready(day_text(day_number(raw)))
        if meta['type'] == 'boolean' and type(raw) is not bool:
            raise CalcError('COMPUTE_TYPE_ERROR', 'Expected a Boolean')
        return ready(raw)
    except CalcError as exc:
        return error(exc.code, [at])


def duration(raw):
    """Whole seconds from an ISO 8601 duration (PT1H30M) or a whole number of seconds."""
    from .fields import duration_seconds
    if isinstance(raw, str) and raw.startswith('P'):
        seconds = duration_seconds(raw)
        if seconds is None:
            raise CalcError('COMPUTE_TYPE_ERROR', 'Use an ISO 8601 duration such as PT1H30M')
        return normalize(seconds, 0)
    value = parse(raw)
    if value.scale:
        raise CalcError('COMPUTE_TYPE_ERROR', 'A duration is a whole number of seconds')
    return value


def check_async(value):
    if inspect.isawaitable(value):
        if hasattr(value, 'close'): value.close()
        raise CalcError('ASYNC_BATCH_UNSUPPORTED', 'A calculation batch cannot await; apply network results as a later batch')


# Document sessions: one bound graph per root, synchronous batches, one publication.

MISSING_VALUE = object()


def same_value(values, field, value):
    """Whether setting a source leaves it unchanged; objects and lists always count as a change."""
    return field in values and not isinstance(value, (dict, list)) and type(values[field]) is type(value) and values[field] == value
RULES = ('applies_when', 'required_when', 'locked_when')

class Row:
    __slots__ = ('handle', 'path', 'values', 'parent', 'children', 'id', 'key', 'label', 'order', 'created')

    def __init__(self, handle, path, parent, record_id, key):
        self.handle, self.path, self.parent = handle, path, parent
        self.values, self.children, self.id, self.key = {}, {}, record_id, key
        self.label, self.order, self.created = '', 0, False


class Scope:
    """Lexical scopes for one evaluation: self (declaring row), parent, and a filter row."""
    __slots__ = ('document', 'row', 'member', 'at', 'misses')

    def __init__(self, document, row, member=None, at='', misses=None):
        self.document, self.row, self.member, self.at = document, row, member, at
        self.misses = set() if misses is None else misses

    def read(self, scope, field):
        target = self.member if scope == 'row' else self.row.parent if scope == 'parent' else self.row
        if target is None:
            return pending('PARENT_CONTEXT_REQUIRED', [self.at])
        return self.document.result(target, field)

    def members(self, table):
        return [Scope(self.document, self.row, member, self.at, self.misses) for member in self.row.children.get(table, ())]

    def address(self, field):
        return (self.member or self.row).label + field

    def causes(self):
        return [self.at]


class Document:
    """A browser-equivalent session over one plan. The server uses it for full settlement."""
    def __init__(self, plan, data=None, *, budget=None, checked=False, currencies=None):
        # The server passes checked=True for plans its own registry compiled and checked at startup.
        if not checked:
            check_plan(plan)
        # Host input for Currency targets: {'decimals': {code: places}, 'settings': {'app.key': code}}. Codes not in
        # decimals have 2. The server leaves it out and reads the site in use (evlet.currencies).
        self.currencies = currencies
        self.plan, self.hash = plan, plan['model_schema_hash']
        self.schema = {tuple(entry['path']): entry for entry in plan['tables'].values()}
        self.templates, self.entries = {}, {}
        for position, field in enumerate(plan['fields']):
            self.templates.setdefault(tuple(field['path']), []).append((position, field))
            self.entries[tuple(field['path']), node_name(field)] = field
        self.budget_limit = plan['limits']['evaluation_steps'] if budget is None else budget
        self.rows, self.results, self.counts = {}, {}, {}
        # Host inputs: today's date and Link lookups {model: {id: {field: value}}}; misses are lookups still wanted.
        self.today, self.lookups, self.misses = None, {}, {}
        self.lookup_fields = {}
        for field in plan['fields']:
            for scope, model, name in field['reads']:
                if scope == 'lookup':
                    self.lookup_fields.setdefault(model, set()).add(name)
        self.revision, self._next = 0, 0
        self._batch = None
        self.trace, self.deps = {}, {}
        # Initial suggestions waiting to be written, and fields the host set in the current write (touched).
        self.armed, self.touched = set(), set()
        # Lock results of the document as loaded (handle -> field -> True, False or None when undecided).
        # Saved rows keep them until rebase(); host inputs such as lookups refine them until the first edit.
        self.locks, self._pristine = {}, True
        self.root = self._build(data or {}, (), None)
        self._bind()
        created = {node for node in self.deps if self.rows[node[0]].created and node[1].endswith(':initial')}
        self._settle(set(), set(self.deps), self.touched, created)
        self.touched = set()
        self._take_locks(saved=True)
        self.revision = 1

    # Structure

    def _handle(self):
        self._next += 1
        return 'h' + str(self._next)

    def _build(self, data, path, owner, *, created=None):
        if not isinstance(data, dict):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Rows must be objects')
        entry = self.schema.get(path)
        if entry is None:
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Unknown collection {address(path, "")}')
        record_id = data.get('id')
        # A row id keeps the form it came in (a RecordId from storage, numbers in fixtures), as in the browser.
        if record_id is not None and link_id(record_id) is None:
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row IDs must be record ids')
        handle = self._handle()
        key = data.get('_key')
        if key is not None and (not isinstance(key, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', key)):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row keys use at most 64 letters, digits, hyphens or underscores')
        row = Row(handle, path, owner, record_id, key or (str(record_id) if record_id else handle))
        self.rows[handle] = row
        if len(self.rows) - 1 > self.plan['limits']['document_rows']:
            raise CalcError('CALCULATION_LIMIT_EXCEEDED', 'The document has too many rows')
        fresh = record_id is None if created is None else created
        row.created = fresh
        for name, value in data.items():
            if name in ('id', '_key', 'children', '_handle'):
                continue
            meta = entry['fields'].get(name)
            if meta is None:
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Unknown field {name}')
            if meta['computed'] and created:
                raise CalcError('COMPUTED_FIELD_WRITE', f'{name} is calculated')
            if not meta['computed']:
                row.values[name] = value
                if fresh:
                    # A value supplied with a new row is the host's choice: its initial does not replace it.
                    self._touch(handle, name)
        if fresh:
            self._defaults(row, entry)
        children = data.get('children') or {}
        if not isinstance(children, dict) or set(children) - set(entry['tables']):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown child collection')
        for table in entry['tables']:
            items = children.get(table, [])
            if not isinstance(items, list):
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'{table} must be a list of rows')
            if len(items) > self.max_rows(path, table):
                raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'{table} exceeds the collection row limit')
            row.children[table], seen = [], (set(), set())
            for item in items:
                child = self._build(item, path + (table,), row, created=created)
                self._place(row, table, child, len(row.children[table]), seen)
        return row

    def _defaults(self, row, entry):
        for name, meta in entry['fields'].items():
            default = meta.get('default')
            if name in row.values or default is None or default.get('server'):
                continue
            if 'ref' in default:
                parts = default['ref'].split('.')
                if parts[0] != 'parent' or len(parts) != 2 or row.parent is None:
                    continue
                source = self.result(row.parent, parts[1]) if row.parent.handle in self.rows else ready(None)
                row.values[name] = wire(source.value, None) if source.state == 'ready' else {'$state': 'pending'}
            else:
                row.values[name] = default['value']

    def _touch(self, handle, field):
        (self._batch['touched'] if self._batch is not None else self.touched).add((handle, field))

    def max_rows(self, path, table):
        """The table's declared max_rows (plans without one use the engine limit)."""
        entry = self.schema.get(tuple(path) + (table,)) or {}
        return min(entry.get('max_rows', self.plan['limits']['collection_rows']), self.plan['limits']['collection_rows'])

    def _place(self, owner, table, row, at, seen=None):
        """Insert a row; `seen` ({keys}, {ids}) of the rows already in the list makes bulk loads linear."""
        items = owner.children[table]
        if row.key in seen[0] if seen else any(item.key == row.key for item in items if item is not row):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Repeated row key {row.key} in {table}')
        if row.id is not None and (row.id in seen[1] if seen else any(item.id == row.id for item in items if item is not row)):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Repeated row ID {row.id} in {table}')
        if len(items) >= self.max_rows(owner.path, table):
            raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'{table} exceeds the collection row limit')
        items.insert(at, row)
        row.parent = owner
        if seen:
            seen[0].add(row.key)
            seen[1].add(row.id)

    def _labels(self, row, label):
        row.label = label
        for table, items in row.children.items():
            for position, item in enumerate(items):
                item.order = position
                self._labels(item, label + table + '.' + item.key + '.')

    def _bind(self):
        """Instantiate templates for actual rows; template order is a valid instance order."""
        self._labels(self.root, '')
        deps, order, edges = {}, [], 0
        for handle, row in self.rows.items():
            for position, field in self.templates.get(row.path, ()):
                node = (handle, node_name(field))
                # applies_when also runs when its own field changes, so a value set while it does not apply is cleared.
                inputs = {(handle, field['field'])} if field.get('kind') == 'applies_when' else set()
                for scope, table, name in field['reads']:
                    if scope == 'self':
                        inputs.add((handle, name))
                    elif scope == 'parent' and row.parent is not None:
                        inputs.add((row.parent.handle, name))
                    elif scope == 'membership':
                        inputs.add((handle, '#' + table))
                    elif scope == 'member':
                        inputs.update((member.handle, name) for member in row.children.get(table, ()))
                    elif scope == 'today':
                        inputs.add(('@', 'today'))
                    elif scope == 'lookup':
                        inputs.add(('@' + table, name))
                deps[node] = inputs
                edges += len(inputs)
                order.append((position, self._sort_key(row), node))
        if len(deps) > self.plan['limits']['graph_nodes'] or edges > self.plan['limits']['graph_edges']:
            raise CalcError('CALCULATION_LIMIT_EXCEEDED', 'The document graph exceeds its node or edge limit')
        self.deps = deps
        self.order = [node for _, _, node in sorted(order)]
        self.dependents = {}
        for node, inputs in deps.items():
            for item in inputs:
                self.dependents.setdefault(item, set()).add(node)

    def _sort_key(self, row):
        chain = []
        while row is not None:
            chain.append(row.order)
            row = row.parent
        return tuple(reversed(chain))

    # Evaluation

    def result(self, row, field):
        if (row.handle, field) in self.deps:
            return self.results.get((row.handle, field), pending('COMPUTE_INPUT_MISSING', [row.label + field]))
        meta = self.schema[row.path]['fields'].get(field)
        if meta is None:
            return error('UNSUPPORTED_EXPRESSION', [row.label + field])
        return source_value(meta, row.values.get(field), row.label + field)

    def money_places(self, money, scope, budget):
        """The decimal places of a Currency target's currency on this row, or a pending Result while a linked
        record's currency is still being looked up. An empty or unknown currency has 2."""
        from .currencies import currency_decimals, setting_code
        if not money:
            code = None
        elif 'code' in money:
            code = money['code']
        elif 'setting' in money:
            code = (self.currencies or {}).get('settings', {}).get(money['setting']) if self.currencies is not None \
                else setting_code(money['setting'])
        else:
            found = evaluate(money['expression'], scope, budget)
            if found.state == 'pending':
                return found
            code = found.value if found.state == 'ready' else None
        if self.currencies is not None:
            places = self.currencies.get('decimals', {}).get(code) if isinstance(code, str) else None
            return 2 if type(places) is not int else places
        return currency_decimals(code)

    def today_result(self):
        if self.today is None:
            return pending('COMPUTE_INPUT_MISSING', ['@today'])
        return source_value({'type': 'date'}, self.today, '@today')

    def _settle(self, changed, fresh, touched=frozenset(), arm=frozenset()):
        """Visit the global order once; evaluate a node only if an input changed.

        An initial is armed when its row is new or a document input it reads changed (host inputs such
        as lookups and today() do not arm it). A field touched in this write disarms it. An armed
        initial writes its first ready result unless the field is locked. applies_when clears its field.
        """
        budget = Budget(self.budget_limit)
        changed, updated, evaluated, skipped = set(changed), set(), 0, 0
        for node in self.order:
            row = self.rows[node[0]]
            field = self.entries[row.path, node[1]]
            kind = field.get('kind')
            if kind == 'initial':
                if node in arm or any(item in changed and not item[0].startswith('@') for item in self.deps[node]):
                    self.armed.add(node)
                if (node[0], field['field']) in touched:
                    self.armed.discard(node)
            if node not in fresh and not (self.deps[node] & changed):
                skipped += 1
                continue
            at = row.label + node[1]
            scope = Scope(self, row, at=at)
            outcome = evaluate(field['expression'], scope, budget)
            places = None
            if field.get('target_type') == 'currency' and kind in (None, 'initial') and outcome.state == 'ready' and outcome.value is not None:
                # A Currency target without a currency source has 2 places, as before amounts were stored at 6.
                places = self.money_places(field.get('currency') or {}, scope, budget)
                if isinstance(places, Result):
                    outcome, places = places, None
            if kind is None:
                outcome = convert(outcome, field, at, self.schema[row.path]['fields'][node[1]], places)
            elif kind == 'initial':
                outcome = convert(outcome, field, at, None, places)
            if scope.misses:
                self.misses[node] = scope.misses
            else:
                self.misses.pop(node, None)
            evaluated += 1
            self.counts[node] = self.counts.get(node, 0) + 1
            if node not in self.results or self.results[node] != outcome:
                self.results[node] = outcome
                changed.add(node)
                updated.add(node)
            target, value = (node[0], field['field']), MISSING_VALUE
            if kind == 'initial' and node in self.armed and outcome.state != 'pending':
                self.armed.discard(node)
                if outcome.state == 'ready' and not self.locked(row, field['field']):
                    value = wire(outcome.value, wire_scale(field))
            elif kind == 'applies_when' and outcome.state == 'ready' and outcome.value is False and row.values.get(target[1]) is not None \
                    and self.locks.get(row.handle, {}).get(field['field'], False) is False:
                # A field locked on the saved row keeps its value; the server refuses the write instead.
                value = None
            if value is not MISSING_VALUE and (target[1] not in row.values or row.values[target[1]] != value
                                               or type(row.values[target[1]]) is not type(value)):
                row.values[target[1]] = value
                changed.add(target)
                updated.add(target)
        self.trace = {'evaluated': evaluated, 'skipped': skipped, 'steps': budget.steps, 'changed': sorted(row_address(self, node) for node in updated)}
        return updated

    # Commands

    def batch(self, run):
        outer = self._batch is None
        if outer:
            self._batch = {'snapshot': self._capture(), 'bound': set(self.deps), 'sources': set(), 'membership': set(), 'moved': set(), 'structure': False, 'touched': set()}
        if not outer:
            # A nested batch is one command: if its error is caught, its changes are undone.
            return self._command(lambda: check_async(run(self)))
        try:
            check_async(run(self))
        except BaseException:
            if outer:
                self._restore(self._batch['snapshot'])
                self._batch = None
            raise
        if outer:
            batch, self._batch = self._batch, None
            try:
                self._commit(batch)
            except BaseException:
                self._restore(batch['snapshot'])
                raise

    def _command(self, run):
        if self._batch is None:
            return self.batch(lambda _: run())
        # A command whose error the batch catches must not leave the document half changed.
        snapshot = self._capture(), {key: set(value) if isinstance(value, set) else value for key, value in self._batch.items()}
        try:
            return run()
        except BaseException:
            self._restore(snapshot[0])
            self._batch.clear()
            self._batch.update(snapshot[1])
            raise

    def set(self, handle, field, value):
        def run():
            row = self._row(handle)
            meta = self.schema[row.path]['fields'].get(field)
            if meta is None:
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Unknown field {field}')
            if meta['computed']:
                raise CalcError('COMPUTED_FIELD_WRITE', f'{row.label}{field} is calculated')
            self._batch['touched'].add((handle, field))
            if not same_value(row.values, field, value):
                row.values[field] = value
                self._batch['sources'].add((handle, field))
        self._command(run)

    def set_today(self, value):
        """The host's current date (YYYY-MM-DD) for today()."""
        def run():
            self.today = value
            self._batch['sources'].add(('@', 'today'))
        self._command(run)

    def set_lookups(self, model, record_id, values):
        """Field values of one linked record, as the host read them, for Link path expressions."""
        def run():
            fields, key = self.lookup_fields.get(model), link_id(record_id)
            if fields is None or key is None or not isinstance(values, dict) or set(values) - fields:
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'This plan does not look up {model}')
            # Copy on write so a rolled-back command restores the previous record.
            self.lookups = {**self.lookups, model: {**self.lookups.get(model, {}), key: {**self.lookups.get(model, {}).get(key, {}), **values}}}
            self._batch['sources'].update(('@' + model, name) for name in values)
        self._command(run)

    def lookup_requests(self):
        """[model, id, fields] the current results still wait for, sorted; the host reads and supplies them."""
        wanted = {}
        for node, misses in self.misses.items():
            if node in self.deps:
                for model, record_id in misses:
                    wanted.setdefault(model, set()).add(record_id)
        # Ids as text, as the browser engine reports them (evlet/identity.py).
        return [[model, str(record_id), sorted(self.lookup_fields[model])] for model in sorted(wanted) for record_id in sorted(wanted[model])]

    def add_row(self, owner, table, values=None):
        created = []

        def run():
            parent_row = self._row(owner)
            if table not in self.schema[parent_row.path]['tables']:
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Unknown collection {table}')
            data = dict(values or {})
            if data.get('id') is not None:
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'New rows cannot carry persisted IDs')
            row = self._build(data, parent_row.path + (table,), parent_row, created=True)
            self._place(parent_row, table, row, len(parent_row.children[table]))
            self._structure(owner, table)
            created.append(row.handle)
        self._command(run)
        return created[0]

    def remove_row(self, handle):
        def run():
            row = self._row(handle)
            if row.parent is None:
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'The root cannot be removed')
            table = row.path[-1]
            row.parent.children[table].remove(row)
            self._structure(row.parent.handle, table)
            self._drop(row)
        self._command(run)

    def replace_rows(self, owner, table, items):
        def run():
            parent_row = self._row(owner)
            if table not in self.schema[parent_row.path]['tables'] or not isinstance(items, list):
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Unknown collection {table}')
            existing = {row.id: row for row in parent_row.children[table] if row.id is not None}
            old = list(parent_row.children[table])
            parent_row.children[table] = []
            seen, placed = set(), (set(), set())
            for data in items:
                record_id = data.get('id') if isinstance(data, dict) else None
                if record_id is not None and link_id(record_id) is None:
                    raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row IDs must be record ids')
                if record_id is not None:
                    if record_id not in existing or record_id in seen:
                        raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row IDs must belong to this collection and appear once')
                    seen.add(record_id)
                    row = existing[record_id]
                    for name, value in data.items():
                        if name in ('id', '_key', 'children'):
                            continue
                        meta = self.schema[row.path]['fields'].get(name)
                        if meta is None or meta['computed']:
                            raise CalcError('COMPUTED_FIELD_WRITE' if meta else 'DOCUMENT_STRUCTURE_ERROR', f'{name} cannot be written')
                        self._batch['touched'].add((row.handle, name))
                        if not same_value(row.values, name, value):
                            row.values[name] = value
                            self._batch['sources'].add((row.handle, name))
                else:
                    row = self._build(data, parent_row.path + (table,), parent_row, created=True)
                self._place(parent_row, table, row, len(parent_row.children[table]), placed)
            kept = {id(row) for row in parent_row.children[table]}
            for row in old:
                if id(row) not in kept:
                    self._drop(row)
            self._structure(owner, table)
        self._command(run)

    def reorder(self, owner, table, handles):
        def run():
            parent_row = self._row(owner)
            items = parent_row.children.get(table)
            if items is None or not isinstance(handles, list) or sorted(map(str, handles)) != sorted(row.handle for row in items):
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Reorder needs every row of the collection exactly once')
            parent_row.children[table] = [self.rows[handle] for handle in handles]
            self._batch['structure'] = True
        self._command(run)

    def move_row(self, handle, owner, table, at):
        def run():
            row, destination = self._row(handle), self._row(owner)
            if destination.path + (table,) != row.path:
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Rows move only between collections of the same embedding')
            ancestor = destination
            while ancestor is not None:
                if ancestor is row:
                    raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'A row cannot move into its own subtree')
                ancestor = ancestor.parent
            source = row.parent
            position = integer(at)
            if position is None or not 0 <= position <= len(destination.children[table]) - (destination is source):
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Use a valid destination position')
            source.children[table].remove(row)
            self._structure(source.handle, table)
            self._place(destination, table, row, position)
            self._structure(owner, table)
            stack = [row]
            while stack:
                item = stack.pop()
                self._batch['moved'].add(item.handle)
                stack.extend(child for items in item.children.values() for child in items)
        self._command(run)

    def _row(self, handle):
        row = self.rows.get(handle)
        if row is None:
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Unknown row {handle}')
        return row

    def _structure(self, owner, table):
        self._batch['structure'] = True
        self._batch['membership'].add((owner, '#' + table))

    def _drop(self, row):
        stack = [row]
        while stack:
            item = stack.pop()
            self.rows.pop(item.handle, None)
            stack.extend(child for items in item.children.values() for child in items)

    def _capture(self):
        return ({handle: (row, dict(row.values), {table: list(items) for table, items in row.children.items()}, row.parent)
                 for handle, row in self.rows.items()}, dict(self.results), self._next, dict(self.counts),
                dict(self.trace), (self.today, self.lookups, dict(self.misses)), set(self.armed))

    def _restore(self, snapshot):
        rows, results, counter, counts, trace, (self.today, self.lookups, self.misses), self.armed = snapshot
        for handle, (row, values, children, owner) in rows.items():
            row.values, row.children, row.parent = values, children, owner
        self.rows = {handle: state[0] for handle, state in rows.items()}
        self.results, self._next, self.counts, self.trace = results, counter, counts, trace
        self._bind()

    def _commit(self, batch):
        # Bindings as the batch began: a rolled-back command rebinds, so self.deps may already be newer.
        old = batch['bound']
        if batch['structure']:
            self._bind()
        live = set(self.deps)
        for node in old - live:
            self.results.pop(node, None)
            self.counts.pop(node, None)
            self.misses.pop(node, None)
        fresh = (live - old) | {node for node in live if node[0] in batch['moved']}
        updated = self._settle(batch['sources'] | batch['membership'], fresh, batch['touched'],
                               {node for node in fresh if node[1].endswith(':initial')})
        if not (updated or batch['sources'] or batch['structure']):
            return
        if batch['structure'] or any(not key[0].startswith('@') for key in batch['sources']):
            self._pristine = False
        elif self._pristine:
            self._take_locks(saved=True)
        self.revision += 1

    def decided(self, row, field, kind, undecided):
        """A rule's Boolean result, or `undecided` when it has no ready true/false value."""
        result = self.results.get((row.handle, field + ':' + kind))
        if (row.handle, field + ':' + kind) not in self.deps:
            return None
        return result.value if result is not None and result.state == 'ready' and result.value is not None else undecided

    def _take_locks(self, saved):
        """Record lock results as the saved state: rows loaded with an ID, or every row on rebase()."""
        self.locks = {handle: {entry['field']: self.decided(row, entry['field'], 'locked_when', None)
                               for _, entry in self.templates.get(row.path, ()) if entry.get('kind') == 'locked_when'}
                      for handle, row in self.rows.items() if not (saved and row.created)}

    def rebase(self):
        """After the host saves this document: its current locks become the saved state."""
        self._take_locks(saved=False)
        for row in self.rows.values():
            row.created = False
        self._pristine = True

    def locked(self, row, field):
        """A field's lock as the server enforces it: decided on the saved row, or now for a new row."""
        saved = self.locks.get(row.handle, {})
        value = saved[field] if field in saved else self.decided(row, field, 'locked_when', None)
        return value is not False and (field in saved or (row.handle, field + ':locked_when') in self.deps)

    def rule_states(self, handle):
        """{field: {applies, required, locked}} for fields of this row that declare rules.

        Undecided rules (pending, error or null) fail closed: the field applies, is required and is locked.
        A saved row's lock is decided on the row as loaded, so one write cannot unlock and change a field.
        """
        row = self._row(handle)
        states = {}
        for _, entry in self.templates.get(row.path, ()):
            if entry.get('kind') in RULES and entry['field'] not in states:
                name = entry['field']
                applies = self.decided(row, name, 'applies_when', True) is not False
                required = bool(self.schema[row.path]['fields'][name]['required'] or self.decided(row, name, 'required_when', True))
                states[name] = {'applies': applies, 'required': applies and required, 'locked': self.locked(row, name)}
        return dict(sorted(states.items()))

    def find(self, label):
        """Row handle by its label path, e.g. 'items.k1'."""
        for handle, row in self.rows.items():
            if row.label.rstrip('.') == label:
                return handle
        raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Unknown row {label}')

    def snapshot(self):
        return self._snapshot(self.root)

    def _snapshot(self, row):
        result = {'_handle': row.handle, **({'id': row.id} if row.id is not None else {}), **({'_key': row.key} if row.parent else {})}
        result.update(row.values)
        states = {}
        for field in self.plan['fields']:
            if tuple(field['path']) != row.path or field.get('kind'):
                continue
            outcome = self.results.get((row.handle, field['field']))
            result[field['field']] = wire(outcome.value, wire_scale(field)) if outcome and outcome.state == 'ready' else None
            if outcome and outcome.state != 'ready':
                states[field['field']] = outcome.public()
        if states:
            result['_states'] = states
        if row.children:
            result['children'] = {table: [self._snapshot(item) for item in items] for table, items in row.children.items()}
        return result


def row_address(document, node):
    handle, field = node
    row = document.rows.get(handle)
    return (row.label if row else handle + '.') + field


def check_plan(plan):
    if not isinstance(plan, dict) or plan.get('plan_version') != PLAN_VERSION or plan.get('language') != LANGUAGE \
            or plan.get('numeric_profile') != PROFILE or plan.get('expression_version') != EXPRESSION_VERSION:
        raise CalcError('SCHEMA_VERSION_MISMATCH', 'Unsupported calculation plan, language or profile')
    if plan.get('model_schema_hash') != fingerprint(plan):
        raise CalcError('SCHEMA_VERSION_MISMATCH', 'The calculation plan does not match its fingerprint')
    if plan.get('limits') != LIMITS:
        raise CalcError('SCHEMA_VERSION_MISMATCH', 'The calculation plan uses different engine limits')
    for field in plan['fields']:
        scale = field.get('scale')
        if scale is not None and (type(scale) is not int or not 0 <= scale <= MAX_SCALE):
            raise CalcError('UNSUPPORTED_EXPRESSION', 'Invalid target scale')
        if field.get('rounding') not in (None, *ROUNDING):
            raise CalcError('UNSUPPORTED_EXPRESSION', 'Invalid target rounding')
        if field.get('kind', None) not in (None, *KINDS) or 'kind' in field and field['kind'] is None:
            raise CalcError('UNSUPPORTED_EXPRESSION', 'Invalid plan entry kind')
        check_node(field['expression'], 0, [0])
        money = field.get('currency')
        if money is not None:
            if field.get('target_type') != 'currency' or not isinstance(money, dict) or len(money) not in (1, 2):
                raise CalcError('UNSUPPORTED_EXPRESSION', 'Invalid currency source')
            if 'code' in money:
                ok = len(money) == 1 and isinstance(money['code'], str) and re.fullmatch('[A-Z]{3}', money['code']) is not None
            elif 'setting' in money:
                ok = len(money) == 1 and isinstance(money['setting'], str) and re.fullmatch(r'[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*', money['setting']) is not None
            else:
                ok = set(money) == {'expression', 'reads'} and isinstance(money['expression'], dict) \
                    and money['expression'].get('$expr') in ('ref', 'hop') and all(read in field['reads'] for read in money['reads'])
                if ok:
                    check_node(money['expression'], 0, [0])
            if not ok:
                raise CalcError('UNSUPPORTED_EXPRESSION', 'Invalid currency source')
    for table in plan['tables'].values():
        for key in ('min_rows', 'max_rows'):
            if key in table and (type(table[key]) is not int or not 0 <= table[key] <= LIMITS['collection_rows']):
                raise CalcError('SCHEMA_VERSION_MISMATCH', 'Invalid table row limits')
        if table.get('min_rows', 0) > table.get('max_rows', LIMITS['collection_rows']):
            raise CalcError('SCHEMA_VERSION_MISMATCH', 'Invalid table row limits')
        for meta in table['fields'].values():
            for bound in (meta.get('minimum'), meta.get('maximum')):
                if bound is not None:
                    parse(bound)


SCOPES = {'self', 'parent', 'row'}
ARITY = {'add': 2, 'sub': 2, 'mul': 2, 'div': 2, 'neg': 1, 'eq': 2, 'ne': 2, 'lt': 2, 'lte': 2, 'gt': 2, 'gte': 2,
         'and': 2, 'or': 2, 'not': 1, 'round': 2, 'coalesce': 2, 'safe_divide': 3, 'bool_to_decimal': 1,
         'when': 3, 'date_add': 2, 'date_sub': 2, 'date_diff': 2}
LITERALS = {'decimal': str, 'boolean': bool, 'text': str}
NAME = r'[A-Za-z][A-Za-z0-9_]*'
MODEL_NAME = r'[a-z0-9_]{1,64}'
HOP_TYPES = {'decimal', 'boolean', 'date', 'text', 'link', 'duration'}


def hop_steps(steps):
    """One to three [model, field, type] lookups; every step but the last follows a Link."""
    return isinstance(steps, list) and 1 <= len(steps) <= MAX_HOPS and all(
        isinstance(step, list) and len(step) == 3 and isinstance(step[0], str) and re.fullmatch(MODEL_NAME, step[0]) is not None
        and isinstance(step[1], str) and re.fullmatch(NAME, step[1]) is not None and step[2] in HOP_TYPES
        and (step[2] == 'link' or index == len(steps) - 1) for index, step in enumerate(steps))


def check_node(node, depth, count, scale=False, measure=False):
    """Closed, bounded IR: unknown operations, arities, scopes or shapes fail before evaluation.

    An integer literal is only valid as round()'s scale (`scale`), and nothing else is valid there.
    month_start is valid only in a measure's where= (`measure`); plans never contain it.
    """
    count[0] += 1
    if not isinstance(node, dict) or depth > LIMITS['expression_depth'] or count[0] > LIMITS['expression_nodes']:
        raise CalcError('UNSUPPORTED_EXPRESSION', 'Calculation plans must be bounded expression objects')
    op = node.get('$expr')
    if op == 'literal':
        kind, value = node.get('type'), node.get('value')
        if kind == 'integer':
            ok = scale and integer(value) is not None and 0 <= value <= MAX_SCALE
        else:
            ok = not scale and (value is None if kind == 'null' else kind in LITERALS and type(value) is LITERALS[kind])
            if ok and kind == 'decimal':
                parse(value)
    elif op == 'ref':
        ok = not scale and node.get('scope') in SCOPES and isinstance(node.get('field'), str) and re.fullmatch(NAME, node['field']) is not None
    elif op == 'today' or op == 'month_start' and measure:
        ok = not scale
    elif op == 'hop':
        ok = not scale and isinstance(node.get('field'), str) and re.fullmatch(NAME, node['field']) is not None and hop_steps(node.get('steps'))
    elif op == 'aggregate':
        field = node.get('field', 0)
        ok = not scale and node.get('kind') in ('sum', 'min', 'max', 'count') and isinstance(node.get('table'), str) and 'where' in node \
            and (field is None if node['kind'] == 'count' else isinstance(field, str) and re.fullmatch(NAME, field) is not None)
        if ok and node['where'] is not None:
            check_node(node['where'], depth + 1, count, measure=measure)
    else:
        args = node.get('args')
        ok = not scale and op in ARITY and isinstance(args, list) and len(args) == ARITY[op]
        for index, arg in enumerate(args if ok else []):
            check_node(arg, depth + 1, count, op == 'round' and index == 1, measure)
    if not ok:
        raise CalcError('UNSUPPORTED_EXPRESSION', f'Unknown or malformed calculation node {op!r}')
