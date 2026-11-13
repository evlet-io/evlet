"""The state manager: a document's states, the rules that move it between them, and its hooks.

    status = State(
        State.Step("draft", "Draft"),
        State.Step("issued", "Issued", color="green", lock=True),
        State.Step("void", "Void", color="rose", lock=True, final=True),
        rules=(State.Rule("draft", "issued", "Issue"), State.Rule("issued", "void", "Void", remark="required")),
    )

`State.Rule`, `State.Level` and `State.Remark` are this module's Rule, Level and Remark (evlet.Rule is the
permission rule).

Nothing moves without a rule. A rule is checked on the saved record, in the move's transaction: who may
use it (`by`), when it is offered (`when`) and the remark it takes. Four hooks run code on a document:
`@validate` on every save, `@state(...)` on entering a state, `@state.exit(...)` on leaving one and
`@release` when a release is made. See docs/states-actions.md.
"""

import inspect
import re
from types import MappingProxyType

from .auth import AccessError, has_role
from .fields import COLORS, MISSING, Select

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
    from .declarations import guard_expression
    return guard_expression(value, what)


# Declarations

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


# Hooks

def _hook(kind, states):
    for name in states:
        _key(name, 'A hook state')
    if kind in ('enter', 'exit') and not states:
        raise ValueError('@state and @state.exit name at least one state')

    def mark(method):
        if not inspect.isfunction(method) or len(inspect.signature(method).parameters) != 1:
            raise ValueError('State manager hooks are methods that take only self')
        method.__evlet_hook__ = (*getattr(method, '__evlet_hook__', ()), (kind, tuple(states)))
        return method
    return mark


def validate(method):
    """@validate: checks on every save (insert, update, move, restore) and preview, read only. On a document,
    a Table row or a form (Dialog, Slip, Report filters); a row's errors are keyed under its row."""
    return _hook('validate', ())(method)


def release(method):
    """@release: runs when a release is made, with self.release.number and self.release.previous."""
    return _hook('release', ())(method)


def state(*states):
    """@state("approved", ...): runs when the document enters one of the states."""
    return _hook('enter', states)


state.exit = lambda *states: _hook('exit', states)
state.exit.__doc__ = '@state.exit("issued", ...): runs when the document leaves one of the states.'


def configure(model):
    """Collect a model's hooks in class order, a subclass's method replacing its base's by name."""
    hooks = []
    for name in dict.fromkeys(name for cls in reversed(model.__mro__) for name in vars(cls)):
        for kind, states in getattr(getattr(model, name, None), '__evlet_hook__', ()):
            hooks.append((kind, states, name))
    field = model.__declared_fields__.get(model.__state__) if model.__state__ else None
    for kind, states, name in hooks:
        if kind in ('enter', 'exit', 'release') and field is None:
            raise ValueError(f'{model.__name__}.{name}: @state, @state.exit and @release need a State on the model')
        unknown = set(states) - set(field.choices if field else ())
        if unknown:
            raise ValueError(f'{model.__name__}.{name} names an unknown state: ' + ', '.join(sorted(unknown)))
    model.__hooks__ = tuple(hooks)
    if field is not None:
        for rule in field.rules:
            for name in rule.ask:
                if name not in model.__declared_fields__ or name == model.__state__:
                    raise ValueError(f'{model.__name__}: rule {rule.key!r} asks for {name!r}, which is not a field of the model')


def check_item(item):
    """Run the @validate hooks of one draft (a document, a Table row or a form) with it read only, as the last
    check of a settle; returns its errors. Assigning raises."""
    from .models import ValidationError
    names = [name for kind, _, name in getattr(item._definition, '__hooks__', ()) if kind == 'validate']
    if not names:
        return {}
    readonly = item._readonly
    object.__setattr__(item, '_readonly', True)
    try:
        for name in names:
            getattr(item._definition, name)(item)
    except ValidationError as exc:
        return dict(exc.errors)
    except ValueError as exc:
        return {'_form': str(exc)}
    except AttributeError as exc:
        if 'read only' not in str(exc):
            raise
        return {'_form': f'{item._definition.__name__}: a @validate check is read only; set values in @state hooks or derive()'}
    finally:
        object.__setattr__(item, '_readonly', readonly)
    return {}


def run_hooks(context, draft, kind, state_name=None):
    """Run @state, @state.exit or @release hooks on the root draft, with self.context's transaction session
    open, as trusted code. A ValueError refuses the save."""
    from .documents import handler_context
    from .models import ValidationError
    from .permissions import trusted
    model = draft._definition
    names = [name for k, states, name in model.__hooks__ if k == kind and (not states or state_name in states)]
    if not names:
        return
    try:
        with trusted(), handler_context(context, draft):
            for name in names:
                getattr(model, name)(draft)
    except ValidationError:
        raise
    except ValueError as exc:
        raise ValidationError({'_form': str(exc)}) from None


def bind(draft, before):
    """Give hooks self.is_new and self.before (the document as last saved, read only; None on insert)."""
    from .documents import freeze_draft
    if before is not None:
        freeze_draft(before)
    object.__setattr__(draft, 'is_new', before is None)
    object.__setattr__(draft, 'before', before)


# Rules on a registry

def rules_of(context, model):
    """The rules in force for a model."""
    return model.__declared_fields__[model.__state__].rules


def conditions(model):
    """Every finite condition of a model's rules, keyed for the guard compiler: "rule:<key>", "rule:<key>:remark"
    and "rule:<key>:level:<index>". Action guards are keyed by method name, so the two never meet."""
    field = model.__declared_fields__[model.__state__]
    result = {}
    for rule in field.rules:
        if rule.when is not None:
            result['rule:' + rule.key] = rule.when
        if rule.remark is not None and rule.remark.required_when is not None:
            result[f'rule:{rule.key}:remark'] = rule.remark.required_when
        for index, level in enumerate(rule.levels):
            if level.when is not None:
                result[f'rule:{rule.key}:level:{index}'] = level.when
    return result


