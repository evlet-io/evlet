"""What a model means to a reader: key fields, related records and attachment limits.

Declared on `Model.Meta`:

    class Meta:
        key_fields = ("customer", "status", "total")
        relations = (Related(Payment, "invoice", label="Payments", limit=5, order="-paid_on"),)
        attachments = Attachments(max_files=5, max_size=262_144)

Relations are reverse Links: every Link on a root model to another root model is discovered
automatically. `Related` names, orders, limits or hides one of them.
"""

import re
from dataclasses import dataclass
from types import MappingProxyType

from .database import ROW_LIMIT
from .fields import Link, Reference
from .models import storage_name

KEY_FIELDS = 8
MAX_FILES = 10
MAX_SIZE = 1024 * 1024
RELATION_ROWS = 10


@dataclass(frozen=True)
class Attachments:
    """Private files on a model's records, within the framework maximums (10 files of 1 MiB)."""
    max_files: int = MAX_FILES
    max_size: int = MAX_SIZE

    def __post_init__(self):
        if type(self.max_files) is not int or not 1 <= self.max_files <= MAX_FILES:
            raise ValueError(f'Attachments max_files must be an integer from 1 to {MAX_FILES}')
        if type(self.max_size) is not int or not 1 <= self.max_size <= MAX_SIZE:
            raise ValueError(f'Attachments max_size must be an integer from 1 to {MAX_SIZE} bytes')

    def public(self):
        return {'max_files': self.max_files, 'max_size': self.max_size}


@dataclass(frozen=True)
class Related:
    """Names, orders, limits or hides the records of `model` whose Link `link` points to this model."""
    model: object
    link: str
    label: str = None
    limit: int = RELATION_ROWS
    order: object = '-id'
    hidden: bool = False

    def __post_init__(self):
        if not isinstance(self.model, (type, str)) or not isinstance(self.link, str) or not self.link:
            raise ValueError('Related needs a model and the name of its Link field')
        if self.label is not None and (not isinstance(self.label, str) or not self.label):
            raise ValueError('Related label must be nonempty text')
        if type(self.limit) is not int or not 1 <= self.limit <= ROW_LIMIT:
            raise ValueError(f'Related limit must be an integer from 1 to {ROW_LIMIT}')
        order = (self.order,) if isinstance(self.order, str) else self.order
        if not isinstance(order, (tuple, list)) or not 1 <= len(order) <= 3 or any(
                not isinstance(item, str) or not re.fullmatch(r'-?[a-z_][a-z0-9_]*', item) for item in order):
            raise ValueError('Related order is one to three field names, each optionally prefixed with -')
        if type(self.hidden) is not bool:
            raise ValueError('Related hidden is True or False')
        object.__setattr__(self, 'order', tuple(order))


@dataclass(frozen=True)
class Relation:
    """A resolved reverse Link: records of `source` whose `link` holds the id of the owning record."""
    name: str
    source: type
    link: str
    label: str
    limit: int
    order: tuple

    def public(self, registry, order=None):
        return {'name': self.name, 'label': self.label, 'model': registry.name_for(self.source),
                'link': self.link, 'limit': self.limit, 'order': list(self.order if order is None else order)}


@dataclass(frozen=True)
class Meaning:
    key_fields: tuple = ()
    relations: tuple = ()
    attachments: Attachments = None


def meaning_for(registry):
    """Validate every model's meaning metadata once Link targets are resolved."""
    children = {child.model for child in registry.children}
    roots = [model for model in registry.models.values() if model not in children]
    discovered = {}
    for source in roots:
        for name, field in source.__declared_fields__.items():
            if isinstance(field, Link) and field.target not in children:
                discovered.setdefault(field.target, []).append((source, name))
    result = {}
    for model in registry.models.values():
        meta = getattr(model, 'Meta', None)
        declared = {key: getattr(meta, key) for key in ('key_fields', 'relations', 'attachments') if hasattr(meta, key)}
        if model in children:
            if declared:
                raise ValueError(f'{model.__name__}: Meta.key_fields, relations and attachments are for root models, not Table rows')
            result[model] = Meaning()
            continue
        result[model] = Meaning(key_fields(model, declared.get('key_fields', ())),
                                relations(registry, model, declared.get('relations', ()), discovered.get(model, ()),
                                          inherited='Meta' not in vars(model)),
                                attachments(model, declared.get('attachments')))
    return MappingProxyType(result)


def key_fields(model, names):
    visible = {name for name, field in model.__declared_fields__.items() if not field.hidden}
    if (not isinstance(names, (tuple, list)) or any(not isinstance(name, str) for name in names)
            or len(names) > KEY_FIELDS or len(set(names)) != len(names) or any(name not in visible for name in names)):
        raise ValueError(f'{model.__name__}: Meta.key_fields are at most {KEY_FIELDS} distinct visible fields '
                         '(Tables are listed as children, not key fields)')
    return tuple(names)


def relations(registry, model, declared, discovered, *, inherited=False):
    """Relations of one concrete model. A subclass that inherits its parent's Meta applies only the
    inherited Related entries whose Link points to the subclass itself; the others belong to the parent."""
    if not isinstance(declared, (tuple, list)) or any(not isinstance(item, Related) for item in declared):
        raise ValueError(f'{model.__name__}: Meta.relations is a tuple of Related(...)')
    counts = {}
    for source, _ in discovered:
        counts[source] = counts.get(source, 0) + 1
    settings = {}
    for item in declared:
        source = registry.resolve(item.model)
        field = source.__declared_fields__.get(item.link)
        if (source, item.link) not in discovered:
            if inherited:
                continue
            raise ValueError(f'{model.__name__}: Related {source.__name__}.{item.link} must be a Link to {model.__name__} '
                             'on a root model')
        if field.hidden:
            raise ValueError(f'{model.__name__}: Related {source.__name__}.{item.link} is a hidden Link')
        if (source, item.link) in settings:
            raise ValueError(f'{model.__name__}: Related {source.__name__}.{item.link} is declared twice')
        for entry in item.order:
            name = entry.lstrip('-')
            if name != 'id' and (name not in source.__fields__ or isinstance(source.__fields__[name], Reference)):
                raise ValueError(f'{model.__name__}: Related order must name stored fields of {source.__name__} (not References)')
        settings[source, item.link] = item
    from .ui.metadata import label_for, view_for
    found = []
    for source, link in discovered:
        item = settings.get((source, link))
        field = source.__declared_fields__[link]
        if item is None and field.hidden or item is not None and item.hidden:
            continue
        if item is not None and item.label is not None:
            label = item.label
        elif getattr(field, 'engine_revises', False):
            label = 'Revisions'
        else:
            label = view_for(source)['plural']
            if counts[source] > 1:
                label += f' ({field.label or label_for(link)})'
        found.append(Relation(f'{storage_name(source)}.{link}', source, link, label,
                              RELATION_ROWS if item is None else item.limit, ('-id',) if item is None else item.order))
    # Declared relations first, in declared order; the rest by model and field name.
    position = {key: index for index, key in enumerate(settings)}
    found.sort(key=lambda relation: (position.get((relation.source, relation.link), len(position)), relation.name))
    return tuple(found)


def attachments(model, value):
    if value is not None and not isinstance(value, Attachments):
        raise ValueError(f'{model.__name__}: Meta.attachments is Attachments(max_files=..., max_size=...)')
    return value
