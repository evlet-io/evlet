"""Small authoring declarations shared by documents, dialogs and reports."""

from dataclasses import dataclass
from types import MappingProxyType
import copy
import functools
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
    the Table hold section rows: headings that group the rows below them (evlet/sections.py).

    `applies_when` and `visible_when` are finite Booleans on the record that owns the rows, as on a field:
    while applies_when is false the Table is hidden, calculations read it as empty and a save removes
    its rows; while visible_when is false it is only hidden (docs/finite-calculations.md#field-rules).
    `totals` names fields of the owning model shown under the table, in order (checked when the model is defined)."""
    def __init__(self, child, *, required=False, min_rows=None, max_rows=DEFAULT_TABLE_ROWS, label=None,
                 storage_name=None, owner_field='evlet_owner', panel=True, copy=True, sections=False, derived=False,
                 applies_when=None, visible_when=None, totals=(), append_when_released=False):
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
        # derive() builds the rows on every write and preview (docs/model-engine.md#derived-values).
        if type(derived) is not bool:
            raise ValueError('Table derived must be True or False')
        self.derived = derived
        # A log of what happened after the document was released (arrivals on a sent transfer): trusted code may
        # append rows to it while the record is locked; no request may, and its rows are not released content.
        if type(append_when_released) is not bool:
            raise ValueError('Table append_when_released must be True or False')
        self.append_when_released = append_when_released
        if not isinstance(totals, (tuple, list)) or not all(type(name) is str for name in totals):
            raise ValueError('Table totals must be a tuple of field names of the owning model')
        self.totals = tuple(totals)
        from .fields import _finite_option
        self.applies_when = _finite_option(applies_when, 'applies_when')
        self.visible_when = _finite_option(visible_when, 'visible_when')
        if applies_when is not None and self.required:
            raise ValueError('A Table that may not apply cannot require rows; drop required/min_rows or applies_when')
        if applies_when is not None and derived:
            raise ValueError('derive() builds the rows of a derived Table; applies_when is for rows people enter')
        if visible_when is not None and self.required:
            raise ValueError('A Table that requires rows must stay visible, so it cannot have visible_when; '
                             'drop required/min_rows, or use applies_when without them')
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


# Parts of an Option: literal text, F("field") or F("link.field") of the option's record, and a per-record
# Count, Sum, Min or Max over a related model (model= and link=), whose where= is {field: value or Ref("path")}.
OPTION_FUNCTIONS = ('count', 'sum', 'min', 'max')
MAX_OPTION_PARTS, MAX_OPTION_LITERAL = 8, 200


class Option:
    """What a record shows as an option in Link dropdowns, record pickers and the search bar: its title and
    a muted subtext line below it, built in the search query (evlet/search.py, docs/model-engine.md).

        Option(subtext=("In stock: ", Sum("qty", model=StockMove, link="item", where={"warehouse": Ref("warehouse")}),
                        " ", F("stock_uom")))

    title and subtext are one part or a tuple of parts, joined as text. A Ref in where= reads the form the
    Link is on, so it is allowed only in a Link's own option=, not in Meta.option. hook(context, options, values)
    is trusted app code for what the parts can't say: it gets the options read for this person (dicts with id,
    title, subtext and record) and the Link's form values ({path: value}), and returns them with title or
    subtext changed."""
    __slots__ = ('title', 'subtext', 'hook')

    def __init__(self, *, title=None, subtext=None, hook=None):
        if title is None and subtext is None and hook is None:
            raise ValueError('Option() takes title=, subtext= or hook=')
        if hook is not None and not callable(hook):
            raise ValueError('Option(hook=) takes a function (context, options, values)')
        self.title, self.subtext, self.hook = _option_parts(title, 'title'), _option_parts(subtext, 'subtext'), hook

    def __repr__(self):
        return f'Option(title={self.title!r}, subtext={self.subtext!r}, hook={self.hook!r})'

    def refs(self):
        """The form paths the option reads (Ref values in where=)."""
        from .calc import Expr
        return tuple(dict.fromkeys(value.path for part in (*(self.title or ()), *(self.subtext or ())) if not isinstance(part, (str, Expr))
                                   for value in (part.where or {}).values() if isinstance(value, Ref)))


def _option_parts(value, what):
    if value is None:
        return None
    parts = value if isinstance(value, tuple) else (value,)
    if not parts or len(parts) > MAX_OPTION_PARTS:
        raise ValueError(f'Option {what}= takes 1 to {MAX_OPTION_PARTS} parts')
    from .calc import Expr
    for part in parts:
        if isinstance(part, str):
            if len(part) > MAX_OPTION_LITERAL:
                raise ValueError(f'Option {what}: literal text has at most {MAX_OPTION_LITERAL} characters')
        elif isinstance(part, Expr):
            if part.op != 'ref' or part.meta.get('scope') != 'self' or part.meta['name'].count('.') > 1:
                raise ValueError(f'Option {what}: use F("field") or F("link.field"), not {part.op} expressions')
        elif getattr(part, 'function', None) in OPTION_FUNCTIONS and getattr(part, 'model', None) is not None:
            where = part.where or {}
            if not isinstance(where, dict) or any(not isinstance(key, str) or isinstance(item, (dict, list, tuple, set))
                                                 for key, item in where.items()):
                raise ValueError(f'Option {what}: where= of {type(part).__name__} is {{field: value or Ref("path")}}')
        else:
            raise ValueError(f'Option {what}: use text, F("field"), or Count, Sum, Min or Max with model= and link=, not {part!r}')
    return parts


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
                 'visible_when', 'visible_if', 'required_if', 'read_only_if')


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
        @functools.wraps(method)
        def run(self, *args, **kwargs):
            # A record built by hand or read through a Session has no context to check the action with.
            if not hasattr(self, '_definition'):
                raise ValueError('Get the record through a context to run its actions')
            return method(self, *args, **kwargs)
        run.__action__ = {'label': label or method.__name__.replace('_', ' ').title(), 'roles': roles,
                          'inputs': inputs, 'available_when': available_when}
        return run
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
    puts the step in the released set: entering the set runs @release, leaving it @release.exit. `final` means
    no rule leaves it. `release` and `final` imply `lock`. Only `label` and `color` may change on an instance;
    the behaviour is the model's."""
    def __init__(self, key, label=None, *, color=None, lock=None, release=False, final=False):
        self.key = _key(key, 'A step key')
        self.label = _text(label, 'A step label') or key.replace('_', ' ').capitalize()
        if color is not None and color not in COLORS:
            raise ValueError(f'Step {key!r}: color is one of ' + ', '.join(COLORS))
        self.color = color
        self.release, self.final = _flag(release, 'release'), _flag(final, 'final')
        if lock is False and (self.release or self.final):
            raise ValueError(f'Step {key!r}: a release or final step is locked; leave out lock=False')
        self.lock = self.release or self.final if lock is None else _flag(lock, 'lock')

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


