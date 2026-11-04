"""Renderer-neutral screen metadata, separate from persistent schema history."""

import re

from ..fields import CurrentUser, LongText, MISSING, Text
from ..declarations import Ref

from ..naming import form_hidden, standard


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


OPEN_IN = ('page', 'panel')
# The options a model's Meta may declare; each is public (docs/api-stability.md).
META_OPTIONS = frozenset(('label', 'plural', 'description', 'title_field', 'list_fields', 'search_fields', 'layout',
                          'calculation_language', 'numeric_profile', 'revisable', 'measures', 'key_fields', 'relations', 'attachments',
                          'effects', 'journal', 'naming', 'mail', 'title', 'open_in', 'api', 'period', 'period_scope'))

# How a collapsible section starts: open, closed, or closed when every field in it is empty.
COLLAPSED = (False, True, 'empty')


def starts_valid(value):
    """Whether value is one of COLLAPSED exactly: 1 and 0 are not True and False here."""
    return value is True or value is False or (type(value) is str and value == 'empty')


class Section:
    """A form section in Model.Meta.layout that a person can open and close.

    `collapsed` is where it starts: False (open), True (closed) or 'empty' (closed when every field in
    it is empty on an existing record; a new record shows it open). A person's own choice wins after that.
    A plain ('Title', fields) pair is a section that can't collapse."""

    __slots__ = ('title', 'fields', 'collapsible', 'collapsed')

    def __init__(self, title, fields, *, collapsible=True, collapsed=False):
        self.title, self.fields, self.collapsible, self.collapsed = title, fields, collapsible, collapsed

    def __repr__(self):
        return f'Section({self.title!r}, {self.fields!r}, collapsible={self.collapsible!r}, collapsed={self.collapsed!r})'

    def check(self, model):
        """Raise ValueError naming the model and the section when the options can't work."""
        where = f'{model} layout section {self.title!r}'
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError(f'{model} layout: give every Section a title')
        if not isinstance(self.fields, (tuple, list)):
            raise ValueError(f'{where}: list its fields as a tuple of field names')
        if type(self.collapsible) is not bool:
            raise ValueError(f'{where}: collapsible must be True or False')
        if not starts_valid(self.collapsed):
            raise ValueError(f"{where}: collapsed must be False, True or 'empty', not {self.collapsed!r}")
        if not self.collapsible and self.collapsed is not False:
            raise ValueError(f'{where}: collapsed needs collapsible=True, because this section always shows')


