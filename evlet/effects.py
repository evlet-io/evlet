"""Effects: rows a document posts to other models when a move enters a state, reversed when it is voided.

A model declares its effects in `Meta.effects`:

    class Meta:
        effects = (Effect(LedgerEntry, rows="ledger_rows", on="issued",
                          reverse=Swap("debit", "credit"), balanced=("debit", "credit")),)

    def ledger_rows(self):
        return [{"account": ..., "debit": self.total, "credit": "0"}, ...]

`rows` names a model method that takes only `self` and returns a list of dicts. `self` is the saved
document, read with full access and frozen, so the rows are a pure function of the record. When a move
enters `on`, the engine validates every row as a new record of the target model, checks the balance and
writes the rows in the move's transaction with `source` pointing at the record. A target model declares
`source = Reference()` and `reversal = Boolean(default=False)`; its rows are read only for every caller.
On void the engine writes mirror rows (`reverse`) marked `reversal=True`. Originals are never changed.
"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal, localcontext
import inspect
import json
import re
from types import MappingProxyType

from .auth import AccessError, User
from .fields import Boolean, Link, Reference
from .models import Model, ValidationError, identifier

NUMERIC = ('integer', 'decimal', 'currency', 'percent')
# The engine validates effect rows with the admin role's read access to the records they link to.
ENGINE = User(0, 'evlet-effects', 'admin')
SOURCE, REVERSAL = 'source', 'reversal'
BATCH = 500


class Swap:
    """Reverse a row by exchanging two numeric fields: Swap("debit", "credit")."""
    def __init__(self, first, second):
        if not all(isinstance(name, str) and name for name in (first, second)) or first == second:
            raise ValueError('Swap takes two different field names')
        self.fields = (first, second)

    def mirror(self, values):
        first, second = self.fields
        values[first], values[second] = values.get(second), values.get(first)

    def describe(self):
        return {'swap': list(self.fields)}


class Negate:
    """Reverse a row by flipping the sign of numeric fields: Negate("qty") or Negate("qty", "value")."""
    def __init__(self, *fields):
        if not fields or any(not isinstance(name, str) or not name for name in fields) or len(set(fields)) != len(fields):
            raise ValueError('Negate takes one or more different field names')
        self.fields = tuple(fields)

    def mirror(self, values):
        for name in self.fields:
            if values.get(name) is not None:
                values[name] = -values[name]

    def describe(self):
        return {'negate': list(self.fields)}


class Effect:
    """Rows a record posts to `target` when a move enters the state `on`; see the module docstring."""
    def __init__(self, target, /, *, rows, on, reverse=None, balanced=None):
        if not (isinstance(target, str) or isinstance(target, type) and issubclass(target, Model)):
            raise ValueError('Effect takes a target model class or its name')
        if not isinstance(rows, str) or not re.fullmatch(r'[a-z][A-Za-z0-9_]*', rows):
            raise ValueError('Effect rows names a model method')
        if not isinstance(on, str) or not on:
            raise ValueError('Effect on names a state')
        if reverse is not None and not isinstance(reverse, (Swap, Negate)):
            raise ValueError('Effect reverse is Swap(...) or Negate(...)')
        if balanced is not None and (not isinstance(balanced, (tuple, list)) or len(balanced) != 2 or len(set(balanced)) != 2
                                     or any(not isinstance(name, str) or not name for name in balanced)):
            raise ValueError('Effect balanced names two different fields, for example ("debit", "credit")')
        self.target, self.rows, self.on, self.reverse = target, rows, on, reverse
        self.balanced = None if balanced is None else tuple(balanced)

    def __repr__(self):
        name = self.target if isinstance(self.target, str) else self.target.__name__
        return f'Effect({name}, rows={self.rows!r}, on={self.on!r})'


@dataclass(frozen=True)
class Bound:
    """An Effect checked against a registry: its source and target models and the states it implies."""
    source: type
    target: type
    rows: str
    on: str
    reverse: object
    balanced: tuple | None
    # States a record can reach only by passing through `on`: such a record has posted its rows.
    implied: frozenset
    # States a record may be in after entering `on` (including "void" when it can be voided from them).
    after: frozenset


# Registration

def compile_effects(registry, models, child_models):
    """({source model: (Bound, ...)}, frozenset of target models) for the models of one registry."""
    result, targets = {}, set()
    for model in models:
        declared = getattr(getattr(model, 'Meta', None), 'effects', ())
        if not declared:
            continue
        name = model.__name__
        if not isinstance(declared, (tuple, list)) or any(not isinstance(effect, Effect) for effect in declared):
            raise ValueError(f'{name}: Meta.effects is a tuple of Effect(...) declarations')
        if model in child_models:
            raise ValueError(f'{name}: effects belong on a root document, not on Table rows')
        if model.__state__ is None:
            raise ValueError(f'{name}: effects are posted when a move enters a state, so the model needs a State')
        bound, seen = [], set()
        for effect in declared:
            try:
                target = registry.resolve(effect.target)
            except ValueError:
                raise ValueError(f'{name}: the effect target {effect!r} is not a model of this registry') from None
            if target in seen:
                raise ValueError(f'{name}: declare at most one effect per target model ({target.__name__})')
            seen.add(target)
            check_target(registry, model, target, child_models)
            implied, after = check_state(model, effect)
            check_method(model, effect)
            check_fields(model, target, effect, after)
            bound.append(Bound(model, target, effect.rows, effect.on, effect.reverse, effect.balanced, implied, after))
            targets.add(target)
        result[model] = tuple(bound)
    overlap = targets & set(result)
    if overlap:
        raise ValueError(f'{sorted(overlap, key=lambda item: item.__name__)[0].__name__} is an effect target, so it cannot post effects itself')
    return MappingProxyType(result), frozenset(targets)


def check_target(registry, model, target, child_models):
    label = f'{model.__name__} effect on {target.__name__}'
    if target is model:
        raise ValueError(f'{label}: a model cannot post effects to itself')
    if target in child_models:
        raise ValueError(f'{label}: the target must be a root model, not Table rows')
    if target.__state__ is not None or target.__tables__:
        raise ValueError(f'{label}: effect rows are flat records, so the target has no State and no Table')
    source = target.__declared_fields__.get(SOURCE)
    if not isinstance(source, Reference) or not source.stored:
        raise ValueError(f'{label}: the target must declare {SOURCE} = Reference(); the engine fills it')
    reversal = target.__declared_fields__.get(REVERSAL)
    if not isinstance(reversal, Boolean) or not reversal.stored or reversal.compute is not None or reversal.fetch \
            or getattr(reversal, 'initial', None) is not None:
        raise ValueError(f'{label}: the target must declare {REVERSAL} = Boolean(default=False); the engine marks reversal rows with it')


def check_state(model, effect):
    """Check `on` and return (implied, after) states.

    `on` must be entered by a declared move, cannot be the default, cannot be entered twice, and every state
    a record may be in after it is locked, so posted rows always match a record that ordinary writes and
    deletes cannot change.
    """
    field = model.__declared_fields__[model.__state__]
    label = f'{model.__name__} effect on {effect.target if isinstance(effect.target, str) else effect.target.__name__}'
    if effect.on not in field.choices:
        raise ValueError(f'{label}: {effect.on!r} is not a state of {model.__name__}.{model.__state__}')
    if effect.on == field.default or not any(move.to == effect.on for move in field.moves):
        raise ValueError(f'{label}: {effect.on!r} must be entered by a declared move, and must not be the default state')

    def reachable(starts, skip=None):
        found, queue = set(starts), deque(starts)
        while queue:
            state = queue.popleft()
            following = [move.to for move in field.moves if state in move.sources]
            if state in field.voidable:
                following.append('void')
            for item in following:
                if item != skip and item not in found:
                    found.add(item)
                    queue.append(item)
        return found
    after = reachable([effect.on])
    if effect.on in reachable([move.to for move in field.moves if effect.on in move.sources] + (['void'] if effect.on in field.voidable else [])):
        raise ValueError(f'{label}: a record could enter {effect.on!r} twice and post its rows again; '
                         f'no move may lead back to {effect.on!r}')
    unlocked = sorted(after - set(field.locked))
    if unlocked:
        raise ValueError(f'{label}: every state after {effect.on!r} must be locked, so posted rows keep matching '
                         'their record; not locked: ' + ', '.join(unlocked))
    implied = frozenset(set(field.choices) - reachable([field.default], skip=effect.on))
    return implied, frozenset(after)


def check_method(model, effect):
    method = getattr(model, effect.rows, None)
    if effect.rows in model.__declared_fields__ or not inspect.isfunction(method):
        raise ValueError(f'{model.__name__}: effect rows {effect.rows!r} is not a method of the model')
    parameters = list(inspect.signature(method).parameters.values())
    if len(parameters) != 1 or parameters[0].kind not in (parameters[0].POSITIONAL_ONLY, parameters[0].POSITIONAL_OR_KEYWORD):
        raise ValueError(f'{model.__name__}.{effect.rows} takes only self: effect rows are a pure function of the record')


def check_fields(model, target, effect, after):
    label = f'{model.__name__} effect on {target.__name__}'
    fields = target.__declared_fields__

    def numeric(name, purpose):
        field = fields.get(name)
        if field is None or not field.stored or field.kind not in NUMERIC or isinstance(field, Link) or name in (SOURCE, REVERSAL):
            raise ValueError(f'{label}: {purpose} names {name!r}, which is not a stored number field of {target.__name__}')
        return field
    if effect.balanced is not None:
        for name in effect.balanced:
            numeric(name, 'balanced')
    voidable = set(after) & set(model.__declared_fields__[model.__state__].voidable)
    if effect.reverse is None:
        if voidable:
            raise ValueError(f'{label}: a record can be voided after posting (from {", ".join(sorted(voidable))}), '
                             'so the effect needs reverse=Swap(...) or Negate(...)')
        return
    if isinstance(effect.reverse, Swap):
        first, second = (numeric(name, 'Swap') for name in effect.reverse.fields)
        if type(first) is not type(second) or getattr(first, 'scale', None) != getattr(second, 'scale', None) \
                or (first.minimum, first.maximum) != (second.minimum, second.maximum):
            raise ValueError(f'{label}: Swap needs two fields of the same type, scale and bounds, so a reversal row is always valid')
    else:
        for name in effect.reverse.fields:
            field = numeric(name, 'Negate')
            low, high = field.minimum, field.maximum
            if (low is None) != (high is None) or low is not None and low != -high:
                raise ValueError(f'{label}: Negate flips the sign of {name!r}, so its bounds must be symmetric '
                                 '(no minimum and maximum, or minimum equal to minus maximum)')


def targets_of(registry):
    return getattr(registry, 'effect_targets', frozenset())


# Posting and reversal

def load_document(business, tx, model, record_id):
    """The saved document with every row at every depth, read with full access and frozen."""
    from .documents import Draft, freeze_draft, owned_rows
    saved = tx.get(model, record_id)
    root = Draft(model, dict(saved._values), record_id=saved.id)
    level = [(model, saved.id, root)]
    while level:
        following = []
        for child in business.children.values():
            owners = {owner_id: item for owner, owner_id, item in level if child.parent is owner}
            if not owners:
                continue
            found = owned_rows(tx, child, list(owners))
            for owner_id, item in owners.items():
                rows = []
                for row in found.get(owner_id, ((), 0))[0]:
                    draft = Draft(child.model, dict(row._values), parent=item, record_id=row.id)
                    rows.append(draft)
                    following.append((child.model, row.id, draft))
                item._rows[child.name] = rows
        level = following
    freeze_draft(root)
    return root


def produce(bound, document):
    """Call the rows method; it must return a list of dicts."""
    rows = getattr(document, bound.rows)()
    if not isinstance(rows, (list, tuple)) or any(not isinstance(row, dict) for row in rows):
        raise TypeError(f'{bound.source.__name__}.{bound.rows} must return a list of dicts')
    return [dict(row) for row in rows]


class RowError(ValueError):
    """An effect row that is not a valid record of its target: {field: message} for row `index` (from 1)."""
    def __init__(self, bound, index, errors):
        self.bound, self.index, self.errors = bound, index, errors
        super().__init__(f'{bound.target.__name__} row {index}: ' + '; '.join(f'{key}: {value}' for key, value in errors.items()))


def engine_context(business, tx):
    from .documents import Context
    return Context(business, tx, ENGINE)


def prepare(engine, bound, values, record_id, index, *, reversal=False):
    """Validate one row as a new record of the target and return its draft, settled but not written.

    The engine fills `source` and `reversal`. Other keys must be fields the row may set: no calculated
    values and no unknown names. Declarative targets settle like any new record (defaults, calculations,
    rules, validators); plain models validate their fields and `validate()`.
    """
    from .documents import Draft, settle
    business, target = engine._business, bound.target
    fields = target.__declared_fields__
    errors = {key: 'Not a field the rows may set' for key in values
              if key not in fields or key in (SOURCE, REVERSAL) or fields[key].compute is not None or fields[key].query is not None}
    if errors:
        raise RowError(bound, index, errors)
    values = {**values, SOURCE: (business.registry.name_for(bound.source), record_id), REVERSAL: reversal}
    try:
        if getattr(target, '__declaration_mode__', False):
            draft = Draft(target, values, context=engine)
            object.__setattr__(draft, '_submitted', frozenset(values))
            settle(engine, draft, complete=True)
        else:
            draft = Draft(target, dict(target(**values)._values), context=engine)
    except ValidationError as exc:
        raise RowError(bound, index, exc.errors) from None
    except AccessError:
        raise RowError(bound, index, {'_form': 'A linked record is not available'}) from None
    return draft


def normalized(draft):
    return {name: draft._values.get(name) for name in draft._definition.__fields__}


def totals(bound, rows):
    """Exact sums of the balanced fields over row values ({field: value}); empty values count as zero."""
    with localcontext() as context:
        context.prec = 200
        first, second = bound.balanced
        return (sum((Decimal(row.get(first) or 0) for row in rows), Decimal(0)),
                sum((Decimal(row.get(second) or 0) for row in rows), Decimal(0)))


def is_balanced(bound, rows):
    if bound.balanced is None:
        return True
    first, second = totals(bound, rows)
    return first == second


def write(engine, bound, draft):
    from .documents import record_plan, write_snapshot
    business, tx = engine._business, engine._tx
    saved = write_snapshot(tx, draft)
    if business.registry.plan_for(bound.target) is not None:
        record_plan(tx, business.registry.name_for(bound.target), saved.id, business.registry.formula_hashes.get(bound.target))
    business.changed(tx, bound.target, saved.id)
    return saved


def readable(business, user, target):
    """Whether this role reads every row of the target: read access without a row scope."""
    policy = business.policy
    return policy.allows(user, target, 'read') and policy.rule(user, target).scope is None


def failure(business, user, bound, message):
    """A move or void that cannot post: a ValidationError naming target details only to roles that can read them."""
    if not readable(business, user, bound.target):
        return ValidationError({'_effects': 'The rows this record posts to other records are not valid'})
    return ValidationError({'_effects': message})


def row_message(business, user, error):
    shown = business.policy.fields(user, error.bound.target)
    parts = [f'{key}: {value}' if key in shown or key.startswith('_') or key not in error.bound.target.__declared_fields__
             else 'a value you cannot read is not valid' for key, value in error.errors.items()]
    return f'{error.bound.target.__name__} row {error.index} is not valid: ' + '; '.join(parts)


def post(context, model, record_id, state):
    """Called once a move into `state` is saved, in its transaction: write the rows of every effect on it."""
    business, tx = context._business, context._tx
    bounds = [bound for bound in business.registry.effects.get(model, ()) if bound.on == state]
    if not bounds:
        return
    document = load_document(business, tx, model, record_id)
    engine = engine_context(business, tx)
    for bound in bounds:
        drafts = []
        for index, values in enumerate(produce(bound, document), 1):
            try:
                drafts.append(prepare(engine, bound, values, record_id, index))
            except RowError as error:
                raise failure(business, context.user, bound, row_message(business, context.user, error)) from None
        if not is_balanced(bound, [normalized(draft) for draft in drafts]):
            first, second = totals(bound, [normalized(draft) for draft in drafts])
            names = bound.balanced
            raise failure(business, context.user, bound, f'{bound.target.__name__} rows are not balanced: '
                                                         f'{names[0]} totals {first} and {names[1]} totals {second}')
        for draft in drafts:
            write(engine, bound, draft)


def posted(tx, registry, bound, record_ids):
    """Saved rows of an effect for the given source ids: {source id: [target records in id order]}."""
    from .permissions import CHUNK
    table, name = identifier(registry.name_for(bound.target)), registry.name_for(bound.source)
    ids, found = list(dict.fromkeys(record_ids)), {}
    for start in range(0, len(ids), CHUNK):
        part = ids[start:start + CHUNK]
        rows = tx.connection.execute(f'SELECT * FROM {table} WHERE "{SOURCE}_model"=? AND "{SOURCE}_id" IN ({", ".join("?" for _ in part)}) '
                                     'ORDER BY id', [name, *part]).fetchall()
        for row in rows:
            record = bound.target._from_row(row)
            found.setdefault(record._values[SOURCE][1], []).append(record)
    return found


def mirror_values(bound, values):
    """The source values of a reversal row: the original's, with `reverse` applied."""
    fields = bound.target.__declared_fields__
    result = {name: value for name, value in values.items()
              if name not in (SOURCE, REVERSAL) and fields[name].compute is None and fields[name].query is None}
    bound.reverse.mirror(result)
    return result


