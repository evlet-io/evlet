"""Prompts: the one transient form. An action is the button, a Prompt the form it opens, submit() the callback.

A prompt is shown only when an @action, a prompt's submit() or an @api function returns it. Over HTTP that
stores a single-use ticket; /api/_prompts/{ticket}/check and /submit work only through it (docs/prompts.md).
"""

import hashlib
import json
import re
import secrets
import time

from . import qb
from .adapter import Column, TableDefinition
from .auth import AccessError, allow, has_role
from .documents import Draft, Record, handler_context, settle, source_values
from .identity import RecordId, jsonable
from .models import Model, ValidationError, checked_body, storage_name
from .permissions import trusted

RESULT_LIMIT = 65536
EXPIRES = 3600
META = ('title', 'primary', 'layout', 'focus', 'script', 'on_open')

# Every Prompt class by its id (module.qualname); a ticket names the prompt it opened.
PROMPTS = {}

TICKETS = TableDefinition('evlet_prompt_tickets', (
    Column('ticket', 'TEXT', primary=True), Column('actor', 'INTEGER', required=True),
    Column('prompt', 'TEXT', required=True), Column('model', 'TEXT'), Column('record_id', 'TEXT'),
    Column('revision', 'TEXT'), Column('origin', 'TEXT', required=True), Column('starting', 'TEXT', required=True),
    Column('expires', 'INTEGER', required=True), Column('result', 'TEXT'),
))


def ensure_tickets(connection):
    """Framework step 0017_prompt_tickets; also run at startup (a no-op once present)."""
    connection.ensure_table(TICKETS)
    connection.ensure_index('evlet_prompt_ticket_expiry', 'evlet_prompt_tickets', ('expires',))
    if connection.table_exists('evlet_operations'):
        connection.drop_table('evlet_operations')


class Prompt(Model):
    """A form without storage: fields, Child Tables, finite formulas, field rules and @validate, laid out with
    Meta.layout. load(self, source) fills it; submit(self) is the callback, run in one transaction.

    Return the class (or an instance with starting values, ConfirmQuotation(customer_po='...')) from an @action,
    a prompt's submit() or an @api function to show it."""
    __transient__ = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        meta = vars(cls).get('Meta')
        unknown = sorted(key for key in vars(meta) if not key.startswith('_') and key not in META) if meta else ()
        if unknown:
            raise ValueError(f'{cls.__name__}.Meta supports {", ".join(META)}, not {", ".join(unknown)}')
        PROMPTS[f'{cls.__module__}.{cls.__qualname__}'] = cls
        # Its name, and its rows', in the schema and the finite plan the browser runs.
        from .declarations import Table
        cls.__form_name__ = '_prompt.' + prompt_name(cls)
        for key, value in vars(cls).items():
            if isinstance(value, Table):
                value.child.__form_name__ = f'{cls.__form_name__}.{key}'
        # A model script, as on a document (docs/model-scripts.md): its hooks on the prompt's fields and rows.
        from .fields import Field, script_method
        rows = [value.child for value in vars(cls).values() if isinstance(value, Table)]
        hooked = [f'{owner.__name__}.{name}' for owner, fields in ((cls, vars(cls)), *((row, getattr(row, '__app_fields__', {})) for row in rows))
                  for name, field in fields.items() if isinstance(field, Field) and field.hooks]
        script, on_open = option(cls, 'script'), option(cls, 'on_open')
        if on_open is not None:
            hooked.append(f'{cls.__name__}.Meta.on_open')
            script_method(on_open, f'{cls.__name__}.Meta.on_open')
        if hooked and script is None:
            raise ValueError(f'{hooked[0]} names a script method, so {cls.__name__} needs Meta.script, the .js file that holds it')
        from .ui.scripts import prompt_script
        cls.__script__ = None if script is None else prompt_script(cls, script, on_open)

    def __init__(self, **values):
        """Starting values, applied after load(); trusted like load()."""
        unknown = set(values) - set(type(self).__declared_fields__) - set(type(self).__tables__)
        if unknown:
            raise ValueError(f'{type(self).__name__} has no field ' + ', '.join(sorted(unknown)))
        object.__setattr__(self, 'starting', values)

    def load(self, source):
        pass

    def submit(self):
        raise NotImplementedError('Define submit(self) on the prompt')


