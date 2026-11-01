"""Finite calculation language (finite-v1): authoring, typed plans and exact evaluation.

Python declarations build immutable expressions. The compiler turns one root document
and its owned tables into a versioned plan. The same plan runs here (authoritative) and
in the browser (preview). Evaluation is pure: no database, clock, network or callbacks.
"""

from dataclasses import dataclass
from decimal import Decimal as PyDecimal
import hashlib
import inspect
import json
import re

LANGUAGE = 'finite-v1'
PROFILE = 'finite40-hu18-v1'
LEGACY_PROFILE = 'legacy-document-v2'
PLAN_VERSION = 1
EXPRESSION_VERSION = 1
COMPILER_VERSION = 'evlet-finite-1'
LIMITS = {'expression_nodes': 128, 'expression_depth': 32, 'table_depth': 8, 'collection_rows': 100,
          'document_rows': 1000, 'graph_nodes': 50000, 'graph_edges': 250000,
          'evaluation_steps': 1000000, 'decimal_text': 128}
MAX_DIGITS, MAX_SCALE, DIVISION_SCALE = 40, 36, 18
SAFE_INTEGER = 2**53 - 1
OPERATIONS = {'add', 'sub', 'mul', 'div', 'neg', 'eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'and', 'or', 'not',
              'round', 'coalesce', 'safe_divide', 'bool_to_decimal'}


class CalcError(ValueError):
    """A finite-language failure with a stable code and closed-path or field causes."""
    def __init__(self, code, message, causes=()):
        self.code, self.message, self.causes = code, message, tuple(causes)
        super().__init__(f'{code}: {message}')


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


def parse(value):
    """Strict numeric input: decimal strings, safe integers or exact Python decimals."""
    if isinstance(value, Num):
        return value
    if type(value) is int:
        if abs(value) > SAFE_INTEGER:
            raise CalcError('COMPUTE_RANGE_ERROR', 'Integer inputs must fit the browser safe range')
        return normalize(value, 0)
    if isinstance(value, PyDecimal):
        if not value.is_finite():
            raise CalcError('COMPUTE_TYPE_ERROR', 'Use a finite decimal value')
        sign, digits, exponent = value.as_tuple()
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


def div(a, b):
    if b.coef == 0:
        raise CalcError('COMPUTE_DIVISION_BY_ZERO', 'Division by zero')
    power = DIVISION_SCALE + b.scale - a.scale
    numerator, denominator = a.coef * 10**max(power, 0), b.coef * 10**max(-power, 0)
    return normalize(half_up(numerator, denominator), DIVISION_SCALE)


def quantize(value, scale):
    if value.scale <= scale:
        return value
    return normalize(half_up(value.coef, 10**(value.scale - scale)), scale)


def total(values):
    """One exact aggregate: a wide accumulator, bounded only at the final result."""
    if not values:
        return Num(0, 0)
    scale = max(value.scale for value in values)
    return normalize(sum(value.coef * 10**(scale - value.scale) for value in values), scale)


def to_decimal(value):
    return PyDecimal(value.coef).scaleb(-value.scale)


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
        raise CalcError('EXPRESSION_TRUTH_TEST', 'Calculations cannot use Python if/and/or/not or chained comparisons; use &, |, ~ and safe functions')

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


def operand(op, left, right=None):
    """Field descriptors build expressions through these operators."""
    return Expr(op, (lift(left),) if right is None else (lift(left), lift(right)))


def D(value):
    """An exact decimal literal, e.g. D("0.05"). It never parses code."""
    if type(value) is bool or isinstance(value, float):
        raise CalcError('UNSUPPORTED_EXPRESSION', 'Use a decimal string or integer, not a float or boolean')
    return Expr('literal', type='decimal', value=text(parse(value)))


