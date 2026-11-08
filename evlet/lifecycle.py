"""Confirmation, immutable revisions and history on the document transaction pipeline."""

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import inspect
import json

from .auth import AccessError
from .identity import RecordId, path_id, text as text_id

SOURCES = ('User', 'API', 'Automation', 'Scheduler', 'System', 'Integration')
OPERATIONS = {'confirm': ('draft', 'confirmed'), 'cancel': ('confirmed', 'cancelled'), 'reopen': ('cancelled', 'draft')}


def handler(kind, operation):
    def decorate(function=None, *, priority=0):
        if type(priority) is not int:
            raise ValueError('Handler priority must be an integer')
        def apply(method):
            if len(inspect.signature(method).parameters) != 1:
                raise ValueError('Lifecycle handlers take only self')
            method.__lifecycle__ = (*getattr(method, '__lifecycle__', ()), (kind, operation, priority))
            return method
        return apply(function) if function is not None else apply
    return decorate


validate = SimpleNamespace(confirm=handler('validate', 'confirm'), cancel=handler('validate', 'cancel'))


def install_decorators(action):
    action.confirm = handler('action', 'confirm')
    action.cancel = handler('action', 'cancel')


def configure_model(model):
    """Called after fields are collected; no stored columns are injected into existing tables."""
    for option in ('confirmable', 'track_changes'):
        value = getattr(model, option, False)
        if type(value) is not bool:
            raise ValueError(f'{model.__name__}.{option} must be a boolean')
    if not getattr(model, 'confirmable', False):
        return
    if model.__state__ is not None or getattr(model, '__revisable__', False) or getattr(model.Meta, 'effects', None):
        raise ValueError(f'{model.__name__}: confirmable documents use lifecycle decorators, not State/Move, Meta.revisable or Meta.effects')
    reserved = {'save', 'confirm', 'cancel', 'reopen', 'delete', 'history', 'revisions', 'diff', 'snapshot', 'lifecycle_state', 'revision'}
    if reserved & (set(model.__app_fields__) | set(model.__tables__)):
        raise ValueError(f'{model.__name__}: reserved lifecycle field or table name')
    if any(name in vars(model) for name in ('confirm', 'cancel', 'reopen')):
        raise ValueError('Lifecycle operation names belong to the framework; choose another handler name')
    number = getattr(model, 'document_number_field', None)
    if number is not None and (not isinstance(number, str) or number not in model.__fields__):
        raise ValueError('document_number_field must name a stored field')
    labels(model)
    # A subclass may override or remove a handler. MRO lookup decides which implementation runs.
    handlers = []
    for name in sorted({name for cls in model.__mro__ for name in vars(cls)}):
        method = getattr(model, name, None)
        for kind, operation, priority in getattr(method, '__lifecycle__', ()):
            handlers.append((kind, operation, priority, name))
    model.__lifecycle_handlers__ = tuple(sorted(handlers, key=lambda item: (item[2], item[3])))


def labels(model):
    defaults = {'draft': 'Draft', 'confirmed': 'Confirmed', 'cancelled': 'Cancelled',
                'confirm': 'Confirm', 'cancel': 'Cancel', 'reopen': 'Reopen'}
    custom = getattr(model, 'lifecycle_labels', {})
    if not isinstance(custom, dict) or set(custom) - set(defaults) or any(not isinstance(value, str) or not value.strip() for value in custom.values()):
        raise ValueError('lifecycle_labels maps lifecycle operations and states to nonempty text')
    return {**defaults, **custom}


