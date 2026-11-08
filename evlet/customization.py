"""Controls model customization: field, layout, list and naming overrides, instance fields and counters.

Overrides are checked here when saved and again on every rebuild. One that no longer validates fails
closed: a field becomes read only for everyone, a layout, list or naming override falls back to the app
file. Instance fields (x_) are columns this instance adds. Their definitions live in evlet_custom_fields and
their Fields in the database's Layer (see fields.Layer); model classes from app code are never changed.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import sqlite3
from threading import Lock
import time
from types import MappingProxyType

from .auth import AccessError
from .naming import form_hidden
from .declarations import Ref
from .fields import (ACTIVE, MISSING, Boolean, Currency, CurrentUser, Date, Decimal, Integer, Layer, Link, LongText, Select,
                     State, Text, active_layer)
from .database import ConflictError
from .models import ValidationError, identifier
from .ui.metadata import FLOWS, OPEN_IN, PANEL_BLOCKS, PANEL_KINDS, label_for, list_tabs_for, starts_valid, view_for

PROPS = ('label', 'help_text', 'tooltip', 'placeholder', 'hidden', 'required', 'read_only', 'default', 'options_add', 'options_hidden')
DISPLAY = ('label', 'help_text', 'tooltip', 'placeholder', 'hidden')
OPTION = re.compile(r'[a-z][a-z0-9_]{0,39}')
FIELD_NAME = re.compile(r'x_[a-z0-9_]{1,38}')
FRAMEWORK_COLUMNS = ('id', 'created_by', 'created_at', 'modified_by', 'modified_at', 'tenant', 'revision')
TYPES = ('Text', 'LongText', 'Integer', 'Decimal', 'Currency', 'Date', 'Boolean', 'Select', 'Link')
PAGE_SIZES = (25, 50, 100)
DROP_COLUMN = (3, 35, 0)
REQUIRED_HIDDEN = "A required field can't be hidden. People couldn't fill it in."
RETIRED = 'This option is no longer offered. Choose another one.'


def ensure_schema(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS evlet_custom_fields (model TEXT NOT NULL, name TEXT NOT NULL, definition TEXT NOT NULL, '
                       'created_by TEXT, created_at INTEGER, PRIMARY KEY (model, name))')


def sqlite_version(connection):
    return connection.execute('SELECT sqlite_version()').fetchone()[0]


# ---------- instance fields ----------

def build_field(definition, registry):
    """The Field for an instance field definition. Instance fields are never required at the field level:
    app code that does not know them keeps working; Controls asks for them on API writes."""
    kind, options = definition['type'], {'label': definition['label'], 'help_text': definition.get('help_text', ''), 'tooltip': definition.get('tooltip', '')}
    if kind == 'Text':
        field = Text(**options)
    elif kind == 'LongText':
        field = LongText(max_length=4000, **options)
    elif kind == 'Integer':
        field = Integer(**options)
    elif kind == 'Decimal':
        field = Decimal(scale=4, **options)
    elif kind == 'Currency':
        field = Currency(**options)
    elif kind == 'Date':
        field = Date(**options)
    elif kind == 'Boolean':
        field = Boolean(**options)
    elif kind == 'Select':
        field = Select(definition['options'], **options)
    else:
        field = Link(registry.models[definition['target']], **options)
    if definition.get('default') is not None:
        field.default = field.validate(definition['default'])
    field.instance = definition
    return field


def instance_fields(registry, rows, previous=None):
    """{model: {name: Field}} for rows [(table, field name, definition)]. Fields whose definition did not
    change are reused from `previous`. Nothing is set on the model classes."""
    result = {}
    for table, name, definition in rows:
        model = registry.models.get(table)
        if model is None or not FIELD_NAME.fullmatch(name):
            continue
        old = (previous or {}).get(model, {}).get(name)
        if old is not None and old.instance == definition:
            field = old
        else:
            field = build_field(definition, registry)
            field.name = name
        result.setdefault(model, {})[name] = field
    return result


@contextmanager
def activated(database, layer):
    """Make a layer active for the code inside, whatever was active before."""
    token = ACTIVE.set((database, layer))
    try:
        yield
    finally:
        ACTIVE.reset(token)


def add_column(connection, table, name, field):
    if isinstance(connection, sqlite3.Connection):
        from .migrations import _column
        connection.execute(f'ALTER TABLE {identifier(table)} ADD COLUMN {_column(name, field)[0]}')
        return
    from .models import storage_name
    from .postgres_schema import column
    connection.execute(f'ALTER TABLE {identifier(table)} ADD COLUMN {column(name, field)[0]}')
    if isinstance(field, Link):
        connection.execute(f'ALTER TABLE {identifier(table)} ADD FOREIGN KEY ({identifier(name)}) '
                           f'REFERENCES {identifier(storage_name(field.target))}(id)')


# ---------- file values ----------

def shown_default(field):
    if field.default is MISSING or field.default is None or callable(field.default) or isinstance(field.default, Ref):
        return None
    return field.serialize(field.validate(field.default))


def file_field(field, name):
    return {'label': field.label or label_for(name), 'help_text': field.help_text, 'tooltip': field.tooltip, 'placeholder': field.placeholder,
            'hidden': field.hidden, 'required': field.required or bool(getattr(field, 'instance', {}).get('required')), 'read_only': field.read_only,
            'default': shown_default(field), 'options': list(field.choices) if isinstance(field, Select) else None}


def fold(section):
    """collapsible and collapsed of a layout section, kept only when it can collapse, and flow only when it is
    columns: a plain section is stored and compared as before these options existed."""
    return {**({'collapsible': True, 'collapsed': section.get('collapsed', False)} if section.get('collapsible') else {}),
            **({'flow': 'columns'} if section.get('flow') == 'columns' else {})}


def sections_of(value):
    """The sections of a layout value: the list itself, or its "sections" when it also orders the record tabs."""
    return value['sections'] if isinstance(value, dict) else value


def tabs_of(value):
    """The record tab order and default tab of a layout value ([] and None when it sets neither)."""
    return (value.get('tabs', []), value.get('default_tab')) if isinstance(value, dict) else ([], None)


def panel_of(value):
    """What a layout value hides in a list's side panel ({} shows everything), or None when it leaves that to the app."""
    return value.get('panel') if isinstance(value, dict) else None