def allowed(user, roles, users, values):
    """Whether this user is named by roles or holds one of the user fields on the saved record."""
    if has_role(user, roles):
        return True
    return any(values.get(name) not in (None, '') and values.get(name) in (user.id, user.username) for name in users)


# Locks and ordinary writes

def state_write(model, scalars, current):
    """Ordinary writes may create a record in the default state and never set it afterwards."""
    from .models import ValidationError
    name = model.__state__
    if name is None or name not in scalars:
        return
    field, value = model.__declared_fields__[name], scalars[name]
    if value == (field.default if current is None else current._values.get(name)):
        del scalars[name]
        return
    if current is None:
        raise ValidationError({name: f'A new record starts as {field.default}; use its rules to change it'})
    raise ValidationError({name: 'This changes only through its rules'})


def locked_state(model, record_values):
    """The saved state's name when it is locked, or None."""
    name = model.__state__
    if name is None:
        return None
    value = record_values.get(name)
    return value if value in model.__declared_fields__[name].locked else None


def state_shown(context, model):
    """Whether the caller may read the State field; messages name the state only then."""
    return model.__state__ in context._business.policy.fields(context.user, model)


def check_state_lock(context, draft, before):
    """Refuse an ordinary write that changes a record in a locked state or any of its Table rows.

    Fields declared versioned=False may still change. Adding, removing or reordering rows at any depth is
    a change. Values are compared as the caller submitted them, before calculations."""
    from .models import ValidationError
    state_name = locked_state(draft._definition, before._values)
    if state_name is None:
        return
    message = f'This record is {state_name}, so it is locked' if state_shown(context, draft._definition) else 'This record is locked'
    errors = {}

    def compare(item, old, prefix):
        fields = item._definition.__declared_fields__
        for name in item._submitted:
            field = fields.get(name)
            if field is None or not field.versioned:
                continue
            value = item._values.get(name)
            try:
                value = None if value is None else field.validate(value)
            except ValueError:
                pass
            if value != old._values.get(name):
                errors[prefix + name] = message
        for table, rows in item._rows.items():
            saved = old._rows.get(table, [])
            if [row.id for row in rows] != [row.id for row in saved] or \
                    [(s.id, s.label, s.at) for s in item._sections.get(table, [])] != [(s.id, s.label, s.at) for s in old._sections.get(table, [])]:
                errors[prefix + table] = message
            by_id = {row.id: row for row in saved}
            for row in rows:
                if row.id in by_id:
                    compare(row, by_id[row.id], prefix + table + '.' + row.key + '.')
    compare(draft, before, '')
    if errors:
        raise ValidationError(errors)


def locked_message(context, model, record, what):
    state_name = locked_state(model, record._values)
    if state_name is not None:
        named = f'is {state_name}, so it ' if state_shown(context, model) else ''
        raise AccessError(409, f'This record {named}is locked{what}')


def check_delete(context, model, record):
    locked_message(context, model, record, ' and cannot be deleted')
    if releasing(model) and latest(context._tx.connection, context._business.registry.name_for(model), record.id) is not None:
        raise AccessError(409, 'This record has a release, so it cannot be deleted. Move it to a final state instead.')
    from .effects import ever_posted
    if ever_posted(context._tx.connection, context._business.registry, model, record.id):
        # Its effect rows, reversed or not, name it as their source, and its number stays used.
        raise AccessError(409, 'This record has posted before, so it cannot be deleted. Move it to a final state instead.')


def check_attachments(context, model, record):
    """Adding or removing attachments is an ordinary write, refused while the record is locked."""
    locked_message(context, model, record, ', so its attachments cannot change')


def check_recalculate(context, model, before):
    if before is not None and locked_state(model, before._values) is not None:
        raise AccessError(409, 'This record is locked, so it keeps its saved results and cannot be recalculated; '
                               'use its rules or actions to change it')


def keep_saved(draft, before):
    """After an ordinary write on a locked record: every field except versioned=False ones keeps its saved value."""
    from .documents import prior_versions, walk
    prior = prior_versions(before, draft)
    for _, item in walk(draft):
        old = prior(item)
        if old is not None:
            for name, field in item._definition.__declared_fields__.items():
                if field.versioned:
                    item._values[name] = old._values.get(name)


# Moves

def find_rule(context, model, key):
    return next((rule for rule in rules_of(context, model) if rule.key == key), None)


def clean_remark(remark):
    from .models import ValidationError
    if remark is None or remark == '':
        return None
    if not isinstance(remark, str) or '\x00' in remark or len(remark.strip()) > REMARK_LENGTH:
        raise ValidationError({'remark': f'Write a remark of at most {REMARK_LENGTH} characters'})
    return remark.strip() or None


def remark_required(context, guards, model, rule):
    if rule.remark is None or rule.remark.mode == 'required':
        return rule.remark is not None
    guard = context._business.registry.guards.get(model, {}).get(f'rule:{rule.key}:remark')
    outcome = None if guard is None else guards.result(guard)
    return outcome is not None and outcome.state == 'ready' and outcome.value is True


def begin_move(context, model, draft, before, key, remark):
    """Check a move on the saved record and return (rule, remark, level); the pipeline sets the new state after
    the @state.exit hooks. `level` is the approval level this move approves (an index), or None.

    A role that cannot read the State gets one answer for every refused move, so it cannot learn which
    states and rules exist."""
    if model.__state__ is None:
        raise AccessError(404, 'This record has no moves')
    if state_shown(context, model):
        return checked_move(context, model, draft, before, key, remark)
    try:
        return checked_move(context, model, draft, before, key, remark)
    except AccessError:
        raise AccessError(409, 'This record has no such move now') from None