def reverse(context, model, record_id):
    """Called when a record is voided: write a mirror row for each original row, marked as a reversal."""
    business, tx = context._business, context._tx
    engine = engine_context(business, tx)
    for bound in business.registry.effects.get(model, ()):
        if bound.reverse is None:
            continue
        rows = posted(tx, business.registry, bound, [record_id]).get(record_id, [])
        originals = [row for row in rows if not row._values[REVERSAL]]
        if not originals or len(originals) != len(rows):
            # Nothing was posted, or this record's rows were reversed already.
            continue
        drafts = []
        for index, row in enumerate(originals, 1):
            try:
                drafts.append(prepare(engine, bound, mirror_values(bound, row._values), record_id, index, reversal=True))
            except RowError as error:
                raise failure(business, context.user, bound, 'The reversal of ' + row_message(business, context.user, error)) from None
        for draft in drafts:
            write(engine, bound, draft)


# Details and schema

def summary(business, context, model, record):
    """`_effects` for a detail response: per target the role reads fully, its row and reversal counts and,
    when declared and readable, whether both batches balance. Left out when no target is listed."""
    bounds = business.registry.effects.get(model, ())
    user, tx = context.user, context._tx
    result = []
    for bound in bounds:
        if not readable(business, user, bound.target):
            continue
        rows = posted(tx, business.registry, bound, [record.id]).get(record.id, [])
        originals = [row._values for row in rows if not row._values[REVERSAL]]
        reversals = [row._values for row in rows if row._values[REVERSAL]]
        entry = {'target': business.registry.name_for(bound.target), 'rows': len(originals), 'reversals': len(reversals)}
        if bound.balanced is not None and set(bound.balanced) <= business.policy.fields(user, bound.target):
            entry['balanced'] = is_balanced(bound, originals) and is_balanced(bound, reversals)
        result.append(entry)
    return {'_effects': result} if result else {}


