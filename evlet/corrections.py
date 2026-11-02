"""Void and revise: engine corrections of locked documents, each in one transaction.

A void moves a record from one of its State's `voidable` states to "void" and records a reason. It is
neither an ordinary write nor a declared Move: every other value, saved results included, stays as it
is. A revise voids the record and creates a new draft copy whose `revises` Link points at the original.

Later phases plug in here: Effects reversal (`reverse_effects`), Sequence numbers on the copy
(`prepare_copy`, which clears them) and the Journal (`voided`, `revised`). Each hook runs in the
operation's transaction and savepoint, so a failure in any of them rolls the whole operation back.
"""

from .auth import AccessError
from .models import ValidationError, identifier
from .permissions import corrects

VOID = 'void'
REASON_LENGTH = 1000


# Hooks for later phases. Each is called once per operation, inside its transaction.

def reverse_effects(context, model, record_id, previous):
    """Called when a record is voided, after its state changed: the effects write their mirror rows."""
    from .effects import reverse
    reverse(context, model, record_id)


def prepare_copy(context, model, original, copy):
    """Called with a revision's draft copy before it settles. Every Sequence number is cleared, so the
    copy gets a fresh number when it reaches its assignment state (on create for assign_on="create")."""
    from .sequences import clear_for_copy
    copy._values.update(clear_for_copy(model, copy._values))


def voided(context, model, record_id, previous, reason):
    """Called once a void is saved. The Journal (Phase 8) records the reason here."""


def revised(context, model, original_id, copy_id, reason):
    """Called once a revise is saved, after `voided` (which is skipped when the original was void already).
    The Journal (Phase 8) records the link here."""


# Checks

