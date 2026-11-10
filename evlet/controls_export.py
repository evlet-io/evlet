"""Export as code: saved overrides as Python, grouped by the file each one belongs in.

Paste the code into the app or the instance, deploy it, then reset the overrides. Values that have no
Python form here (a callable from models.py, say) are written as ... and must be copied from the file.
"""

from decimal import Decimal
import inspect
from pathlib import Path

from .controls import SCHEDULE, SCOPES, SETTINGS
from .models import storage_name

PROPS = ('label', 'help_text', 'tooltip', 'placeholder', 'hidden', 'required', 'read_only', 'default')
UNKNOWN = '...'
# Setting prefixes other than security, as config.py names and what they cover.
GROUPS = {'mcp': ('assistants', 'AI assistant settings (Controls, MCP and AI)'), 'mail': ('mail', 'Email settings (Controls, Email)')}


def literal(value):
    """Python source for a value, or ... when it has none."""
    if value is None or isinstance(value, (bool, int, float, str, Decimal)):
        return repr(value)
    if isinstance(value, type):
        return value.__name__
    if inspect.isfunction(value) and value.__name__.isidentifier():
        return value.__qualname__ if '<' not in value.__qualname__ else value.__name__
    if isinstance(value, tuple):
        return '(' + ', '.join(map(literal, value)) + (',)' if len(value) == 1 else ')')
    if isinstance(value, list):
        return '[' + ', '.join(map(literal, value)) + ']'
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return '{' + ', '.join(f'{key!r}: {literal(item)}' for key, item in value.items()) + '}'
    from .declarations import Ref
    if isinstance(value, Ref):
        return f'Ref({value.path!r})'
    return UNKNOWN


def call(name, args=(), kwargs=None):
    return f"{name}({', '.join([*map(literal, args), *(f'{key}={literal(value)}' for key, value in (kwargs or {}).items())])})"


def block_code(block, sections, main=None):
    """Python source for one Meta.layout block; a section as `sections` (a Controls layout, by title) has it.
    `main` is the title that names the Main block's fields (the model's label)."""
    from .forms import Chart
    from .meaning import Related
    from .ui.metadata import Html, Main, Section
    if isinstance(block, Main):
        section = sections.get(main)
        fields = tuple(item['name'] for item in section['fields']) if section is not None else ()
        if not fields and not block.tables:
            return None
        return call('Main', (fields, *block.tables))
    if isinstance(block, (Section, tuple, list)):
        section = sections.get(block.title if isinstance(block, Section) else block[0])
        if section is None:
            return None
        options = {**({'collapsed': section.get('collapsed', False)} if section.get('collapsible') and section.get('collapsed') else {}),
                   **({} if section.get('collapsible') else {'collapsible': False}), **({'flow': 'columns'} if section.get('flow') == 'columns' else {}),
                   **({'description': block.description} if isinstance(block, Section) and block.description is not None else {})}
        return call('Section', (section['title'], tuple(item['name'] for item in section['fields'])), options)
    if isinstance(block, str):
        return repr(block)
    if isinstance(block, Html):
        return call('Html', (block.content,))
    model = block.model if isinstance(block.model, str) else storage_name(block.model)
    if isinstance(block, Related):
        options = {'label': block.label, 'filters': block.filters, 'fields': block.fields or None, 'limit': block.limit,
                   'order': None if block.order == ('-id',) else block.order[0] if len(block.order) == 1 else block.order}
        return call('Related', (model, *((block.link,) if block.link else ())), {key: value for key, value in options.items() if value is not None})
    if isinstance(block, Chart):
        options = {'x': block.x, 'y': block.y, 'label': block.label, 'model': model, 'link': block.link, 'filters': block.filters}
        return call('Chart', (block.type,), {key: value for key, value in options.items() if value is not None})
    return UNKNOWN


