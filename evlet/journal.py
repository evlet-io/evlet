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
from .models import ValidationError

TABLE = ('CREATE TABLE IF NOT EXISTS evlet_journal (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, '
         'record_id INTEGER NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL, '
         'prev_hash TEXT, hash TEXT)')
INDEX = 'CREATE INDEX IF NOT EXISTS evlet_journal_record ON evlet_journal (model, record_id, id)'
# Models that have declared tamper_evident=True; verify() treats their unhashed entries as breaks.
HASHED = 'CREATE TABLE IF NOT EXISTS evlet_journal_hashed (model TEXT PRIMARY KEY)'
APPEND_ONLY = 'The journal is append only'
# Kinds the engine writes; `effects` is written by effects_posted() after a move posts or a void reverses effect rows,
# and `recomputed` by a Recompute migration that changed a record's stored results.
ENGINE_KINDS = ('created', 'changed', 'moved', 'voided', 'revised', 'number', 'file', 'deleted', 'effects', 'recomputed')
# Kinds people write through HTTP (or journal.post), and the values each takes.
USER_KINDS = ('note', 'message', 'call', 'follow_up', 'follow_up_done')
VALUES = {'note': ('text',), 'message': ('text', 'subject', 'party', 'direction'),
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


def ensure_table(connection, *, postgres=False, hashed=()):
    """Create the table, its index and the triggers that refuse UPDATE and DELETE (and, on SQLite, an
    INSERT OR REPLACE over an existing entry), and record the tamper-evident models `hashed`.

    Every statement is idempotent, so a database created by an earlier release gets new guards on start.
    """
    connection.execute(TABLE)
    connection.execute(INDEX)
    connection.execute(HASHED)
    for name in hashed:
        connection.execute('INSERT INTO evlet_journal_hashed (model) VALUES (?) ON CONFLICT (model) DO NOTHING', (name,))
    if postgres:
        connection.execute("CREATE OR REPLACE FUNCTION evlet_journal_append_only() RETURNS trigger LANGUAGE plpgsql AS "
                           f"$$ BEGIN RAISE EXCEPTION '{APPEND_ONLY}'; END $$")
        if connection.execute("SELECT 1 FROM pg_trigger WHERE tgname='evlet_journal_append_only'").fetchone() is None:
            connection.execute('CREATE TRIGGER evlet_journal_append_only BEFORE UPDATE OR DELETE ON evlet_journal '
                               'FOR EACH ROW EXECUTE FUNCTION evlet_journal_append_only()')
            connection.execute('CREATE TRIGGER evlet_journal_no_truncate BEFORE TRUNCATE ON evlet_journal '
                               'FOR EACH STATEMENT EXECUTE FUNCTION evlet_journal_append_only()')
    else:
        for event in ('UPDATE', 'DELETE'):
            connection.execute(f'CREATE TRIGGER IF NOT EXISTS evlet_journal_no_{event.lower()} BEFORE {event} ON evlet_journal '
                               f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END")
        # REPLACE deletes the old row without firing DELETE triggers, so an insert may not reuse an id.
        connection.execute('CREATE TRIGGER IF NOT EXISTS evlet_journal_no_replace BEFORE INSERT ON evlet_journal '
                           'WHEN EXISTS (SELECT 1 FROM evlet_journal WHERE id = NEW.id) '
                           f"BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END")


# Writing

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def entry_hash(model, record_id, kind, actor, at, data, prev_hash):
    """SHA-256 of the canonical JSON of an entry: [model, record_id, kind, actor, at, data, prev_hash].

    `data` is the stored JSON text, itself canonical (sorted keys, no spaces)."""
    return hashlib.sha256(canonical([model, record_id, kind, actor, at, data, prev_hash]).encode('utf-8')).hexdigest()


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def actor_of(user):
    return str(user.id) if user is not None and user.id else ENGINE


def via(tx, user):
    """The AI assistant a request came through, or None. The actor stays the person it acts as."""
    access = getattr(user, 'access', None)
    assistant = None if access is None else access.assistant
    if assistant is None:
        return None
    row = tx.connection.execute('SELECT client_name FROM evlet_mcp_clients WHERE id=?', (assistant,)).fetchone() if assistant else None
    return row[0] if row is not None else 'Local MCP connector'


def append(business, tx, user, model, record_id, kind, data):
    """Write one entry in this transaction and return its id. Callers have checked permissions.
    An AI assistant's write keeps the person as the actor and names the assistant in data["via"]."""
    through = via(tx, user)
    if through is not None:
        data = {**data, 'via': through}
    return insert(tx.connection, business.registry, model, record_id, kind, actor_of(user), data)


def insert(connection, registry, model, record_id, kind, actor, data):
    name = registry.name_for(model)
    at, text = now(), canonical(data)
    prev_hash = digest = None
    if registry.journals[model].tamper_evident:
        # Writers are serialized (SQLite BEGIN IMMEDIATE, a PostgreSQL transaction lock), so the
        # previous entry cannot change before this one commits.
        row = connection.execute('SELECT hash FROM evlet_journal WHERE model=? AND record_id=? ORDER BY id DESC LIMIT 1',
                                 (name, record_id)).fetchone()
        prev_hash = None if row is None else row[0]
        digest = entry_hash(name, record_id, kind, actor, at, text, prev_hash)
    row = connection.execute('INSERT INTO evlet_journal (model, record_id, kind, actor, at, data, prev_hash, hash) '
                             'VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id',
                             (name, record_id, kind, actor, at, text, prev_hash, digest)).fetchone()
    return row[0]


def recomputed(connection, registry, changes, *, postgres=False):
    """`recomputed` entries, as the engine, for the records a Recompute migration changed:
    {(model, record_id): {"fields": ..., "rows": ...}} in the shape of a `changed` entry."""
    changes = {key: data for key, data in changes.items() if key[0] in registry.journals}
    if not changes:
        return
    ensure_table(connection, postgres=postgres,
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
              for name, field in model.__fields__.items() if name not in names and old.get(name) != new.get(name)}
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


def moved(context, model, record_id, previous, move, before, after):
    """`moved` with the states and the move's label, and anything else the move changed."""
    if journals(context, model):
        data = tree_changes(context._business, model, before, after, skip=(model.__state__,))
        append(context._business, context._tx, context.user, model, record_id, 'moved',
               {**data, 'from': previous, 'to': move.to, 'move': move.label})


def voided(context, model, record_id, previous, reason):
    if journals(context, model):
        append(context._business, context._tx, context.user, model, record_id, 'voided',
               {'from': previous, 'to': 'void', 'reason': reason})


def revised(context, model, original_id, copy_id, reason):
    """`revised` on the original, naming its copy; the copy's own history starts with `created`."""
    if journals(context, model):
        append(context._business, context._tx, context.user, model, original_id, 'revised', {'copy': copy_id, 'reason': reason})


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
    """`effects` after a move posted effect rows or a void wrote their mirror rows, in the same transaction.

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
    data = {'text': clean_text(values, 'text', required=kind != 'follow_up_done')}
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
        if type(target) is not int or tx.connection.execute(
                "SELECT 1 FROM evlet_journal WHERE id=? AND model=? AND record_id=? AND kind='follow_up'", (target, name, record_id)).fetchone() is None:
            raise ValidationError({'follow_up': 'Name a follow up of this record'})
        if target in completed(tx, name, record_id):
            raise AccessError(409, 'This follow up is already done')
        data['follow_up'] = target
    return data


def assignee(business, tx, model, record_id, value):
    """The id of an active user who can read the record, or None."""
    if value is None:
        return None
    row = tx.connection.execute('SELECT id, username, role, active FROM evlet_users WHERE id=?', (value,)).fetchone() \
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
    rows = tx.connection.execute("SELECT id, data FROM evlet_journal WHERE model=? AND record_id=? AND kind='follow_up_done'",
                                 (name, record_id)).fetchall()
    return {json.loads(row['data'])['follow_up']: row['id'] for row in rows}


def post(context, model, record_id, kind, **values):
    """Add a user entry with the checks of POST /api/{model}/{id}/journal; returns it as the caller sees it.

    The model must journal (404), the caller must read the record within its row scope (403 or 404)
    and hold the `journal` operation (403). A follow up is completed once (409)."""
    if context.readonly:
        raise AccessError(403, 'This context is read only')
    model = context._model(model)
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
    business, tx, user = context._business, context._tx, context.user
    if model not in business.journals:
        raise AccessError(404, 'This record has no journal')
    business.policy.record(tx, user, model, record_id)
    if not business.policy.allows(user, model, 'journal'):
        raise AccessError(403, 'Your role cannot write to this journal')
    data = clean_entry(business, tx, model, record_id, kind, values)
    entry_id = append(business, tx, user, model, record_id, kind, data)
    business.changed(tx, model, record_id, journal=True)
    row = tx.connection.execute('SELECT id, kind, actor, at, data FROM evlet_journal WHERE id=?', (entry_id,)).fetchone()
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
    if isinstance(field, Link):
        target = field.target
        if policy.rule(user, target).scope is not None and not visibility(target, value):
            return None, True
    elif isinstance(field, Reference):
        target = business.registry.models.get(value.get('model')) if isinstance(value, dict) else None
        if target is None or not policy.allows(user, target, 'read') or \
                policy.rule(user, target).scope is not None and not visibility(target, value.get('id')):
            return None, True
    return value, False


def shown_changes(business, user, model, data, visibility):
    """The fields and rows of a change entry the caller may read, and whether anything was removed."""
    policy = business.policy
    readable, partial, result = policy.fields(user, model), False, {}
    fields = {}
    for name, change in data.get('fields', {}).items():
        field = model.__fields__.get(name)
        if field is None or name not in readable:
            partial = True
            continue
        shown = {}
        for key, value in change.items():
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
                if scoped and (part == 'removed' or not visibility(child.model, row['id'])):
                    partial = True
                    continue
                inner, hidden = shown_changes(business, user, child.model, row, visibility)
                partial |= hidden
                if inner or part != 'updated':
                    items.append({'id': row['id'], **inner})
            if items:
                shown[part] = items
        if collection.get('reordered'):
            shown['reordered'] = True
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
    if kind in ('created', 'changed', 'moved', 'recomputed'):
        shown, partial = shown_changes(business, user, model, data, visibility)
        # Which action ran says something about the state it was available in.
        if 'action' in data and (model.__state__ is None or state_shown):
            shown['action'] = data['action']
        if kind == 'moved':
            if state_shown:
                shown.update({key: data[key] for key in ('from', 'to', 'move')})
            else:
                kind, partial = 'changed', True
    elif kind in ('voided', 'revised', 'effects'):
        shown = dict(data) if state_shown else {}
        if not state_shown:
            kind, partial = 'changed', True
        elif kind == 'revised' and not visibility(model, data['copy']):
            shown['copy'], partial = None, True
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
    only, values = ('', ()) if kind is None else (' AND kind=?', (kind,))
    rows = tx.connection.execute(f'SELECT id, kind, actor, at, data FROM evlet_journal WHERE model=? AND record_id=?{only} '
                                 'ORDER BY id DESC LIMIT ? OFFSET ?', (name, record_id, *values, limit, offset)).fetchall()
    total = tx.connection.execute(f'SELECT count(*) FROM evlet_journal WHERE model=? AND record_id=?{only}', (name, record_id, *values)).fetchone()[0]
    items = shown(business, tx, user, model, record_id, rows)
    ids = {item['actor'] for item in items if item['actor'] is not None}
    ids |= {item['data']['assignee'] for item in items if item['kind'] == 'follow_up' and item['data'].get('assignee')}
    users = {}
    if ids:
        ids = sorted(ids)
        found = tx.connection.execute(f'SELECT id, username FROM evlet_users WHERE id IN ({", ".join("?" for _ in ids)})', ids).fetchall()
        users = {str(row['id']): row['username'] for row in found}
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset, 'users': users}


def summary(business, tx, model, record_id):
    """`_journal` for a detail response: the number of entries and of follow ups not done yet."""
    if model not in business.journals:
        return {}
    row = tx.connection.execute("SELECT count(*), sum(CASE WHEN kind='follow_up' THEN 1 ELSE 0 END), "
                                "sum(CASE WHEN kind='follow_up_done' THEN 1 ELSE 0 END) FROM evlet_journal "
                                'WHERE model=? AND record_id=?', (business.registry.name_for(model), record_id)).fetchone()
    return {'_journal': {'count': row[0], 'open_follow_ups': (row[1] or 0) - (row[2] or 0)}}


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
            return 201, post(context, model, record_id, kind, **values), []
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
        # One read snapshot (PostgreSQL: repeatable read, read only).
        connection.execute('BEGIN')
        context = Context(business, Session(connection, business.registry), user, readonly=True)
        return 200, entries(context, model, record_id, limit=int(limit), offset=int(offset), kind=kind), []


# Verification

def hashed_models(connection, columns):
    """Names of the models that have declared tamper_evident=True, as recorded at startup."""
    if 'model' not in columns(connection, 'evlet_journal_hashed'):
        return set()
    return {row[0] for row in connection.execute('SELECT model FROM evlet_journal_hashed').fetchall()}


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
        rows = connection.execute('SELECT id, model, record_id, kind, actor, at, data, prev_hash, hash FROM evlet_journal '
                                  'WHERE id > ? ORDER BY id LIMIT ?', (after, batch)).fetchall()
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
            if entry_hash(row['model'], row['record_id'], row['kind'], row['actor'], row['at'], row['data'], row['prev_hash']) != row['hash']:
                breaks.append((row['id'], *key, 'the hash does not match the entry'))
            last[key] = row['hash']
        after = rows[-1]['id']


def verify(database):
    """verify_connection() over a database (SQLite or PostgreSQL) in one read snapshot. Tamper-evident
    models are those its registry declares plus those recorded in the database."""
    declared = {database.registry.name_for(model) for model, journal in getattr(database.registry, 'journals', {}).items()
                if journal.tamper_evident}
    with database._connection() as connection:
        if 'id' not in database.columns(connection, 'evlet_journal'):
            return 0, []
        connection.execute('BEGIN')
        return verify_connection(connection, hashed=declared | hashed_models(connection, database.columns))
