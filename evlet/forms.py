"""Transient Python forms for source-bound operations and bounded reports."""

from dataclasses import dataclass
import hashlib
import json
import re

from .auth import AccessError
from .identity import jsonable, path_id
from .models import Model, ValidationError
from .documents import Draft, Snapshot, settle
from .permissions import trusted
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
    # 'model.field' (the model's route name): the column is left out for anyone who cannot read that field.
    source: str | None = None

    def __post_init__(self):
        from .models import identifier
        identifier(self.name)
        if self.source is not None and (not isinstance(self.source, str) or self.source.count('.') != 1):
            raise ValueError("A column source is 'model.field'")
        if self.type not in ('text', 'integer', 'decimal', 'currency', 'date', 'boolean', 'link'):
            raise ValueError('Unknown report column type')
        if self.total and self.type not in ('integer', 'decimal', 'currency'):
            raise ValueError('Only numeric columns can declare totals')
        if self.total and not self.unit: raise ValueError('Totals require an explicit common unit or currency')


# Chart types: a report draws bars or lines; a record layout chart may also show its measures as stat numbers.
CHART_TYPES = ('bar', 'line', 'stat')


@dataclass(frozen=True)
class Chart:
    """A chart. In a report: bars or a line of the report's rows, one label column (x) and up to four number
    columns (y). In Meta.layout: a block over the records of `model` whose Link `link` points to the record,
    y naming up to four of that model's declared measures, grouped by its field x ('field' or 'field:month');
    type 'stat' shows the measures as numbers, without x."""
    type: str
    x: str = None
    y: tuple = ()
    label: str | None = None
    model: object = None
    link: str = None
    filters: object = None

    def __post_init__(self):
        if self.type not in CHART_TYPES: raise ValueError('A chart is bar, line or stat')
        object.__setattr__(self, 'y', (self.y,) if isinstance(self.y, str) else tuple(self.y))
        if not 1 <= len(self.y) <= 4: raise ValueError('A chart shows one to four columns or measures')
        if self.label is not None and (not isinstance(self.label, str) or not self.label):
            raise ValueError('A chart label is nonempty text')
        if self.model is None:
            if self.type == 'stat' or self.link is not None or self.filters is not None:
                raise ValueError('A report chart is bar or line over its own rows; model, link and filters are for record layout charts')
            if not isinstance(self.x, str): raise ValueError('A report chart names its label column as x')
            return
        if not isinstance(self.model, (type, str)) or self.link is not None and (not isinstance(self.link, str) or not self.link):
            raise ValueError('A layout chart names a model and, optionally, its Link field to this model')
        if (self.type == 'stat') != (self.x is None):
            raise ValueError("A bar or line chart groups by a field (x='status' or x='due:month'); a stat chart has no x")
        if self.filters is not None and not isinstance(self.filters, (dict, list, tuple)):
            raise ValueError('Chart filters use the list filter syntax: a dict or a list of conditions')

    def public(self):
        """A report chart as /api/_reports lists it."""
        return {'type': self.type, 'x': self.x, 'y': list(self.y), 'label': self.label}


