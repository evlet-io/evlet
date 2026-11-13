"""The Journal: one append-only history per record.

    class Meta:
        journal = Journal(tamper_evident=True)

A model with a State journals by default; `Journal()` turns it on for any root model and
`journal = None` turns it off. Entries live in `evlet_journal(id, model, record_id, kind, actor,
at, data, prev_hash, hash)`. The engine writes its entries in the transaction and savepoint of the
change they describe, so a failed operation leaves none. People add notes, logged messages, calls
and follow ups through `POST /api/{model}/{id}/journal`. Nothing is ever edited or deleted: no
route or function changes an entry, and database triggers refuse UPDATE and DELETE on the table.

With `tamper_evident=True` each entry stores the SHA-256 hash of its canonical JSON and the hash of
the record's previous entry, and `evlet journal verify <instance>` reports breaks in those chains.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import re
from types import MappingProxyType
from urllib.parse import parse_qs

from .auth import AccessError, User
from .fields import Link, Reference
from .identity import RecordId, text as text_id
from .models import ValidationError
from . import qb
from .adapter import Column, TableDefinition

SCALARS = ('rule', 'remark', 'level', 'base', 'rules_version')
SCALAR_COLUMNS = (Column('rule', 'TEXT'), Column('remark', 'TEXT'), Column('level', 'INTEGER'), Column('base', 'INTEGER'),
                  Column('rules_version', 'INTEGER'))
TABLE = TableDefinition('evlet_journal', (
    Column('model', 'TEXT', required=True), Column('record_id', 'INTEGER', required=True), Column('kind', 'TEXT', required=True),
    Column('actor', 'TEXT', required=True), Column('at', 'TEXT', required=True), Column('data', 'TEXT', required=True),
    Column('prev_hash', 'TEXT'), Column('hash', 'TEXT'),
    # The state manager's scalars (evlet/statemanager.py): the rule key, the remark, the approval level, the
    # release the entry builds on and the rule set version. Columns, so lists can filter on them.
    *SCALAR_COLUMNS), numbered=True)
# Models that have declared tamper_evident=True; verify() treats their unhashed entries as breaks.
HASHED = TableDefinition('evlet_journal_hashed', (Column('model', 'TEXT', primary=True),))
APPEND_ONLY = 'The journal is append only'
# Kinds the engine writes; `effects` is written by effects_posted() after a move posts or reverses effect rows,
# and `recomputed` by a Recompute migration that changed a record's stored results. Entries of kinds earlier
# releases wrote (voided, revised, unposted, reposted, confirmed, cancelled, reopened) stay readable.
ENGINE_KINDS = ('created', 'changed', 'moved', 'approved', 'rejected', 'released', 'restored', 'snapshot', 'number', 'file',
                'deleted', 'effects', 'recomputed')
# Kinds about the State: shown as `changed` to a reader who cannot read it.
STATE_KINDS = ('released', 'restored', 'snapshot', 'voided', 'revised', 'unposted', 'reposted', 'effects')
# Kinds people write through HTTP (or journal.post), and the values each takes.
USER_KINDS = ('note', 'message', 'call', 'follow_up', 'follow_up_done')
HTML_LENGTH = 40000
VALUES = {'note': ('text', 'html'), 'message': ('text', 'html', 'subject', 'party', 'direction'),
          'call': ('text', 'party', 'direction', 'minutes'), 'follow_up': ('text', 'due', 'assignee'),
          'follow_up_done': ('follow_up', 'text')}
# The actor of entries written without a signed in user (jobs, schedules, system sessions).
ENGINE = 'engine'
TEXT_LENGTH = 10000
SHORT_LENGTH = 200
PAGE, MAX_PAGE = 50, 100


@dataclass(frozen=True)
class Journal:
    """Meta.journal = Journal(tamper_evident=False). `journal = None` turns the journal off."""
    tamper_evident: bool = False

    def __post_init__(self):
        if type(self.tamper_evident) is not bool:
            raise ValueError('Journal(tamper_evident=...) is True or False')


def compile_journals(models, child_models, effects=()):
    """{model: Journal} for root models that journal: declared with Meta.journal, or by default those
    with a State or Effects (`effects` holds the models that declare Meta.effects).

    Table rows are journaled with their document, so a Meta.journal on a row model is an error.
    """
    result = {}
    for model in models:
        meta = getattr(model, 'Meta', None)
        declared = hasattr(meta, 'journal')
        value = getattr(meta, 'journal', None)
        if value is not None and not isinstance(value, Journal):
            raise ValueError(f'{model.__name__}: Meta.journal is Journal(...) or None')
        if model in child_models:
            if declared and value is not None and 'journal' in vars(meta):
                raise ValueError(f'{model.__name__}: Meta.journal is for root documents; Table rows are journaled with their document')
            continue
        if not declared:
            value = Journal() if model.__state__ is not None or model in effects else None
        if value is not None:
            result[model] = value
    return MappingProxyType(result)


def ensure_table(connection, *, hashed=()):
    """Create the table and its index, make it append-only (UPDATE, DELETE and an insert that replaces an
    entry are refused), and record the tamper-evident models `hashed`.

    Every statement is idempotent, so a database created by an earlier release gets new guards on start.
    """
    connection.ensure_table(TABLE)
    # Journals from before 0.4.6 gain the state manager's columns, empty on their old entries.
    present = connection.columns('evlet_journal')
    for column in SCALAR_COLUMNS:
        if column.name not in present:
            connection.add_column('evlet_journal', column)
    connection.ensure_index('evlet_journal_record', 'evlet_journal', ('model', 'record_id', 'id'))
    connection.ensure_table(HASHED)
    for name in hashed:
        connection.run(qb.insert('evlet_journal_hashed', model=name).on_conflict('model').do_nothing())
    connection.make_append_only('evlet_journal', APPEND_ONLY)


# Writing

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def entry_hash(model, record_id, kind, actor, at, data, prev_hash, scalars=None):
    """SHA-256 of the canonical JSON of an entry: [model, record_id, kind, actor, at, data, prev_hash], plus
    the state manager's scalars when the entry has any, so entries written before them verify as they were.

    `data` is the stored JSON text, itself canonical (sorted keys, no spaces)."""
    scalars = {key: value for key, value in (scalars or {}).items() if value is not None}
    return hashlib.sha256(canonical([model, record_id, kind, actor, at, data, prev_hash, *([scalars] if scalars else [])])
                          .encode('utf-8')).hexdigest()


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def actor_of(user):
    return str(user.id) if user is not None and user.id else ENGINE


def via(tx, user):
    """The AI assistant a request came through, the system name trusted code acted as, or None.
    The actor stays the person it acts as (the engine for the system)."""
    if getattr(user, 'system', None):
        return user.system
    access = getattr(user, 'access', None)
    if access is not None and access.origin in ('eva', 'schedule'):
        return 'Eva'
    assistant = None if access is None else access.assistant
    if assistant is None:
        return None
    row = tx.connection.run(qb.select('evlet_mcp_clients', 'client_name').where(id=assistant)).one() if assistant else None
    return row[0] if row is not None else 'Local MCP connector'


def append(business, tx, user, model, record_id, kind, data, **scalars):
    """Write one entry in this transaction and return its id. Callers have checked permissions.
    An AI assistant's write keeps the person as the actor and names the assistant in data["via"].
    `scalars` are the state manager's columns (SCALARS); the rule set version is added to entries with a rule."""
    through = via(tx, user)
    if through is not None:
        data = {**data, 'via': through}
    if scalars.get('rule') is not None:
        scalars.setdefault('rules_version', business.controls.version if getattr(business, 'controls', None) else None)
    return insert(tx.connection, business.registry, model, record_id, kind, actor_of(user), data, **scalars)