def opens_prompt(method):
    """Whether an @action only opens a prompt and so writes nothing: its body (after a docstring) is `return P` or
    `return P(...)` with constant or plain-name arguments, where P is a Prompt class, as in
    `def confirm(self): return ConfirmQuotation`. Read from the source once; anything else is False."""
    import ast
    import inspect
    import textwrap
    cached = getattr(method, '__opens_prompt__', None)
    if cached is not None:
        return cached
    source = inspect.unwrap(method)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(source)))
    except (OSError, TypeError, SyntaxError):
        tree = None
    body = tree.body[0].body if tree and isinstance(tree.body[0], ast.FunctionDef) else []
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    returned = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
    plain = lambda node: isinstance(node, (ast.Constant, ast.Name)) or isinstance(node, ast.Attribute) and plain(node.value)
    if isinstance(returned, ast.Call) and all(plain(item) for item in [*returned.args, *(keyword.value for keyword in returned.keywords)]):
        returned = returned.func
    found = isinstance(returned, ast.Name) and is_prompt(getattr(source, '__globals__', {}).get(returned.id))
    try:
        method.__opens_prompt__ = found
    except AttributeError:
        pass
    return found


def is_prompt(value):
    return isinstance(value, Prompt) or isinstance(value, type) and issubclass(value, Prompt) and value is not Prompt


def inputs_prompt(model, name):
    """The prompt an action's inputs={...} make: the inputs as its fields, its label as title and button; its
    submit() runs the action with them, with every check."""
    import copy
    cached = model.__dict__.get('__inputs_prompts__', {})
    if name in cached:
        return cached[name]
    declaration = getattr(model, name).__action__
    fields = {}
    for key, field in declaration['inputs'].items():
        fields[key] = copy.copy(field)
        fields[key].name = ''

    def submit(self):
        result = self.context.action(model, self.source.id, name, **{key: getattr(self, key) for key in fields})
        return {'data': {'job': result['_job']}} if '_job' in result else result.get('_outcome')
    meta = type('Meta', (), {'title': declaration['label'], 'primary': declaration['label']})
    prompt = type(f'{model.__name__}{name.title().replace("_", "")}', (Prompt,),
                  {'__module__': model.__module__, '__qualname__': f'{model.__qualname__}.{name}.inputs', 'Meta': meta,
                   'submit': submit, **fields})
    model.__inputs_prompts__ = {**cached, name: prompt}
    return prompt


def prompt_id(prompt):
    return f'{prompt.__module__}.{prompt.__qualname__}'


def prompt_name(prompt):
    """The prompt's name in the schema: ConfirmQuotation -> confirm_quotation."""
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', prompt.__name__).lower()


def option(prompt, name, default=None):
    return getattr(getattr(prompt, 'Meta', None), name, default)


class Outcome(dict):
    """What an action or a prompt's submit() returns: a message, a record to open, a new record to review
    prefilled (new=(Model, values)) or data for browser code. Return another Prompt to chain one.

    A model may be named by class or by name ("deliveryorder" or its resource name), in `open=(model, id)` and
    `new=(model, values)`. A name is resolved, and the values serialized, where the outcome goes into the answer;
    an unknown name fails there with "Unknown model"."""
    def __init__(self, message=None, *, open=None, new=None, data=None):
        super().__init__()
        self._pending = {}
        if message is not None:
            if not isinstance(message, str) or len(message) > 500:
                raise ValueError('An outcome message is text of at most 500 characters')
            self['message'] = message
        if open is not None:
            if isinstance(open, tuple) and len(open) == 2 and isinstance(open[0], str):
                if open[1] is None:
                    raise ValueError('A record reference needs a saved record id')
                self._pending['open'] = open
            else:
                self['open'] = record_ref(open)
        if new is not None:
            if not isinstance(new, tuple) or len(new) != 2 or not isinstance(new[0], (type, str)) or not isinstance(new[1], dict):
                raise ValueError('Pass new=(Model, values) to open a new record filled with values; Model may be its name')
            if isinstance(new[0], type):
                self['new'] = {'model': storage_name(new[0]), 'values': serialized(*new)}
            else:
                self._pending['new'] = new
        if data is not None:
            self['data'] = data

    def resolve(self, registry):
        """Resolve the models named in open= and new= through the registry and serialize the values; returns self."""
        pending, self._pending = self._pending, {}
        if 'open' in pending:
            model, record_id = pending['open']
            self['open'] = record_ref((registry.resolve(model), record_id))
        if 'new' in pending:
            model, values = pending['new']
            model = registry.resolve(model)
            self['new'] = {'model': storage_name(model), 'values': serialized(model, values)}
        return self