class Move:
    """One move: State.Move("draft", "issued", "Issue", by=("editor",), when=F("total") > 0, remark="optional").

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


# The framework's own modules call a move a Rule (rules=, check_rules); apps write State.Move.
Rule = Move


class _RenamedMember:
    """A class attribute renamed in 0.4.x: still returns the same object, with a DeprecationWarning once."""
    def __init__(self, old, new, value):
        self.old, self.new, self.value = old, new, value

    def __get__(self, instance, owner=None):
        from .fields import renamed_warning
        renamed_warning(self.old, self.new)
        return self.value


# The name every State field has; it shows as "Status" unless it declares a label.
STATE_FIELD = 'docstate'


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


State.Move, State.Level, State.Remark = Move, Level, Remark
# Named State.Rule before 0.4.x, which read like evlet.Rule (a permission rule).
State.Rule = _RenamedMember('State.Rule', 'State.Move', Move)


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


class Extend:
    """Additions one app makes to a model of another (docs/model-scripts.md#extending-a-model-from-another-app).

        class AcmeQuotation(Extend):
            @validate
            def po_needed(self): ...

            class Meta:
                model = "quotation"
                script = "acme_quotation.js"
                on_change = {"customer": "check_blacklist"}
                on_open = "opened"

    Additive only: @validate checks that run after the model's own, on_change hooks on existing fields ("field" or
    "table.field") and its own script. It cannot add, remove or change fields, formulas, states, actions or the
    model's hooks. Register it with AppDefinition(extensions=(...)); the registry checks the names at start-up."""

    OPTIONS = ('model', 'script', 'on_change', 'on_open')

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        meta = vars(cls).get('Meta')
        if meta is None or not isinstance(getattr(meta, 'model', None), str):
            raise ValueError(f'{cls.__name__}: name the model it extends, Meta.model = "quotation"')
        unknown = sorted(key for key in vars(meta) if not key.startswith('_') and key not in Extend.OPTIONS)
        if unknown:
            raise ValueError(f'{cls.__name__}.Meta supports {", ".join(Extend.OPTIONS)}, not {", ".join(unknown)}')
        for name, value in vars(cls).items():
            if name.startswith('__') or name == 'Meta':
                continue
            if not (inspect.isfunction(value) and any(kind == 'validate' for kind, _ in getattr(value, '__evlet_hook__', ()))):
                raise ValueError(f'{cls.__name__}.{name}: an Extend adds only @validate checks and script hooks; it cannot add '
                                 'or change fields, formulas, states, actions or other methods')
        on_change = getattr(meta, 'on_change', {})
        if not isinstance(on_change, dict) or not all(isinstance(key, str) for key in on_change):
            raise ValueError(f'{cls.__name__}.Meta.on_change maps field names to script methods, {{"customer": "check_blacklist"}}')
        from .fields import script_method
        for key, method in on_change.items():
            script_method(method, f'{cls.__name__}.Meta.on_change[{key!r}]')
        if getattr(meta, 'on_open', None) is not None:
            script_method(meta.on_open, f'{cls.__name__}.Meta.on_open')
        if (on_change or getattr(meta, 'on_open', None)) and getattr(meta, 'script', None) is None:
            raise ValueError(f'{cls.__name__}: its hooks name methods of a script; declare Meta.script')

    @classmethod
    def checks(cls):
        """Its @validate methods, in declaration order."""
        return tuple(value for value in vars(cls).values()
                     if inspect.isfunction(value) and any(kind == 'validate' for kind, _ in getattr(value, '__evlet_hook__', ())))