def ensure_tables(connection, *, postgres=False):
    connection.execute('CREATE TABLE IF NOT EXISTS evlet_document_lifecycle (model TEXT NOT NULL, record_id INTEGER NOT NULL, '
                       "state TEXT NOT NULL CHECK(state IN ('draft','confirmed','cancelled')), revision INTEGER NOT NULL CHECK(revision>=0), "
                       'confirmation_id TEXT, PRIMARY KEY(model,record_id))')
    connection.execute('CREATE TABLE IF NOT EXISTS evlet_document_revisions (model TEXT NOT NULL, record_id INTEGER NOT NULL, '
                       'revision INTEGER NOT NULL CHECK(revision>0), execution_id TEXT NOT NULL UNIQUE, at TEXT NOT NULL, '
                       'actor TEXT NOT NULL, data TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(model,record_id,revision))')
    # Reuse the journal's append-only contract, including SQLite REPLACE and PostgreSQL TRUNCATE.
    if postgres:
        connection.execute("CREATE OR REPLACE FUNCTION evlet_revision_append_only() RETURNS trigger LANGUAGE plpgsql AS "
                           "$$ BEGIN RAISE EXCEPTION 'Revisions are immutable'; END $$")
        if connection.execute("SELECT 1 FROM pg_trigger WHERE tgname='evlet_revision_append_only'").fetchone() is None:
            connection.execute('CREATE TRIGGER evlet_revision_append_only BEFORE UPDATE OR DELETE ON evlet_document_revisions '
                               'FOR EACH ROW EXECUTE FUNCTION evlet_revision_append_only()')
            connection.execute('CREATE TRIGGER evlet_revision_no_truncate BEFORE TRUNCATE ON evlet_document_revisions '
                               'FOR EACH STATEMENT EXECUTE FUNCTION evlet_revision_append_only()')
    else:
        for operation in ('UPDATE', 'DELETE'):
            connection.execute(f'CREATE TRIGGER IF NOT EXISTS evlet_revision_no_{operation.lower()} BEFORE {operation} '
                               "ON evlet_document_revisions BEGIN SELECT RAISE(ABORT, 'Revisions are immutable'); END")
        connection.execute('CREATE TRIGGER IF NOT EXISTS evlet_revision_no_replace BEFORE INSERT ON evlet_document_revisions '
                           'WHEN EXISTS (SELECT 1 FROM evlet_document_revisions WHERE model=NEW.model AND record_id=NEW.record_id AND revision=NEW.revision) '
                           "OR EXISTS (SELECT 1 FROM evlet_document_revisions WHERE execution_id=NEW.execution_id) "
                           "BEGIN SELECT RAISE(ABORT, 'Revisions are immutable'); END")


def metadata(context, model, record_id):
    row = context._tx.connection.execute('SELECT state, revision, confirmation_id FROM evlet_document_lifecycle WHERE model=? AND record_id=?',
                                         (context._business.registry.name_for(model), record_id)).fetchone()
    return {'state': row['state'], 'revision': row['revision'], 'confirmation_id': row['confirmation_id']} if row else {
        'state': 'draft', 'revision': 0, 'confirmation_id': None}


def require_confirmable(context, model, record_id):
    if not getattr(model, 'confirmable', False):
        raise AccessError(404, 'This model is not confirmable')
    context._business.policy.record(context._tx, context.user, model, record_id)


def check_delete(context, model, record_id):
    if getattr(model, 'confirmable', False):
        current = metadata(context, model, record_id)
        if current['state'] != 'draft' or current['revision']:
            raise AccessError(409, 'Only a never-confirmed draft may be deleted')


def check_write(context, model, draft, before):
    if before is None or not getattr(model, 'confirmable', False):
        return
    current = metadata(context, model, before.id)
    if current['revision']:
        numbering = context._business.registry.naming.get(model)
        number = getattr(model, 'document_number_field', None) or (numbering.field if numbering else 'number' if 'number' in model.__fields__ else None)
        if number and before._values.get(number) != draft._values.get(number):
            raise AccessError(409, 'The document number is permanent after its first confirmation')
    if current['state'] == 'draft':
        return
    for name, field in model.__fields__.items():
        if before._values.get(name) != draft._values.get(name) and not field.editable_when_locked:
            raise AccessError(409, 'This document is locked; cancel and reopen it before editing protected fields')
    if {name: [row.stored_values() for row in rows] for name, rows in before._rows.items()} != {
            name: [row.stored_values() for row in rows] for name, rows in draft._rows.items()}:
        raise AccessError(409, 'Child tables are locked')


