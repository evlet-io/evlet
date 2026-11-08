"""Workflow enforcement: the protected-operation gate, stage edit rules, approval invalidation, the Python
handle (`context.workflow`) and the HTTP routes that decide and withdraw.

Every path meets the same checks because they sit in the document pipeline (evlet/documents.py `operate`),
not in a route: HTTP, `context.confirm`, `context.move`, `context.action`, custom actions that call
`context`, jobs, the connector and Eva. Raw `Session` writes to a workflow model are refused outside
`tx.maintenance()`, which stays the audited administrative boundary.

* The gate lets a protected operation (confirm, cancel, reopen, a Move target or a model action the app
  lists in `WorkflowOptions.protects`) run only with unused approval evidence that still matches the saved
  data, and uses that evidence once. See docs/workflows.md.
* Stage edit rules narrow writes while a run is open; they never widen what the app's permissions allow.
* Any committed change to non-harmless data raises the record's data generation, ends the approval attempt
  in progress and voids evidence not yet used, in the writer's transaction.
"""

import json
import re
from urllib.parse import parse_qs

from .auth import AccessError
from .database import ConflictError, NotFoundError
from .identity import RecordId
from .models import ValidationError
from .workflow import LIFECYCLE, protect_object
from .workflow_engine import CHANGED, OPEN, Blocked, Run, WorkflowError, canonical

# The body of a workflow request is small: an idempotency key, the token, a review id and feedback.
BODY_LIMIT = 16 * 1024
ACTION_ID = re.compile('act_[a-z0-9]{6,26}')
REVIEW_ID = re.compile('rev_[a-z0-9]{6,26}')
CODES = {403: 'FORBIDDEN', 404: 'NOT_FOUND', 405: 'METHOD_NOT_ALLOWED', 409: 'CONFLICT', 413: 'TOO_LARGE', 428: 'PRECONDITION_REQUIRED'}
TABLE_VERBS = {'add': 'added', 'remove': 'removed', 'reorder': 'reordered', 'edit': 'changed'}


def engine_for(business, model):
    return business.workflows if business.workflows is not None and model in business.registry.workflows else None


def noun(model):
    label = getattr(getattr(model, 'Meta', None), 'label', None)
    if label:
        return label.lower()
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', model.__name__).lower()


def operation_label(model, key):
    if key in LIFECYCLE:
        from .lifecycle import labels
        return labels(model).get(key, key.capitalize())
    kind, _, name = key.partition(':')
    if kind == 'move':
        return f'Moving to {name.replace("_", " ")}'
    declaration = getattr(getattr(model, name, None), '__action__', None) or {}
    return declaration.get('label') or name.replace('_', ' ').capitalize()


# ---------- the gate ----------

def gate(context, model, record_id, key):
    """Let a protected operation run only with valid approval evidence, and use the evidence. Returns what
    the confirmation snapshot records, or None when the operation is not protected or nothing applies and
    the model allows that. Raises WorkflowError WORKFLOW_REQUIRED, WORKFLOW_NONE or WORKFLOW_BLOCKED."""
    business = context._business
    engine = engine_for(business, model)
    if engine is None or key not in business.registry.workflows[model].protects:
        return None
    tx = context._tx
    found = engine.evidence(tx, model, record_id, key)
    if found is not None:
        evidence = engine.consume(tx, *found)
        context._workflow_evidence = (model, RecordId(record_id), key, evidence)
        return evidence
    label, what = operation_label(model, key), noun(model)
    run = engine.latest(tx, model, record_id, key)
    if run is not None and run['status'] in OPEN and run['status'] != 'draft':
        raise WorkflowError('WORKFLOW_REQUIRED', f'{label} needs approval. This {what} is awaiting review.')
    requester = run['requester'] if run is not None else context.user.id
    if run is not None and run['status'] in OPEN or engine.applies(tx, model, record_id, key, requester):
        raise WorkflowError('WORKFLOW_REQUIRED', f'{label} needs approval. Send this {what} for review first.')
    if business.registry.workflows[model].unmatched == 'block':
        raise WorkflowError('WORKFLOW_NONE', f'{label} is blocked: no approval workflow applies to this {what}.')
    return None


