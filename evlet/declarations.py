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


class Table:
    def __init__(self, child, *, required=False, label=None, storage_name=None, owner_field='evlet_owner'):
        self.child, self.required, self.label = child, required, label
        self.name = ''
        self.storage_name, self.owner_field = storage_name, owner_field

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner=None):
        return self if instance is None else getattr(instance, '_children', {}).get(self.name, ())


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