@dataclass(frozen=True)
class Execution:
    id: str
    operation: str
    revision: int
    confirmation_id: str | None
    # Effects are collected by Context.reference_effect; callers only receive this immutable tuple.
    effects: tuple = ()


def begin(context, model, draft, operation, reason):
    from .models import ValidationError
    require_confirmable(context, model, draft.id)
    context._business.policy.record(context._tx, context.user, model, draft.id, 'update')
    context._business.policy.require(context.user, model, operation)
    current = metadata(context, model, draft.id)
    if current['state'] != OPERATIONS[operation][0]:
        raise AccessError(409, f'Cannot {operation} a {current["state"]} document')
    if operation == 'cancel' and (not isinstance(reason, str) or not reason.strip() or len(reason) > 1000):
        raise ValidationError({'reason': 'Give a cancellation reason of at most 1,000 characters'})
    context._lifecycle_execution = Execution(uuid4().hex, operation, current['revision'] + (operation == 'confirm'), current['confirmation_id'])


def run(context, model, draft, operation):
    from .documents import readonly_drafts, walk
    # The policy is also where an app enforces approval and legal correction/reopening restrictions.
    with readonly_drafts(context, list(walk(draft))):
        policy = getattr(model, 'check_lifecycle', None)
        try:
            if policy:
                policy(draft, operation)
            for kind, op, priority, name in model.__lifecycle_handlers__:
                if kind == 'validate' and op == operation:
                    getattr(model, name)(draft)
        except ValueError as exc:
            from .models import ValidationError
            if isinstance(exc, ValidationError):
                raise
            raise ValidationError({'_form': str(exc)}) from None
    for kind, op, priority, name in model.__lifecycle_handlers__:
        if kind == 'action' and op == operation:
            getattr(model, name)(draft)


def reference_effect(context, model, record_id):
    from dataclasses import replace
    execution = context._lifecycle_execution
    if context.readonly or execution is None:
        raise AccessError(403, 'Effect references require a lifecycle action')
    model = context._business.registry.resolve(model)
    context._business.policy.record(context._tx, context.user, model, record_id)
    item = (context._business.registry.name_for(model), record_id)
    context._lifecycle_execution = replace(execution, effects=(*execution.effects, item))


def event_data(context, model, record_id, data):
    if not (getattr(model, 'confirmable', False) or getattr(model, 'track_changes', False)):
        return data
    revision = metadata(context, model, record_id)['revision'] if getattr(model, 'confirmable', False) else 0
    return {**data, 'event_id': uuid4().hex, 'document_id': record_id, 'model': context._business.registry.name_for(model), 'source': context.source, 'revision': revision,
            'correlation_id': context.correlation_id}


def stored_tree(business, draft):
    """All persisted fields and owned rows, with explicit field tracking policies applied at rest."""
    model = draft._definition
    result = {'id': draft.id}
    for name, field in model.__fields__.items():
        if field.tracking == 'exclude':
            continue
        value = field.serialize(draft._values.get(name))
        result[name] = '[masked]' if field.tracking == 'mask' and value is not None else value
    result['children'] = {name: [stored_tree(business, row) for row in rows] for name, rows in draft._rows.items()}
    return result


