"""Explicit actions, owned child collections, and private attachments."""

import hashlib
import json
import logging
import threading
import base64
import binascii
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import re
from urllib.parse import parse_qs, quote

from .audit import actor_id, detail as audit_detail
from .auth import AccessError
from .identity import RecordId, checked as item_id, path_id
from .naming import drop_echoes
from .database import ROW_LIMIT, Session, using
from .fields import Link
from .ui.metadata import view_for
from .models import ValidationError, identifier
from .permissions import CORRECTIONS, Policy, calling, read_only, trusted
from .query import SqlQuery, operators_for
from .storage import SQLiteStorage
from .jobs import JobReceipt
from .discovery import Discovery

logger = logging.getLogger(__name__)


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


RULES = ('applies_when', 'required_when', 'locked_when')
# Rows a detail response embeds per owned collection; larger collections are paged through lines().
EMBEDDED_ROWS = ROW_LIMIT
EMPTY_EXPRESSIONS = {'compute': None, 'initial': None, 'applies_when': None, 'required_when': None, 'locked_when': None}


@dataclass(frozen=True)
class Download:
    content: bytes


def size_text(size):
    for unit, scale in (('MiB', 1024 * 1024), ('KiB', 1024)):
        if size % scale == 0:
            return f'{size // scale} {unit}'
    return '1 byte' if size == 1 else f'{size} bytes'


class Gate:
    """Requests and jobs pass in shared; a schema change (adding or removing an instance field) passes
    alone, after the work in flight has finished. A thread already inside may enter again."""

    def __init__(self):
        self.condition, self.readers, self.writer, self.waiting = threading.Condition(), 0, False, 0
        self.local = threading.local()

    @contextmanager
    def shared(self):
        depth = getattr(self.local, 'depth', 0)
        with self.condition:
            while depth == 0 and (self.writer or self.waiting):
                self.condition.wait()
            self.readers += 1
        self.local.depth = depth + 1
        try:
            yield
        finally:
            self.local.depth = depth
            with self.condition:
                self.readers -= 1
                self.condition.notify_all()

    @contextmanager
    def exclusive(self):
        with self.condition:
            self.waiting += 1
            while self.writer or self.readers:
                self.condition.wait()
            self.waiting -= 1
            self.writer = True
        try:
            yield
        finally:
            with self.condition:
                self.writer = False
                self.condition.notify_all()


