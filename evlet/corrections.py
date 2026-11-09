"""Void, revise, unpost and repost: engine corrections of locked and posted documents, each in one transaction.

A void moves a record from one of its State's `voidable` states to "void" and records a reason. It is
neither an ordinary write nor a declared Move: every other value, saved results included, stays as it
is. A revise voids the record and creates a new draft copy whose `revises` Link points at the original.

An unpost takes a posted record (one with live effect rows) back to the state it posted from, keeping its
id and Sequence numbers, and cancels its effect rows with reversal rows. A repost posts it again through
its normal posting move, which re-validates it and writes fresh rows.

Later phases plug in here: Effects reversal (`reverse_effects`), Sequence numbers on the copy
(`prepare_copy`, which clears them) and the Journal (`voided`, `revised`, `unposted`, `reposted`). Each
hook runs in the operation's transaction and savepoint, so a failure in any of them rolls the whole
operation back.
"""

import json

from .audit import actor_id, touch
from .auth import AccessError
from .fields import Link, Reference
from .identity import RecordId
from .models import ValidationError, checked_body, identifier
from . import periods, sections
from .permissions import CHUNK, corrects, trusted

VOID = 'void'
REASON_LENGTH = 1000
# Dependent records an unpost refusal names before it counts the rest.
NAMED = 5
VERBS = {'void': 'voided', 'revise': 'revised', 'unpost': 'unposted', 'repost': 'reposted'}


# Hooks for later phases. Each is called once per operation, inside its transaction.

def reverse_effects(context, model, record_id, previous):
    """Called when a record is voided, after its state changed: the effects write their mirror rows.
    Returns the Journal items of the targets that received them."""
    from .effects import reverse
    return reverse(context, model, record_id)


def prepare_copy(context, model, original, copy):
    """Called with a revision's draft copy before it settles. Every Sequence number is cleared, so the
    copy gets a fresh number when it reaches its assignment state (on create for assign_on="create")."""
    from .sequences import clear_for_copy
    copy._values.update(clear_for_copy(model, copy._values))


def voided(context, model, record_id, previous, reason):
    """Called once a void is saved. Writes the Journal's `voided` entry with the reason."""
    from . import journal
    journal.voided(context, model, record_id, previous, reason)


def revised(context, model, original_id, copy_id, reason):
    """Called once a revise is saved, after `voided` (which is skipped when the original was void already).
    Writes the Journal's `revised` entry on the original, naming the copy."""
    from . import journal
    journal.revised(context, model, original_id, copy_id, reason)


def unposted(context, model, record_id, previous, to, reason):
    """Called once an unpost is saved, before the `effects` entry of its reversal rows. Writes the Journal's
    `unposted` entry and a Controls audit row."""
    from . import journal
    journal.unposted(context, model, record_id, previous, to, reason)
    audit(context, model, record_id, f'Unposted from {previous} to {to} · {reason}')


def reposted(context, model, record_id, move, reason):
    """Called once a repost is saved, after the move's `moved` and `effects` entries. Writes the Journal's
    `reposted` entry and a Controls audit row."""
    from . import journal
    journal.reposted(context, model, record_id, move, reason)
    audit(context, model, record_id, f'Reposted by {move.label}' + (f' · {reason}' if reason else ''))


def audit(context, model, record_id, detail):
    """One row in the Controls audit log, in the operation's transaction (area "Records")."""
    business = context._business
    actor = getattr(context.user, 'username', None) or 'engine'
    business.controls.audit(context._tx.connection, actor, 'Records', f'{business.registry.name_for(model)} {record_id}', detail)


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
        raise AccessError(404, f'This record cannot be {VERBS[operation]}')
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
    from .documents import check_close
    check_close(context, model, record.id, VOID)
    void_record(context, model, record, reason)
    return business.detail(tx, user, tx.get(model, record.id), row_limit=None)