def finish(context, model, record_id, before, draft, operation, reason):
    from . import journal
    business, tx, user = context._business, context._tx, context.user
    current, execution = metadata(context, model, record_id), context._lifecycle_execution
    name, at = business.registry.name_for(model), journal.now()
    confirmation = execution.id if operation == 'confirm' else current['confirmation_id']
    effects = [{'model': target, 'id': row_id} for target, row_id in execution.effects]
    if operation == 'confirm':
        data = {'document': stored_tree(business, draft), 'approval': approval_information(context, model, record_id), 'effects': effects}
        text = journal.canonical(data)
        integrity = hashlib.sha256(journal.canonical([name, record_id, execution.revision, execution.id, at, journal.actor_of(user), text]).encode()).hexdigest()
        tx.connection.execute('INSERT INTO evlet_document_revisions(model,record_id,revision,execution_id,at,actor,data,hash) VALUES (?,?,?,?,?,?,?,?)',
                              (name, record_id, execution.revision, execution.id, at, journal.actor_of(user), text, integrity))
    tx.connection.execute('INSERT INTO evlet_document_lifecycle(model,record_id,state,revision,confirmation_id) VALUES (?,?,?,?,?) '
                          'ON CONFLICT(model,record_id) DO UPDATE SET state=excluded.state, revision=excluded.revision, confirmation_id=excluded.confirmation_id',
                          (name, record_id, OPERATIONS[operation][1], execution.revision, confirmation))
    data = event_data(context, model, record_id, journal.tree_changes(business, model, before, draft))
    data.update(execution_id=execution.id, confirmation_id=confirmation, effects=effects, reason=reason,
                **{'from': current['state'], 'to': OPERATIONS[operation][1]})
    if operation == 'cancel':
        # Keep both the original effect references and the application's compensations.
        original = tx.connection.execute('SELECT data FROM evlet_document_revisions WHERE model=? AND record_id=? AND revision=?',
                                         (name, record_id, current['revision'])).fetchone()
        data['effects'] = [*json.loads(original[0]).get('effects', []), *effects]
    kind = 'reconfirmed' if operation == 'confirm' and current['revision'] else {'confirm': 'confirmed', 'cancel': 'cancelled', 'reopen': 'reopened'}[operation]
    journal.append(business, tx, user, model, record_id, kind, data)


def approval_information(context, model, record_id):
    row = context._tx.connection.execute("SELECT id, kind, actor, at, data FROM evlet_journal WHERE model=? AND record_id=? AND kind IN ('approved','rejected') ORDER BY id DESC LIMIT 1",
                                         (context._business.registry.name_for(model), record_id)).fetchone()
    return None if row is None else {'event_id': row['id'], 'approved': row['kind'] == 'approved', 'actor': row['actor'], 'at': row['at'],
                                     'reason': json.loads(row['data']).get('reason')}


def record_approval(context, model, record_id, approved, reason):
    """Trusted application approval policies call this inside their own permission-checked actions."""
    from .models import ValidationError
    from . import journal
    if context.readonly:
        raise AccessError(403, 'This context is read only')
    if type(approved) is not bool or reason is not None and (not isinstance(reason, str) or len(reason) > 1000):
        raise ValidationError({'approval': 'Use a boolean decision and a reason of at most 1,000 characters'})
    context._business.policy.record(context._tx, context.user, model, record_id, 'update')
    if model not in context._business.journals:
        raise AccessError(404, 'Enable track_changes to record approvals')
    return journal.append(context._business, context._tx, context.user, model, record_id, 'approved' if approved else 'rejected',
                          event_data(context, model, record_id, {'reason': reason}))


