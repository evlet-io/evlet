"""Transient Python forms for source-bound operations and bounded reports."""

from dataclasses import dataclass
from types import SimpleNamespace
import hashlib
import json

from .auth import AccessError
from .models import Model, ValidationError
from .documents import Draft, Context, Snapshot, settle
from .ui.metadata import field_schema, source_schema
from .reactivity import plans, serialized


class Dialog(Model):
    title = ''
    action = ''
    permission = None
    __transient__ = True

    def load(self, source):
        pass

    def submit(self, source):
        raise NotImplementedError('Define the dialog submit operation')


@dataclass(frozen=True)
class Column:
    name: str
    type: str = 'text'
    label: str | None = None
    link: str | None = None
    total: bool = False
    unit: str | None = None

    def __post_init__(self):
        from .models import identifier
        identifier(self.name)
        if self.type not in ('text', 'integer', 'decimal', 'currency', 'date', 'boolean', 'link'):
            raise ValueError('Unknown report column type')
        if self.total and self.type not in ('integer', 'decimal', 'currency'):
            raise ValueError('Only numeric columns can declare totals')
        if self.total and not self.unit: raise ValueError('Totals require an explicit common unit or currency')


class Report(Model):
    title = ''
    roles = ()
    permissions = ()  # Alias for roles, kept for existing reports.
    autorun = False
    columns = ()
    __transient__ = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        roles, permissions = cls.__dict__.get('roles'), cls.__dict__.get('permissions')
        if roles is not None and permissions is not None and tuple(roles) != tuple(permissions):
            raise ValueError('Set report roles once; permissions is an alias for roles')
        value = roles if roles is not None else permissions
        if value is not None:
            cls.roles = cls.permissions = tuple(value)

    def run(self):
        raise NotImplementedError('Define the report run operation')


def metadata(definition, name, source=None):
    source = getattr(definition, '__source__', source)
    fields = plans(definition)
    # A child row may declare a field called title; only a text title labels the form.
    title = getattr(definition, 'title', '')
    result = {'name': name, 'label': title if isinstance(title, str) and title else definition.__name__,
              'fields': [field_schema(field) for field in definition.__declared_fields__.values()],
              'behavior': {key: {kind: {name: serialized(value) for name, value in entry.items()} if kind == 'filters' else serialized(entry)
                                for kind, entry in value.items()} for key, value in fields.items()},
              'children': [], 'sources': source_schema(definition),
              'source_fields': [] if source is None else [field_schema(field) for field in source.__declared_fields__.values()]}
    for table_name, table in definition.__tables__.items():
        result['children'].append({'name': table_name, 'label': table.label or table_name.title(),
                                   'definition': metadata(table.child, name + '.' + table_name, source)})
    return result


def shown(field, value):
    """A value as the browser gets it; an input that failed its check comes back as typed."""
    try:
        return field.serialize(value)
    except (ValueError, TypeError, ArithmeticError):
        return value if isinstance(value, str) else None


def draft_values(draft):
    from .documents import source_values
    return {**{name: shown(field, draft._values.get(name)) for name, field in draft._definition.__declared_fields__.items()},
            '_sources': source_values(draft, set(draft._definition.__declared_fields__),
                                      ('compute', 'visible_if', 'required_if', 'read_only_if', 'default_ref', 'filters')),
            '_source': {} if draft.source is None else {key: value for key, value in draft.source._data.items() if not isinstance(value, (Snapshot, tuple))},
            'children': {name: [{'_key': row.key, **draft_values(row)} for row in rows] for name, rows in draft._rows.items()}}


def form_draft(definition, context, values, *, source=None, loaded=None):
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