def evidence_for(context, model, record_id, key='confirm'):
    """The evidence the gate used in this context for this record and operation, for the confirmation snapshot."""
    found = getattr(context, '_workflow_evidence', None)
    if found is None or found[0] is not model or found[1] != RecordId(record_id) or found[2] != key:
        return None
    return found[3]


# ---------- stage edit rules ----------

def writable(model):
    internal = set(getattr(model, '__internal_fields__', ()))
    return {name for name, field in model.__declared_fields__.items()
            if field.stored and not field.read_only and field.compute is None and name not in internal}


def diff(business, model, before, after, *, people_only):
    """(changed root fields, {table path: {"add", "remove", "reorder", "edit"}}) between two drafts.
    With people_only, only fields a person writes count (not computed or read-only ones, not the State)."""
    names = writable(model) - {model.__state__} if people_only else set(model.__fields__)
    fields = {name for name in names if before._values.get(name) != after._values.get(name)}
    tables = {}
    rows(business, model, before, after, '', tables, people_only)
    return fields, tables


def rows(business, model, before, after, prefix, tables, people_only):
    for child in business.children.values():
        if child.parent is not model:
            continue
        path = prefix + child.name
        old = before._rows.get(child.name, []) if before is not None else []
        new = after._rows.get(child.name, [])
        old_by_id = {row.id: row for row in old}
        kept = {row.id for row in new if row.id in old_by_id}
        names = (writable(child.model) if people_only else set(child.model.__fields__)) - {'evlet_position', child.link} \
            - set(getattr(child.model, '__internal_fields__', ()))
        ops = set()
        for row in new:
            if row.id not in old_by_id:
                ops.add('add')
                # The rows of an added row are added too.
                rows(business, child.model, None, row, path + '.', tables, people_only)
                continue
            previous = old_by_id[row.id]
            if any(previous._values.get(name) != row._values.get(name) for name in names):
                ops.add('edit')
            rows(business, child.model, previous, row, path + '.', tables, people_only)
        if set(old_by_id) - kept:
            ops.add('remove')
        if [row.id for row in old if row.id in kept] != [row.id for row in new if row.id in kept]:
            ops.add('reorder')
        if ops:
            tables.setdefault(path, set()).update(ops)


def may_edit(state, rule, user):
    """Whether this person is among who can edit at the run's current stage."""
    who = rule['who']
    if who == 'existing':
        return True
    if who == 'requester':
        return user.id == state.run['requester']
    if who == 'assigned':
        execution = state.execution()
        return execution is not None and state.connection.execute(
            "SELECT 1 FROM evlet_workflow_assignees a JOIN evlet_workflow_tasks t ON t.id=a.task_id "
            "WHERE t.execution_id=? AND t.status='pending' AND a.active=1 AND a.user_id=?", (execution['id'], user.id)).fetchone() is not None
    if who == 'selected':
        label = state.stages[state.run['stage_id']]['label']
        try:
            return any(user.id == user_id for item in rule.get('people') or () for user_id, _ in state.resolve(item, label))
        except Blocked:
            return False
    return False


def locked(message):
    return WorkflowError('WORKFLOW_LOCKED', message)