class Business:
    def __init__(self, database, auth, *, policy=None, actions=(), children=(), attachments=(), storage=None, jobs=None, events=None):
        self.database, self.registry, self.auth = database, database.registry, auth
        self.tenant = None
        self.gate = Gate()
        self.application = None
        self.jobs, self.events = jobs, events
        if jobs is not None: jobs.business = self
        if any(service is not None and service.database is not database for service in (jobs, events)):
            raise ValueError('Jobs and events must use the application database')
        if auth.database is not database:
            raise ValueError('Authentication must use the application database')
        self.policy = policy or Policy(self.registry)
        if self.policy.registry is not self.registry:
            raise ValueError('Policy must use the application registry')
        from .controls import Controls
        from .connectors import Connectors
        from .settings import Settings
        from .assistants import Assistants
        from .mailboxes import Mailboxes
        self.controls = Controls(self)
        self.settings = Settings(self)
        self.connectors = Connectors(self)
        # instances/<name>/secret.key for evlet.secrets; None keeps two-step sign-in off.
        self.secret_key = None
        self.assistants = Assistants(self)
        from .tools import Tools
        self.tools = Tools(self)
        # Eva, when config.py turns AI on (Application(ai=AI(...))); None keeps every Eva route and table away.
        self.eva = None
        self.actions, self.children, self.child_models = {}, {}, set()
        self.attachments = set(attachments)
        self.storage = storage if storage is not None else SQLiteStorage()
        children = (*children, *self.registry.children)
        declared_actions = []
        for model in self.registry.models.values():
            for name in dir(model):
                method = getattr(model, name)
                declaration = getattr(method, '__action__', None)
                if declaration is not None:
                    declared_actions.append(Action(model, name, declaration['label'], method, declaration['roles']))
        actions = (*actions, *declared_actions)
        for model in self.attachments:
            self.registry.name_for(model)
        # Application(attachments=...) enables the default limits; Meta.attachments enables its own and wins.
        from .meaning import Attachments
        self.attachment_limits = {model: Attachments() for model in self.attachments}
        self.attachment_limits.update({model: meaning.attachments for model, meaning in self.registry.meaning.items()
                                       if meaning.attachments is not None})
        self.attachments |= set(self.attachment_limits)
        for action in actions:
            self.registry.name_for(action.model)
            identifier(action.name)
            if not callable(action.handler) or not action.label or set(action.roles) - set(self.policy.roles) or (action.model, action.name) in self.actions:
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
        registered = set(self.registry.children)
        if any(child.parent in self.child_models and child not in registered for child in children):
            raise ValueError('Nested child tables are only supported for finite-v1 Table declarations')
        for child in self.children.values():
            if child not in registered and self.registry.plan_for(child.model) is not None:
                raise ValueError(f'Rows of {child.parent.__name__}.{child.name} use finite calculations, initial values or field rules, '
                                 'which ChildTable rows do not run; declare the rows with Table(Child) on the parent instead')
        for action in self.actions.values():
            if self.registry.plan_for(action.model) is not None and not hasattr(getattr(action.model, action.name, None), '__action__'):
                raise ValueError(f'Legacy Action {action.name!r} would bypass finite-v1 settlement on '
                                 f'{action.model.__name__}; declare it with @action on the model instead')
        auth.owners = {**{child.model: child.parent for child in self.registry.children}, **{child.model: child.parent for child in self.children.values()}}
        # Rows of a ChildTable are listed as children of their parent, not as related records.
        self.relations = {}
        for model, meaning in self.registry.meaning.items():
            if model in self.child_models and (meaning.attachments is not None or meaning.key_fields or getattr(model.Meta, 'relations', ())):
                raise ValueError(f'{model.__name__} rows belong to a child table; declare Meta.key_fields, relations and '
                                 'attachments on the parent model')
            declared = {(self.registry.resolve(item.model), item.link) for item in getattr(model.Meta, 'relations', ())}
            if any(source in self.child_models for source, _ in declared):
                raise ValueError(f'{model.__name__}: Related names a child table model; its rows are listed as children')
            self.relations[model] = tuple(item for item in meaning.relations if item.source not in self.child_models)
        if any('children' in model.__fields__ for model in self.registry.models.values()):
            raise ValueError('children is reserved for owned collections')
        # Rows of a ChildTable are journaled with their parent, like Table rows.
        self.journals = {model: journal for model, journal in self.registry.journals.items() if model not in self.child_models}
        self.mailboxes = Mailboxes(self)

    def initialize(self):
        self.auth.initialize()
        self.controls.initialize()
        self.connectors.initialize()
        self.assistants.initialize()
        self.tools.initialize()
        if self.eva is not None:
            self.eva.initialize()
        self.mailboxes.initialize()
        self.storage.initialize(self.database)
        self.settings.initialize()
        if any(endpoint.public for endpoint in getattr(self, 'apis', ())):
            with self.database.transaction() as tx:
                tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_public_limits (client TEXT NOT NULL, period INTEGER NOT NULL, calls INTEGER NOT NULL, resets INTEGER NOT NULL, PRIMARY KEY (client, period))')
        if self.registry.plans:
            with self.database.transaction() as tx:
                # The plan each saved finite-v1 document was calculated with (history guard).
                tx.connection.execute('CREATE TABLE IF NOT EXISTS evlet_calculation_versions (resource TEXT NOT NULL, record_id INTEGER NOT NULL, plan_hash TEXT NOT NULL, PRIMARY KEY (resource, record_id))')
                # Earlier releases recorded the whole plan's hash; rows saved under the active plan
                # move to its formula hash so they keep saving without a recalculation round trip.
                for root, plan in self.registry.plans.items():
                    tx.connection.execute('UPDATE evlet_calculation_versions SET plan_hash=? WHERE plan_hash=?',
                                          (self.registry.formula_hashes[root], plan['model_schema_hash']))
        if self.registry.sequences or self.registry.naming:
            from .sequences import upgrade_counters
            with self.database.transaction() as tx:
                # Document number and naming counters, updated in each assigning write transaction.
                upgrade_counters(tx.connection)
        if any(getattr(model, 'confirmable', False) for model in self.registry.models.values()):
            from .lifecycle import ensure_tables
            with self.database.transaction() as tx:
                ensure_tables(tx.connection, postgres=getattr(self.database, 'driver', None) is not None)
        if self.journals:
            from .journal import ensure_table
            with self.database.transaction() as tx:
                ensure_table(tx.connection, postgres=getattr(self.database, 'driver', None) is not None,
                             hashed=[self.registry.name_for(model) for model, journal in self.journals.items() if journal.tamper_evident])
        if getattr(self, 'forms', None) and (self.forms.dialogs or self.forms.reports):
            self.forms.initialize()
        if getattr(self, 'slips', None) and self.slips.slips:
            self.slips.initialize()
        if getattr(self, 'prints', None) is not None:
            self.prints.initialize()
        if self.events:
            self.events.initialize()
        if getattr(self, 'ui', None):
            from .auth import User
            with using(self.database):
                for role in self.policy.roles:
                    user = User(0, 'schema', role)
                    self.ui.compile(self.schema(user), user)
        if self.jobs:
            self.jobs.policy = self.policy
            self.jobs.initialize()
            if self.application is not None:
                with self.database._connection() as connection:
                    namespace = connection.execute('SELECT namespace FROM evlet_job_namespace WHERE id=1').fetchone()[0]
                self.application['id'] = hashlib.sha256(namespace.encode()).hexdigest()

    def changed(self, tx, model, record_id, *, deleted=False, journal=False):
        if self.events:
            self.events.publish(tx, model, record_id, deleted=deleted, journal=journal)

    @property
    def timezone(self):
        """The instance timezone, kept on the database so trusted sessions date names like the API does."""
        return self.database.timezone

    @timezone.setter
    def timezone(self, value):
        self.database.timezone = value

    @contextmanager
    def context(self, user, *, readonly=False, source=None, correlation_id=None):
        from .documents import Context
        with self.gate.shared(), self.database.transaction() as tx:
            yield Context(self, tx, user, readonly=readonly, source=source, correlation_id=correlation_id)

    def event_page(self, scope):
        with self.gate.shared(), using(self.database):
            return self._event_page(scope)

    def _event_page(self, scope):
        if self.events is None or scope['method'] != 'GET':
            raise AccessError(404, 'Events are unavailable')
        self.auth.guard(scope)
        self.controls.refresh()
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

    def currency_for(self, user, model, field, readable):
        """A Currency field's source for this user: a setting becomes its code, a field they can't read is left out."""
        source = field.pop('currency_source', None)
        if source is None:
            return field
        if 'setting' in source:
            from .settings import _default
            app, key = source['setting']['app'], source['setting']['key']
            try:
                # Before the instance is initialized (no settings table yet) the declared default applies.
                code = self.settings.values.get(app, key) if self.controls.ready else _default(self.settings.declared(app, 'settings').__fields__[key])
            except KeyError:
                logger.warning('%s.%s: no setting %s.%s for its currency', model.__name__, field['name'], app, key)
                code = None
            if isinstance(code, str) and re.fullmatch('[A-Z]{3}', code):
                field['currency'] = code
            return field
        if 'link' in source:
            link = model.__declared_fields__[source['link']]
            if source['link'] in readable and self.policy.allows(user, link.target, 'read') and source['field'] in self.policy.fields(user, link.target):
                field['currency_source'] = {**source, 'model': self.registry.name_for(link.target)}
        elif source['field'] in readable:
            field['currency_source'] = source
        return field

    def schema(self, user):
        result = self.registry.ui_schema()
        if getattr(self, 'forms', None) and self.forms.reports:
            result['reports'] = self.forms.catalog(user)
            if getattr(self, 'prints', None) is not None:
                for entry in result['reports']:
                    designs = self.prints.for_report(user, self.forms.reports[entry['name']])
                    if designs:
                        entry['prints'] = designs
        if getattr(self, 'prints', None) is not None and self.prints.designs:
            from .printing import available
            result['printing'] = {'available': available()}
        if getattr(self, 'slips', None) and self.slips.slips:
            result['slips'] = self.slips.schema(user)
        if getattr(self, 'build_manifest', None) and self.build_manifest.get('native', {}).get('panels'):
            result['native_panels'] = []
            from .native import can_access
            for panel in self.build_manifest['native']['panels']:
                if can_access(self, panel['target'], user):
                    result['native_panels'].append({**panel, 'url': '/assets/apps/' + self.build_manifest['fingerprint'] + '/' + panel['app'] + '/' + panel['file']})
        if self.events or self.jobs:
            result['capabilities'] = {'events': self.events is not None, 'jobs': self.jobs is not None}
        if self.tenant:
            result['tenant'] = self.tenant
        available = {name for name, model in self.registry.models.items() if self.policy.allows(user, model, 'read')}
        result['models'] = [entry for entry in result['models'] if entry['name'] in available]
        for entry in result['models']:
            model = self.registry.models[entry['name']]
            readable, writable = self.policy.fields(user, model), self.policy.fields(user, model, write=True)
            if 'behavior' in entry:
                from .reactivity import references, plans
                available_behavior = {}
                for name, behavior in entry['behavior'].items():
                    if name not in readable: continue
                    # References to protected inputs stay private, including defaults and conditions.
                    allowed = {}
                    for key, value in behavior.items():
                        original = plans(model)[name][key]
                        dependencies = set().union(*(references(item) for item in original.values())) if key == 'filters' else references(original)
                        def available(path):
                            parts = path.split('.')
                            if len(parts) == 1: return path in readable
                            if len(parts) == 2 and parts[0] == 'parent':
                                owner = next((child.parent for child in self.children.values() if child.model is model), None)
                                return owner is not None and parts[1] in self.policy.fields(user, owner)
                            return False
                        if all(available(path) for path in dependencies):
                            allowed[key] = value
                        elif key == 'compute' and self.policy.derivation_allowed(user, model, name):
                            allowed[key] = value
                        elif key == 'filters':
                            allowed['filters_blocked'] = True
                    available_behavior[name] = allowed
                entry['behavior'] = available_behavior
                from .reactivity import references
                used_sources = {}
                for path in (path for name in readable for path in references(plans(model)[name].get('compute'))):
                    if path.count('.') == 1: used_sources.setdefault(path.split('.')[0], set()).add(path.split('.')[1])
                entry['sources'] = {name: {key: value for key, value in outputs.items() if key in used_sources[name]}
                                    for name, outputs in entry.get('sources', {}).items() if name in used_sources}
                def ordered(key):
                    head, _, rest = key.partition('.')
                    if not rest:
                        return head in readable or head in model.__tables__ and owned_readable(head) is not None
                    fields = owned_readable(head)
                    return fields is not None and rest in fields

                def owned_readable(name):
                    """Readable fields of an owned table's rows, or None when the rows are hidden."""
                    child = next((item.model for item in self.children.values() if item.parent is model and item.name == name), None)
                    return self.policy.fields(user, child) if child is not None and self.policy.allows(user, child, 'read') else None
                entry['order'] = [key for key in entry.get('order', []) if ordered(key)]
            expressions = self.field_expressions(user, model)
            # operators: what a list filter may use on the field (evlet.query); none when it is not stored.
            entry['fields'] = [{**field, 'read_only': field['name'] not in writable, 'stored': model.__declared_fields__[field['name']].stored,
                                'operators': operators_for(model, field['name']), **expressions.get(field['name'], EMPTY_EXPRESSIONS)}
                               for field in entry['fields'] if field['name'] in readable]
            entry['fields'] = [self.currency_for(user, model, field, readable) for field in entry['fields']]
            entry['list_fields'] = [name for name in entry['list_fields'] if name == 'id' or name in readable]
            entry['search_fields'] = [name for name in entry['search_fields'] if name in readable]
            entry['layout'] = [{**group, 'fields': [name for name in group['fields'] if name in readable]} for group in entry.get('layout', [])]
            # Controls overrides: labels, rules, options, layout and list view on this instance.
            self.controls.custom.merge(entry, model, readable, writable)
            if entry['title_field'] not in readable:
                entry['title_field'] = 'id'
            # The field naming fills: the record page shows it as the document number, and until it has one,
            # when it will ("Assigned on save", "Assigned on confirm").
            naming = self.registry.naming.get(model)
            if naming is not None and naming.field in readable:
                placeholder = None if naming.manual else 'Assigned on save' if naming.assign_on == 'create' else \
                    'Assigned on confirm' if naming.assign_on == 'confirm' else f'Assigned on {naming.assign_on.replace("_", " ")}'
                entry['naming'] = {'field': naming.field, 'assign_on': naming.assign_on, 'manual': naming.manual, 'placeholder': placeholder}
                for field in entry['fields']:
                    if field['name'] == naming.field and placeholder and not field.get('placeholder'):
                        field['placeholder'] = placeholder
            entry['permissions'] = {op: self.policy.allows(user, model, op) for op in ('create', 'update', 'delete')}
            # The designs this role may print the model with; absent when there are none.
            designs = self.prints.for_model(user, model) if getattr(self, 'prints', None) is not None else []
            if designs:
                entry['prints'] = designs
            from .corrections import model_schema
            flags, corrections = model_schema(self, user, model)
            entry.update(flags)
            entry['permissions'].update(corrections)
            entry['child_only'] = model in self.child_models
            from .documents import row_bounds
            # row_changes: the parent's save takes {create, update, delete, order} change sets (document route);
            # otherwise it takes only full row lists without _key (legacy ChildTable on a plain model).
            entry['children'] = [{'name': child.name, 'model': self.registry.name_for(child.model), 'link': child.link, 'label': child.label,
                                  'row_changes': bool(getattr(model, '__declaration_mode__', False)),
                                  # Whether list filters may match the parent on these rows (no row scope of their own).
                                  'filterable': self.policy.rule(user, child.model).scope is None,
                                  **dict(zip(('min_rows', 'max_rows'), row_bounds(self, child)))}
                                 for child in self.children.values() if child.parent is model and self.policy.allows(user, child.model, 'read')]
            from .states import action_schema, state_schema
            entry['actions'] = [{'name': action.name, 'label': action.label,
                                 **(action_schema(self, user, model, action.name) if hasattr(getattr(model, action.name, None), '__action__') else {})}
                                for action in self.actions.values() if action.model is model and user.role in action.roles and self.policy.allows(user, model, 'update')]
            state = state_schema(self, user, model)
            if state is not None:
                entry['state'] = state
            from .effects import model_schema as effect_schema
            entry.update(effect_schema(self, user, model))
            entry['attachments'] = model in self.attachments
            from .measures import schema as measure_schema
            if self.registry.measures.get(model):
                entry['measures'] = measure_schema(self, user, model)
            if entry['attachments']:
                entry['attachment_limits'] = self.attachment_limits[model].public()
            from .journal import schema as journal_schema
            entry.update(journal_schema(self, user, model))
            if getattr(model, 'confirmable', False):
                from .lifecycle import labels
                entry['lifecycle'] = {'confirmable': True, 'labels': labels(model),
                                      'permissions': {op: self.policy.allows(user, model, op) and self.policy.allows(user, model, 'update') for op in ('confirm', 'cancel', 'reopen')}}
            meaning = self.registry.meaning[model]
            if meaning.key_fields:
                entry['key_fields'] = [name for name in meaning.key_fields if name in readable]
            relations = [relation.public(self.registry, self.relation_order(user, relation))
                         for relation in self.visible_relations(user, model)]
            if relations:
                entry['relations'] = relations
            plan = self.registry.plans.get(model)
            if plan is not None:
                entry['calculation'] = self.calculation_projection(user, model, plan)
            if getattr(self, 'forms', None):
                for (owner, name), definition in self.forms.dialogs.items():
                    if owner is model and self.policy.allows(user, model, 'update'):
                        entry['actions'].append({'name': name, 'label': definition.title or name, 'dialog': True})
            slips = self.slips.schema(user, model) if getattr(self, 'slips', None) and self.slips.slips else []
            if slips:
                entry['slips'] = slips
        return result

    def docs_path(self, scope, user):
        """Where Docs are for this request, or None: off, on a sub-host, or outside Docs(roles=...)."""
        routes = getattr(self, 'routes', None)
        if routes is None or not routes.docs or routes.scopes(dict(scope.get('headers', [])).get(b'host', b'')) is not None:
            return None
        if routes.docs_roles and (user is None or user.role not in routes.docs_roles):
            return None
        return routes.docs_path

    def dispatch(self, scope, body):
        # A request sees one set of instance fields throughout; adding or removing one waits for the rest.
        schema = scope['path'].startswith('/api/_controls/fields/') and scope['method'] in ('POST', 'DELETE')
        # Every route serves a caller, tool calls from MCP and Eva included: Meta.api limits what it reaches.
        with calling(), self.gate.exclusive() if schema else self.gate.shared():
            if schema:
                return self._dispatch(scope, body)
            with using(self.database):
                return self._dispatch(scope, body)

    def _dispatch(self, scope, body):
        path, method = scope['path'], scope['method']
        self.auth.guard(scope)
        if path == '/api/auth/session' and method == 'GET':
            info = dict(self.application or {})
            if self.tenant:
                info.update(name=self.tenant['label'], playground=False)
            try:
                user, csrf = self.auth.session(scope)
                if 'docs' in info:
                    info['docs'] = self.docs_path(scope, user)
                extra = {'application': info} if info else {}
                return 200, {'user': user.public(), 'csrf': csrf, **({'tenant': self.tenant} if self.tenant else {}), **extra}, []
            except AccessError as exc:
                if exc.status != 401:
                    raise
                if 'docs' in info:
                    info['docs'] = self.docs_path(scope, None)
                extra = {'application': info} if info else {}
                return 200, {'user': None, 'setup': self.auth.allow_setup and self.auth.setup_needed(), **({'tenant': self.tenant} if self.tenant else {}), **extra}, []
        if path == '/api/auth/setup' and method == 'POST':
            self.auth.setup(scope, body)
            return 201, {'created': True}, []
        if path == '/api/auth/login' and method == 'POST':
            payload, headers = self.auth.login(scope, body)
            return 200, payload, headers
        if path.startswith('/api/_services/'):
            from .services import dispatch, endpoint_for
            endpoint = endpoint_for(self, path)
            if endpoint is not None and endpoint.public:
                return dispatch(self, scope, body, None)
        user, csrf = self.auth.session(scope)
        if user.access is None and dict(scope.get('headers', [])).get(b'x-evlet-client') == b'mcp':
            # The local MCP connector: the same narrowing as a remote assistant.
            user = self.assistants.narrow(user)
        if user.access is not None:
            assistant = user.access.assistant is not None or user.access.origin is not None
            if path.startswith(('/api/auth/', '/api/_settings/', '/api/_oauth/', '/api/_eva', '/api/_tools/approvals', '/api/_tools/calls')):
                raise AccessError(403, 'AI assistants cannot use this.' if assistant else 'API keys cannot be used here. Sign in instead.')
            # Tool calls are POST; the Tool Engine checks each write itself, and its run_report only reads.
            inner = scope.get('evlet.assistant') is not None
            reading = path == '/api/_tools/execute' or (inner and path.startswith('/api/_reports/') and not path.endswith('/evaluate'))
            if method not in ('GET', 'HEAD') and not user.access.write and not reading:
                raise AccessError(403, 'AI assistants can only read here.' if assistant else 'This key can only read.')
        if path == '/api/_tools' or path.startswith('/api/_tools/'):
            return self.tools.api(scope, body, user)
        if path == '/api/_eva' or path.startswith('/api/_eva/'):
            if self.eva is None:
                raise AccessError(404, 'Route not found')
            return self.eva.api(scope, body, user)
        if path == '/api/_settings/me/ai' or path.startswith(('/api/_settings/me/ai/', '/api/_oauth/')):
            return self.assistants.api(scope, body, user)
        if path == '/api/_settings/me/keys' or path.startswith('/api/_settings/me/keys/'):
            return self.controls.my_keys(scope, body, user)
        # /api/_settings/me/email is the sign-in address change (Settings); the mailbox has its own routes.
        if path == '/api/_settings/me/mailbox' or path.startswith('/api/_settings/me/mailbox/'):
            return self.mailboxes.api(scope, body, user)
        if path == '/api/_controls' or path.startswith('/api/_controls/'):
            return self.controls.dispatch(scope, body, user)
        if path.startswith('/api/_settings/'):
            return self.settings.dispatch(scope, body, user)
        if path.startswith('/api/_services/'):
            from .services import dispatch
            return dispatch(self, scope, body, user)
        if getattr(self, 'slips', None) and path.startswith('/api/_slips/'):
            return self.slips.dispatch(scope, body, user)
        prints = getattr(self, 'prints', None)
        if prints is not None and path == '/api/_prints':
            if method != 'GET': raise AccessError(405, 'Method not allowed')
            return 200, prints.catalog(user), []
        if prints is not None and path.startswith('/api/_reports/') and path.count('/') == 5 and path.split('/')[4] == 'prints':
            if method != 'POST': raise AccessError(405, 'Method not allowed')
            _, _, _, report, _, design = path.split('/')
            return prints.report_pdf(report, design, body, user)
        if getattr(self, 'forms', None) and (path == '/api/_reports' or path.startswith(('/api/_reports/', '/api/_dialogs/'))):
            return self.forms.dispatch(scope, body, user)
        if path.startswith('/api/_draft/') and method == 'POST':
            from .documents import load_draft, settle, source_values, walk
            model = self.registry.resolve(path.removeprefix('/api/_draft/'))
            if not getattr(model, '__declaration_mode__', False) or model in self.child_models or self.registry.api.get(model) is None:
                raise AccessError(404, 'Draft evaluation is unavailable')
            if self.registry.api.get(model) == 'read':
                raise AccessError(403, read_only(model))
            if set(body) - {'id', 'values', 'revision', 'calculation'} or not isinstance(body.get('values', {}), dict):
                raise ValidationError({'draft': 'Invalid draft request'})
            record_id = body.get('id')
            if record_id is not None:
                try:
                    record_id = RecordId(record_id)
                except ValueError:
                    raise ValidationError({'draft': 'Invalid draft request'}) from None
            if 'calculation' in body:
                self.check_calculation(model, body['calculation'])
            with self.context(user, readonly=True) as context:
                draft, previous = load_draft(context, model, record_id, body.get('values', {}), preview=True)
                # A locked or posted record keeps its saved results on a save, so the preview shows those.
                from .effects import has_postings
                from .states import locked_state
                kept = previous is not None and (locked_state(model, previous._values) is not None
                                                 or has_postings(context._tx.connection, self.registry, model, previous))
                errors, states = settle(context, draft, complete=False, previous=previous, kept=kept)
                def values(item):
                    allowed = self.policy.fields(user, item._definition)
                    def shown(field, value):
                        # Invalid submitted values are reported in errors; the preview sends null.
                        try: return field.serialize(value)
                        except Exception: return None
                    return {'id': item.id, '_key': item.key, '_sources': source_values(item, allowed), **{name: shown(field, item._values.get(name))
                            for name, field in item._definition.__declared_fields__.items() if name in allowed and not name.startswith('evlet_')}}
                allowed_errors = set()
                saved_rows = {}
                for _, item in walk(draft):
                    if item is not draft and item.id:
                        saved_rows.setdefault(item._definition, []).append(item.id)
                shown = {(definition, row_id) for definition, ids in saved_rows.items()
                         for row_id in self.policy.visible(context._tx, user, definition, ids)}

                def tree(item, prefix):
                    result = values(item)
                    allowed_errors.update(prefix + name for name in result if name not in ('_key', 'id', '_sources'))
                    owned = [child for child in self.children.values() if child.parent is item._definition]
                    if owned or item is draft:
                        result['children'] = {}
                    for child in owned:
                        if not self.policy.allows(user, child.model, 'read'):
                            continue
                        rows = []
                        for row in item._rows.get(child.name, []):
                            if row.id and (child.model, row.id) not in shown:
                                continue
                            rows.append(tree(row, prefix + child.name + '.' + row.key + '.'))
                        result['children'][child.name] = rows
                    return result
                result = tree(draft, '')
                errors = {key: value for key, value in errors.items() if key in allowed_errors}
                states = {key: value for key, value in states.items() if key in allowed_errors}
            return 200, {'values': result, 'errors': errors, 'states': states, 'revision': body.get('revision')}, []
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
        if model is None or model in self.child_models or self.registry.api.get(model) is None:
            raise AccessError(404, 'Route not found')
        if self.registry.api.get(model) == 'read' and scope['method'] not in ('GET', 'HEAD'):
            # Meta.api = "read": no route writes it, whatever the role or its Controls overrides.
            raise AccessError(403, read_only(model))
        try:
            return self.model_dispatch(scope, body, user, model, parts)
        except ValidationError as exc:
            raise ValidationError(self.visible_errors(user, model, exc.errors)) from None

    def visible_errors(self, user, model, errors, tx=None):
        """Hide error keys that name unreadable fields or rows the user cannot see."""
        readable = self.policy.fields(user, model)
        checks = ExitStack()
        def row_visible(child, key):
            nonlocal tx
            if path_id(key) is None:
                return True  # A client's temporary row key.
            if tx is None:
                tx = checks.enter_context(self.database.transaction())  # One lookup transaction, opened only if needed.
            try:
                self.policy.record(tx, user, child, path_id(key))
            except AccessError:
                return False
            return True
        visible, hidden = {}, False
        with checks:
            for key, message in errors.items():
                # Walk table.<row>.field segments at any depth; each row must be visible.
                owner, rest, shown = model, key, True
                while shown:
                    parts = rest.split('.', 2)
                    owned = {child.name: child.model for child in self.children.values() if child.parent is owner}
                    if len(parts) == 3 and parts[0] in owned:
                        owner, rest = owned[parts[0]], parts[2]
                        shown = self.policy.allows(user, owner, 'read') and row_visible(owner, parts[1])
                        continue
                    name = parts[0]
                    shown = name not in owner.__declared_fields__ or name in (readable if owner is model else self.policy.fields(user, owner))
                    break
                if shown: visible[key] = message
                else: hidden = True
        if hidden: visible.setdefault('_form', 'One or more values are invalid')
        return visible

    def model_dispatch(self, scope, body, user, model, parts):
        path, method = scope['path'], scope['method']
        if len(parts) >= 5 and parts[4] in ('confirm', 'cancel', 'reopen', 'history', 'revisions', 'snapshot', 'diff'):
            from .lifecycle import route
            return route(self, scope, body, user, model, parts)
        if len(parts) == 5 and parts[4] in CORRECTIONS:
            if method != 'POST':
                raise AccessError(405, 'Method not allowed')
            if path_id(parts[3]) is None: raise AccessError(404, 'Record not found')
            from .corrections import dispatch
            return dispatch(self, scope, body, user, model, path_id(parts[3]), parts[4])
        if len(parts) == 5 and parts[4] == 'journal':
            if path_id(parts[3]) is None: raise AccessError(404, 'Record not found')
            from .journal import route
            return route(self, scope, body, user, model, path_id(parts[3]))
        if getattr(model, '__declaration_mode__', False) and method == 'DELETE' and len(parts) == 4:
            if path_id(parts[3]) is None: raise AccessError(404, 'Record not found')
            with self.context(user) as context:
                record = self.policy.record(context._tx, user, model, path_id(parts[3]), 'delete')
                if b'if-match' not in dict(scope.get('headers', [])): raise AccessError(428, 'A current If-Match revision is required')
                self.check_revision(scope, context._tx, user, record)
                context.delete(model, record.id)
            return 204, None, []
        # Legacy Action registrations keep their own route on document models.
        legacy_action = len(parts) == 6 and parts[4] == 'actions' and not hasattr(getattr(model, parts[5], None), '__action__')
        # Attachments and line pages have their own routes on every model.
        own_route = len(parts) >= 5 and parts[4] in ('attachments', 'lines', 'related', 'prints')
        if getattr(model, '__declaration_mode__', False) and method in ('POST', 'PATCH') and not legacy_action and not own_route:
            from .documents import Context
            record_id = None
            if len(parts) >= 4:
                if path_id(parts[3]) is None:
                    raise AccessError(404, 'Record not found')
                record_id = path_id(parts[3])
            action = parts[5] if len(parts) == 6 and parts[4] == 'actions' and method == 'POST' else None
            move = parts[5] if len(parts) == 6 and parts[4] == 'moves' and method == 'POST' else None
            if not ((len(parts) == 3 and method == 'POST') or (len(parts) == 4 and method == 'PATCH') or action or move):
                raise AccessError(405, 'Method not allowed')
            plan = self.registry.plan_for(model)
            recalculate = False
            if plan is not None and '_calculation' in body:
                body = dict(body)
                calculation = body.pop('_calculation')
                self.check_calculation(model, calculation)
                recalculate = calculation.get('recalculate', False)
            with self.context(user) as context:
                context._recalculate = (model, record_id) if recalculate and record_id else None
                context._person = None if action or move else (model, record_id)
                if record_id:
                    record = self.policy.record(context._tx, user, model, record_id, 'update')
                    if b'if-match' not in dict(scope.get('headers', [])):
                        raise AccessError(428, 'A current If-Match revision is required')
                    self.check_revision(scope, context._tx, user, record)
                result = context._write(model, record_id, {} if action else body, action=action,
                                        arguments=body if action else None, move=move)
                extra = self.availability(context, model, context._tx.get(model, result['id']))
            # The write returns every readable row; the ETag covers them all and the body embeds the first page.
            etag = self.revision({key: value for key, value in result.items() if key not in ('_row_ids', '_outcome')})
            result = {**self.embedded(result), **extra}
            if plan is not None:
                result['_calculation'] = {'language': plan['language'], 'model_schema_hash': plan['model_schema_hash']}
            return (201 if record_id is None else 200), result, [(b'etag', etag)]
        if len(parts) == 3 and method == 'GET':
            return self.page(scope, user, model)
        if len(parts) == 3 and method == 'POST':
            with self.database.transaction() as tx:
                record, result = self.create_plain(tx, user, model, body)
            return 201, result, [(b'location', f'/api/{parts[2]}/{record.id}'.encode())]
        if len(parts) == 4 and parts[3] in ('aggregate', 'measures') or len(parts) == 5 and parts[4] == 'measures':
            from .measures import route
            return route(self, scope, user, model, parts)
        if len(parts) < 4 or path_id(parts[3]) is None:
            raise AccessError(404, 'Record not found')
        record_id = path_id(parts[3])
        if len(parts) >= 6 and parts[4] == 'lines':
            if method != 'GET':
                raise AccessError(405, 'Method not allowed')
            return self.lines(scope, user, model, record_id, parts[5:])
        if len(parts) >= 5 and parts[4] == 'related':
            if method != 'GET':
                raise AccessError(405, 'Method not allowed')
            return self.related(scope, user, model, record_id, parts[5:])
        if len(parts) >= 5 and parts[4] == 'attachments':
            return self.files(user, model, record_id, parts[5:], method, body)
        if len(parts) >= 6 and parts[4] == 'prints' and getattr(self, 'prints', None) is not None:
            return self.prints.record_pdf(scope, user, model, record_id, parts[5:], method)
        if len(parts) == 6 and parts[4] == 'actions' and method == 'POST':
            action = self.actions.get((model, parts[5]))
            if action is None or user.role not in action.roles:
                raise AccessError(403, 'Action is unavailable')
            if body:
                raise ValidationError({'_form': 'This action takes no arguments'})
            with self.database.transaction() as tx:
                tx.actor = actor_id(user)
                record = self.policy.record(tx, user, model, record_id, 'update')
                if getattr(model, '__declaration_mode__', False) and b'if-match' not in dict(scope.get('headers', [])):
                    raise AccessError(428, 'A current If-Match revision is required')
                self.check_revision(scope, tx, user, record)
                with trusted():
                    outcome = action.handler(tx, user, record)
                self.changed(tx, model, record_id)
                record = self.policy.record(tx, user, model, record_id)
                result = self.detail(tx, user, record)
            if isinstance(outcome, JobReceipt):
                return 202, {'job': outcome.public(), 'record': result}, []
            return 200, result, []
        if len(parts) != 4 or method not in ('GET', 'PATCH', 'DELETE'):
            raise AccessError(405, 'Method not allowed')
        from .documents import Context
        with self.database.transaction() as tx:
            tx.actor = actor_id(user)
            record = self.policy.record(tx, user, model, record_id, {'GET': 'read', 'PATCH': 'update', 'DELETE': 'delete'}[method])
            if method != 'GET': self.check_revision(scope, tx, user, record)
            if method == 'DELETE':
                for child in self.children.values():
                    if child.parent is model:
                        # Lists are capped at ROW_LIMIT rows, so delete page by page until none are left.
                        while rows := tx.list(child.model, filters={child.link: record_id}, limit=ROW_LIMIT):
                            for row in rows:
                                self.policy.record(tx, user, child.model, row.id, 'delete')
                                tx.delete(child.model, row.id)
                self.changed(tx, model, record_id, deleted=True)
                tx.delete(model, record_id)
                tx.connection.execute('DELETE FROM evlet_attachments WHERE model=? AND record_id=?', (parts[2], record_id))
                return 204, None, []
            if method == 'PATCH':
                values, children = self.split_body(body)
                drop_echoes(model, values, record._values)
                self.policy.check_values(tx, user, model, values)
                self.controls.custom.enforce(model, dict(model(**{**record._values, **values})._values), record._values)
                record = tx.update(model, record_id, **values)
                self.policy.record(tx, user, model, record_id, 'update')
                self.save_children(tx, user, record, children)
                self.changed(tx, model, record_id)
            loaded = {} if method == 'GET' else None
            result = self.detail(tx, user, record, row_limit=None, loaded=loaded)
            extra = self.availability(Context(self, tx, user, readonly=True), model, record, loaded) if method == 'GET' else {}
        return 200, {**self.embedded(result), **extra}, [(b'etag', self.revision(result))]

    def availability(self, context, model, record, loaded=None):
        """Moves and actions the caller may use on a saved record now (`_moves`, `_actions`, `_locked`); not part of the ETag.

        `loaded` holds the owned rows a detail of the record just read (see `detail`), for guards that read rows.
        """
        from .journal import summary as journal_summary
        result = journal_summary(self, context._tx, model, record.id)
        if getattr(model, 'confirmable', False):
            from .lifecycle import metadata
            lifecycle = metadata(context, model, record.id)
            result['_locked'] = lifecycle['state'] != 'draft'
            result['_deletable'] = lifecycle['state'] == 'draft' and lifecycle['revision'] == 0
            result['_lifecycle_actions'] = [op for op, state in (('confirm', 'draft'), ('cancel', 'confirmed'), ('reopen', 'cancelled'))
                                            if lifecycle['state'] == state and self.policy.allows(context.user, model, op) and self.policy.allows(context.user, model, 'update')]
        if model in self.registry.effects:
            from .effects import ever_posted
            if ever_posted(context._tx.connection, self.registry, model, record.id):
                # A record that ever posted is kept: delete answers 409, so screens do not offer it.
                result['_deletable'] = False
        if model.__state__ is None and model not in self.registry.guards:
            return result
        from .states import availability
        from . import corrections
        reader = context.read_context()
        from .effects import summary
        return {**availability(self, reader, model, record, loaded), **corrections.availability(self, reader, model, record),
                **summary(self, reader, model, record), **result}

    def field_expressions(self, user, model):
        """Serialized finite expressions per readable field: compute, initial and the three rules.

        A calculation or initial is shown with its field, which is readable only when its inputs are.
        A rule is shown only when this role can read every input it reads; it is still enforced.
        """
        plan = self.registry.plan_for(model)
        if plan is None:
            return {}
        owners, path, item = {child.model: child for child in self.children.values()}, [], model
        while item in owners:
            path.insert(0, owners[item].name)
            item = owners[item].parent
        result = {}
        for entry in plan['fields']:
            if entry['path'] != path:
                continue
            kind = entry.get('kind') or 'compute'
            if kind in RULES and not self.policy.expression_allowed(user, model, entry['field'], kind):
                continue
            result.setdefault(entry['field'], dict(EMPTY_EXPRESSIONS))[kind] = entry['expression']
        return result

    def calculation_projection(self, user, model, plan):
        """Which calculated results this user may evaluate locally; never ships hidden inputs.

        A result is local only when it and its whole input closure are readable and every collection
        it aggregates is complete for this user. Other results are withheld (no declassification policy).
        The plan itself is shipped only when every calculated field is local for this user.
        """
        owners = {child.model: child for child in self.children.values()}
        models = {}
        for item in self.registry.models.values():
            path, current = [], item
            while current in owners:
                path.insert(0, owners[current].name)
                current = owners[current].parent
            if current is model:
                models[tuple(path)] = item
        def readable(target):
            return self.policy.fields(user, target) if target is not None and self.policy.allows(user, target, 'read') else set()
        capabilities, local, withheld = {}, True, set()
        for field in plan['fields']:
            target = models.get(tuple(field['path']))
            if field.get('kind'):
                # Rules and initial suggestions over inputs this role cannot read are left out of the shipped
                # plan (the server still applies them). Leaving out one that writes a value would make the
                # preview disagree with the save, so then no plan is shipped.
                readable_field = field['field'] in readable(target)
                if not readable_field or not self.policy.expression_allowed(user, target, field['field'], field['kind']):
                    withheld.add(field['target'])
                    local = local and (not readable_field or field['kind'] not in ('initial', 'applies_when'))
                continue
            # Link lookups are local only when the browser can read their records through the record routes.
            fetchable = all(self.registry.models[name] not in self.child_models for scope, name, _ in field['reads'] if scope == 'lookup')
            if field['field'] in readable(target) and fetchable:
                capabilities[field['target']] = 'local_universal'
                continue
            local = False
            # Fields this role cannot see at all are not named.
            if target is not None and self.policy.allows(user, target, 'read') and field['field'] in self.policy.presented(user, target):
                capabilities[field['target']] = 'unavailable'
        result = {'language': plan['language'], 'numeric_profile': plan['numeric_profile'],
                  'model_schema_hash': plan['model_schema_hash'], 'capabilities': capabilities}
        if local:
            # Ship only fields this role can read: no hidden names, bounds or defaults.
            from copy import deepcopy
            from .calc import fingerprint
            projected = deepcopy(dict(plan))
            projected['fields'] = [field for field in projected['fields'] if field['target'] not in withheld]
            # Owned tables this role cannot read are left out with everything below them.
            hidden_paths = [tuple(entry['path']) for key, entry in projected['tables'].items() if key != '$root'
                            and (models.get(tuple(entry['path'])) is None or not self.policy.allows(user, models[tuple(entry['path'])], 'read'))]
            for key in list(projected['tables']):
                path = tuple(projected['tables'][key]['path'])
                if any(path[:len(item)] == item for item in hidden_paths):
                    del projected['tables'][key]
            for entry in projected['tables'].values():
                entry['tables'] = [name for name in entry['tables'] if tuple(entry['path']) + (name,) not in hidden_paths]
            for entry in projected['tables'].values():
                target = models.get(tuple(entry['path']))
                visible = readable(target)
                entry['fields'] = {name: meta for name, meta in entry['fields'].items() if name in visible}
                # Looked-up fields are named only when this role can read them; withheld rules may read others.
                if 'lookups' in entry:
                    lookups = [{**lookup, 'fields': [name for name in lookup['fields'] if name in readable(self.registry.models[lookup['model']])]}
                               for lookup in entry['lookups']]
                    entry['lookups'] = [lookup for lookup in lookups if lookup['fields']]
                    if not entry['lookups']:
                        del entry['lookups']
            projected['source_plan_hash'] = plan['model_schema_hash']
            projected['model_schema_hash'] = fingerprint(projected)
            result['plan'] = projected
        return result

    def check_calculation(self, model, calculation):
        """A finite-v1 client names the plan it previewed; a different active plan is a conflict."""
        plan = self.registry.plan_for(model)
        if plan is None or not isinstance(calculation, dict) or set(calculation) - {'language', 'model_schema_hash', 'recalculate'} \
                or type(calculation.get('recalculate', False)) is not bool:
            raise ValidationError({'calculation': 'Invalid calculation metadata'})
        if calculation.get('language', plan['language']) != plan['language'] or calculation.get('model_schema_hash') != plan['model_schema_hash']:
            raise AccessError(409, 'SCHEMA_VERSION_MISMATCH: this document uses a newer calculation plan; reload it')

    @staticmethod
    def revision(detail):
        # `_audit` names people by their current display name, which must not change the record's revision.
        detail = {key: value for key, value in detail.items() if key != '_audit'}
        encoded = json.dumps(detail, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
        return ('"' + hashlib.sha256(encoded).hexdigest() + '"').encode()

    def record_revision(self, tx, user, record):
        """The ETag of a record as this user sees it: every readable row of every collection, not the first page."""
        return self.revision(self.detail(tx, user, record, row_limit=None))

    @staticmethod
    def embedded(detail, limit=EMBEDDED_ROWS):
        """A full detail cut to the first `limit` rows per collection, with `children_pages` for longer ones.

        Gives the same result as detail(row_limit=limit), so a response and its ETag come from one read.
        """
        if not isinstance(detail.get('children'), dict):
            return detail
        result, children = dict(detail), {}
        for name, rows in detail['children'].items():
            children[name] = [Business.embedded(row, limit) for row in rows[:limit]]
            if len(rows) > limit:
                result['children_pages'] = {**result.get('children_pages', {}), name: {'total': len(rows), 'limit': limit, 'partial': True}}
        result['children'] = children
        return result

    def check_revision(self, scope, tx, user, record):
        expected = dict(scope.get('headers', [])).get(b'if-match')
        if expected is not None and expected != self.record_revision(tx, user, record):
            raise AccessError(409, 'This record changed since you opened it. Reload it before saving.')

    def accounts(self, scope, body, user):
        """The older account routes: the same access check, limits and audit rows as Controls Users."""
        path, method = scope['path'], scope['method']
        if not self.controls.allows(scope, user):
            raise AccessError(403, 'Administrator access required')
        self.controls.check(scope, user)
        if method not in ('GET', 'HEAD'):
            # Account changes are Controls changes: a recent two-step code, as in Controls.
            self.settings.recently_verified(user)
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
                return 201, self.controls.add_user(user, body).public(), []
        match = re.fullmatch(r'/api/auth/users/([1-9][0-9]{0,18})', path)
        if match and method == 'PATCH' and int(match[1]) <= 2**63-1:
            if not isinstance(body, dict) or 'sessions' in body:
                raise ValidationError({'_form': 'Use role, active, or password'})
            self.controls.change_user(user, int(match[1]), body)
            return 204, None, []
        raise AccessError(405, 'Method not allowed')

    def create_plain(self, tx, user, model, body):
        """POST /api/{model} for a model that is not declarative: permissions, Controls, naming, child rows."""
        self.policy.require(user, model, 'create')
        tx.actor = actor_id(user)
        values, children = self.split_body(body)
        from .documents import drop_empty_name, host_today
        from .sequences import name_new, naming_rules
        drop_empty_name(self.registry, model, values)
        rules = naming_rules(self.registry, model)
        if rules is not None and rules.field == 'name' and rules.system and values.get('name') not in (None, ''):
            raise ValidationError({'name': 'Assigned on save'})
        drop_echoes(model, values)
        self.policy.check_values(tx, user, model, values)
        values = self.controls.custom.prepare(model, values)
        cleaned = dict(model(**values)._values)
        self.controls.custom.enforce(model, cleaned, None)
        name = name_new(tx.connection, self.registry, model, cleaned, host_today(self), user.username, record=model(**cleaned))
        if name:
            values[self.registry.naming[model].field] = name
        record = tx.create(model, **values)
        self.policy.record(tx, user, model, record.id, 'create')
        self.save_children(tx, user, record, children)
        self.changed(tx, model, record.id)
        return record, self.detail(tx, user, record)

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
            # Child validate() errors may name child fields this user cannot read.
            models = [child.model for child in self.children.values() if child.parent is type(parent)]
            errors = {key: value for key, value in exc.errors.items()
                      if not any(key in model.__declared_fields__ and key not in self.policy.fields(user, model) for model in models)}
            parts = [str(ValidationError(errors))] if errors else []
            if len(errors) < len(exc.errors): parts.append('One or more values are invalid')
            raise ValidationError({'children': '; '.join(parts)}) from None

    def _save_children(self, tx, user, parent, collections):
        for name, rows in collections.items():
            child = self.children.get((type(parent), name))
            if child is None or not isinstance(rows, list) or len(rows) > ROW_LIMIT:
                raise ValidationError({'children': f'Use a known child collection with at most {ROW_LIMIT} rows'})
            if tx.count(child.model, filters={child.link: parent.id}) > ROW_LIMIT:
                raise ValidationError({'children': f'This collection exceeds the {ROW_LIMIT}-row editor limit'})
            existing, hidden = {}, set()
            for row in tx.list(child.model, filters={child.link: parent.id}, limit=ROW_LIMIT):
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
                original = existing.get(item_id(row_id)) if row_id is not None else None
                drop_echoes(child.model, values, original._values if original else None)
                self.policy.check_values(tx, user, child.model, values)
                if row_id is not None:
                    try:
                        row_id = RecordId(row_id)
                    except ValueError:
                        row_id = None
                    if row_id is None or row_id not in existing or row_id in kept:
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

    def detail(self, tx, user, record, *, row_limit=EMBEDDED_ROWS, loaded=None):
        """A record with its readable owned rows at every depth, in position order.

        Each collection embeds at most `row_limit` rows (None embeds all). A collection with more
        readable rows is listed under `children_pages` with its total so a client can page it.
        Rows load one table level at a time, so the statement count does not grow with the rows.
        A `loaded` dict receives the saved rows read ({(child, owner id): records}) and is marked
        partial when some saved rows were left out (unreadable, outside the row scope or not embedded).
        """
        result = self.policy.serialize(tx, user, record)
        self.embed(tx, user, type(record), {record.id: result}, row_limit, loaded)
        if getattr(type(record), 'confirmable', False):
            from .lifecycle import metadata
            from .documents import Context
            result['_lifecycle'] = metadata(Context(self, tx, user, readonly=True), type(record), record.id)
        if getattr(type(record), '__revisable__', False) and 'revises' in self.policy.fields(user, type(record)):
            # The other direction of `revises`, found by a lookup; there is no column for it.
            from .corrections import revised_by
            result['revised_by'] = revised_by(self, tx, user, type(record), record.id)
        info = audit_detail(self, tx, user, record)
        if info is not None:
            result['_audit'] = info
        return result

    def embed(self, tx, user, model, results, row_limit=EMBEDDED_ROWS, loaded=None):
        """Add readable owned rows to serialized records of one model ({id: result}), level by level."""
        from .documents import owned_rows
        for child in self.children.values():
            if child.parent is not model:
                continue
            readable = self.policy.allows(user, child.model, 'read')
            if loaded is not None and (not readable or row_limit is not None or self.policy.rule(user, child.model).scope is not None):
                loaded['partial'] = True
            if not readable:
                continue
            found = owned_rows(tx, child, list(results), scope=self.policy.scope_sql(user, child.model), limit=row_limit)
            if loaded is not None:
                loaded.update({(child, owner): found.get(owner, ((), 0))[0] for owner in results})
            records = [record for owner in results for record in found.get(owner, ((), 0))[0]]
            values = dict(zip((record.id for record in records), self.policy.serialize_many(tx, user, records)))
            for value in values.values():
                value.pop(child.link, None)
                value.pop('evlet_position', None)
            for owner, result in results.items():
                rows, total = found.get(owner, ((), 0))
                result.setdefault('children', {})[child.name] = [values[record.id] for record in rows]
                if total > len(rows):
                    result.setdefault('children_pages', {})[child.name] = {'total': total, 'limit': row_limit, 'partial': True}
            if values:
                self.embed(tx, user, child.model, values, row_limit, loaded)

    def lines(self, scope, user, model, record_id, path):
        """GET /api/{model}/{id}/lines/{name}[/{row id}/{name}...]: one page of a readable owned collection."""
        from .documents import owned_rows
        try:
            raw = scope.get('query_string', b'')
            query = parse_qs(raw.decode('ascii'), keep_blank_values=True, max_num_fields=2) if len(raw) <= 256 else None
            if query is None or set(query) - {'limit', 'offset'} or any(len(values) != 1 for values in query.values()):
                raise ValueError()
            limit, offset = query.get('limit', [str(EMBEDDED_ROWS)])[0], query.get('offset', ['0'])[0]
            if not re.fullmatch('[0-9]{1,19}', limit) or not re.fullmatch('[0-9]{1,19}', offset):
                raise ValueError()
            limit, offset = int(limit), int(offset)
            if not 1 <= limit <= ROW_LIMIT or offset > 2**63-1:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValidationError({'query': f'Use a limit of 1 to {ROW_LIMIT} rows and a nonnegative offset'})
        if len(path) % 2 != 1:
            raise AccessError(404, 'Route not found')
        with self.database._connection() as connection:
            connection.execute('BEGIN')
            tx = Session(connection, self.registry)
            self.policy.record(tx, user, model, record_id)
            owner, owner_id = model, record_id
            for index in range(0, len(path), 2):
                child = self.children.get((owner, path[index]))
                if child is None or not self.policy.allows(user, child.model, 'read'):
                    raise AccessError(404, 'Collection not found')
                if index + 1 == len(path):
                    break
                if path_id(path[index + 1]) is None:
                    raise AccessError(404, 'Record not found')
                row = self.policy.record(tx, user, child.model, path_id(path[index + 1]))
                if getattr(row, child.link) != owner_id:
                    raise AccessError(404, 'Record not found')
                owner, owner_id = child.model, row.id
            where, values = self.policy.scope_sql(user, child.model)
            where += (' AND ' if where else ' WHERE ') + f'record.{identifier(child.link)} = ?'
            values = [*values, owner_id]
            table = identifier(self.registry.name_for(child.model))
            order = ' ORDER BY record."evlet_position", record.id' if 'evlet_position' in child.model.__fields__ else ' ORDER BY record.id'
            rows = [child.model._from_row(row) for row in connection.execute(
                f'SELECT record.* FROM {table} AS record{where}{order} LIMIT ? OFFSET ?', [*values, limit, offset]).fetchall()]
            total = connection.execute(f'SELECT count(*) FROM {table} AS record{where}', values).fetchone()[0]
            items = dict(zip((row.id for row in rows), self.policy.serialize_many(tx, user, rows)))
            for item in items.values():
                item.pop(child.link, None)
                item.pop('evlet_position', None)
            if items:
                self.embed(tx, user, child.model, items)
        return 200, {'items': list(items.values()), 'total': total, 'limit': limit, 'offset': offset}, []

    def visible_relations(self, user, model):
        """Relations whose records this role may read and whose Link it may see."""
        return [relation for relation in self.relations.get(model, ())
                if self.policy.allows(user, relation.source, 'read') and relation.link in self.policy.fields(user, relation.source)]

    def relation_order(self, user, relation):
        """The part of a relation's order this role may use, by the rule of the list route's order_by.

        A field must be readable, and a Link must not point to a model with a row scope for the role,
        since ordering by it would reveal which rows link to records the role cannot see.
        """
        source = relation.source
        readable = self.policy.fields(user, source)
        def allowed(name):
            field = source.__fields__.get(name)
            return name == 'id' or name in readable and not (
                isinstance(field, Link) and self.policy.rule(user, field.target).scope is not None)
        return [item for item in relation.order if allowed(item.lstrip('-'))] or ['-id']

    def related(self, scope, user, model, record_id, path):
        """GET /api/{model}/{id}/related/{name}: one page of the records whose Link points to a readable record."""
        relation = next((item for item in self.visible_relations(user, model) if len(path) == 1 and item.name == path[0]), None)
        if relation is None:
            raise AccessError(404, 'Relation not found')
        try:
            raw = scope.get('query_string', b'')
            query = parse_qs(raw.decode('ascii'), keep_blank_values=True, max_num_fields=2) if len(raw) <= 256 else None
            if query is None or set(query) - {'limit', 'offset'} or any(len(values) != 1 for values in query.values()):
                raise ValueError()
            limit, offset = query.get('limit', [str(relation.limit)])[0], query.get('offset', ['0'])[0]
            if not re.fullmatch('[0-9]{1,19}', limit) or not re.fullmatch('[0-9]{1,19}', offset):
                raise ValueError()
            limit, offset = int(limit), int(offset)
            if not 1 <= limit <= ROW_LIMIT or offset > 2**63-1:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValidationError({'query': f'Use a limit of 1 to {ROW_LIMIT} rows and a nonnegative offset'})
        source = relation.source
        order = self.relation_order(user, relation)
        with self.database._connection() as connection:
            connection.execute('BEGIN')
            tx = Session(connection, self.registry)
            self.policy.record(tx, user, model, record_id)
            where, values = self.policy.scope_sql(user, source)
            where += (' AND ' if where else ' WHERE ') + f'record.{identifier(relation.link)} = ?'
            values = [*values, record_id]
            table = identifier(self.registry.name_for(source))
            ordering = SqlQuery(self.registry, source).ordering(order)
            rows = [source._from_row(row) for row in connection.execute(
                f'SELECT record.* FROM {table} AS record{where}{ordering} LIMIT ? OFFSET ?', [*values, limit, offset]).fetchall()]
            total = connection.execute(f'SELECT count(*) FROM {table} AS record{where}', values).fetchone()[0]
            items = self.policy.serialize_many(tx, user, rows)
        return 200, {'items': items, 'total': total, 'limit': limit, 'offset': offset}, []

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
            order = query.get('order_by', [self.controls.custom.default_order(self.policy, user, model)])[0].split(',')
            limit, offset = int(query.get('limit', ['50'])[0]), int(query.get('offset', ['0'])[0])
            if not isinstance(filters, (dict, list)) or not 1 <= limit <= ROW_LIMIT or not 0 <= offset <= 2**63-1:
                raise ValueError()
        except (ValueError, UnicodeError, RecursionError):
            raise ValidationError({'query': 'Invalid query parameters'})
        children = self.children.values()
        self.policy.query_fields(user, model, filters, order, database=self.database, children=children)
        compiler = SqlQuery(self.registry, model, children=children)
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
            # One visibility check per Link target for the page, not per row.
            items = self.policy.serialize_many(tx, user, [model._from_row(row) for row in rows])
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
            record = self.policy.record(tx, user, model, record_id, 'read' if method == 'GET' else 'update')
            if method in ('POST', 'DELETE'):
                from .documents import Context
                from .states import check_attachments
                check_attachments(Context(self, tx, user, readonly=True), model, record)
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
                limits = self.attachment_limits[model]
                if not 1 <= len(content) <= limits.max_size:
                    raise ValidationError({'attachment': f'Use a nonempty file up to {size_text(limits.max_size)}'})
                if tx.connection.execute('SELECT count(*) FROM evlet_attachments WHERE model=? AND record_id=?', args).fetchone()[0] >= limits.max_files:
                    raise ValidationError({'attachment': f'Use at most {limits.max_files} attachment{"s" if limits.max_files > 1 else ""} per record'})
                file_id = self.storage.add(tx, *args, filename, content)
                from .journal import file
                file(self, tx, user, model, record_id, 'added', {'id': file_id, 'name': filename, 'size': len(content)})
                self.changed(tx, model, record_id)
                return 201, {'id': file_id, 'name': filename, 'size': len(content)}, []
            row = tx.connection.execute('SELECT * FROM evlet_attachments WHERE model=? AND record_id=? AND id=?', (*args, file_id)).fetchone()
            if row is None:
                raise AccessError(404, 'Attachment not found')
            if method == 'GET':
                return 200, Download(self.storage.read(row)), [(b'content-type', b'application/octet-stream'), (b'content-disposition', ("attachment; filename*=UTF-8''" + quote(row['name'], safe='')).encode()), (b'content-security-policy', b"default-src 'none'; sandbox")]
            if method == 'DELETE':
                tx.connection.execute('DELETE FROM evlet_attachments WHERE id=?', (file_id,))
                from .journal import file
                file(self, tx, user, model, record_id, 'removed', {'id': file_id, 'name': row['name']})
                self.changed(tx, model, record_id)
                return 204, None, []
        raise AccessError(405, 'Method not allowed')