def tracking_values(registry, model, data):
    """Apply field policies at the storage boundary, including migration-generated differences."""
    result = dict(data)
    if 'fields' in data:
        fields = {}
        for name, change in data['fields'].items():
            field = model.__declared_fields__.get(name)
            if field is not None and field.tracking == 'exclude':
                continue
            fields[name] = ({key: None if value is None else '[masked]' for key, value in change.items()}
                            if field is not None and field.tracking == 'mask' else change)
        result['fields'] = fields
    if 'rows' in data:
        children = {child.name: child.model for child in registry.children if child.parent is model}
        result['rows'] = {
            name: {key: [tracking_values(registry, children[name], row) for row in value]
                   if key in ('added', 'updated', 'removed') and name in children else value
                   for key, value in collection.items()}
            for name, collection in data['rows'].items()
        }
    if data.get('field') in model.__declared_fields__ and 'number' in data:
        policy = model.__declared_fields__[data['field']].tracking
        if policy != 'value':
            result['number'] = '[masked]' if policy == 'mask' else None
    return result


def insert(connection, registry, model, record_id, kind, actor, data, **scalars):
    if set(scalars) - set(SCALARS):
        raise ValueError('Unknown journal column')
    data = tracking_values(registry, model, data)
    name = registry.name_for(model)
    at, text = now(), canonical(data)
    prev_hash = digest = None
    if registry.journals[model].tamper_evident:
        # Writers are serialized (one write transaction at a time), so the
        # previous entry cannot change before this one commits.
        row = connection.run(qb.select('evlet_journal', 'hash').where(model=name, record_id=record_id).order_by('-id').limit(1)).one()
        prev_hash = None if row is None else row[0]
        digest = entry_hash(name, record_id, kind, actor, at, text, prev_hash, scalars)
    entry = qb.insert('evlet_journal', model=name, record_id=record_id, kind=kind, actor=actor, at=at, data=text, prev_hash=prev_hash,
                      hash=digest, **{key: value for key, value in scalars.items() if value is not None})
    return connection.run(entry.returning_id()).id


