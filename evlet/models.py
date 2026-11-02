"""Explicit application registries and validated record snapshots."""

import re
from types import MappingProxyType

from .fields import Field, Link, MISSING, RULES, State
from .declarations import Table, ReactiveQuery, DEFAULT_TABLE_ROWS


def identifier(name):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or name.lower().startswith("sqlite_"):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return '"' + name + '"'


def storage_name(model):
    # A field may itself be called storage_name, so only a str class attribute names the table.
    for cls in model.__mro__:
        value = vars(cls).get('storage_name')
        if isinstance(value, str):
            return value
    return model.__name__.lower()


class ValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(f"{key}: {value}" for key, value in errors.items()))


class ModelType(type):
    def __new__(mcls, name, bases, namespace):
        fields = {}
        tables = {}
        sources = {}
        for base in bases:
            fields.update(getattr(base, "__declared_fields__", getattr(base, '__fields__', {})))
            tables.update(getattr(base, '__tables__', {}))
            sources.update(getattr(base, '__sources__', {}))
        for key, value in namespace.items():
            if isinstance(value, Field):
                identifier(key)
                if "__" in key or key.lower() == "id" or key == "to_dict" or any(
                    hasattr(base, key) and key not in getattr(base, "__fields__", {})
                    for base in bases
                ):
                    raise ValueError(f"Reserved field name: {key}")
                fields[key] = value
            elif isinstance(value, ReactiveQuery):
                identifier(key)
                if key in fields or key in tables or key in ('children', 'id', 'context', 'parent', 'source'):
                    raise ValueError(f'Reserved source name: {key}')
                sources[key] = value
            elif isinstance(value, Table):
                identifier(key)
                if key in fields or key in ('children', 'id', 'context'):
                    raise ValueError(f'Reserved table name: {key}')
                tables[key] = value
            elif key in fields:
                raise ValueError(f"Inherited field {key!r} must remain a field")
        if len({key.lower() for key in fields}) != len(fields):
            raise ValueError("Field names must be distinct ignoring case")
        columns = [column.lower() for key, value in fields.items() if value.stored for column, _ in value.columns(key)]
        if len(set(columns)) != len(columns) or 'id' in columns:
            raise ValueError("Reference columns (<name>_model, <name>_id) must not collide with other fields")
        cls = super().__new__(mcls, name, bases, namespace)
        cls.__declared_fields__ = MappingProxyType(fields)
        cls.__fields__ = MappingProxyType({key: value for key, value in fields.items() if value.stored})
        cls.__tables__ = MappingProxyType(tables)
        cls.__sources__ = MappingProxyType(sources)
        states = [key for key, value in fields.items() if isinstance(value, State)]
        if len(states) > 1:
            raise ValueError(f"{name} declares more than one State: " + ", ".join(states))
        # The State field's name; its value changes only through moves, so the model is a document.
        cls.__state__ = states[0] if states else None
        if states or sources or tables or 'permissions' in namespace or any(hasattr(value, '__action__') for value in namespace.values()) or any(field.compute is not None or not field.stored or getattr(field, 'initial', None) is not None
                or any(getattr(field, key, None) is not None for key in RULES) for field in fields.values()):
            cls.__declaration_mode__ = True
        if getattr(cls, '__declaration_mode__', False) and (set(fields) | set(tables) | set(sources)) & {'context', 'parent', 'source', 'key', 'read', 'values', 'stored_values'}:
            raise ValueError('A document field conflicts with its draft interface')
        return cls


class Model(metaclass=ModelType):
    class Meta:
        pass

    def __init__(self, /, **values):
        errors = {key: "Unknown field" for key in values if key not in self.__fields__}
        cleaned = {}
        for name, field in self.__fields__.items():
            try:
                cleaned[name] = field.clean(values.get(name, MISSING))
            except ValueError as exc:
                errors[name] = str(exc)
        if errors:
            raise ValidationError(errors)
        self._values = MappingProxyType(cleaned)
        self._id = None
        self.validate()

    def validate(self):
        """Override for cross-field rules; raise ValidationError with field errors."""

    @property
    def id(self):
        return self._id

    def to_dict(self):
        return {"id": self.id, **{
            name: field.serialize(self._values[name])
            for name, field in self.__fields__.items()
        }}

    @classmethod
    def _from_row(cls, row):
        # Stored snapshots must remain readable after business rules change.
        instance = cls.__new__(cls)
        instance._values = MappingProxyType({
            name: field.from_columns(row, name) for name, field in cls.__fields__.items()
        })
        instance._id = row["id"]
        return instance