def checked_move(context, model, draft, before, key, remark):
    from .guards import Guards, check_guard
    from .models import ValidationError
    rule = find_rule(context, model, key) if isinstance(key, str) else None
    if rule is None:
        raise AccessError(404, 'Move not found')
    current = before._values.get(model.__state__)
    if current in model.__declared_fields__[model.__state__].final:
        raise AccessError(409, f'This record is {current}, which is final; it has no moves')
    if current not in rule.sources:
        raise AccessError(409, f'This record cannot move from {current} to {rule.to}')
    guards = Guards(context, model, before._values, lambda: before)
    level = who_may(context, model, before, rule, guards)
    if level is False:
        raise AccessError(403, f'{rule.label} is made by the application only' if rule.system else f'Your role cannot use {rule.label}')
    check_guard(context, guards, context._business.registry.guards.get(model, {}).get('rule:' + rule.key), rule.label, 'move')
    remark = clean_remark(remark)
    if remark is not None and rule.remark is None:
        raise ValidationError({'remark': f'{rule.label} takes no remark'})
    if remark is None and remark_required(context, guards, model, rule):
        raise ValidationError({'remark': f'{rule.label} needs a remark'})
    return rule, remark, level


def moved(context, model, record, previous, rule, remark, level, before, after, released=None):
    """The Journal's `moved` entry, with the rule key, the remark, the level it approved, the levels that apply
    in the new state (see entering) and anything else the move changed."""
    from . import journal
    context.__dict__.get('_stays', {}).pop(record.id, None)
    if journal.journals(context, model):
        data = journal.tree_changes(context._business, model, before, after, skip=(model.__state__,))
        # A move that releases vN builds on v(N-1); it names the release it made in data["release"].
        base = (released - 1 if released else None) if released is not None else \
            changes_base(context._tx.connection, context._business.registry, model, record.id, data)
        journal.append(context._business, context._tx, context.user, model, record.id, 'moved',
                       {**data, 'from': previous, 'to': rule.to, 'move': rule.label, **entering(context, model, record),
                        **({'release': released} if released is not None else {})}, rule=rule.key, remark=remark, level=level, base=base)


# Approvals: a rule with levels moves once its last level that applies is approved

STAY = 200  # Approvals one stay in a state may hold before its entry is out of reach (then levels are decided now).


def entering(context, model, record):
    """{"levels": {rule key: [level indexes that apply]}} for the approval rules out of the record's (new) state,
    decided on the saved record as it enters the state; {} when no approval rule leaves it."""
    from .guards import Guards
    if model.__state__ is None:
        return {}
    state_name = record._values.get(model.__state__)
    rules = [rule for rule in rules_of(context, model) if rule.levels and state_name in rule.sources]
    if not rules:
        return {}
    from .documents import load_draft
    guards = Guards(context, model, record._values, lambda: load_draft(context, model, record.id, {}, preview=True)[0])
    compiled = context._business.registry.guards.get(model, {})

    def applies(guard):
        if guard is None:
            return True
        outcome = guards.result(guard)
        return outcome.state == 'ready' and outcome.value is True
    return {'levels': {rule.key: [index for index in range(len(rule.levels)) if applies(compiled.get(f'rule:{rule.key}:level:{index}'))]
                       for rule in rules}}


def stay(context, model, record):
    """(approvals, levels) of the record's stay in its state: the `approved` entries since the entry that brought
    it there (approvals expire when it leaves) and the levels that entry logged; one statement per record."""
    import json
    from . import qb
    cache = context.__dict__.setdefault('_stays', {})
    if model not in context._business.journals:
        return [], None
    if record.id not in cache:
        rows = context._tx.connection.run(qb.select('evlet_journal', 'kind', 'actor', 'level', 'rule', 'data')
                                          .where(qb.in_('kind', ('created', 'moved', 'restored', 'approved')),
                                                 model=context._business.registry.name_for(model), record_id=record.id)
                                          .order_by('-id').limit(STAY)).all()
        approvals = []
        for row in rows:
            if row['kind'] != 'approved':
                cache[record.id] = approvals, json.loads(row['data']).get('levels')
                break
            approvals.append(row)
        else:
            cache[record.id] = approvals, None
    return cache[record.id]


def waiting(context, model, record, rule):
    """(index of the level waiting or None, approvals of this rule in this stay)."""
    approvals, levels = stay(context, model, record)
    applicable = (levels or {}).get(rule.key)
    if applicable is None:
        applicable = entering(context, model, record).get('levels', {}).get(rule.key, [])
    mine = [row for row in approvals if row['rule'] == rule.key]
    done = {row['level'] for row in mine}
    return next((index for index in applicable if index not in done), None), mine


def may_approve(context, model, record, rule, index, approvals):
    """Whether the user may approve this level: named by it, approved no other level of this rule in this stay,
    and not the record's creator unless the rule says self_approve=True."""
    from .audit import actor_id
    from .journal import actor_of
    level, user = rule.levels[index], context.user
    if not allowed(user, level.roles, level.users, record._values) or actor_of(user) in {row['actor'] for row in approvals}:
        return False
    if rule.self_approve:
        return True
    from . import qb
    creator = context._tx.connection.run(qb.select(context._business.registry.name_for(model), 'evlet_created_by').where(id=record.id)).value()
    return creator is None or creator != actor_id(user)