def revise(context, model, record_id, reason):
    """Void a saved record (unless it is void already) and create its draft copy; returns the copy's detail."""
    from . import sequences
    from .documents import Change, freeze, freeze_draft, persist_rows, record_plan, settle, write_snapshot
    business, tx, user = context._business, context._tx, context.user
    record, reason = check(context, model, record_id, 'revise', reason)
    if record._values.get(model.__state__) != VOID:
        from .documents import check_close
        check_close(context, model, record.id, VOID)
        void_record(context, model, record, reason)
    copy = copy_draft(context, model, record)
    prepare_copy(context, model, record, copy)
    settle(context, copy, complete=True)
    # A number due on create is assigned to the copy as it would be to any new record.
    numbers = sequences.assign(context, model, copy, None, None)
    # So is a name from the model's naming series (the copy's unique fields start empty).
    from .documents import host_today
    name = sequences.name_new(tx.connection, business.registry, model, copy._values, host_today(business), user.username, record=copy, context=context)
    if name:
        copy._values[business.registry.naming[model].field] = name
    periods.check(tx, model, after=copy._values)
    # The copy gets its own copy of each Image field's image (an image belongs to one record).
    from .images import clone
    clone(business, tx, user, model, copy._values)
    saved = write_snapshot(tx, copy)
    plan = business.registry.plan_for(model)
    if plan is not None:
        record_plan(tx, business.registry.name_for(model), saved.id, business.registry.formula_hashes.get(model))
    business.policy.record(tx, user, model, saved.id, 'create')
    children = persist_rows(context, model, copy, None, saved.id, {})
    reaction = getattr(model, 'changed', None)
    if reaction is not None:
        freeze_draft(copy)
        with trusted():
            reaction(copy, Change('revise', user, None, freeze(copy.stored_values()), tuple(model.__fields__), freeze(children)))
    from . import journal
    journal.written(context, model, saved.id, None, copy)
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
    # A record in a closed period (Meta.period) is neither voided nor unposted.
    periods.check(tx, model, before=record._values)
    tx.connection.execute(f'UPDATE {identifier(business.registry.name_for(model))} SET {identifier(name)}=? WHERE id=?', (VOID, record.id))
    touch(tx.connection, identifier(business.registry.name_for(model)), record.id, actor_id(user))
    reversed_rows = reverse_effects(context, model, record.id, previous)
    reaction = getattr(model, 'changed', None)
    if reaction is not None:
        after, _ = load_draft(context, model, record.id, {}, preview=True)
        values = after.stored_values()
        freeze_draft(after)
        with trusted():
            reaction(after, Change('void', user, freeze({**values, name: previous}), freeze(values), (name,), freeze({})))
    voided(context, model, record.id, previous, reason)
    from . import journal
    journal.effects_posted(context, model, record.id, reversed_rows)
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
            headings = sections.load(tx, child, list(owners))
            for owner_id, item in owners.items():
                rows = []
                for saved in found.get(owner_id, ((), 0))[0]:
                    row = draft(child.model, saved, item)
                    rows.append(row)
                    following.append((child.model, saved.id, row))
                item._rows[child.name] = rows
                if owner_id in headings:
                    # Section rows are copied too, as new section rows in the same places.
                    placed = sections.anchored(found[owner_id][0], headings[owner_id])
                    item._sections[child.name] = [sections.SectionRow(heading.label, at=heading.at,
                                                                      position=heading._values['evlet_position']) for heading in placed]
        level = following
    return root


# Unpost and repost

def posting_states(business, model):
    """The states a move posts effects on."""
    return {bound.on for bound in business.registry.effects.get(model, ())}


def before_posting(field, posting):
    """States a record can be in before it posts anything: reachable from the default without entering a posting state."""
    found, queue = [field.default], [field.default]
    while queue:
        state = queue.pop(0)
        for move in field.moves:
            if state in move.sources and move.to not in posting and move.to not in found:
                found.append(move.to)
                queue.append(move.to)
    return found