def F(name):
    """A local field by name, for forward references."""
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
        raise CalcError('UNSUPPORTED_EXPRESSION', 'F() takes one declared field name')
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
        return name, field_type(owner.__declared_fields__[name])

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
        if op in ('add', 'sub', 'mul', 'div', 'neg', 'safe_divide'):
            return {'$expr': op, 'args': [numeric(item) for item in args]}, 'decimal', via_parent
        if op == 'round':
            return {'$expr': 'round', 'args': [numeric(args[0]), args[1][0]]}, 'decimal', via_parent
        if op in ('eq', 'ne'):
            if 'null' not in kinds and kinds[0] != kinds[1]:
                fail(error, f'cannot compare {kinds[0]} with {kinds[1]}')
            return {'$expr': op, 'args': [item[0] for item in args]}, 'boolean', via_parent
        if op in ('lt', 'lte', 'gt', 'gte'):
            if any(kind != 'decimal' for kind in kinds):
                fail(error, 'ordered comparisons require numeric operands')
            return {'$expr': op, 'args': [item[0] for item in args]}, 'boolean', via_parent
        if op in ('and', 'or', 'not'):
            if any(kind != 'boolean' for kind in kinds):
                fail(error, f'{op} requires Boolean predicates')
            return {'$expr': op, 'args': [item[0] for item in args]}, 'boolean', via_parent
        if op == 'coalesce':
            if kinds[0] != 'null' and kinds[1] != 'null' and kinds[0] != kinds[1]:
                fail(error, 'coalesce() operands must share a type')
            return {'$expr': op, 'args': [item[0] for item in args]}, kinds[0] if kinds[0] != 'null' else kinds[1], via_parent
        fail('UNSUPPORTED_EXPRESSION', f'unknown operation {op!r}')

    ir, kind, _ = visit(expr, 0, 'self')
    return ir, kind, reads


def compile_plan(registry, root):
    """Compile the finite plan for a root model, or return None for legacy documents."""
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
    finite = any(is_finite(field) for model, _, _ in tables.values() for field in model.__declared_fields__.values())
    if not finite and language is None:
        return None
    fields, schema = [], {}
    for path, (model, parent_model, children) in tables.items():
        if any(field.compute is not None and not is_finite(field) for field in model.__declared_fields__.values()):
            raise CalcError('UNSUPPORTED_EXPRESSION', f'{root.__name__}: finite-v1 documents cannot also use legacy string computes ({address(path, "")})')
        if model.__sources__ or any(field.query is not None for field in model.__declared_fields__.values()):
            raise CalcError('UNSUPPORTED_EXPRESSION', f'{root.__name__}: finite-v1 documents do not support Query sources yet')
        entry = {}
        for name, field in model.__declared_fields__.items():
            if name in getattr(model, '__internal_fields__', ()):
                continue
            entry[name] = {'type': field_type(field), 'kind': field.kind, 'stored': field.stored, 'computed': is_finite(field),
                           'required': field.required, 'scale': getattr(field, 'scale', None) if isinstance(field, DecimalField) else (0 if isinstance(field, Integer) else None),
                           'minimum': None if getattr(field, 'minimum', None) is None else str(field.minimum),
                           'maximum': None if getattr(field, 'maximum', None) is None else str(field.maximum),
                           'default': creation_default(field)}
        schema[address(path, '').rstrip('.') or '$root'] = {'path': list(path), 'model': registry.name_for(model), 'fields': entry,
                                                               'tables': sorted(children)}
        for name, field in model.__declared_fields__.items():
            if not is_finite(field):
                continue
            if field.default is not MISSING or field.fetch is not None or field.query is not None:
                raise CalcError('UNSUPPORTED_EXPRESSION', f'{address(path, name)}: a computed field has one writer; remove its default/fetch/query')
            context = _Context(model, path, parent_model, children)
            ir, kind, reads = compile_expression(field.compute, context, name)
            target = field_type(field)
            if target != kind and not (target == 'decimal' and kind == 'decimal'):
                raise CalcError('COMPUTE_TYPE_ERROR', f'{address(path, name)}: expression type {kind} does not match {target}')
            fields.append({'target': address(path, name), 'path': list(path), 'field': name, 'result_type': kind,
                           'target_type': field.kind, 'scale': entry[name]['scale'], 'stored': field.stored,
                           'expression': ir, 'reads': [list(read) for read in reads]})
    order = template_order(fields)
    plan = {'plan_version': PLAN_VERSION, 'language': LANGUAGE, 'numeric_profile': PROFILE,
            'expression_version': EXPRESSION_VERSION, 'compiler_version': COMPILER_VERSION,
            'root_resource': getattr(root, '__resource_id__', registry.name_for(root)), 'limits': LIMITS,
            'tables': schema, 'fields': [fields[index] for index in order]}
    plan['model_schema_hash'] = fingerprint(plan)
    return plan


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
    """Deterministic topological order; any value cycle fails with a closed path."""
    index = {field['target']: position for position, field in enumerate(fields)}
    edges = {field['target']: sorted(read for read in template_reads(field) if read in index) for field in fields}
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
        order = compare(*values)
        return ready({'lt': order < 0, 'lte': order <= 0, 'gt': order > 0, 'gte': order >= 0}[op])
    if op == 'neg': return ready(Num(-values[0].coef, values[0].scale))
    if op == 'round': return guarded(lambda: quantize(values[0], values[1]), scope)
    return guarded(lambda: {'add': add, 'sub': sub, 'mul': mul, 'div': div}[op](*values), scope)


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