def who_may(context, model, record, rule, guards=None):
    """False when this user may not use the rule on the saved record now; otherwise the approval level it approves
    (an index) or None. An approval with no level that applies is an ordinary move for those who may change the
    record. A reject rule is for the approvers of the level waiting on an approval rule out of the same state."""
    from .permissions import CALLER
    if rule.system:
        # Only trusted app code (a job, an action or a hook) uses a rule with by=(); a caller never does.
        return False if CALLER.get() else None
    if rule.levels:
        index, approvals = waiting(context, model, record, rule)
        return index if index is None or may_approve(context, model, record, rule, index, approvals) else False
    if rule.reject:
        current = record._values.get(model.__state__)
        open_levels = [(other, *waiting(context, model, record, other)) for other in rules_of(context, model)
                       if other.levels and current in other.sources]
        open_levels = [item for item in open_levels if item[1] is not None]
        if open_levels:
            return None if any(may_approve(context, model, record, *item) for item in open_levels) else False
    return None if allowed(context.user, rule.roles, rule.users, record._values) else False


def approve(context, model, record, rule, level, remark, values):
    """Record the approval of one level that is not the last: an `approved` entry, and no change to the record."""
    from . import journal
    from .models import ValidationError
    if values:
        raise ValidationError({'_form': f'{rule.label} saves values with its last approval'})
    if not journal.journals(context, model):
        raise ValidationError({'_form': 'Approval levels need the journal'})
    context.__dict__.get('_stays', {}).pop(record.id, None)
    journal.append(context._business, context._tx, context.user, model, record.id, 'approved',
                   {'from': record._values.get(model.__state__), 'move': rule.label, 'pending': rule.levels[level].pending},
                   rule=rule.key, remark=remark, level=level)
    context._business.changed(context._tx, model, record.id)
    return context._business.detail(context._tx, context.user, context._tx.get(model, record.id), row_limit=None)


def last_level(context, model, record, rule, level):
    """Whether approving `level` completes the rule (no later level applies)."""
    levels = stay(context, model, record)[1] or entering(context, model, record).get('levels', {})
    return level is None or level == levels.get(rule.key, [level])[-1]


def awaiting(context, model):
    """Ids of the records whose waiting approval level this user may approve now ("Awaiting my approval")."""
    from . import qb
    from .permissions import CHUNK
    rules = [rule for rule in rules_of(context, model) if rule.levels] if model.__state__ else []
    states = sorted({source for rule in rules for source in rule.sources})
    ids, last = [], 0
    while states:
        batch = [model._from_row(row) for row in context._tx.connection.run(
            qb.select(context._business.registry.name_for(model)).where(qb.in_(model.__state__, states), qb.gt('id', last))
            .order_by('id').limit(CHUNK))]
        if not batch:
            break
        last = batch[-1].id
        ids += [record.id for record in batch if any(
            record._values.get(model.__state__) in rule.sources and who_may(context, model, record, rule) is not False
            and waiting(context, model, record, rule)[0] is not None for rule in rules)]
    return ids


# Values a move asks for

class _StateOnly:
    """An evaluation scope that knows only the State, for deciding rules per state without a record."""
    def __init__(self, name, value):
        self.name, self.value, self.document, self.lookups, self.misses, self.row, self.at = name, value, self, {}, set(), None, 'move'

    def read(self, scope, field):
        from .calc import pending, ready
        return ready(self.value) if scope == 'self' and field == self.name else pending('COMPUTE_INPUT_MISSING', ['move'])

    def today_result(self):
        from .calc import pending
        return pending('COMPUTE_INPUT_MISSING', ['move'])

    def causes(self):
        return ['move']


def move_asks(business, model, rule):
    """The fields a move may carry values for: `ask=` and the root fields whose applies_when or
    required_when reads the State and becomes true (or depends on other values) in the target state while
    it is false in a source state. Decided once per rule, in field order."""
    cache = business.__dict__.setdefault('_move_asks', {})
    key = (model, rule)
    if key not in cache:
        from .calc import Budget, CalcError, _Context, compile_expression, evaluate, lift
        name = model.__state__
        tables = {child.name: child.model for child in business.registry.children if child.parent is model}

        def decided(ir, value):
            try:
                outcome = evaluate(ir, _StateOnly(name, value), Budget())
            except (CalcError, AttributeError, TypeError):
                return None
            return outcome.value if outcome.state == 'ready' else None
        asks = []
        for field_name, field in model.__declared_fields__.items():
            if field_name in rule.ask:
                asks.append(field_name)
                continue
            for kind in ('applies_when', 'required_when'):
                expression = getattr(field, kind, None)
                if expression is None or field_name == name:
                    continue
                ir, _, reads = compile_expression(lift(expression), _Context(model, (), None, tables, registry=business.registry), f'{field_name}_{kind}')
                if any(scope == 'self' and read == name for scope, _, read in reads) and decided(ir, rule.to) is not False \
                        and any(decided(ir, source) is False for source in rule.sources):
                    asks.append(field_name)
                    break
        cache[key] = tuple(asks)
    return cache[key]


def writable_asks(business, user, model, rule):
    writable = business.policy.fields(user, model, write=True)
    return [name for name in move_asks(business, model, rule) if name in writable]


def check_move_values(context, model, rule, values):
    """Refuse values the checked move does not ask this role for, before any of them is applied.

    A key that names no field this role can read (unknown or hidden) gets one form error, so the answer
    never confirms a hidden field."""
    from .models import ValidationError
    business = context._business
    wanted, readable = set(writable_asks(business, context.user, model, rule)), business.policy.fields(context.user, model)
    errors = {key if key in readable else '_form': 'This move does not take this value' for key in values if key not in wanted}
    if errors:
        raise ValidationError(errors)


# Schema and availability

def remark_schema(rule):
    return None if rule.remark is None else rule.remark.public()