def record_ref(value):
    """(Model, id), a bound record or a draft with an id, as {"model": name, "id": id}."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], type):
        model, record_id = value
    elif isinstance(value, Draft):
        model, record_id = value._definition, value.id
    elif isinstance(value, Record):
        # Record.model is the class unless the model has a field named model; the record's own class is always it.
        model, record_id = value._model, value.id
    else:
        raise ValueError('Name a record as (Model, id) or pass the record itself')
    if record_id is None:
        raise ValueError('A record reference needs a saved record id')
    return {'model': storage_name(model), 'id': RecordId(record_id)}


def serialized(model, values):
    """Field values, and Table rows as lists of objects, as the browser sends them."""
    result = {}
    for name, value in values.items():
        if name in model.__tables__:
            child = model.__tables__[name].child.__declared_fields__
            result.setdefault('children', {})[name] = [{key: None if item is None else child[key].serialize(child[key].validate(item))
                                                         for key, item in row.items() if key in child} for row in value]
        elif name in model.__declared_fields__:
            field = model.__declared_fields__[name]
            result[name] = None if value is None else field.serialize(field.validate(value))
        else:
            raise ValueError(f'{model.__name__} has no field {name!r}')
    return result


# ---------- the draft: load(), starting values, input, submit ----------

def shown(field, value):
    """A value as the browser gets it; an input that failed its check comes back as typed."""
    try:
        return field.serialize(value)
    except (ValueError, TypeError, ArithmeticError):
        return value if isinstance(value, str) else None


def draft_values(draft):
    return {**{name: shown(field, draft._values.get(name)) for name, field in draft._definition.__declared_fields__.items()},
            '_sources': source_values(draft, set(draft._definition.__declared_fields__),
                                      ('compute', 'visible_if', 'required_if', 'read_only_if', 'default_ref', 'filters')),
            'children': {name: [{'_key': row.key, **draft_values(row)} for row in rows] for name, rows in draft._rows.items()}}


def form_draft(definition, context, values, *, source=None, loaded=None):
    """Apply what a person sent to a form draft: unknown and read-only inputs are refused."""
    if not isinstance(values, dict): raise ValidationError({'_form': 'Use a field object'})
    scalars, collections = context._business.split_body(values)
    if set(scalars) - set(definition.__declared_fields__) or set(collections) - set(definition.__tables__):
        raise ValidationError({'_form': 'Unknown input field'})
    if any(definition.__declared_fields__[name].read_only for name in scalars):
        raise AccessError(403, 'A protected input cannot be supplied')
    draft = loaded or Draft(definition, context=context, source=source)
    object.__setattr__(draft, '_submitted', frozenset(scalars))
    draft._values.update(scalars)
    for name, rows in collections.items():
        if not isinstance(rows, list) or len(rows) > 100: raise ValidationError({name: 'Use at most 100 rows'})
        table = definition.__tables__[name]
        originals = {row.key: row for row in draft._rows.get(name, [])}
        result, seen = [], set()
        for value in rows:
            if not isinstance(value, dict): raise ValidationError({name: 'Each row must be an object'})
            value = dict(value)
            key = value.pop('_key', None)
            if key is not None and (not isinstance(key, str) or key in seen or len(key) > 64):
                raise ValidationError({name: 'Invalid or repeated row identity'})
            seen.add(key)
            row = originals.get(key)
            if row is None and originals: raise ValidationError({name: 'A selected row is no longer available'})
            row = form_draft(table.child, context, value, source=source, loaded=row)
            object.__setattr__(row, 'parent', draft)
            if key: object.__setattr__(row, 'key', key)
            result.append(row)
        draft._rows[name] = result
    return draft


class PromptDraft(Draft):
    """A filled prompt. submit() settles and fully validates it, then runs the prompt's submit()."""
    def submit(self):
        return submit_draft(self.context, self)