def convert(result, field, at):
    """Strict target assignment: no unrequested rounding."""
    if result.state != 'ready' or result.value is None or field['result_type'] != 'decimal':
        return result
    value, scale = result.value, field['scale']
    if scale is not None and quantize(value, scale) != value:
        return error('COMPUTE_SCALE_ERROR', [at])
    if field['target_type'] == 'integer' and abs(value.coef) > SAFE_INTEGER:
        return error('COMPUTE_RANGE_ERROR', [at])
    if field['stored'] and scale is not None and abs(value.coef * 10**(scale - value.scale)) > 2**63 - 1:
        return error('COMPUTE_RANGE_ERROR', [at])
    return result


def source_value(meta, raw, at):
    """Interpret one submitted source value for evaluation."""
    if isinstance(raw, dict) and raw.get('$state') == 'pending':
        return pending('COMPUTE_INPUT_MISSING', [at])
    if raw is None or raw == '' and meta['type'] != 'text':
        return ready(None)
    try:
        if meta['type'] == 'decimal':
            return ready(parse(raw))
        if meta['type'] == 'boolean' and type(raw) is not bool:
            raise CalcError('COMPUTE_TYPE_ERROR', 'Expected a Boolean')
        return ready(raw)
    except CalcError as exc:
        return error(exc.code, [at])


def check_async(value):
    if inspect.isawaitable(value):
        if hasattr(value, 'close'): value.close()
        raise CalcError('ASYNC_BATCH_UNSUPPORTED', 'A calculation batch cannot await; apply network results as a later batch')


# Document sessions: one bound graph per root, synchronous batches, one publication.

class Row:
    __slots__ = ('handle', 'path', 'values', 'parent', 'children', 'id', 'key', 'label', 'order')

    def __init__(self, handle, path, parent, record_id, key):
        self.handle, self.path, self.parent = handle, path, parent
        self.values, self.children, self.id, self.key = {}, {}, record_id, key
        self.label, self.order = '', 0


class Scope:
    """Lexical scopes for one evaluation: self (declaring row), parent, and a filter row."""
    __slots__ = ('document', 'row', 'member', 'at')

    def __init__(self, document, row, member=None, at=''):
        self.document, self.row, self.member, self.at = document, row, member, at

    def read(self, scope, field):
        target = self.member if scope == 'row' else self.row.parent if scope == 'parent' else self.row
        if target is None:
            return pending('PARENT_CONTEXT_REQUIRED', [self.at])
        return self.document.result(target, field)

    def members(self, table):
        return [Scope(self.document, self.row, member, self.at) for member in self.row.children.get(table, ())]

    def address(self, field):
        return (self.member or self.row).label + field

    def causes(self):
        return [self.at]


