"""Approval workflows: what an app declares a workflow may read, protect and run.

    class PurchaseOrder(Model):
        confirmable = True

        class Meta:
            workflow = WorkflowOptions(
                protects=("confirm",),
                unmatched="block",
                facts={"lines.count": Fact.count("lines", label="Number of lines"),
                       "supplier_risk": Fact(Select(("low", "high")), "Supplier risk", read="supplier_risk")},
                people=PEOPLE,
            )

Nothing is configurable until a model declares `Meta.workflow`. Registration compiles each
declaration into a catalogue of typed facts that workflow conditions read, the person facts that
can be assignees, and the operations a workflow may protect or run. The definitions themselves
are documents an administrator publishes (evlet/workflow_definition.py); the engine that stores and
evaluates them is evlet/workflow_engine.py. See docs/workflows.md.
"""

from types import MappingProxyType

from .fields import Field, Link
from .permissions import Hierarchy, team_target

# Operations a confirmable model may protect or run.
LIFECYCLE = ('confirm', 'cancel', 'reopen')
UNMATCHED = ('block', 'allow')
# Field kinds a condition may read, and the fact type each becomes.
FACT_TYPES = {'text': 'text', 'select': 'enum', 'state': 'enum', 'boolean': 'boolean', 'integer': 'number',
              'decimal': 'number', 'percent': 'number', 'currency': 'money', 'date': 'date', 'link': 'link'}
AGGREGATES = ('count', 'sum', 'min', 'max', 'any')
# Aggregates of these types only: a sum of dates means nothing.
AGGREGATE_TYPES = {'sum': ('number', 'money'), 'min': ('number', 'money', 'date'), 'max': ('number', 'money', 'date')}


def label_of(name, field=None):
    label = getattr(field, 'label', None)
    return label or name.replace('_', ' ').capitalize()


class Fact:
    """A value workflow conditions may read besides the model's own stored fields.

    `Fact(field, label, read="method")` is a read-only lookup: `field` gives its type and choices and
    `read` names a model method taking only `self`, which runs on the saved record when a stage starts.
    `Fact.count(table)`, `Fact.sum(table, field)`, `Fact.min(...)`, `Fact.max(...)` and
    `Fact.any(table, where)` are aggregates over the record's own rows.
    """

    def __init__(self, field, label, *, read):
        if not isinstance(field, Field) or field.kind not in FACT_TYPES or field.kind == 'link' or field.compute is not None:
            raise ValueError('Fact(field, ...) takes a plain Text, Select, Boolean, Integer, Decimal, Currency or Date field')
        if not isinstance(label, str) or not label.strip():
            raise ValueError('Fact needs a label')
        if not isinstance(read, str) or not read.isidentifier():
            raise ValueError('Fact(read=...) names a model method')
        self.field, self.label, self.read = field, label, read
        self.aggregate = self.table = self.column = self.where = None

    @classmethod
    def _rows(cls, aggregate, table, column=None, where=None, label=None):
        fact = cls.__new__(cls)
        if not isinstance(table, str) or not table.isidentifier():
            raise ValueError(f'Fact.{aggregate}() names a Table of the model')
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ValueError('Fact labels are nonempty text')
        fact.field = fact.read = None
        fact.aggregate, fact.table, fact.column, fact.where, fact.label = aggregate, table, column, where, label
        return fact

    @classmethod
    def count(cls, table, *, label=None):
        return cls._rows('count', table, label=label)

    @classmethod
    def sum(cls, table, field, *, label=None):
        return cls._rows('sum', table, field, label=label)

    @classmethod
    def min(cls, table, field, *, label=None):
        return cls._rows('min', table, field, label=label)

    @classmethod
    def max(cls, table, field, *, label=None):
        return cls._rows('max', table, field, label=label)

    @classmethod
    def any(cls, table, where, *, label=None):
        """True when at least one row of the table meets `where`, a finite condition over the row's fields."""
        if where is None:
            raise ValueError('Fact.any() needs a condition over the rows')
        return cls._rows('any', table, where=where, label=label)