class Report(Model):
    title = ''
    roles = ()
    permissions = ()  # Alias for roles, kept for existing reports.
    autorun = False
    columns = ()
    group = ''  # The sidebar heading the report is listed under.
    model = None  # The model whose list offers the report.
    sortable = ()  # Columns people may sort by; run() reads the choice as context.sort, e.g. ('-total',).
    chart = None
    export = True
    group_by = None  # A column whose rows are kept together, with a subtotal under each group.
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
            self.check(report)
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

    def check(self, report):
        registry, columns = self.business.registry, {column.name: column for column in report.columns}
        numbers = ('integer', 'decimal', 'currency')
        if not isinstance(report.group, str): raise ValueError('Report group is text')
        if type(report.export) is not bool: raise ValueError('Report export is True or False')
        if report.model is not None: registry.resolve(report.model)
        if not isinstance(report.sortable, (tuple, list)) or set(report.sortable) - set(columns):
            raise ValueError('Report sortable names declared columns')
        for column in report.columns:
            if column.link is not None: registry.resolve(column.link)
            if column.source is not None:
                model, field = column.source.split('.')
                if field not in registry.resolve(model).__declared_fields__: raise ValueError('Unknown column source: ' + column.source)
        if report.group_by is not None and report.group_by not in columns: raise ValueError('Report group_by names a declared column')
        chart = report.chart
        if chart is not None and (not isinstance(chart, Chart) or chart.model is not None or chart.x not in columns
                                  or any(columns.get(name) is None or columns[name].type not in numbers for name in chart.y)):
            raise ValueError('A report chart names a declared column and number columns')

    def hidden(self, user, report):
        """Columns whose source field this person cannot read."""
        hidden = set()
        for column in report.columns:
            if column.source is not None:
                name, field = column.source.split('.')
                model = self.business.registry.resolve(name)
                if self.business.registry.api.get(model, 'full') is None:
                    # No caller reads a model with Meta.api = None; the report's own roles decide who sees its columns.
                    continue
                if not self.business.policy.allows(user, model, 'read') or field not in self.business.policy.fields(user, model):
                    hidden.add(column.name)
        return hidden

    def offered(self, user, report):
        """Whether this person may list and run the report: its roles decide, also over a model with Meta.api = None."""
        return user.role in report.roles

    def surfaced(self, model):
        """The storage name of a model callers reach, or None for one with Meta.api = None, which a report never names."""
        registry = self.business.registry
        model = registry.resolve(model)
        return registry.name_for(model) if registry.api.get(model, 'full') is not None else None

    def catalog(self, user):
        result = []
        for name, report in self.reports.items():
            if not self.offered(user, report): continue
            hidden, chart = self.hidden(user, report), report.chart
            if chart is not None and (chart.x in hidden or set(chart.y) <= hidden): chart = None
            descriptor = metadata(report, name)
            for field in descriptor['fields']:
                declared = report.__declared_fields__[field['name']]
                if declared.compute is not None or declared.query is not None: field['calculated'] = True
            result.append({**descriptor, 'autorun': report.autorun,
                           'columns': [self.column(column) for column in report.columns if column.name not in hidden],
                           'group': report.group, 'model': None if report.model is None else self.surfaced(report.model),
                           'sortable': [column for column in report.sortable if column not in hidden], 'export': report.export,
                           'group_by': None if report.group_by in hidden else report.group_by,
                           'chart': None if chart is None else {**chart.public(), 'y': [column for column in chart.y if column not in hidden]}})
        return result

    def column(self, column):
        entry = {key: value for key, value in vars(column).items() if key != 'source'}
        if column.link is not None: entry['link'] = self.surfaced(column.link)
        return entry

    def page(self, context, report, draft, limit, offset, sort):
        """One page of a report as the browser gets it: cleaned cells, readable columns and totals."""
        context.limit, context.offset, context.sort = limit, offset, self.order(report, context.user, sort)
        # A report's run() is trusted app code: it reads models callers cannot reach (Meta.api = None) and
        # returns only its declared columns, to the roles the report names.
        with trusted():
            result = draft.run()
        if not isinstance(result, dict) or not isinstance(result.get('rows'), list) or len(result['rows']) > limit:
            raise ValueError('Reports return a bounded rows/total result from context.list')
        if type(result.get('total')) is not int or result['total'] < len(result['rows']): raise ValueError('Report total must be a nonnegative row count')
        allowed = {column.name for column in report.columns}
        if any(not isinstance(row, dict) for row in result['rows']): raise ValueError('Report rows must match declared columns')
        # Rows from context.list carry the standard name and title; a report shows them only as columns.
        result['rows'] = [{key: value for key, value in row.items() if key in allowed or key not in ('name', 'title')} for row in result['rows']]
        if any(set(row) - allowed for row in result['rows']): raise ValueError('Report rows must match declared columns')
        from .fields import Text, Integer, Decimal, Currency, Date, Boolean, Link
        from decimal import Decimal as Number
        # A link column holds record ids, which the response writes as text ids.
        types = {'text': Text, 'integer': Integer, 'decimal': Decimal, 'currency': Currency, 'date': Date, 'boolean': Boolean, 'link': lambda: Link(None)}
        hidden = self.hidden(context.user, report)
        given = result.get('totals')
        if given is not None and (not isinstance(given, dict) or set(given) - {column.name for column in report.columns if column.total}):
            raise ValueError('Report totals name columns that declare total')
        rows = [{} for _ in result['rows']]
        totals = {}
        for column in report.columns:
            if column.name in hidden: continue
            field = types[column.type]()
            for row, source in zip(rows, result['rows']):
                row[column.name] = field.serialize(field.clean(source.get(column.name)))
            if not column.total: continue
            if given is not None:
                value = field.serialize(field.clean(given.get(column.name)))
                totals[column.name] = None if value is None else str(value)
            else:
                totals[column.name] = str(sum((Number(str(row[column.name])) for row in rows if row[column.name] is not None), Number(0)))
        page = {'rows': rows, 'total': result['total']}
        if totals: page['totals'] = {'scope': 'page' if given is None else 'all', 'values': totals}
        grouping = self.grouping(context.user, report)
        if grouping is not None: page['groups'] = self.groups(report, grouping, hidden, result.get('groups'), rows, types)
        elif result.get('groups') is not None: raise ValueError('Report groups need Report.group_by')
        return page

    def grouping(self, user, report):
        """The group column for this person: None without group_by, or when they cannot read it."""
        return None if report.group_by is None or report.group_by in self.hidden(user, report) else report.group_by

    def order(self, report, user, sort):
        """What run() reads as context.sort: the group column first, then the person's sort."""
        grouping = self.grouping(user, report)
        if grouping is None: return None if sort is None else (sort,)
        return (grouping,) if sort is None or sort.lstrip('-') == grouping else (grouping, sort)

    def groups(self, report, grouping, hidden, given, rows, types):
        """Subtotals of the groups on this page: of every row when run() gives them, else of the page."""
        from decimal import Decimal as Number
        column = next(item for item in report.columns if item.name == grouping)
        key = types[column.type]()
        numbers = [item for item in report.columns if item.total and item.name not in hidden]
        shown = {json.dumps(row[grouping]) for row in rows}
        items = {}
        if given is not None:
            if not isinstance(given, list) or len(given) > 10000: raise ValueError('Report groups are a list of at most 10000 groups')
            for group in given:
                if not isinstance(group, dict) or set(group) - {'value', 'count', 'totals'} or type(group.get('count')) is not int or group['count'] < 0:
                    raise ValueError('Each report group is {value, count, totals}')
                totals = group.get('totals') or {}
                if not isinstance(totals, dict) or set(totals) - {item.name for item in report.columns if item.total}:
                    raise ValueError('Report group totals name columns that declare total')
                value = key.serialize(key.clean(group.get('value')))
                if json.dumps(value) not in shown: continue
                cleaned = {}
                for item in numbers:
                    field = types[item.type]()
                    total = field.serialize(field.clean(totals.get(item.name)))
                    cleaned[item.name] = None if total is None else str(total)
                items[json.dumps(value)] = {'value': value, 'count': group['count'], 'totals': cleaned}
            return {'scope': 'all', 'items': list(items.values())}
        for row in rows:
            entry = items.setdefault(json.dumps(row[grouping]), {'value': row[grouping], 'count': 0, 'totals': {item.name: Number(0) for item in numbers}})
            entry['count'] += 1
            for item in numbers:
                if row[item.name] is not None: entry['totals'][item.name] += Number(str(row[item.name]))
        for entry in items.values(): entry['totals'] = {name: str(value) for name, value in entry['totals'].items()}
        return {'scope': 'page', 'items': list(items.values())}

    def sort(self, report, user, value):
        """The sort a person asked for: a sortable column name, '-' first for descending."""
        if value is None: return None
        name = value[1:] if isinstance(value, str) and value.startswith('-') else value
        if not isinstance(name, str) or name not in report.sortable or name in self.hidden(user, report):
            raise ValidationError({'sort': 'This report cannot be sorted by that column'})
        return value

    def permission(self, context, definition, owner, record_id):
        permission = definition.permission or (owner, 'update')
        model, operation = permission
        model = self.business.registry.resolve(model)
        self.business.policy.require(context.user, model, operation)
        # The source itself always requires the owning operation, even when another capability is declared.
        return self.business.policy.record(context._tx, context.user, owner, record_id, 'update')

    def dispatch(self, scope, body, user):
        path, method = scope['path'], scope['method']
        if path == '/api/_reports':
            if method != 'GET': raise AccessError(405, 'Method not allowed')
            return 200, {'reports': self.catalog(user)}, []
        if path.startswith('/api/_reports/'):
            name = path.removeprefix('/api/_reports/')
            operation = 'run'
            for suffix in ('evaluate', 'export.csv'):
                if name.endswith('/' + suffix): name, operation = name.removesuffix('/' + suffix), suffix
            report = self.reports.get(name)
            if method != 'POST' or report is None or not self.offered(user, report): raise AccessError(403, 'Report is unavailable')
            if operation == 'export.csv': return self.export(report, body, user)
            if set(body) - {'filters', 'revision', 'limit', 'offset', 'sort'}: raise ValidationError({'_form': 'Unknown report argument'})
            limit, offset = body.get('limit', 50), body.get('offset', 0)
            if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 2**53-1:
                raise ValidationError({'page': 'Invalid report page'})
            sort = self.sort(report, user, body.get('sort'))
            with self.business.context(user, readonly=True) as context:
                context.limit, context.offset, context.sort = limit, offset, self.order(report, user, sort)
                draft = form_draft(report, context, body.get('filters', {}))
                errors, states = settle(context, draft, complete=operation == 'run')
                filters = draft_values(draft)
                if operation == 'evaluate':
                    from .slips import rules
                    return 200, {'values': filters, 'errors': errors, 'states': states, 'rules': rules(draft), 'revision': body.get('revision')}, []
                object.__setattr__(draft, '_readonly', True)
                result = self.page(context, report, draft, limit, offset, sort)
                return 200, {**result, 'filters': filters, 'revision': body.get('revision'), 'limit': limit, 'offset': offset, 'sort': sort}, []
        if path.startswith('/api/_dialogs/'):
            parts = path.removeprefix('/api/_dialogs/').split('/')
            if len(parts) != 4 or method != 'POST' or parts[3] not in ('load', 'submit', 'evaluate'):
                raise AccessError(404, 'Dialog operation is unavailable')
            model = self.business.registry.resolve(parts[0])
            record_id = path_id(parts[1])
            if record_id is None: raise AccessError(404, 'Record not found')
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
                with trusted():
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
                with trusted():
                    outcome = draft.submit(source)
                outcome = jsonable(outcome)
                encoded = json.dumps(outcome, allow_nan=False)
                if len(encoded) > 65536: raise ValueError('Dialog result exceeds 64 KiB')
                context._tx.connection.execute('INSERT INTO evlet_operations(key,actor,request,result) VALUES (?,?,?,?)', (key, user.id, signature, encoded))
                return 200, outcome, []
        return None

    def export(self, report, body, user):
        """Every row (up to EXPORT_ROWS) as CSV, run with the same filters, sort and checks as the view."""
        import csv, io
        from urllib.parse import quote
        from .business import Download
        if not report.export: raise AccessError(403, 'This report cannot be exported')
        if set(body) - {'filters', 'sort'}: raise ValidationError({'_form': 'Unknown report argument'})
        sort = self.sort(report, user, body.get('sort'))
        rows, offset, total = [], 0, None
        with self.business.context(user, readonly=True) as context:
            context.limit, context.offset, context.sort = 100, 0, self.order(report, user, sort)
            draft = form_draft(report, context, body.get('filters', {}))
            settle(context, draft, complete=True)
            object.__setattr__(draft, '_readonly', True)
            hidden = self.hidden(user, report)
            while total is None or offset < min(total, EXPORT_ROWS):
                page = self.page(context, report, draft, min(100, EXPORT_ROWS - offset), offset, sort)
                total = page['total']
                if not page['rows']: break
                rows.extend(page['rows'])
                offset += len(page['rows'])
        columns = [column for column in report.columns if column.name not in hidden]
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\r\n')
        # Money stays a plain number; a currency column names its ISO code in the header, never a symbol or image.
        writer.writerow([(column.label or column.name.replace('_', ' ').capitalize())
                         + (f' ({column.unit})' if column.type == 'currency' and column.unit and re.fullmatch('[A-Z]{3}', column.unit) else '')
                         for column in columns])
        for row in rows:
            writer.writerow([csv_cell(row.get(column.name), column.type) for column in columns])
        if (total or 0) > len(rows):
            writer.writerow([f'{total - len(rows)} more rows were left out. Narrow the filters to export them.'])
        filename = quote((report.title or report.__name__) + '.csv', safe='')
        return 200, Download(('\ufeff' + out.getvalue()).encode()), [
            (b'content-type', b'text/csv; charset=utf-8'), (b'content-disposition', f"attachment; filename*=UTF-8''{filename}".encode()),
            (b'content-security-policy', b"default-src 'none'; sandbox")]


EXPORT_ROWS = 5000


def csv_cell(value, kind='text'):
    """A cell as text. Text a spreadsheet would read as a formula starts with an apostrophe; numbers,
    negative ones included, are written as they are."""
    if value is None: return ''
    if value is True or value is False: return 'Yes' if value else 'No'
    text = str(value)
    return "'" + text if kind == 'text' and text[:1] in ('=', '+', '-', '@', '\t', '\r') else text