def view_for(model):
    meta = getattr(model, 'Meta', None)
    allowed = META_OPTIONS
    if meta is not None:
        if not isinstance(meta, type) or any(not key.startswith('_') and key not in allowed for key in vars(meta)):
            raise ValueError('Model.Meta supports ' + ', '.join(sorted(allowed)))
    # App fields only: instance fields (x_) differ per database and are added by Controls. The standard
    # name shows (first) on models whose naming fills it; the standard title follows its source and never shows.
    from ..naming import standard
    named = getattr(getattr(meta, 'naming', None), 'field', None) == 'name' and standard(model.__app_fields__.get('name')) == 'name'
    visible = {name: field for name, field in model.__app_fields__.items()
               if (standard(field) == 'name' and named) or (not standard(field) and not field.hidden)}
    if named:
        visible = {'name': visible.pop('name'), **visible}
    texts = [name for name, field in visible.items() if isinstance(field, Text) and not isinstance(field, LongText)]
    label = getattr(meta, 'label', label_for(model.__name__))
    plural = getattr(meta, 'plural', plural_for(label))
    description = getattr(meta, 'description', '')
    if any(not isinstance(value, str) for value in (label, plural, description)) or not label or not plural:
        raise ValueError('View labels must be nonempty strings and description must be text')
    title = getattr(meta, 'title_field', next((name for name in ('name', 'title', 'reference') if name in texts), texts[0] if texts else 'id'))
    if title != 'id' and title not in texts:
        raise ValueError('title_field must be a visible short text field or id')
    if not hasattr(meta, 'title_field') and 'title' in model.__app_fields__ and (standard(model.__app_fields__['title']) or 'title' in texts):
        # Without Meta.title_field, records show their title column (a template, the name or the text
        # id). With it, that field holds the same text the title does.
        title = "title"
    columns = getattr(meta, 'list_fields', tuple(name for name, field in visible.items() if not isinstance(field, LongText))[:4])
    search = getattr(meta, 'search_fields', tuple(texts[:8]))
    standard_text = {key for key, field in model.__app_fields__.items() if standard(field)}
    for value, maximum, names in ((columns, 8, {'id', *visible}), (search, 8, {name for name, field in visible.items() if isinstance(field, Text)} | standard_text)):
        if not isinstance(value, (tuple, list)) or len(value) > maximum or any(not isinstance(name, str) or name not in names for name in value) or len(set(value)) != len(value):
            raise ValueError('View fields must be distinct visible field names, with at most eight entries')
    # How a list opens its records: the full record page, or a side panel beside the narrowed list.
    open_in = getattr(meta, 'open_in', 'page')
    if open_in not in OPEN_IN:
        raise ValueError('open_in must be "page" or "panel"')
    # Search also finds records by their title and name.
    search = tuple(dict.fromkeys((*search, *(key for key in ('title', 'name') if key in standard_text))))
    layout = getattr(meta, 'layout', (('Details', tuple(visible)),))
    used = []
    groups = []
    if not isinstance(layout, (tuple, list)) or len(layout) > 8:
        raise ValueError('Use at most eight layout groups')
    for group in layout:
        section = group if isinstance(group, Section) else None
        if section is not None:
            section.check(model.__name__)
            group = (section.title, section.fields)
        if not isinstance(group, (tuple, list)) or len(group) != 2 or not isinstance(group[0], str) or not group[0] or not isinstance(group[1], (tuple, list)):
            raise ValueError('Layout groups are (label, field names) pairs or Section(title, fields)')
        unknown = [str(name) for name in group[1] if not isinstance(name, str) or name not in visible]
        if unknown:
            raise ValueError(f'Layout group {group[0]} names unknown or hidden fields: {", ".join(unknown)}')
        for name in group[1]:
            if name in used:
                raise ValueError(f'Layout lists {name} more than once')
            used.append(name)
        # A plain (label, fields) pair is a section that can't collapse.
        groups.append({'label': group[0], 'fields': list(group[1]), 'collapsible': bool(section and section.collapsible),
                       'collapsed': section.collapsed if section and section.collapsible else False})
    # Visible fields the layout leaves out follow under Other, in declaration order.
    rest = [name for name in visible if name not in used]
    if rest:
        groups.append({'label': 'Other', 'fields': rest, 'collapsible': False, 'collapsed': False})
    return {'layout': groups, 'label': label, 'plural': plural, 'description': description, 'title_field': title,
            'list_fields': list(dict.fromkeys(('id', *columns))), 'search_fields': list(search), 'open_in': open_in}


def currency_schema(field):
    """A fixed code as currency; any other source as currency_source, resolved per user by /api/schema."""
    source = field.currency_source() if hasattr(field, 'currency_source') else None
    if source is None:
        return {}
    return {'currency': source['code']} if 'code' in source else {'currency_source': source}


def field_schema(field):
    result = {**field.metadata(), 'label': field.label or label_for(field.name),
              'help_text': field.help_text, **({'tooltip': field.tooltip} if field.tooltip else {}), 'placeholder': field.placeholder,
              **({'suffix': field.suffix} if field.suffix else {}),
              # Forms leave out the standard title and a name no naming fills (evlet/naming.py); responses keep them.
              'hidden': field.hidden or form_hidden(field), 'read_only': field.read_only,
              **({'stored': field.stored} if field.compute is not None or field.query is not None or not field.stored else {}),
              **currency_schema(field),
              **({'editable_when_locked': True} if getattr(field, 'editable_when_locked', False) else {}),
              **({'sequence': field.schema()} if field.kind == 'sequence' else {}),
              'default_kind': 'none' if field.default is MISSING else 'reference' if isinstance(field.default, Ref) else 'user' if field.default is CurrentUser
              else 'callable' if callable(field.default) else 'static'}
    if result['default_kind'] == 'static':
        value = None if field.default is None else field.validate(field.default)
        result['default'] = field.serialize(value)
    return result


def ui_schema(registry):
    # A model with Meta.api = None is left out; "read" is stated so screens offer no writes ("full" is the default).
    api = getattr(registry, 'api', {})
    return {'version': 1, 'ui_version': 1, 'models': [
        {'name': name, **view_for(model), 'fields': [field_schema(field) for field in model.__app_fields__.values()],
         **({'api': 'read'} if api.get(model) == 'read' else {}), **behavior(model, registry)}
        for name, model in registry.models.items() if api.get(model, 'full') is not None
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