def state_schema(business, user, model):
    """The one state schema for the UI and the API, or None when the role cannot read the State field."""
    from .guards import readable
    name = model.__state__
    if name is None or name not in business.policy.fields(user, model):
        return None
    field, guards = model.__declared_fields__[name], business.registry.guards.get(model, {})
    rules = []
    for rule in rules_of(None, model):
        if rule.system:
            continue  # Only the application makes it, so no caller is told about it.
        guard = guards.get('rule:' + rule.key)
        asks = writable_asks(business, user, model, rule)
        rules.append({'key': rule.key, 'from': list(rule.sources), 'to': rule.to, 'label': rule.label, 'by': list(rule.roles),
                      'guarded': guard is not None, 'when': guard.ir if guard is not None and readable(business.policy, user, guard) else None,
                      'primary': rule.primary, 'reject': rule.reject, 'remark': remark_schema(rule), **({'asks': asks} if asks else {}),
                      **({'levels': [{'by': list(level.roles), 'pending': level.pending, 'guarded': f'rule:{rule.key}:level:{index}' in guards}
                                     for index, level in enumerate(rule.levels)], 'self_approve': rule.self_approve} if rule.levels else {})})
    return {'field': name, 'default': field.default, 'show': field.show, 'steps': [step.public() for step in field.steps.values()],
            'rules': rules}


def moves_available(business, context, model, record, guards_now, updatable, locks):
    """`_moves` and `_locked` for a detail: rules out of the current state, with a plain-English reason when
    one is blocked. `primary` marks a rule declared primary=True; without one, screens use the first allowed. Listed for every role that can read the State; reasons over unreadable inputs are generic."""
    from . import periods
    from .guards import blocked_reason, readable, saved_locks
    policy, user = business.policy, context.user
    field = model.__declared_fields__[model.__state__]
    current = record._values.get(model.__state__)
    guards = business.registry.guards.get(model, {})
    # What blocks every move of this record: a model callers only read, or a date in a closed period.
    read_only = business.registry.api.get(model) == 'read'
    closed = None if read_only or getattr(context._tx, '_periods_unlocked', False) else periods.closed(context._tx, model, record._values)
    moves, taken = [], set()
    for rule in rules_of(context, model):
        if current not in rule.sources or rule.system:
            continue
        reason, extra = None, {}
        if rule.levels:
            # An approval shows the level waiting and whether this user may approve it.
            index, approvals = waiting(context, model, record, rule)
            if index is not None:
                can = may_approve(context, model, record, rule, index, approvals)
                extra = {'level': index + 1, 'pending': rule.levels[index].pending, 'can_approve': can}
                reason = None if can else 'Waiting for another approver'
        if reason is None and who_may(context, model, record, rule) is False:
            reason = 'Only the approvers of the waiting level can use this move' if rule.reject else 'Your role cannot use this move'
        elif reason is None and not updatable or read_only:
            reason = 'You cannot change this record'
        elif closed is not None:
            reason = 'This record is in a closed period'
        elif 'rule:' + rule.key in guards:
            guard = guards['rule:' + rule.key]
            reason = blocked_reason(guards_now.result(guard), readable(policy, user, guard), 'move')
        # Of several rules to one state, the first that this user may use now is offered.
        if rule.to in taken:
            continue
        if reason is None:
            taken.add(rule.to)
        asks = writable_asks(business, user, model, rule)
        # A field locked on this record cannot take a value, so the move does not ask for it.
        if any(model.__declared_fields__[ask].locked_when is not None for ask in asks):
            if not locks:
                locks.append(saved_locks(context, model, guards_now.load))
            asks = [ask for ask in asks if ask not in locks[0]]
        remark = None if rule.remark is None else {**rule.remark.public(), 'required': remark_required(context, guards_now, model, rule)}
        moves.append({'key': rule.key, 'to': rule.to, 'label': rule.label, 'allowed': reason is None, **({'primary': True} if rule.primary else {}),
                      **({'reason': reason} if reason else {}), **({'asks': asks} if asks else {}),
                      **({'remark': remark} if remark else {}), **extra})
    return {'_moves': moves, '_locked': current in field.locked}


# Releases: numbered versions of a document, with lazy snapshots

def releases_table():
    from .adapter import Column, TableDefinition
    # A release row (snapshot 0) keeps number, state, hash, actor, time and {approvals, remark}; a snapshot row
    # (snapshot 1) is appended once, before the document first diverges from that release. Nothing is updated.
    return TableDefinition('evlet_releases', (
        Column('model', 'TEXT', required=True), Column('record_id', 'INTEGER', required=True), Column('number', 'INTEGER', required=True),
        Column('snapshot', 'INTEGER', required=True), Column('at', 'TEXT', required=True), Column('actor', 'TEXT', required=True),
        Column('state', 'TEXT', required=True), Column('hash', 'TEXT', required=True), Column('drifted', 'INTEGER', required=True, default=0),
        Column('data', 'TEXT', required=True)), primary_key=('model', 'record_id', 'number', 'snapshot'))


def ensure_releases(connection):
    connection.ensure_table(releases_table())
    connection.make_append_only('evlet_releases', 'Releases are never changed')


def releasing(model):
    """Whether a model has a release step."""
    return model.__state__ is not None and bool(model.__declared_fields__[model.__state__].releases)


def tree(item):
    """A saved document tree (a Draft, or a record with `_rows`) as JSON values: every stored field, at every depth."""
    definition = item._definition if hasattr(item, '_definition') else type(item)
    result = {'id': str(item.id)}
    for name, field in definition.__fields__.items():
        if name not in getattr(definition, '__internal_fields__', ()):
            result[name] = field.serialize(item._values.get(name))
    result['children'] = {name: [tree(row) for row in rows] for name, rows in getattr(item, '_rows', {}).items()}
    return result


def versioned(model, data, registry):
    """The content of a document tree: versioned fields only (not the State, not versioned=False, not excluded)."""
    tables = {child.name: child.model for child in registry.children if child.parent is model}
    result = {key: value for key, value in data.items() if key in ('id', 'children') or (
        key in model.__fields__ and model.__fields__[key].versioned and model.__fields__[key].tracking != 'exclude' and key != model.__state__)}
    result['children'] = {name: [versioned(tables[name], row, registry) for row in rows]
                          for name, rows in data.get('children', {}).items() if name in tables}
    return result


