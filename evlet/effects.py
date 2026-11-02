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
import logging
import re
from types import MappingProxyType

from .auth import AccessError, User
from .fields import Boolean, Link, Reference
from .models import Model, ValidationError, identifier
from .permissions import CHUNK, Policy, Rule

NUMERIC = ('integer', 'decimal', 'currency', 'percent')
# The engine validates the rows it writes without any role: see EnginePolicy.
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
        index_source(target)

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

def index_source(target):
    """Rows are found by their source, so the engine indexes a target's `source` Reference on both columns."""
    field = getattr(target, '__declared_fields__', {}).get(SOURCE) if isinstance(target, type) else None
    if isinstance(field, Reference):
        field.index = True


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
            index_source(target)
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
    if effect.on not in reachable([field.default]):
        raise ValueError(f'{label}: no sequence of declared moves from {field.default!r} reaches {effect.on!r}')
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
    touched = set(effect.reverse.fields) & set(effect.balanced or ())
    if touched and touched != set(effect.balanced):
        raise ValueError(f'{label}: the reverse changes {", ".join(sorted(touched))} but not the other balanced field, '
                         'so reversal rows would not balance; swap the balanced pair, or negate both or neither')
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

class VirtualRead(AttributeError):
    """A rows method read a virtual result, which the saved document does not hold."""


def saved_draft():
    """The Draft class of a saved document handed to a rows method: reading a virtual result is an error."""
    from .documents import Draft

    class Saved(Draft):
        def __getattr__(self, name):
            field = self._definition.__declared_fields__.get(name)
            if field is not None and not field.stored:
                raise VirtualRead(f'{self._definition.__name__}.{name} is a virtual result (stored=False), which effect rows '
                                  'cannot read; store it or calculate the value in the rows method')
            return super().__getattr__(name)
    return Saved


def load_document(business, tx, model, record_id):
    """The saved document with every row at every depth, read with full access and frozen."""
    from .documents import freeze_draft, owned_rows
    Saved = saved_draft()
    saved = tx.get(model, record_id)
    root = Saved(model, dict(saved._values), record_id=saved.id)
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
                    draft = Saved(child.model, dict(row._values), parent=item, record_id=row.id)
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


class EnginePolicy(Policy):
    """Full read access without roles or row scopes, for validating rows the engine writes itself.

    Linked records are read as they are stored, in batches, and kept for the rest of the operation, so Link
    existence and Link filters are decided on the raw records whatever any role may read.
    """
    def __init__(self, policy):
        self.registry, self.rules, self.governed, self.corrections = policy.registry, policy.rules, policy.governed, policy.corrections
        self.cache = {}

    def rule(self, user, model):
        return Rule(('read',))

    def allows(self, user, model, operation):
        return operation == 'read'

    def presented(self, user, model):
        return set(model.__declared_fields__)

    def fields(self, user, model, *, write=False, _visiting=frozenset()):
        return set(model.__declared_fields__)

    def scope_sql(self, user, model):
        return '', []

    def load(self, tx, model, record_ids):
        """Read the records not read yet, one statement per model and chunk; absent ids are remembered too."""
        wanted = [record_id for record_id in dict.fromkeys(record_ids) if (model, record_id) not in self.cache]
        table = identifier(self.registry.name_for(model))
        for start in range(0, len(wanted), CHUNK):
            part = wanted[start:start + CHUNK]
            self.cache.update(((model, record_id), None) for record_id in part)
            for row in tx.connection.execute(f'SELECT * FROM {table} WHERE id IN ({", ".join("?" for _ in part)})', part).fetchall():
                self.cache[model, row['id']] = model._from_row(row)

    def _scoped(self, tx, user, model, record_ids, operation, columns):
        if operation != 'read':
            return {}
        ids = [record_id for record_id in dict.fromkeys(record_ids) if type(record_id) is int]
        self.load(tx, model, ids)
        found = {record_id: self.cache[model, record_id] for record_id in ids if self.cache[model, record_id] is not None}
        return dict.fromkeys(found) if columns == 'record.id' else found

    def record(self, tx, user, model, record_id, operation='read'):
        found = self._scoped(tx, user, model, [record_id], operation, 'record.*')
        if record_id not in found:
            raise AccessError(404, 'Record not found')
        return found[record_id]


class EngineBusiness:
    """The application's business with the engine policy, for the engine's own Context."""
    def __init__(self, business):
        self._business, self.policy = business, EnginePolicy(business.policy)

    def __getattr__(self, name):
        return getattr(self._business, name)


def engine_context(business, tx):
    from .documents import Context
    return Context(EngineBusiness(business), tx, ENGINE)


