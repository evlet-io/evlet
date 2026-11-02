"""Conditions for naming series, stored as text: `total >= 250000 and status == "sent"`.

A small language with no functions, attributes, arithmetic or names other than the model's fields.
It is parsed when an override is saved and again on start; `evaluate` runs on cleaned values inside
the create transaction. Separate from evlet/ui/expressions.py, which serves the browser.

    expr    := or
    or      := and ("or" and)*
    and     := unary ("and" unary)*
    unary   := "not" unary | "(" expr ")" | compare
    compare := FIELD op value | FIELD "in" "(" value ("," value)* ")"
    op      := "==" | "!=" | ">" | ">=" | "<" | "<="
    value   := STRING | NUMBER | "true" | "false" | "empty"
"""

from datetime import date
import difflib
from decimal import Decimal
import re

from .fields import Boolean, Date, Decimal as DecimalField, Integer, Select, active_layer

MAX_LENGTH, MAX_DEPTH = 500, 10
ISO_DATE = re.compile(r'[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])')
EMPTY = object()
TOKEN = re.compile(r'\s*(?:(==|!=|>=|<=|>|<)|(-?[0-9]+(?:\.[0-9]+)?)(?![A-Za-z0-9_.])|([A-Za-z_][A-Za-z0-9_]*)|([(),]))')


class ConditionError(ValueError):
    """A condition that can't be used, with a message for people and the position it starts at."""

    def __init__(self, message, at=0):
        super().__init__(message)
        self.at = at


class UnknownField(ConditionError):
    def __init__(self, name, fields, at):
        near = difflib.get_close_matches(name, fields, n=1, cutoff=0.6)
        super().__init__(f"{name} isn't a field on this model." + (f' Did you mean {near[0]}?' if near else ''), at)
        self.name = name


def tokens(source):
    """(kind, value, position) tokens; kind is op, num, str, word or a punctuation character."""
    result, position = [], 0
    while position < len(source):
        if source[position].isspace():
            position += 1
            continue
        character = source[position]
        if not character.isprintable():
            raise ConditionError("A condition can't contain control characters.", position)
        if character in '"\'':
            end = source.find(character, position + 1)
            if end < 0:
                raise ConditionError('A quote is not closed.', position)
            result.append(('str', source[position + 1:end], position))
            position = end + 1
            continue
        match = TOKEN.match(source, position)
        if match is None or match.end() == position:
            if character == '=':
                raise ConditionError('Use == to compare, not =.', position)
            raise ConditionError(f'"{character}" isn\'t allowed here.', position)
        op, number, word, punctuation = match.groups()
        at = match.start(match.lastindex)
        if op:
            result.append(('op', op, at))
        elif number:
            result.append(('num', Decimal(number), at))
        elif word:
            result.append(('word', word, at))
        else:
            result.append((punctuation, punctuation, at))
        position = match.end()
    return result