def hidden_of(panel):
    """The hidden names of a schema "panel" entry, without its order."""
    return {kind: list(panel[kind]) for kind in PANEL_KINDS if panel and panel.get(kind)}


def width(field):
    return 12 if isinstance(field, LongText) else 6


class Customization:
    """The model side of Controls, for one Business."""

    def __init__(self, controls):
        self.controls, self.business = controls, controls.business
        self.registry = controls.registry
        self.effective, self.defaults, self.choices = {}, {}, {}
        self.layouts, self.lists, self.read_only, self.tabs, self.panels = {}, {}, {}, {}, {}
        self.file_naming = dict(self.registry.naming)
        self.naming = MappingProxyType(self.file_naming)
        self.drop = None
        # Instance field changes run one at a time: backup, DDL, definition, version.
        self.engine = Lock()

    # ---------- start ----------

    def initialize(self, connection):
        ensure_schema(connection)
        if isinstance(connection, sqlite3.Connection):
            version = sqlite_version(connection)
            if tuple(int(part) for part in version.split('.')[:3]) < DROP_COLUMN:
                self.drop = (f'Removing a field needs SQLite 3.35 or later, and this server has {version}. '
                             'Update SQLite to remove fields. Adding fields still works.')

    def fields(self, model):
        """Fields of a model that Controls can change: everything but the engine's own."""
        return {name: field for name, field in model.__declared_fields__.items() if not name.startswith('evlet_')}

    def editable(self, model, name, field):
        rules = self.file_naming.get(model)
        return field.stored and field.compute is None and field.query is None and field.fetch is None \
            and not isinstance(field, State) and field.kind != 'sequence' and not (rules and rules.field == name) \
            and not getattr(field, 'engine_revises', False)

    def file_value(self, kind, model, name=None):
        if kind == 'field':
            return file_field(model.__declared_fields__[name], name)
        if kind == 'layout':
            fields, view = model.__declared_fields__, view_for(model)
            sections = [{'title': group['label'], 'fields': [{'name': key, 'width': width(fields[key])} for key in group['fields']], **fold(group)}
                        for group in view['layout']]
            # What the app hides in a list's side panel (panel=False flags or Meta.panel), for the Controls toggles.
            panel = hidden_of(view.get('panel'))
            if not (view['tabs'] or view['default_tab'] or panel):
                return sections
            return {'sections': sections, **({'tabs': view['tabs'], 'default_tab': view['default_tab']} if view['tabs'] or view['default_tab'] else {}),
                    **({'panel': panel} if panel else {})}
        if kind == 'list':
            view = view_for(model)
            tabs = view.get('list_tabs')
            return {'columns': [key for key in view['list_fields'] if key != 'id'], 'page_size': 50, 'sort': '-id', 'open_in': view['open_in'],
                    'list_tabs': tabs['field'] if tabs else None, 'list_tab': tabs['default'] if tabs else 'all'}
        return [dict(item) for item in getattr(model.Meta, 'naming').series]

    def items(self):
        """The model kinds for GET /api/_controls/overrides: (kind, key, file value)."""
        for name, model in self.controls.models().items():
            for field_name in self.fields(model):
                yield 'field', f'{name}.{field_name}', self.file_value('field', model, field_name)
            yield 'layout', name, self.file_value('layout', model)
            yield 'list', name, self.file_value('list', model)
            if model in self.file_naming:
                yield 'naming', name, self.file_value('naming', model)

    # ---------- checks ----------

    def target(self, kind, key):
        if kind == 'field':
            model_name, _, name = key.partition('.')
            model = self.controls.models().get(model_name)
            if model is None or name not in self.fields(model):
                raise ValueError('Choose a field of an installed model.')
            return model, name
        model = self.controls.models().get(key)
        if model is None:
            raise ValueError('Choose an installed model.')
        if kind == 'naming' and model not in self.file_naming:
            raise ValueError(f'{key} has no naming series in models.py.')
        return model, None

    def check(self, kind, key, value, saved=False):
        """A checked value, or None when it equals the file value. Raises ValueError.

        `saved` checks a stored override on start: a series whose condition names a missing field is then
        skipped rather than refused."""
        model, name = self.target(kind, key)
        if value is None:
            return None
        if kind == 'naming':
            checked = self.check_naming(model, name, value, saved)
        else:
            checked = {'field': self.check_field, 'layout': self.check_layout, 'list': self.check_list}[kind](model, name, value)
        file = self.file_value(kind, model, name)
        if kind == 'field':
            return checked or None
        return None if checked == file else checked

    def check_field(self, model, name, value):
        field = model.__declared_fields__[name]
        file = file_field(field, name)
        if not isinstance(value, dict) or not value or set(value) - set(PROPS):
            raise ValueError('Send label, help_text, tooltip, placeholder, hidden, required, read_only, default, options_add or options_hidden.')
        if set(value) - set(DISPLAY) and not self.editable(model, name, field):
            raise ValueError(f'{name} is filled by the app or the server, so only its label, help text, tooltip, placeholder and hidden can change.')
        out = {}
        for prop in ('label', 'help_text', 'tooltip', 'placeholder'):
            if prop in value:
                text = value[prop]
                if not isinstance(text, str) or len(text) > (80 if prop == 'label' else 200):
                    raise ValueError(f'Use text of at most {80 if prop == "label" else 200} characters.')
                text = text.strip()
                if prop == 'label' and not text:
                    raise ValueError("A label can't be empty.")
                out[prop] = text
        for prop in ('hidden', 'required', 'read_only'):
            if prop in value:
                if type(value[prop]) is not bool:
                    raise ValueError(f'Use true or false for {prop.replace("_", " ")}.')
                out[prop] = value[prop]
        added = getattr(field, 'instance', None)
        if file['required'] and out.get('required') is False:
            raise ValueError(f'{name} is required on this instance. Remove the field and add it again to make it optional.' if added
                             else f'{name} is required in models.py and stays required.')
        if file['read_only'] and out.get('read_only') is False:
            raise ValueError(f'{name} is read only in models.py and stays read only.')
        if (file['required'] or out.get('required')) and out.get('hidden'):
            raise ValueError(REQUIRED_HIDDEN)
        if file['hidden'] and out.get('hidden') is False:
            raise ValueError(f'{name} is hidden in models.py and stays hidden.')
        options = file['options']
        for prop in ('options_add', 'options_hidden'):
            if prop not in value:
                continue
            if options is None or isinstance(field, State):
                raise ValueError('Only choice fields have options.')
            items = value[prop]
            if not isinstance(items, list) or any(not isinstance(item, str) for item in items) or len(set(items)) != len(items):
                raise ValueError('Send a list of options, each once.')
            if prop == 'options_add':
                for item in items:
                    if not OPTION.fullmatch(item):
                        raise ValueError(f'{item} is not a usable option. Use lowercase letters, digits and underscores, like exhibition.')
                    if item in options:
                        raise ValueError(f'{item} is already an option.')
            elif set(items) - set(options):
                raise ValueError('Only options from models.py can be hidden.')
            out[prop] = items
        visible = [item for item in options or () if item not in out.get('options_hidden', ())] + out.get('options_add', [])
        if options is not None and not visible:
            raise ValueError('Keep at least one option shown.')
        if file['default'] in out.get('options_hidden', ()) and value.get('default') in (None, file['default']):
            raise ValueError(f"{file['default']} is the default in models.py, so it can't be hidden. Choose another default first.")
        if value.get('default') is not None:
            if field.default is CurrentUser:
                raise ValueError(f'{name} is filled with the signed-in user in models.py, so its default stays.')
            if isinstance(field, Link):
                raise ValueError("Links don't take a default.")
            if options is not None and value['default'] not in visible:
                raise ValueError('Choose a default from the options shown.')
            current = active_layer()
            with activated(self.business.database, Layer(current.fields, {**current.choices, field: tuple(out.get('options_add', ()))})):
                try:
                    out['default'] = field.serialize(field.validate(value['default']))
                except (ValueError, TypeError) as exc:
                    raise ValueError(f'The default is not a valid value: {exc}') from None
        required = file['required'] or out.get('required', False)
        read_only = file['read_only'] or out.get('read_only', False)
        if required and read_only and (out.get('required') or out.get('read_only')) and out.get('default', file['default']) is None:
            raise ValueError('A field that is required and read only needs a default, or no one could create a record.')
        return {prop: item for prop, item in out.items() if item != file.get(prop, False if prop in ('hidden', 'required', 'read_only') else [])
                and not (prop in ('options_add', 'options_hidden') and not item)}

    def tab_names(self, model):
        """Tabs a record page of the model can show, in their usual order: Activity, Details, Revisions, its child
        tables, Related and Postings. Meta.tabs and the Controls tab order name these."""
        tables = [child.name for child in self.registry.children if child.parent is model]
        return ['activity', 'details', 'revisions', *tables, 'related', 'postings']

    def check_layout(self, model, _, value):
        tabs = default_tab = panel = None
        if isinstance(value, dict):
            # The record tab order and what the side panel hides ride along with the sections; a plain list is a
            # layout with neither.
            if not {'sections'} <= set(value) <= {'sections', 'tabs', 'default_tab', 'panel'}:
                raise ValueError('Send the layout as a list of sections, or as sections with tabs, default_tab and panel.')
            panel = value.get('panel')
            tabs, default_tab = value.get('tabs', []), value.get('default_tab')
            known = self.tab_names(model)
            if not isinstance(tabs, list) or any(name not in known for name in tabs) or len(set(tabs)) != len(tabs):
                raise ValueError(f'List record tabs of {model.__name__} once each, from {", ".join(known)}.')
            if default_tab is not None and (default_tab not in known or (tabs and default_tab not in tabs)):
                raise ValueError('Open records on one of the record tabs.')
            value = value['sections']
        sections = self.check_sections(model, value)
        if panel is not None:
            panel = self.check_panel(model, sections, panel)
        if not (tabs or default_tab or panel is not None):
            return sections
        return {'sections': sections, **({'tabs': list(tabs), 'default_tab': default_tab} if tabs or default_tab else {}),
                **({'panel': panel} if panel is not None else {})}

    def check_panel(self, model, sections, value):
        """What a list's side panel hides: section titles, fields, tabs and blocks of this layout, by kind. An empty
        object shows everything, overriding the app's panel=False flags and Meta.panel. Display only."""
        if not isinstance(value, dict) or not set(value) <= set(PANEL_KINDS):
            raise ValueError(f'Send what the side panel hides as {", ".join(PANEL_KINDS)}, each a list of names.')
        known = {'sections': {section['title'] for section in sections},
                 'fields': {item['name'] for section in sections for item in section['fields']} | {name for name, field in self.fields(model).items() if not field.hidden},
                 'tabs': set(self.tab_names(model)) - {'details'},
                 'blocks': set(PANEL_BLOCKS) - {'related'}}
        out = {}
        for kind, names in value.items():
            if not isinstance(names, list) or len(set(map(str, names))) != len(names) or any(type(name) is not str or name not in known[kind] for name in names):
                raise ValueError(f'Hide {kind} of {model.__name__} in the side panel once each, from {", ".join(sorted(known[kind]))}.')
            if names:
                out[kind] = sorted(names)
        return out

    def check_sections(self, model, value):
        fields = self.fields(model)
        placeable = {name for name, field in fields.items() if not field.hidden}
        if not isinstance(value, list) or not 1 <= len(value) <= 12:
            raise ValueError('Keep between 1 and 12 sections.')
        seen, out = set(), []
        for section in value:
            # collapsible and collapsed are optional: layouts saved before them have sections that always show.
            if not isinstance(section, dict) or not {'title', 'fields'} <= set(section) <= {'title', 'fields', 'collapsible', 'collapsed', 'flow'} \
                    or not isinstance(section['fields'], list):
                raise ValueError('Send each section as a title and a list of fields, and optionally collapsible, collapsed and flow.')
            title = section['title']
            if not isinstance(title, str) or not title.strip() or len(title) > 60:
                raise ValueError('Give every section a title of at most 60 characters.')
            collapsible, collapsed = section.get('collapsible', False), section.get('collapsed', False)
            if type(collapsible) is not bool:
                raise ValueError(f'Section {title.strip()}: use true or false for Can collapse.')
            if not starts_valid(collapsed):
                raise ValueError(f'Section {title.strip()}: Starts must be open (false), closed (true) or closed when empty ("empty").')
            if not collapsible and collapsed is not False:
                raise ValueError(f"Section {title.strip()} can't collapse, so it starts open. Turn on Can collapse first.")
            if section.get('flow', 'rows') not in FLOWS:
                raise ValueError(f'Section {title.strip()}: fill fields in rows ("rows") or down each column ("columns").')
            items = []
            for item in section['fields']:
                if not isinstance(item, dict) or set(item) != {'name', 'width'} or item['name'] not in placeable or item['width'] not in (6, 12):
                    raise ValueError(f'Place fields of {model.__name__} by name, with a width of 6 or 12.')
                if item['name'] in seen:
                    raise ValueError(f'{item["name"]} is placed twice. Put each field in one place.')
                seen.add(item['name'])
                items.append({'name': item['name'], 'width': item['width']})
            if not items:
                raise ValueError('Every section needs at least one field, so section ids stay in place.')
            out.append({'title': title.strip(), 'fields': items, **fold(section)})
        return out

    def check_list(self, model, _, value):
        stored = {name for name, field in self.fields(model).items() if field.stored and field.kind != 'reference'}
        # open_in, list_tabs and list_tab are optional: list views saved before them keep the app's choice.
        optional = {'open_in', 'list_tabs', 'list_tab'}
        if not isinstance(value, dict) or not {'columns', 'page_size', 'sort'} <= set(value) <= {'columns', 'page_size', 'sort', *optional}:
            raise ValueError('Send columns, page_size and sort, and optionally open_in, list_tabs and list_tab.')
        columns = value['columns']
        if not isinstance(columns, list) or not 1 <= len(columns) <= 8 or any(name not in stored for name in columns) or len(set(columns)) != len(columns):
            raise ValueError(f'Choose 1 to 8 columns of {model.__name__}, each once.')
        if value['page_size'] not in PAGE_SIZES or type(value['page_size']) is not int:
            raise ValueError('Show 25, 50 or 100 rows per page.')
        sort = value['sort']
        if not isinstance(sort, str) or sort.removeprefix('-') not in {'id', *stored}:
            raise ValueError('Sort by a field of the model, or -id for newest first.')
        if 'open_in' in value and value['open_in'] not in OPEN_IN:
            raise ValueError('Open records in the full page or a side panel.')
        if 'list_tab' in value and 'list_tabs' not in value:
            raise ValueError('Send list_tabs with list_tab: the tab the list opens on is one of its values.')
        if 'list_tabs' in value:
            # The same rules as Meta.list_tabs and Meta.list_tab, over this instance's fields.
            self.list_tabs(model, value)
        return {'columns': list(columns), 'page_size': value['page_size'], 'sort': sort,
                **{key: value[key] for key in ('open_in', 'list_tabs', 'list_tab') if key in value}}

    def list_tabs(self, model, value):
        """The list tabs a saved list view sets, as list_tabs_for gives them; ValueError when they can't work."""
        class Options:
            list_tabs, list_tab = value['list_tabs'], value.get('list_tab', 'all')
        try:
            return list_tabs_for(model, Options, self.fields(model))
        except ValueError as exc:
            raise ValueError(str(exc).replace(f'{model.__name__}.Meta.', 'The list view ')) from None

    def check_naming(self, model, _, value, saved=False):
        from .sequences import SERIES_ID, check_order
        if not isinstance(value, list) or not 1 <= len(value) <= 20:
            raise ValueError('Keep between 1 and 20 series.')
        series = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {'id', 'label', 'format', 'when'} or not all(isinstance(item[key], str) for key in item):
                raise ValueError('Send each series as id, label, format and when.')
            if not SERIES_ID.fullmatch(item['id']):
                raise ValueError('Series ids use lowercase letters, digits and underscores.')
            if not item['label'].strip() or len(item['label']) > 60:
                raise ValueError('Give every series a name of at most 60 characters.')
            series.append({'id': item['id'], 'label': item['label'].strip(), 'format': item['format'], 'when': item['when'].strip()})
        check_order(series)
        rules = self.compile_naming(model, series)
        if rules.skipped and not saved:
            raise ValueError(rules.skipped[0]['error'])
        return series

    def compile_naming(self, model, series):
        from .expressions import ConditionError
        from .sequences import compile_naming
        try:
            return compile_naming(model, model.Meta.naming, series)
        except ConditionError as exc:
            raise ValueError(str(exc)) from None
        except ValueError as exc:
            raise ValueError(str(exc).split(': ', 1)[-1]) from None

    # ---------- runtime ----------

    def apply(self, stored, problems):
        """Build the effective field, layout, list and naming values from saved overrides."""
        effective, defaults, choices, read_only, layouts, lists, tabs, panels = {}, {}, {}, {}, {}, {}, {}, {}
        naming = dict(self.file_naming)
        models = self.controls.models()
        for model in models.values():
            for name, field in self.fields(model).items():
                definition = getattr(field, 'instance', None)
                if definition and definition.get('required'):
                    effective.setdefault(model, {})[name] = {'required': True}
        for row in stored:
            kind, key = row['kind'], row['key']
            try:
                value = json.loads(row['value'])
                checked = self.check(kind, key, value, saved=True)
            except (ValueError, KeyError, TypeError) as exc:
                problems.append({'kind': kind, 'key': key, 'error': str(exc)})
                if kind == 'field':
                    try:
                        model, name = self.target(kind, key)
                        read_only.setdefault(model, set()).add(name)
                    except ValueError:
                        pass
                continue
            if checked is None:
                continue
            model, name = self.target(kind, key)
            if kind == 'field':
                field = model.__declared_fields__[name]
                props = {**effective.get(model, {}).get(name, {}), **checked}
                effective.setdefault(model, {})[name] = props
                if props.get('read_only'):
                    read_only.setdefault(model, set()).add(name)
                if props.get('options_add'):
                    choices[field] = tuple(props['options_add'])
                if props.get('default') is not None:
                    defaults.setdefault(model, {})[name] = props['default']
            elif kind == 'layout':
                layouts[model] = sections_of(checked)
                tabs[model] = tabs_of(checked)
                if panel_of(checked) is not None:
                    panels[model] = panel_of(checked)
            elif kind == 'list':
                lists[model] = checked
            else:
                rules = self.compile_naming(model, checked)
                for skipped in rules.skipped:
                    problems.append({'kind': 'naming', 'key': key, 'error': f'Series {skipped["id"]} is skipped: {skipped["error"]}'})
                naming[model] = rules
        with activated(self.business.database, Layer(active_layer().fields, choices)):
            for model, values in defaults.items():
                defaults[model] = {name: model.__declared_fields__[name].validate(value) for name, value in values.items()}
        self.effective, self.defaults, self.choices, self.read_only = effective, defaults, MappingProxyType(choices), read_only
        self.layouts, self.lists, self.naming, self.tabs, self.panels = layouts, lists, MappingProxyType(naming), tabs, panels

    def layout(self, model):
        """The effective layout. Fields placed nowhere (instance fields, fields an app adds later) go to the
        last section of a Controls layout. Without one, they follow the app file's rule: a declared
        Meta.layout leaves them to an Other group, and the default Details group takes every field."""
        override = self.layouts.get(model)
        sections = override or sections_of(self.file_value('layout', model))
        fields = self.fields(model)
        # The standard title and a name no naming fills are not form fields (evlet/naming.py).
        placeable = [name for name, field in fields.items() if not field.hidden and not form_hidden(field)]
        placed = {item['name'] for section in sections for item in section['fields']}
        result = [{'title': section['title'], 'fields': [item for item in section['fields'] if item['name'] in placeable],
                   **fold(section)} for section in sections]
        rest = [{'name': name, 'width': width(fields[name])} for name in placeable if name not in placed]
        declared = getattr(getattr(model, 'Meta', None), 'layout', None) is not None
        if rest and not override and declared and result[-1]['title'] != 'Other':
            result.append({'title': 'Other', 'fields': []})
        result[-1]['fields'] += rest
        return result

    def merge(self, entry, model, readable, writable):
        """Merged field, layout and list values for one model entry of /api/schema."""
        from .ui.metadata import field_schema
        for name, field in model.__declared_fields__.items():
            if getattr(field, 'instance', None) and name in readable:
                entry['fields'].append({**field_schema(field), 'read_only': name not in writable, 'stored': True, 'compute': None,
                                        'initial': None, 'applies_when': None, 'required_when': None, 'locked_when': None})
        props = self.effective.get(model, {})
        for field in entry['fields']:
            changed = props.get(field['name'])
            if not changed:
                continue
            for prop in ('label', 'help_text', 'tooltip', 'placeholder', 'hidden', 'required'):
                if prop in changed:
                    field[prop] = changed[prop]
            if 'default' in changed:
                field['default'], field['default_kind'] = changed['default'], 'static'
            if 'options_add' in changed or 'options_hidden' in changed:
                retired = changed.get('options_hidden', [])
                field['choices'] = [item for item in field['choices'] if item not in retired] + changed.get('options_add', [])
                field['retired_choices'] = list(retired)
        if model in self.layouts or any(getattr(field, 'instance', None) for field in model.__declared_fields__.values()):
            # Sections with nothing this role can read are left out, titles included.
            entry['layout'] = [{'label': section['title'], 'fields': [item['name'] for item in section['fields'] if item['name'] in readable],
                                'wide': [item['name'] for item in section['fields'] if item['width'] == 12 and item['name'] in readable],
                                'collapsible': section.get('collapsible', False), 'collapsed': section.get('collapsed', False),
                                'flow': section.get('flow', 'rows')}
                               for section in self.layout(model) if any(item['name'] in readable for item in section['fields'])]
            wide = {name for section in entry['layout'] for name in section['wide']}
            for field in entry['fields']:
                field['wide'] = field['name'] in wide
        if model in self.tabs:
            entry['tabs'], entry['default_tab'] = self.tabs[model]
        # What a list's side panel hides: the Controls choice when there is one, else the app's. The app's order stays.
        panel = {**self.panels[model], **({'order': entry['panel']['order']} if 'order' in entry.get('panel', {}) else {})} \
            if model in self.panels else entry.get('panel')
        panel = {kind: [name for name in names if name in readable] if kind == 'fields' else names for kind, names in (panel or {}).items()}
        panel = {kind: names for kind, names in panel.items() if names}
        if panel:
            entry['panel'] = panel
        else:
            entry.pop('panel', None)
        if model in self.lists:
            value = self.lists[model]
            entry['list_fields'] = ['id', *(name for name in value['columns'] if name in readable)]
            entry['page_size'] = value['page_size']
            if 'open_in' in value:
                entry['open_in'] = value['open_in']
            if 'list_tabs' in value:
                # A saved choice that no longer fits (its field was removed since) keeps the app's tabs.
                try:
                    tabs = self.list_tabs(model, value)
                except ValueError:
                    tabs = entry.get('list_tabs')
                entry.pop('list_tabs', None)
                if tabs:
                    entry['list_tabs'] = tabs
            if value['sort'].removeprefix('-') in readable or value['sort'] in ('id', '-id'):
                entry['order_by'] = value['sort']

    def default_order(self, policy, user, model):
        """The list's order when the client sends none: the list view's sort when this role can read it."""
        sort = self.lists.get(model, {}).get('sort', '-id')
        return sort if sort.removeprefix('-') in {'id', *policy.fields(user, model)} else '-id'

    def prepare(self, model, values):
        """Values for a new record: defaults set on this instance for fields the caller left out."""
        return {**{name: value for name, value in self.defaults.get(model, {}).items() if name not in values}, **values}

    def enforce(self, model, values, saved):
        """Required fields and retired options for an API write. `saved` is None on create."""
        errors = {}
        for name, props in self.effective.get(model, {}).items():
            value = values.get(name)
            if props.get('required') and value in (None, ''):
                errors[name] = 'This field is required'
            elif value in props.get('options_hidden', ()) and (saved is None or saved.get(name) != value):
                errors[name] = RETIRED
        if errors:
            raise ValidationError(errors)

    # ---------- instance fields ----------

    def backup(self):
        """A copy of a SQLite database next to it, in backups/. PostgreSQL is backed up by its operator."""
        database = self.business.database
        if getattr(database, 'path', None) is None or not hasattr(database, 'must_exist'):
            return None
        from .cli.install import snapshot
        folder = database.path.parent / 'backups'
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
        snapshot(database.path, target)
        return target.name

    def check_new(self, model, body):
        if not isinstance(body, dict) or set(body) - {'name', 'type', 'label', 'help_text', 'tooltip', 'required', 'default', 'options', 'target'}:
            raise ValidationError({'_form': 'Use name, type, label, help_text, tooltip, required, default, options and target.'})
        errors = {}
        raw = body.get('name')
        name = raw if isinstance(raw, str) and raw.startswith('x_') else 'x_' + raw if isinstance(raw, str) else ''
        if not FIELD_NAME.fullmatch(name):
            errors['name'] = 'Use lowercase letters, digits and underscores, 40 characters at most with x_.'
        else:
            with self.business.database._connection() as connection:
                taken = {column.lower() for column in self.business.database.columns(connection, self.registry.name_for(model))}
            # Names compare ignoring case, and against every column, a Reference's <name>_model and <name>_id included.
            if name in taken | {key.lower() for key in model.__declared_fields__} | set(FRAMEWORK_COLUMNS):
                errors['name'] = f'{model.__name__} already has a field called {name}.'
        kind = body.get('type')
        if kind not in TYPES:
            errors['type'] = 'Choose ' + ', '.join(TYPES[:-1]) + ' or Link.'
        label = body.get('label')
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            errors['label'] = 'Give the field a label.'
        help_text = body.get('help_text', '')
        if not isinstance(help_text, str) or len(help_text) > 200:
            errors['help_text'] = 'Use at most 200 characters.'
        tooltip = body.get('tooltip', '')
        if not isinstance(tooltip, str) or len(tooltip) > 200:
            errors['tooltip'] = 'Use at most 200 characters.'
        if type(body.get('required', False)) is not bool:
            errors['required'] = 'Use true or false.'
        definition = {'type': kind, 'label': label.strip() if isinstance(label, str) else '', 'help_text': help_text.strip() if isinstance(help_text, str) else '',
                      **({'tooltip': tooltip.strip()} if isinstance(tooltip, str) and tooltip.strip() else {}),
                      'required': body.get('required', False), 'default': None}
        if kind == 'Select':
            options = body.get('options')
            if not isinstance(options, list) or len(options) < 2 or len(set(options)) != len(options) or any(not isinstance(item, str) or not OPTION.fullmatch(item) for item in options):
                errors['options'] = 'Give at least two options, each once, in lowercase letters, digits and underscores.'
            definition['options'] = options
        if kind == 'Link':
            target = body.get('target')
            if target not in {self.registry.name_for(item) for item in self.controls.models().values()}:
                errors['target'] = 'Choose a model to link to.'
            definition['target'] = target
        if errors:
            raise ValidationError(errors)
        field = build_field(definition, self.registry)
        if body.get('default') is not None:
            if kind == 'Link':
                raise ValidationError({'default': "Links don't take a default."})
            try:
                definition['default'] = field.serialize(field.validate(body['default']))
            except (ValueError, TypeError) as exc:
                raise ValidationError({'default': str(exc)}) from None
            field = build_field(definition, self.registry)
        return name, definition, field

    def add_field(self, actor, model_name, body):
        model = self.instance_model(model_name)
        name, definition, field = self.check_new(model, body)
        table = self.registry.name_for(model)
        taken = ValidationError({'name': f'{model.__name__} already has a field called {name}.'})
        with self.engine:
            with self.business.database._connection() as connection:
                rows = connection.execute(f'SELECT count(*) FROM {identifier(table)}').fetchone()[0]
            if definition['required'] and definition['default'] is None and rows:
                raise ValidationError({'default': f'{rows} records already exist. Give a default for them, or leave the field optional.'})
            backup = self.backup()
            try:
                version = self.insert_field(actor, model, table, name, definition, field, backup)
            except (sqlite3.Error, ConflictError):
                # Another process added the same field first.
                with self.business.database._connection() as connection:
                    if name in self.business.database.columns(connection, table):
                        raise taken from None
                raise
        self.controls.refresh()
        return {'name': name, 'backup': backup, 'version': version}

    def insert_field(self, actor, model, table, name, definition, field, backup):
        with self.business.database.transaction() as tx:
            connection = tx.connection
            if connection.execute('SELECT 1 FROM evlet_custom_fields WHERE model=? AND name=?', (table, name)).fetchone():
                raise ValidationError({'name': f'{model.__name__} already has a field called {name}.'})
            rows = connection.execute(f'SELECT count(*) FROM {identifier(table)}').fetchone()[0]
            if definition['required'] and definition['default'] is None and rows:
                raise ValidationError({'default': f'{rows} records already exist. Give a default for them, or leave the field optional.'})
            field.name = name
            add_column(connection, table, name, field)
            if definition['required'] and definition['default'] is not None and rows:
                connection.execute(f'UPDATE {identifier(table)} SET {identifier(name)}=?', (field.to_storage(field.default),))
            connection.execute('INSERT INTO evlet_custom_fields(model,name,definition,created_by,created_at) VALUES (?,?,?,?,?)',
                               (table, name, json.dumps(definition, sort_keys=True), actor.username, int(time.time())))
            self.grant_new_field(connection, model, name)
            self.controls.audit(connection, actor.username, 'Models', f'{model.__name__}.{name}',
                                f'Field added ({definition["type"]})' + (f' · backup {backup}' if backup else ''), None, definition)
            return self.controls.publish(tx)

    def remove_field(self, actor, model_name, name):
        model = self.instance_model(model_name)
        if self.drop:
            raise AccessError(409, self.drop)
        table = self.registry.name_for(model)
        with self.engine:
            with self.business.database._connection() as connection:
                row = connection.execute('SELECT definition FROM evlet_custom_fields WHERE model=? AND name=?', (table, name)).fetchone()
            if row is None:
                raise AccessError(404, 'Field not found')
            backup = self.backup()
            with self.business.database.transaction() as tx:
                connection = tx.connection
                cleaned = self.cleanup(connection, model, name)
                connection.execute(f'ALTER TABLE {identifier(table)} DROP COLUMN {identifier(name)}')
                connection.execute('DELETE FROM evlet_custom_fields WHERE model=? AND name=?', (table, name))
                detail = 'Field removed' + (f' with {cleaned} override{"s" if cleaned != 1 else ""} cleaned' if cleaned else '')
                self.controls.audit(connection, actor.username, 'Models', f'{model.__name__}.{name}',
                                    detail + (f' · backup {backup}' if backup else ''), json.loads(row[0]), None)
                version = self.controls.publish(tx)
        self.controls.refresh()
        return {'name': name, 'backup': backup, 'version': version}

    @staticmethod
    def grant_new_field(connection, model, name):
        """A new instance field is readable and writable like the record for desk roles. A desk role's permission
        override that lists fields gets it too, so it is hidden from staff only when an admin unticks it there.
        Portal roles get it only when an admin ticks it."""
        pattern = '%|' + model.__name__.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        desk = {row['id'] for row in connection.execute("SELECT id FROM evlet_roles WHERE realm='desk'")}
        for row in connection.execute("SELECT key, value FROM evlet_overrides WHERE kind='perm' AND key LIKE ? ESCAPE '\\'", (pattern,)).fetchall():
            if row['key'].partition('|')[0] not in desk:
                continue
            value = json.loads(row['value'])
            if not isinstance(value, dict) or 'read' not in value.get('ops', []):
                continue
            changed = dict(value)
            if isinstance(value.get('read'), list) and name not in value['read']:
                changed['read'] = [*value['read'], name]
            if isinstance(value.get('write'), list) and name not in value['write'] and {'create', 'update'} & set(value['ops']):
                changed['write'] = [*value['write'], name]
            if changed != value:
                connection.execute("UPDATE evlet_overrides SET value=? WHERE kind='perm' AND key=?", (json.dumps(changed, sort_keys=True), row['key']))

    def instance_model(self, model_name):
        model = self.controls.models().get(model_name)
        if model is None:
            raise AccessError(404, 'Model not found')
        return model

    def cleanup(self, connection, model, name):
        """Take a field out of every override that names it. Returns how many overrides changed."""
        from .expressions import ConditionError, parse
        from .sequences import parse_series
        model_name, changed = model.__name__, 0

        def write(kind, key, value):
            nonlocal changed
            changed += 1
            if value is None:
                connection.execute('DELETE FROM evlet_overrides WHERE kind=? AND key=?', (kind, key))
            else:
                connection.execute('UPDATE evlet_overrides SET value=? WHERE kind=? AND key=?', (json.dumps(value, sort_keys=True), kind, key))

        escaped = model_name.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        rows = connection.execute("SELECT kind, key, value FROM evlet_overrides WHERE (kind='field' AND key=?) OR (kind IN ('layout','list','naming') AND key=?) "
                                  "OR (kind='perm' AND key LIKE ? ESCAPE '\\')", (f'{model_name}.{name}', model_name, f'%|{escaped}')).fetchall()
        fields = {key: field for key, field in model.__declared_fields__.items() if key != name}
        for kind, key, raw in ((row[0], row[1], row[2]) for row in rows):
            value = json.loads(raw)
            if kind == 'field':
                write(kind, key, None)
            elif kind == 'layout' and (any(item['name'] == name for section in sections_of(value) for item in section['fields'])
                                       or name in (panel_of(value) or {}).get('fields', ())):
                # Sections left empty go, so the rest keep their places; with none left the layout goes back to the file.
                kept = [{**section, 'fields': [item for item in section['fields'] if item['name'] != name]} for section in sections_of(value)]
                kept = [section for section in kept if section['fields']]
                panel = panel_of(value)
                if panel is not None:
                    # The side panel stops naming the field and any section that went with it.
                    titles = {section['title'] for section in kept}
                    panel = {kind_: names for kind_, names in {**panel, 'fields': [item for item in panel.get('fields', []) if item != name],
                             'sections': [item for item in panel.get('sections', []) if item in titles]}.items() if names}
                    value = {**value, 'panel': panel}
                write(kind, key, ({**value, 'sections': kept} if isinstance(value, dict) else kept) if kept else None)
            elif kind == 'list' and (name in value['columns'] or value['sort'].removeprefix('-') == name):
                columns = [item for item in value['columns'] if item != name]
                write(kind, key, {**value, 'columns': columns, 'sort': '-id' if value['sort'].removeprefix('-') == name else value['sort']} if columns else None)
            elif kind == 'perm' and any(name in (value.get(part) or ()) for part in ('read', 'write')):
                read = None if value['read'] is None else [item for item in value['read'] if item != name]
                write_fields = None if value['write'] is None else [item for item in value['write'] if item != name]
                write(kind, key, {'ops': [], 'read': None, 'write': None, 'scope': None} if read == [] else {**value, 'read': read, 'write': write_fields})
            elif kind == 'naming':
                kept = []
                for item in value:
                    try:
                        uses = item['when'] and name in parse(item['when'], {**fields, name: model.__declared_fields__[name]}).fields
                    except ConditionError:
                        uses = False
                    if not uses and ('field', name) not in parse_series(item['format']):
                        kept.append(item)
                if kept != value:
                    # Without its last series the list could leave records unnamed, so it goes back to the file.
                    write(kind, key, kept if value[-1] in kept else None)
        return changed

    # ---------- counters and Try it ----------

    def naming_model(self, model_name):
        model = self.controls.models().get(model_name)
        if model is None or model not in self.naming:
            raise AccessError(404, 'Model not found')
        return model, self.naming[model]

    def counters(self, model_name):
        from .sequences import split
        model, rules = self.naming_model(model_name)
        table = self.registry.name_for(model)
        with self.business.database._connection() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT scope_key AS prefix, \"last\" AS value, last_name, how, note, changed_by, changed_at FROM evlet_sequence "
                "WHERE model=? AND field=? AND period='' ORDER BY changed_at DESC, scope_key", (table, rules.field))] \
                if self.counter_table(connection) else []
        for row in rows:
            match = self.series_of(rules, row['prefix'])
            row['series'] = match['id'] if match else None
            width = split(match['parts'])[1] if match else 4
            row['next'] = row['prefix'] + str(row['value'] + 1).zfill(width) + (match['suffix'] if match else '')
            row['issued'] = issued(row['prefix'], row['last_name'])
        return {'field': rules.field, 'items': rows}

    def counter_table(self, connection):
        if isinstance(connection, sqlite3.Connection):
            return connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_sequence'").fetchone() is not None
        return connection.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='evlet_sequence'").fetchone() is not None

    @staticmethod
    def series_of(rules, prefix):
        """The series whose prefix pattern matches a counter prefix, with its literal suffix."""
        from .sequences import split
        for item, parts, _ in rules.series:
            head, _, tail = split(parts)
            pattern = ''.join(re.escape(part) if isinstance(part, str) else r'\d{4}' if part[1:] == ('YYYY',) else r'\d{2}' if part[0] == 'date' else r'[^\s]*' for part in head)
            if re.fullmatch(pattern, prefix):
                return {**item, 'parts': parts, 'suffix': ''.join(part for part in tail if isinstance(part, str)) if all(isinstance(part, str) for part in tail) else ''}
        return None

    def set_counter(self, actor, model_name, body):
        model, rules = self.naming_model(model_name)
        if not isinstance(body, dict) or set(body) != {'prefix', 'value', 'note'}:
            raise ValidationError({'_form': 'Send prefix, value and note.'})
        prefix, value, note = body['prefix'], body['value'], body['note']
        if not isinstance(prefix, str) or self.series_of(rules, prefix) is None:
            raise ValidationError({'prefix': 'No series of this model uses this prefix.'})
        if type(value) is not int or not 0 <= value <= 99999999:
            raise ValidationError({'value': 'Enter a whole number from 0 to 99999999.'})
        if not isinstance(note, str) or not note.strip() or len(note) > 200:
            raise ValidationError({'note': 'Give a reason. It is saved in the audit log.'})
        table = self.registry.name_for(model)
        with self.business.database.transaction() as tx:
            connection = tx.connection
            from .sequences import upgrade_counters
            upgrade_counters(connection)
            row = connection.execute("SELECT \"last\", last_name FROM evlet_sequence WHERE model=? AND field=? AND scope_key=? AND period=''",
                                     (table, rules.field, prefix)).fetchone()
            floor = issued(prefix, row[1]) if row else None
            if floor is not None and value < floor:
                raise ValidationError({'value': f'Lower than {row[1]}, which already exists. The next names would clash with existing records.'})
            connection.execute("INSERT INTO evlet_sequence (model, field, scope_key, period, \"last\", how, note, changed_by, changed_at) "
                               "VALUES (?, ?, ?, '', ?, 'set', ?, ?, ?) ON CONFLICT (model, field, scope_key, period) DO UPDATE SET "
                               "\"last\" = excluded.\"last\", how = 'set', note = excluded.note, changed_by = excluded.changed_by, changed_at = excluded.changed_at",
                               (table, rules.field, prefix, value, note.strip(), actor.username, int(time.time())))
            before = row[0] if row else 0
            self.controls.audit(connection, actor.username, 'Naming', f'{model.__name__} · {prefix}', f'Counter {before} → {value} · {note.strip()}',
                                {'value': before}, {'value': value})
        return self.counters(model_name)

    def try_name(self, model_name, body):
        from .documents import host_today
        from .expressions import ConditionError, parse
        from .sequences import NamingRules, check_order, parse_series, preview_name
        model, rules = self.naming_model(model_name)
        if not isinstance(body, dict) or set(body) - {'values', 'series'} or not isinstance(body.get('values', {}), dict):
            raise ValidationError({'_form': 'Send values and, to try a draft, series.'})
        fields = model.__declared_fields__
        checks = []
        if 'series' in body:
            series = body['series']
            if not isinstance(series, list) or not series or any(not isinstance(item, dict) or not isinstance(item.get('format'), str) or not isinstance(item.get('when'), str) for item in series):
                raise ValidationError({'series': 'Send each series with its format and condition.'})
            series = [{**item, 'id': str(item.get('id') or f'series_{index + 1}')} for index, item in enumerate(series)]
            for item in series:
                problem = {}
                try:
                    parts = parse_series(item['format'])
                    for part in parts:
                        if isinstance(part, tuple) and part[0] == 'field' and part[1] not in fields:
                            raise ValueError(f"{{{part[1]}}} isn't a token or a field on {model.__name__}.")
                except ValueError as exc:
                    problem['format'] = str(exc)
                if item['when'].strip():
                    try:
                        parse(item['when'], fields)
                    except ConditionError as exc:
                        problem['when'] = str(exc)
                checks.append({'id': item.get('id'), **problem})
            usable = [{'id': item['id'], 'label': str(item.get('label') or ''), 'format': item['format'], 'when': item['when'].strip()}
                      for index, (item, check) in enumerate(zip(series, checks)) if len(check) == 1]
            try:
                check_order(usable)
                rules = NamingRules(model, rules.field, rules.date_field, usable)
                if not rules.series:
                    raise ValueError('No series can be used yet.')
            except ValueError as exc:
                return {'checks': checks, 'match': None, 'name': None, 'error': str(exc)}
        values, errors = {}, {}
        for name, value in body.get('values', {}).items():
            field = fields.get(name)
            if field is None or value in (None, ''):
                continue
            try:
                values[name] = field.validate(value)
            except (ValueError, TypeError) as exc:
                errors[name] = str(exc)
        resource, today = self.registry.name_for(model), host_today(self.business)
        with self.business.database._connection() as connection:
            reader = connection if self.counter_table(connection) else None
            names = {item['id']: preview_name(reader, resource, rules, parts, values, today) for item, parts, _ in rules.series}
        item, _ = rules.match(values)
        for check in checks:
            check['next'] = names.get(str(check['id']))
        return {'checks': checks or [{'id': key, 'next': value} for key, value in names.items()], 'match': item['id'], 'label': item['label'],
                'name': names[item['id']], 'errors': errors, 'error': None}

    # ---------- state ----------

    def model_state(self, model):
        """What the Models and Naming pages show for one model."""
        fields = []
        for name, field in self.fields(model).items():
            fields.append({'name': name, 'type': field.kind, 'custom': bool(getattr(field, 'instance', None)), 'editable': self.editable(model, name, field),
                           'stored': field.stored, 'unique': field.unique, 'target': self.registry.name_for(field.target) if isinstance(field, Link) else None,
                           'width': width(field)})
        table = self.registry.name_for(model)
        with self.business.database._connection() as connection:
            count = connection.execute(f'SELECT count(*) FROM {identifier(table)}').fetchone()[0]
        rules = self.naming.get(model)
        # Record page tabs the form layout can order: the record's own tabs, then each child table with its label.
        labels = {child.name: child.label for child in self.registry.children if child.parent is model}
        tabs = [{'name': name, 'label': labels.get(name, name.capitalize())} for name in self.tab_names(model)]
        return {'table': table, 'records': count, 'fields_detail': fields, 'tabs': tabs,
                'naming': None if rules is None else {'field': rules.field, 'date_field': rules.date_field}}


def issued(prefix, last_name):
    """The counter number inside the last issued name, or None."""
    if not last_name or not last_name.startswith(prefix):
        return None
    digits = re.match(r'\d+', last_name[len(prefix):])
    return int(digits.group()) if digits else None