def unpost_state(context, model, record_id):
    """The state an unpost returns a record to: the state its posting move started from, or None when it cannot
    be told. When moves from several states post, the record's Journal names the one it took last."""
    business = context._business
    field = model.__declared_fields__[model.__state__]
    posting = posting_states(business, model)
    starts = [state for state in before_posting(field, posting)
              if any(move.to in posting and state in move.sources for move in field.moves)]
    if len(starts) == 1:
        return starts[0]
    if model not in business.journals:
        return None
    rows = context._tx.connection.execute("SELECT data FROM evlet_journal WHERE model=? AND record_id=? AND kind='moved' ORDER BY id DESC",
                                          (business.registry.name_for(model), record_id)).fetchall()
    for (data,) in rows:
        data = json.loads(data)
        if data.get('to') in posting and data.get('from') in starts:
            return data['from']
    return None


def repost_move(context, model, record):
    """The move that posts the record again from its current state, or None when there is none or several."""
    field = model.__declared_fields__[model.__state__]
    posting = posting_states(context._business, model)
    current = record._values.get(model.__state__)
    moves = [move for move in field.moves if current in move.sources and move.to in posting]
    return moves[0] if len(moves) == 1 else None


def was_posted(context, model, record_id):
    """Whether the record ever posted rows of an effect (they may all be reversed since)."""
    from .effects import ever_posted
    return ever_posted(context._tx.connection, context._business.registry, model, record_id)


def roots(business, tx, model, ids):
    """{row id: (root model, root id)} for records or Table rows of `model`, one statement per level and chunk."""
    owners = {child.model: child for child in business.children.values()}
    result = {record_id: (model, record_id) for record_id in ids}
    current = model
    while current in owners and result:
        child = owners[current]
        table, link = identifier(business.registry.name_for(current)), identifier(child.link)
        wanted = list({item[1] for item in result.values()})
        parents = {}
        for start in range(0, len(wanted), CHUNK):
            part = wanted[start:start + CHUNK]
            parents.update((row[0], row[1]) for row in tx.connection.execute(
                f'SELECT id, {link} FROM {table} WHERE id IN ({", ".join("?" for _ in part)})', part).fetchall())
        result = {key: (child.parent, parents[item[1]]) for key, item in result.items() if parents.get(item[1]) is not None}
        current = child.parent
    return result


def live_record(context, model, record_id):
    """Whether a record holds on to what it links to: it has a State, is not void, and is locked or has live postings."""
    from .effects import has_postings
    business, tx = context._business, context._tx
    state = model.__state__
    if state is None:
        return False
    row = tx.connection.execute(f'SELECT * FROM {identifier(business.registry.name_for(model))} WHERE id=?', (record_id,)).fetchone()
    if row is None:
        return False
    record = model._from_row(row)
    current = record._values.get(state)
    if current == VOID:
        return False
    return current in model.__declared_fields__[state].locked or has_postings(tx.connection, business.registry, model, record)


def owned(business, tx, model, record_id):
    """{model: [ids]}: the record itself, its Table rows at every depth and the effect rows it posted.
    One statement per table level and chunk, and one per effect."""
    from .effects import posted
    result = {model: [record_id]}
    level = {model: [record_id]}
    while level:
        following = {}
        for child in business.children.values():
            parents = level.get(child.parent)
            if not parents:
                continue
            table, link = identifier(business.registry.name_for(child.model)), identifier(child.link)
            for start in range(0, len(parents), CHUNK):
                part = parents[start:start + CHUNK]
                ids = [row[0] for row in tx.connection.execute(
                    f'SELECT id FROM {table} WHERE {link} IN ({", ".join("?" for _ in part)})', part).fetchall()]
                following.setdefault(child.model, []).extend(ids)
        level = {key: ids for key, ids in following.items() if ids}
        for key, ids in level.items():
            result.setdefault(key, []).extend(ids)
    for bound in business.registry.effects.get(model, ()):
        rows = posted(tx, business.registry, bound, [record_id]).get(record_id, [])
        if rows:
            result.setdefault(bound.target, []).extend(row.id for row in rows)
    return result


