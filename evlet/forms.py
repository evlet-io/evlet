"""Reports: bounded, filtered result pages built by trusted Python."""

from dataclasses import dataclass, field as attribute, replace
import json
import re

from .auth import AccessError, allow, has_role
from .models import Model, ValidationError
from .documents import Record, settle, walk
from .permissions import trusted
from .prompts import draft_values, form_draft
from .ui.metadata import field_schema, source_schema
from .reactivity import evaluate, map_filters, plans, serialized


@dataclass(frozen=True)
class Column:
    name: str
    type: str = 'text'
    label: str | None = None
    link: str | None = None
    total: bool = False
    # Text ('AED', 'kg'), or worked out per run from the report's filters: Ref('company.currency') reads a filter
    # (through its Link), a callable gets the report with its filters and returns the text. None leaves the
    # column without a unit for that run, and a total over it is left out.
    unit: object = attribute(default=None, hash=False)
    # 'model.field' (the model's route name): the column is left out for anyone who cannot read that field.
    source: str | None = None
    # Drill down: each cell links to another report, its filters set from this row's columns (or, when no column
    # has the name, from this report's filters): filters={'account': 'account', 'from_date': 'from_date'}.
    report: str | None = None
    filters: object = attribute(default=None, hash=False)

    def __post_init__(self):
        from .models import identifier
        from .declarations import Ref
        identifier(self.name)
        if self.unit is not None and not (isinstance(self.unit, str) and self.unit or isinstance(self.unit, Ref) or callable(self.unit)):
            raise ValueError("A column unit is text, Ref('filter.field') or a callable that returns the text")
        if self.report is not None and (not isinstance(self.report, str) or not self.report):
            raise ValueError('A column report names a registered report')
        if self.filters is not None:
            if self.report is None:
                raise ValueError('Column filters go with report=: they fill the linked report\'s filters')
            if not isinstance(self.filters, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in self.filters.items()):
                raise ValueError("Column filters map the linked report's filter to this report's column or filter: {'account': 'account'}")
            object.__setattr__(self, 'filters', dict(self.filters))
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
    # True: run() also gets self.context.session, a read-only transaction session that reads any model without the
    # person's role, row and field rules (a ledger they cannot open). The report's roles still decide who runs it.
    trusted = False
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


def run_report(context, report, draft):
    """Call a report's run(). It is trusted app code: it reads models callers cannot reach (Meta.api = None) and
    returns only its declared columns, to the roles the report names. With Report.trusted, self.context.session
    is open read only for as long as run() runs (evlet.database.ReadSession, as derive() gets it)."""
    with trusted():
        if not report.trusted:
            return draft.run()
        from .documents import hook_context
        with hook_context(context, draft):
            inner = draft.context
            inner.limit, inner.offset, inner.sort = context.limit, context.offset, getattr(context, 'sort', None)
            return draft.run()


def dynamic_unit(column):
    """Whether a column's unit is worked out per run (a Ref or a callable) rather than fixed text."""
    return column.unit is not None and not isinstance(column.unit, str)


def unit_path(registry, report, path):
    """Check Ref('filter.field...') on a report: a filter, then a field of each Link's target. Returns the fields."""
    from .fields import Link
    parts, owner, found = path.split('.'), report, []
    for index, part in enumerate(parts):
        field = owner.__declared_fields__.get(part)
        if field is None:
            raise ValueError(f'{report.__name__}: a column unit reads {path!r}, but {owner.__name__} has no field {part}')
        found.append(field)
        if index < len(parts) - 1:
            if not isinstance(field, Link):
                raise ValueError(f'{report.__name__}: a column unit reads {path!r} through {part}, which is not a Link')
            owner = registry.resolve(field.target)
    return found