class WorkflowOptions:
    """Meta.workflow: the operations a workflow may protect and run, and the facts it may read."""

    def __init__(self, protects, *, executes=(), unmatched=None, facts=None, people=None, assignable=(),
                 fields=None, harmless=(), tables=None):
        self.protects, self.executes, self.unmatched = protects, executes, unmatched
        self.facts, self.people, self.assignable = facts, people, assignable
        self.fields, self.harmless, self.tables = fields, harmless, tables


class FactSpec:
    """One entry of a model's fact catalogue."""
    __slots__ = ('name', 'label', 'type', 'source', 'choices', 'target', 'field', 'fact', 'child')

    def __init__(self, name, label, type, source, *, choices=None, target=None, field=None, fact=None, child=None):
        self.name, self.label, self.type, self.source = name, label, type, source
        self.choices, self.target, self.field, self.fact, self.child = choices, target, field, fact, child

    def public(self, registry):
        result = {'label': self.label, 'type': self.type, 'source': self.source}
        if self.choices is not None:
            result['choices'] = [[choice, choice.replace('_', ' ').capitalize()] for choice in self.choices]
        if self.target is not None:
            result['target'] = registry.name_for(self.target)
        return result


class Compiled:
    """A model's Meta.workflow after registration checks."""

    def __init__(self, model, protects, executes, unmatched, facts, people, assignable, fields, harmless, tables):
        self.model, self.protects, self.executes, self.unmatched = model, protects, executes, unmatched
        self.facts, self.people, self.assignable = facts, people, assignable
        self.fields, self.harmless, self.tables = fields, harmless, tables

    @property
    def person_facts(self):
        return ('requester.manager',) if self.people is not None else ()


def protect_key(value):
    """The stored text of a protected or executed operation: confirm, move:<state> or action:<name>."""
    if value is None:
        return ''
    if not isinstance(value, dict):
        return None
    kind = value.get('kind')
    if kind == 'lifecycle' and set(value) == {'kind', 'operation'} and value['operation'] in LIFECYCLE:
        return value['operation']
    if kind == 'move' and set(value) == {'kind', 'to'} and isinstance(value['to'], str):
        return 'move:' + value['to']
    if kind == 'action' and set(value) == {'kind', 'name'} and isinstance(value['name'], str):
        return 'action:' + value['name']
    return None


def protect_object(key):
    if not key:
        return None
    if key in LIFECYCLE:
        return {'kind': 'lifecycle', 'operation': key}
    kind, _, name = key.partition(':')
    return {'kind': 'move', 'to': name} if kind == 'move' else {'kind': 'action', 'name': name}


def operations(model, values, argument):
    """Check a protects or executes tuple against what the model has; the operations' stored keys."""
    where = f'{model.__name__}: WorkflowOptions({argument}=...)'
    if isinstance(values, str) or not isinstance(values, (tuple, list)):
        raise ValueError(f'{where} is a tuple of operation names')
    result = []
    state = getattr(model, '__state__', None)
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f'{where} names operations as text')
        kind, _, name = value.partition(':')
        if value in LIFECYCLE:
            if not getattr(model, 'confirmable', False):
                raise ValueError(f'{where}: {value} needs a confirmable model')
        elif kind == 'move' and name:
            choices = model.__declared_fields__[state].choices if state is not None else ()
            if name not in choices or name == 'void':
                raise ValueError(f'{where}: move:{name} is not a state the model moves to')
        elif kind == 'action' and name:
            if not hasattr(getattr(model, name, None), '__action__'):
                raise ValueError(f'{where}: action:{name} is not a model action declared with @action')
        else:
            raise ValueError(f'{where}: {value!r} is not confirm, cancel, reopen, move:<state> or action:<name>')
        if value in result:
            raise ValueError(f'{where} names {value} twice')
        result.append(value)
    return tuple(result)


def names(model, values, argument, allowed):
    where = f'{model.__name__}: WorkflowOptions({argument}=...)'
    if isinstance(values, str) or not isinstance(values, (tuple, list)) or any(not isinstance(value, str) for value in values):
        raise ValueError(f'{where} is a tuple of names')
    unknown = [value for value in values if value not in allowed]
    if unknown:
        raise ValueError(f'{where} names {", ".join(unknown)}, which the model does not have')
    return tuple(dict.fromkeys(values))


