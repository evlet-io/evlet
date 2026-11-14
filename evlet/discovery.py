"""Compact, permission-filtered descriptions of the existing HTTP contract."""

from urllib.parse import parse_qs

from .auth import AccessError
from .identity import SCHEMA as ID_SCHEMA
from .models import ValidationError
from .query import OPERATORS
from .calc import LIMITS
from .sections import LABEL_LENGTH, MAX_SECTIONS

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


_TEXT = {'type': 'string'}
_BOOL = {'type': 'boolean'}
_COUNT = {'type': 'integer', 'minimum': 0}
_TEXTS = {'type': 'array', 'items': _TEXT}
_MOVE = object_schema({
    'key': _TEXT, 'to': _TEXT, 'label': _TEXT, 'allowed': _BOOL, 'primary': _BOOL, 'reason': _TEXT,
    'asks': _TEXTS,
    'remark': object_schema({'mode': {'enum': ['optional', 'required']}, 'label': {'type': ['string', 'null']},
                             'required': _BOOL}, ('mode', 'label', 'required')),
    'level': {'type': 'integer', 'minimum': 1}, 'pending': _TEXT, 'can_approve': _BOOL,
}, ('key', 'to', 'label', 'allowed'))
_PAGE = object_schema({'total': _COUNT, 'limit': {'type': 'integer', 'minimum': 1}, 'partial': _BOOL},
                      ('total', 'limit', 'partial'))