def check_rule(business, user, model, state, fields, tables, attachments=None):
    """Raise WORKFLOW_LOCKED unless the run's current stage lets this person make these changes."""
    stage = state.stages[state.run['stage_id']]
    rule, label = stage['edit'] or {'who': 'nobody'}, stage['label']
    if rule['who'] == 'nobody':
        raise locked(f'Locked during {label}. Nobody edits at this stage.')
    if not may_edit(state, rule, user):
        raise locked(f'Locked during {label}. You cannot edit at this stage.')
    allowed = rule.get('fields')
    # A rule without fields lets nobody change a field (restrictive by default); "all" leaves the app's own limits.
    names = set() if allowed is None else None if allowed['mode'] == 'all' else set(allowed['names'])
    refused = set() if names is None else fields - names
    if refused:
        readable = business.policy.fields(user, model)
        if refused <= readable:
            from .workflow import label_of
            shown = ', '.join(sorted(label_of(name, model.__declared_fields__.get(name)) for name in refused))
            raise locked(f'{shown} cannot change during {label}.')
        raise locked(f'Some fields cannot change during {label}.')
    for path, ops in sorted(tables.items()):
        rights = (rule.get('tables') or {}).get(path) or {}
        denied = sorted(op for op in ops if not rights.get(op))
        if denied:
            table = path.replace('_', ' ').replace('.', ' / ').capitalize()
            raise locked(f'{table}: rows cannot be {" or ".join(TABLE_VERBS[op] for op in denied)} during {label}.')
    if attachments is not None and not (rule.get('attachments') or {}).get(attachments):
        raise locked(f'Attachments cannot be {"added" if attachments == "add" else "removed"} during {label}.')


def before_write(context, model, before, draft, lifecycle=None):
    """Check a document update against the stage edit rules of every open run of the record, before it is
    written. Returns whether the write changes non-harmless data (the caller then calls after_write)."""
    business = context._business
    engine = engine_for(business, model)
    if engine is None or before is None:
        return False
    tx, user = context._tx, context.user
    if lifecycle is None:
        runs = engine.open_runs(tx, model, before.id)
        if runs:
            fields, tables = diff(business, model, before, draft, people_only=True)
            if fields or tables:
                for run in runs:
                    check_rule(business, user, model, Run(engine, tx, model, run), fields, tables)
    fields, tables = diff(business, model, before, draft, people_only=False)
    return bool(fields - set(business.registry.workflows[model].harmless) or tables)


def after_write(context, model, record_id):
    """After a write that changed non-harmless data: raise the generation and invalidate approval."""
    engine = engine_for(context._business, model)
    if engine is not None:
        engine.record_changed(context._tx, context.user, model, record_id, f'The {noun(model)} changed after it was sent for review.')


def check_attachment(business, tx, user, model, record_id, operation):
    """Adding or removing an attachment (operation "add" or "remove") obeys the stage rules; a rich text
    image upload ("image") needs only to be among who can edit."""
    engine = engine_for(business, model)
    if engine is None:
        return
    for run in engine.open_runs(tx, model, record_id):
        check_rule(business, user, model, Run(engine, tx, model, run), set(), {}, None if operation == 'image' else operation)


def attachment_changed(business, tx, user, model, record_id):
    engine = engine_for(business, model)
    if engine is not None:
        engine.record_changed(tx, user, model, record_id, f'The attachments of the {noun(model)} changed after it was sent for review.')


def check_delete(context, model, record_id):
    """Deleting a record in a workflow: refused while a request awaits review; at a work stage the person must
    be among who can edit, and the open run ends with the record."""
    business = context._business
    engine = engine_for(business, model)
    if engine is None:
        return
    tx, user = context._tx, context.user
    for run in engine.open_runs(tx, model, record_id):
        state = Run(engine, tx, model, run)
        stage = state.stages[run['stage_id']]
        if run['status'] != 'draft':
            raise locked(f'This {noun(model)} is in {stage["label"]}. Withdraw the request before deleting it.')
        rule = stage['edit'] or {'who': 'nobody'}
        if rule['who'] == 'nobody' or not may_edit(state, rule, user):
            raise locked(f'Locked during {stage["label"]}. You cannot delete it at this stage.')
    engine.record_deleted(tx, model, record_id)


# ---------- status, receipts and the shared mutation ----------