def compile_workflows(registry, models, child_models):
    """{model: Compiled} for every model that declares Meta.workflow, after the registration checks."""
    result = {}
    for model in models:
        meta = getattr(model, 'Meta', None)
        options = vars(meta).get('workflow') if meta is not None else None
        if options is None:
            continue
        if not isinstance(options, WorkflowOptions):
            raise ValueError(f'{model.__name__}: Meta.workflow is WorkflowOptions(...)')
        if model in child_models:
            raise ValueError(f'{model.__name__}: Meta.workflow is for root documents, not Table rows')
        result[model] = compile_options(registry, model, options)
    return MappingProxyType(result)


def compile_options(registry, model, options):
    protects = operations(model, options.protects, 'protects')
    executes = operations(model, options.executes, 'executes')
    if options.unmatched not in UNMATCHED:
        raise ValueError(f'{model.__name__}: WorkflowOptions(unmatched=...) is required: "block" or "allow"')
    tables = {child.name: child for child in registry.children if child.parent is model}
    facts = {}
    for name, field in model.__declared_fields__.items():
        kind = FACT_TYPES.get(field.kind)
        if kind is None or not field.stored or field.query is not None or field.tracking != 'value' \
                or name in getattr(model, '__internal_fields__', ()) or getattr(field, 'instance', None):
            continue
        facts[name] = FactSpec(name, label_of(name, field), kind, 'field', choices=getattr(field, 'choices', None),
                               target=field.target if isinstance(field, Link) else None, field=field)
    people = target = None
    if options.people is not None:
        if not isinstance(options.people, Hierarchy):
            raise ValueError(f'{model.__name__}: WorkflowOptions(people=...) is a Hierarchy')
        target = team_target(registry, registry.resolve(options.people.model), (options.people, 'id'))
        people = options.people
        facts['requester'] = FactSpec('requester', 'Requester', 'user', 'requester')
        facts['requester.role'] = FactSpec('requester.role', "Requester's role", 'enum', 'requester')
        department = target.__declared_fields__.get('department')
        if department is not None and FACT_TYPES.get(department.kind) and department.stored:
            facts['requester.department'] = FactSpec('requester.department', "Requester's department", FACT_TYPES[department.kind],
                                                     'requester', choices=getattr(department, 'choices', None),
                                                     target=department.target if isinstance(department, Link) else None,
                                                     field=department)
    else:
        facts['requester'] = FactSpec('requester', 'Requester', 'user', 'requester')
        facts['requester.role'] = FactSpec('requester.role', "Requester's role", 'enum', 'requester')
    declared = options.facts or {}
    if not isinstance(declared, dict):
        raise ValueError(f'{model.__name__}: WorkflowOptions(facts=...) maps names to Fact(...)')
    for key, fact in declared.items():
        where = f'{model.__name__}: WorkflowOptions(facts={key!r})'
        if not isinstance(fact, Fact):
            raise ValueError(f'{where} is a Fact(...)')
        if fact.aggregate is None:
            if not isinstance(key, str) or not key.isidentifier():
                raise ValueError(f'{where}: a lookup fact is named like a field')
            if key in model.__declared_fields__ or key in model.__tables__:
                raise ValueError(f'{where} collides with a field of the model')
            if not callable(getattr(model, fact.read, None)) or fact.read in model.__declared_fields__:
                raise ValueError(f'{where}: read={fact.read!r} is not a method of the model')
            facts['fact.' + key] = FactSpec('fact.' + key, fact.label, FACT_TYPES[fact.field.kind], 'lookup',
                                            choices=getattr(fact.field, 'choices', None), field=fact.field, fact=fact)
            continue
        child = tables.get(fact.table)
        if child is None:
            raise ValueError(f'{where}: {fact.table} is not a Table of the model')
        if not isinstance(key, str) or not key.startswith(fact.table + '.') or not key[len(fact.table) + 1:].isidentifier():
            raise ValueError(f'{where}: name an aggregate {fact.table}.<name>, such as {fact.table}.count')
        if fact.aggregate in AGGREGATE_TYPES:
            column = child.model.__declared_fields__.get(fact.column)
            kind = FACT_TYPES.get(getattr(column, 'kind', None))
            if column is None or not column.stored or kind not in AGGREGATE_TYPES[fact.aggregate]:
                raise ValueError(f'{where}: {fact.column!r} is not a stored number, money{" or date" if fact.aggregate != "sum" else ""} '
                                 f'field of {fact.table}')
        elif fact.aggregate == 'any':
            from .measures import compile_where
            compile_where(registry, child.model, fact.where)
            kind = 'boolean'
        else:
            kind = 'number'
        label = fact.label or (f'Number of {child.label.lower()}' if fact.aggregate == 'count' else key)
        facts[key] = FactSpec(key, label, kind, 'rows', fact=fact, child=child,
                              field=child.model.__declared_fields__.get(fact.column) if fact.column else None)
    assignable = {}
    for name in names(model, options.assignable, 'assignable', model.__declared_fields__):
        field = model.__declared_fields__[name]
        if not isinstance(field, Link) or target is None or registry.resolve(field.target) is not target:
            raise ValueError(f'{model.__name__}: WorkflowOptions(assignable={name!r}) must be a Link to the people hierarchy model')
        assignable[name] = field
    writable = [name for name, field in model.__declared_fields__.items()
                if field.stored and not field.read_only and field.compute is None and name not in getattr(model, '__internal_fields__', ())]
    fields = writable if options.fields is None else names(model, options.fields, 'fields', writable)
    harmless = names(model, options.harmless, 'harmless', model.__declared_fields__)
    read_by_results = set()
    if registry.plan_for(model) is not None:
        for name, field in model.__declared_fields__.items():
            if field.stored and field.compute is not None:
                read_by_results |= {read for scope, owner, read in registry.finite_reads(model, name) if scope == 'self'}
    for name in harmless:
        if name in read_by_results:
            raise ValueError(f'{model.__name__}: WorkflowOptions(harmless={name!r}) is read by a stored result, so its changes matter')
    all_tables = table_paths(registry, model)
    tables_allowed = all_tables if options.tables is None else names(model, options.tables, 'tables', all_tables)
    return Compiled(model, protects, executes, options.unmatched, MappingProxyType(facts), people, MappingProxyType(assignable),
                    tuple(fields), harmless, tuple(tables_allowed))


