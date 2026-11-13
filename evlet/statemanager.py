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


def moved(context, model, record, previous, rule, remark, level, before, after):
    """The Journal's `moved` entry, with the rule key, the remark, the level it approved, the levels that apply
    in the new state (see entering) and anything else the move changed."""
    from . import journal
    context.__dict__.get('_stays', {}).pop(record.id, None)
    if journal.journals(context, model):
        data = journal.tree_changes(context._business, model, before, after, skip=(model.__state__,))
        journal.append(context._business, context._tx, context.user, model, record.id, 'moved',
                       {**data, 'from': previous, 'to': rule.to, 'move': rule.label, **entering(context, model, record)},
                       rule=rule.key, remark=remark, level=level)


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
