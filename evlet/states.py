"""States and moves, state locks, and action availability on the document pipeline.

A State field changes only through its declared moves. Guards (`Move.when`, `available_when`)
are finite-v1 Boolean expressions decided on the record as saved, before the operation runs,
in the same transaction. An undecided guard (pending or error) blocks, like an undecided lock.
"""

from dataclasses import dataclass
from types import MappingProxyType

from .auth import AccessError
from .fields import MISSING, Link, Reference
from .models import ValidationError


@dataclass(frozen=True)
class Guard:
    """A compiled guard: its IR, the (scope, model, field) inputs it reads and the Link lookups it needs."""
    ir: dict
    reads: tuple
    lookups: MappingProxyType
    # Whether the guard needs the whole document (owned rows or virtual results); otherwise it reads
    # the saved root values, stored results included, without loading any rows.
    whole: bool


def compile_guards(registry, models, plans, child_models):
    """Guards of root documents: ({model: {Move or action name: Guard}}, {model: plan}, {model: root plan}).

    The plan evaluates guards over the whole saved document; the root plan reads the saved root values
    (stored results as values) for guards that read no owned rows and no virtual results.
    """
    from .calc import CalcError, _Context, compile_expression, compile_plan, lift
    from .declarations import Move
    guards, guard_plans, root_plans = {}, {}, {}
    for model in models:
        state = model.__state__
        if model in child_models:
            if state is not None:
                raise ValueError(f'{model.__name__}.{state}: a State belongs on a root document, not on Table rows')
            continue
        if state is not None:
            check_kept_results(registry, model, plans.get(model))
        wanted = {}
        if state is not None:
            for move in model.__declared_fields__[state].moves:
                if move.run is not None and (not callable(getattr(model, move.run, None)) or move.run in model.__declared_fields__):
                    raise ValueError(f'{model.__name__}: move {move.label!r} runs {move.run!r}, which is not a method of the model')
                if move.when is not None:
                    wanted[move] = move.when
        for name in dir(model):
            declaration = getattr(getattr(model, name, None), '__action__', None)
            if isinstance(declaration, dict) and declaration.get('available_when') is not None:
                wanted[name] = declaration['available_when']
        if not wanted:
            continue
        plan = plans.get(model) or compile_plan(registry, model, force=True)
        tables = {child.name: child.model for child in registry.children if child.parent is model}
        compiled = {}
        for key, expression in wanted.items():
            label = (f'{key.label} (when)' if isinstance(key, Move) else f'{key} (available_when)').replace(' ', '_').replace('(', '').replace(')', '')
            ir, kind, reads = compile_expression(lift(expression), _Context(model, (), None, tables, registry=registry), label)
            if kind != 'boolean':
                raise CalcError('COMPUTE_TYPE_ERROR', f'{model.__name__}: {label} must be a Boolean expression, not {kind}')
            resolved, lookups = [], {}
            for scope, table, read in reads:
                if scope == 'self':
                    resolved.append(('self', model, read))
                elif scope == 'lookup':
                    resolved.append(('lookup', registry.models[table], read))
                    lookups.setdefault(table, set()).add(read)
                elif scope != 'today':
                    resolved.append((scope, tables[table], read))
            whole = any(scope in ('member', 'membership') or scope == 'self' and not model.__declared_fields__[read].stored
                        for scope, _, read in resolved)
            compiled[key] = Guard(ir, tuple(resolved), MappingProxyType({name: tuple(sorted(fields)) for name, fields in lookups.items()}), whole)
        guards[model], guard_plans[model], root_plans[model] = MappingProxyType(compiled), plan, root_plan(plan)
    return MappingProxyType(guards), MappingProxyType(guard_plans), MappingProxyType(root_plans)


def root_plan(plan):
    """The root table of a plan with no calculations: every field, stored results included, is a saved value."""
    from copy import deepcopy
    result = deepcopy(dict(plan))
    root = result['tables']['$root']
    root['fields'] = {name: {**meta, 'computed': False, 'default': None} for name, meta in root['fields'].items()}
    root['tables'] = []
    result['tables'], result['fields'] = {'$root': root}, []
    return result