class Parser:
    def __init__(self, source, fields):
        if not isinstance(source, str):
            raise ConditionError('Write the condition as text.')
        if len(source) > MAX_LENGTH:
            raise ConditionError(f'Keep a condition under {MAX_LENGTH} characters.')
        self.source, self.fields, self.items, self.position = source, fields, tokens(source), 0
        self.used = set()

    def peek(self):
        return self.items[self.position] if self.position < len(self.items) else None

    def bracket(self, kind):
        item = self.peek()
        if item is None or item[0] != kind:
            message = 'A bracket is not closed.' if kind == ')' else 'Put the values in brackets, like status in ("sent", "paid").'
            raise ConditionError(message, len(self.source) if item is None else item[2])
        self.position += 1

    def text(self, item):
        return self.source[item[2]:].split()[0] if self.source[item[2]:].strip() else str(item[1])

    def parse(self):
        node = self.either(0)
        item = self.peek()
        if item is not None:
            raise ConditionError(f'Unexpected "{self.text(item)}". Join conditions with and / or.', item[2])
        return node

    def either(self, depth):
        node = self.both(depth)
        while self.peek() and self.peek()[:2] == ('word', 'or'):
            self.position += 1
            node = ('or', node, self.both(depth))
        return node

    def both(self, depth):
        node = self.unary(depth)
        while self.peek() and self.peek()[:2] == ('word', 'and'):
            self.position += 1
            node = ('and', node, self.unary(depth))
        return node

    def unary(self, depth):
        item = self.peek()
        if item is None:
            raise ConditionError('The condition ends too early.', len(self.source))
        if item[:2] in (('word', 'not'), ('(', '(')):
            if depth >= MAX_DEPTH:
                raise ConditionError(f'Nest brackets and not at most {MAX_DEPTH} deep.', item[2])
            self.position += 1
            if item[0] == 'word':
                return ('not', self.unary(depth + 1))
            node = self.either(depth + 1)
            self.bracket(')')
            return node
        return self.compare()

    def compare(self):
        item = self.peek()
        if item[0] != 'word' or item[1] in ('and', 'or', 'in', 'true', 'false', 'empty'):
            raise ConditionError(f'Start with a field name, not "{self.text(item)}".', item[2])
        self.position += 1
        name, field = item[1], self.fields.get(item[1])
        if field is None:
            raise UnknownField(name, list(self.fields), item[2])
        self.used.add(name)
        following = self.peek()
        if following is not None and following[:2] == ('word', 'in'):
            self.position += 1
            self.bracket('(')
            values = [self.value(field)]
            while self.peek() and self.peek()[0] == ',':
                self.position += 1
                values.append(self.value(field))
            self.bracket(')')
            return ('in', name, tuple(values))
        if following is None:
            raise ConditionError('The condition ends too early.', len(self.source))
        if following[0] != 'op':
            raise ConditionError(f'Compare {name} with ==, !=, >, >=, < or <=.', following[2])
        self.position += 1
        if isinstance(field, Boolean) and following[1] not in ('==', '!='):
            raise ConditionError(f'Compare {name} with == or !=.', following[2])
        value = self.value(field)
        if value is EMPTY and following[1] not in ('==', '!='):
            raise ConditionError(f'Compare with empty using == or !=, like {name} != empty.', following[2])
        return ('cmp', name, following[1], value)

    def value(self, field):
        item = self.peek()
        if item is None:
            raise ConditionError('A value is missing at the end.', len(self.source))
        self.position += 1
        kind, value, at = item
        if kind == 'word' and value == 'empty':
            return EMPTY
        if kind == 'word' and value in ('true', 'false'):
            if not isinstance(field, Boolean):
                raise ConditionError(f'{field.name} is not a yes or no field, so it takes no true or false.', at)
            return value == 'true'
        if kind == 'word':
            raise ConditionError(f'Put text values in quotes, like "{value}".', at)
        if isinstance(field, Boolean):
            raise ConditionError(f'Compare {field.name} with true or false.', at)
        numeric = isinstance(field, (Integer, DecimalField))
        if kind == 'num':
            if not numeric:
                raise ConditionError(f'Put text values in quotes, like "{self.source[at:].split()[0].rstrip(",)")}".', at)
            return value
        if kind == 'str':
            if numeric:
                raise ConditionError(f'{field.name} is a number, so leave out the quotes.', at)
            if isinstance(field, Select):
                options = (*field.choices, *active_layer().choices.get(field, ()))
                if value not in options:
                    raise ConditionError(f"{value} isn't an option of {field.name}. Use {', '.join(options)}.", at)
            if isinstance(field, Date) and not ISO_DATE.fullmatch(value):
                raise ConditionError(f'Write dates as year-month-day, like {field.name} >= "2026-01-31".', at)
            return value
        raise ConditionError('A value is missing.', at)


class Condition:
    """A parsed condition: `fields` it reads, and `matches(values)`."""

    def __init__(self, source, fields):
        parser = Parser(source, fields)
        self.source, self.tree, self.fields = source, parser.parse(), frozenset(parser.used)

    def matches(self, values):
        return evaluate(self.tree, values)


def parse(source, fields):
    """Parse a condition against {name: Field}. Raises ConditionError (UnknownField for a missing field)."""
    return Condition(source, fields)


def plain(value):
    """A stored value as the language compares it: numbers as Decimal, dates as ISO text."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def evaluate(node, values):
    kind = node[0]
    if kind == 'and':
        return evaluate(node[1], values) and evaluate(node[2], values)
    if kind == 'or':
        return evaluate(node[1], values) or evaluate(node[2], values)
    if kind == 'not':
        return not evaluate(node[1], values)
    actual = plain(values.get(node[1]))
    if kind == 'in':
        return any(same(actual, item) for item in node[2])
    _, _, op, expected = node
    if op == '==':
        return same(actual, expected)
    if op == '!=':
        return not same(actual, expected)
    if expected is EMPTY or actual in (None, '') or type(actual) is not type(expected):
        return False
    return {'>': actual > expected, '>=': actual >= expected, '<': actual < expected, '<=': actual <= expected}[op]


def same(actual, expected):
    if expected is EMPTY:
        return actual in (None, '')
    return type(actual) is type(expected) and actual == expected