class Registry:
    def __init__(self, *models):
        from .fields import Integer
        from .business import ChildTable
        from .calc import CalcError, LIMITS, uses_finite, root_language
        self.children = []
        expanded = list(models)

        def finite(model, seen=()):
            # Finite-v1 roots own nested tables; legacy roots keep one level.
            if model in seen:
                raise CalcError('MODEL_CONTAINMENT_CYCLE', ' -> '.join(item.__name__ for item in seen + (model,)))
            return any(uses_finite(field) for field in model.__declared_fields__.values()) or any(
                isinstance(table.child, type) and issubclass(table.child, Child) and finite(table.child, seen + (model,))
                for table in model.__tables__.values())

        def expand(model, owners, nested):
            for name, table in model.__tables__.items():
                if not isinstance(table.child, type) or not issubclass(table.child, Child):
                    raise ValueError('Tables require Child subclasses')
                if table.child in owners:
                    raise CalcError('MODEL_CONTAINMENT_CYCLE', ' -> '.join(item.__name__ for item in owners + (table.child,)))
                if table.child.__tables__ and not nested:
                    raise ValueError('Nested tables require a finite-v1 root (Meta.calculation_language = "finite-v1")')
                if table.max_rows > DEFAULT_TABLE_ROWS and not nested:
                    raise ValueError(f'{model.__name__}.{name}: max_rows above {DEFAULT_TABLE_ROWS} requires a finite-v1 root '
                                     '(Meta.calculation_language = "finite-v1")')
                if len(owners) > LIMITS['table_depth']:
                    raise CalcError('CALCULATION_LIMIT_EXCEEDED', f'Owned tables nest deeper than {LIMITS["table_depth"]}')
                child = generate(model, name, table)
                expand(child, owners + (table.child,), nested)

        def generate(model, name, table):
            storage = table.storage_name or storage_name(model) + '_' + name
            identifier(storage)
            identifier(table.owner_field)
            if table.owner_field in table.child.__declared_fields__ or 'evlet_position' in table.child.__declared_fields__:
                raise ValueError('Child owner and order fields are framework-managed')
            child = type(table.child.__name__, (table.child,), {
                '__module__': model.__module__, 'storage_name': storage,
                '__resource_id__': getattr(model, '__resource_id__', model.__name__) + '.' + name,
                table.owner_field: Link(model, required=True, index=True, hidden=True, read_only=True),
                'evlet_position': Integer(required=True, default=0, hidden=True, read_only=True),
                '__internal_fields__': (table.owner_field, 'evlet_position'),
            })
            expanded.append(child)
            self.children.append(ChildTable(model, name, child, table.owner_field, table.label or name.replace('_', ' ').title()))
            return child
        for model in models:
            if isinstance(model, type) and issubclass(model, Model) and getattr(model, '__tables__', None):
                expand(model, (model,), finite(model) or root_language(model) is not None)
        models = tuple(expanded)
        registered = {}
        resources = {}
        for model in models:
            if not isinstance(model, type) or not issubclass(model, Model) or model is Model:
                raise ValueError("Register Model subclasses")
            if getattr(model, '__transient__', False): raise ValueError('Transient forms cannot be persistent models')
            name = storage_name(model)
            identifier(name)
            if not re.fullmatch('[a-z][a-z0-9_]*', name):
                # Model names are also UI routes, which are lowercase.
                raise ValueError(f'Model and table names (storage_name or Table attribute) must use lowercase letters, digits and underscores: {name!r}')
            if name in ("schema", "auth") or name.startswith("evlet_"):
                raise ValueError("The model name is reserved for metadata")
            if name in registered:
                raise ValueError(f"Duplicate model name: {name}")
            registered[name] = model
            logical = getattr(model, '__resource_id__', name)
            if logical in resources:
                raise ValueError(f'Duplicate resource identity: {logical}')
            resources[logical] = model
        for model in models:
            for field in model.__declared_fields__.values():
                if isinstance(field, Link) and isinstance(field.target, str):
                    target = resources.get(field.target) or registered.get(field.target)
                    if target is None:
                        raise ValueError(f'Unresolved Link {model.__name__}.{field.name}: {field.target}')
                    field.target = target
                if isinstance(field, Link) and field.target not in models:
                    raise ValueError(f"Link target for {model.__name__}.{field.name} is not registered")
                code = getattr(field, 'currency', None)
                if code is not None and not re.fullmatch('[A-Z]{3}', code):
                    holder = model.__declared_fields__.get(code)
                    if holder is None or holder.kind not in ('text', 'select'):
                        raise ValueError(f'{model.__name__}.{field.name}: currency must be an ISO 4217 code or a text field on the same model')
        from .ui.metadata import view_for
        for model in models:
            view_for(model)
        self.models = MappingProxyType(registered)
        self.resources = MappingProxyType(resources)
        for model in models:
            resolve_sources(self, model)
        from .reactivity import graph
        child_models = {child.model for child in self.children}
        from .calc import compile_plan, formula_hash
        plans = {}
        for model in models:
            if getattr(model, '__declaration_mode__', False) and model not in child_models:
                graph(self, model)
                plan = compile_plan(self, model)
                if plan is not None:
                    plans[model] = plan
        self.plans = MappingProxyType(plans)
        # What saved documents record: changes only when formulas or the fields they use change.
        self.formula_hashes = MappingProxyType({root: formula_hash(plan) for root, plan in plans.items()})
        # Move guards and action availability, compiled once per root document.
        from .states import compile_guards, kept_plan
        self.guards, self.guard_plans, self.guard_root_plans = compile_guards(self, models, plans, child_models)
        # Ordinary writes on a locked record evaluate rules over the results it keeps.
        self.kept_plans = MappingProxyType({root: kept_plan(plan) for root, plan in plans.items() if root.__state__ is not None})

    def root_of(self, model):
        owners = {child.model: child.parent for child in self.children}
        while model in owners:
            model = owners[model]
        return model

    def finite_reads(self, model, name, kind=None):
        """(scope, model, field) inputs of a finite computed field (or its initial/rule), resolved to models."""
        plan, path, owners = self.plan_for(model), [], {child.model: child for child in self.children}
        item = model
        while item in owners:
            path.insert(0, owners[item].name)
            item = owners[item].parent
        field = next(entry for entry in plan['fields'] if entry['path'] == path and entry['field'] == name and entry.get('kind') == kind)
        tables = {child.name: child.model for child in self.children if child.parent is model}
        result = []
        for scope, table, read in field['reads']:
            if scope == 'self': result.append(('self', model, read))
            elif scope == 'parent': result.append(('parent', owners[model].parent, read))
            elif scope == 'lookup': result.append(('lookup', self.models[table], read))
            elif scope != 'today': result.append((scope, tables[table], read))
        return result

    def plan_for(self, model):
        """The finite plan governing a model's document, or None for legacy calculation."""
        return self.plans.get(self.root_of(model))

    def name_for(self, model):
        name = storage_name(model)
        if self.models.get(name) is not model:
            raise ValueError("Model does not belong to this registry")
        return name

    def resolve(self, value):
        if isinstance(value, type):
            self.name_for(value)
            return value
        model = self.resources.get(value) or self.models.get(value)
        if model is None:
            raise ValueError(f'Unknown model: {value}')
        return model

    def ui_schema(self):
        from .ui.metadata import ui_schema
        from copy import deepcopy
        if not getattr(self, '_freeze_ui', False) and not all(getattr(model, '__declaration_mode__', False) for model in self.models.values()):
            return ui_schema(self)
        if not hasattr(self, '_ui_schema'):
            self._ui_schema = ui_schema(self)
        return deepcopy(self._ui_schema)

    def schema(self):
        return {"version": 1, "models": [
            {"name": name, "label": model.__name__,
             "fields": [field.metadata() for field in model.__fields__.values()]}
            for name, model in self.models.items()
        ]}


