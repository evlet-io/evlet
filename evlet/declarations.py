"""Small authoring declarations shared by documents, dialogs and reports."""

from dataclasses import dataclass
from types import MappingProxyType
import copy
import inspect
import re

from .fields import COLORS, MISSING, Select


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


# State manager declarations (evlet/statemanager.py runs them).

KEY = re.compile(r'[a-z][a-z0-9_]{0,62}')
REMARK_LENGTH = 1000


def _key(value, what):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise ValueError(f'{what} is a lowercase name such as "draft", not {value!r}')
    return value


def _text(value, what):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f'{what} must be nonempty text')
    return value


def _flag(value, what):
    if type(value) is not bool:
        raise ValueError(f'{what} must be True or False')
    return value


def _condition(value, what):
    return guard_expression(value, what)


class Step:
    """One state: State.Step("issued", "Issued", color="green", lock=True, release=True, final=False).

    `lock` makes the document read only in this state, except fields declared `versioned=False`. `release`
    makes entering it a release. `final` means no rule leaves it. Only `label` and `color` may change on
    an instance; the behaviour is the model's."""
    def __init__(self, key, label=None, *, color=None, lock=False, release=False, final=False):
        self.key = _key(key, 'A step key')
        self.label = _text(label, 'A step label') or key.replace('_', ' ').capitalize()
        if color is not None and color not in COLORS:
            raise ValueError(f'Step {key!r}: color is one of ' + ', '.join(COLORS))
        self.color = color
        self.lock, self.release, self.final = _flag(lock, 'lock'), _flag(release, 'release'), _flag(final, 'final')

    def public(self):
        return {'key': self.key, 'label': self.label, 'color': self.color, 'lock': self.lock, 'release': self.release, 'final': self.final}


class Remark:
    """What a move asks people to write: Remark("required", label="Why?"), or "optional" with
    `required_when`, a finite Boolean on the saved record that makes it required."""
    MODES = ('optional', 'required')

    def __init__(self, mode='optional', *, label=None, required_when=None):
        if mode not in self.MODES:
            raise ValueError('A remark is "optional" or "required"')
        if mode == 'required' and required_when is not None:
            raise ValueError('A required remark has no required_when')
        self.mode, self.label = mode, _text(label, 'A remark label')
        self.required_when = _condition(required_when, 'required_when')

    def public(self):
        return {'mode': self.mode, 'label': self.label}


def _remark(value):
    if value is None or isinstance(value, Remark):
        return value
    if value in Remark.MODES:
        return Remark(value)
    raise ValueError('remark is "optional", "required" or Remark(...)')


def _by(value, what):
    """(roles, user fields): role names, built-in or custom, and F("field") user fields. () is the application."""
    from .calc import Expr
    if isinstance(value, (str, Expr)) or not isinstance(value, (list, tuple)):
        raise ValueError(f'{what} is a tuple of role names and F("field") user fields')
    roles, users = [], []
    for item in value:
        if isinstance(item, Expr) and item.op == 'ref' and '.' not in item.meta.get('name', '.'):
            users.append(item.meta['name'])
        elif isinstance(item, str) and KEY.fullmatch(item):
            roles.append(item)
        else:
            raise ValueError(f'{what} names roles ("editor") and user fields (F("approver")), not {item!r}')
    return tuple(roles), tuple(users)


class Level:
    """One approval level of a rule: Level(by=("cfo",), when=F("total") >= 10000, pending="Pending CFO approval")."""
    def __init__(self, by, *, when=None, pending=None):
        self.roles, self.users = _by(by, 'A level by')
        if not self.roles and not self.users:
            raise ValueError('A level needs at least one approver')
        self.when = _condition(when, 'when')
        self.pending = _text(pending, 'A level pending text')
        if pending is None:
            raise ValueError('A level needs its pending text, for example "Pending manager approval"')