def resolve_protects(business, tx, model, record_id, value):
    """The operation a request is about: the one given, the record's one open run, or the model's only one."""
    engine = business.workflows
    options = business.registry.workflows[model].protects or ('',)
    if value is not None:
        if not isinstance(value, str) or value not in (*options, ''):
            raise ValidationError({'protects': 'This model has no such protected operation'})
        return value
    runs = engine.open_runs(tx, model, record_id)
    if len(runs) == 1:
        return runs[0]['protects']
    if not runs:
        if len(options) == 1:
            return options[0]
        name = business.registry.name_for(model)
        latest = tx.connection.execute('SELECT protects FROM evlet_workflow_runs WHERE model=? AND record_id=? ORDER BY id DESC LIMIT 1',
                                       (name, RecordId(record_id))).fetchone()
        if latest is not None:
            return latest[0]
        published = [row[0] for row in tx.connection.execute(
            "SELECT DISTINCT protects FROM evlet_workflows WHERE model=? AND status IN ('published','paused')", (name,)).fetchall()
            if row[0] in options]
        if len(published) == 1:
            return published[0]
    raise ValidationError({'protects': 'Choose the operation this request is for'})


def public_status(business, tx, user, model, record_id, protects):
    """The workflow of a record as this person may see it (GET /api/{model}/{id}/workflow): stage, tasks,
    decisions with feedback, the actions this person may take now and the token. No routing facts."""
    engine = business.workflows
    record_id = RecordId(record_id)
    result = {'protects': protect_object(protects), 'token': engine.token(tx, model, record_id, protects), 'run': None, 'status': None,
              'stage': None, 'attempt': None, 'requester': None, 'blocked': None, 'evidence': None, 'tasks': [], 'decisions': [],
              'actions': [], 'withdraw': False}
    run = engine.latest(tx, model, record_id, protects)
    if run is None or run['status'] not in OPEN:
        if run is not None:
            add_run(engine, tx, model, run, result)
        # Nothing open: the Send of the workflow that would apply, when this person may send the record.
        if not business.policy.visible(tx, user, model, [record_id], 'update'):
            return result
        try:
            selected = engine.select(tx, model, record_id, protects, user.id)
        except WorkflowError as exc:
            result['blocked'] = {'code': exc.code, 'message': exc.message}
            return result
        if selected is not None:
            definition = selected[2]
            start = next(stage for stage in definition['stages'] if stage['id'] == definition['start'])
            result['actions'] = [action_entry(action, None, None) for action in start['actions'] if action['kind'] == 'submit']
        return result
    state = Run(engine, tx, model, run)
    add_run(engine, tx, model, run, result)
    if run['status'] in ('draft', 'active'):
        result['actions'] = available(business, tx, user, model, state)
    attempt = state.attempt()
    result['withdraw'] = (run['status'] in ('active', 'blocked') and attempt['status'] == 'active'
                          and state.definition['settings']['withdraw']['allowed'] and user.id == run['requester']
                          and bool(business.policy.visible(tx, user, model, [record_id], 'update')))
    return result