def recomputed(connection, registry, changes):
    """`recomputed` entries, as the engine, for the records a Recompute migration changed:
    {(model, record_id): {"fields": ..., "rows": ...}} in the shape of a `changed` entry."""
    changes = {key: data for key, data in changes.items() if key[0] in registry.journals}
    if not changes:
        return
    ensure_table(connection,
                 hashed=[registry.name_for(model) for model, journal in registry.journals.items() if journal.tamper_evident])
    for (model, record_id), data in sorted(changes.items(), key=lambda item: (registry.name_for(item[0][0]), item[0][1])):
        insert(connection, registry, model, record_id, 'recomputed', ENGINE, data)


def skipped(business, model):
    """Stored fields a change entry leaves out: the row link and position, and Sequence numbers
    (each assignment is its own `number` entry)."""
    names = set(getattr(model, '__internal_fields__', ())) | {'evlet_position'}
    names |= {name for name, field in model.__declared_fields__.items() if field.kind == 'sequence'}
    names |= {child.link for child in business.children.values() if child.model is model}
    return names


def tree_changes(business, model, before, after, skip=()):
    """Field and row changes between two drafts of one record or row; None stands for no record.

    {"fields": {name: {"old": ..., "new": ...}}, "rows": {collection: {"added": [...], "updated": [...],
    "removed": [...], "reordered": true}}}. Each row item is {"id": ..., "fields": ..., "rows": ...}.
    """
    old, new = (before._values if before is not None else {}), (after._values if after is not None else {})
    names = skipped(business, model) | set(skip)
    fields = {name: {'old': field.serialize(old.get(name)), 'new': field.serialize(new.get(name))}
              for name, field in model.__fields__.items() if name not in names and old.get(name) != new.get(name)
              and field.tracking != 'exclude'}
    for name, change in fields.items():
        if model.__fields__[name].tracking == 'mask':
            fields[name] = {key: None if value is None else '[masked]' for key, value in change.items()}
    rows = {}
    for child in business.children.values():
        if child.parent is not model:
            continue
        old_rows = before._rows.get(child.name, []) if before is not None else []
        new_rows = after._rows.get(child.name, []) if after is not None else []
        old_by_id, new_ids = {row.id: row for row in old_rows}, {row.id for row in new_rows}
        collection = {
            'added': [{'id': row.id, **tree_changes(business, child.model, None, row)} for row in new_rows if row.id not in old_by_id],
            'updated': [{'id': row.id, **change} for row in new_rows if row.id in old_by_id
                        for change in (tree_changes(business, child.model, old_by_id[row.id], row),) if change],
            'removed': [{'id': row.id, **tree_changes(business, child.model, row, None)} for row in old_rows if row.id not in new_ids],
        }
        collection = {key: value for key, value in collection.items() if value}
        if [row.id for row in old_rows if row.id in new_ids] != [row.id for row in new_rows if row.id in old_by_id]:
            collection['reordered'] = True
            root = business.registry.root_of(model)
            if getattr(root, 'confirmable', False) or getattr(root, 'track_changes', False):
                collection['positions'] = [{'id': row.id, 'old': index, 'new': next(i for i, item in enumerate(new_rows) if item.id == row.id)}
                                           for index, row in enumerate(old_rows) if row.id in new_ids
                                           and index != next(i for i, item in enumerate(new_rows) if item.id == row.id)]
        # Section rows: {"added": [{"id", "label"}], "renamed": [{"id", "old", "new"}], "removed": [...], "moved": true}.
        old_sections = getattr(before, '_sections', {}).get(child.name, []) if before is not None else []
        new_sections = getattr(after, '_sections', {}).get(child.name, []) if after is not None else []
        if old_sections or new_sections:
            from .sections import changes as section_changes
            headings = section_changes(old_sections, new_sections, old_rows, new_rows)
            if headings:
                collection['sections'] = headings
        if collection:
            rows[child.name] = collection
    return {**({'fields': fields} if fields else {}), **({'rows': rows} if rows else {})}


def journals(context, model):
    return model in context._business.journals