def layout_code(model, sections):
    """Meta.layout with the app's blocks and the sections of a Controls layout, as Python source.

    A section the app places keeps its place; one the app does not place (added or renamed in Controls) goes last.
    A section Controls removed is left out."""
    from .ui.metadata import Main, Section, view_for
    layout = model.Meta.layout
    by_title = {section['title']: section for section in sections}
    placed = set()
    main = view_for(model)['label']

    def blocks(items, indent):
        lines = []
        for block in items:
            if isinstance(block, (Section, tuple, list)):
                placed.add(block.title if isinstance(block, Section) else block[0])
            if isinstance(block, Main):
                placed.add(main)
            text = block_code(block, by_title, main)
            if text is not None:
                lines.append(f'{indent}{text},')
        return lines
    lines = blocks(layout, '    ')
    lines += [f'    {block_code(Section(section["title"], ()), by_title)},' for section in sections if section['title'] not in placed]
    return 'layout = (\n' + '\n'.join(lines) + '\n)'


def field_line(name, kind, args, kwargs, props):
    """A field line with this instance's changes merged into the arguments models.py gives."""
    args, kwargs = list(args), dict(kwargs)
    kwargs.update({prop: props[prop] for prop in PROPS if prop in props})
    if props.get('options_add'):
        if 'choices' in kwargs:
            kwargs['choices'] = (*kwargs['choices'], *props['options_add'])
        elif args:
            args[0] = (*args[0], *props['options_add'])
    line = f'    {name} = {call(kind, args, kwargs)}'
    if UNKNOWN in line:
        line += '  # ... is set in code in models.py: copy it from there'
    if props.get('options_hidden'):
        line += f"  # hidden from new values on this instance: {', '.join(props['options_hidden'])}"
    return line


def instance_line(name, definition, props, registry):
    """An instance field as an app field. Apps can't declare x_ names, so rename it while moving it."""
    kind, args = definition['type'], []
    kwargs = {'label': definition['label']} | ({'help_text': definition['help_text']} if definition.get('help_text') else {}) \
        | ({'tooltip': definition['tooltip']} if definition.get('tooltip') else {})
    if kind == 'Select':
        args.append(tuple(definition['options']))
    if kind == 'Link':
        args.append(registry.models[definition['target']])
    kwargs |= {'LongText': {'max_length': 4000}, 'Decimal': {'scale': 4}}.get(kind, {})
    if definition.get('required'):
        kwargs['required'] = True
    if definition.get('default') is not None:
        kwargs['default'] = definition['default']
    return field_line(name, kind, args, kwargs, props) + '  # rename without x_ in the app'


