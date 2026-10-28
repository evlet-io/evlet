"""Explicit application registries and validated record snapshots."""

import re
from types import MappingProxyType

from .fields import Field, Link, MISSING


def identifier(name):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or name.lower().startswith("sqlite_"):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return '"' + name + '"'


class ValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(f"{key}: {value}" for key, value in errors.items()))


class ModelType(type):
    def __new__(mcls, name, bases, namespace):
        fields = {}
        for base in bases:
            fields.update(getattr(base, "__fields__", {}))
        for key, value in namespace.items():
            if isinstance(value, Field):
                identifier(key)
                if key.lower() == "id" or key == "to_dict" or any(
                    hasattr(base, key) and key not in getattr(base, "__fields__", {})
                    for base in bases
                ):
                    raise ValueError(f"Reserved field name: {key}")
                fields[key] = value
            elif key in fields:
                raise ValueError(f"Inherited field {key!r} must remain a field")
        if len({key.lower() for key in fields}) != len(fields):
            raise ValueError("Field names must be distinct ignoring case")
        cls = super().__new__(mcls, name, bases, namespace)
        cls.__fields__ = MappingProxyType(fields)
        return cls


class Model(metaclass=ModelType):
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
        instance = cls(**{
            name: field.from_storage(row[name]) for name, field in cls.__fields__.items()
        })
        instance._id = row["id"]
        return instance


class Registry:
    def __init__(self, *models):
        registered = {}
        for model in models:
            if not isinstance(model, type) or not issubclass(model, Model) or model is Model:
                raise ValueError("Register Model subclasses")
            name = model.__name__.lower()
            identifier(name)
            if name == "schema":
                raise ValueError("The model name 'schema' is reserved for metadata")
            if name in registered:
                raise ValueError(f"Duplicate model name: {name}")
            registered[name] = model
        for model in models:
            for field in model.__fields__.values():
                if isinstance(field, Link) and field.target not in models:
                    raise ValueError(f"Link target for {model.__name__}.{field.name} is not registered")
        self.models = MappingProxyType(registered)

    def name_for(self, model):
        name = model.__name__.lower()
        if self.models.get(name) is not model:
            raise ValueError("Model does not belong to this registry")
        return name

    def schema(self):
        return {"version": 1, "models": [
            {"name": name, "label": model.__name__,
             "fields": [field.metadata() for field in model.__fields__.values()]}
            for name, model in self.models.items()
        ]}