def written(context, model, record_id, before, after, *, action=None):
    """`created` or `changed` after a document write (create, update, action, revision copy)."""
    if not journals(context, model):
        return
    data = tree_changes(context._business, model, before, after)
    if action is not None:
        data['action'] = action
    if before is None or data:
        append(context._business, context._tx, context.user, model, record_id, 'created' if before is None else 'changed', data)


def number(context, model, record_id, name, value):
    if journals(context, model):
        append(context._business, context._tx, context.user, model, record_id, 'number', {'field': name, 'number': value})


def deleted(context, model, record_id):
    """`deleted`: the record is gone, its history stays (ids are never reused)."""
    if journals(context, model):
        append(context._business, context._tx, context.user, model, record_id, 'deleted', {})


def file(business, tx, user, model, record_id, action, attachment):
    """`file` when an attachment is added or removed through the attachment routes."""
    if model in business.journals:
        append(business, tx, user, model, record_id, 'file', {'action': action, **attachment})


def effects_posted(context, model, record_id, targets):
    """`effects` after a move posted effect rows or wrote their mirror rows, in the same transaction.

    `targets` lists, per target model that received rows, {"target", "rows", "reversal"} and, for a
    balanced effect, "balanced" (see `evlet.effects.journal_item`). Nothing is written without rows.
    Readers see the targets they read fully and only when they can read the State (see `present`)."""
    if targets and journals(context, model):
        append(context._business, context._tx, context.user, model, record_id, 'effects', {'targets': list(targets)})


# User entries

def clean_text(values, name, *, required=False, limit=TEXT_LENGTH):
    value = values.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({name: 'Write some text' if required else 'Use text or leave it out'})
    value = value.strip()
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValidationError({name: 'Expected valid Unicode text'}) from None
    if len(value) > limit:
        raise ValidationError({name: f'Use at most {limit} characters'})
    if '\x00' in value:
        raise ValidationError({name: 'Text must not contain NUL characters'})
    return value


def clean_entry(business, tx, model, record_id, kind, values):
    """Validated data of a user entry, or ValidationError / AccessError(409)."""
    if kind not in USER_KINDS:
        raise ValidationError({'kind': 'Use one of ' + ', '.join(USER_KINDS)})
    if set(values) - set(VALUES[kind]):
        raise ValidationError({'_form': f'A {kind} takes only ' + ', '.join(VALUES[kind])})
    if values.get('html') is not None:
        # Written with the rich text composer: sanitised (mentions kept), and the plain text derived from it.
        from .richtext import sanitise
        markup = values['html']
        if not isinstance(markup, str) or len(markup) > HTML_LENGTH:
            raise ValidationError({'html': f'Use formatted text of at most {HTML_LENGTH} characters'})
        markup, plain = sanitise(markup, mentions=True)
        values = {**values, 'text': plain}
    else:
        markup = None
    data = {'text': clean_text(values, 'text', required=kind != 'follow_up_done')}
    if markup:
        data['html'] = markup
    if kind in ('message', 'call'):
        for name in ('subject', 'party') if kind == 'message' else ('party',):
            data[name] = clean_text(values, name, limit=SHORT_LENGTH)
        direction = values.get('direction', 'out')
        if direction not in ('in', 'out'):
            raise ValidationError({'direction': 'Use in or out'})
        data['direction'] = direction
    if kind == 'call':
        minutes = values.get('minutes')
        if minutes is not None and (type(minutes) is not int or not 0 <= minutes <= 1440):
            raise ValidationError({'minutes': 'Use whole minutes from 0 to 1440'})
        data['minutes'] = minutes
    if kind == 'follow_up':
        due = values.get('due')
        if due is not None:
            try:
                if not isinstance(due, str) or not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}', due):
                    raise ValueError()
                date.fromisoformat(due)
            except ValueError:
                raise ValidationError({'due': 'Use a date as YYYY-MM-DD'}) from None
        data['due'] = due
        data['assignee'] = assignee(business, tx, model, record_id, values.get('assignee'))
    if kind == 'follow_up_done':
        target = values.get('follow_up')
        name = business.registry.name_for(model)
        if type(target) is not int or tx.connection.run(
                qb.select('evlet_journal', 'id').where(id=target, model=name, record_id=record_id, kind='follow_up')).one() is None:
            raise ValidationError({'follow_up': 'Name a follow up of this record'})
        if target in completed(tx, name, record_id):
            raise AccessError(409, 'This follow up is already done')
        data['follow_up'] = target
    return data


