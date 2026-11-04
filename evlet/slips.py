"""Slips: controlled forms presented from code.

A slip declares fields like a model and never saves itself. When it is handed in, its values go to the
slip's hand_in() method, or become a document of another model through the normal create path.
"""

import hashlib
import json
import re

from .auth import AccessError
from .documents import Draft, settle, walk
from .forms import draft_values, form_draft, metadata
from .identity import RecordId, jsonable
from .models import Model, ValidationError, storage_name
from .reactivity import evaluate, plans

RESULT_LIMIT = 65536


def slip_name(slip):
    """The slip's name in routes and schemas: StartProject -> start_project."""
    name = getattr(slip, '__resource_id__', None)
    return name or re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', slip.__name__).lower()


class Slip(Model):
    """A form with the full field capability of a model that is presented, filled and handed in.

    Set `source = SomeModel` to present it from a record of that model (a button on the record), or
    `menu = True` to list it under New. Choose one outcome: define hand_in(self), or set `becomes`.
    """
    title = ''
    hand_in_label = ''
    roles = ('admin', 'editor')
    permission = None
    becomes = None
    review = True
    menu = False
    layout = ()
    __transient__ = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # The source model is kept apart: inside a slip, self.source is the source record.
        if 'source' in cls.__dict__:
            cls.__slip_source__ = cls.__dict__['source']
            delattr(cls, 'source')

    def load(self, source):
        pass

    def document(self, source):
        """The values of the document a `becomes` slip turns into. By default, fields and tables with
        the same names copy over."""
        return same_named(self, self._definition.becomes)

    def hand_in(self):
        raise NotImplementedError('Define hand_in() or set becomes')


class Outcome(dict):
    """What a hand-in returns: a message to show, a record to open, data for browser code, or the next
    slip (present(...))."""
    def __init__(self, message=None, *, open=None, data=None, present=None):
        super().__init__()
        if message is not None:
            if not isinstance(message, str) or len(message) > 500:
                raise ValueError('An outcome message is text of at most 500 characters')
            self['message'] = message
        if open is not None:
            self['open'] = record_ref(open)
        if data is not None:
            self['data'] = data
        if present is not None:
            if not isinstance(present, dict) or set(present) != {'present'}:
                raise ValueError('Pass present(...) as the next slip')
            self['present'] = present['present']


