"""Explicit application registries and validated record snapshots."""

import re
from types import MappingProxyType

from .fields import Field, Link, MISSING
from .declarations import Table, ReactiveQuery


def identifier(name):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or name.lower().startswith("sqlite_"):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return '"' + name + '"'


def storage_name(model):
    return getattr(model, 'storage_name', model.__name__.lower())


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
        cls = super().__new__(mcls, name, bases, namespace)
        cls.__declared_fields__ = MappingProxyType(fields)
        cls.__fields__ = MappingProxyType({key: value for key, value in fields.items() if value.stored})
        cls.__tables__ = MappingProxyType(tables)
        cls.__sources__ = MappingProxyType(sources)
        if sources or tables or 'permissions' in namespace or any(hasattr(value, '__action__') for value in namespace.values()) or any(field.compute is not None or not field.stored for field in fields.values()):
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
            name: field.from_storage(row[name]) for name, field in cls.__fields__.items()
        })
        instance._id = row["id"]
        return instance


class Registry:
    def __init__(self, *models):
        from .fields import Integer
        from .business import ChildTable
        self.children = []
        expanded = list(models)
        for model in models:
            for name, table in model.__tables__.items():
                if not isinstance(table.child, type) or not issubclass(table.child, Child) or table.child.__tables__:
                    raise ValueError('Tables require Child subclasses; nested tables are unsupported')
                storage = table.storage_name or getattr(model, 'storage_name', model.__name__.lower()) + '_' + name
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
        models = tuple(expanded)
        registered = {}
        resources = {}
        for model in models:
            if not isinstance(model, type) or not issubclass(model, Model) or model is Model:
                raise ValueError("Register Model subclasses")
            if getattr(model, '__transient__', False): raise ValueError('Transient forms cannot be persistent models')
            name = getattr(model, 'storage_name', model.__name__.lower())
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
        from .ui.metadata import view_for
        for model in models:
            view_for(model)
        self.models = MappingProxyType(registered)
        self.resources = MappingProxyType(resources)
        for model in models:
            resolve_sources(self, model)
        from .reactivity import graph
        child_models = {child.model for child in self.children}
        for model in models:
            if getattr(model, '__declaration_mode__', False) and model not in child_models:
                graph(self, model)

    def name_for(self, model):
        name = getattr(model, 'storage_name', model.__name__.lower())
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
    from .query import Query
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
            for name in source.filters: Query(registry, target).where({name: None})
    for table in definition.__tables__.values(): resolve_sources(registry, table.child)