class Document:
    """A browser-equivalent session over one plan. The server uses it for full settlement."""
    def __init__(self, plan, data=None, *, budget=None):
        check_plan(plan)
        self.plan, self.hash = plan, plan['model_schema_hash']
        self.schema = {tuple(entry['path']): entry for entry in plan['tables'].values()}
        self.templates = {}
        for position, field in enumerate(plan['fields']):
            self.templates.setdefault(tuple(field['path']), []).append((position, field))
        self.budget_limit = budget or plan['limits']['evaluation_steps']
        self.rows, self.results, self.counts = {}, {}, {}
        self.observers, self.table_observers = {}, {}
        self.revision, self.structure_revision, self._next = 0, 0, 0
        self._batch = None
        self._publishing = self._disposed = False
        self.trace, self.deps = {}, {}
        self.root = self._build(data or {}, (), None)
        self._bind()
        self._settle(set(), set(self.deps))
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
        if record_id is not None and (type(record_id) is not int or record_id < 1):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row IDs must be positive integers')
        handle = self._handle()
        key = data.get('_key')
        if key is not None and (not isinstance(key, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', key)):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Row keys use at most 64 letters, digits, hyphens or underscores')
        row = Row(handle, path, owner, record_id, key or (str(record_id) if record_id else handle))
        self.rows[handle] = row
        if len(self.rows) > self.plan['limits']['document_rows']:
            raise CalcError('CALCULATION_LIMIT_EXCEEDED', 'The document has too many rows')
        fresh = record_id is None if created is None else created
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
            self._defaults(row, entry)
        children = data.get('children') or {}
        if not isinstance(children, dict) or set(children) - set(entry['tables']):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Unknown child collection')
        for table in entry['tables']:
            items = children.get(table, [])
            if not isinstance(items, list) or len(items) > self.plan['limits']['collection_rows']:
                raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'{table} exceeds the collection row limit')
            row.children[table] = []
            for item in items:
                child = self._build(item, path + (table,), row, created=created)
                self._place(row, table, child, len(row.children[table]))
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

    def _place(self, owner, table, row, at):
        items = owner.children[table]
        if any(item.key == row.key for item in items if item is not row):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Repeated row key {row.key} in {table}')
        if row.id is not None and any(item.id == row.id for item in items if item is not row):
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Repeated row ID {row.id} in {table}')
        if len(items) >= self.plan['limits']['collection_rows']:
            raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'{table} exceeds the collection row limit')
        items.insert(at, row)
        row.parent = owner

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
                node = (handle, field['field'])
                inputs = set()
                for scope, table, name in field['reads']:
                    if scope == 'self':
                        inputs.add((handle, name))
                    elif scope == 'parent' and row.parent is not None:
                        inputs.add((row.parent.handle, name))
                    elif scope == 'membership':
                        inputs.add((handle, '#' + table))
                    elif scope == 'member':
                        inputs.update((member.handle, name) for member in row.children.get(table, ()))
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

    def _settle(self, changed, fresh):
        """Visit the global order once; evaluate a node only if an input changed."""
        budget = Budget(self.budget_limit)
        changed, updated, evaluated, skipped = set(changed), set(), 0, 0
        templates = {(field['field'], tuple(field['path'])): field for field in self.plan['fields']}
        for node in self.order:
            if node not in fresh and not (self.deps[node] & changed):
                skipped += 1
                continue
            row = self.rows[node[0]]
            field = templates[node[1], row.path]
            at = row.label + node[1]
            outcome = convert(evaluate(field['expression'], Scope(self, row, at=at), budget), field, at)
            evaluated += 1
            self.counts[node] = self.counts.get(node, 0) + 1
            if node not in self.results or self.results[node] != outcome:
                self.results[node] = outcome
                changed.add(node)
                updated.add(node)
        self.trace = {'evaluated': evaluated, 'skipped': skipped, 'steps': budget.steps, 'changed': sorted(row_address(self, node) for node in updated)}
        return updated

    # Commands

    def batch(self, run):
        self._usable()
        outer = self._batch is None
        if outer:
            self._batch = {'snapshot': self._capture(), 'sources': set(), 'membership': set(), 'moved': set(), 'structure': False, 'order': set()}
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
                failures = self._commit(batch)
            except BaseException:
                self._restore(batch['snapshot'])
                raise
            if failures:
                # The batch is committed; an observer's failure is reported, not rolled back.
                raise failures[0]

    def _command(self, run):
        if self._batch is None:
            return self.batch(lambda _: run())
        self._usable()
        return run()

    def _usable(self):
        if self._disposed:
            raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'This document session was disposed')
        if self._publishing:
            raise CalcError('REACTIVE_REENTRANCY', 'Observers cannot change the document during publication')

    def set(self, handle, field, value):
        def run():
            row = self._row(handle)
            meta = self.schema[row.path]['fields'].get(field)
            if meta is None:
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', f'Unknown field {field}')
            if meta['computed']:
                raise CalcError('COMPUTED_FIELD_WRITE', f'{row.label}{field} is calculated')
            row.values[field] = value
            self._batch['sources'].add((handle, field))
        self._command(run)

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
            seen = set()
            for data in items:
                record_id = data.get('id') if isinstance(data, dict) else None
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
                        row.values[name] = value
                        self._batch['sources'].add((row.handle, name))
                else:
                    row = self._build(data, parent_row.path + (table,), parent_row, created=True)
                self._place(parent_row, table, row, len(parent_row.children[table]))
            for row in old:
                if row not in parent_row.children[table]:
                    self._drop(row)
            self._structure(owner, table)
        self._command(run)

    def reorder(self, owner, table, handles):
        def run():
            parent_row = self._row(owner)
            items = parent_row.children.get(table)
            if items is None or sorted(handles) != sorted(row.handle for row in items):
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Reorder needs every row of the collection exactly once')
            parent_row.children[table] = [self.rows[handle] for handle in handles]
            self._batch['order'].add((owner, table))
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
            source.children[table].remove(row)
            self._structure(source.handle, table)
            if type(at) is not int or not 0 <= at <= len(destination.children[table]):
                raise CalcError('DOCUMENT_STRUCTURE_ERROR', 'Use a valid destination position')
            self._place(destination, table, row, at)
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
        return {handle: (row, dict(row.values), {table: list(items) for table, items in row.children.items()}, row.parent)
                for handle, row in self.rows.items()}, dict(self.results), self._next

    def _restore(self, snapshot):
        rows, results, counter = snapshot
        for handle, (row, values, children, owner) in rows.items():
            row.values, row.children, row.parent = values, children, owner
        self.rows = {handle: state[0] for handle, state in rows.items()}
        self.results, self._next = results, counter
        self._bind()

    def _commit(self, batch):
        old = set(self.deps)
        if batch['structure']:
            self._bind()
        live = set(self.deps)
        for node in old - live:
            self.results.pop(node, None)
            self.counts.pop(node, None)
        fresh = (live - old) | {node for node in live if node[0] in batch['moved']}
        updated = self._settle(batch['sources'] | batch['membership'], fresh)
        if not (updated or batch['sources'] or batch['structure']):
            return []
        self.revision += 1
        if batch['structure']:
            self.structure_revision += 1
        for node in [key for key in self.observers if key[0] not in self.rows]:
            self.observers.pop(node)
        for key in [key for key in self.table_observers if key[0] not in self.rows]:
            self.table_observers.pop(key)
        notify = [(node, listeners) for node, listeners in self.observers.items() if node in updated or node in batch['sources']]
        tables = [(key, listeners) for key, listeners in self.table_observers.items()
                  if (key[0], '#' + key[1]) in batch['membership'] or key in batch['order']]
        self._publishing, failures = True, []
        try:
            for (handle, field), listeners in notify:
                outcome = self.result(self.rows[handle], field)
                for listener in list(listeners):
                    try: listener(outcome)
                    except Exception as exc: failures.append(exc)
            for (handle, table), listeners in tables:
                handles = [row.handle for row in self.rows[handle].children[table]]
                for listener in list(listeners):
                    try: listener(handles)
                    except Exception as exc: failures.append(exc)
        finally:
            self._publishing = False
        return failures

    # Observation and diagnostics

    def observe(self, handle, field, listener):
        self._usable()
        self._row(handle)
        listeners = self.observers.setdefault((handle, field), [])
        listeners.append(listener)

        def unsubscribe():
            if listener in self.observers.get((handle, field), []):
                self.observers[handle, field].remove(listener)
        return unsubscribe

    def observe_table(self, handle, table, listener):
        self._usable()
        self._row(handle)
        listeners = self.table_observers.setdefault((handle, table), [])
        listeners.append(listener)

        def unsubscribe():
            if listener in self.table_observers.get((handle, table), []):
                self.table_observers[handle, table].remove(listener)
        return unsubscribe

    def subscriptions(self):
        return sum(len(items) for items in self.observers.values()) + sum(len(items) for items in self.table_observers.values())

    def explain(self, handle, field):
        row = self._row(handle)
        node = (handle, field)
        computed = node in self.deps
        return {'role': 'computed' if computed else 'source', 'target': row.label + field,
                'owner': row.parent.label.rstrip('.') if row.parent else None,
                'inputs': sorted(row_address(self, item) for item in self.deps.get(node, ())),
                'dependents': sorted(row_address(self, item) for item in self.dependents.get(node, ())),
                'state': self.result(row, field).public(), 'evaluations': self.counts.get(node, 0),
                'language': self.plan['language'], 'profile': self.plan['numeric_profile'],
                'plan': self.hash, 'revision': self.revision}

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
            if tuple(field['path']) != row.path:
                continue
            outcome = self.results.get((row.handle, field['field']))
            result[field['field']] = wire(outcome.value, field['scale']) if outcome and outcome.state == 'ready' else None
            if outcome and outcome.state != 'ready':
                states[field['field']] = outcome.public()
        if states:
            result['_states'] = states
        if row.children:
            result['children'] = {table: [self._snapshot(item) for item in items] for table, items in row.children.items()}
        return result

    def sources(self, row=None, *, identities=True):
        """Source-only data, e.g. to duplicate a document or rebuild it from scratch."""
        row = row or self.root
        result = dict(row.values)
        if identities:
            result.update({**({'id': row.id} if row.id is not None else {}), **({'_key': row.key} if row.parent else {})})
        if row.children:
            result['children'] = {table: [self.sources(item, identities=identities) for item in items] for table, items in row.children.items()}
        return result

    def dispose(self):
        self.observers.clear()
        self.table_observers.clear()
        self.rows.clear()
        self.results.clear()
        self.deps, self.dependents, self.order = {}, {}, []
        self._disposed = True


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