def revisions(context, model, record_id, *, limit=50, offset=0):
    from .models import ValidationError
    require_confirmable(context, model, record_id)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 2**63-1:
        raise ValidationError({'page': 'Use a limit of 1 to 100 and a nonnegative offset'})
    name = context._business.registry.name_for(model)
    rows = context._tx.connection.execute('SELECT revision, execution_id, at, actor, hash FROM evlet_document_revisions WHERE model=? AND record_id=? ORDER BY revision LIMIT ? OFFSET ?',
                                         (name, record_id, limit, offset)).fetchall()
    total = context._tx.connection.execute('SELECT count(*) FROM evlet_document_revisions WHERE model=? AND record_id=?', (name, record_id)).fetchone()[0]
    return {'items': [dict(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


def project_tree(context, model, data):
    """Historical data obeys current field, link and child-row permissions, including deleted rows."""
    from .journal import Visibility, shown_value
    business, policy = context._business, context._business.policy
    visibility = Visibility(business, context._tx, context.user)
    def project(definition, tree):
        allowed = policy.fields(context.user, definition)
        result = {'id': text_id(tree['id'])}
        for name, field in definition.__fields__.items():
            if name in allowed and name in tree and field.tracking != 'exclude':
                value = tree[name]
                if field.tracking == 'mask' and value is not None:
                    value = '[masked]'
                result[name] = value if field.tracking == 'mask' else shown_value(business, context.user, field, value, visibility)[0]
        children = {}
        for child in business.children.values():
            if child.parent is definition and policy.allows(context.user, child.model, 'read'):
                scoped = policy.rule(context.user, child.model).scope is not None
                rows = tree.get('children', {}).get(child.name, [])
                children[child.name] = [project(child.model, row) for row in rows if not scoped or visibility(child.model, RecordId(row['id']))]
        result['children'] = children
        return result
    result = project(model, data)
    return project(model, data) if visibility.resolve() else result


def snapshot(context, model, record_id, revision):
    from .models import ValidationError
    require_confirmable(context, model, record_id)
    if type(revision) is not int or not 1 <= revision <= 2**63-1:
        raise ValidationError({'revision': 'Use a positive revision number'})
    row = context._tx.connection.execute('SELECT * FROM evlet_document_revisions WHERE model=? AND record_id=? AND revision=?',
                                         (context._business.registry.name_for(model), record_id, revision)).fetchone()
    if row is None:
        raise AccessError(404, 'Revision not found')
    from .journal import canonical
    expected = hashlib.sha256(canonical([
        row['model'], row['record_id'], row['revision'], row['execution_id'], row['at'], row['actor'], row['data']]).encode()).hexdigest()
    if expected != row['hash']:
        raise AccessError(409, 'Revision integrity check failed')
    data = json.loads(row['data'])
    # Return copies; editing the result can never write the immutable database snapshot.
    shown = {'revision': revision, 'execution_id': row['execution_id'], 'at': row['at'], 'actor': row['actor'], 'hash': row['hash'],
             'document': project_tree(context, model, data['document'])}
    policy = context._business.policy
    # Effect references obey target row visibility independently of the document's visibility.
    effects = []
    for item in data.get('effects', []):
        target = context._business.registry.models.get(item['model'])
        if target is not None and item['id'] in policy.visible(context._tx, context.user, target, [item['id']]):
            effects.append(item)
    shown['effects'] = effects
    if policy.rule(context.user, model).scope is None and set(model.__fields__) <= policy.fields(context.user, model):
        shown['approval'] = data.get('approval')
    return shown


def compare(old, new):
    fields = {key: {'old': old.get(key), 'new': new.get(key)} for key in sorted(set(old) | set(new))
              if key not in ('id', 'children') and old.get(key) != new.get(key)}
    tables = {}
    for name in sorted(set(old.get('children', {})) | set(new.get('children', {}))):
        left, right = old.get('children', {}).get(name, []), new.get('children', {}).get(name, [])
        a, b = {row['id']: row for row in left}, {row['id']: row for row in right}
        changed = [{'id': row['id'], **delta} for row in right if row['id'] in a for delta in [compare(a[row['id']], row)] if delta]
        table = {'added': [row for row in right if row['id'] not in a], 'removed': [row for row in left if row['id'] not in b], 'updated': changed}
        positions = [{'id': row['id'], 'old': i, 'new': next(j for j, item in enumerate(right) if item['id'] == row['id'])}
                     for i, row in enumerate(left) if row['id'] in b and i != next(j for j, item in enumerate(right) if item['id'] == row['id'])]
        if positions:
            table['positions'] = positions
        table = {key: value for key, value in table.items() if value}
        if table:
            tables[name] = table
    return {**({'fields': fields} if fields else {}), **({'rows': tables} if tables else {})}


def diff(context, model, record_id, first, second):
    return {'from': first, 'to': second, 'changes': compare(snapshot(context, model, record_id, first)['document'],
                                                         snapshot(context, model, record_id, second)['document'])}


def route(business, scope, body, user, model, parts):
    from urllib.parse import parse_qs
    from .models import ValidationError
    if len(parts) != 5 or path_id(parts[3]) is None:
        raise AccessError(404, 'Record not found')
    record_id, operation, method = path_id(parts[3]), parts[4], scope['method']
    if operation in OPERATIONS:
        if method != 'POST':
            raise AccessError(405, 'Method not allowed')
        if set(body) - ({'reason'} if operation == 'cancel' else set()):
            raise ValidationError({'body': 'Unexpected lifecycle arguments'})
        with business.context(user, source='API' if user.access is not None else 'User') as context:
            require_confirmable(context, model, record_id)
            record = business.policy.record(context._tx, user, model, record_id, operation)
            if b'if-match' not in dict(scope.get('headers', [])):
                raise AccessError(428, 'A current If-Match revision is required')
            business.check_revision(scope, context._tx, user, record)
            result = context._write(model, record_id, {}, lifecycle=operation, reason=body.get('reason'))
            extra = business.availability(context, model, context._tx.get(model, record_id))
            etag = business.revision(result)
            return 200, {**business.embedded(result), **extra}, [(b'etag', etag)]
    if method != 'GET':
        raise AccessError(405, 'Method not allowed')
    try:
        query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True)
        if any(len(values) != 1 for values in query.values()):
            raise ValueError()
        allowed = {'limit', 'offset'} if operation in ('history', 'revisions') else {'revision'} if operation == 'snapshot' else {'from', 'to'}
        if set(query) - allowed:
            raise ValueError()
        values = {key: int(value[0]) for key, value in query.items()}
        if operation in ('snapshot', 'diff') and set(values) != allowed:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValidationError({'query': 'Invalid history or revision query'}) from None
    with business.context(user, readonly=True) as context:
        if operation == 'history':
            return 200, context.history(model, record_id, **values), []
        if operation == 'revisions':
            return 200, context.revisions(model, record_id, **values), []
        if operation == 'snapshot':
            return 200, context.snapshot(model, record_id, revision=values['revision']), []
        return 200, context.diff(model, record_id, values['from'], values['to']), []


class BoundDocument:
    """An editable handle bound to a live Context; persistence always uses the checked pipeline."""
    def __init__(self, context, model, record_id, values):
        object.__setattr__(self, '_context', context)
        object.__setattr__(self, '_model', model)
        object.__setattr__(self, '_id', record_id)
        object.__setattr__(self, '_pending', dict(values))
        if record_id is not None:
            context._business.policy.record(context._tx, context.user, model, record_id)

    @property
    def id(self):
        return self._id

    @property
    def lifecycle_state(self):
        return metadata(self._context, self._model, self.id)['state']

    @property
    def revision(self):
        return metadata(self._context, self._model, self.id)['revision']

    def __getattr__(self, name):
        if name in self._pending:
            return self._pending[name]
        value = self._context.get(self._model, self.id)
        return getattr(value, name)

    def __setattr__(self, name, value):
        if name not in self._model.__declared_fields__:
            raise AttributeError('Assign a declared field; pass child change sets to save(children=...)')
        self._pending[name] = value

    def save(self, **values):
        result = self._context._write(self._model, self.id, {**self._pending, **values})
        object.__setattr__(self, '_id', result['id'])
        self._pending.clear()
        return self

    def _transition(self, operation, **arguments):
        from .models import ValidationError
        if self._pending:
            raise ValidationError({'_form': 'Save pending changes before changing lifecycle'})
        getattr(self._context, operation)(self._model, self.id, **arguments)
        return self

    def confirm(self): return self._transition('confirm')
    def cancel(self, *, reason): return self._transition('cancel', reason=reason)
    def reopen(self): return self._transition('reopen')
    def delete(self): return self._context.delete(self._model, self.id)
    def history(self, **page): return self._context.history(self._model, self.id, **page)
    def revisions(self, **page): return self._context.revisions(self._model, self.id, **page)
    def snapshot(self, *, revision): return self._context.snapshot(self._model, self.id, revision=revision)
    def diff(self, first, second): return self._context.diff(self._model, self.id, first, second)
