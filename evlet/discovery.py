"""Compact, permission-filtered descriptions of the existing HTTP contract."""

from urllib.parse import parse_qs

from .auth import AccessError
from .identity import SCHEMA as ID_SCHEMA
from .models import ValidationError
from .query import OPERATORS

# Record ids travel as their 13-character text form (evlet/identity.py).
ID = dict(ID_SCHEMA)


# `_audit` of a record detail (evlet/audit.py): UTC times, and who as {"id", "name"} or null.
_PERSON = {'anyOf': [{'type': 'object', 'properties': {'id': {'type': 'integer'}, 'name': {'type': 'string'}},
                      'required': ['id', 'name'], 'additionalProperties': False}, {'type': 'null'}]}
_TIME = {'anyOf': [{'type': 'string', 'format': 'date-time'}, {'type': 'null'}]}
AUDIT = {'type': 'object', 'description': 'When and by whom the record was created and last changed. Who is null for the engine.',
         'properties': {'created_at': _TIME, 'created_by': _PERSON, 'modified_at': _TIME, 'modified_by': _PERSON},
         'required': ['created_at', 'created_by', 'modified_at', 'modified_by'], 'additionalProperties': False}


def object_schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def value_schema(field, *, read=False):
    kind = field['type']
    if kind == 'link':
        value = dict(ID)
    elif kind == 'integer':
        value = {'type': 'integer', 'minimum': -2**63, 'maximum': 2**63 - 1}
        value.update({key: field[key] for key in ('minimum', 'maximum') if field.get(key) is not None})
    elif kind == 'boolean':
        value = {'type': 'boolean'}
    elif kind == 'tags':
        value = {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 40}, 'maxItems': 20, 'uniqueItems': True}
    elif kind == 'multiselect':
        value = {'type': 'array', 'items': {'type': 'string', 'enum': field['choices']}, 'uniqueItems': True}
    elif kind == 'reference':
        value = {'type': 'object', 'properties': {'model': {'type': 'string'}, 'id': ID}, 'required': ['model', 'id'], 'additionalProperties': False}
    elif kind == 'duration':
        value = {'anyOf': [{'type': 'string', 'pattern': r'^P(?:[0-9]+D)?(?:T(?:[0-9]+H)?(?:[0-9]+M)?(?:[0-9]+S)?)?$'},
                           {'type': 'integer', 'minimum': 0, 'maximum': 2**63 - 1}],
                 'description': 'An ISO 8601 duration such as PT1H30M, or whole seconds.'}
    else:
        value = {'type': 'string'}
        if field.get('max_length'):
            value['maxLength'] = field['max_length']
        if kind == 'select':
            value['enum'] = field['choices']
        if kind in ('date', 'datetime', 'email', 'url'):
            value['format'] = {'date': 'date', 'datetime': 'date-time', 'email': 'email', 'url': 'uri'}[kind]
        if kind == 'time':
            value['pattern'] = r'^[0-9]{2}:[0-9]{2}(:[0-9]{2})?$'
        if kind in ('currency', 'decimal', 'percent'):
            scale = field['scale']
            value['pattern'] = r'^-?[0-9]+$' if not scale else rf'^-?[0-9]+(?:\.[0-9]{{1,{scale}}})?$'
            value['description'] = 'Use an exact decimal string. Bounds and business validation apply on the server.'
    if not field['required'] or (read and kind == 'link'):
        value = {'anyOf': [value, {'type': 'null'}]}
    if field.get('help_text'):
        value['description'] = field['help_text']
    # A static Link default may point outside the current caller's row scope.
    if field['default_kind'] == 'static' and kind != 'link':
        value['default'] = field['default']
    return value


def writable(entry, *, create=False, omit=()):
    fields = [field for field in entry['fields'] if not field['read_only'] and field['name'] not in omit]
    required = [field['name'] for field in fields if create and field['required'] and field['default_kind'] == 'none']
    return object_schema({field['name']: value_schema(field) for field in fields}, required)