def build(context, prompt, source, starting=None, values=None, *, wire=False):
    """The prompt's draft: load(source), the starting values, then `values` (the person's input over HTTP when
    `wire`, otherwise trusted Python values that replace whole tables)."""
    check(prompt)
    draft = PromptDraft(prompt, context=context.read_context(), source=source)
    with trusted():
        draft.load(source)
        for name, value in (starting or {}).items():
            setattr(draft, name, value)
    # Rows load() fills keep a key derived from their read-only values, so a later check or submit finds the
    # same rows; such a table takes no new rows.
    for name, rows in draft._rows.items():
        for index, row in enumerate(rows):
            protected = {key: str(value) for key, value in row._values.items() if row._definition.__declared_fields__[key].read_only}
            object.__setattr__(row, 'key', hashlib.sha256(json.dumps([name, index, protected], sort_keys=True).encode()).hexdigest()[:32])
    object.__setattr__(draft, 'context', context)
    if wire:
        return form_draft(prompt, context, values or {}, source=source, loaded=draft)
    for name, value in (values or {}).items():
        if name not in prompt.__declared_fields__ and name not in prompt.__tables__:
            raise ValidationError({name: 'Unknown input'})
        setattr(draft, name, value)
    object.__setattr__(draft, '_submitted', frozenset(values or ()))
    return draft


def submit_draft(context, draft):
    """Settle and fully validate the prompt (ValidationError keyed by field), then run its submit() in this
    transaction, with the transaction session open as in an @action."""
    if context.readonly:
        raise AccessError(403, 'This context is read only')
    settle(context, draft, complete=True)
    with trusted(), handler_context(context, draft):
        result = draft._definition.submit(draft)
    return result.resolve(context._business.registry) if isinstance(result, Outcome) else result


# ---------- over HTTP: tickets, the surface and the routes ----------

def digest(ticket):
    return hashlib.sha256(ticket.encode()).hexdigest()


def issue(business, context, value, *, origin, model=None, record_id=None):
    """Store a single-use ticket for a returned prompt and answer its surface. `origin` is the presenting
    action ('action:<name>' on the record `model`/`record_id`) or endpoint ('api:<name>')."""
    prompt, starting = (type(value), value.starting) if isinstance(value, Prompt) else (value, {})
    tx, user = context._tx, context.user
    record = source = None
    if model is not None:
        record = business.policy.record(tx, user, model, record_id)
        source = context.get(model, record_id)
    draft = build(context, prompt, source, starting)
    errors, states = settle(context, draft, complete=False)
    ticket = secrets.token_urlsafe(24)
    now = int(time.time())
    tx.connection.run(qb.delete('evlet_prompt_tickets').where(qb.le('expires', now)))
    tx.connection.run(qb.insert('evlet_prompt_tickets', ticket=digest(ticket), actor=user.id, prompt=prompt_id(prompt),
                                model=None if model is None else business.registry.name_for(model),
                                record_id=None if record is None else str(record.id),
                                revision=None if record is None else business.record_revision(tx, user, record).decode(),
                                origin=origin, starting=json.dumps(jsonable(serialized(prompt, starting))), expires=now + EXPIRES))
    return surface(business, user, ticket, prompt, draft, model, record, errors, states)


