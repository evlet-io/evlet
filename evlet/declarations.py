"""Small authoring declarations shared by documents, dialogs and reports."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Ref:
    path: str

    def __post_init__(self):
        if (not isinstance(self.path, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*', self.path)
                or any(part in {'constructor', 'prototype', '__proto__'} for part in self.path.split('.'))):
            raise ValueError('Use a declared field path')


# Rows one owned table may hold. Tables under legacy (non finite-v1) roots keep 100.
MAX_TABLE_ROWS = 1000
DEFAULT_TABLE_ROWS = 100


class Table:
    """Owned rows. `min_rows` is checked on save (`required=True` means at least one row);
    `max_rows` (default 100, at most 1,000) bounds the collection in both runtimes."""
    def __init__(self, child, *, required=False, min_rows=None, max_rows=DEFAULT_TABLE_ROWS, label=None,
                 storage_name=None, owner_field='evlet_owner'):
        for name, value in (('min_rows', min_rows), ('max_rows', max_rows)):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f'Table {name} must be a nonnegative integer')
        if required and min_rows == 0:
            raise ValueError('A required table needs at least one row; use min_rows=1 or more, or drop required')
        min_rows = (1 if required else 0) if min_rows is None else min_rows
        if not 1 <= max_rows <= MAX_TABLE_ROWS:
            raise ValueError(f'Table max_rows must be between 1 and {MAX_TABLE_ROWS}')
        if min_rows > max_rows:
            raise ValueError('Table min_rows cannot exceed max_rows')
        self.child, self.label = child, label
        self.min_rows, self.max_rows = min_rows, max_rows
        self.required = min_rows > 0
        self.name = ''
        self.storage_name, self.owner_field = storage_name, owner_field

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner=None):
        return self if instance is None else getattr(instance, '_children', {}).get(self.name, ())

    # Finite aggregates over this table's rows, e.g. lines.sum("amount").
    def where(self, predicate=None, **equals):
        from .calc import Selection
        return Selection(self).where(predicate, **equals)

    def sum(self, field):
        from .calc import Selection
        return Selection(self).sum(field)

    def min(self, field):
        from .calc import Selection
        return Selection(self).min(field)

    def max(self, field):
        from .calc import Selection
        return Selection(self).max(field)

    def count(self):
        from .calc import Selection
        return Selection(self).count()


class ReactiveQuery:
    """One typed, permission-aware source shared by several derived fields."""
    def __init__(self, target, *, filters=None, fields):
        from .fields import Field
        self.target, self.filters = target, dict(filters or {})
        if not fields or not isinstance(fields, (list, tuple, dict)):
            raise ValueError('Query fields must declare a nonempty result shape')
        if callable(target) and not isinstance(target, type):
            if not hasattr(target, '__depends__') or not isinstance(fields, dict) or any(not isinstance(field, Field) for field in fields.values()):
                raise ValueError('Function queries require @depends and a mapping of typed result fields')
        self.fields = dict(fields) if isinstance(fields, dict) else {name: None for name in fields}
        for name in self.fields: Ref(name)
        self.name = ''

    def __set_name__(self, owner, name): self.name = name

    def __get__(self, instance, owner=None):
        return self if instance is None else getattr(instance, '_sources', {}).get(self.name)


Query = ReactiveQuery


def depends(*paths):
    for path in paths:
        Ref(path.replace('.*.', '.'))
    def decorate(function):
        function.__depends__ = paths
        return function
    return decorate


def action(function=None, *, label=None, roles=('admin', 'editor')):
    def decorate(method):
        method.__action__ = {'label': label or method.__name__.replace('_', ' ').title(), 'roles': tuple(roles)}
        return method
    return decorate(function) if function is not None else decorate