def add_run(engine, tx, model, run, result):
    state = Run(engine, tx, model, run)
    stage = state.stages[run['stage_id']]
    attempt = state.attempt()
    blocked = json.loads(run['blocked']) if run['blocked'] else None
    labels = {}
    for item in state.definition['stages']:
        labels.update({review['id']: review['label'] for review in (item['reviews'] or {}).get('items', ())})
    actions = {action['id']: action['label'] for item in state.definition['stages'] for action in item['actions']}
    tasks = []
    for task in tx.connection.execute('SELECT t.*, (SELECT count(*) FROM evlet_workflow_assignees a WHERE a.task_id=t.id AND a.active=1) AS people '
                                      'FROM evlet_workflow_tasks t JOIN evlet_workflow_executions e ON e.id=t.execution_id '
                                      'WHERE e.attempt_id=? ORDER BY t.id', (run['attempt_id'],)).fetchall():
        tasks.append({'id': task['id'], 'review': task['review_id'], 'label': labels.get(task['review_id'], stage['label']),
                      'status': task['status'], 'closed_reason': task['closed_reason'], 'people': task['people']})
    decisions = []
    for row in tx.connection.execute('SELECT d.*, t.review_id FROM evlet_workflow_decisions d LEFT JOIN evlet_workflow_tasks t ON t.id=d.task_id '
                                     'WHERE d.run_id=? ORDER BY d.id', (run['id'],)).fetchall():
        decisions.append({'id': row['id'], 'attempt': row['attempt_id'], 'superseded': row['attempt_id'] != run['attempt_id'],
                          'kind': row['kind'], 'action': row['action_id'], 'label': actions.get(row['action_id'], row['kind'].capitalize()),
                          'review': row['review_id'], 'actor': row['actor'], 'feedback': row['feedback'], 'at': row['at']})
    evidence = None
    if run['status'] == 'approved':
        evidence = 'used' if attempt['evidence_used_at'] else 'valid'
    result.update(run=run['id'], status=run['status'], stage={'id': stage['id'], 'label': stage['label'], 'kind': stage['kind']},
                  attempt=attempt['n'], requester=run['requester'], evidence=evidence, tasks=tasks, decisions=decisions,
                  blocked=None if blocked is None else {'code': 'WORKFLOW_BLOCKED', 'message': blocked['message']})


def action_entry(action, review, review_label):
    feedback = action['feedback'] or {'mode': 'optional', 'max_length': 2000}
    return {'id': action['id'], 'label': action['label'], 'kind': action['kind'], 'review': review, 'review_label': review_label,
            'feedback': {'mode': feedback['mode'], 'max_length': feedback['max_length']}}


def available(business, tx, user, model, state):
    """The actions this person may take now, one per review they may decide; the same checks as a decision."""
    execution = state.execution()
    if execution is None:
        return []
    stage = state.stages[state.run['stage_id']]
    routing = state.routing(execution)
    labels = {item['id']: item['label'] for item in (stage['reviews'] or {}).get('items', ())}
    reviews = [row['review_id'] for row in state.tasks(execution['id'], 'pending')]
    may_update = bool(business.policy.visible(tx, user, model, [state.run['record_id']], 'update'))
    result = []
    for action in stage['actions']:
        if not routing['when'].get(action['id']) or action['kind'] == 'submit' and not may_update:
            continue
        for review in (reviews if stage['reviews'] is not None else [None]):
            try:
                state.task_for(user, execution, stage, action, review)
            except (AccessError, WorkflowError, ValidationError):
                continue
            result.append(action_entry(action, review, labels.get(review)))
    return result


def receipt(tx, user, model_name, record_id, key, signature):
    """The stored result of an earlier request with this idempotency key, or None. The same key with other
    input is refused (IDEMPOTENCY_MISMATCH)."""
    row = tx.connection.execute('SELECT actor, request, result FROM evlet_operations WHERE key=?',
                                (f'{user.id}:{model_name}:{record_id}:workflow:{key}',)).fetchone()
    if row is None:
        return None
    if row['actor'] != user.id or row['request'] != signature:
        raise WorkflowError('IDEMPOTENCY_MISMATCH', 'Idempotency key has different input')
    return json.loads(row['result'])


def store_receipt(tx, user, model_name, record_id, key, signature, result):
    tx.connection.execute('INSERT INTO evlet_operations (key, actor, request, result) VALUES (?, ?, ?, ?)',
                          (f'{user.id}:{model_name}:{record_id}:workflow:{key}', user.id, signature, canonical(result)))


def check_key(key, *, required):
    if key is None and not required:
        return None
    if not isinstance(key, str) or not 8 <= len(key) <= 128:
        raise ValidationError({'key': 'Supply an idempotency key of 8 to 128 characters'})
    return key