def assignee(business, tx, model, record_id, value):
    """The id of an active user who can read the record, or None."""
    if value is None:
        return None
    row = tx.connection.run(qb.select('evlet_users', 'id', 'username', 'role', 'active').where(id=value)).one() \
        if type(value) is int and 1 <= value <= 2**63 - 1 else None
    if row is None or not row['active']:
        raise ValidationError({'assignee': 'Choose an active user'})
    try:
        business.policy.record(tx, User(row['id'], row['username'], row['role']), model, record_id)
    except AccessError:
        raise ValidationError({'assignee': 'This user cannot read this record'}) from None
    return row['id']


def completed(tx, name, record_id):
    """{follow up id: id of the entry that completed it} for one record."""
    rows = tx.connection.run(qb.select('evlet_journal', 'id', 'data').where(model=name, record_id=record_id, kind='follow_up_done')).all()
    return {json.loads(row['data'])['follow_up']: row['id'] for row in rows}


def post(context, model, record_id, kind, **values):
    """Add a user entry with the checks of POST /api/{model}/{id}/journal; returns it as the caller sees it.

    The model must journal (404), the caller must read the record within its row scope (403 or 404)
    and hold the `journal` operation (403). A follow up is completed once (409)."""
    if context.readonly:
        raise AccessError(403, 'This context is read only')
    model = context._model(model)
    from .documents import known_id
    record_id = known_id(record_id)
    # Like other document operations: refused inside an operation on the same record, and all or
    # nothing in its own savepoint even when trusted code catches a failure.
    key = (model, record_id)
    if key in context._active or len(context._active) >= 16:
        raise ValidationError({'_form': 'Recursive document change detected'})
    context._active.append(key)
    try:
        with context._savepoint():
            return add(context, model, record_id, kind, values)
    finally:
        context._active.pop()


def add(context, model, record_id, kind, values):
    from .documents import known_id
    business, tx, user, record_id = context._business, context._tx, context.user, known_id(record_id)
    if model not in business.journals:
        raise AccessError(404, 'This record has no journal')
    business.policy.record(tx, user, model, record_id)
    if not business.policy.allows(user, model, 'journal'):
        raise AccessError(403, 'Your role cannot write to this journal')
    data = clean_entry(business, tx, model, record_id, kind, values)
    entry_id = append(business, tx, user, model, record_id, kind, data)
    if data.get('html'):
        # Mentions: inbox rows now, emails once the entry is committed (route() sends them).
        from .mentions import record as mentioned
        context.__dict__.setdefault('_mention_emails', []).extend(mentioned(business, tx, user, model, record_id, entry_id, data['html']))
    business.changed(tx, model, record_id, journal=True)
    row = tx.connection.run(qb.select('evlet_journal', 'id', 'kind', 'actor', 'at', 'data').where(id=entry_id)).one()
    return shown(business, tx, user, model, record_id, [row])[0]


# Reading

class Visibility:
    """Batched row-scope checks: the first pass over a page collects (model, id) pairs, the second answers."""

    def __init__(self, business, tx, user):
        self.business, self.tx, self.user = business, tx, user
        self.wanted, self.known = {}, None

    def __call__(self, model, record_id):
        if self.known is None:
            self.wanted.setdefault(model, set()).add(record_id)
            return True
        return record_id in self.known.get(model, ())

    def resolve(self):
        """Look up what the first pass asked for; True when a second pass is needed."""
        if self.known is not None or not self.wanted:
            return False
        policy = self.business.policy
        self.known = {model: policy.visible(self.tx, self.user, model, list(ids)) for model, ids in self.wanted.items()}
        return True


def shown_value(business, user, field, value, visibility):
    """A Link or Reference value the caller may follow, else None (with True when it was hidden)."""
    policy = business.policy
    if value is None:
        return None, False
    if isinstance(field, Link) and field.ledger_target:
        # A ledger row id is a plain integer with no text form and no record to read behind it,
        # shown as record responses show it (permissions.serialize).
        return int(value), False
    if isinstance(field, Link):
        target = field.target
        if policy.rule(user, target).scope is not None and not visibility(target, RecordId(value)):
            return None, True
        # Older entries kept Link values as numbers; every entry shows the text id.
        return text_id(value), False
    elif isinstance(field, Reference):
        target = business.registry.models.get(value.get('model')) if isinstance(value, dict) else None
        if target is None or not policy.allows(user, target, 'read') or \
                policy.rule(user, target).scope is not None and not visibility(target, RecordId(value.get('id'))):
            return None, True
        return {**value, 'id': text_id(value.get('id'))}, False
    return value, False