class Forms:
    def __init__(self, business, reports=()):
        self.business = business
        self.reports, self.dialogs = {}, {}
        for report in reports:
            if not isinstance(report, type) or not issubclass(report, Report): raise ValueError('Register Report classes')
            name = getattr(report, '__resource_id__', report.__name__)
            if name in self.reports: raise ValueError('Duplicate report identity: ' + name)
            if type(report.autorun) is not bool or set(report.roles) - {'admin', 'editor', 'viewer'}:
                raise ValueError('Reports require explicit roles and a boolean autorun')
            if not report.columns or any(not isinstance(column, Column) for column in report.columns):
                raise ValueError('Reports require typed columns')
            if len({column.name for column in report.columns}) != len(report.columns): raise ValueError('Duplicate report column')
            self.reports[name] = report
        for model in business.registry.models.values():
            for value in vars(model).values():
                if isinstance(value, type) and issubclass(value, Dialog) and value is not Dialog:
                    name = value.action or value.__name__
                    if (model, name) in self.dialogs or (model, name) in business.actions: raise ValueError('Duplicate dialog action')
                    value.__source__ = model
                    self.dialogs[model, name] = value
        from .models import resolve_sources
        from .reactivity import graph
        for definition in [*self.reports.values(), *self.dialogs.values()]:
            resolve_sources(business.registry, definition)
            graph(business.registry, definition)

    def initialize(self):
        with self.business.database.transaction() as tx:
            tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_operations (key TEXT PRIMARY KEY, actor INTEGER NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL)')

    def catalog(self, user):
        return [{**metadata(report, name), 'autorun': report.autorun,
                 'columns': [vars(column) for column in report.columns]}
                for name, report in self.reports.items() if user.role in report.roles]

    def permission(self, context, definition, owner, record_id):
        permission = definition.permission or (owner, 'update')
        model, operation = permission
        model = self.business.registry.resolve(model)
        self.business.policy.require(context.user, model, operation)
        # The source itself always requires the owning operation, even when another capability is declared.
        return self.business.policy.record(context._tx, context.user, owner, record_id, 'update')

    def dispatch(self, scope, body, user):
        path, method = scope['path'], scope['method']
        if path.startswith('/api/_reports/'):
            name = path.removeprefix('/api/_reports/')
            evaluate_only = name.endswith('/evaluate')
            if evaluate_only: name = name.removesuffix('/evaluate')
            report = self.reports.get(name)
            if method != 'POST' or report is None or user.role not in report.roles: raise AccessError(403, 'Report is unavailable')
            if set(body) - {'filters', 'revision', 'limit', 'offset'}: raise ValidationError({'_form': 'Unknown report argument'})
            limit, offset = body.get('limit', 50), body.get('offset', 0)
            if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 2**53-1:
                raise ValidationError({'page': 'Invalid report page'})
            with self.business.context(user, readonly=True) as context:
                context.limit, context.offset = limit, offset
                draft = form_draft(report, context, body.get('filters', {}))
                errors, states = settle(context, draft, complete=not evaluate_only)
                filters = draft_values(draft)
                if evaluate_only: return 200, {'values': filters, 'errors': errors, 'states': states, 'revision': body.get('revision')}, []
                object.__setattr__(draft, '_readonly', True)
                result = draft.run()
                if not isinstance(result, dict) or not isinstance(result.get('rows'), list) or len(result['rows']) > limit:
                    raise ValueError('Reports return a bounded rows/total result from context.list')
                if type(result.get('total')) is not int or result['total'] < len(result['rows']): raise ValueError('Report total must be a nonnegative row count')
                allowed = {column.name for column in report.columns}
                if any(not isinstance(row, dict) or set(row) - allowed for row in result['rows']): raise ValueError('Report rows must match declared columns')
                from .fields import Text, Integer, Decimal, Currency, Date, Boolean
                types = {'text': Text, 'integer': Integer, 'decimal': Decimal, 'currency': Currency, 'date': Date, 'boolean': Boolean, 'link': Integer}
                totals = {}
                for column in report.columns:
                    field = types[column.type]()
                    for row in result['rows']:
                        row[column.name] = field.serialize(field.clean(row.get(column.name)))
                    if column.total:
                        from decimal import Decimal as Number
                        totals[column.name] = str(sum((Number(str(row[column.name])) for row in result['rows'] if row[column.name] is not None), Number(0)))
                if totals: result = {**result, 'totals': {'scope': 'page', 'values': totals}}
                return 200, {**result, 'filters': filters, 'revision': body.get('revision'), 'limit': limit, 'offset': offset}, []
        if path.startswith('/api/_dialogs/'):
            parts = path.removeprefix('/api/_dialogs/').split('/')
            if len(parts) != 4 or method != 'POST' or parts[3] not in ('load', 'submit', 'evaluate'):
                raise AccessError(404, 'Dialog operation is unavailable')
            model = self.business.registry.resolve(parts[0])
            try: record_id = int(parts[1])
            except ValueError: raise AccessError(404, 'Record not found') from None
            definition = self.dialogs.get((model, parts[2]))
            if definition is None: raise AccessError(404, 'Dialog is unavailable')
            submit = parts[3] == 'submit'
            with self.business.context(user, readonly=not submit) as context:
                record = self.permission(context, definition, model, record_id)
                source = context.get(model, record_id)
                signature = json.dumps({'path': path, 'body': body}, sort_keys=True, separators=(',', ':'))
                key = body.get('key')
                if submit:
                    if not isinstance(key, str) or not 8 <= len(key) <= 128: raise ValidationError({'key': 'Supply an idempotency key of 8 to 128 characters'})
                    key = f'{user.id}:{model.__name__}:{record_id}:{parts[2]}:{key}'
                    old = context._tx.connection.execute('SELECT * FROM evlet_operations WHERE key=?', (key,)).fetchone()
                    if old:
                        if old['request'] != signature or old['actor'] != user.id: raise AccessError(409, 'Idempotency key has different input')
                        return 200, json.loads(old['result']), []
                if b'if-match' not in dict(scope.get('headers', [])): raise AccessError(428, 'A source revision is required')
                self.business.check_revision(scope, context._tx, user, record)
                context.source_revision = self.business.record_revision(context._tx, user, record).decode()
                context.idempotency_key = key
                draft = Draft(definition, context=context.read_context(), source=source)
                draft.load(source)
                for name, rows in draft._rows.items():
                    for index, row in enumerate(rows):
                        protected = {key: str(value) for key, value in row._values.items() if row._definition.__declared_fields__[key].read_only}
                        identity = hashlib.sha256(json.dumps([name, index, protected], sort_keys=True).encode()).hexdigest()
                        object.__setattr__(row, 'key', identity)
                object.__setattr__(draft, 'context', context)
                if parts[3] != 'load': draft = form_draft(definition, context, body.get('values', {}), source=source, loaded=draft)
                errors, states = settle(context, draft, complete=submit)
                if not submit:
                    descriptor = metadata(definition, parts[2])
                    readable_source = self.business.policy.fields(user, model)
                    def project_source(item):
                        item['source_fields'] = [field for field in item['source_fields'] if field['name'] in readable_source]
                        for table in item['children']: project_source(table['definition'])
                    project_source(descriptor)
                    for table in descriptor['children']: table['allow_add'] = not bool(draft._rows.get(table['name']))
                    return 200, {'definition': descriptor, 'values': draft_values(draft), 'errors': errors, 'states': states, 'revision': body.get('revision')}, []
                outcome = draft.submit(source)
                encoded = json.dumps(outcome, allow_nan=False)
                if len(encoded) > 65536: raise ValueError('Dialog result exceeds 64 KiB')
                context._tx.connection.execute('INSERT INTO evlet_operations(key,actor,request,result) VALUES (?,?,?,?)', (key, user.id, signature, encoded))
                return 200, outcome, []
        return None