def mutate(business, tx, user, model, record_id, protects, operation, arguments, meta):
    engine = business.workflows
    if operation == 'decide':
        engine.decide(tx, user, model, record_id, arguments['action'], kind=arguments.get('kind'), review=arguments.get('review'),
                      protects=protects, feedback=arguments.get('feedback'), meta=meta)
    else:
        engine.withdraw(tx, user, model, record_id, protects=protects, feedback=arguments.get('feedback'), meta=meta)
    return public_status(business, tx, user, model, record_id, protects)


def client_of(tx, user, default):
    from .journal import via
    through = via(tx, user)
    return through or default


class WorkflowHandle:
    """context.workflow(Model, id): status, decisions and withdrawal with the same checks as the HTTP routes,
    without the HTTP preconditions (ETag and token). The idempotency key is optional in Python."""

    def __init__(self, context, model, record_id, protects=None):
        business = context._business
        if engine_for(business, model) is None:
            raise AccessError(404, 'This model has no workflow')
        business.policy.record(context._tx, context.user, model, record_id)
        self._context, self.model, self.record_id = context, model, RecordId(record_id)
        self.protects = resolve_protects(business, context._tx, model, self.record_id, protects)

    def status(self):
        context = self._context
        return public_status(context._business, context._tx, context.user, self.model, self.record_id, self.protects)

    def decide(self, action_id=None, *, kind=None, review=None, feedback=None, key=None):
        """Take an action by its id, or by `kind` when exactly one action of that kind is open to you.
        Sending for review is decide(kind="submit")."""
        if action_id is None and kind is None:
            raise ValidationError({'action': 'Name the action or its kind'})
        return self._mutate('decide', {'action': action_id, 'kind': kind, 'review': review, 'feedback': feedback}, key)

    def withdraw(self, *, feedback=None, key=None):
        return self._mutate('withdraw', {'feedback': feedback}, key)

    def _mutate(self, operation, arguments, key):
        context = self._context
        if context.readonly:
            raise AccessError(403, 'This context is read only')
        key = check_key(key, required=False)
        business, tx, user = context._business, context._tx, context.user
        name = business.registry.name_for(self.model)
        signature = canonical({'operation': operation, 'protects': self.protects, **arguments})
        with context._savepoint():
            if key is not None:
                stored = receipt(tx, user, name, self.record_id, key, signature)
                if stored is not None:
                    return stored
            meta = {'source': context.source, 'client': client_of(tx, user, 'Python'), 'correlation_id': context.correlation_id}
            result = mutate(business, tx, user, self.model, self.record_id, self.protects, operation, arguments, meta)
            if key is not None:
                store_receipt(tx, user, name, self.record_id, key, signature, result)
            return result


# ---------- HTTP ----------

def route(business, scope, body, user, model, parts):
    """GET /api/{model}/{id}/workflow, POST .../workflow/actions/{action_id} and POST .../workflow/withdraw.
    Every error carries a stable `code` (docs/workflows.md)."""
    try:
        return dispatch(business, scope, body, user, model, parts)
    except WorkflowError:
        raise
    except ConflictError as exc:
        if 'evlet_workflow_decisions' in str(exc.__cause__):
            # A unique decision index refused a second vote that raced this one.
            raise WorkflowError('WORKFLOW_CHANGED', CHANGED) from exc
        if getattr(exc, 'code', None) is None:
            exc.code = 'CONFLICT'
        raise
    except (AccessError, ValidationError, NotFoundError) as exc:
        if getattr(exc, 'code', None) is None:
            exc.code = 'VALIDATION_FAILED' if isinstance(exc, ValidationError) else 'NOT_FOUND' if isinstance(exc, NotFoundError) \
                else CODES.get(exc.status, 'ERROR')
        raise