def shown_changes(business, user, model, data, visibility):
    """The fields and rows of a change entry the caller may read, and whether anything was removed."""
    policy = business.policy
    readable, partial, result = policy.fields(user, model), False, {}
    fields = {}
    for name, change in data.get('fields', {}).items():
        field = model.__fields__.get(name)
        if field is None or name not in readable or field.tracking == 'exclude':
            partial = True
            continue
        shown = {}
        for key, value in change.items():
            if field.tracking == 'mask':
                shown[key], hidden = (None if value is None else '[masked]'), False
            else:
                shown[key], hidden = shown_value(business, user, field, value, visibility)
            partial |= hidden
        fields[name] = shown
    if fields:
        result['fields'] = fields
    rows = {}
    for name, collection in data.get('rows', {}).items():
        child = business.children.get((model, name))
        if child is None or not policy.allows(user, child.model, 'read'):
            partial = True
            continue
        # Removed rows cannot be checked against a row scope any more, so they are hidden under one.
        scoped = policy.rule(user, child.model).scope is not None
        shown = {}
        for part in ('added', 'updated', 'removed'):
            items = []
            for row in collection.get(part, ()):
                if scoped and (part == 'removed' or not visibility(child.model, RecordId(row['id']))):
                    partial = True
                    continue
                inner, hidden = shown_changes(business, user, child.model, row, visibility)
                partial |= hidden
                if inner or part != 'updated':
                    items.append({'id': text_id(row['id']), **inner})
            if items:
                shown[part] = items
        if collection.get('sections'):
            # Section labels are part of the Table this role reads; ids are shown in their text form.
            shown['sections'] = {key: [{**item, 'id': text_id(item['id'])} for item in value] if isinstance(value, list) else value
                                 for key, value in collection['sections'].items()}
        if collection.get('reordered'):
            shown['reordered'] = True
            if not scoped and 'positions' in collection:
                shown['positions'] = [{**item, 'id': text_id(item['id'])} for item in collection['positions']]
        if shown:
            rows[name] = shown
    if rows:
        result['rows'] = rows
    return result, partial


def shown_effects(business, user, model, data):
    """The targets of an `effects` entry the caller reads fully (read access without a row scope, as
    for `_effects` in details), with `balanced` only when it reads both balanced fields."""
    from .effects import readable
    policy, targets, partial = business.policy, [], False
    bounds = {business.registry.name_for(bound.target): bound for bound in business.registry.effects.get(model, ())}
    for item in data.get('targets', ()):
        target = business.registry.models.get(item.get('target'))
        if target is None or not readable(business, user, target):
            partial = True
            continue
        item = dict(item)
        bound = bounds.get(item['target'])
        if 'balanced' in item and (bound is None or bound.balanced is None or not set(bound.balanced) <= policy.fields(user, target)):
            del item['balanced']
            partial = True
        targets.append(item)
    return {'targets': targets}, partial


def present(business, user, model, row, visibility, done):
    """One entry as the caller sees it. Values the caller cannot read are removed and the entry is
    marked `partial`; entries about the State (moves, voids, revisions, effects) and numbers the
    caller cannot read are shown as `changed` with what remains."""
    policy = business.policy
    kind, data = row['kind'], json.loads(row['data'])
    through = data.pop('via', None)
    readable = policy.fields(user, model)
    state_shown = model.__state__ is not None and model.__state__ in readable
    partial = False
    if kind in ('created', 'changed', 'moved', 'recomputed', 'approved', 'rejected', 'confirmed', 'reconfirmed', 'cancelled', 'reopened',
                'post_confirmation_edit'):
        shown, partial = shown_changes(business, user, model, data, visibility)
        # Which action ran says something about the state it was available in.
        if 'action' in data and (model.__state__ is None or state_shown):
            shown['action'] = data['action']
        if kind == 'recomputed' and data.get('posted') and state_shown:
            # Recompute changed a posted record because an admin asked it to include them.
            shown['posted'] = True
        if kind in ('moved', 'approved', 'rejected'):
            if state_shown:
                shown.update({key: data[key] for key in ('from', 'to', 'move', 'level', 'pending') if key in data})
            else:
                kind, partial = 'changed', True
    elif kind in STATE_KINDS:
        shown = dict(data) if state_shown else {}
        if not state_shown:
            kind, partial = 'changed', True
        elif kind == 'revised' and not visibility(model, RecordId(data['copy'])):
            shown['copy'], partial = None, True
        elif kind == 'revised':
            shown['copy'] = text_id(data['copy'])
        elif kind == 'effects':
            shown, partial = shown_effects(business, user, model, data)
            if not shown['targets']:
                kind, shown = 'changed', {}
    elif kind == 'number':
        shown = data if data['field'] in readable else {}
        if data['field'] not in readable:
            kind, partial = 'changed', True
    elif kind == 'message' and 'field' in data:
        # A message a personal mailbox matched by one of the record's Email fields: the address is that
        # field's value, so a reader who can't read the field doesn't see it.
        shown = {key: value for key, value in data.items() if key != 'field'}
        if data['field'] not in readable:
            shown['party'], partial = None, True
    else:
        shown = data
    actor = row['actor']
    item = {'id': row['id'], 'kind': kind, 'actor': None if actor == ENGINE else int(actor), 'at': row['at'], 'data': shown}
    # The rule, remark, level and base release say what the state was, so only readers of the State see them.
    columns = row.keys() if hasattr(row, 'keys') else ()
    if state_shown:
        item.update({key: row[key] for key in ('rule', 'remark', 'level', 'base') if key in columns and row[key] is not None})
    if kind == 'follow_up':
        item['done'] = done.get(row['id'])
    if through is not None:
        item['via'] = through
    if partial:
        item['partial'] = True
    return item