def check_links(engine, bound, rows):
    """Batch-read every record the rows link to; returns {row index: {field: message}} for Links to missing records.

    Values that are not valid ids are left to field validation.
    """
    policy, tx = engine._business.policy, engine._tx
    fields = {name: field for name, field in bound.target.__declared_fields__.items() if isinstance(field, Link)}

    def cleaned(field, value):
        try:
            return None if value is None else field.clean(value)
        except (ValueError, TypeError):
            return None
    wanted = {}
    for values in rows:
        for name, field in fields.items():
            value = cleaned(field, values.get(name))
            if value is not None:
                wanted.setdefault(field.target, set()).add(value)
    for model, ids in wanted.items():
        policy.load(tx, model, list(ids))
    errors = {}
    for index, values in enumerate(rows, 1):
        for name, field in fields.items():
            value = cleaned(field, values.get(name))
            if value is not None and policy.cache.get((field.target, value)) is None:
                errors.setdefault(index, {})[name] = 'The linked record does not exist'
    return errors


def prepare_all(engine, bound, rows, record_id, *, reversal=False):
    """Validate a batch of rows (dicts) as new records of the target; returns their drafts, or raises RowError."""
    linked = check_links(engine, bound, rows)
    drafts = []
    for index, values in enumerate(rows, 1):
        if index in linked:
            raise RowError(bound, index, linked[index])
        drafts.append(prepare(engine, bound, values, record_id, index, reversal=reversal))
    return drafts


def prepare(engine, bound, values, record_id, index, *, reversal=False):
    """Validate one row as a new record of the target and return its draft, settled but not written.

    The engine fills `source` and `reversal`. Other keys must be fields the row may set: no calculated
    values and no unknown names. Declarative targets settle like any new record (defaults, calculations,
    rules, validators); plain models validate their fields and `validate()`. Links were checked by
    `check_links`, without any role.
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
        raise RowError(bound, index, {'_form': 'A linked record does not exist'}) from None
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
    """Insert one row with one statement, plus the calculation version of a finite-v1 target and an event when
    events are enabled. The source is the record being moved or voided, so it is not checked again."""
    from .database import storage_columns
    from .documents import record_plan
    business, tx, target = engine._business, engine._tx, bound.target
    tx.check_references(target, {name: draft._values.get(name) for name, field in target.__declared_fields__.items()
                                 if isinstance(field, Reference) and name != SOURCE})
    names, values = storage_columns(target, draft._values, target.__fields__)
    table = identifier(business.registry.name_for(target))
    record_id = tx.connection.execute(f'INSERT INTO {table} ({", ".join(identifier(name) for name in names)}) '
                                      f'VALUES ({", ".join("?" for _ in names)})', values).lastrowid
    if business.registry.plan_for(target) is not None:
        record_plan(tx, business.registry.name_for(target), record_id, business.registry.formula_hashes.get(target))
    business.changed(tx, target, record_id)
    return record_id


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


def balance_message(business, user, bound, values, prefix=''):
    """Why a batch does not balance; the totals only for roles that can read both balanced fields."""
    if not set(bound.balanced) <= business.policy.fields(user, bound.target):
        return f'{prefix}{bound.target.__name__} rows are not balanced'
    first, second = totals(bound, values)
    return (f'{prefix}{bound.target.__name__} rows are not balanced: '
            f'{bound.balanced[0]} totals {first} and {bound.balanced[1]} totals {second}')


def rows_of(context, bound, document):
    """The rows method's result, or a 422 failure. Its exception is logged and never shown."""
    try:
        return produce(bound, document)
    except VirtualRead as exc:
        raise failure(context._business, context.user, bound, str(exc)) from None
    except Exception:
        logging.getLogger(__name__).exception('%s.%s failed', bound.source.__name__, bound.rows)
        raise failure(context._business, context.user, bound,
                      f'{bound.source.__name__}.{bound.rows} could not produce the {bound.target.__name__} rows') from None


def journal_item(business, bound, drafts, *, reversal):
    """One target of a Journal `effects` entry: how many rows were written, whether they reverse
    earlier rows and, for a balanced effect, whether they balance (always true once written)."""
    item = {'target': business.registry.name_for(bound.target), 'rows': len(drafts), 'reversal': reversal}
    if bound.balanced is not None:
        item['balanced'] = is_balanced(bound, [normalized(draft) for draft in drafts])
    return item


def post(context, model, record_id, state):
    """Called once a move into `state` is saved, in its transaction: write the rows of every effect on it.

    Returns the Journal items of the targets that received rows (see `journal_item`)."""
    business, tx = context._business, context._tx
    bounds = [bound for bound in business.registry.effects.get(model, ()) if bound.on == state]
    if not bounds:
        return []
    written = []
    document = load_document(business, tx, model, record_id)
    engine = engine_context(business, tx)
    for bound in bounds:
        try:
            drafts = prepare_all(engine, bound, rows_of(context, bound, document), record_id)
        except RowError as error:
            raise failure(business, context.user, bound, row_message(business, context.user, error)) from None
        values = [normalized(draft) for draft in drafts]
        if not is_balanced(bound, values):
            raise failure(business, context.user, bound, balance_message(business, context.user, bound, values))
        for draft in drafts:
            write(engine, bound, draft)
        if drafts:
            written.append(journal_item(business, bound, drafts, reversal=False))
    return written


def posted(tx, registry, bound, record_ids):
    """Saved rows of an effect for the given source ids: {source id: [target records in id order]}.

    The `source` index covers (source_model, source_id), so this is an index search.
    """
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