def model_schema(business, user, model):
    """Additive schema keys: `effects` on a source (targets this role reads fully) and `effect_target` on a target."""
    result = {}
    if model in targets_of(business.registry):
        result['effect_target'] = True
    entries = []
    for bound in business.registry.effects.get(model, ()):
        if not readable(business, user, bound.target):
            continue
        shown = business.policy.fields(user, bound.target)
        entries.append({
            'target': business.registry.name_for(bound.target),
            'on': bound.on if model.__state__ in business.policy.fields(user, model) else None,
            'balanced': list(bound.balanced) if bound.balanced is not None and set(bound.balanced) <= shown else None,
            'reverse': bound.reverse.describe() if bound.reverse is not None and set(bound.reverse.fields) <= shown else None,
        })
    if entries:
        result['effects'] = entries
    return result


# Check

def check(application):
    """Recompute every source record's rows and compare them with the saved rows, without writing.

    Takes an Application (or its Business). Returns a list of differences, each a dict with `model`,
    `record`, `target`, `kind` ("missing", "extra", "changed", "unbalanced" or "invalid"), `reversal`
    and, where they apply, `expected`, `actual` (serialized values), `row` (the saved row id) and `message`.
    Reads run in one transaction that writes nothing.
    """
    business = getattr(application, 'business', application)
    registry = business.registry
    differences = []
    with business.database.transaction() as tx:
        engine = engine_context(business, tx)
        for model, bounds in registry.effects.items():
            table, name = identifier(registry.name_for(model)), registry.name_for(model)
            state_name = model.__state__
            seen = set()
            last = 0
            while True:
                records = [model._from_row(row) for row in tx.connection.execute(
                    f'SELECT * FROM {table} WHERE id > ? ORDER BY id LIMIT ?', (last, BATCH)).fetchall()]
                if not records:
                    break
                last = records[-1].id
                ids = [record.id for record in records]
                saved = {bound: posted(tx, registry, bound, ids) for bound in bounds}
                for record in records:
                    seen.add(record.id)
                    document = None
                    for bound in bounds:
                        rows = saved[bound].get(record.id, [])
                        state = record._values.get(state_name)
                        if document is None and (rows or state in bound.implied):
                            document = load_document(business, tx, model, record.id)
                        differences += compare(engine, bound, name, record.id, state, document, rows)
            # Rows whose source record no longer exists.
            for bound in bounds:
                target_table = identifier(registry.name_for(bound.target))
                for row in tx.connection.execute(f'SELECT * FROM {target_table} WHERE "{SOURCE}_model"=? ORDER BY id', (name,)).fetchall():
                    item = bound.target._from_row(row)
                    if item._values[SOURCE][1] not in seen:
                        differences.append(difference(bound, name, item._values[SOURCE][1], 'extra', item._values[REVERSAL],
                                                      actual=item, message='The source record does not exist'))
    return differences