def shown(business, tx, user, model, record_id, rows):
    """Entries as this caller sees them, with row scopes checked in batches."""
    done = completed(tx, business.registry.name_for(model), record_id) if any(row['kind'] == 'follow_up' for row in rows) else {}
    visibility = Visibility(business, tx, user)
    items = [present(business, user, model, row, visibility, done) for row in rows]
    if visibility.resolve():
        items = [present(business, user, model, row, visibility, done) for row in rows]
    return items


def entries(context, model, record_id, *, limit=PAGE, offset=0, kind=None):
    """One page of a record's journal, newest first, as GET /api/{model}/{id}/journal returns it.
    kind keeps entries of one stored kind, such as message."""
    business, tx, user = context._business, context._tx, context.user
    model = context._model(model)
    if model not in business.journals:
        raise AccessError(404, 'This record has no journal')
    business.policy.record(tx, user, model, record_id)
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE or type(offset) is not int or not 0 <= offset <= 2**63 - 1:
        raise ValidationError({'query': f'Use a limit of 1 to {MAX_PAGE} entries and a nonnegative offset'})
    name = business.registry.name_for(model)
    only = qb.TRUE if kind is None else qb.eq('kind', kind)
    rows = tx.connection.run(qb.select('evlet_journal', 'id', 'kind', 'actor', 'at', 'data', 'rule', 'remark', 'level', 'base')
                             .where(only, model=name, record_id=record_id)
                             .order_by('-id').limit(limit).offset(offset)).all()
    total = tx.connection.run(qb.select('evlet_journal', qb.count()).where(only, model=name, record_id=record_id)).value()
    items = shown(business, tx, user, model, record_id, rows)
    ids = {item['actor'] for item in items if item['actor'] is not None}
    ids |= {item['data']['assignee'] for item in items if item['kind'] == 'follow_up' and item['data'].get('assignee')}
    # People a note mentions show with their current name.
    from .richtext import mention_ids
    ids |= {user_id for item in items if isinstance(item.get('data'), dict) and item['data'].get('html') for user_id in mention_ids(item['data']['html'])}
    users = {}
    if ids:
        ids = sorted(ids)
        found = tx.connection.run(qb.select('evlet_users', 'id', 'username').where(qb.in_('id', ids))).all()
        users = {str(row['id']): row['username'] for row in found}
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset, 'users': users}


def summary(business, tx, model, record_id):
    """`_journal` for a detail response: the number of entries and of follow ups not done yet."""
    if model not in business.journals:
        return {}
    kinds = dict(tx.connection.run(qb.select('evlet_journal', 'kind', qb.count())
                                   .where(model=business.registry.name_for(model), record_id=record_id).group_by('kind')).all())
    return {'_journal': {'count': sum(kinds.values()), 'open_follow_ups': kinds.get('follow_up', 0) - kinds.get('follow_up_done', 0)}}


def schema(business, user, model):
    """The schema entry's `journal` key: whether entries are hashed and the kinds this role may post."""
    journal = business.journals.get(model)
    if journal is None:
        return {}
    kinds = list(USER_KINDS) if business.policy.allows(user, model, 'journal') else []
    return {'journal': {'tamper_evident': journal.tamper_evident, 'kinds': kinds}}