def resolve_units(context, report, draft):
    """{column: unit or None} of the columns whose unit is a Ref or a callable, for the filters this run has.
    A Ref reads the linked record with the person's permissions; a record they cannot read gives None."""
    units = {}
    for column in report.columns:
        if not dynamic_unit(column): continue
        if callable(column.unit):
            with trusted():
                value = column.unit(draft)
        else:
            value = ref_unit(context, report, draft, column.unit.path)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 20):
            raise ValueError(f'The unit of report column {column.name} is text of at most 20 characters, or None')
        units[column.name] = value or None
    return units


def ref_unit(context, report, draft, path):
    from .database import NotFoundError
    registry = context._business.registry
    fields, parts = unit_path(registry, report, path), path.split('.')
    value = draft._values.get(parts[0])
    for field, part in zip(fields, parts[1:]):
        if value is None: return None
        try:
            value = context.get(registry.resolve(field.target), value).get(part)
        except (AccessError, NotFoundError):
            return None
    return None if value is None else str(value)


def resolved_columns(report, units):
    """The report's columns with this run's units in place of Ref and callable units."""
    return tuple(replace(column, unit=units.get(column.name)) if dynamic_unit(column) else column for column in report.columns)


def metadata(definition, name, source=None):
    source = getattr(definition, '__source__', source)
    fields = plans(definition)
    # A child row may declare a field called title; only a text title labels the form.
    title = getattr(definition, 'title', '')
    result = {'name': name, 'label': title if isinstance(title, str) and title else definition.__name__,
              'fields': [field_schema(field) for field in definition.__declared_fields__.values()],
              'behavior': {key: {kind: map_filters(entry, serialized) if kind == 'filters' else serialized(entry)
                                for kind, entry in value.items()} for key, value in fields.items()},
              'children': [], 'sources': source_schema(definition),
              'source_fields': [] if source is None else [field_schema(field) for field in source.__declared_fields__.values()]}
    for table_name, table in definition.__tables__.items():
        result['children'].append({'name': table_name, 'label': table.label or table_name.title(),
                                   'definition': metadata(table.child, name + '.' + table_name, source)})
    return result


def rules(draft):
    """Visible, required and read-only states of every filter with a string condition, by path."""
    result = {}
    for prefix, item in walk(draft):
        entries = plans(item._definition)
        for name in item._definition.__declared_fields__:
            entry = entries.get(name, {})
            state = {}
            for key, label in (('visible_if', 'visible'), ('required_if', 'required'), ('read_only_if', 'read_only')):
                if key in entry:
                    try: state[label] = bool(evaluate(entry[key], item.read))
                    except (ValueError, TypeError, ArithmeticError): state[label] = key == 'visible_if'
            if state:
                result[prefix + name] = state
    return result