def digest(model, data, registry):
    import hashlib
    from .journal import canonical
    return hashlib.sha256(canonical(versioned(model, data, registry)).encode()).hexdigest()


def latest(connection, name, record_id):
    """The latest release of a record ({number, hash, state, snapshotted}) or None, in one statement. Once it has its
    snapshot, the hash is the snapshot's: the content the record had when it first diverged."""
    from . import qb
    row = connection.run(qb.select('evlet_releases', 'number', 'snapshot', 'hash', 'state').where(model=name, record_id=record_id)
                         .order_by('-number', '-snapshot').limit(1)).one()
    return None if row is None else {'number': row['number'], 'hash': row['hash'], 'state': row['state'], 'snapshotted': bool(row['snapshot'])}


def store(connection, registry, model, record_id, number, *, snapshot, state, data, hash, actor, drifted=False):
    import json
    from . import journal, qb
    connection.run(qb.insert('evlet_releases', model=registry.name_for(model), record_id=record_id, number=number, snapshot=int(snapshot),
                             at=journal.now(), actor=actor, state=state, hash=hash, drifted=int(drifted),
                             data=json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)))


def keep(connection, registry, model, record_id, data, actor):
    """Save the snapshot of the latest release, once, before the record first diverges from it. A hash that differs
    from the release's means the row changed outside the engine: the snapshot is kept, marked drifted, and logged."""
    import logging
    from . import journal
    if not releasing(model):
        return
    last = latest(connection, registry.name_for(model), record_id)
    if last is None or last['snapshotted']:
        return
    drifted = digest(model, data, registry) != last['hash']
    if drifted:
        logging.getLogger('evlet.statemanager').warning('%s %s changed outside the engine since v%s', registry.name_for(model), record_id, last['number'])
    store(connection, registry, model, record_id, last['number'], snapshot=True, state=last['state'], data=versioned(model, data, registry),
          hash=digest(model, data, registry), actor=actor, drifted=drifted)
    if model in registry.journals:
        journal.insert(connection, registry, model, record_id, 'snapshot', actor, {'number': last['number'], 'drifted': drifted},
                       base=last['number'])


def diverge(context, model, before, draft, leaving=False):
    """Snapshot the latest release before this write when it leaves a release step or changes the content."""
    from .journal import actor_of
    registry = context._business.registry
    if before is None or not releasing(model):
        return
    last = latest(context._tx.connection, registry.name_for(model), before.id)
    if last is not None and not last['snapshotted'] and (leaving or digest(model, tree(draft), registry) != last['hash']):
        keep(context._tx.connection, registry, model, before.id, tree(before), actor_of(context.user))


def prepare_release(context, model, draft, manual=False):
    """Before the write: when the content changed since the last release (or there is none), run @release with
    self.release.number and .previous and return the number; otherwise None."""
    import json
    from types import SimpleNamespace
    from . import qb
    registry = context._business.registry
    name = registry.name_for(model)
    last = latest(context._tx.connection, name, draft.id) if draft.id else None
    if last is not None and digest(model, tree(draft), registry) == last['hash']:
        return None
    previous = None
    if last is not None:
        row = context._tx.connection.run(qb.select('evlet_releases', 'data').where(model=name, record_id=draft.id, number=last['number'], snapshot=1)).one()
        previous = json.loads(row[0]) if row else None
    number = 0 if last is None else last['number'] + 1
    object.__setattr__(draft, 'release', SimpleNamespace(number=number, previous=previous))
    run_hooks(context, draft, 'release')
    return number


def finish_release(context, model, record, draft, number, rule=None, remark=None):
    """After the write: store release `number` and its `released` journal entry."""
    from . import journal
    registry = context._business.registry
    approvals = [{'actor': row['actor'], 'level': row['level']} for row in stay(context, model, record)[0]] if rule is not None else []
    store(context._tx.connection, registry, model, record.id, number, snapshot=False, state=record._values.get(model.__state__),
          data={'approvals': approvals, 'remark': remark}, hash=digest(model, tree(draft), registry), actor=journal.actor_of(context.user))
    # A move's release is its `moved` entry (with base = the number); the Release button writes its own entry.
    if rule is None and journal.journals(context, model):
        journal.append(context._business, context._tx, context.user, model, record.id, 'released', {'number': number},
                       remark=remark, base=number)


# Versions: the release list, one version (field permissions applied) and a comparison

def saved(context, model, record_id):
    from .documents import load_draft
    return load_draft(context, model, record_id, {}, preview=True)[0]


def version_of(context, model, record):
    """`_version` for a detail: {number, released, state}; number is None before the first release. Two indexed
    reads: the latest release and whether the journal changed versioned content since."""
    from . import qb
    name = context._business.registry.name_for(model)
    last = latest(context._tx.connection, name, record.id)
    if last is None:
        return {'number': None, 'released': False, 'state': None}
    if model not in context._business.journals:
        # Without a journal (Meta.journal = None) the content is compared with the release's hash.
        return {'number': last['number'], 'state': last['state'],
                'released': digest(model, tree(saved(context, model, record.id)), context._business.registry) == last['hash']}
    # Modified: the journal has an entry that changed versioned content after this release (see changes_base).
    changed = context._tx.connection.run(qb.select('evlet_journal', 'id').where(
        qb.in_('kind', ('changed', 'moved', 'restored', 'recomputed')), model=name, record_id=record.id, base=last['number']).limit(1)).one()
    return {'number': last['number'], 'released': changed is None, 'state': last['state']}