def dependents(context, model, record_id):
    """Live records that depend on this one: they (or their Table rows) link with a Link or a Reference to the
    record, to one of its Table rows or to one of the effect rows it posted, or they posted effect rows that do.
    Returns [(model, id)] in a stable order.

    Only Link fields whose target is one of those models are read, and every Reference field (it may name any
    model): one statement per field and chunk of ids, and one per level and chunk to find the documents of
    Table rows. These columns are often not indexed, so each is a scan of its table."""
    from .effects import SOURCE, targets_of
    business, tx = context._business, context._tx
    registry = business.registry
    targets = targets_of(registry)
    mine = owned(business, tx, model, record_id)
    names = {registry.name_for(item): ids for item, ids in mine.items()}
    found = {}
    for other in registry.models.values():
        table = identifier(registry.name_for(other))
        for field_name, field in other.__declared_fields__.items():
            if not field.stored:
                continue
            if isinstance(field, Link) and field.target in mine:
                queries = [(f'SELECT id FROM {table} WHERE {identifier(field_name)} IN ', (), mine[field.target])]
            elif isinstance(field, Reference) and not (other in targets and field_name == SOURCE):
                queries = [(f'SELECT id FROM {table} WHERE {identifier(field_name + "_model")}=? AND {identifier(field_name + "_id")} IN ',
                            (name,), ids) for name, ids in names.items()]
            else:
                continue
            ids = []
            for sql, first, values in queries:
                for start in range(0, len(values), CHUNK):
                    part = values[start:start + CHUNK]
                    ids += [row[0] for row in tx.connection.execute(sql + f'({", ".join("?" for _ in part)})', (*first, *part)).fetchall()]
            # Rows of this record linking to its own rows are not dependents.
            ids = [item for item in dict.fromkeys(ids) if item not in set(mine.get(other, ()))]
            if not ids:
                continue
            if other in targets:
                # An effect row: the record that posted it depends on this one.
                for start in range(0, len(ids), CHUNK):
                    part = ids[start:start + CHUNK]
                    for row in tx.connection.execute(f'SELECT "{SOURCE}_model", "{SOURCE}_id" FROM {table} '
                                                     f'WHERE id IN ({", ".join("?" for _ in part)})', part).fetchall():
                        source = registry.models.get(row[0])
                        if source is not None and row[1] is not None:
                            found[source, row[1]] = None
                continue
            for root in roots(business, tx, other, ids).values():
                found[root] = None
    found.pop((model, record_id), None)
    return [item for item in found if live_record(context, *item)]


def dependents_message(context, items):
    """A plain refusal naming the dependent records the caller can open; the others are only counted."""
    business, user, tx = context._business, context.user, context._tx
    shown, hidden = [], 0
    for item_model, item_id in items:
        if business.policy.allows(user, item_model, 'read') and business.policy.visible(tx, user, item_model, [item_id]):
            shown.append(f'{item_model.__name__} {RecordId(item_id)}')
        else:
            hidden += 1
    names = shown[:NAMED]
    more = len(shown) - len(names) + hidden
    if more:
        names.append(f'{more} other record{"s" if more != 1 else ""}' if names else
                     f'{more} record{"s" if more != 1 else ""} you cannot open')
    listed = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
    one = len(items) == 1
    return (f'This record cannot be unposted while {listed} {"depends" if one else "depend"} on it. '
            f'Void or unpost {"it" if one else "them"} first.')