def outcome_of(business, context, value, *, origin=None, model=None, record_id=None):
    """What an action or a submit() returned, as the JSON answer: an Outcome, any JSON value, or the next prompt's surface."""
    if is_prompt(value):
        return {'prompt': issue(business, context, value, origin=origin, model=model, record_id=record_id)}
    if value is None:
        return {}
    if isinstance(value, Outcome):
        value = value.resolve(business.registry)
    encoded = json.dumps(jsonable(value), allow_nan=False)
    if len(encoded) > RESULT_LIMIT:
        raise ValueError('An outcome exceeds 64 KiB')
    return json.loads(encoded)


def check(prompt):
    """Meta.layout and Meta.focus of a prompt (or its rows), checked when it is first shown; returns the layout blocks."""
    from .ui.metadata import check_focus, layout_blocks
    title = option(prompt, 'title') or prompt.__name__
    visible = {key: field for key, field in prompt.__app_fields__.items() if not field.hidden}
    blocks, warnings = layout_blocks(prompt, option(prompt, 'layout'), visible, title)
    if warnings:
        raise ValueError(warnings[0])
    if any(block['kind'] == 'custom' for block in blocks):
        raise ValueError(f'{prompt.__name__}.Meta.layout: custom blocks are not supported in a prompt')
    if option(prompt, 'focus') is not None:
        check_focus(f'{prompt.__name__}.Meta.focus', prompt, option(prompt, 'focus'))
    return blocks


def schema(registry, prompt, name):
    """The prompt as the browser's record engine reads a model: fields, layout, Tables, rules and the finite plan."""
    from .reactivity import plans, serialized as rule
    from .ui.metadata import field_schema
    title = option(prompt, 'title') or prompt.__name__
    blocks = check(prompt)
    entries = [{
        'name': name, 'label': title, 'plural': title, 'title_field': 'id', 'list_fields': [], 'search_fields': [],
        'fields': [{**field_schema(field), 'stored': True} for field in prompt.__app_fields__.values()],
        'layout': [block['group'] for block in blocks if block['kind'] == 'section'],
        'record_layout': {'blocks': [{'kind': block['kind'], 'name': block['name'], 'main': block['main'],
                                      **({'label': block['name']} if block['kind'] == 'section' else {})} for block in blocks]},
        'behavior': {key: {kind: {item: rule(value) for item, value in entry.items()} if kind == 'filters' else rule(entry)
                           for kind, entry in value.items()} for key, value in plans(prompt).items()},
        'children': [{'name': table, 'model': declared.child.__form_name__, 'label': declared.label or table.replace('_', ' ').capitalize(),
                      'row_changes': True, 'min_rows': declared.min_rows, 'max_rows': min(declared.max_rows, 100),
                      **({'totals': list(declared.totals)} if declared.totals else {})}
                     for table, declared in prompt.__tables__.items()],
        'permissions': {'create': True, 'update': True, 'delete': False}, 'actions': [], 'autosave': False,
        **({'focus': option(prompt, 'focus')} if option(prompt, 'focus') else {}),
        **({'script': vars(prompt)['__script__']} if vars(prompt).get('__script__') else {}),
    }]
    for table, declared in prompt.__tables__.items():
        entries += schema(registry, declared.child, declared.child.__form_name__)
        entries[-1]['child_only'] = True
    plan = registry.plan_for(prompt) if issubclass(prompt, Prompt) else None
    if plan is not None:
        entries[0]['calculation'] = {'language': plan['language'], 'numeric_profile': plan['numeric_profile'],
                                     'model_schema_hash': plan['model_schema_hash'], 'capabilities': {}, 'plan': plan}
    return entries


def surface(business, user, ticket, prompt, draft, model, record, errors, states):
    name = prompt.__form_name__
    source = None
    if record is not None:
        from .ui.metadata import view_for
        field = view_for(model).get('title_field', 'id')
        readable = business.policy.fields(user, model)
        title = getattr(record, field, None) if field in readable else None
        source = {'model': business.registry.name_for(model), 'id': record.id, 'title': str(title or record.id)}
    models = schema(business.registry, prompt, name)
    # A Table that load() (or the starting values) filled takes no new rows (form_draft): the grid offers none.
    for child in models[0]['children']:
        if draft._rows.get(child['name']):
            child['max_rows'] = len(draft._rows[child['name']])
    return {'ticket': ticket, 'name': name, 'title': option(prompt, 'title') or prompt.__name__,
            'primary': option(prompt, 'primary') or 'Submit', 'source': source,
            'models': models, 'values': draft_values(draft), 'errors': errors, 'states': states}