class Exporter:
    def __init__(self, controls):
        self.controls, self.registry = controls, controls.registry
        self.models = controls.models()

    def app(self, model):
        """The app a model comes from and whether it uses the flat layout (the package is the app folder)."""
        app = self.controls.app_of.get(model.__name__)
        return app, any(item['id'] == app and item.get('layout') == 'flat' for item in self.controls.apps)

    def folder(self, model):
        """The package folder for permissions.py: apps/<app>[/<package>], or the module's package."""
        package = model.__module__.split('.')[0]
        app, flat = self.app(model)
        return package if app is None else f'apps/{app}' if flat else f'apps/{app}/{package}'

    def models_file(self, model, module=None, init=None):
        """The file that declares the model, such as apps/notes/models/item.py."""
        module = (module or model.__module__).split('.')
        if init is None:
            try:
                init = Path(inspect.getsourcefile(model)).name == '__init__.py'
            except TypeError:
                init = False
        tail = '/__init__.py' if init else '.py'
        app, flat = self.app(model)
        if app is None:
            return '/'.join(module) + tail
        return f'apps/{app}/' + '/'.join(module[1:] if flat else module) + tail

    def scope(self, role, model, value):
        if value is None:
            return 'None'
        file = self.controls.file_rule(role, model).scope
        if file is not None and self.controls.scope_id(file) == value:
            return literal(file)
        if value in SCOPES:
            return f'SCOPES[{value!r}]'
        if value == 'own_customer':
            # permissions.own_customer, as Controls compiles the preset: the customer model scopes its own id.
            return "own_customer(field='id')" if self.registry.name_for(model) == 'customer' else 'own_customer()'
        return literal(self.controls.scopes.get(value))

    def perm(self, key, value):
        role, model = self.controls.perm_key(key)
        fields = lambda names: literal(None if names is None else tuple(names))
        return (f'{self.folder(model)}/permissions.py',
                f"    ({role!r}, {model.__name__}): Rule(operations={literal(tuple(value['ops']))}, read_fields={fields(value['read'])}, "
                f"write_fields={fields(value['write'])}, scope={self.scope(role, model, value['scope'])}),")

    def model_class(self, name, fields, naming, meta=()):
        """class Model(Model): the changed field lines, the instance fields and the Meta options."""
        model = self.models[name]
        lines = [f'class {name}(Model):']
        declared = model.__declared_fields__
        for field, props in fields.items():
            definition = getattr(declared.get(field), 'instance', None)
            if definition:
                lines.append(instance_line(field, definition, props, self.registry))
            else:
                lines.append(field_line(field, type(declared[field]).__name__, *getattr(declared[field], 'arguments', ((), {})), props))
        if naming is not None or meta:
            lines += [''] * (len(lines) > 1) + ['    class Meta:']
        if naming is not None:
            file = model.Meta.naming
            lines += ['        naming = Naming(', f'            {file.field!r},']
            for series in naming:
                options = {'when': series['when']} if series['when'] else {}
                lines.append(f"            {call('Series', (series['format'],), options | {'label': series['label'], 'id': series['id']})},")
            if file.date_field:
                lines.append(f'            date_field={file.date_field!r},')
            lines.append('        )')
        lines += ['        ' + line for line in meta]
        return '\n'.join(lines)

    def layout(self, name, value):
        """Meta.layout as Section(...) lines."""
        from .customization import panel_of, sections_of, width
        model, sections = self.models[name], sections_of(value)
        declared = model.__declared_fields__
        lines = ['layout = (']
        for section in sections:
            options = {'collapsible': False} if not section.get('collapsible') else \
                {'collapsed': section['collapsed']} if section.get('collapsed', False) is not False else {}
            if section.get('flow') == 'columns':
                options['flow'] = 'columns'
            lines.append('    ' + call('Section', (section['title'], tuple(item['name'] for item in section['fields'])), options) + ',')
        lines.append(')')
        # Meta.layout has no field widths: a field's type sets them (LongText is full width).
        wide = [entry['name'] for section in sections for entry in section['fields']
                if entry['name'] in declared and entry['width'] != width(declared[entry['name']])]
        if wide:
            lines.append(f"# Widths have no Meta form; this instance changes them for: {', '.join(wide)}")
        # What the side panel hides is panel=False on the field, Section or Table, or Meta.panel.
        if panel_of(value):
            lines.append(f'# Side panel hides (panel=False or Meta.panel): {panel_of(value)!r}')
        if model in getattr(self.registry, 'layouts', {}):
            # A record layout keeps its blocks, with this instance's sections in their places.
            lines[:len(sections) + 2] = layout_code(model, sections).splitlines()
        return lines

    def list_view(self, value):
        """Meta.list_fields, open_in, list_tabs and list_tab; page size and sort stay as a note."""
        lines = [f"list_fields = {literal(tuple(name for name in value['columns'] if name != 'id'))}"]
        lines += [f'{key} = {value[key]!r}' for key in ('open_in', 'list_tabs', 'list_tab') if key in value]
        lines.append(f"# Page size {value['page_size']} and sort {value['sort']!r} have no Meta option; they stay instance overrides.")
        return lines

    def settings(self, values):
        """Security settings for config.py, allowed networks for routes.py and paused schedules beside the app's schedules."""
        instance = f'instances/{self.controls.system.instance_name() or "<name>"}'
        blocks = []
        # Security(...) in config.py sets the sign-in rules. The block keeps what config.py sets today, with the overrides on top.
        rules = ('session_hours', 'lockout', 'password_min')
        if any(f'security.{name}' in values for name in rules):
            from .auth import Security
            current, default = self.controls.business.auth.security, Security()
            security = {name: values.get(f'security.{name}', getattr(current, name)) for name in rules}
            security = {name: value for name, value in security.items() if value != getattr(default, name)}
            blocks.append((f'{instance}/config.py', '# Replaces any security = Security(...) in config.py. Restart, then reset these overrides.\n'
                           f"security = {call('Security', (), security)}"))
        if 'security.two_step_roles' in values:
            blocks.append((f'{instance}/config.py', '# Two-step roles are not read from config.py yet. Keep this override until they are.\n'
                           f"two_step_roles = {literal(tuple(values['security.two_step_roles']))}"))
        # Other settings keep their full keys, one block per prefix, since no config class takes them yet.
        for prefix, (name, what) in GROUPS.items():
            group = {key: value for key, value in values.items() if key in SETTINGS and key.startswith(prefix + '.')}
            if group:
                blocks.append((f'{instance}/config.py', f'# {what} are not read from config.py yet. Keep these overrides until they are.\n'
                               f'{name} = {literal(group)}'))
        if 'security.controls_ips' in values:
            access = self.controls.routes.controls
            options = {'allow_ips': tuple(values['security.controls_ips'])} | ({'require_two_step': True} if access.require_two_step else {})
            note = f"# routes.py allows {', '.join(access.allow_ips)} today; a request must match both lists.\n" if access.allow_ips else ''
            blocks.append((f'{instance}/routes.py', note + f"controls = {call('ControlsAccess', (), options)}"))
        apps = {name: app['id'] for app in self.controls.apps for name in app['schedules']}
        for key in values:
            schedule = SCHEDULE.fullmatch(key)
            if schedule:
                app = apps.get(schedule[1])
                blocks.append((f'apps/{app}/{app}/hooks.py' if app else 'hooks.py',
                               f'# {schedule[1]} is paused on this instance. Remove its Schedule here to stop it everywhere.'))
        return blocks

    def export(self, items):
        files, perms, classes, settings = {}, {}, {}, {}
        for item in items:
            kind, key, value = item['kind'], item['key'], item['value']
            if kind == 'perm':
                file, line = self.perm(key, value)
                perms.setdefault(file, []).append(line)
            elif kind == 'field':
                name, field = key.split('.', 1)
                classes.setdefault(name, [{}, None, []])[0][field] = value
            elif kind == 'custom':
                fields = classes.setdefault(key, [{}, None, []])[0]
                for definition in value:
                    fields.setdefault(definition['name'], {})
            elif kind == 'naming':
                classes.setdefault(key, [{}, None, []])[1] = value
            elif kind == 'layout':
                classes.setdefault(key, [{}, None, []])[2][:0] = self.layout(key, value)
            elif kind == 'list':
                classes.setdefault(key, [{}, None, []])[2].extend(self.list_view(value))
            else:
                settings[key] = value
        for file, lines in perms.items():
            files.setdefault(file, []).insert(0, 'rules = {\n' + '\n'.join(lines) + '\n}')
        for name, (fields, naming, meta) in classes.items():
            files.setdefault(self.models_file(self.models[name]), []).append(self.model_class(name, fields, naming, meta))
        for file, code in self.settings(settings):
            files.setdefault(file, []).append(code)
        return [{'file': file, 'code': '\n\n'.join(blocks) + '\n'} for file, blocks in sorted(files.items())]


def export(controls, query):
    """GET /api/_controls/overrides/export: [{file, code}] for every saved override, or for one kind and key."""
    items = [item for item in controls.overrides()['items'] if item['value'] is not None]
    if query:
        items = [item for item in items if item['kind'] == query.get('kind') and item['key'] == query.get('key')]
    # An override that no longer validates has nothing to move; a list with a skipped naming series still applies.
    failed = {(problem['kind'], problem['key']) for problem in controls.problems if not problem['error'].startswith('Series ')}
    return Exporter(controls).export([item for item in items if (item['kind'], item['key']) not in failed])