class Forms:
    def __init__(self, business, reports=()):
        self.business = business
        self.reports = {}
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
        for report in self.reports.values():
            self.check_drill(report)
        from .models import resolve_sources
        from .reactivity import graph
        for definition in self.reports.values():
            resolve_sources(business.registry, definition)
            graph(business.registry, definition)

    def check(self, report):
        registry, columns = self.business.registry, {column.name: column for column in report.columns}
        numbers = ('integer', 'decimal', 'currency')
        if not isinstance(report.group, str): raise ValueError('Report group is text')
        if type(report.export) is not bool: raise ValueError('Report export is True or False')
        if type(report.trusted) is not bool: raise ValueError('Report trusted is True or False')
        if report.model is not None: registry.resolve(report.model)
        if not isinstance(report.sortable, (tuple, list)) or set(report.sortable) - set(columns):
            raise ValueError('Report sortable names declared columns')
        for column in report.columns:
            if column.link is not None: registry.resolve(column.link)
            if dynamic_unit(column) and not callable(column.unit): unit_path(registry, report, column.unit.path)
            if column.source is not None:
                model, field = column.source.split('.')
                if field not in registry.resolve(model).__declared_fields__: raise ValueError('Unknown column source: ' + column.source)
        if report.group_by is not None and report.group_by not in columns: raise ValueError('Report group_by names a declared column')
        chart = report.chart
        if chart is not None and (not isinstance(chart, Chart) or chart.model is not None or chart.x not in columns
                                  or any(columns.get(name) is None or columns[name].type not in numbers for name in chart.y)):
            raise ValueError('A report chart names a declared column and number columns')

    def check_drill(self, report):
        """A column's report= names a registered report, and its filters name that report's filters and this
        report's columns or filters."""
        names = {column.name for column in report.columns} | set(report.__declared_fields__)
        for column in report.columns:
            if column.report is None: continue
            target = self.reports.get(column.report)
            if target is None: raise ValueError(f'Column {column.name} links to an unknown report: {column.report}')
            for key, value in (column.filters or {}).items():
                if key not in target.__declared_fields__: raise ValueError(f'{column.report} has no filter {key}')
                if value not in names: raise ValueError(f'Column {column.name} fills {key} from {value}, which is neither a column nor a filter')

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
        return has_role(user, report.roles)

    def surfaced(self, model):
        """The storage name of a model callers reach, or None for one with Meta.api = None, which a report never names."""
        registry = self.business.registry
        model = registry.resolve(model)
        return registry.name_for(model) if registry.api.get(model, 'full') is not None else None

    def source_kind(self, report):
        """What the report's `model` is: "ledger" for a ledger model (which the catalog never names), "model"
        for any other model, None without one. The browser groups ledger reports by it."""
        if report.model is None:
            return None
        return 'ledger' if getattr(self.business.registry.resolve(report.model), '__ledger__', False) else 'model'

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
                           'columns': [self.column(column, user) for column in report.columns if column.name not in hidden],
                           'group': report.group, 'model': None if report.model is None else self.surfaced(report.model),
                           'source_kind': self.source_kind(report),
                           'sortable': [column for column in report.sortable if column not in hidden], 'export': report.export,
                           'group_by': None if report.group_by in hidden else report.group_by,
                           'chart': None if chart is None else {**chart.public(), 'y': [column for column in chart.y if column not in hidden]}})
        return result

    def column(self, column, user=None):
        entry = {key: value for key, value in vars(column).items() if key not in ('source', 'report', 'filters')}
        if column.link is not None: entry['link'] = self.surfaced(column.link)
        if dynamic_unit(column):
            # Worked out per run: the page's `units` gives it.
            entry['unit'], entry['unit_from_filters'] = None, True
        if column.report is not None:
            target = self.reports[column.report]
            # A drill-down link only to a report this person may run.
            if user is None or self.offered(user, target):
                entry['report'], entry['filters'] = column.report, dict(column.filters or {})
        return entry

    def page(self, context, report, draft, limit, offset, sort):
        """One page of a report as the browser gets it: cleaned cells, readable columns and totals."""
        context.limit, context.offset, context.sort = limit, offset, self.order(report, context.user, sort)
        result = run_report(context, report, draft)
        if not isinstance(result, dict) or not isinstance(result.get('rows'), list) or len(result['rows']) > limit:
            raise ValueError('Reports return a bounded rows/total result from context.list')
        if type(result.get('total')) is not int or result['total'] < len(result['rows']): raise ValueError('Report total must be a nonnegative row count')
        allowed = {column.name for column in report.columns}
        # Records from context.list give the values their detail carries.
        result['rows'] = [row._detail if isinstance(row, Record) else row for row in result['rows']]
        if any(not isinstance(row, dict) for row in result['rows']): raise ValueError('Report rows must match declared columns')
        # Rows from context.list carry the standard name and title; a report shows them only as columns.
        result['rows'] = [{key: value for key, value in row.items() if key in allowed or key not in ('name', 'title')} for row in result['rows']]
        if any(set(row) - allowed for row in result['rows']): raise ValueError('Report rows must match declared columns')
        from .fields import Text, Integer, Decimal, Currency, Date, Boolean, Link
        from decimal import Decimal as Number
        # A link column holds record ids, which the response writes as text ids.
        types = {'text': Text, 'integer': Integer, 'decimal': Decimal, 'currency': Currency, 'date': Date, 'boolean': Boolean, 'link': lambda: Link(None)}
        hidden = self.hidden(context.user, report)
        units = resolve_units(context, report, draft)
        # A total over a column whose unit this run could not work out would add up unlike amounts: it is left out.
        unitless = {name for name, unit in units.items() if unit is None}
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
            if not column.total or column.name in unitless: continue
            if given is not None:
                value = field.serialize(field.clean(given.get(column.name)))
                totals[column.name] = None if value is None else str(value)
            else:
                totals[column.name] = str(sum((Number(str(row[column.name])) for row in rows if row[column.name] is not None), Number(0)))
        page = {'rows': rows, 'total': result['total']}
        if totals: page['totals'] = {'scope': 'page' if given is None else 'all', 'values': totals}
        if units: page['units'] = {name: unit for name, unit in units.items() if name not in hidden}
        grouping = self.grouping(context.user, report)
        if grouping is not None: page['groups'] = self.groups(report, grouping, hidden | unitless, result.get('groups'), rows, types)
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

    def dispatch(self, scope, body, user):
        path, method = scope['path'], scope['method']
        if path == '/api/_reports':
            allow(method, 'GET')
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
                    return 200, {'values': filters, 'errors': errors, 'states': states, 'rules': rules(draft), 'revision': body.get('revision')}, []
                object.__setattr__(draft, '_readonly', True)
                result = self.page(context, report, draft, limit, offset, sort)
                return 200, {**result, 'filters': filters, 'revision': body.get('revision'), 'limit': limit, 'offset': offset, 'sort': sort}, []
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
            hidden, units = self.hidden(user, report), resolve_units(context, report, draft)
            while total is None or offset < min(total, EXPORT_ROWS):
                page = self.page(context, report, draft, min(100, EXPORT_ROWS - offset), offset, sort)
                total = page['total']
                if not page['rows']: break
                rows.extend(page['rows'])
                offset += len(page['rows'])
        columns = [column for column in resolved_columns(report, units) if column.name not in hidden]
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\r\n')
        # Money stays a plain number; a currency column names its ISO code in the header, never a symbol or image.
        writer.writerow([(column.label or column.name.replace('_', ' ').capitalize())
                         + (f' ({column.unit})' if column.type == 'currency' and column.unit and re.fullmatch('[A-Z]{3}', column.unit) else '')
                         for column in columns])
        for row in rows:
            writer.writerow([csv_cell(money_cell(row.get(column.name), column), column.type) for column in columns])
        if (total or 0) > len(rows):
            writer.writerow([f'{total - len(rows)} more rows were left out. Narrow the filters to export them.'])
        filename = quote((report.title or report.__name__) + '.csv', safe='')
        return 200, Download(('\ufeff' + out.getvalue()).encode()), [
            (b'content-type', b'text/csv; charset=utf-8'), (b'content-disposition', f"attachment; filename*=UTF-8''{filename}".encode()),
            (b'content-security-policy', b"default-src 'none'; sandbox")]


EXPORT_ROWS = 5000


def money_cell(value, column):
    """A currency column's amount with exactly its currency's places (KWD 1.500, JPY 100), when the column names one."""
    if column.type != 'currency' or not column.unit or not re.fullmatch('[A-Z]{3}', column.unit) or value in (None, ''):
        return value
    from decimal import Decimal, InvalidOperation
    from .currencies import round_money
    try:
        return format(round_money(value if isinstance(value, Decimal) else str(value), column.unit), 'f')
    except (InvalidOperation, ValueError):
        return value


def csv_cell(value, kind='text'):
    """A cell as text. Text a spreadsheet would read as a formula starts with an apostrophe; numbers,
    negative ones included, are written as they are."""
    if value is None: return ''
    if value is True or value is False: return 'Yes' if value else 'No'
    text = str(value)
    return "'" + text if kind == 'text' and text[:1] in ('=', '+', '-', '@', '\t', '\r') else text