class Rule:
    """One move: Rule("draft", "issued", "Issue", by=("editor",), when=F("total") > 0, remark="optional").

    `from_` is a state or a tuple of states. `key` defaults to "<from>_to_<to>"; it is the rule's stable id
    in routes, the log and instance overrides. `by` names roles and user fields (default admin and editor);
    `by=()` is a rule only application code uses. `levels` makes it an approval; `reject=True` marks a rule
    back that only the current level's approvers are offered. `ask` adds fields the move dialog asks for to
    those the fields' applies_when and required_when imply. `primary=True` marks the main button."""
    def __init__(self, from_, to, /, label=None, *, key=None, by=None, when=None, levels=(), self_approve=False,
                 remark=None, ask=(), primary=False, reject=False):
        sources = (from_,) if isinstance(from_, str) else from_
        if not isinstance(sources, (list, tuple)) or not sources or len(set(sources)) != len(sources):
            raise ValueError('A rule starts from a state or a tuple of distinct states')
        self.sources = tuple(_key(source, 'A rule source') for source in sources)
        self.to = _key(to, 'A rule target')
        if to in self.sources:
            raise ValueError('A rule must change the state')
        self.label = _text(label, 'A rule label') or to.replace('_', ' ').capitalize()
        self.key = _key(key or '_'.join(self.sources) + '_to_' + to, 'A rule key')
        self.roles, self.users = _by(('admin', 'editor') if by is None else by, 'by')
        self.when = _condition(when, 'when')
        if isinstance(levels, Level) or not isinstance(levels, (list, tuple)) or any(not isinstance(level, Level) for level in levels):
            raise ValueError('levels is a tuple of Level(...)')
        self.levels = tuple(levels)
        self.self_approve, self.primary, self.reject = _flag(self_approve, 'self_approve'), _flag(primary, 'primary'), _flag(reject, 'reject')
        if self.self_approve and not self.levels:
            raise ValueError('self_approve applies to a rule with levels')
        if self.levels and self.reject:
            raise ValueError('A rule with levels approves; a reject is its own rule back')
        if self.levels and (self.roles or self.users) and by is not None:
            raise ValueError('An approval rule is used by its levels\' approvers; leave out by')
        if self.levels:
            self.roles, self.users = (), ()
        self.remark = _remark(remark)
        if isinstance(ask, str) or not isinstance(ask, (list, tuple)):
            raise ValueError('ask is a tuple of field names')
        self.ask = tuple(ask)

    @property
    def system(self):
        """Whether only application code makes this move."""
        return not self.roles and not self.users and not self.levels

    def __repr__(self):
        return f'Rule({self.sources!r}, {self.to!r}, key={self.key!r})'


class State(Select):
    """A document's state field: its steps in order and the rules between them. See the module docstring.

    `show` is how the record page draws it: "badge" (the default), "bar" (the stage bar) or "none"."""
    Step = Step
    OPTIONS = ('label', 'help_text', 'tooltip', 'placeholder', 'hidden', 'index', 'max_length', 'panel', 'copy', 'column')
    SHOW = ('badge', 'bar', 'none')

    def __init__(self, *steps, rules=(), default=MISSING, show='badge', **kwargs):
        if set(kwargs) - set(self.OPTIONS):
            raise ValueError('State takes its steps, rules, default, show and ' + ', '.join(self.OPTIONS))
        if not steps or any(not isinstance(step, Step) for step in steps):
            raise ValueError('A State takes its steps: State(State.Step("draft"), State.Step("done"), rules=(...))')
        if show not in self.SHOW:
            raise ValueError('State show is "badge", "bar" or "none"')
        self.show = show
        keys = [step.key for step in steps]
        if len(set(keys)) != len(keys):
            raise ValueError('State steps must have distinct keys')
        default = keys[0] if default is MISSING else default
        colors = {step.key: step.color for step in steps if step.color}
        super().__init__(keys, default=default, required=True, read_only=True, colors=colors or None, **kwargs)
        if default not in keys:
            raise ValueError('A State default must be one of its steps')
        self.steps = MappingProxyType({step.key: step for step in steps})
        self.locked = tuple(step.key for step in steps if step.lock)
        self.final = tuple(step.key for step in steps if step.final)
        self.releases = tuple(step.key for step in steps if step.release)
        if default in self.final:
            raise ValueError('A State default cannot be final')
        if isinstance(rules, Rule) or not isinstance(rules, (list, tuple)) or any(not isinstance(rule, Rule) for rule in rules):
            raise ValueError('State rules is a tuple of Rule(...)')
        self.rules = tuple(rules)
        check_rules(self, self.rules)

    def metadata(self):
        return {**super().metadata(), 'state': True}


State.Rule, State.Level, State.Remark = Rule, Level, Remark


def check_rules(field, rules):
    """Refuse rules that name unknown states, leave a final state or repeat a key."""
    keys = set()
    for rule in rules:
        unknown = {*rule.sources, rule.to} - set(field.choices)
        if unknown:
            raise ValueError(f'Rule {rule.key!r} names an unknown state: ' + ', '.join(sorted(unknown)))
        if set(rule.sources) & set(field.final):
            raise ValueError(f'Rule {rule.key!r} leaves a final state; final states have no rules out')
        if rule.key in keys:
            raise ValueError(f'Two rules have the key {rule.key!r}; give one its own key=')
        keys.add(rule.key)
