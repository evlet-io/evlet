"""Bounded expressions for string compute= rules, as neutral {op, args} instructions."""

import ast
from dataclasses import dataclass
from functools import lru_cache
from decimal import Decimal, localcontext, ROUND_HALF_EVEN

from .declarations import Ref
from .fields import Decimal as DecimalField, Integer
from .calc import is_finite


@dataclass(frozen=True)
class Expression:
    """One instruction: an operation name and its arguments, which may be expressions too."""
    op: str
    args: tuple

# Field kinds string formulas calculate with: exact decimals, as finite-v1 reads them (a Percent of 12.50 is 12.50).
NUMERIC = ('integer', 'decimal', 'currency', 'percent')

OPS = {ast.Add: 'add', ast.Sub: 'sub', ast.Mult: 'mul', ast.Div: 'div',
       ast.Eq: 'eq', ast.NotEq: 'ne', ast.Lt: 'lt', ast.LtE: 'lte', ast.Gt: 'gt', ast.GtE: 'gte',
       ast.Is: 'eq', ast.IsNot: 'ne'}


def compile_expression(source):
    if not isinstance(source, str) or len(source) > 2048:
        raise ValueError('Expressions must be text of at most 2048 characters')
    tree = ast.parse(source, mode='eval')
    if len(list(ast.walk(tree))) > 128:
        raise ValueError('Expression is too complex')

    def path(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return path(node.value) + '.' + node.attr
        raise ValueError('Use a declared field path')

    def visit(node):
        if isinstance(node, ast.Constant):
            if type(node.value) not in (str, int, bool, type(None)):
                raise ValueError('Use decimal strings in expressions, not binary float literals')
            if type(node.value) is int and abs(node.value) > 2**53-1:
                raise ValueError('Integer expression literals must fit the browser safe range')
            return node.value
        if isinstance(node, (ast.Name, ast.Attribute)):
            value = Ref(path(node)).path
            return Expression('ref', (value,))
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return Expression(OPS[type(node.op)], (visit(node.left), visit(node.right)))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
            return Expression('not' if isinstance(node.op, ast.Not) else 'neg', (visit(node.operand),))
        if isinstance(node, ast.BoolOp):
            result = visit(node.values[0])
            for value in node.values[1:]:
                result = Expression('and' if isinstance(node.op, ast.And) else 'or', (result, visit(value)))
            return result
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in OPS:
            return Expression(OPS[type(node.ops[0])], (visit(node.left), visit(node.comparators[0])))
        if isinstance(node, ast.IfExp):
            return Expression('choose', (visit(node.test), visit(node.body), visit(node.orelse)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('sum', 'count', 'min', 'max') and len(node.args) == 1 and not node.keywords:
            return Expression(node.func.id, (visit(node.args[0]),))
        raise ValueError(f'Unsupported expression at column {getattr(node, "col_offset", 0) + 1}: {type(node).__name__}')
    return visit(tree.body)


def serialized(value):
    return {'$expr': value.op, 'args': [serialized(item) for item in value.args]} if isinstance(value, Expression) else value


def references(value):
    if not isinstance(value, Expression):
        return set()
    return {value.args[0]} if value.op == 'ref' else set().union(*(references(arg) for arg in value.args))


def number(value):
    if value is None:
        raise ValueError('A required calculation input is missing')
    if type(value) is bool:
        raise ValueError('Booleans are not numeric calculation inputs')
    value = Decimal(value)
    if not value.is_finite() or len(value.as_tuple().digits) > 40 or value.as_tuple().exponent < -36 or value.adjusted() > 39:
        raise ValueError('Calculation exceeds the supported range')
    return value


def evaluate(value, read):
    if not isinstance(value, Expression):
        return value
    op, args = value.op, value.args
    if op == 'ref':
        return read(args[0])
    a = evaluate(args[0], read)
    if op == 'choose':
        return evaluate(args[1] if a else args[2], read)
    if op == 'and':
        return bool(a) and bool(evaluate(args[1], read))
    if op == 'or':
        return bool(a) or bool(evaluate(args[1], read))
    if op == 'not':
        return not a
    if op in ('sum', 'count', 'min', 'max'):
        if not isinstance(a, (tuple, list)):
            raise ValueError('Aggregates require a table field')
        if op == 'count':
            return len(a)
        values = [number(item) for item in a]
        with localcontext() as context:
            context.prec = 82
            return number(sum(values, Decimal(0))) if op == 'sum' else (min(values) if op == 'min' else max(values)) if values else None
    if op == 'neg':
        return number(a).copy_negate()
    b = evaluate(args[1], read)
    if op in ('add', 'sub', 'mul', 'div'):
        a, b = number(a), number(b)
        with localcontext() as context:
            context.prec = 82
            if op == 'div':
                if b == 0:
                    raise ValueError('Division by zero')
                return number((a / b).quantize(Decimal('1e-18'), rounding=ROUND_HALF_EVEN))
            return number({'add': lambda: a + b, 'sub': lambda: a - b, 'mul': lambda: a * b}[op]())
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        if a is not None and b is not None:
            a, b = number(a), number(b)
    if op == 'eq': return a == b
    if op == 'ne': return a != b
    if a is None or b is None: return False
    return {'lt': lambda: a < b, 'lte': lambda: a <= b, 'gt': lambda: a > b, 'gte': lambda: a >= b}[op]()


class Plans(dict):
    """Per-field plans of a model's app fields. An instance field (x_) has no rules, so it has an empty plan."""

    def __missing__(self, name):
        return {}


@lru_cache(maxsize=512)
def plans(model, parent=None):
    result = Plans()
    fields = model.__app_fields__
    for name, field in fields.items():
        entry = {}
        for key in ('compute', 'visible_if', 'required_if', 'read_only_if'):
            source = getattr(field, key)
            if key == 'compute' and is_finite(field):
                entry['finite'] = True  # Evaluated from the finite plan, not this legacy compiler.
            elif source is not None:
                entry[key] = compile_expression(source)
        if isinstance(field.default, Ref):
            entry['default_ref'] = Expression('ref', (field.default.path,))
        if field.filters:
            entry['filters'] = {key: Expression('ref', (value.path,)) if isinstance(value, Ref) else value for key, value in field.filters.items()}
        if field.query is not None:
            entry['query'] = True
        if field.fetch is not None:
            entry['fetch'] = field.fetch
        result[name] = entry
    return result


def graph(registry, model):
    """Build a template graph across parent fields and each owned row type: (nodes, order)."""
    nodes, order, _, _ = template(registry, model)
    return nodes, order


@lru_cache(maxsize=512)
def template(registry, model):
    """(nodes, order, inputs, owners) of a document's template graph.

    `inputs` maps each node to the nodes its value is derived from (compute, Query and fetch), without
    the nodes that only conditions, filters or creation defaults read. `owners` maps nodes to models.
    """
    nodes, dependencies, inputs, owners = {}, {}, {}, {}
    children = {child.name: child.model for child in registry.children if child.parent is model}
    contexts = [('', model, None)] + [(name + '.', children.get(name, table.child), model) for name, table in model.__tables__.items()]
    for prefix, definition, parent in contexts:
        for name, source in definition.__sources__.items():
            nodes[prefix + name] = {'source': True}
            paths = {value.path for value in source.filters.values() if isinstance(value, Ref)} | set(getattr(source.target, '__depends__', ()))
            dependencies[prefix + name] = {path[7:] if path.startswith('parent.') and parent else prefix + path.replace('.*.', '.') for path in paths}
            inputs[prefix + name] = dependencies[prefix + name]
            owners[prefix + name] = definition
        for name, field in definition.__app_fields__.items():
            owners[prefix + name] = definition
            entry = plans(definition, parent)[name]
            nodes[prefix + name] = entry
            paths = set().union(*(references(value) for key, value in entry.items() if key != 'filters'))
            paths |= set().union(*(references(value) for value in entry.get('filters', {}).values()))
            paths |= set(getattr(field.query, '__depends__', ()))
            if field.fetch: paths.add(field.fetch.split('.')[0])
            values = references(entry.get('compute')) | set(getattr(field.query, '__depends__', ()))
            if field.fetch: values.add(field.fetch.split('.')[0])

            def keys(paths):
                deps = set()
                for path in paths:
                    path = path.replace('.*.', '.')
                    if path.startswith('source.') and getattr(model, '__source__', None) is not None:
                        if path[7:] not in model.__source__.__declared_fields__: raise ValueError('Unknown source field: ' + path)
                        continue
                    if not prefix and path in model.__tables__:
                        continue
                    deps.add(path[7:] if path.startswith('parent.') and parent else prefix + path)
                return deps
            dependencies[prefix + name], inputs[prefix + name] = keys(paths), keys(values)

    def resolve(key, deps):
        resolved = set()
        for dependency in deps:
            owner_path, _, member = dependency.rpartition('.')
            if owner_path in nodes and nodes[owner_path].get('source'):
                source = owners[owner_path].__sources__[owner_path.split('.')[-1]]
                if member not in source.fields: raise ValueError(f'{model.__name__}.{key}: unknown query output {dependency}')
                dependency = owner_path
            if dependency not in nodes:
                raise ValueError(f'{model.__name__}.{key}: unknown dependency {dependency}')
            resolved.add(dependency)
        return resolved
    dependencies = {key: resolve(key, deps) for key, deps in dependencies.items()}
    inputs = {key: frozenset(resolve(key, deps)) for key, deps in inputs.items()}
    visited, active, order = set(), set(), []
    def visit(key):
        if key in active:
            raise ValueError(f'{model.__name__}: circular field dependency at {key}')
        if key in visited: return
        active.add(key)
        for dependency in sorted(dependencies[key]):
            # Defaults/conditions do not write values; only derived sources form value cycles.
            if 'compute' in nodes[dependency] or nodes[dependency].get('source') or nodes[dependency].get('query'):
                visit(dependency)
        active.remove(key)
        visited.add(key)
        order.append(key)
    for key in nodes: visit(key)
    for prefix, definition, parent in contexts:
        for name, field in definition.__declared_fields__.items():
            entry = nodes[prefix + name]
            def reference_type(path):
                parts = path.split('.')
                owner, aggregate = definition, False
                if parts[0] == 'parent' and parent: owner, parts = parent, parts[1:]
                elif parts[0] == 'source' and getattr(model, '__source__', None): owner, parts = model.__source__, parts[1:]
                if parts[0] in owner.__tables__:
                    if len(parts) == 1: return ('array', 'record')
                    owner, parts, aggregate = owner.__tables__[parts[0]].child, parts[1:], True
                if parts[0] in owner.__sources__:
                    value = owner.__sources__[parts[0]].fields[parts[1]]
                else: value = owner.__declared_fields__[parts[0]]
                kind = 'number' if value.kind in NUMERIC else value.kind
                return ('array', kind) if aggregate else kind
            for kind in ('compute', 'visible_if', 'required_if', 'read_only_if'):
                if kind not in entry: continue
                try:
                    result_type = expression_type(entry[kind], reference_type)
                except ValueError as exc:
                    raise ValueError(f'{model.__name__}.{prefix}{name} ({kind}): {exc}') from None
                # A Select holds text: a formula may calculate it (one of its choices, which validation checks).
                target_type = 'number' if field.kind in NUMERIC else 'text' if field.kind == 'select' else field.kind
                if kind == 'compute' and result_type not in (target_type, 'null'):
                    raise ValueError(f'{model.__name__}.{prefix}{name}: expression type {result_type} does not match {target_type}')
    return nodes, order, inputs, owners


def expression_type(value, read):
    if not isinstance(value, Expression):
        if value is None: return 'null'
        if type(value) is bool: return 'boolean'
        if type(value) is int: return 'number'
        return 'text'
    op, args = value.op, value.args
    if op == 'ref': return read(args[0])
    kinds = [expression_type(arg, read) for arg in args]
    if op in ('add', 'sub', 'mul', 'div', 'neg'):
        for arg, kind in zip(args, kinds):
            if kind in ('number', 'null'): continue
            if isinstance(arg, str):
                try: number(arg); continue
                except ArithmeticError: pass
            if isinstance(arg, Expression) and arg.op == 'ref':
                raise ValueError(f'Arithmetic requires numbers, but {arg.args[0]} is {describe(kind)}. '
                                 'Use integer, decimal, currency or percent fields, or decimal text such as "0.05"')
            raise ValueError(f'Arithmetic requires numbers, but part of the formula gives {describe(kind, "value")}. '
                             'Use integer, decimal, currency or percent fields, or decimal text such as "0.05"')
        return 'number'
    if op in ('sum', 'count', 'min', 'max'):
        if not isinstance(kinds[0], tuple) or kinds[0][0] != 'array' or op != 'count' and kinds[0][1] != 'number':
            raise ValueError('Aggregates require a numeric table field; count also accepts a table')
        return 'number'
    if op == 'choose':
        if kinds[1] == 'null': return kinds[2]
        if kinds[2] == 'null' or kinds[1] == kinds[2]: return kinds[1]
        raise ValueError('Conditional branches require compatible types')
    return 'boolean'


def describe(kind, noun='field'):
    """What a formula part is, as an error message names it: "a text field", "a boolean value", "a table"."""
    if isinstance(kind, tuple):
        return 'a table' if kind[1] == 'record' else 'a table column (use sum, min or max)'
    return ('an ' if str(kind)[0] in 'aeiou' else 'a ') + f'{kind} {noun}'


def round_field(field, value, read=None):
    """A legacy formula's result for its field: whole for Integer, the field's scale (half even) for Decimal, and
    for Currency the decimals of the currency it resolves to through `read` (the record's values), else 2."""
    if value is not None and getattr(field, 'kind', None) == 'currency':
        from .currencies import currency_decimals
        source, code = field.currency_source(), None
        if source is not None and 'code' in source:
            code = source['code']
        elif source is not None and 'field' in source and read is not None:
            try:
                code = read(source['link'] + '.' + source['field'] if 'link' in source else source['field'])
            except (KeyError, ValueError, TypeError, AttributeError):
                code = None
        elif source is not None and 'setting' in source:
            from .currencies import setting_code
            code = setting_code(source['setting']['app'] + '.' + source['setting']['key'])
        with localcontext() as context:
            context.prec = 40
            return number(value).quantize(Decimal(1).scaleb(-currency_decimals(code)), rounding=ROUND_HALF_EVEN)
    if value is not None and isinstance(field, Integer):
        numeric = number(value)
        if numeric != numeric.to_integral_value() or abs(numeric) > 2**53-1:
            raise ValueError('Integer calculations require a whole browser-safe value')
        return int(numeric)
    if value is not None and isinstance(field, DecimalField):
        with localcontext() as context:
            context.prec = 40
            return number(value).quantize(Decimal(1).scaleb(-field.scale), rounding=ROUND_HALF_EVEN)
    return value