def route(business, scope, body, user, model, record_id):
    """GET (?limit=&offset=&kind=) and POST /api/{model}/{id}/journal."""
    from .database import Session
    method = scope['method']
    if method == 'POST':
        if not isinstance(body, dict) or not isinstance(body.get('kind'), str):
            raise ValidationError({'kind': 'Use one of ' + ', '.join(USER_KINDS)})
        values = dict(body)
        kind = values.pop('kind')
        with business.context(user) as context:
            entry = post(context, model, record_id, kind, **values)
            emails = list(getattr(context, '_mention_emails', ()))
        if emails:
            from .mentions import send_emails
            send_emails(business, emails)
        return 201, entry, []
    if method != 'GET':
        raise AccessError(405, 'Method not allowed')
    try:
        raw = scope.get('query_string', b'')
        query = parse_qs(raw.decode('ascii'), keep_blank_values=True, max_num_fields=3) if len(raw) <= 256 else None
        if query is None or set(query) - {'limit', 'offset', 'kind'} or any(len(values) != 1 for values in query.values()):
            raise ValueError()
        limit, offset = query.get('limit', [str(PAGE)])[0], query.get('offset', ['0'])[0]
        kind = query.get('kind', [None])[0]
        if not re.fullmatch('[0-9]{1,19}', limit) or not re.fullmatch('[0-9]{1,19}', offset) or kind not in (None, *ENGINE_KINDS, *USER_KINDS):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValidationError({'query': f'Use a limit of 1 to {MAX_PAGE} entries, a nonnegative offset and a known kind'})
    from .documents import Context
    with business.database._connection() as connection:
        # One read snapshot.
        connection.snapshot()
        context = Context(business, Session(connection, business.registry), user, readonly=True)
        return 200, entries(context, model, record_id, limit=int(limit), offset=int(offset), kind=kind), []


def history_route(business, scope, user, model, parts):
    """GET /api/{model}/{id}/history?limit=&offset=: the journal page Context.history returns."""
    from .identity import path_id
    if len(parts) != 5 or path_id(parts[3]) is None:
        raise AccessError(404, 'Record not found')
    if scope['method'] != 'GET':
        raise AccessError(405, 'Method not allowed')
    try:
        query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True)
        if set(query) - {'limit', 'offset'} or any(len(values) != 1 for values in query.values()):
            raise ValueError()
        page = {key: int(values[0]) for key, values in query.items()}
    except (ValueError, UnicodeError):
        raise ValidationError({'query': 'Use a limit and a nonnegative offset'}) from None
    with business.context(user, readonly=True) as context:
        return 200, context.history(model, path_id(parts[3]), **page), []


# Verification

def hashed_models(connection):
    """Names of the models that have declared tamper_evident=True, as recorded at startup."""
    if 'model' not in connection.columns('evlet_journal_hashed'):
        return set()
    return {row[0] for row in connection.run(qb.select('evlet_journal_hashed', 'model')).all()}


def verify_connection(connection, *, hashed=(), batch=1000):
    """(entries checked, breaks) for every hash chain, read on this connection (in one snapshot).

    A break is (entry id, model, record id, message). Per record, an entry with a hash must name the
    previous entry's hash and match its own content. An entry without a hash is a break when its model
    is in `hashed` (tamper evident) or when an earlier entry of the record has one. Other unhashed
    entries are skipped.
    """
    hashed = set(hashed)
    last, breaks, checked, after = {}, [], 0, 0
    while True:
        rows = connection.run(qb.select('evlet_journal', 'id', 'model', 'record_id', 'kind', 'actor', 'at', 'data', 'prev_hash', 'hash', *SCALARS)
                              .where(qb.gt('id', after)).order_by('id').limit(batch)).all()
        if not rows:
            return checked, breaks
        for row in rows:
            key = (row['model'], row['record_id'])
            previous = last.get(key)
            if row['hash'] is None:
                if row['model'] in hashed:
                    breaks.append((row['id'], *key, 'this entry has no hash, but its model is tamper evident'))
                elif previous is not None:
                    breaks.append((row['id'], *key, 'this entry has no hash, but earlier entries of the record do'))
                last[key] = None
                continue
            checked += 1
            if row['prev_hash'] != previous:
                breaks.append((row['id'], *key, 'the previous hash does not match the entry before it'))
            if entry_hash(row['model'], row['record_id'], row['kind'], row['actor'], row['at'], row['data'], row['prev_hash'],
                          {key: row[key] for key in SCALARS}) != row['hash']:
                breaks.append((row['id'], *key, 'the hash does not match the entry'))
            last[key] = row['hash']
        after = rows[-1]['id']


def verify(database):
    """verify_connection() over a database in one read snapshot. Tamper-evident
    models are those its registry declares plus those recorded in the database."""
    declared = {database.registry.name_for(model) for model, journal in getattr(database.registry, 'journals', {}).items()
                if journal.tamper_evident}
    with database._connection() as connection:
        if 'id' not in connection.columns('evlet_journal'):
            return 0, []
        connection.snapshot()
        return verify_connection(connection, hashed=declared | hashed_models(connection))