def kept_plan(plan):
    """The plan of ordinary writes on a locked record: stored results, at every depth, are saved values.

    Virtual results and the rules (required_when, applies_when, locked_when) still evaluate, so they
    read the results the record keeps instead of results it would get if it were recalculated.
    """
    from copy import deepcopy
    result = deepcopy(dict(plan))
    for table in result['tables'].values():
        table['fields'] = {name: {**meta, 'computed': False, 'default': None} if meta['computed'] and meta['stored'] else meta
                           for name, meta in table['fields'].items()}
    result['fields'] = [field for field in result['fields'] if field.get('kind') or not field['stored']]
    return result


def check_kept_results(registry, model, plan):
    """A locked record keeps its saved results, so no stored result may read an editable_when_locked field.

    Reads are followed through virtual results and fetched copies: finite results by their plan reads,
    legacy string formulas, Query fields and fetches by the value inputs of the legacy template graph.
    Creation defaults and conditions are not results, so what they read does not matter here.
    """
    from .calc import is_finite
    from .reactivity import template
    members = [model] + [child.model for child in registry.children if registry.root_of(child.model) is model]
    nodes, _, inputs, owners = template(registry, model)
    keys = {}
    for key, definition in owners.items():
        keys.setdefault((definition, key.rpartition('.')[2]), key)

    def derived(field):
        return field.compute is not None or field.query is not None or bool(field.fetch)

    def editable(target, name, seen):
        field = target.__declared_fields__.get(name)
        if field is None or (target, name) in seen:
            return False
        if field.editable_when_locked:
            return True
        seen = seen | {(target, name)}
        if field.fetch:
            return editable(target, field.fetch.split('.')[0], seen)
        if is_finite(field):
            return plan is not None and any(read is not None and scope != 'lookup' and editable(source, read, seen)
                                            for scope, source, read in registry.finite_reads(target, name))
        if derived(field):
            key = keys.get((target, name))
            return key is not None and any(node(dependency, seen) for dependency in inputs[key])
        return False

    def node(key, seen):
        if nodes[key].get('source'):
            return any(node(dependency, seen) for dependency in inputs[key])
        return editable(owners[key], key.rpartition('.')[2], seen)
    for member in members:
        for name, field in member.__declared_fields__.items():
            if field.stored and derived(field) and not field.editable_when_locked and editable(member, name, frozenset()):
                raise ValueError(f'{member.__name__}.{name} is a stored result that reads a field editable while locked; '
                                 'a locked record keeps its saved results, so make the field it reads not editable_when_locked '
                                 'or the result virtual (stored=False)')


# Guard evaluation

def saved_data(item):
    """A saved document tree as finite-engine data: source values with internal keys, at every depth."""
    from .calc import is_finite
    values = {name: item._values.get(name) for name, field in item._definition.__declared_fields__.items()
              if not is_finite(field) and name not in getattr(item._definition, '__internal_fields__', ())}
    values['_key'], values['id'] = 's' + str(item.id), item.id
    if item._rows:
        values['children'] = {name: [saved_data(row) for row in rows] for name, rows in item._rows.items()}
    return values


class Guards:
    """Guards of one saved record, evaluated over engine sessions built on first use.

    `values` are the saved root values. `load` returns the saved document tree; it is called only for a
    guard that reads owned rows or virtual results, so most guards cost no row reads.
    """
    def __init__(self, context, model, values, load):
        self.context, self.model, self.values, self.load = context, model, values, load
        self.documents = {}

    def session(self, whole):
        from .calc import Document
        from .documents import host_today, resolve_lookups
        business = self.context._business
        if whole not in self.documents:
            if whole:
                saved = self.load()
                document = Document(business.registry.guard_plans[self.model], saved_data(saved), checked=True)
            else:
                plan = business.registry.guard_root_plans[self.model]
                document = Document(plan, {name: self.values.get(name) for name in plan['tables']['$root']['fields']}, checked=True)
            document.set_today(host_today(business))
            if whole:
                resolve_lookups(self.context, document)
            self.documents[whole] = document
        return self.documents[whole]

    def result(self, guard):
        from .calc import Budget, CalcError, MAX_HOPS, Scope, evaluate, error
        from .database import ROW_LIMIT
        business = self.context._business
        try:
            try:
                document = self.session(guard.whole)
            except ValidationError:
                # A document that cannot be loaded for editing (over a row limit) cannot pass a guard either.
                return error('DOCUMENT_STRUCTURE_ERROR', ['guard'])
            self.document = document
            for _ in range(MAX_HOPS + 1):
                scope = Scope(self.document, self.document.root, at='guard')
                outcome = evaluate(guard.ir, scope, Budget())
                wanted = {}
                for name, record_id in scope.misses:
                    if name in guard.lookups and record_id not in self.document.lookups.get(name, {}):
                        wanted.setdefault(name, []).append(record_id)
                if not wanted:
                    return outcome
                # Linked records are read with full access, like the stored lookups of calculations.
                lookups = dict(self.document.lookups)
                for name, ids in wanted.items():
                    target, found = business.registry.models[name], dict(lookups.get(name, {}))
                    for start in range(0, len(ids), ROW_LIMIT):
                        for record in self.context._tx.list(target, filters={'id__in': ids[start:start + ROW_LIMIT]}, limit=ROW_LIMIT):
                            found[record.id] = {**found.get(record.id, {}), **{field: target.__declared_fields__[field].serialize(getattr(record, field))
                                                                            for field in guard.lookups[name]}}
                    lookups[name] = found
                if lookups == self.document.lookups:
                    return outcome
                self.document.lookups = lookups
            return outcome
        except CalcError as exc:
            return error(exc.code, ['guard'])


