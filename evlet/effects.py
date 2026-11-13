"""Effects: rows a document posts to other models when it enters a release state, reversed when it leaves it.

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
When a rule leaves `on` the engine writes mirror rows (`reverse`) marked `reversal=True`, before the
@state.exit hooks run. Originals are never changed.
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext
import inspect
import json
import logging
import re
from types import MappingProxyType

from .auth import AccessError, User
from .identity import RecordId, jsonable
from .naming import standard
from .fields import Boolean, Link, Reference
from .models import Model, ValidationError
from . import periods, qb, sections
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
    # States a record may be in while its rows are live: `on`.
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
            raise ValueError(f'{name}: effects are posted when a record enters a release state, so the model needs a State')
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
    """Check `on` and return (implied, after) states: both are just `on`.

    `on` is a locked release step: rows are posted when the record enters it and reversed when a rule leaves
    it, so a record has live rows exactly while it is in `on`, and ordinary writes cannot change it meanwhile."""
    field = model.__declared_fields__[model.__state__]
    label = f'{model.__name__} effect on {effect.target if isinstance(effect.target, str) else effect.target.__name__}'
    step = field.steps.get(effect.on)
    if step is None:
        raise ValueError(f'{label}: {effect.on!r} is not a state of {model.__name__}.{model.__state__}')
    if not (step.lock and step.release) or effect.on == field.default:
        raise ValueError(f'{label}: {effect.on!r} must be a locked release step that is not the default, '
                         'State.Step(..., lock=True, release=True), so posted rows always match the record')
    return frozenset((effect.on,)), frozenset((effect.on,))


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
    leaving = [rule.key for rule in model.__declared_fields__[model.__state__].rules if effect.on in rule.sources]
    if effect.reverse is None:
        if leaving:
            raise ValueError(f'{label}: rule {leaving[0]!r} leaves {effect.on!r}, which reverses the rows, '
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
            headings = sections.load(tx, child, list(owners))
            for owner_id, item in owners.items():
                rows = []
                for row in found.get(owner_id, ((), 0))[0]:
                    draft = Saved(child.model, dict(row._values), parent=item, record_id=row.id)
                    rows.append(draft)
                    following.append((child.model, row.id, draft))
                item._rows[child.name] = rows
                if owner_id in headings:
                    # The rows method may read the grouping: self.lines.sections().
                    item._sections[child.name] = sections.anchored(found[owner_id][0], headings[owner_id])
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

    def fields(self, user, model, *, write=False):
        return set(model.__declared_fields__)

    def scope(self, user, model):
        return qb.TRUE

    def load(self, tx, model, record_ids):
        """Read the records not read yet, one statement per model and chunk; absent ids are remembered too."""
        wanted = [record_id for record_id in dict.fromkeys(record_ids) if (model, record_id) not in self.cache]
        table = self.registry.name_for(model)
        for start in range(0, len(wanted), CHUNK):
            part = wanted[start:start + CHUNK]
            self.cache.update(((model, record_id), None) for record_id in part)
            for row in tx.connection.run(qb.select(table).where(qb.in_('id', part))):
                self.cache[model, row['id']] = model._from_row(row)

    def _scoped(self, tx, user, model, record_ids, operation, columns):
        if operation != 'read':
            return {}
        ids = [record_id for record_id in dict.fromkeys(record_ids) if isinstance(record_id, int) and not isinstance(record_id, bool)]
        self.load(tx, model, ids)
        found = {record_id: self.cache[model, record_id] for record_id in ids if self.cache[model, record_id] is not None}
        return dict.fromkeys(found) if columns == 'record.id' else found

    def record(self, tx, user, model, record_id, operation='read'):
        found = self._scoped(tx, user, model, [record_id], operation, 'record.*')
        if record_id not in found:
            raise AccessError(404, 'Record not found')
        return found[record_id]


class SessionPolicy(EnginePolicy):
    """The engine policy for settling a document after a trusted Session write: it may also write the document's rows."""
    def allows(self, user, model, operation):
        return operation in ('read', 'create', 'update', 'delete')

    def _scoped(self, tx, user, model, record_ids, operation, columns):
        return super()._scoped(tx, user, model, record_ids, 'read', columns)


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
    # A posted row's name and title are its own (the title falls back to its id), not part of the effect.
    return {name: draft._values.get(name) for name, field in draft._definition.__fields__.items() if not standard(field)}


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
    events are enabled. The source is the record being moved, so it is not checked again."""
    from .database import insert, storage_columns, stored_values
    from .documents import record_plan
    from .naming import has_title
    business, tx, target = engine._business, engine._tx, bound.target
    tx.check_references(target, {name: draft._values.get(name) for name, field in target.__declared_fields__.items()
                                 if isinstance(field, Reference) and name != SOURCE})
    from .currencies import check_amounts
    check_amounts(tx, target, draft._values)
    names, values = storage_columns(target, stored_values(target, draft._values), target.__fields__)
    table = business.registry.name_for(target)
    record_id = insert(tx.connection, table, names, values, id_title=has_title(target), actor=tx.actor)
    if business.registry.plan_for(target) is not None:
        record_plan(tx, business.registry.name_for(target), record_id, business.registry.formula_hashes.get(target))
    business.changed(tx, target, record_id)
    return record_id


def readable(business, user, target):
    """Whether this role reads every row of the target: read access without a row scope."""
    policy = business.policy
    return policy.allows(user, target, 'read') and policy.rule(user, target).scope is None


def failure(business, user, bound, message):
    """A move that cannot post: a ValidationError naming target details only to roles that can read them."""
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
        check_periods(context, model, bound, drafts)
        for draft in drafts:
            write(engine, bound, draft)
        if drafts:
            written.append(journal_item(business, bound, drafts, reversal=False))
    return written


def check_periods(context, model, bound, drafts):
    """Refuse effect rows dated in a closed period of their target (Meta.period). The error is on the posting
    document's own period field when it declares one, else on `_effects`."""
    tx = context._tx
    if getattr(tx, '_periods_unlocked', False):
        return
    for draft in drafts:
        lock = periods.closed(tx, bound.target, draft._values)
        if lock is not None:
            own = tx.registry.periods.get(model)
            if own is not None:
                raise ValidationError({own.field: periods.message(lock)})
            raise ValidationError({'_effects': f'{bound.target.__name__} rows: ' + periods.message(lock)})