class Child(Model):
    """Owned row definition. Register it through a parent's Table declaration."""


def resolve_sources(registry, definition):
    from .query import SqlQuery
    for field in definition.__declared_fields__.values():
        if isinstance(field, Link):
            field.target = registry.resolve(field.target)
            for name in field.filters:
                if name not in field.target.__fields__: raise ValueError('Link filters must name stored target fields')
        elif field.filters: raise ValueError('Selection filters require a Link field')
        if field.fetch:
            link_name, target_name = field.fetch.split('.')
            link = definition.__declared_fields__.get(link_name)
            if not isinstance(link, Link): raise ValueError('Fetch requires a Link source')
            link.target = registry.resolve(link.target)
            target = link.target.__declared_fields__.get(target_name)
            if target is None or target.kind != field.kind: raise ValueError('Fetch target must have the same declared field type')
    for source in definition.__sources__.values():
        if isinstance(source.target, (str, type)):
            target = registry.resolve(source.target)
            source.target = target
            if set(source.fields) - set(target.__fields__): raise ValueError('Query output names must be stored target fields')
            source.fields = {name: target.__fields__[name] for name in source.fields}
            for name in source.filters: SqlQuery(registry, target).where({name: None})
    for table in definition.__tables__.values(): resolve_sources(registry, table.child)
