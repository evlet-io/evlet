"""Renderer-neutral screen metadata, separate from persistent schema history."""

import re

from .fields import LongText, MISSING, Text


def label_for(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ").capitalize()


def view_for(model):
    meta = getattr(model, 'Meta', None)
    allowed = {'label', 'plural', 'description', 'title_field', 'list_fields', 'search_fields'}
    if meta is not None:
        if not isinstance(meta, type) or any(not key.startswith('_') and key not in allowed for key in vars(meta)):
            raise ValueError('Model.Meta supports label, plural, description, title_field, list_fields, and search_fields')
    visible = {name: field for name, field in model.__fields__.items() if not field.hidden}
    texts = [name for name, field in visible.items() if isinstance(field, Text) and not isinstance(field, LongText)]
    label = getattr(meta, 'label', label_for(model.__name__))
    plural = getattr(meta, 'plural', label + 's')
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
    return {'label': label, 'plural': plural, 'description': description, 'title_field': title,
            'list_fields': list(dict.fromkeys(('id', *columns))), 'search_fields': list(search)}


def field_schema(field):
    result = {**field.metadata(), 'label': field.label or label_for(field.name),
              'help_text': field.help_text, 'placeholder': field.placeholder,
              'hidden': field.hidden, 'read_only': field.read_only,
              'default_kind': 'none' if field.default is MISSING else 'callable' if callable(field.default) else 'static'}
    if result['default_kind'] == 'static':
        value = None if field.default is None else field.validate(field.default)
        result['default'] = field.serialize(value)
    return result


def ui_schema(registry):
    return {'version': 1, 'ui_version': 1, 'models': [
        {'name': name, **view_for(model), 'fields': [field_schema(field) for field in model.__fields__.values()]}
        for name, model in registry.models.items()
    ]}