class Discovery:
    def __init__(self, business, user):
        self.business, self.user = business, user
        schema = business.schema(user)
        reachable = {child['model'] for entry in schema['models'] if not entry['child_only'] for child in entry['children']}
        self.models = {entry['name']: entry for entry in schema['models'] if not entry['child_only'] or entry['name'] in reachable}

    @staticmethod
    def operations(entry):
        return ['read', *[name for name, allowed in entry['permissions'].items() if allowed]]

    def catalog(self, query):
        try:
            if len(query) > 8192:
                raise ValueError()
            params = parse_qs(query.decode('ascii'), keep_blank_values=True, max_num_fields=3)
            if set(params) - {'limit', 'offset', 'q'} or any(len(values) != 1 for values in params.values()):
                raise ValueError()
            limit, offset = int(params.get('limit', ['50'])[0]), int(params.get('offset', ['0'])[0])
            search = params.get('q', [''])[0].casefold()
            if not 1 <= limit <= 100 or not 0 <= offset <= 2**63 - 1 or len(search) > 200:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValidationError({'query': 'Use limit 1 to 100, a nonnegative offset, and an optional search up to 200 characters'})
        roots = [entry for entry in self.models.values() if not entry['child_only']]
        matches = [entry for entry in roots if search in (entry['name'] + ' ' + entry['label']).casefold()]
        capabilities = {'operations': sorted({operation for entry in roots for operation in self.operations(entry)}),
                        'actions': any(entry['actions'] for entry in roots), 'jobs': self.business.jobs is not None}
        access = getattr(self.user, 'access', None)
        if access is not None and access.assistant is not None:
            settings = self.business.controls.settings
            capabilities['assistant'] = {'writes': access.write, 'rate': settings['mcp.rate'], 'disabled_tools': settings['mcp.disabled_tools']}
        return {'discovery_version': 1, 'models': [
            {'name': entry['name'], 'label': entry['label'], 'operations': self.operations(entry), 'actions': entry['actions']}
            for entry in matches[offset:offset + limit]], 'total': len(matches), 'limit': limit, 'offset': offset,
            'capabilities': capabilities, **({'tenant': self.business.tenant} if self.business.tenant else {})}

    def describe(self, name):
        entry = self.models.get(name)
        if entry is None:
            raise AccessError(404, 'Model not found')
        fields = []
        for field in entry['fields']:
            keys = ('name', 'type', 'required', 'read_only', 'unique', 'default_kind', 'max_length', 'minimum', 'maximum', 'scale', 'choices', 'target')
            fields.append({key: field[key] for key in keys if key in field})
            fields[-1]['read_only'] = field['read_only'] or not any(entry['permissions'][operation] for operation in ('create', 'update'))
        schemas = {'read': object_schema({'id': dict(ID), **{field['name']: value_schema(field, read=True) for field in entry['fields']}}, ('id',))}
        if not entry['child_only']:
            schemas['read']['properties']['_audit'] = AUDIT
        if not entry['child_only']:
            for operation in ('create', 'update'):
                if entry['permissions'][operation]:
                    schemas[operation] = writable(entry, create=operation == 'create')
        children = []
        for child in entry['children']:
            child_entry = self.models[child['model']]
            children.append({**child, 'operations': self.operations(child_entry), 'replacement': 'Omit to preserve; supplied rows replace the collection. Existing IDs stay with this parent.'})
            read_properties = {'id': dict(ID), **{field['name']: value_schema(field, read=True) for field in child_entry['fields'] if field['name'] != child['link']}}
            child_read = {'type': 'array', 'maxItems': 100, 'items': object_schema(read_properties, ('id',))}
            schemas['read']['properties'].setdefault('children', object_schema({}))['properties'][child['name']] = child_read
            for operation in ('create', 'update'):
                if operation not in schemas:
                    continue
                variants = []
                if child_entry['permissions']['create']:
                    variants.append(writable(child_entry, create=True, omit=(child['link'],)))
                if operation == 'update':
                    existing = writable(child_entry, omit=(child['link'],)) if child_entry['permissions']['update'] else object_schema({})
                    existing['properties']['id'] = dict(ID)
                    existing['required'] = ['id']
                    variants.append(existing)
                if variants:
                    schemas[operation]['properties'].setdefault('children', object_schema({}))['properties'][child['name']] = {
                        'type': 'array', 'maxItems': 100, 'items': {'oneOf': variants}}
        model = self.business.registry.models[name]
        query_fields = ['id', *[field['name'] for field in entry['fields'] if field['type'] != 'link' or self.business.policy.rule(self.user, model.__fields__[field['name']].target).scope is None]]
        return {'discovery_version': 1, 'name': name, 'label': entry['label'], 'description': entry['description'],
                'child_only': entry['child_only'], 'operations': [] if entry['child_only'] else self.operations(entry),
                'fields': fields, 'schemas': schemas, 'children': children, 'actions': [] if entry['child_only'] else entry['actions'],
                'query': {'fields': query_fields, 'operators': sorted(OPERATORS), 'search_fields': entry['search_fields'], 'max_limit': 100},
                'validation': 'Schemas describe accepted input shapes. Permissions, scopes, required values, constraints, and business rules are enforced on every request.'}