def difference(bound, model_name, record_id, kind, reversal, *, expected=None, actual=None, message=None):
    def shown(values):
        return {name: field.serialize(values.get(name)) for name, field in bound.target.__fields__.items()}
    result = {'model': model_name, 'record': record_id, 'target': target_name(bound), 'kind': kind, 'reversal': bool(reversal)}
    if expected is not None:
        result['expected'] = shown(expected)
    if actual is not None:
        result['actual'] = shown(actual._values)
        result['row'] = actual.id
    if message is not None:
        result['message'] = message
    return result


def target_name(bound):
    from .models import storage_name
    return storage_name(bound.target)


def compare(engine, bound, model_name, record_id, state, document, rows):
    """Differences between what a record should have posted for one effect and the saved rows."""
    originals = [row for row in rows if not row._values[REVERSAL]]
    reversals = [row for row in rows if row._values[REVERSAL]]
    result = []
    for batch, flag in ((originals, False), (reversals, True)):
        if batch and not is_balanced(bound, [row._values for row in batch]):
            first, second = totals(bound, [row._values for row in batch])
            result.append(difference(bound, model_name, record_id, 'unbalanced', flag,
                                     message=f'{bound.balanced[0]} totals {first} and {bound.balanced[1]} totals {second}'))
    if state not in bound.after:
        # The record has not entered the effect's state, so it should have no rows at all.
        return result + [difference(bound, model_name, record_id, 'extra', row._values[REVERSAL], actual=row,
                                    message='The record has not posted this effect') for row in rows]
    if state not in bound.implied and not originals:
        # The record may have reached its state without entering `on`; with no rows there is nothing to compare.
        return result + [difference(bound, model_name, record_id, 'extra', True, actual=row,
                                    message='A reversal without original rows') for row in reversals]
    try:
        expected = [normalized(prepare(engine, bound, values, record_id, index))
                    for index, values in enumerate(produce(bound, document), 1)]
        mirrors = []
        if state == 'void' and bound.reverse is not None:
            mirrors = [normalized(prepare(engine, bound, mirror_values(bound, values), record_id, index, reversal=True))
                       for index, values in enumerate(expected, 1)]
    except (RowError, TypeError) as error:
        return result + [difference(bound, model_name, record_id, 'invalid', False, message=str(error))]
    result += match(bound, model_name, record_id, expected, originals, False)
    result += match(bound, model_name, record_id, mirrors, reversals, True)
    return result


