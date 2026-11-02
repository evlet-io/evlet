"""Renderer-neutral screen metadata, separate from persistent schema history."""

import re

from ..fields import LongText, MISSING, Text
from ..declarations import Ref


def label_for(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ").capitalize()


def plural_for(label):
    """A default plural label by simple English rules; Meta.plural overrides it."""
    lower = label.lower()
    if len(lower) > 1 and lower.endswith('y') and lower[-2] not in 'aeiou':
        return label[:-1] + 'ies'
    if lower.endswith(('s', 'x', 'z', 'ch', 'sh')):
        return label + 'es'
    return label + 's'


def view_for(model):
    meta = getattr(model, 'Meta', None)
    allowed = {'label', 'plural', 'description', 'title_field', 'list_fields', 'search_fields', 'layout',
               'calculation_language', 'numeric_profile', 'revisable', 'measures', 'key_fields', 'relations', 'attachments', 'effects', 'mail'}
    if meta is not None:
        if not isinstance(meta, type) or any(not key.startswith('_') and key not in allowed for key in vars(meta)):
            raise ValueError('Model.Meta supports ' + ', '.join(sorted(allowed)))
    visible = {name: field for name, field in model.__declared_fields__.items() if not field.hidden}
    texts = [name for name, field in visible.items() if isinstance(field, Text) and not isinstance(field, LongText)]
    label = getattr(meta, 'label', label_for(model.__name__))
    plural = getattr(meta, 'plural', plural_for(label))
    description = getattr(meta, 'description', '')
    if any(not isinstance(value, str) for value in (label, plural, description)) or not label or not plural:
        raise ValueError('View labels must be nonempty strings and description must be text')
    title = getattr(meta, 'title_field', next((name for name in ('name', 'title', 'reference') if name in texts), texts[0] if texts else 'id'))
    if title != 'id' and title not in texts:
        raise ValueError('title_field must be a visible short text field or id')
    columns = getattr(meta, 'list_fields', tuple(name for name, field in visible.items() if not isinstance(field, LongText))[:4])
    search = getattr(meta, 'search_fields', tuple(texts[:8]))
    for value, maximum, names in ((columns, 8, {'id', *visible}), (search, 8, {name for name, field in visible.items() if isinstance(field, Text)})):
        if not isinstance(value, (tuple, list)) or len(value) > maximum or any(not isinstance(name, str) or name not in names for name in value) or len(set(value)) != len(value):
            raise ValueError('View fields must be distinct visible field names, with at most eight entries')
    layout = getattr(meta, 'layout', (('Details', tuple(visible)),))
    used = []
    groups = []
    if not isinstance(layout, (tuple, list)) or len(layout) > 8:
        raise ValueError('Use at most eight layout groups')
    for group in layout:
        if not isinstance(group, (tuple, list)) or len(group) != 2 or not isinstance(group[0], str) or not group[0] or not isinstance(group[1], (tuple, list)) or any(not isinstance(name, str) or name not in visible for name in group[1]):
            raise ValueError('Layout groups need a label and visible field names')
        used.extend(group[1])
        groups.append({'label': group[0], 'fields': list(group[1])})
    if len(set(used)) != len(used) or set(used) != set(visible):
        raise ValueError('Layout must include every visible field exactly once')
    return {'layout': groups, 'label': label, 'plural': plural, 'description': description, 'title_field': title,
            'list_fields': list(dict.fromkeys(('id', *columns))), 'search_fields': list(search)}


def field_schema(field):
    result = {**field.metadata(), 'label': field.label or label_for(field.name),
              'help_text': field.help_text, 'placeholder': field.placeholder,
              'hidden': field.hidden, 'read_only': field.read_only,
              **({'stored': field.stored} if field.compute is not None or field.query is not None or not field.stored else {}),
              **({'currency': field.currency} if getattr(field, 'currency', None) is not None else {}),
              **({'editable_when_locked': True} if getattr(field, 'editable_when_locked', False) else {}),
              **({'sequence': field.schema()} if field.kind == 'sequence' else {}),
              'default_kind': 'none' if field.default is MISSING else 'reference' if isinstance(field.default, Ref) else 'callable' if callable(field.default) else 'static'}
    if result['default_kind'] == 'static':
        value = None if field.default is None else field.validate(field.default)
        result['default'] = field.serialize(value)
    return result


def ui_schema(registry):
    return {'version': 1, 'ui_version': 1, 'models': [
        {'name': name, **view_for(model), 'fields': [field_schema(field) for field in model.__declared_fields__.values()],
         **behavior(model, registry)}
        for name, model in registry.models.items()
    ]}


def behavior(model, registry):
    if not getattr(model, '__declaration_mode__', False):
        return {}
    from ..reactivity import plans, serialized, graph
    nodes, order = ({}, []) if any(child.model is model for child in registry.children) else graph(registry, model)
    fields = plans(model)
    return {'document_version': 1, 'resource': getattr(model, '__resource_id__', registry.name_for(model)),
            'behavior': {name: {key: {field: serialized(value) for field, value in item.items()} if key == 'filters' else serialized(item)
                                for key, item in entry.items()} for name, entry in fields.items()},
            'order': order, 'sources': source_schema(model)}


def source_schema(model):
    return {name: {key: {'type': field.kind, **({'scale': field.scale} if hasattr(field, 'scale') else {})} for key, field in source.fields.items()}
            for name, source in model.__sources__.items()}