def posting_problem(context, model, record, operation, *, with_dependents=True):
    """None when the saved record allows an unpost or a repost now, otherwise (status, message)."""
    from .effects import has_postings
    from .states import state_shown
    business = context._business
    current = record._values.get(model.__state__)
    if current == VOID:
        return 409, f'This record is void, so it cannot be {VERBS[operation]}'
    posted = has_postings(context._tx.connection, business.registry, model, record)
    if operation == 'unpost':
        if not posted:
            return 409, 'This record has nothing posted to cancel'
        if unpost_state(context, model, record.id) is None:
            return 409, 'The state this record posted from is not known, so it cannot be unposted'
        if with_dependents:
            items = dependents(context, model, record.id)
            if items:
                return 409, dependents_message(context, items)
        return None
    if posted:
        return 409, 'This record is posted already'
    if not was_posted(context, model, record.id):
        return 409, 'This record was never posted. Use its moves to post it'
    move = repost_move(context, model, record)
    if move is None:
        if state_shown(context, model):
            return 409, f'This record is {current}, and no single move posts it from there'
        return 409, 'This record cannot be reposted now'
    if asks_values(business, model, move):
        return 409, f'{move.label} asks for values, so post the record with {move.label} itself'
    return None


def asks_values(business, model, move):
    """Whether the posting move asks for a value it may require; a repost sends none, so the move is the way."""
    from .states import move_asks
    fields = model.__declared_fields__
    return any(fields[name].required or fields[name].required_when is not None for name in move_asks(business, model, move))


def check_posting(context, model, record_id, operation, reason):
    """The checks of unpost and repost, in order: the model supports it (404), the role and row scope (403 or
    404), the reason (422; optional for a repost) and the saved record (409)."""
    business, tx, user = context._business, context._tx, context.user
    if not corrects(model, operation):
        raise AccessError(404, f'This record cannot be {VERBS[operation]}')
    record = business.policy.record(tx, user, model, record_id, operation)
    reason = clean_reason(reason) if operation == 'unpost' or reason not in (None, '') else None
    problem = posting_problem(context, model, record, operation)
    if problem is not None:
        raise AccessError(*problem)
    return record, reason


def unpost(context, model, record_id, reason):
    """Take a posted record back to the state it posted from and cancel its effect rows; returns its detail.

    The record keeps its id and Sequence numbers; nothing is deleted. Every live effect row it posted gets a
    reversal row, as for a void."""
    from .documents import Change, freeze, freeze_draft, load_draft
    business, tx, user = context._business, context._tx, context.user
    record, reason = check_posting(context, model, record_id, 'unpost', reason)
    name, previous = model.__state__, record._values.get(model.__state__)
    to = unpost_state(context, model, record.id)
    periods.check(tx, model, before=record._values)
    tx.connection.execute(f'UPDATE {identifier(business.registry.name_for(model))} SET {identifier(name)}=? WHERE id=?', (to, record.id))
    touch(tx.connection, identifier(business.registry.name_for(model)), record.id, actor_id(user))
    reversed_rows = reverse_effects(context, model, record.id, previous)
    reaction = getattr(model, 'changed', None)
    if reaction is not None:
        after, _ = load_draft(context, model, record.id, {}, preview=True)
        values = after.stored_values()
        freeze_draft(after)
        with trusted():
            reaction(after, Change('unpost', user, freeze({**values, name: previous}), freeze(values), (name,), freeze({})))
    unposted(context, model, record.id, previous, to, reason)
    from . import journal
    journal.effects_posted(context, model, record.id, reversed_rows)
    business.changed(tx, model, record.id)
    return business.detail(tx, user, tx.get(model, record.id), row_limit=None)


def repost(context, model, record_id, reason=None):
    """Post an unposted record again through its posting move, with every check of that move; returns its detail."""
    from .documents import operate
    record, reason = check_posting(context, model, record_id, 'repost', reason)
    move = repost_move(context, model, record)
    result = operate(context, model, record.id, {}, move=move.to)
    reposted(context, model, record.id, move, reason)
    return result


# Details and schema

def revised_by(business, tx, user, model, record_id):
    """The id of the revision that replaced a record when this caller can read it, else None."""
    copy_id = has_revision(business, tx, model, record_id)
    if copy_id is None or not business.policy.visible(tx, user, model, [copy_id]):
        return None
    return RecordId(copy_id)