def check_origin(business, context, origin, model, record_id):
    """The presenting action or endpoint, checked again on the source as it is now."""
    from .documents import load_draft
    from .guards import action_available
    user = context.user
    kind, _, name = origin.partition(':')
    if kind == 'api':
        endpoint = next((item for item in business.apis if item.name == name), None)
        if endpoint is None:
            raise AccessError(404, 'Prompt is unavailable')
        business.policy.require(user, business.registry.resolve(endpoint.permission[0]), endpoint.permission[1])
        return None
    method = getattr(model, name, None)
    declaration = getattr(method, '__action__', None)
    if declaration is None or not has_role(user, declaration['roles']):
        raise AccessError(403, 'Action is unavailable')
    record = business.policy.record(context._tx, user, model, record_id, 'update')
    saved, _ = load_draft(context, model, record_id, {})
    action_available(context, model, method, declaration, saved)
    return record


def dispatch(business, scope, body, user):
    """POST /api/_prompts/{ticket}/check and /submit."""
    parts = scope['path'].removeprefix('/api/_prompts/').split('/')
    if len(parts) != 2 or parts[1] not in ('check', 'submit'):
        raise AccessError(404, 'Prompt operation is unavailable')
    allow(scope['method'], 'POST')
    checked_body(body, {'values'}, 'Unknown prompt argument')
    submit = parts[1] == 'submit'
    with business.context(user, readonly=not submit) as context:
        connection = context._tx.connection
        row = connection.run(qb.select('evlet_prompt_tickets').where(ticket=digest(parts[0]))).one()
        if row is None or row['actor'] != user.id or row['prompt'] not in PROMPTS:
            raise AccessError(404, 'Prompt is unavailable')
        if row['result'] is not None:
            # The ticket is the idempotency key: a retried submit answers the first result.
            if submit:
                return 200, json.loads(row['result']), []
            raise AccessError(409, 'This prompt was already submitted')
        if row['expires'] <= int(time.time()):
            raise AccessError(410, 'This prompt expired. Open it again.')
        prompt = PROMPTS[row['prompt']]
        model = source = None
        if row['model'] is not None:
            model = business.registry.resolve(row['model'])
            record = check_origin(business, context, row['origin'], model, RecordId(row['record_id']))
            if business.record_revision(context._tx, user, record).decode() != row['revision']:
                raise AccessError(409, 'The record changed after this prompt opened. Close it and open it again.')
            source = context.get(model, record.id)
        else:
            check_origin(business, context, row['origin'], None, None)
        starting = json.loads(row['starting'])
        draft = build(context, prompt, source, wire_values(prompt, starting), body.get('values', {}), wire=True)
        if not submit:
            errors, states = settle(context, draft, complete=False)
            return 200, {'values': draft_values(draft), 'errors': errors, 'states': states}, []
        value = submit_draft(context, draft)
        # A chained prompt keeps the source and the presenting action, checked again when it is submitted.
        result = outcome_of(business, context, value, origin=row['origin'], model=model, record_id=None if model is None else source.id)
        connection.run(qb.update('evlet_prompt_tickets', result=json.dumps(result)).where(ticket=row['ticket']))
        return 200, result, []


def wire_values(prompt, values):
    """Starting values as stored on a ticket, back to field values."""
    result = {name: prompt.__declared_fields__[name].clean(value) for name, value in values.items() if name != 'children'}
    for name, rows in values.get('children', {}).items():
        child = prompt.__tables__[name].child.__declared_fields__
        result[name] = [{key: child[key].clean(item) for key, item in row.items()} for row in rows]
    return result