def posted(tx, registry, bound, record_ids):
    """Saved rows of an effect for the given source ids: {source id: [target records in id order]}.

    The `source` index covers (source_model, source_id), so this is an index search.
    """
    table, name = registry.name_for(bound.target), registry.name_for(bound.source)
    ids, found = list(dict.fromkeys(record_ids)), {}
    for start in range(0, len(ids), CHUNK):
        part = ids[start:start + CHUNK]
        rows = tx.connection.run(qb.select(table).where(qb.eq(f'{SOURCE}_model', name), qb.in_(f'{SOURCE}_id', part)).order_by('id'))
        for row in rows:
            record = bound.target._from_row(row)
            found.setdefault(record._values[SOURCE][1], []).append(record)
    return found


def has_postings(connection, registry, model, record):
    """Whether a record has live posted effects: its latest row of some effect is an original, so it was posted
    and has not been reversed since.

    Its stored calculated values are then frozen: corrections go through a rule out of the posting state."""
    bounds = registry.effects.get(model, ())
    if not bounds:
        return False
    name = registry.name_for(model)
    for bound in bounds:
        row = connection.run(qb.select(registry.name_for(bound.target), REVERSAL).where(**{f'{SOURCE}_model': name, f'{SOURCE}_id': record.id})
                             .order_by('-id').limit(1)).one()
        if row is not None and not row[0]:
            return True
    return False


def ever_posted(connection, registry, model, record_id):
    """Whether a record ever posted rows of an effect, live or reversed since. Such a record is never deleted:
    its rows name it as their source."""
    name = registry.name_for(model)
    return any(connection.run(qb.select(registry.name_for(bound.target), qb.value(1))
                              .where(**{f'{SOURCE}_model': name, f'{SOURCE}_id': qb.value(record_id)}).limit(1)).one()
               for bound in registry.effects.get(model, ()))