def has_postings(connection, registry, model, record):
    """Whether a record has posted effects: rows with it as source, and it is not void.

    Its stored calculated values are then frozen: corrections go through void or revise."""
    bounds = registry.effects.get(model, ())
    if not bounds or model.__state__ is not None and record._values.get(model.__state__) == 'void':
        return False
    name = registry.name_for(model)
    return any(connection.execute(f'SELECT 1 FROM {identifier(registry.name_for(bound.target))} '
                                  f'WHERE "{SOURCE}_model"=? AND "{SOURCE}_id"=? LIMIT 1', (name, record.id)).fetchone()
               for bound in bounds)


def mirror_values(bound, values):
    """The source values of a reversal row: the original's, with `reverse` applied."""
    fields = bound.target.__declared_fields__
    result = {name: value for name, value in values.items()
              if name not in (SOURCE, REVERSAL) and fields[name].compute is None and fields[name].query is None}
    bound.reverse.mirror(result)
    return result


def reverse(context, model, record_id):
    """Called when a record is voided: write a mirror row for each original row, marked as a reversal.

    Returns the Journal items of the targets that received mirror rows."""
    business, tx = context._business, context._tx
    engine = engine_context(business, tx)
    written = []
    for bound in business.registry.effects.get(model, ()):
        if bound.reverse is None:
            continue
        rows = posted(tx, business.registry, bound, [record_id]).get(record_id, [])
        originals = [row for row in rows if not row._values[REVERSAL]]
        if not originals or len(originals) != len(rows):
            # Nothing was posted, or this record's rows were reversed already.
            continue
        try:
            drafts = prepare_all(engine, bound, [mirror_values(bound, row._values) for row in originals], record_id, reversal=True)
        except RowError as error:
            raise failure(business, context.user, bound, 'The reversal of ' + row_message(business, context.user, error)) from None
        values = [normalized(draft) for draft in drafts]
        # Registration keeps a balanced pair balanced; this also guards a target that recalculates the pair.
        if not is_balanced(bound, values):
            raise failure(business, context.user, bound, balance_message(business, context.user, bound, values, 'The reversal of the '))
        for draft in drafts:
            write(engine, bound, draft)
        written.append(journal_item(business, bound, drafts, reversal=True))
    return written


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
    Everything is read from one read-only snapshot (a deferred SQLite transaction, or REPEATABLE READ READ
    ONLY on PostgreSQL), so writers are not blocked and records and rows are read consistently.
    """
    from .database import Session
    business = getattr(application, 'business', application)
    registry = business.registry
    differences = []
    with business.database._connection() as connection:
        connection.execute('BEGIN')
        try:
            tx = Session(connection, registry)
            engine = engine_context(business, tx)
            for model, bounds in registry.effects.items():
                table, name = identifier(registry.name_for(model)), registry.name_for(model)
                state_name = model.__state__
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
                        document = None
                        for bound in bounds:
                            rows = saved[bound].get(record.id, [])
                            state = record._values.get(state_name)
                            if document is None and (rows or state in bound.implied):
                                document = load_document(business, tx, model, record.id)
                            differences += compare(engine, bound, name, record.id, state, document, rows)
                for bound in bounds:
                    differences += orphans(tx, registry, bound, name, table)
        finally:
            connection.rollback()
    return differences


def orphans(tx, registry, bound, name, table):
    """Rows whose source record no longer exists, read in batches."""
    target_table = identifier(registry.name_for(bound.target))
    result, last = [], 0
    while True:
        rows = tx.connection.execute(f'SELECT * FROM {target_table} WHERE "{SOURCE}_model"=? AND id > ? ORDER BY id LIMIT ?',
                                     (name, last, BATCH)).fetchall()
        if not rows:
            return result
        items = [bound.target._from_row(row) for row in rows]
        last = items[-1].id
        sources = list({item._values[SOURCE][1] for item in items})
        existing = set()
        for start in range(0, len(sources), CHUNK):
            part = sources[start:start + CHUNK]
            existing |= {row[0] for row in tx.connection.execute(
                f'SELECT id FROM {table} WHERE id IN ({", ".join("?" for _ in part)})', part).fetchall()}
        result += [difference(bound, name, item._values[SOURCE][1], 'extra', item._values[REVERSAL], actual=item,
                              message='The source record does not exist') for item in items if item._values[SOURCE][1] not in existing]


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
        expected = [normalized(draft) for draft in prepare_all(engine, bound, produce(bound, document), record_id)]
        mirrors = []
        if state == 'void' and bound.reverse is not None:
            mirrors = [normalized(draft) for draft in
                       prepare_all(engine, bound, [mirror_values(bound, values) for values in expected], record_id, reversal=True)]
    except Exception as error:  # Any failure of the rows method is reported for this record.
        message = str(error) if isinstance(error, (RowError, VirtualRead)) else \
            f'{bound.source.__name__}.{bound.rows} failed: {type(error).__name__}: {error}'
        return result + [difference(bound, model_name, record_id, 'invalid', False, message=message)]
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