def clean_reason(reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError({'reason': 'Give a reason'})
    reason = reason.strip()
    if len(reason) > REASON_LENGTH:
        raise ValidationError({'reason': f'Use at most {REASON_LENGTH} characters'})
    if '\x00' in reason:
        raise ValidationError({'reason': 'The reason must not contain NUL characters'})
    return reason


def has_revision(business, tx, model, record_id):
    """The id of a saved revision of this record, or None; `revises` is indexed, so this is an index search."""
    table = identifier(business.registry.name_for(model))
    row = tx.connection.execute(f'SELECT id FROM {table} WHERE "revises"=? ORDER BY id LIMIT 1', (record_id,)).fetchone()
    return None if row is None else row[0]


def state_problem(context, model, record, operation):
    """None when the saved state allows the operation, otherwise the message (state named only when readable).

    Void starts from a voidable state. Revise also starts from "void" when the record has no revision
    (for example after its revision draft was deleted); the record then stays void and gets a new copy.
    """
    from .states import state_shown
    current = record._values.get(model.__state__)
    if current in model.__declared_fields__[model.__state__].voidable:
        return None
    if operation == 'revise' and current == VOID:
        if has_revision(context._business, context._tx, model, record.id) is None:
            return None
        return 'This record already has a revision'
    verb = 'voided' if operation == 'void' else 'revised'
    return f'This record is {current}, which cannot be {verb}' if state_shown(context, model) else f'This record cannot be {verb} now'


def copy_problem(context, model, record):
    """Why this caller's copy would fail its create checks, predicted on the saved record, or None.

    The copy needs every row of the document to be visible to the caller, create permission for each
    collection that has rows, and every Link value (except `revises` and cleared unique fields) to name
    a record the caller can read. One statement per table level, and one per linked model and chunk.
    """
    from .documents import SavedRows
    from .fields import Link
    business, tx, user = context._business, context._tx, context.user
    policy = business.policy
    saved = SavedRows(context, model, record.id)
    if saved.hidden:
        return 'You cannot see every row of this record, so you cannot revise it'
    items = [(model, record)] + [(child.model, row) for (child, _), rows in saved.rows.items() for row in rows]
    if any(not policy.allows(user, definition, 'create') for definition, _ in items[1:]):
        return 'Your role cannot add the rows of this record, so you cannot revise it'
    wanted = {}
    for definition, item in items:
        internal = set(getattr(definition, '__internal_fields__', ()))
        for name, field in definition.__fields__.items():
            value = item._values.get(name)
            if isinstance(field, Link) and value is not None and name not in internal and name != 'revises' and not field.unique:
                wanted.setdefault(field.target, set()).add(value)
    for target, ids in wanted.items():
        if ids - policy.visible(tx, user, target, list(ids)):
            return 'This record links to a record you cannot read, so you cannot revise it'
    return None


def check(context, model, record_id, operation, reason):
    """The checks shared by void and revise, in order: the model supports it (404), the role and row scope
    (403 or 404), create permission for a revise (403), the reason (422), the saved state (409) and, for a
    revise, whether the caller's copy could be created (403)."""
    business, tx, user = context._business, context._tx, context.user
    if not corrects(model, operation):
        raise AccessError(404, f'This record cannot be {"voided" if operation == "void" else "revised"}')
    record = business.policy.record(tx, user, model, record_id, operation)
    if operation == 'revise':
        # The copy is a new draft of this caller's; its rows are created with the usual checks too.
        business.policy.require(user, model, 'create')
    reason = clean_reason(reason)
    message = state_problem(context, model, record, operation)
    if message is not None:
        raise AccessError(409, message)
    if operation == 'revise':
        message = copy_problem(context, model, record)
        if message is not None:
            raise AccessError(403, message)
    return record, reason


# Operations

def void(context, model, record_id, reason):
    """Void a saved record; returns its permission-filtered detail with every readable row."""
    business, tx, user = context._business, context._tx, context.user
    record, reason = check(context, model, record_id, 'void', reason)
    void_record(context, model, record, reason)
    return business.detail(tx, user, tx.get(model, record.id), row_limit=None)


def revise(context, model, record_id, reason):
    """Void a saved record (unless it is void already) and create its draft copy; returns the copy's detail."""
    from . import sequences
    from .documents import Change, freeze, freeze_draft, persist_rows, record_plan, settle, write_snapshot
    business, tx, user = context._business, context._tx, context.user
    record, reason = check(context, model, record_id, 'revise', reason)
    if record._values.get(model.__state__) != VOID:
        void_record(context, model, record, reason)
    copy = copy_draft(context, model, record)
    prepare_copy(context, model, record, copy)
    settle(context, copy, complete=True)
    # A number due on create is assigned to the copy as it would be to any new record.
    numbers = sequences.assign(context, model, copy, None, None)
    saved = write_snapshot(tx, copy)
    plan = business.registry.plan_for(model)
    if plan is not None:
        record_plan(tx, business.registry.name_for(model), saved.id, business.registry.formula_hashes.get(model))
    business.policy.record(tx, user, model, saved.id, 'create')
    children = persist_rows(context, model, copy, None, saved.id, {})
    reaction = getattr(model, 'changed', None)
    if reaction is not None:
        freeze_draft(copy)
        reaction(copy, Change('revise', user, None, freeze(copy.stored_values()), tuple(model.__fields__), freeze(children)))
    for name, number in numbers:
        sequences.assigned(context, model, saved.id, name, number)
    revised(context, model, record.id, saved.id, reason)
    business.changed(tx, model, saved.id)
    return business.detail(tx, user, tx.get(model, saved.id), row_limit=None)


def void_record(context, model, record, reason):
    """Write the void state and nothing else: no settlement, so saved results, locked values and any
    Sequence number stay (a record voided before its number was due keeps null)."""
    from .documents import Change, freeze, freeze_draft, load_draft
    business, tx, user = context._business, context._tx, context.user
    name, previous = model.__state__, record._values.get(model.__state__)
    tx.connection.execute(f'UPDATE {identifier(business.registry.name_for(model))} SET {identifier(name)}=? WHERE id=?', (VOID, record.id))
    reverse_effects(context, model, record.id, previous)
    reaction = getattr(model, 'changed', None)
    if reaction is not None:
        after, _ = load_draft(context, model, record.id, {}, preview=True)
        values = after.stored_values()
        freeze_draft(after)
        reaction(after, Change('void', user, freeze({**values, name: previous}), freeze(values), (name,), freeze({})))
    voided(context, model, record.id, previous, reason)
    business.changed(tx, model, record.id)


def copy_draft(context, model, record):
    """A new draft with the original's stored source values and owned rows at every depth.

    Rows are read with full access, one statement per table level, and keep their order. Calculated
    values are left out for the pipeline to recalculate. The State starts at its default and `revises`
    points at the original. Unique fields are cleared. Fields with an initial suggestion count as
    supplied, so the suggestion does not replace the copied value. Attachments are not copied.
    """
    from .calc import is_finite
    from .documents import Draft, owned_rows
    business, tx = context._business, context._tx

    def draft(definition, saved, parent=None):
        internal = set(getattr(definition, '__internal_fields__', ()))
        values = {name: saved._values.get(name) for name, field in definition.__fields__.items()
                  if name not in internal and field.compute is None and not is_finite(field)}
        # Unique values would repeat the original's, so the copy starts without them.
        values.update({name: None for name, field in definition.__fields__.items() if field.unique and name in values})
        item = Draft(definition, values, context=context, parent=parent)
        if 'evlet_position' in definition.__fields__:
            item._values['evlet_position'] = saved._values.get('evlet_position')
        object.__setattr__(item, '_submitted', frozenset(name for name, field in definition.__declared_fields__.items()
                                                         if getattr(field, 'initial', None) is not None and name in values))
        return item
    root = draft(model, record)
    root._values[model.__state__] = model.__declared_fields__[model.__state__].default
    root._values['revises'] = record.id
    level = [(model, record.id, root)]
    while level:
        following = []
        for child in business.children.values():
            owners = {saved_id: item for owner, saved_id, item in level if child.parent is owner}
            if not owners:
                continue
            found = owned_rows(tx, child, list(owners))
            for owner_id, item in owners.items():
                rows = []
                for saved in found.get(owner_id, ((), 0))[0]:
                    row = draft(child.model, saved, item)
                    rows.append(row)
                    following.append((child.model, saved.id, row))
                item._rows[child.name] = rows
        level = following
    return root


# Details and schema

def revised_by(business, tx, user, model, record_id):
    """The id of the revision that replaced a record when this caller can read it, else None."""
    copy_id = has_revision(business, tx, model, record_id)
    if copy_id is None or not business.policy.visible(tx, user, model, [copy_id]):
        return None
    return copy_id


def availability(business, context, model, record):
    """`_void` and `_revise` for a detail response: whether this caller may use them now, and why not.

    Listed only for roles that can read the State, like `_moves`; the reasons never name a state.
    """
    from .states import state_shown
    if not corrects(model, 'void') or not state_shown(context, model):
        return {}
    policy, tx, user = business.policy, context._tx, context.user
    result = {}
    for operation in ('void', 'revise'):
        if not corrects(model, operation):
            continue
        reason = None
        if not policy.allows(user, model, operation) or operation == 'revise' and not policy.allows(user, model, 'create'):
            reason = f'Your role cannot {operation} this record'
        elif policy.rule(user, model).scope is not None and not policy.visible(tx, user, model, [record.id], operation):
            reason = f'You cannot {operation} this record'
        elif state_problem(context, model, record, operation) is not None:
            reason = 'This record already has a revision' if operation == 'revise' and record._values.get(model.__state__) == VOID \
                else f'This record cannot be {"voided" if operation == "void" else "revised"} in its current state'
        elif operation == 'revise':
            reason = copy_problem(context, model, record)
        result['_' + operation] = {'allowed': reason is None, **({'reason': reason} if reason else {})}
    return result


def model_schema(business, user, model):
    """Additive schema keys of a voidable model: `voidable`, `revisable` and this role's `void` and
    `revise` permissions, for roles that can read the State. Other entries are unchanged."""
    if not corrects(model, 'void') or model.__state__ not in business.policy.fields(user, model):
        return {}, {}
    permissions = {operation: business.policy.allows(user, model, operation) for operation in ('void', 'revise') if corrects(model, operation)}
    return {'voidable': True, 'revisable': corrects(model, 'revise')}, permissions


# HTTP

def dispatch(business, scope, body, user, model, record_id, operation):
    """POST /api/{model}/{id}/void and /revise with {"reason": "..."}; If-Match is required."""
    with business.context(user) as context:
        if not corrects(model, operation):
            raise AccessError(404, f'This record cannot be {"voided" if operation == "void" else "revised"}')
        record = business.policy.record(context._tx, user, model, record_id, operation)
        if b'if-match' not in dict(scope.get('headers', [])):
            raise AccessError(428, 'A current If-Match revision is required')
        business.check_revision(scope, context._tx, user, record)
        if not isinstance(body, dict) or set(body) - {'reason'}:
            raise ValidationError({'_form': 'Send only a reason'})
        method = context.void if operation == 'void' else context.revise
        result = method(model, record_id, body.get('reason'))
        extra = business.availability(context, model, context._tx.get(model, result['id']))
    etag = business.revision(result)
    result = {**business.embedded(result), **extra}
    plan = business.registry.plan_for(model)
    if plan is not None:
        result['_calculation'] = {'language': plan['language'], 'model_schema_hash': plan['model_schema_hash']}
    if operation == 'void':
        return 200, result, [(b'etag', etag)]
    result['_original'] = record_id
    return 201, result, [(b'etag', etag), (b'location', f'/api/{business.registry.name_for(model)}/{result["id"]}'.encode())]