def record_ref(value):
    """(Model, id) or a draft or record with an id, as {"model": name, "id": id}."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], type):
        model, record_id = value
    elif isinstance(value, Draft):
        model, record_id = value._definition, value.id
    else:
        raise ValueError('Name a record as (Model, id) or pass the record itself')
    if record_id is None:
        raise ValueError('A record reference needs a saved record id')
    return {'model': storage_name(model), 'id': RecordId(record_id)}


def present(slip, *, source=None, values=None):
    """An instruction to present `slip`, returned from a model action or from hand_in().

    The browser opens the slip with these starting values. They are treated as input: a read-only
    field cannot be filled this way (use load()).
    """
    if not isinstance(slip, type) or not issubclass(slip, Slip) or slip is Slip:
        raise ValueError('present() takes a Slip class')
    serialized = {}
    for name, value in (values or {}).items():
        if name in slip.__declared_fields__:
            field = slip.__declared_fields__[name]
            serialized[name] = field.serialize(field.validate(value)) if value is not None else None
        elif name in slip.__tables__:
            child = slip.__tables__[name].child
            serialized.setdefault('children', {})[name] = [{key: child.__declared_fields__[key].serialize(child.__declared_fields__[key].validate(item))
                                 if item is not None else None for key, item in row.items()} for row in value]
        else:
            raise ValueError(f'{slip.__name__} has no field {name!r}')
    return {'present': {'slip': slip_name(slip), 'source': None if source is None else record_ref(source), 'values': serialized}}


def row_models(business, target):
    """The row model of each child table of `target`: declared Tables and registered ChildTables."""
    result = {name: table.child for name, table in target.__tables__.items()}
    result.update({child.name: child.model for child in business.children.values() if child.parent is target})
    return result


def same_named(draft, target):
    values = {}
    fields = target.__declared_fields__
    for name, value in draft._values.items():
        field = fields.get(name)
        if field is not None and field.compute is None and field.query is None and not field.read_only:
            values[name] = value
    tables = row_models(draft.context._business, target)
    for name, rows in draft._rows.items():
        if name not in tables: continue
        child = tables[name].__declared_fields__
        values[name] = [{key: value for key, value in row._values.items()
                         if key in child and child[key].compute is None and not child[key].read_only} for row in rows]
    return values


def rules(draft):
    """Visible, required and read-only states of every field with a condition, by path."""
    result = {}
    for prefix, item in walk(draft):
        entries = plans(item._definition)
        for name, field in item._definition.__declared_fields__.items():
            entry = entries.get(name, {})
            if not any(key in entry for key in ('visible_if', 'required_if', 'read_only_if')): continue
            state = {}
            for key, label in (('visible_if', 'visible'), ('required_if', 'required'), ('read_only_if', 'read_only')):
                if key in entry:
                    try: state[label] = bool(evaluate(entry[key], item.read))
                    except (ValueError, TypeError, ArithmeticError): state[label] = key == 'visible_if'
            result[prefix + name] = state
    return result


class Slips:
    def __init__(self, business, slips=()):
        self.business = business
        self.slips = {}
        registry, policy = business.registry, business.policy
        for slip in slips:
            if not isinstance(slip, type) or not issubclass(slip, Slip) or slip is Slip:
                raise ValueError('Register Slip classes')
            name = slip_name(slip)
            if name in self.slips:
                raise ValueError('Duplicate slip name: ' + name)
            label = slip.__name__
            source = getattr(slip, '__slip_source__', None)
            if source is not None:
                source = registry.resolve(source)
                if source in business.child_models:
                    raise ValueError(f'{label}: a slip cannot be presented from child rows')
            slip.__source__ = source
            if not isinstance(slip.roles, (tuple, list)) or not slip.roles or set(slip.roles) - set(policy.roles):
                raise ValueError(f'{label}: roles must name roles of the policy')
            if type(slip.review) is not bool or type(slip.menu) is not bool:
                raise ValueError(f'{label}: review and menu are True or False')
            if slip.menu and source is not None:
                raise ValueError(f'{label}: a slip with a source is presented from its record, not the New menu')
            handles = slip.hand_in is not Slip.hand_in
            if slip.becomes is not None:
                slip.becomes = registry.resolve(slip.becomes)
                if slip.becomes in business.child_models:
                    raise ValueError(f'{label}: becomes names a model with its own records')
                if handles:
                    raise ValueError(f'{label}: choose one outcome, hand_in() or becomes')
            elif not handles:
                raise ValueError(f'{label}: define hand_in() or set becomes')
            if slip.permission is not None:
                if source is None or not isinstance(slip.permission, tuple) or len(slip.permission) != 2:
                    raise ValueError(f'{label}: permission is (Model, operation) on a slip with a source')
                slip.permission = (registry.resolve(slip.permission[0]), slip.permission[1])
            names = set(slip.__declared_fields__) | set(slip.__tables__)
            for group in slip.layout:
                if not isinstance(group, tuple) or len(group) != 2 or not isinstance(group[0], str) or set(group[1]) - names:
                    raise ValueError(f'{label}: layout groups are (label, field names) of declared fields')
            from .models import resolve_sources
            from .reactivity import graph
            resolve_sources(registry, slip)
            graph(registry, slip)
            self.slips[name] = slip

    def initialize(self):
        with self.business.database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_operations (key TEXT PRIMARY KEY, actor INTEGER NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL)')

    def allowed(self, user, slip):
        """Whether the schema should offer this slip to the role (the routes still check everything)."""
        policy = self.business.policy
        if user.role not in slip.roles:
            return False
        if slip.__source__ is not None:
            model, operation = slip.permission or (slip.__source__, 'update')
            if not policy.allows(user, slip.__source__, 'read') or not policy.allows(user, model, operation):
                return False
        return slip.becomes is None or policy.allows(user, slip.becomes, 'create')

    def entry(self, name, slip):
        return {'name': name, 'label': slip.title or slip.__name__, 'hand_in_label': slip.hand_in_label or slip.title or 'Hand in'}

    def schema(self, user, model=None):
        """Slips the role may present: from records of `model`, or (model None) from the New menu."""
        return [self.entry(name, slip) for name, slip in self.slips.items()
                if (slip.__source__ is model if model is not None else slip.menu) and self.allowed(user, slip)]

    def require(self, context, slip, source):
        """Check the role, the source record and the target model; returns the source record or None."""
        user, policy, registry = context.user, self.business.policy, self.business.registry
        if user.role not in slip.roles:
            raise AccessError(403, 'Slip is unavailable')
        if slip.becomes is not None:
            policy.require(user, slip.becomes, 'create')
        if slip.__source__ is None:
            if source is not None:
                raise ValidationError({'source': 'This slip is not presented from a record'})
            return None
        if not isinstance(source, dict) or set(source) != {'model', 'id'}:
            raise ValidationError({'source': 'Name the source record as {"model", "id"}'})
        try:
            model = registry.resolve(source['model'])
        except ValueError:
            raise AccessError(404, 'Record not found') from None
        try:
            record_id = RecordId(source['id'])
        except ValueError:
            record_id = None
        if model is not slip.__source__ or record_id is None:
            raise ValidationError({'source': 'This slip is presented from a ' + storage_name(slip.__source__) + ' record'})
        permission, operation = slip.permission or (model, 'update')
        policy.require(user, permission, operation)
        return policy.record(context._tx, user, model, record_id, operation if permission is model else 'read')

    def dispatch(self, scope, body, user):
        parts = scope['path'].removeprefix('/api/_slips/').split('/')
        if len(parts) != 2 or scope['method'] != 'POST' or parts[1] not in ('present', 'check', 'hand-in'):
            raise AccessError(404, 'Slip operation is unavailable')
        slip = self.slips.get(parts[0])
        if slip is None or user is None:
            raise AccessError(404, 'Slip is unavailable')
        if not isinstance(body, dict) or set(body) - {'source', 'values', 'key', 'revision'}:
            raise ValidationError({'_form': 'Unknown slip argument'})
        operation = parts[1]
        hand_in = operation == 'hand-in'
        business = self.business
        with business.context(user, readonly=not hand_in) as context:
            record = self.require(context, slip, body.get('source'))
            source = None if record is None else context.get(slip.__source__, record.id)
            key = signature = None
            if hand_in:
                key = body.get('key')
                if not isinstance(key, str) or not 8 <= len(key) <= 128:
                    raise ValidationError({'key': 'Supply an idempotency key of 8 to 128 characters'})
                key = f'slip:{user.id}:{parts[0]}:{key}'
                signature = json.dumps({'path': scope['path'], 'body': body}, sort_keys=True, separators=(',', ':'))
                old = context._tx.connection.execute('SELECT * FROM evlet_operations WHERE key=?', (key,)).fetchone()
                if old:
                    if old['request'] != signature or old['actor'] != user.id:
                        raise AccessError(409, 'Idempotency key has different input')
                    return 200, json.loads(old['result']), []
                if record is not None:
                    # A slip filled against an older revision of its source is refused.
                    if b'if-match' not in dict(scope.get('headers', [])):
                        raise AccessError(428, 'A source revision is required')
                    business.check_revision(scope, context._tx, user, record)
            draft = Draft(slip, context=context.read_context(), source=source)
            draft.load(source)
            # Rows load() supplies keep a key derived from their protected values, so a later check or
            # hand-in finds the same rows; such a table takes no new rows.
            loaded = {name for name, rows in draft._rows.items() if rows}
            for name, rows in draft._rows.items():
                for index, row in enumerate(rows):
                    protected = {field: str(value) for field, value in row._values.items() if row._definition.__declared_fields__[field].read_only}
                    object.__setattr__(row, 'key', hashlib.sha256(json.dumps([name, index, protected], sort_keys=True).encode()).hexdigest())
            object.__setattr__(draft, 'context', context)
            draft = form_draft(slip, context, body.get('values', {}), source=source, loaded=draft)
            errors, states = settle(context, draft, complete=hand_in)
            if not hand_in:
                return 200, self.surface(user, parts[0], slip, draft, record, loaded, errors, states, body.get('revision')), []
            if slip.becomes is None:
                outcome = draft.hand_in()
                if outcome is None:
                    outcome = Outcome()
                if not isinstance(outcome, dict):
                    raise ValueError('hand_in() returns an Outcome, present(...), a dict or None')
            else:
                outcome = self.convert(context, slip, draft, source)
            encoded = json.dumps(jsonable(outcome), allow_nan=False)
            if len(encoded) > RESULT_LIMIT:
                raise ValueError('Slip outcome exceeds 64 KiB')
            context._tx.connection.execute('INSERT INTO evlet_operations(key,actor,request,result) VALUES (?,?,?,?)',
                                           (key, user.id, signature, encoded))
            return 200, json.loads(encoded), []

    def convert(self, context, slip, draft, source):
        """Turn a handed-in slip into a document of `becomes`: prefilled values to review, or created now."""
        target, policy, user = slip.becomes, self.business.policy, context.user
        values = draft.document(source)
        if not isinstance(values, dict):
            raise ValueError('document() returns a mapping of field values')
        models = row_models(self.business, target)
        scalars = {name: value for name, value in values.items() if name not in models}
        tables = {name: value for name, value in values.items() if name in models}
        unknown = set(scalars) - set(target.__declared_fields__)
        if unknown:
            raise ValueError(f'document() names fields {target.__name__} does not have: ' + ', '.join(sorted(unknown)))
        # Values the role cannot write are refused, not dropped.
        policy.check_values(context._tx, user, target, scalars)
        name = storage_name(target)
        if not slip.review:
            # The normal create path of the model: declarative documents settle, other models take the HTTP route's checks.
            body = {**scalars, **({'children': tables} if tables else {})}
            if getattr(target, '__declaration_mode__', False):
                record_id = context.create(target, **body)['id']
            else:
                record_id = self.business.create_plain(context._tx, user, target, body)[0].id
            return {'document': {'model': name, 'id': record_id}, 'open': {'model': name, 'id': record_id}}
        fields = target.__declared_fields__
        prefill = {key: None if value is None else fields[key].serialize(fields[key].validate(value)) for key, value in scalars.items()}
        for key, rows in tables.items():
            child = models[key].__declared_fields__
            prefill.setdefault('children', {})[key] = [{field: None if value is None else child[field].serialize(child[field].validate(value))
                                                        for field, value in row.items() if field in child} for row in rows]
        return {'document': {'model': name, 'values': prefill, 'review': True}}

    def surface(self, user, name, slip, draft, record, loaded, errors, states, revision):
        """The neutral schema the browser renders: definition, values, rules, states and errors."""
        descriptor = metadata(slip, name)
        readable = self.business.policy.fields(user, slip.__source__) if slip.__source__ is not None else set()
        def project(item, definition):
            item['source_fields'] = [field for field in item['source_fields'] if field['name'] in readable]
            for field in item['fields']:
                declared = definition.__declared_fields__[field['name']]
                if declared.compute is not None or declared.query is not None:
                    field['calculated'] = True
            for table in item['children']: project(table['definition'], definition.__tables__[table['name']].child)
        project(descriptor, slip)
        for table in descriptor['children']:
            table['allow_add'] = table['name'] not in loaded
        title = None
        if record is not None:
            from .ui.metadata import view_for
            field = view_for(slip.__source__).get('title_field', 'id')
            title = str(getattr(record, field, None) or record.id) if field in readable or field == 'id' else str(record.id)
        layout = [{'label': label, 'fields': list(fields)} for label, fields in slip.layout]
        return {'slip': name, 'version': 1, 'label': slip.title or slip.__name__,
                'hand_in_label': slip.hand_in_label or slip.title or 'Hand in',
                'source': None if record is None else {'model': storage_name(slip.__source__), 'id': record.id, 'title': title,
                                                       'revision': self.business.record_revision(draft.context._tx, user, record).decode()},
                'outcome': {'kind': 'handler'} if slip.becomes is None else
                           {'kind': 'document', 'model': storage_name(slip.becomes), 'review': slip.review},
                'layout': layout, 'definition': descriptor, 'values': draft_values(draft),
                'rules': rules(draft), 'errors': errors, 'states': states, 'revision': revision}