def availability(business, context, model, record):
    """`_void`, `_revise`, `_unpost` and `_repost` for a detail response: whether this caller may use them now,
    and why not.

    Listed only for roles that can read the State, like `_moves`; the reasons never name a state. Unpost and
    repost are listed only for roles that have them, and only when they apply: `_unpost` on a posted record,
    `_repost` on an unposted one. Records that depend on a posted one are checked when it is unposted, not here.
    """
    from .states import state_shown
    if not state_shown(context, model):
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
                else f'This record cannot be {VERBS[operation]} in its current state'
        elif operation == 'revise':
            reason = copy_problem(context, model, record)
        result['_' + operation] = {'allowed': reason is None, **({'reason': reason} if reason else {})}
    if corrects(model, 'unpost') and record._values.get(model.__state__) != VOID:
        from .effects import has_postings
        posted = has_postings(tx.connection, business.registry, model, record)
        applies = {'unpost': posted, 'repost': not posted and was_posted(context, model, record.id)}
    else:
        applies = {}
    for operation, applied in applies.items():
        if not applied or not policy.allows(user, model, operation):
            continue
        if operation == 'repost':
            # A repost runs the posting move: it is offered only when this caller could run that move without values.
            move = repost_move(context, model, record)
            if move is None or user.role not in move.roles or asks_values(business, model, move) \
                    or not policy.allows(user, model, 'update') \
                    or policy.rule(user, model).scope is not None and not policy.visible(tx, user, model, [record.id], 'update'):
                continue
        problem = posting_problem(context, model, record, operation, with_dependents=False)
        reason = problem[1] if problem is not None else None
        if reason is None and policy.rule(user, model).scope is not None and not policy.visible(tx, user, model, [record.id], operation):
            reason = f'You cannot {operation} this record'
        result['_' + operation] = {'allowed': reason is None, **({'reason': reason} if reason else {})}
    return result


def model_schema(business, user, model):
    """Additive schema keys, for roles that can read the State: `voidable` and `revisable` on a voidable model,
    `unpostable` on a model whose effects can be cancelled, and this role's `void`, `revise`, `unpost` and
    `repost` permissions where the model supports them. Other entries are unchanged."""
    if model.__state__ is None or model.__state__ not in business.policy.fields(user, model):
        return {}, {}
    flags = {}
    if corrects(model, 'void'):
        flags.update({'voidable': True, 'revisable': corrects(model, 'revise')})
    if corrects(model, 'unpost'):
        flags['unpostable'] = True
    permissions = {operation: business.policy.allows(user, model, operation) for operation in VERBS if corrects(model, operation)}
    return flags, permissions


# HTTP

def dispatch(business, scope, body, user, model, record_id, operation):
    """POST /api/{model}/{id}/void, /revise, /unpost and /repost with {"reason": "..."}; If-Match is required.
    A repost's reason is optional."""
    with business.context(user) as context:
        if not corrects(model, operation):
            raise AccessError(404, f'This record cannot be {VERBS[operation]}')
        record = business.policy.record(context._tx, user, model, record_id, operation)
        if b'if-match' not in dict(scope.get('headers', [])):
            raise AccessError(428, 'A current If-Match revision is required')
        business.check_revision(scope, context._tx, user, record)
        checked_body(body, {'reason'}, 'Send only a reason')
        result = getattr(context, operation)(model, record_id, body.get('reason'))
        extra = business.availability(context, model, context._tx.get(model, result['id']))
    etag = business.revision(result)
    result = {**business.embedded(result), **extra}
    plan = business.registry.plan_for(model)
    if plan is not None:
        result['_calculation'] = {'language': plan['language'], 'model_schema_hash': plan['model_schema_hash']}
    if operation != 'revise':
        return 200, result, [(b'etag', etag)]
    result['_original'] = record_id
    return 201, result, [(b'etag', etag), (b'location', f'/api/{business.registry.name_for(model)}/{result["id"]}'.encode())]