def live(rows):
    """The original rows not cancelled yet, from one effect's rows of a record in id order.

    Each reversal batch (a rule leaving the posting state) cancels every original written before it, so the live rows are the
    originals after the last reversal: all of them before any reversal, none right after one."""
    last = max((row.id for row in rows if row._values[REVERSAL]), default=0)
    return [row for row in rows if not row._values[REVERSAL] and row.id > last]


def cycles(rows):
    """One effect's rows of a record split into postings: [(originals, reversals)] in id order.

    A posting is a batch of originals and the reversal batch that cancelled it (empty while it is live)."""
    result = []
    for row in rows:
        if row._values[REVERSAL]:
            if not result:
                result.append(([], []))
            result[-1][1].append(row)
        elif not result or result[-1][1]:
            result.append(([row], []))
        else:
            result[-1][0].append(row)
    return result


def effect_values(row):
    """A saved posted row's values as an effect compares them: without its own name and title."""
    return {name: value for name, value in row._values.items() if not standard(row.__fields__.get(name))}


def mirror_values(bound, values):
    """The source values of a reversal row: the original's, with `reverse` applied."""
    fields = bound.target.__declared_fields__
    result = {name: value for name, value in values.items()
              if name not in (SOURCE, REVERSAL) and fields[name].compute is None and fields[name].query is None and not standard(fields[name])}
    bound.reverse.mirror(result)
    return result