# Optional record response annotations; discovery's writable fields stay separate.
READ_ANNOTATIONS = {
    '_audit': AUDIT,
    '_actions': {'type': 'array', 'items': object_schema({'name': _TEXT, 'label': _TEXT, 'allowed': _BOOL, 'reason': _TEXT},
                                                        ('name', 'label', 'allowed'))},
    '_moves': {'type': 'array', 'items': _MOVE},
    '_locked': _BOOL,
    '_journal': object_schema({'count': _COUNT, 'open_follow_ups': _COUNT}, ('count', 'open_follow_ups')),
    '_row_ids': {'type': 'object', 'additionalProperties': dict(ID)},
    '_calculation': object_schema({'language': _TEXT, 'model_schema_hash': _TEXT}, ('language', 'model_schema_hash')),
    '_job': object_schema({'id': {'type': 'integer', 'minimum': 1}, 'state': _TEXT}, ('id', 'state')),
    '_outcome': {'description': 'Application-defined JSON value returned by an action.'},
    '_effects': {'type': 'array', 'items': object_schema({'target': _TEXT, 'rows': _COUNT, 'reversals': _COUNT, 'balanced': _BOOL},
                                                        ('target', 'rows', 'reversals'))},
    '_deletable': _BOOL,
    '_version': object_schema({'number': {'type': ['integer', 'null'], 'minimum': 0}, 'released': _BOOL,
                               'state': {'type': ['string', 'null']}}, ('number', 'released', 'state')),
    '_restore': object_schema({'version': _COUNT, 'dropped': _TEXTS, 'kept': _TEXTS}, ('version', 'dropped', 'kept')),
}


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
    elif kind == 'image':
        # Upload with POST /api/{model}/{id}/attachments {"name", "content", "purpose": "image"}, then save the id.
        value = {'type': 'string', 'pattern': '^[1-9][0-9]{0,18}$',
                 'description': 'The id of an uploaded PNG, JPEG, GIF or WebP image: one of this record, or one you uploaded for a new record.'}
    elif kind == 'richtext':
        # The editor's HTML subset; the server sanitises every write (docs/internal/rich-text.md).
        value = {'type': 'string', 'contentMediaType': 'text/html', 'maxLength': max(4096, field['max_length'] * 10),
                 'description': f"HTML: p, h2, h3, ul, ol, li, blockquote, pre, code, hr, br, strong, em, u, s and a href "
                                f"(http, https, mailto). Other markup is removed. At most {field['max_length']} characters of text."}
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
            value['description'] = 'Use an exact decimal string. Bounds and business validation apply on the server.' + (
                " An amount has at most its currency's decimal places (KWD 3, JPY 0; see currency_decimals in /api/schema)." if kind == 'currency' else '')
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
        available = {entry['name']: entry for entry in schema['models']}
        self.models = {}
        pending = [entry for entry in available.values() if not entry['child_only']]
        while pending:
            entry = pending.pop()
            if entry['name'] in self.models:
                continue
            self.models[entry['name']] = entry
            pending.extend(available[child['model']] for child in entry['children'] if child['model'] in available)
        self.models = {name: entry for name, entry in available.items() if name in self.models}

    @staticmethod
    def section_schema(*, existing=False, keys=False):
        fields = {'section': {'type': 'string', 'minLength': 1, 'maxLength': LABEL_LENGTH}}
        required = ['section']
        if existing:
            fields['id'] = dict(ID)
            required.append('id')
        if keys:
            fields['_key'] = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,64}$'}
        return object_schema(fields, required)

    def read_schema(self, entry, *, omit=()):
        result = object_schema({'id': dict(ID), **{field['name']: value_schema(field, read=True)
                               for field in entry['fields'] if field['name'] not in omit}}, ('id',))
        for child in entry['children']:
            row = self.read_schema(self.models[child['model']], omit=(child['link'],))
            sections = child.get('sections', False)
            if sections:
                row = {'oneOf': [row, self.section_schema(existing=True)]}
            result['properties'].setdefault('children', object_schema({}))['properties'][child['name']] = {
                'type': 'array', 'maxItems': 100 + (MAX_SECTIONS if sections else 0), 'items': row}
            result['properties'].setdefault('children_pages', object_schema({}))['properties'][child['name']] = _PAGE
        return result

    def write_schema(self, entry, *, create=False, omit=()):
        if not entry['permissions']['create' if create else 'update']:
            # Changing descendants also changes this owned row. Its caller may echo an id, but cannot
            # reach writable grandchildren through a row it may only read (documents.persist_rows).
            return object_schema({})
        result = writable(entry, create=create, omit=omit)
        for child in entry['children']:
            if child.get('read_only'):
                continue
            child_entry = self.models[child['model']]
            variants, new, saved = [], [], []
            for existing in (False, True):
                if existing and create or not existing and not child_entry['permissions']['create']:
                    continue
                row = self.write_schema(child_entry, create=not existing, omit=(child['link'],))
                if existing:
                    row['properties']['id'] = dict(ID)
                    row['required'] = ['id']
                if child.get('row_changes'):
                    row['properties']['_key'] = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,64}$'}
                kinds = [row]
                if child.get('sections') and child_entry['permissions']['update' if existing else 'create']:
                    kinds.append(self.section_schema(existing=existing, keys=True))
                (saved if existing else new).extend(kinds)
                variants.extend(kinds)
            if not variants:
                continue
            collection = {'type': 'array', 'maxItems': child['max_rows'] + (MAX_SECTIONS if child.get('sections') else 0),
                          'items': {'oneOf': variants}}
            if child.get('row_changes'):
                # JSON Schema applies items/maxItems to arrays and properties to objects. Keeping both
                # describes full lists and bounded change sets without changing the existing items contract.
                changes = {}
                for name, rows in (('create', new), ('update', saved)):
                    if rows:
                        changes[name] = {'type': 'array', 'maxItems': LIMITS['collection_rows'], 'items': {'oneOf': rows}}
                if not create and child_entry['permissions']['delete']:
                    changes['delete'] = {'type': 'array', 'maxItems': LIMITS['collection_rows'], 'items': dict(ID), 'uniqueItems': True}
                if child_entry['permissions']['update'] or create and child_entry['permissions']['create']:
                    changes['order'] = {'type': 'array', 'maxItems': LIMITS['collection_rows'],
                                        'items': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,64}$'}, 'uniqueItems': True}
                collection.update(type=['array', 'object'], properties=changes, additionalProperties=False)
            result['properties'].setdefault('children', object_schema({}))['properties'][child['name']] = collection
        return result

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
        schemas = {'read': self.read_schema(entry)}
        if not entry['child_only']:
            schemas['read']['properties'].update(READ_ANNOTATIONS)
        if not entry['child_only']:
            for operation in ('create', 'update'):
                if entry['permissions'][operation]:
                    schemas[operation] = self.write_schema(entry, create=operation == 'create')
        children = []
        for child in entry['children']:
            child_entry = self.models[child['model']]
            children.append({**child, 'operations': self.operations(child_entry), 'replacement': 'Omit to preserve; supplied rows replace the collection. Existing IDs stay with this parent.'})
        model = self.business.registry.models[name]
        query_fields = ['id', *[field['name'] for field in entry['fields'] if field['type'] != 'link' or self.business.policy.rule(self.user, model.__fields__[field['name']].target).scope is None]]
        return {'discovery_version': 1, 'name': name, 'label': entry['label'], 'description': entry['description'],
                'child_only': entry['child_only'], 'operations': [] if entry['child_only'] else self.operations(entry),
                'fields': fields, 'schemas': schemas, 'children': children, 'actions': [] if entry['child_only'] else entry['actions'],
                'query': {'fields': query_fields, 'operators': sorted(OPERATORS), 'search_fields': entry['search_fields'], 'max_limit': 100},
                'validation': 'Schemas describe accepted input shapes. Permissions, scopes, required values, constraints, and business rules are enforced on every request.'}
