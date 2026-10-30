"""Explicit actions, owned child collections, and private attachments."""

import hashlib
import json
import base64
import binascii
from dataclasses import dataclass
import re
from urllib.parse import parse_qs, quote

from .auth import AccessError, ROLES
from .database import Session
from .fields import Link
from .metadata import view_for
from .models import ValidationError, identifier
from .permissions import Policy
from .query import Query
from .storage import SQLiteStorage
from .jobs import JobReceipt
from .discovery import Discovery


@dataclass(frozen=True)
class Action:
    model: type
    name: str
    label: str
    handler: object
    roles: tuple = ('admin', 'editor')


@dataclass(frozen=True)
class ChildTable:
    parent: type
    name: str
    model: type
    link: str
    label: str


@dataclass(frozen=True)
class Download:
    content: bytes


class Business:
    def __init__(self, database, auth, *, policy=None, actions=(), children=(), attachments=(), storage=None, jobs=None, events=None):
        self.database, self.registry, self.auth = database, database.registry, auth
        self.tenant = None
        self.application = None
        self.jobs, self.events = jobs, events
        if any(service is not None and service.database is not database for service in (jobs, events)):
            raise ValueError('Jobs and events must use the application database')
        if auth.database is not database:
            raise ValueError('Authentication must use the application database')
        self.policy = policy or Policy(self.registry)
        if self.policy.registry is not self.registry:
            raise ValueError('Policy must use the application registry')
        self.actions, self.children, self.child_models = {}, {}, set()
        self.attachments = set(attachments)
        self.storage = storage if storage is not None else SQLiteStorage()
        for model in self.attachments:
            self.registry.name_for(model)
        for action in actions:
            self.registry.name_for(action.model)
            identifier(action.name)
            if not callable(action.handler) or not action.label or set(action.roles) - set(ROLES) or (action.model, action.name) in self.actions:
                raise ValueError('Invalid or duplicate action')
            self.actions[action.model, action.name] = action
        for child in children:
            for model in (child.parent, child.model):
                self.registry.name_for(model)
            identifier(child.name)
            field = child.model.__fields__.get(child.link)
            if not isinstance(field, Link) or field.target is not child.parent or not field.required or child.model in self.child_models or child.parent is child.model or (child.parent, child.name) in self.children:
                raise ValueError('Child tables require a unique model and required parent Link')
            self.children[child.parent, child.name] = child
            self.child_models.add(child.model)
        if any(child.parent in self.child_models for child in children):
            raise ValueError('Nested child tables are not supported')
        if any('children' in model.__fields__ for model in self.registry.models.values()):
            raise ValueError('children is reserved for owned collections')

    def initialize(self):
        self.auth.initialize()
        self.storage.initialize(self.database)
        if self.events:
            self.events.initialize()
        if self.jobs:
            self.jobs.policy = self.policy
            self.jobs.initialize()
            if self.application is not None:
                with self.database._connection() as connection:
                    namespace = connection.execute('SELECT namespace FROM evlet_job_namespace WHERE id=1').fetchone()[0]
                self.application['id'] = hashlib.sha256(namespace.encode()).hexdigest()

    def changed(self, tx, model, record_id, *, deleted=False):
        if self.events:
            self.events.publish(tx, model, record_id, deleted=deleted)

    def event_page(self, scope):
        if self.events is None or scope['method'] != 'GET':
            raise AccessError(404, 'Events are unavailable')
        self.auth.guard(scope)
        guard = scope.get('evlet.tenant_active')
        if guard is not None and not guard():
            raise AccessError(421, 'Tenant is unavailable')
        user, _ = self.auth.session(scope)
        try:
            query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True, max_num_fields=1)
            if set(query) - {'cursor'} or any(len(values) != 1 for values in query.values()):
                raise ValueError()
            cursor = query.get('cursor', [None])[0]
            if b'last-event-id' in dict(scope.get('headers', [])):
                cursor = dict(scope['headers'])[b'last-event-id'].decode('ascii')
        except (ValueError, UnicodeError):
            raise ValidationError({'cursor': 'Invalid event cursor'})
        return self.events.page(user, self.policy, after=cursor)

    def schema(self, user):
        result = self.registry.ui_schema()
        if self.events or self.jobs:
            result['capabilities'] = {'events': self.events is not None, 'jobs': self.jobs is not None}
        if self.tenant:
            result['tenant'] = self.tenant
        available = {name for name, model in self.registry.models.items() if self.policy.allows(user, model, 'read')}
        result['models'] = [entry for entry in result['models'] if entry['name'] in available]
        for entry in result['models']:
            model = self.registry.models[entry['name']]
            readable, writable = self.policy.fields(user, model), self.policy.fields(user, model, write=True)
            entry['fields'] = [{**field, 'read_only': field['name'] not in writable} for field in entry['fields'] if field['name'] in readable]
            entry['list_fields'] = [name for name in entry['list_fields'] if name == 'id' or name in readable]
            entry['search_fields'] = [name for name in entry['search_fields'] if name in readable]
            entry['layout'] = [{**group, 'fields': [name for name in group['fields'] if name in readable]} for group in entry.get('layout', [])]
            if entry['title_field'] not in readable:
                entry['title_field'] = 'id'
            entry['permissions'] = {op: self.policy.allows(user, model, op) for op in ('create', 'update', 'delete')}
            entry['child_only'] = model in self.child_models
            entry['children'] = [{'name': child.name, 'model': self.registry.name_for(child.model), 'link': child.link, 'label': child.label} for child in self.children.values() if child.parent is model and self.policy.allows(user, child.model, 'read')]
            entry['actions'] = [{'name': action.name, 'label': action.label} for action in self.actions.values() if action.model is model and user.role in action.roles and self.policy.allows(user, model, 'update')]
            entry['attachments'] = model in self.attachments
        return result

    def dispatch(self, scope, body):
        path, method = scope['path'], scope['method']
        self.auth.guard(scope)
        if path == '/api/auth/session' and method == 'GET':
            info = dict(self.application or {})
            if self.tenant:
                info.update(name=self.tenant['label'], playground=False)
            extra = {'application': info} if info else {}
            try:
                user, csrf = self.auth.session(scope)
                return 200, {'user': user.public(), 'csrf': csrf, **({'tenant': self.tenant} if self.tenant else {}), **extra}, []
            except AccessError as exc:
                if exc.status != 401:
                    raise
                return 200, {'user': None, 'setup': self.auth.allow_setup and self.auth.setup_needed(), **({'tenant': self.tenant} if self.tenant else {}), **extra}, []
        if path == '/api/auth/setup' and method == 'POST':
            self.auth.setup(scope, body)
            return 201, {'created': True}, []
        if path == '/api/auth/login' and method == 'POST':
            payload, headers = self.auth.login(scope, body)
            return 200, payload, headers
        user, csrf = self.auth.session(scope)
        if path == '/api/auth/logout' and method == 'POST':
            return 204, None, self.auth.logout(scope)
        if path.startswith('/api/auth/users'):
            return self.accounts(scope, body, user)
        if path == '/api/schema' and method == 'GET':
            return 200, self.schema(user), []
        if path == '/api/_discovery' and method == 'GET':
            return 200, Discovery(self, user).catalog(scope.get('query_string', b'')), []
        if path.startswith('/api/_discovery/') and method == 'GET':
            if scope.get('query_string'):
                raise ValidationError({'query': 'Model descriptions take no query parameters'})
            return 200, Discovery(self, user).describe(path.removeprefix('/api/_discovery/')), []
        if path == '/api/_events' and method == 'GET':
            return 200, self.event_page(scope), []
        if path.startswith('/api/_jobs/') and method == 'GET' and self.jobs:
            value = path.removeprefix('/api/_jobs/')
            if not re.fullmatch('[1-9][0-9]{0,18}', value) or int(value) > 2**63-1:
                raise AccessError(404, 'Job not found')
            return 200, self.jobs.status(int(value), user), []
        parts = path.split('/')
        model = self.registry.models.get(parts[2]) if len(parts) >= 3 and parts[:2] == ['', 'api'] else None
        if model is None or model in self.child_models:
            raise AccessError(404, 'Route not found')
        if len(parts) == 3 and method == 'GET':
            return self.page(scope, user, model)
        if len(parts) == 3 and method == 'POST':
            with self.database.transaction() as tx:
                self.policy.require(user, model, 'create')
                values, children = self.split_body(body)
                self.policy.check_values(tx, user, model, values)
                record = tx.create(model, **values)
                self.policy.record(tx, user, model, record.id, 'create')
                self.save_children(tx, user, record, children)
                self.changed(tx, model, record.id)
                result = self.detail(tx, user, record)
            return 201, result, [(b'location', f'/api/{parts[2]}/{record.id}'.encode())]
        if len(parts) < 4 or not re.fullmatch(r'[1-9][0-9]{0,18}', parts[3]) or int(parts[3]) > 2**63-1:
            raise AccessError(404, 'Record not found')
        record_id = int(parts[3])
        if len(parts) >= 5 and parts[4] == 'attachments':
            return self.files(user, model, record_id, parts[5:], method, body)
        if len(parts) == 6 and parts[4] == 'actions' and method == 'POST':
            action = self.actions.get((model, parts[5]))
            if action is None or user.role not in action.roles:
                raise AccessError(403, 'Action is unavailable')
            if body:
                raise ValidationError({'_form': 'This action takes no arguments'})
            with self.database.transaction() as tx:
                record = self.policy.record(tx, user, model, record_id, 'update')
                self.check_revision(scope, tx, user, record)
                outcome = action.handler(tx, user, record)
                self.changed(tx, model, record_id)
                record = self.policy.record(tx, user, model, record_id)
                result = self.detail(tx, user, record)
            if isinstance(outcome, JobReceipt):
                return 202, {'job': outcome.public(), 'record': result}, []
            return 200, result, []
        if len(parts) != 4 or method not in ('GET', 'PATCH', 'DELETE'):
            raise AccessError(405, 'Method not allowed')
        with self.database.transaction() as tx:
            record = self.policy.record(tx, user, model, record_id, {'GET': 'read', 'PATCH': 'update', 'DELETE': 'delete'}[method])
            if method != 'GET': self.check_revision(scope, tx, user, record)
            if method == 'DELETE':
                for child in self.children.values():
                    if child.parent is model:
                        for row in tx.list(child.model, filters={child.link: record_id}, limit=100):
                            self.policy.record(tx, user, child.model, row.id, 'delete')
                            tx.delete(child.model, row.id)
                self.changed(tx, model, record_id, deleted=True)
                tx.delete(model, record_id)
                tx.connection.execute('DELETE FROM evlet_attachments WHERE model=? AND record_id=?', (parts[2], record_id))
                return 204, None, []
            if method == 'PATCH':
                values, children = self.split_body(body)
                self.policy.check_values(tx, user, model, values)
                record = tx.update(model, record_id, **values)
                self.policy.record(tx, user, model, record_id, 'update')
                self.save_children(tx, user, record, children)
                self.changed(tx, model, record_id)
            result = self.detail(tx, user, record)
        return 200, result, [(b'etag', self.revision(result))]

    @staticmethod
    def revision(detail):
        encoded = json.dumps(detail, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
        return ('"' + hashlib.sha256(encoded).hexdigest() + '"').encode()

    def check_revision(self, scope, tx, user, record):
        expected = dict(scope.get('headers', [])).get(b'if-match')
        if expected is not None and expected != self.revision(self.detail(tx, user, record)):
            raise AccessError(409, 'This record changed since you opened it. Reload it before saving.')

    def accounts(self, scope, body, user):
        path, method = scope['path'], scope['method']
        if user.role != 'admin':
            raise AccessError(403, 'Administrator access required')
        if path == '/api/auth/users':
            if method == 'GET':
                try:
                    query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True, max_num_fields=1)
                    if set(query) - {'offset'}:
                        raise ValueError()
                    offset = int(query.get('offset', ['0'])[0])
                except (ValueError, UnicodeError):
                    raise ValidationError({'offset': 'Use a valid offset'})
                return 200, self.auth.user_page(offset), []
            if method == 'POST':
                return 201, self.auth.create_user(**body).public(), []
        match = re.fullmatch(r'/api/auth/users/([1-9][0-9]{0,18})', path)
        if match and method == 'PATCH' and int(match[1]) <= 2**63-1:
            self.auth.update_user(int(match[1]), body)
            return 204, None, []
        raise AccessError(405, 'Method not allowed')

    @staticmethod
    def split_body(body):
        values = dict(body)
        children = values.pop('children', {})
        if not isinstance(children, dict):
            raise ValidationError({'children': 'Use an object of child collections'})
        return values, children

    def save_children(self, tx, user, parent, collections):
        try:
            self._save_children(tx, user, parent, collections)
        except ValidationError as exc:
            raise ValidationError({'children': str(exc)}) from exc

    def _save_children(self, tx, user, parent, collections):
        for name, rows in collections.items():
            child = self.children.get((type(parent), name))
            if child is None or not isinstance(rows, list) or len(rows) > 100:
                raise ValidationError({'children': 'Use a known child collection with at most 100 rows'})
            if tx.count(child.model, filters={child.link: parent.id}) > 100:
                raise ValidationError({'children': 'This collection exceeds the 100-row editor limit'})
            existing, hidden = {}, set()
            for row in tx.list(child.model, filters={child.link: parent.id}, limit=100):
                existing[row.id] = row
                try:
                    self.policy.record(tx, user, child.model, row.id)
                except AccessError:
                    hidden.add(row.id)
            kept = set()
            for row in rows:
                if not isinstance(row, dict):
                    raise ValidationError({'children': 'Each child must be an object'})
                values = dict(row)
                row_id = values.pop('id', None)
                if child.link in values:
                    raise AccessError(403, 'The parent link is managed by the collection')
                self.policy.check_values(tx, user, child.model, values)
                if row_id is not None:
                    if type(row_id) is not int or row_id not in existing or row_id in kept:
                        raise ValidationError({'children': 'Child ID must belong to this parent and appear once'})
                    self.policy.record(tx, user, child.model, row_id, 'update' if values else 'read')
                    if values:
                        tx.update(child.model, row_id, **values)
                        self.policy.record(tx, user, child.model, row_id, 'update')
                    kept.add(row_id)
                else:
                    self.policy.require(user, child.model, 'create')
                    added = tx.create(child.model, **{**values, child.link: parent.id})
                    self.policy.record(tx, user, child.model, added.id, 'create')
            for row_id in existing.keys() - kept - hidden:
                self.policy.record(tx, user, child.model, row_id, 'delete')
                tx.delete(child.model, row_id)

    def detail(self, tx, user, record):
        result = self.policy.serialize(tx, user, record)
        children = {}
        for child in self.children.values():
            if child.parent is not type(record) or not self.policy.allows(user, child.model, 'read'):
                continue
            if tx.count(child.model, filters={child.link: record.id}) > 100:
                raise ValidationError({'children': 'This collection exceeds the 100-row editor limit'})
            rows = tx.list(child.model, filters={child.link: record.id}, limit=100, order_by=('id',))
            children[child.name] = []
            for row in rows:
                try:
                    self.policy.record(tx, user, child.model, row.id)
                except AccessError:
                    continue
                value = self.policy.serialize(tx, user, row)
                value.pop(child.link, None)
                children[child.name].append(value)
        if children:
            result['children'] = children
        return result

    def page(self, scope, user, model):
        self.policy.require(user, model, 'read')
        try:
            raw = scope.get('query_string', b'')
            if len(raw) > 8192:
                raise ValueError()
            query = parse_qs(raw.decode('ascii'), keep_blank_values=True, max_num_fields=10)
            if set(query) - {'limit', 'offset', 'filters', 'order_by', 'q'} or any(len(values) != 1 for values in query.values()):
                raise ValueError()
            import json
            filters = json.loads(query.get('filters', ['{}'])[0])
            order = query.get('order_by', ['-id'])[0].split(',')
            limit, offset = int(query.get('limit', ['50'])[0]), int(query.get('offset', ['0'])[0])
            if not isinstance(filters, dict) or not 1 <= limit <= 100 or not 0 <= offset <= 2**63-1:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise ValidationError({'query': 'Invalid query parameters'})
        self.policy.query_fields(user, model, filters, order)
        compiler = Query(self.registry, model)
        readable = self.policy.fields(user, model)
        search_fields = [name for name in view_for(model)['search_fields'] if name in readable]
        where, values = compiler.where(filters, query.get('q', [''])[0], search_fields=search_fields)
        scoped, parameters = self.policy.scope_sql(user, model)
        if scoped:
            where += (' AND ' if where else ' WHERE ') + scoped.removeprefix(' WHERE ')
            values.extend(parameters)
        ordering = compiler.ordering(order)
        table = identifier(self.registry.name_for(model))
        with self.database._connection() as connection:
            connection.execute('BEGIN')
            tx = Session(connection, self.registry)
            rows = connection.execute(f'SELECT record.* FROM {table} AS record{where}{ordering} LIMIT ? OFFSET ?', [*values, limit, offset]).fetchall()
            total = connection.execute(f'SELECT count(*) FROM {table} AS record{where}', values).fetchone()[0]
            items = [self.policy.serialize(tx, user, model._from_row(row)) for row in rows]
        return 200, {'items': items, 'total': total, 'limit': limit, 'offset': offset}, []

    def files(self, user, model, record_id, tail, method, body):
        if model not in self.attachments or len(tail) > 1:
            raise AccessError(404, 'Attachments unavailable')
        file_id = None
        if tail:
            if not re.fullmatch(r'[1-9][0-9]{0,18}', tail[0]) or int(tail[0]) > 2**63-1:
                raise AccessError(404, 'Attachment not found')
            file_id = int(tail[0])
        name = self.registry.name_for(model)
        with self.database.transaction() as tx:
            self.policy.record(tx, user, model, record_id, 'read' if method == 'GET' else 'update')
            args = (name, record_id)
            if method == 'GET' and file_id is None:
                rows = tx.connection.execute('SELECT id,name,COALESCE(file_size,length(content)) AS size FROM evlet_attachments WHERE model=? AND record_id=? ORDER BY id', args)
                return 200, {'items': [dict(row) for row in rows]}, []
            if method == 'POST' and file_id is None:
                filename = body.get('name')
                if set(body) != {'name', 'content'} or not isinstance(filename, str) or not 1 <= len(filename) <= 180 or any(ord(c) < 32 or c in '/\\' for c in filename):
                    raise ValidationError({'attachment': 'Use a filename without paths or control characters'})
                try:
                    content = base64.b64decode(body['content'], validate=True)
                    filename.encode('utf-8')
                except (ValueError, TypeError, binascii.Error, UnicodeError):
                    raise ValidationError({'attachment': 'Invalid file encoding'})
                if not 1 <= len(content) <= 1024 * 1024:
                    raise ValidationError({'attachment': 'Use a nonempty file up to 1 MiB'})
                if tx.connection.execute('SELECT count(*) FROM evlet_attachments WHERE model=? AND record_id=?', args).fetchone()[0] >= 10:
                    raise ValidationError({'attachment': 'Use at most 10 attachments per record'})
                file_id = self.storage.add(tx, *args, filename, content)
                self.changed(tx, model, record_id)
                return 201, {'id': file_id, 'name': filename, 'size': len(content)}, []
            row = tx.connection.execute('SELECT * FROM evlet_attachments WHERE model=? AND record_id=? AND id=?', (*args, file_id)).fetchone()
            if row is None:
                raise AccessError(404, 'Attachment not found')
            if method == 'GET':
                return 200, Download(self.storage.read(row)), [(b'content-type', b'application/octet-stream'), (b'content-disposition', ("attachment; filename*=UTF-8''" + quote(row['name'], safe='')).encode()), (b'content-security-policy', b"default-src 'none'; sandbox")]
            if method == 'DELETE':
                tx.connection.execute('DELETE FROM evlet_attachments WHERE id=?', (file_id,))
                self.changed(tx, model, record_id)
                return 204, None, []
        raise AccessError(405, 'Method not allowed')