def dispatch(business, scope, body, user, model, parts):
    from .identity import path_id
    if engine_for(business, model) is None:
        raise AccessError(404, 'This record has no workflow')
    record_id = path_id(parts[3])
    if record_id is None:
        raise AccessError(404, 'Record not found')
    tail, method = parts[5:], scope['method']
    engine = business.workflows
    if not tail:
        if method != 'GET':
            raise AccessError(405, 'Method not allowed')
        try:
            query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True)
        except UnicodeError:
            raise ValidationError({'query': 'Invalid workflow query'}) from None
        if set(query) - {'protects'} or any(len(values) != 1 for values in query.values()):
            raise ValidationError({'query': 'Invalid workflow query'})
        with business.context(user, readonly=True) as context:
            business.policy.record(context._tx, user, model, record_id)
            protects = resolve_protects(business, context._tx, model, record_id, query['protects'][0] if 'protects' in query else None)
            return 200, public_status(business, context._tx, user, model, record_id, protects), []
    if method != 'POST':
        raise AccessError(405, 'Method not allowed')
    if tail == ['withdraw']:
        operation, allowed = 'withdraw', {'key', 'token', 'feedback', 'protects'}
    elif len(tail) == 2 and tail[0] == 'actions' and ACTION_ID.fullmatch(tail[1]):
        operation, allowed = 'decide', {'key', 'token', 'review', 'feedback', 'protects'}
    else:
        raise AccessError(404, 'Route not found')
    if len(json.dumps(body).encode()) > BODY_LIMIT:
        raise AccessError(413, 'A workflow request is at most 16 KB')
    for name, value in body.items():
        try:
            if isinstance(value, str):
                value.encode('utf-8')
        except UnicodeEncodeError:
            raise ValidationError({name: 'Feedback must be valid text' if name == 'feedback' else 'Use valid text'}) from None
    unknown = sorted(set(body) - allowed)
    if unknown:
        # The stage is the engine's; a body cannot name a stage, an outcome or an assignee.
        raise ValidationError({name: 'Unexpected input' for name in unknown})
    key = check_key(body.get('key'), required=True)
    review = body.get('review')
    if review is not None and (not isinstance(review, str) or not REVIEW_ID.fullmatch(review)):
        raise ValidationError({'review': 'Choose a review'})
    signature = canonical({'path': scope['path'], 'body': body})
    name = business.registry.name_for(model)
    with business.context(user) as context:
        tx = context._tx
        record = business.policy.record(tx, user, model, record_id)
        protects = resolve_protects(business, tx, model, record_id, body.get('protects'))
        stored = receipt(tx, user, name, record_id, key, signature)
        if stored is not None:
            # A retry of a request that succeeded (its response was lost): the same result, whatever changed since.
            return 200, stored, [(b'etag', business.record_revision(tx, user, record))]
        expected = dict(scope.get('headers', [])).get(b'if-match')
        if expected is None:
            raise AccessError(428, 'A record revision is required')
        if expected != business.record_revision(tx, user, record):
            raise WorkflowError('RECORD_CHANGED', 'This record changed since you opened it. Review the latest version.')
        token = body.get('token')
        if not isinstance(token, str) or not token:
            raise AccessError(428, 'A workflow token is required')
        if token != engine.token(tx, model, record_id, protects):
            raise WorkflowError('WORKFLOW_CHANGED', CHANGED)
        meta = {'source': context.source, 'client': client_of(tx, user, 'HTTP'), 'correlation_id': context.correlation_id}
        arguments = {'action': tail[1], 'review': review, 'feedback': body.get('feedback')} if operation == 'decide' \
            else {'feedback': body.get('feedback')}
        result = mutate(business, tx, user, model, record_id, protects, operation, arguments, meta)
        store_receipt(tx, user, name, record_id, key, signature, result)
        etag = business.record_revision(tx, user, tx.get(model, record_id))
    return 200, result, [(b'etag', etag)]