def table_paths(registry, model, prefix=''):
    paths = []
    for child in registry.children:
        if child.parent is model:
            paths.append(prefix + child.name)
            paths += table_paths(registry, child.model, prefix + child.name + '.')
    return paths


def catalogue(business, model):
    """The catalogue an editor reads for one model (contract section 2.11)."""
    registry = business.registry
    compiled = registry.workflows[model]
    labels = {'confirm': 'Confirm', 'cancel': 'Cancel', 'reopen': 'Reopen'}
    if getattr(model, 'confirmable', False):
        from .lifecycle import labels as lifecycle_labels
        labels = lifecycle_labels(model)
    def entry(key):
        found = protect_object(key)
        kind, _, name = key.partition(':')
        label = labels.get(key) if key in LIFECYCLE else name.replace('_', ' ').capitalize()
        return {**found, 'label': label}
    roles = []
    for role in business.policy.roles:
        roles.append({'role': role, 'label': role.replace('_', ' ').capitalize(),
                      'review': role in business.policy.reviewers.get(model, ())})
    return {'model': registry.name_for(model), 'label': getattr(model.Meta, 'label', None) or model.__name__,
            'protects': [entry(key) for key in compiled.protects], 'executes': [entry(key) for key in compiled.executes],
            'unmatched': compiled.unmatched,
            'facts': {name: spec.public(registry) for name, spec in compiled.facts.items()},
            'people': [{'fact': 'requester.manager', 'label': "Requester's manager"}] if compiled.people is not None else [],
            'fields': [{'name': name, 'label': label_of(name, model.__declared_fields__[name])} for name in compiled.fields],
            'tables': [{'name': name, 'label': name.replace('_', ' ').replace('.', ' / ').capitalize()} for name in compiled.tables],
            'harmless': list(compiled.harmless), 'roles': roles}