def changes_base(connection, registry, model, record_id, data):
    """`base` of a journal entry: the latest release's number when the entry changes versioned content (a field
    that is not versioned=False, or Table rows), else None. "Changes after v2" are the entries with base 2."""
    fields = model.__fields__
    if not releasing(model) or not data.get('rows') and not any(
            name in fields and fields[name].versioned and name != model.__state__ for name in data.get('fields', {})):
        return None
    last = latest(connection, registry.name_for(model), record_id)
    return None if last is None else last['number']


def readable_version(context, model, record_id):
    if not releasing(model):
        raise AccessError(404, 'This record has no versions')
    if model.__state__ not in context._business.policy.fields(context.user, model):
        raise AccessError(404, 'This record has no versions')
    context._business.policy.record(context._tx, context.user, model, record_id)


def versions(context, model, record_id):
    """Every release of the record, newest first, as {items: [{number, state, at, actor, remark, approvals}]}."""
    import json
    from . import qb
    readable_version(context, model, record_id)
    rows = context._tx.connection.run(qb.select('evlet_releases', 'number', 'state', 'at', 'actor', 'data')
                                      .where(model=context._business.registry.name_for(model), record_id=record_id, snapshot=0)
                                      .order_by('-number')).all()
    items = []
    for row in rows:
        data = json.loads(row['data'])
        items.append({'number': row['number'], 'state': row['state'], 'at': row['at'],
                      'actor': None if row['actor'] == 'engine' else int(row['actor']),
                      'remark': data.get('remark'), 'approvals': data.get('approvals', [])})
    return {'items': items}


def content_of(context, model, record_id, number):
    """The versioned content of release `number`: its snapshot, or the live record while it still is that release."""
    import json
    from . import qb
    name = context._business.registry.name_for(model)
    row = context._tx.connection.run(qb.select('evlet_releases', 'data').where(model=name, record_id=record_id, number=number, snapshot=1)).one()
    if row is not None:
        return json.loads(row[0])
    last = latest(context._tx.connection, name, record_id)
    if last is None or last['number'] != number:
        raise AccessError(404, 'Version not found')
    return versioned(model, tree(saved(context, model, record_id)), context._business.registry)


def shown_tree(context, model, data):
    """A version as this user may read it: today's field, Link and row permissions, also on rows deleted since."""
    from .identity import RecordId
    from .journal import Visibility, shown_value
    business, policy = context._business, context._business.policy
    visibility = Visibility(business, context._tx, context.user)

    def project(definition, item):
        allowed = policy.fields(context.user, definition)
        result = {'id': item['id']}
        for name, field in definition.__fields__.items():
            if name in allowed and name in item:
                result[name] = '[masked]' if field.tracking == 'mask' and item[name] is not None else \
                    shown_value(business, context.user, field, item[name], visibility)[0]
        result['children'] = {}
        for child in business.children.values():
            if child.parent is definition and policy.allows(context.user, child.model, 'read'):
                scoped = policy.rule(context.user, child.model).scope is not None
                result['children'][child.name] = [project(child.model, row) for row in item.get('children', {}).get(child.name, [])
                                                  if not scoped or visibility(child.model, RecordId(row['id']))]
        return result
    first = project(model, data)
    return project(model, data) if visibility.resolve() else first


def version(context, model, record_id, number):
    readable_version(context, model, record_id)
    return {'number': number, 'document': shown_tree(context, model, content_of(context, model, record_id, number))}


def compare(old, new):
    """Changed fields, and rows added, removed and changed (matched by row id), between two document trees."""
    fields = {key: {'old': old.get(key), 'new': new.get(key)} for key in sorted(set(old) | set(new))
              if key not in ('id', 'children') and old.get(key) != new.get(key)}
    rows = {}
    for name in sorted(set(old.get('children', {})) | set(new.get('children', {}))):
        left, right = old.get('children', {}).get(name, []), new.get('children', {}).get(name, [])
        before, after = {row['id']: row for row in left}, {row['id']: row for row in right}
        table = {'added': [row for row in right if row['id'] not in before], 'removed': [row for row in left if row['id'] not in after],
                 'updated': [{'id': row['id'], **delta} for row in right if row['id'] in before for delta in [compare(before[row['id']], row)] if delta]}
        table = {key: value for key, value in table.items() if value}
        if table:
            rows[name] = table
    return {**({'fields': fields} if fields else {}), **({'rows': rows} if rows else {})}


def version_diff(context, model, record_id, first, second):
    readable_version(context, model, record_id)
    old = shown_tree(context, model, content_of(context, model, record_id, first))
    current = versioned(model, tree(saved(context, model, record_id)), context._business.registry) if second == 'current' else None
    new = shown_tree(context, model, current if current is not None else content_of(context, model, record_id, second))
    return {'from': first, 'to': second, 'changes': compare(old, new)}


def route(business, scope, body, user, model, parts):
    """POST /api/{model}/{id}/release and .../restore ({version, remark}), GET .../versions, .../versions/{n} and .../versions/{a}/diff/{b or current}."""
    from .identity import path_id
    from .models import ValidationError, checked_body
    record_id = path_id(parts[3]) if len(parts) > 3 else None
    if record_id is None:
        raise AccessError(404, 'Record not found')
    if parts[4] in ('release', 'restore'):
        if len(parts) != 5 or scope['method'] != 'POST':
            raise AccessError(405, 'Method not allowed')
        checked_body(body, {'remark', 'version'} if parts[4] == 'restore' else {'remark'}, 'Send only a remark and, to restore, a version')
        with business.context(user) as context:
            record = business.policy.record(context._tx, user, model, record_id, 'update')
            if b'if-match' not in dict(scope.get('headers', [])):
                raise AccessError(428, 'A current If-Match revision is required')
            business.check_revision(scope, context._tx, user, record)
            result = context.release(model, record_id, remark=body.get('remark')) if parts[4] == 'release' else \
                context.restore(model, record_id, version=body.get('version'), remark=body.get('remark'))
            extra = business.availability(context, model, context._tx.get(model, record_id))
        return 200, {**business.embedded(result), **extra}, [(b'etag', business.revision(result))]
    if scope['method'] != 'GET':
        raise AccessError(405, 'Method not allowed')
    tail = parts[5:]
    try:
        numbers = [int(part) for part in tail if part not in ('diff', 'current')]
    except ValueError:
        raise ValidationError({'version': 'Use a version number'}) from None
    with business.context(user, readonly=True) as context:
        if not tail:
            return 200, context.versions(model, record_id), []
        if len(tail) == 1:
            return 200, context.version(model, record_id, numbers[0]), []
        if len(tail) == 3 and tail[1] == 'diff':
            return 200, context.version_diff(model, record_id, numbers[0], 'current' if tail[2] == 'current' else numbers[1]), []
    raise AccessError(404, 'Route not found')