def reverse(context, model, record_id):
    """Called when a rule leaves an effect's state: write a mirror row for each live original row, marked as a
    reversal. Rows are only ever added, so earlier postings and their reversals stay as they are.

    Returns the Journal items of the targets that received mirror rows."""
    business, tx = context._business, context._tx
    engine = engine_context(business, tx)
    written = []
    for bound in business.registry.effects.get(model, ()):
        if bound.reverse is None:
            continue
        rows = posted(tx, business.registry, bound, [record_id]).get(record_id, [])
        # Only the live rows: those of earlier postings were reversed when the record left their state.
        originals = live(rows)
        if not originals:
            # Nothing was posted, or this record's rows were reversed already.
            continue
        try:
            # A mirror row keeps its original's date unless that period is closed (evlet/periods.py).
            mirrors = [periods.reopened(tx, bound.target, mirror_values(bound, row._values)) for row in originals]
            drafts = prepare_all(engine, bound, mirrors, record_id, reversal=True)
        except RowError as error:
            raise failure(business, context.user, bound, 'The reversal of ' + row_message(business, context.user, error)) from None
        values = [normalized(draft) for draft in drafts]
        # Registration keeps a balanced pair balanced; this also guards a target that recalculates the pair.
        if not is_balanced(bound, values):
            raise failure(business, context.user, bound, balance_message(business, context.user, bound, values, 'The reversal of the '))
        check_periods(context, model, bound, drafts)
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
    Everything is read from one read-only snapshot (connection.snapshot()), so writers are not blocked and
    records and rows are read consistently.
    """
    from .database import Session
    business = getattr(application, 'business', application)
    registry = business.registry
    differences = []
    with business.database._connection() as connection:
        connection.snapshot()
        try:
            tx = Session(connection, registry)
            engine = engine_context(business, tx)
            for model, bounds in registry.effects.items():
                name = registry.name_for(model)
                state_name = model.__state__
                last = 0
                while True:
                    records = [model._from_row(row) for row in tx.connection.run(
                        qb.select(name).where(qb.gt('id', last)).order_by('id').limit(BATCH))]
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
                    differences += orphans(tx, registry, bound, name)
        finally:
            connection.rollback()
    return differences


def orphans(tx, registry, bound, name):
    """Rows whose source record no longer exists, read in batches."""
    target_table = registry.name_for(bound.target)
    result, last = [], 0
    while True:
        rows = tx.connection.run(qb.select(target_table).where(qb.eq(f'{SOURCE}_model', name), qb.gt('id', last)).order_by('id').limit(BATCH)).all()
        if not rows:
            return result
        items = [bound.target._from_row(row) for row in rows]
        last = items[-1].id
        sources = list({item._values[SOURCE][1] for item in items})
        existing = set()
        for start in range(0, len(sources), CHUNK):
            part = sources[start:start + CHUNK]
            existing |= {row[0] for row in tx.connection.run(qb.select(name, 'id').where(qb.in_('id', part)))}
        result += [difference(bound, name, item._values[SOURCE][1], 'extra', item._values[REVERSAL], actual=item,
                              message='The source record does not exist') for item in items if item._values[SOURCE][1] not in existing]


def difference(bound, model_name, record_id, kind, reversal, *, expected=None, actual=None, message=None):
    def shown(values):
        return {name: field.serialize(values.get(name)) for name, field in bound.target.__fields__.items() if not standard(field)}
    result = {'model': model_name, 'record': RecordId(record_id), 'target': target_name(bound), 'kind': kind, 'reversal': bool(reversal)}
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
    """Differences between what a record should have posted for one effect and the saved rows.

    The rows are split into postings (see `cycles`). Postings a later reversal cancelled are checked on their own:
    their reversals must mirror their originals. The record's current posting, the last one, is compared
    with the rows the record produces now, as is the only posting of a record that never left its state.
    """
    postings = cycles(rows)
    result = []
    for originals, reversals in postings:
        for batch, flag in ((originals, False), (reversals, True)):
            if batch and not is_balanced(bound, [row._values for row in batch]):
                first, second = totals(bound, [row._values for row in batch])
                result.append(difference(bound, model_name, record_id, 'unbalanced', flag,
                                         message=f'{bound.balanced[0]} totals {first} and {bound.balanced[1]} totals {second}'))
    if state not in bound.after:
        # The record has not entered the effect's state, or left it: every posting it made must be cancelled.
        for originals, reversals in postings:
            if originals and not reversals:
                result += [difference(bound, model_name, record_id, 'extra', False, actual=row,
                                      message='The record has not posted this effect') for row in originals]
            else:
                result += settled(engine, bound, model_name, record_id, originals, reversals)
        return result
    for originals, reversals in postings[:-1]:
        result += settled(engine, bound, model_name, record_id, originals, reversals)
    originals, reversals = postings[-1] if postings else ([], [])
    if state not in bound.implied and not originals:
        # The record may have reached its state without entering `on`; with no rows there is nothing to compare.
        return result + [difference(bound, model_name, record_id, 'extra', True, actual=row,
                                    message='A reversal without original rows') for row in reversals]
    try:
        expected = [normalized(draft) for draft in prepare_all(engine, bound, produce(bound, document), record_id)]
        mirrors = []
    except Exception as error:  # Any failure of the rows method is reported for this record.
        message = str(error) if isinstance(error, (RowError, VirtualRead)) else \
            f'{bound.source.__name__}.{bound.rows} failed: {type(error).__name__}: {error}'
        return result + [difference(bound, model_name, record_id, 'invalid', False, message=message)]
    result += match(bound, model_name, record_id, expected, originals, False)
    result += match(bound, model_name, record_id, mirrors, reversals, True)
    return result


def settled(engine, bound, model_name, record_id, originals, reversals):
    """Differences of a cancelled posting: its reversal rows must mirror its original rows."""
    if bound.reverse is None:
        return [difference(bound, model_name, record_id, 'extra', True, actual=row, message='The effect has no reverse') for row in reversals]
    try:
        mirrors = [normalized(draft) for draft in prepare_all(
            engine, bound, [mirror_values(bound, row._values) for row in originals], record_id, reversal=True)]
    except RowError as error:
        return [difference(bound, model_name, record_id, 'invalid', True, message=str(error))]
    return match(bound, model_name, record_id, mirrors, reversals, True)


def match(bound, model_name, record_id, expected, actual, reversal):
    """Pair expected rows with saved rows: equal rows match, then leftovers pair in order as changed rows."""
    remaining = list(actual)
    unmatched = []
    for values in expected:
        found = next((row for row in remaining if effect_values(row) == values), None)
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
        parts.append('expected ' + json.dumps(jsonable(item['expected']), sort_keys=True))
    if 'actual' in item:
        parts.append('saved ' + json.dumps(jsonable(item['actual']), sort_keys=True))
    return '; '.join(parts)
