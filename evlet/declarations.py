"""Small authoring declarations shared by documents, dialogs and reports."""

from dataclasses import dataclass
from types import MappingProxyType
import copy
import inspect
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
    `max_rows` (default 100, at most 1,000) bounds the collection in both runtimes. `sections=True` lets
    the Table hold section rows: headings that group the rows below them (evlet/sections.py)."""
    def __init__(self, child, *, required=False, min_rows=None, max_rows=DEFAULT_TABLE_ROWS, label=None,
                 storage_name=None, owner_field='evlet_owner', panel=True, copy=True, sections=False):
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
        # Whether its tab shows when a list opens the record in its side panel (display only).
        if type(panel) is not bool:
            raise ValueError('Table panel must be True or False')
        self.panel = panel
        # Whether the browser's Duplicate copies its rows into the new record (display only).
        if type(copy) is not bool:
            raise ValueError('Table copy must be True or False')
        self.copy = copy
        # Section rows live in evlet_sections, so turning this on or off needs no migration.
        if type(sections) is not bool:
            raise ValueError('Table sections must be True or False')
        self.sections = sections
        if sections and 'section' in {*getattr(child, '__declared_fields__', {}), *getattr(child, '__tables__', {})}:
            # {"section": label} marks a section row, so the rows cannot have a field of that name.
            raise ValueError('A Table with sections=True needs a row model without a field named section')

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


NAME = r'[A-Za-z][A-Za-z0-9_]*'
# Options that make a field calculated or rule-bound; inputs of actions are plain values.
# Context.action(model, record_id, name, **inputs) takes these names itself.
RESERVED_INPUTS = ('self', 'model', 'record_id', 'name')
INPUT_OPTIONS = ('compute', 'query', 'fetch', 'initial', 'applies_when', 'required_when', 'locked_when',
                 'visible_if', 'required_if', 'read_only_if')


def guard_expression(value, name):
    """`when=` and `available_when=` take a finite Boolean expression (or a Boolean field), never text or a callable."""
    from .calc import Expr
    from .fields import Field
    if value is None or isinstance(value, (Expr, Field)):
        return value
    raise ValueError(f"{name} takes a finite expression such as F('total') > 0")


def check_roles(roles):
    from .auth import PORTAL, ROLES
    if isinstance(roles, str) or not isinstance(roles, (list, tuple)) or not roles or set(roles) - {*ROLES, PORTAL}:
        raise ValueError('roles must name at least one of: ' + ', '.join((*ROLES, PORTAL)))
    return tuple(roles)


class Move:
    """A declared change of a State: Move("draft", "issued", "Issue", when=F("total") > 0, run="on_issue").

    The first argument is one state or a tuple of states the move may start from. `when` is a finite
    Boolean guard decided on the record as saved; `run` names a model method (taking only self) that runs
    in the same transaction before the record settles.

    `system=True` declares a move only the application makes: it has no roles, is never offered to people
    (no button, not in `_moves` or the schema) and every HTTP caller is refused it, while trusted Python
    (jobs, actions, other moves' run methods) makes it with context.move() under all its other checks.
    """
    def __init__(self, from_, to, /, label=None, *, roles=None, when=None, run=None, system=False):
        sources = (from_,) if isinstance(from_, str) else from_
        if not isinstance(sources, (list, tuple)) or not sources or any(not isinstance(item, str) or not item for item in sources) \
                or len(set(sources)) != len(sources):
            raise ValueError('A move starts from a state name or a tuple of distinct state names')
        if not isinstance(to, str) or not to:
            raise ValueError('A move goes to one state name')
        if to in sources:
            raise ValueError('A move must change the state')
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ValueError('A move label must be nonempty text')
        if run is not None and (not isinstance(run, str) or not re.fullmatch(NAME, run) or run.startswith('_')):
            raise ValueError('run names a model method')
        if type(system) is not bool:
            raise ValueError('Move system must be True or False')
        if system and roles is not None:
            raise ValueError('A system move has no roles: only the application makes it. Drop roles, or drop system=True')
        if not system and isinstance(roles, (list, tuple)) and not roles:
            raise ValueError('A move needs at least one role. For a move only the application makes, '
                             'declare Move(..., system=True)')
        self.sources, self.to = tuple(sources), to
        self.label = label or to.replace('_', ' ').capitalize()
        self.system = system
        self.roles = () if system else check_roles(('admin', 'editor') if roles is None else roles)
        self.when, self.run = guard_expression(when, 'when'), run

    def __repr__(self):
        return f'Move({self.sources!r}, {self.to!r}, {self.label!r})'


def action(function=None, *, label=None, roles=('admin', 'editor'), inputs=None, available_when=None):
    """Declare a model method as an action: @action, @action("Label", inputs={...}, available_when=...).

    With `inputs` (a mapping of names to Field instances) each input is validated by its field and
    passed as a keyword argument; the method's signature is not read for types. Without `inputs`, the
    method's annotated scalar parameters (str, int, bool, float, RecordId) are its inputs.
    """
    if isinstance(function, str):
        label, function = function, None
    from .fields import Field
    if inputs is not None:
        if not isinstance(inputs, dict):
            raise ValueError('Action inputs are a mapping of names to fields')
        named = {}
        for name, field in inputs.items():
            if not isinstance(name, str) or not re.fullmatch(NAME, name) or name.startswith('evlet_') or '__' in name:
                raise ValueError(f'Invalid action input name: {name!r}')
            if name in RESERVED_INPUTS:
                raise ValueError(f'Action input {name!r} is reserved; Context.action() uses it. Choose another name')
            if not isinstance(field, Field) or any(getattr(field, option, None) is not None for option in INPUT_OPTIONS) \
                    or field.filters or not field.stored or getattr(field, 'moves', None) is not None:
                raise ValueError(f'Action input {name!r} must be a plain field without calculations, rules or fetches')
            # A copy, so a Field instance shared with a model or another action keeps its own name.
            field = copy.copy(field)
            field.name = ''
            field.__set_name__(None, name)
            named[name] = field
        inputs = MappingProxyType(named)
    roles = check_roles(roles)
    available_when = guard_expression(available_when, 'available_when')

    def decorate(method):
        if inputs is not None:
            parameters = list(inspect.signature(method).parameters.values())[1:]
            names = {parameter.name for parameter in parameters if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)}
            variadic = any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters)
            needed = {parameter.name for parameter in parameters if parameter.default is parameter.empty
                      and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)}
            if needed - set(inputs) or (set(inputs) - names and not variadic):
                raise ValueError(f'{method.__name__} must take exactly its declared inputs as keyword arguments')
        method.__action__ = {'label': label or method.__name__.replace('_', ' ').title(), 'roles': roles,
                             'inputs': inputs, 'available_when': available_when}
        return method
    return decorate(function) if function is not None else decorate