def match(bound, model_name, record_id, expected, actual, reversal):
    """Pair expected rows with saved rows: equal rows match, then leftovers pair in order as changed rows."""
    remaining = list(actual)
    unmatched = []
    for values in expected:
        found = next((row for row in remaining if dict(row._values) == values), None)
        if found is None:
            unmatched.append(values)
        else:
            remaining.remove(found)
    result = []
    for values, row in zip(unmatched, remaining):
        result.append(difference(bound, model_name, record_id, 'changed', reversal, expected=values, actual=row))
    for values in unmatched[len(remaining):]:
        result.append(difference(bound, model_name, record_id, 'missing', reversal, expected=values))
    for row in remaining[len(unmatched):]:
        result.append(difference(bound, model_name, record_id, 'extra', reversal, actual=row))
    return result


def describe(item):
    """One line of text for a difference, as the check command prints it."""
    kind = ('reversal ' if item['reversal'] else '') + item['kind']
    parts = [f'{item["model"]} {item["record"]} -> {item["target"]}: {kind}']
    if 'row' in item:
        parts.append(f'row {item["row"]}')
    if item.get('message'):
        parts.append(item['message'])
    if 'expected' in item:
        parts.append('expected ' + json.dumps(item['expected'], sort_keys=True))
    if 'actual' in item:
        parts.append('saved ' + json.dumps(item['actual'], sort_keys=True))
    return '; '.join(parts)