def readable(policy, user, guard):
    """Whether this role may read every input of a guard; cached per role like rule expressions."""
    cache = policy.__dict__.setdefault('_guards', {})
    key = (user.role, id(guard))
    if key not in cache:
        cache[key] = policy.reads_allowed(user, guard.reads)
    return cache[key]


def blocked_reason(outcome, open_inputs, noun):
    """None when a guard is true; otherwise a plain-English reason (generic over unreadable inputs)."""
    if outcome.state == 'ready' and outcome.value is True:
        return None
    if not open_inputs:
        return f'This {noun} is not available now'
    if outcome.state == 'ready':
        return f'The condition for this {noun} is not met'
    if outcome.state == 'pending':
        return f'The condition for this {noun} needs values that are not filled in yet'
    return f'The condition for this {noun} could not be checked'


def check_guard(context, guards, guard, label, noun):
    """Raise 409 unless the guard is true on the saved record."""
    if guard is None:
        return
    reason = blocked_reason(guards.result(guard), readable(context._business.policy, context.user, guard), noun)
    if reason is not None:
        raise AccessError(409, f'{label} is not available: {reason[0].lower()}{reason[1:]}')


# Ordinary writes, moves and actions

def state_write(model, scalars, current):
    """Ordinary writes may create a record in the default state and never set it afterwards."""
    name = model.__state__
    if name is None or name not in scalars:
        return
    field = model.__declared_fields__[name]
    value = scalars[name]
    if current is None and value == field.default:
        del scalars[name]
        return
    if current is not None and value == current._values.get(name):
        del scalars[name]
        return
    if current is None:
        raise ValidationError({name: f'A new record starts as {field.default}; use its moves to change it'})
    raise ValidationError({name: 'This changes only through its moves'})


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

    Fields declared with editable_when_locked=True may still change. Adding, removing or reordering
    rows at any depth is a change. Values are compared as the caller submitted them, before calculations.
    """
    state = locked_state(draft._definition, before._values)
    if state is None:
        return
    message = f'This record is {state}, so it is locked' if state_shown(context, draft._definition) else 'This record is locked'
    errors = {}

    def compare(item, old, prefix):
        fields = item._definition.__declared_fields__
        for name in item._submitted:
            field = fields.get(name)
            if field is None or field.editable_when_locked:
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
            if [row.id for row in rows] != [row.id for row in saved]:
                errors[prefix + table] = message
            by_id = {row.id: row for row in saved}
            for row in rows:
                if row.id in by_id:
                    compare(row, by_id[row.id], prefix + table + '.' + row.key + '.')
    compare(draft, before, '')
    if errors:
        raise ValidationError(errors)


def check_delete(context, model, record):
    state = locked_state(model, record._values)
    if state is not None:
        named = f'is {state}, so it ' if state_shown(context, model) else ''
        raise AccessError(409, f'This record {named}is locked and cannot be deleted')


def check_attachments(context, model, record):
    """Adding or removing attachments is an ordinary write, refused while the record is locked."""
    state = locked_state(model, record._values)
    if state is not None:
        named = f'is {state}, so it ' if state_shown(context, model) else ''
        raise AccessError(409, f'This record {named}is locked, so its attachments cannot change')


def check_recalculate(context, model, before):
    if before is not None and locked_state(model, before._values) is not None:
        raise AccessError(409, 'This record is locked, so it keeps its saved results and cannot be recalculated; '
                               'use its moves or actions to change it')


def keep_saved(draft, before):
    """After an ordinary write on a locked record: every field except editable_when_locked ones keeps its saved value."""
    from .documents import prior_versions, walk
    prior = prior_versions(before, draft)
    for _, item in walk(draft):
        old = prior(item)
        if old is None:
            continue
        for name, field in item._definition.__declared_fields__.items():
            if not field.editable_when_locked:
                item._values[name] = old._values.get(name)


def begin_move(context, model, draft, before, to):
    """Check a move on the saved record, set the new state and return the declared Move.

    A role that cannot read the State gets one answer for every refused move (an unknown state, no such
    move now, a move for other roles or a guard that is not met), so it cannot learn which states and
    moves exist.
    """
    name = model.__state__
    if name is None:
        raise AccessError(404, 'This record has no moves')
    field = model.__declared_fields__[name]
    current, shown = before._values.get(name), state_shown(context, model)
    if not shown:
        try:
            return checked_move(context, model, field, draft, before, current, to)
        except AccessError:
            raise AccessError(409, 'This record has no such move now') from None
    return checked_move(context, model, field, draft, before, current, to)


def checked_move(context, model, field, draft, before, current, to):
    if to not in field.choices:
        raise AccessError(404, 'Move not found')
    if current in field.final:
        raise AccessError(409, f'This record is {current}, which is final; it has no moves')
    move = field.move(current, to)
    if move is None:
        raise AccessError(409, f'This record cannot move from {current} to {to}')
    if context.user.role not in move.roles:
        raise AccessError(403, f'Your role cannot use {move.label}')
    guard = context._business.registry.guards.get(model, {}).get(move)
    check_guard(context, Guards(context, model, before._values, lambda: before), guard, move.label, 'move')
    # The engine writes the state; Draft attribute writes of a State are refused.
    draft._values[model.__state__] = to
    return move


def moved(context, model, record_id, previous, move):
    """Called once a move is saved, in its transaction. The Journal entry for the move is written here."""


def action_arguments(context, model, method, declaration, arguments, saved):
    """Check availability on the saved record, then validate and clean the inputs of a model action."""
    from .documents import typed_arguments
    if saved is not None:
        guard = context._business.registry.guards.get(model, {}).get(method.__name__)
        check_guard(context, Guards(context, model, saved._values, lambda: saved), guard, declaration['label'], 'action')
    inputs = declaration.get('inputs')
    if inputs is None:
        return typed_arguments(method, arguments or {}, skip=1)
    values = {} if arguments is None else arguments
    if not isinstance(values, dict):
        raise ValidationError({'arguments': 'Use an object of action inputs'})
    errors = {name: 'Unknown input' for name in values if name not in inputs}
    cleaned = {}
    policy, tx, user = context._business.policy, context._tx, context.user
    for name, field in inputs.items():
        try:
            cleaned[name] = field.clean(values.get(name, MISSING))
            if cleaned[name] is not None and isinstance(field, (Link, Reference)):
                target, record_id = (field.target, cleaned[name]) if isinstance(field, Link) else \
                    (context._business.registry.resolve(cleaned[name][0]), cleaned[name][1])
                try:
                    policy.record(tx, user, target, record_id)
                except AccessError:
                    raise ValueError('Choose a record you can read') from None
        except ValueError as exc:
            errors[name] = str(exc)
    if errors:
        raise ValidationError(errors)
    return cleaned


# What the caller may do now

def loaded_document(business, model, record, loaded):
    """The saved document tree from the rows a detail already read, or None when it left rows out.

    Over a row limit it is None too, so the guard loads the document itself and reports the limit.
    """
    from .calc import LIMITS
    from .documents import Draft, row_bounds
    if loaded is None or loaded.get('partial'):
        return None
    finite = business.registry.plan_for(model) is not None
    count = [0]

    def build(definition, values, record_id):
        item = Draft(definition, values, record_id=record_id)
        for child in business.children.values():
            if child.parent is not definition:
                continue
            rows = loaded.get((child, record_id), ())
            count[0] += len(rows)
            if len(rows) > row_bounds(business, child)[2] or finite and count[0] > LIMITS['document_rows']:
                return None
            item._rows[child.name] = []
            for row in rows:
                built = build(child.model, row._values, row.id)
                if built is None:
                    return None
                item._rows[child.name].append(built)
        return item
    return build(model, record._values, record.id)


def availability(business, context, model, record, loaded=None):
    """`_moves`, `_actions` and `_locked` for a detail response, or {} for models without them.

    Guards that read rows use the rows the detail `loaded` when it read them all.

    Moves out of the current state are listed for every role that can read the State, with a plain-English
    reason when one is blocked; actions are those this role may run, listed also when the State is unreadable.
    Reasons over guard inputs the role cannot read are generic.
    """
    from .documents import load_draft
    guards = business.registry.guards.get(model, {})
    name = model.__state__
    user, policy = context.user, business.policy
    declared = [(action_name, action) for (owner, action_name), action in business.actions.items()
                if owner is model and hasattr(getattr(model, action_name, None), '__action__')]
    guarded_actions = any(action_name in guards for action_name, _ in declared)
    shown_state = name is not None and name in policy.fields(user, model)
    if name is None and not guarded_actions:
        return {}
    # Without a row scope, update permission is the role's; otherwise one statement checks the row.
    updatable = policy.allows(user, model, 'update') and (policy.rule(user, model).scope is None
                                                          or bool(policy.visible(context._tx, user, model, [record.id], 'update')))
    guards_now = Guards(context, model, record._values, lambda: loaded_document(business, model, record, loaded)
                        or load_draft(context, model, record.id, {}, preview=True)[0])

    def evaluator():
        return guards_now
    result = {}
    if shown_state:
        field = model.__declared_fields__[name]
        current = record._values.get(name)
        moves = []
        for move in field.moves:
            if current not in move.sources or current in field.final:
                continue
            reason = None
            if user.role not in move.roles:
                reason = 'Your role cannot use this move'
            elif not updatable:
                reason = 'You cannot change this record'
            elif move in guards:
                reason = blocked_reason(evaluator().result(guards[move]), readable(policy, user, guards[move]), 'move')
            moves.append({'to': move.to, 'label': move.label, 'allowed': reason is None, **({'reason': reason} if reason else {})})
        result['_moves'] = moves
        result['_locked'] = current in field.locked
    actions = []
    for action_name, action in declared:
        if user.role not in action.roles or not policy.allows(user, model, 'update'):
            continue
        reason = None if updatable else 'You cannot change this record'
        if reason is None and action_name in guards:
            reason = blocked_reason(evaluator().result(guards[action_name]), readable(policy, user, guards[action_name]), 'action')
        actions.append({'name': action_name, 'label': action.label, 'allowed': reason is None, **({'reason': reason} if reason else {})})
    result['_actions'] = actions
    return result


def state_schema(business, user, model):
    """Schema metadata of a model's State for this role, or None when the role cannot read the field."""
    name = model.__state__
    if name is None or name not in business.policy.fields(user, model):
        return None
    field, guards = model.__declared_fields__[name], business.registry.guards.get(model, {})
    return {'field': name, 'states': list(field.choices), 'default': field.default,
            'moves': [{'from': list(move.sources), 'to': move.to, 'label': move.label, 'roles': list(move.roles),
                       'guarded': move in guards, 'when': guards[move].ir if move in guards and readable(business.policy, user, guards[move]) else None}
                      for move in field.moves],
            'locked': list(field.locked), 'final': list(field.final), 'voidable': list(field.voidable)}


def action_schema(business, user, model, name):
    """Additive schema keys of a model @action: roles, inputs, available_when (when readable)."""
    from .ui.metadata import field_schema
    method = getattr(model, name)
    declaration = method.__action__
    guard = business.registry.guards.get(model, {}).get(name)
    result = {'roles': list(declaration['roles']), 'guarded': guard is not None,
              'available_when': guard.ir if guard is not None and readable(business.policy, user, guard) else None}
    inputs = declaration.get('inputs')
    if inputs is None:
        from .documents import argument_schema
        result['arguments'] = argument_schema(method, skip=1)
        result['inputs'] = []
        return result
    from .fields import Boolean, Integer
    result['inputs'] = [field_schema(field) for field in inputs.values()]
    # The scalar form the current UI renders: text for everything except whole numbers and Booleans.
    result['arguments'] = []
    for input_name, field in inputs.items():
        kind = 'bool' if isinstance(field, Boolean) else 'int' if isinstance(field, Integer) and not isinstance(field, Link) else 'str'
        item = {'name': input_name, 'type': kind, 'required': field.required}
        if not field.required and field.default is not MISSING and field.default is not None and not callable(field.default):
            item['default'] = field.serialize(field.validate(field.default))
        result['arguments'].append(item)
    return result
