"""Export as code: saved overrides as Python, grouped by the file each one belongs in.

Paste the code into the app or the instance, deploy it, then reset the overrides. Values that have no
Python form here (a callable from models.py, say) are written as ... and must be copied from the file.
"""

from decimal import Decimal
import inspect

from .controls import SCHEDULE, SCOPES, SETTINGS

PROPS = ('label', 'help_text', 'placeholder', 'hidden', 'required', 'read_only', 'default')
UNKNOWN = '...'


def literal(value):
    """Python source for a value, or ... when it has none."""
    if value is None or isinstance(value, (bool, int, float, str, Decimal)):
        return repr(value)
    if isinstance(value, type):
        return value.__name__
    if inspect.isfunction(value):
        return value.__qualname__ if '<' not in value.__qualname__ else value.__name__
    if isinstance(value, tuple):
        return '(' + ', '.join(map(literal, value)) + (',)' if len(value) == 1 else ')')
    if isinstance(value, list):
        return '[' + ', '.join(map(literal, value)) + ']'
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return '{' + ', '.join(f'{key!r}: {literal(item)}' for key, item in value.items()) + '}'
    return UNKNOWN


def call(name, args=(), kwargs=None):
    return f"{name}({', '.join([*map(literal, args), *(f'{key}={literal(value)}' for key, value in (kwargs or {}).items())])})"


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
    if props.get('options_hidden'):
        line += f"  # hidden from new values on this instance: {', '.join(props['options_hidden'])}"
    return line


def instance_line(name, definition, props, registry):
    """An instance field as an app field. Apps can't declare x_ names, so rename it while moving it."""
    kind, args = definition['type'], []
    kwargs = {'label': definition['label']} | ({'help_text': definition['help_text']} if definition.get('help_text') else {})
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

    def folder(self, model):
        """apps/<app>/<package> for a model, or its module's folder outside a project."""
        module = model.__module__.split('.')
        parts = module[:module.index('models')] if 'models' in module else module[:-1]
        folder = '/'.join(parts or module[:1])
        app = self.controls.app_of.get(model.__name__)
        if app is None:
            return folder
        flat = any(item['id'] == app and item.get('layout') == 'flat' for item in self.controls.apps)
        return f'apps/{app}' if flat else f'apps/{app}/{folder}'

    def scope(self, role, model, value):
        if value is None:
            return 'None'
        file = self.controls.file_rule(role, model).scope
        if file is not None and self.controls.scope_id(file) == value:
            return literal(file)
        if value in SCOPES:
            return f'SCOPES[{value!r}]'
        if value == 'own_customer':
            return 'own_customer()'
        return literal(self.controls.scopes.get(value))

    def perm(self, key, value):
        role, model = self.controls.perm_key(key)
        fields = lambda names: literal(None if names is None else tuple(names))
        return (f'{self.folder(model)}/permissions.py',
                f"    ({role!r}, {model.__name__}): Rule(operations={literal(tuple(value['ops']))}, read_fields={fields(value['read'])}, "
                f"write_fields={fields(value['write'])}, scope={self.scope(role, model, value['scope'])}),")

    def model_class(self, name, fields, naming):
        """class Model(Model): the changed field lines, the instance fields and Meta.naming."""
        model = self.models[name]
        lines = [f'class {name}(Model):']
        declared = model.__declared_fields__
        for field, props in fields.items():
            definition = getattr(declared.get(field), 'instance', None)
            if definition:
                lines.append(instance_line(field, definition, props, self.registry))
            else:
                lines.append(field_line(field, type(declared[field]).__name__, *getattr(declared[field], 'arguments', ((), {})), props))
        if naming is not None:
            file = model.Meta.naming
            lines += [''] * (len(lines) > 1) + ['    class Meta:', '        naming = Naming(', f'            {file.field!r},']
            for series in naming:
                options = {'when': series['when']} if series['when'] else {}
                lines.append(f"            {call('Series', (series['format'],), options | {'label': series['label'], 'id': series['id']})},")
            if file.date_field:
                lines.append(f'            date_field={file.date_field!r},')
            lines.append('        )')
        return '\n'.join(lines)

    def layout(self, name, sections):
        model = self.models[name]
        children = []
        for index, section in enumerate(sections):
            fields = ', '.join(call('Field', (item['name'],), {'id': item['name'], **({'width': 12} if item['width'] == 12 else {})}) for item in section['fields'])
            children.append(f"    FormLayout(id={'main_details' if index == 0 else f'section_{index + 1}'!r}, title={section['title']!r}, children=[{fields}]),")
        return (f'{self.folder(model)}/views/__init__.py',
                f'{name.lower()}_form = FormView(model={name}, layout=Column(children=[\n' + '\n'.join(children) + '\n]))')

    def list_view(self, name, value):
        return (f'{self.folder(self.models[name])}/views/__init__.py',
                f"{name.lower()}_list = {call('ListView', (), {'model': self.models[name], 'columns': value['columns'], 'page_size': value['page_size'], 'order_by': value['sort']})}")

    def settings(self, values):
        """Security settings for config.py, allowed networks for routes.py and paused schedules beside the app's schedules."""
        instance = f'instances/{self.controls.system.instance_name() or "<name>"}'
        blocks = []
        security = {key.split('.', 1)[1]: value for key, value in values.items() if key in SETTINGS and key != 'security.controls_ips'}
        if security:
            blocks.append((f'{instance}/config.py', "# Security() is not read from config.py yet. Keep these overrides until it is.\n"
                           f"security = {call('Security', (), {key: tuple(value) if isinstance(value, list) else value for key, value in security.items()})}"))
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
                classes.setdefault(name, [{}, None])[0][field] = value
            elif kind == 'custom':
                fields = classes.setdefault(key, [{}, None])[0]
                for definition in value:
                    fields.setdefault(definition['name'], {})
            elif kind == 'naming':
                classes.setdefault(key, [{}, None])[1] = value
            elif kind == 'layout':
                file, code = self.layout(key, value)
                files.setdefault(file, []).append(code)
            elif kind == 'list':
                file, code = self.list_view(key, value)
                files.setdefault(file, []).append(code)
            else:
                settings[key] = value
        for file, lines in perms.items():
            files.setdefault(file, []).insert(0, 'rules = {\n' + '\n'.join(lines) + '\n}')
        for name, (fields, naming) in classes.items():
            files.setdefault(f'{self.folder(self.models[name])}/models.py', []).append(self.model_class(name, fields, naming))
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