# Restore: a release's content written onto the document as a new change, forward only

def dependents(context, model, record_id):
    """Live documents that link to this one (a Link or a Reference, from them or their Table rows), as [(model, id)]."""
    from . import qb
    from .documents import owner_of
    from .fields import Link, Reference
    registry, connection = context._business.registry, context._tx.connection
    owners = {child.model for child in registry.children}
    found = {}
    for other in registry.models.values():
        table = registry.name_for(other)
        for name, field in other.__declared_fields__.items():
            if field.stored and isinstance(field, Link) and field.target is model:
                query = qb.select(table, 'id').where(**{name: record_id})
            elif field.stored and isinstance(field, Reference):
                query = qb.select(table, 'id').where(**{name + '_model': registry.name_for(model), name + '_id': qb.value(record_id)})
            else:
                continue
            for (found_id,) in connection.run(query).all():
                root, root_id = (registry.root_of(other), owner_of(context._tx, other, found_id)) if other in owners else (other, found_id)
                if root_id is not None and (root, root_id) != (model, record_id):
                    found[root, root_id] = None
    return list(found)


def restorable(model, data, current, registry):
    """(values, dropped): the release's source values for a write (Table rows as full lists) and the fields it held
    that the model no longer has. A field added since takes its default; versioned=False fields are left out."""
    from .calc import is_finite
    from .naming import standard
    values, dropped = {}, sorted(key for key in data if key not in ('id', 'children') and key not in model.__fields__)
    for name, field in model.__fields__.items():
        if not field.versioned or name == model.__state__ or field.compute is not None or field.query is not None or field.fetch \
                or is_finite(field) or field.kind == 'sequence' or standard(field) or name in getattr(model, '__internal_fields__', ()):
            continue
        values[name] = data.get(name, None if field.default is MISSING or callable(field.default) else field.serialize(field.default))
    tables = {child.name: child.model for child in registry.children if child.parent is model}
    children = {}
    for name, rows in data.get('children', {}).items():
        if name in tables:
            saved = {str(row.id): row for row in current._rows.get(name, ())}
            children[name] = [{'id': row['id'], **restorable(tables[name], row, saved.get(row['id']) or current, registry)[0]} for row in rows]
    if children:
        values['children'] = children
    return values, dropped


def recreate(context, model, data, current):
    """Insert the Table rows the release held that were deleted since, under their original ids."""
    from . import qb
    from .audit import created
    from .database import storage_columns
    from .identity import RecordId
    registry = context._business.registry
    for child in (child for child in registry.children if child.parent is model):
        saved = {str(row.id): row for row in current._rows.get(child.name, ())}
        for index, row in enumerate(data.get('children', {}).get(child.name, ())):
            if row['id'] not in saved:
                fields = {name: child.model.__fields__[name].clean(value) for name, value in row.items()
                          if name in child.model.__fields__ and value is not None}
                if 'evlet_position' in child.model.__fields__:
                    fields['evlet_position'] = index  # The write then places every row as the release listed them.
                names, stored = storage_columns(child.model, {**fields, child.link: current.id}, child.model.__fields__)
                stamp_names, stamp_values = created(context._tx.actor)
                context._tx.connection.run(qb.insert(registry.name_for(child.model), id=int(RecordId(row['id'])),
                                                     **dict(zip([*names, *stamp_names], [*stored, *stamp_values]))))


def restore(context, model, record_id, number, remark):
    """Write release `number`'s content onto the record as a new change (logged `restored`), then recalculate."""
    from .models import ValidationError
    business, user = context._business, context.user
    if not releasing(model) or not business.policy.allows(user, model, 'restore'):
        raise AccessError(403, 'Your role cannot restore this record')
    record = business.policy.record(context._tx, user, model, record_id, 'update')
    if type(number) is not int:
        raise ValidationError({'version': 'Use a version number'})
    remark = clean_remark(remark)
    if remark is None:
        raise ValidationError({'remark': 'Restore needs a remark'})
    if locked_state(model, record._values) is not None:
        raise AccessError(409, 'Move back to an editable state first')
    data = content_of(context, model, record_id, number)
    found = dependents(context, model, record_id)
    if found:
        names = ', '.join(f'{other.__name__} {other_id}' for other, other_id in found[:5])
        raise AccessError(409, f'Restore is blocked while other records depend on this one: {names}')
    current = saved(context, model, record_id)
    values, dropped = restorable(model, data, current, business.registry)
    # A unique value another record has taken since stays as it is now, and is reported.
    from . import qb
    kept = [name for name, field in model.__fields__.items() if field.unique and values.get(name) is not None
            and values[name] != field.serialize(current._values.get(name)) and context._tx.connection.run(
                qb.select(business.registry.name_for(model), 'id').where(qb.ne('id', record_id), **{name: field.clean(values[name])})).one()]
    for name in kept:
        del values[name]
    recreate(context, model, data, current)
    result = context._write(model, record_id, values, restored=(number, remark))
    return {**result, '_restore': {'version': number, 'dropped': dropped, 'kept': kept}}
