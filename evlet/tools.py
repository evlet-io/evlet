"""The Tool Engine: one set of tools for Eva, MCP clients and the API.

Each tool is a call to an existing authenticated route, made in process through Business.dispatch as
the person the caller acts for. Account, tenant, row, field and action checks are the browser's, and
they run again on every call, so a tool listed earlier grants nothing. The engine adds what the routes
don't have: one definition per tool (MCP and Eva schemas come from it), argument checks, approvals for
changes that need a click, and one log of every call (`evlet_tool_calls`).

    tools = business.tools
    tools.listing(user)                      # what this person (and origin) may call now
    tools.execute(user, 'list_records', {'model': 'invoice'}, scope)

Origins: eva and schedule (Eva), mcp (remote clients and `evlet mcp`), api (sessions and API keys).
"""

from dataclasses import dataclass, field
import hashlib
import json
import re
import time
from urllib.parse import urlencode

from .auth import AccessError
from .connector import ConnectorError
from .database import ConflictError, NotFoundError
from .identity import SCHEMA as ID_SCHEMA, RecordId, jsonable
from .models import ValidationError, checked_body

SAFE_INTEGER = 2**53 - 1
MODEL = {'type': 'string', 'pattern': '^[a-z][a-z0-9_]{0,127}$'}
ID = {'type': 'integer', 'minimum': 1, 'maximum': SAFE_INTEGER}
# A record id is its 13-character text form; ID is for jobs and files.
RECORD = dict(ID_SCHEMA)
PAGE = {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': SAFE_INTEGER}}
ORIGINS = ('eva', 'schedule', 'mcp', 'api')
# Confirmation groups: which kinds of allowed change can still need an approval click.
GROUPS = ('create', 'update', 'confirm', 'action', 'destructive')
OUTCOMES = ('ok', 'denied', 'failed', 'approval')
KEEP_CALLS, KEEP_APPROVALS, APPROVAL_SECONDS = 90 * 86400, 30 * 86400, 86400
# The call log. Framework step 0002_tool_calls creates it too, before moving the old MCP log in.
CALLS_TABLE = (
    "CREATE TABLE IF NOT EXISTS evlet_tool_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, user_id INTEGER NOT NULL, "
    "origin TEXT NOT NULL CHECK(origin IN ('eva','schedule','mcp','api')), client TEXT, client_id INTEGER, key_id INTEGER, run_id INTEGER, "
    "tool TEXT NOT NULL, model TEXT, record_id INTEGER, kind TEXT NOT NULL CHECK(kind IN ('read','write')), "
    "outcome TEXT NOT NULL CHECK(outcome IN ('ok','denied','failed','approval')), duration_ms INTEGER, inputs TEXT, error TEXT)",
    'CREATE INDEX IF NOT EXISTS evlet_tool_call_at ON evlet_tool_calls(at)',
    'CREATE INDEX IF NOT EXISTS evlet_tool_call_user ON evlet_tool_calls(user_id, at)',
    'CREATE INDEX IF NOT EXISTS evlet_tool_call_client ON evlet_tool_calls(client_id, at)',
    'CREATE INDEX IF NOT EXISTS evlet_tool_call_run ON evlet_tool_calls(run_id)',
)
# Text a file tool returns at most, and the largest file it reads.
TEXT_LIMIT, FILE_LIMIT = 60_000, 2 * 1024 * 1024
LOCAL_CLIENT = 'Local MCP connector'


@dataclass(frozen=True)
class Tool:
    """One tool: its schema, whether it changes data, and which confirmation group a change belongs to."""
    name: str
    description: str
    properties: dict
    required: tuple = ()
    kind: str = 'read'  # read, write or destructive
    group: str | None = None  # a GROUPS entry; change_state decides per operation
    origins: tuple = ORIGINS
    label: str = ''  # what the activity row says while it runs, such as "Reading records"

    def schema(self):
        return {'type': 'object', 'properties': self.properties, 'required': list(self.required), 'additionalProperties': False}

    @property
    def writes(self):
        return self.kind != 'read'

    def mcp(self):
        return {'name': self.name, 'description': self.description, 'inputSchema': self.schema(),
                'annotations': {'readOnlyHint': not self.writes, 'destructiveHint': self.writes, 'idempotentHint': not self.writes, 'openWorldHint': self.writes}}

    def public(self):
        return {'name': self.name, 'description': self.description, 'input_schema': self.schema(), 'kind': self.kind, 'group': self.group}


TOOLS = (
    Tool('discover_models', 'List permitted models and operations. Use describe_model for fields before creating or changing records.',
         {**PAGE, 'search': {'type': 'string', 'maxLength': 200}}, label='Looking at the available data'),
    Tool('describe_model', 'Read one permitted model, validation metadata, input schemas, child collections, and registered actions.',
         {'model': MODEL}, ('model',), label='Reading {model} fields'),
    Tool('list_records', 'Query permitted records. Filters use the described fields and operators; all results obey row and field permissions.',
         {'model': MODEL, **PAGE, 'search': {'type': 'string', 'maxLength': 200}, 'filters': {'type': 'object'}, 'order_by': {'type': 'string', 'maxLength': 500}},
         ('model',), label='Reading {model}'),
    Tool('get_record', 'Read a permitted record and its accessible child collections.', {'model': MODEL, 'id': RECORD}, ('model', 'id'), label='Reading {model} {id}'),
    Tool('aggregate_records', 'Totals, counts, minimums and maximums over permitted records, optionally grouped. '
         'measures: comma separated field:function[:name] such as amount:sum:total,count:count. group_by: comma separated fields, date fields may add :month or :year.',
         {'model': MODEL, 'measures': {'type': 'string', 'maxLength': 500}, 'group_by': {'type': 'string', 'maxLength': 200},
          'filters': {'type': 'object'}, 'search': {'type': 'string', 'maxLength': 200}}, ('model', 'measures'), label='Totalling {model}'),
    Tool('record_history', "Read a permitted record's journal: who changed what and when, notes and calls.",
         {'model': MODEL, 'id': RECORD, **PAGE}, ('model', 'id'), label='Reading the history of {model} {id}'),
    Tool('list_reports', 'List the reports this person can run, with their filters and columns.', {}, label='Looking at reports'),
    Tool('run_report', 'Run a permitted report with its filters. Returns one page of rows.',
         {'report': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_.]{0,127}$'}, 'filters': {'type': 'object'}, **PAGE}, ('report',), label='Running the {report} report'),
    Tool('list_files', 'List the files attached to a permitted record.', {'model': MODEL, 'id': RECORD}, ('model', 'id'), label='Listing files on {model} {id}'),
    Tool('read_file', "Read the text of a file attached to a permitted record. Files that aren't text return their size only.",
         {'model': MODEL, 'id': RECORD, 'file_id': ID}, ('model', 'id', 'file_id'), label='Reading a file'),
    Tool('list_services', 'List the business services this app offers to assistants, with their arguments.', {}, label='Looking at services'),
    Tool('job_status', 'Read the status of a queued action owned by this account, or accessible to its administrator.', {'id': ID}, ('id',), label='Checking a background task'),
    Tool('create_record', 'Create a record using its described create schema. Backend validation and permissions apply. Do not retry an uncertain write automatically.',
         {'model': MODEL, 'values': {'type': 'object'}}, ('model', 'values'), kind='write', group='create', label='Creating {model}'),
    Tool('update_record', 'Update supplied values only. Supplied child collections replace their rows; omit children to preserve them.',
         {'model': MODEL, 'id': RECORD, 'values': {'type': 'object'}}, ('model', 'id', 'values'), kind='write', group='update', label='Updating {model} {id}'),
    Tool('change_state', 'Confirm, cancel or reopen a document. Confirming finalises it; cancelling and reopening need a reason for cancel.',
         {'model': MODEL, 'id': RECORD, 'operation': {'type': 'string', 'enum': ['confirm', 'cancel', 'reopen']}, 'reason': {'type': 'string', 'maxLength': 500}},
         ('model', 'id', 'operation'), kind='write', label='Changing the state of {model} {id}'),
    Tool('run_action', 'Execute one registered permitted action on a record. It may modify records or enqueue work.',
         {'model': MODEL, 'id': RECORD, 'action': MODEL}, ('model', 'id', 'action'), kind='write', group='action', label='Running {action} on {model} {id}'),
    Tool('call_service', 'Call a business service from list_services with its arguments.',
         {'service': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_.]{0,200}$'}, 'arguments': {'type': 'object'}}, ('service',),
         kind='write', group='action', label='Running {service}'),
    Tool('delete_record', 'Delete a permitted record and owned children. This can permanently remove data.',
         {'model': MODEL, 'id': RECORD}, ('model', 'id'), kind='destructive', group='destructive', label='Deleting {model} {id}'),
)
BY_NAME = {tool.name: tool for tool in TOOLS}
NAMES = tuple(BY_NAME)


class ApprovalNeeded(Exception):
    """A change the policy says needs a person's click. approval is the evlet_tool_approvals row (public form)."""
    def __init__(self, approval):
        self.approval = approval
        super().__init__('This change needs approval')


def arguments(tool, values):
    """Check values against the tool's schema: known keys, required keys, types, bounds, patterns and enums."""
    schema = tool.properties
    if not isinstance(values, dict) or set(values) - schema.keys() or set(tool.required) - values.keys():
        raise ConnectorError(422, 'Use the documented tool arguments')
    for key, value in values.items():
        spec = schema[key]
        kind = spec['type']
        valid = (kind == 'object' and isinstance(value, dict)) or (kind == 'string' and isinstance(value, str)) or (kind == 'integer' and type(value) is int)
        if not valid or (kind == 'string' and ('\x00' in value or len(value) > spec.get('maxLength', 128))) \
                or (kind == 'integer' and not spec['minimum'] <= value <= spec['maximum']):
            raise ConnectorError(422, f'Invalid argument: {key}')
        if 'pattern' in spec and not re.fullmatch(spec['pattern'], value) or 'enum' in spec and value not in spec['enum']:
            raise ConnectorError(422, f'Invalid argument: {key}')
    return values


def group_of(tool, values):
    if tool.name == 'change_state':
        return 'confirm' if values.get('operation') == 'confirm' else 'destructive'
    return tool.group


def redacted(values):
    """What the log keeps of a call's arguments: identifiers as they are, field names only for values and filters."""
    kept = {}
    for key, value in values.items():
        if key in ('values', 'filters', 'arguments') and isinstance(value, dict):
            kept[key] = sorted(value)[:50]
        elif key == 'search':
            kept[key] = '…'
        elif key == 'reason':
            kept[key] = '…'
        else:
            kept[key] = value
    return kept


def label(tool, values):
    """The activity line for a call: "Reading invoice", with the arguments it names."""
    text = tool.label or tool.name.replace('_', ' ').capitalize()
    return re.sub(r'\{(\w+)\}', lambda match: str(values.get(match[1], '')).replace('_', ' '), text).replace('  ', ' ').strip()


def origin_of(user):
    access = getattr(user, 'access', None)
    if access is None:
        return 'api'
    if access.origin in ('eva', 'schedule'):
        return access.origin
    return 'mcp' if access.assistant is not None else 'api'


class Dispatch:
    """In-process requests to the ordinary routes, as the user, answering (payload, headers)."""

    def __init__(self, business, scope, user):
        self.business, self.user = business, user
        self.host = dict((scope or {}).get('headers', [])).get(b'host', b'') or business.tools.primary_host()
        self.scope = {key: value for key, value in (scope or {}).items() if key in ('type', 'scheme', 'client', 'server', 'http_version')}
        self.scope.setdefault('type', 'http')
        self.scope.setdefault('client', ('127.0.0.1', 0))

    def __call__(self, method, path, body=None, headers=()):
        from .app import HTTPError
        route, _, query = path.partition('?')
        inner = {**self.scope, 'method': method, 'path': route, 'query_string': query.encode('ascii'),
                 'headers': [(b'host', self.host), (b'x-evlet-request', b'1'), *headers], 'evlet.assistant': self.user}
        try:
            status, payload, response = self.business.dispatch(inner, None if method in ('GET', 'DELETE') else json.loads(json.dumps(body or {})))
        except (AccessError, HTTPError) as exc:
            raise ConnectorError(exc.status, exc.message) from None
        except ValidationError as exc:
            raise ConnectorError(422, 'Validation failed', exc.errors) from None
        except NotFoundError:
            raise ConnectorError(404, 'Record not found') from None
        except ConflictError as exc:
            raise ConnectorError(409, str(exc)) from None
        if status >= 400:
            raise ConnectorError(status, payload.get('error', 'Request failed') if isinstance(payload, dict) else 'Request failed')
        return (None if status == 204 else jsonable(payload)), dict(response)

    def get(self, path):
        return self('GET', path)[0]

    def revision(self, model, record_id):
        """The record's current ETag, read as the user: the If-Match of the change that follows."""
        payload, headers = self('GET', f'/api/{model}/{record_id}')
        return payload, headers.get(b'etag', b'')


class Tools:
    def __init__(self, business):
        self.business, self.database = business, business.database
        self.pruned = 0.0
        # Origin-specific tools added by other parts of Evlet: Eva's create_file. name -> (Tool, handler(user, values, run_id)).
        self.extra = {}

    def initialize(self):
        with self.database.transaction() as tx:
            connection = tx.connection
            for statement in (
                *CALLS_TABLE,
                "CREATE TABLE IF NOT EXISTS evlet_tool_approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, "
                "origin TEXT NOT NULL, client TEXT, run_id INTEGER, tool TEXT NOT NULL, arguments TEXT NOT NULL, digest TEXT NOT NULL, summary TEXT NOT NULL, "
                "status TEXT NOT NULL CHECK(status IN ('pending','approved','declined','used','expired')), created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, decided_at INTEGER)",
                'CREATE INDEX IF NOT EXISTS evlet_tool_approval_user ON evlet_tool_approvals(user_id, status)',
                'CREATE INDEX IF NOT EXISTS evlet_tool_approval_digest ON evlet_tool_approvals(digest)',
            ):
                connection.execute(statement)
        self.prune()

    def prune(self):
        """Remove calls after 90 days and decided approvals after 30; expire pending ones. At most once an hour."""
        now = time.time()
        if now - self.pruned < 3600:
            return
        self.pruned = now
        now = int(now)
        with self.database.transaction() as tx:
            tx.connection.execute('DELETE FROM evlet_tool_calls WHERE at<?', (now - KEEP_CALLS,))
            tx.connection.execute("UPDATE evlet_tool_approvals SET status='expired' WHERE status IN ('pending','approved') AND expires_at<=?", (now,))
            tx.connection.execute('DELETE FROM evlet_tool_approvals WHERE created_at<?', (now - KEEP_APPROVALS,))

    def primary_host(self):
        from urllib.parse import urlsplit
        origins = sorted(self.business.auth.origins)
        return urlsplit(origins[0]).netloc.encode() if origins else b'127.0.0.1'

    # ---------- what may be called ----------

    def definition(self, name):
        if name in BY_NAME:
            return BY_NAME[name]
        if name in self.extra:
            return self.extra[name][0]
        return None

    def policy(self, origin):
        """The workspace settings for an origin: (disabled tool names, writes allowed)."""
        settings = self.business.controls.settings
        if origin == 'mcp':
            return set(settings['mcp.disabled_tools']), settings['mcp.allow_writes']
        if origin in ('eva', 'schedule'):
            return set(settings.get('eva.disabled_tools', ())), True
        return set(), True

    def listing(self, user, origin=None):
        """The tools this user may call through this origin now. Calls are checked again when they run."""
        origin = origin or origin_of(user)
        disabled, writes = self.policy(origin)
        access = getattr(user, 'access', None)
        writes = writes and (access is None or access.write)
        schema = self.business.schema(user)
        operations = {name for entry in schema['models'] for name, allowed in entry['permissions'].items() if allowed}
        # Dialogs are listed with actions in the schema, but they are forms, not tool calls.
        actions = any(not action.get('dialog') for entry in schema['models'] for action in entry.get('actions') or ())
        lifecycle = any(entry.get('lifecycle') for entry in schema['models'])
        files = any(entry.get('attachments') for entry in schema['models'])
        journals = any(entry.get('journal') for entry in schema['models'])
        services = bool(self.services(user))
        result = []
        for tool in (*TOOLS, *(entry[0] for entry in self.extra.values())):
            if origin not in tool.origins or tool.name in disabled or (tool.writes and not writes):
                continue
            if tool.name == 'job_status' and not self.business.jobs:
                continue
            if tool.name in ('list_reports', 'run_report') and not schema.get('reports'):
                continue
            if tool.name in ('list_services', 'call_service') and not services:
                continue
            if tool.name in ('create_record', 'update_record', 'delete_record') and tool.name.split('_')[0] not in operations:
                continue
            if tool.name == 'run_action' and not actions or tool.name == 'change_state' and not lifecycle:
                continue
            if tool.name in ('list_files', 'read_file') and not files or tool.name == 'record_history' and not journals:
                continue
            result.append(tool)
        return result

    def services(self, user):
        """Business services an app marked for assistants (@api(..., tool=True)) that this user may call."""
        found = []
        for endpoint in getattr(self.business, 'apis', ()):
            if not getattr(endpoint, 'tool', False) or endpoint.public:
                continue
            try:
                model = self.business.registry.resolve(endpoint.permission[0])
            except Exception:
                continue
            if self.business.policy.allows(user, model, endpoint.permission[1]):
                found.append(endpoint)
        return found

    # ---------- running a call ----------

    def execute(self, user, name, values, scope=None, *, run_id=None, client=None):
        """Run one tool call as user and log it. Raises ConnectorError (refused or failed) or ApprovalNeeded."""
        self.prune()
        origin = origin_of(user)
        tool = self.definition(name)
        if tool is None or origin not in tool.origins:
            raise ConnectorError(404, 'Unknown tool')
        access = user.access
        client = client or self.client_label(user)
        client_id = access.assistant if access is not None and access.assistant else None
        started = time.monotonic()
        call_id = None
        record_id = values.get('id') if isinstance(values, dict) and type(values.get('id')) is int else None
        model = values.get('model') if isinstance(values, dict) and isinstance(values.get('model'), str) else None
        try:
            disabled, writes = self.policy(origin)
            if name in disabled:
                raise ConnectorError(403, 'This tool is turned off for this workspace.')
            if tool.writes and not writes:
                raise ConnectorError(403, 'Your admin has turned off changes from AI assistants. They can read and search only.')
            if tool.writes and access is not None and not access.write:
                raise ConnectorError(403, "This assistant can't make changes. You can allow it in Settings, under AI." if origin == 'mcp' else 'This key can only read.')
            values = arguments(tool, values)
            call_id = self.admit(user, origin, client, client_id, run_id, tool, values)
            result = self.run(user, origin, tool, values, scope, run_id, client)
            if name == 'create_record' and isinstance(result, dict) and type(result.get('id')) is int:
                record_id = result['id']
            self.finish(call_id, 'ok', started, record_id=record_id)
            return result
        except ApprovalNeeded:
            self.finish(call_id, 'approval', started)
            raise
        except Exception as exc:
            status = getattr(exc, 'status', None)
            outcome = 'denied' if status in (401, 403) else 'failed'
            message = getattr(exc, 'message', None) or ('Tool call failed' if not isinstance(exc, ConnectorError) else str(exc))
            if call_id is None:
                # Refused before it was admitted (settings, arguments, rate): logged so refusals show up too.
                if not (isinstance(exc, ConnectorError) and status == 429):
                    self.log(user, origin, client, client_id, run_id, tool, values, outcome, started, message, model=model, record_id=record_id)
            else:
                self.finish(call_id, outcome, started, error=message)
            raise

    def client_label(self, user):
        access = user.access
        if access is None:
            return None
        if access.origin in ('eva', 'schedule'):
            return 'Eva'
        if access.assistant is not None:
            if not access.assistant:
                return LOCAL_CLIENT
            with self.database._connection() as connection:
                row = connection.execute('SELECT client_name FROM evlet_mcp_clients WHERE id=?', (access.assistant,)).fetchone()
            return row[0] if row else None
        if access.key_id is not None:
            with self.database._connection() as connection:
                row = connection.execute('SELECT name FROM evlet_api_keys WHERE id=?', (access.key_id,)).fetchone()
            return row[0] if row else None
        return None

    def admit(self, user, origin, client, client_id, run_id, tool, values):
        """Record the call before it runs. MCP clients get their per-minute rate, counted across processes."""
        now = int(time.time())
        with self.database.transaction() as tx:
            connection = tx.connection
            if origin == 'mcp':
                rate = min(60, self.business.controls.settings['mcp.rate'])
                recent = connection.execute('SELECT count(*) FROM evlet_tool_calls WHERE origin=? AND user_id=? AND COALESCE(client_id,0)=? AND at>?',
                                            ('mcp', user.id, client_id or 0, now - 60)).fetchone()[0]
                if recent >= rate:
                    raise ConnectorError(429, f'Use at most {rate} tool calls per minute.')
                if client_id:
                    connection.execute('UPDATE evlet_mcp_clients SET calls=calls+1, last_used=? WHERE id=?', (now, client_id))
            return self.insert(connection, now, user, origin, client, client_id, run_id, tool, values, 'ok')

    def insert(self, connection, now, user, origin, client, client_id, run_id, tool, values, outcome, error=None, model=None, record_id=None, duration=None):
        access = user.access
        if isinstance(values, dict):
            model = values.get('model') if isinstance(values.get('model'), str) else model
            record_id = values.get('id') if type(values.get('id')) is int else record_id
        inputs = json.dumps(redacted(values), separators=(',', ':'))[:4000] if isinstance(values, dict) else None
        return connection.execute('INSERT INTO evlet_tool_calls(at,user_id,origin,client,client_id,key_id,run_id,tool,model,record_id,kind,outcome,duration_ms,inputs,error) '
                                  'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                  (now, user.id, origin, client, client_id, None if access is None else access.key_id, run_id, tool.name, model, record_id,
                                   'write' if tool.writes else 'read', outcome, duration, inputs, None if error is None else str(error)[:500])).lastrowid

    def log(self, user, origin, client, client_id, run_id, tool, values, outcome, started, error=None, model=None, record_id=None):
        with self.database.transaction() as tx:
            self.insert(tx.connection, int(time.time()), user, origin, client, client_id, run_id, tool, values, outcome, error, model, record_id,
                        int((time.monotonic() - started) * 1000))

    def finish(self, call_id, outcome, started, *, record_id=None, error=None):
        if call_id is None:
            return
        with self.database.transaction() as tx:
            tx.connection.execute('UPDATE evlet_tool_calls SET outcome=?, duration_ms=?, error=?, record_id=COALESCE(record_id, ?) WHERE id=?',
                                  (outcome, int((time.monotonic() - started) * 1000), None if error is None else str(error)[:500], record_id, call_id))

    def run(self, user, origin, tool, values, scope, run_id, client):
        name = tool.name
        if name in self.extra:
            return self.extra[name][1](user, values, run_id)
        request = Dispatch(self.business, scope, user)
        if name == 'discover_models':
            query = {key: value for key, value in values.items() if key != 'search'}
            if 'search' in values:
                query['q'] = values['search']
            return request.get('/api/_discovery?' + urlencode(query))
        if name == 'describe_model':
            return request.get('/api/_discovery/' + values['model'])
        if name == 'job_status':
            return request.get('/api/_jobs/' + str(values['id']))
        if name == 'list_reports':
            return {'reports': [{'name': report['name'], 'label': report['label'], 'columns': report['columns'],
                                 'filters': [{key: entry[key] for key in ('name', 'type', 'label', 'required', 'choices') if key in entry} for entry in report['fields']]}
                                for report in request.get('/api/_reports')['reports']]}
        if name == 'run_report':
            body = {key: values[key] for key in ('filters', 'limit', 'offset') if key in values}
            result = request('POST', '/api/_reports/' + values['report'], body)[0]
            # Where the same report opens in the browser, with the plain filter values in the address.
            plain = {key: value for key, value in (values.get('filters') or {}).items() if isinstance(value, (str, int, float)) and not isinstance(value, bool)}
            return {**result, 'view': '/reports/' + values['report'] + ('?' + urlencode(plain) if plain else '')}
        if name == 'list_services':
            return {'services': [self.describe_service(endpoint) for endpoint in self.services(user)]}
        if name == 'call_service':
            endpoint = next((item for item in self.services(user) if item.name == values['service']), None)
            if endpoint is None:
                raise ConnectorError(404, 'Service not found')
            return self.approved(user, origin, tool, values, run_id, client, {'service': endpoint.name, 'arguments': values.get('arguments', {})},
                                 lambda: self.call_service(request, endpoint, values.get('arguments', {})))
        path = '/api/' + values['model']
        if name == 'list_records':
            query = {key: values[key] for key in ('limit', 'offset', 'order_by') if key in values}
            if 'search' in values:
                query['q'] = values['search']
            if 'filters' in values:
                query['filters'] = json.dumps(values['filters'], allow_nan=False, separators=(',', ':'))
            return request.get(path + '?' + urlencode(query))
        if name == 'aggregate_records':
            query = {key: values[key] for key in ('measures', 'group_by') if key in values}
            if 'search' in values:
                query['q'] = values['search']
            if 'filters' in values:
                query['filters'] = json.dumps(values['filters'], allow_nan=False, separators=(',', ':'))
            return request.get(path + '/aggregate?' + urlencode(query))
        if name == 'create_record':
            return self.approved(user, origin, tool, values, run_id, client, {'model': values['model'], 'values': values['values']},
                                 lambda: request('POST', path, values['values'])[0])
        path += '/' + str(values['id'])
        if name == 'get_record':
            return request.get(path)
        if name == 'record_history':
            query = {key: values[key] for key in ('limit', 'offset') if key in values}
            return request.get(path + '/journal' + ('?' + urlencode(query) if query else ''))
        if name == 'list_files':
            return request.get(path + '/attachments')
        if name == 'read_file':
            files = request.get(path + '/attachments')['items']
            entry = next((item for item in files if item['id'] == values['file_id']), None)
            if entry is None:
                raise ConnectorError(404, 'Attachment not found')
            if entry['size'] > FILE_LIMIT:
                return {'name': entry['name'], 'size': entry['size'], 'text': None, 'note': 'This file is too large to read here.'}
            content = request('GET', f"{path}/attachments/{values['file_id']}")[0]
            return file_text(entry['name'], getattr(content, 'content', b''))
        # Changes to a saved record: read it first, so the change carries its revision and an approval names it.
        record, revision = request.revision(values['model'], values['id'])
        headers = [(b'if-match', revision)] if revision else []
        summary = {'model': values['model'], 'id': values['id'], 'record': brief(record)}
        if name == 'update_record':
            return self.approved(user, origin, tool, values, run_id, client, {**summary, 'values': values['values']},
                                 lambda: request('PATCH', path, values['values'], headers)[0], revision)
        if name == 'delete_record':
            def delete():
                request('DELETE', path, None, headers)
                return {'deleted': True}
            return self.approved(user, origin, tool, values, run_id, client, summary, delete, revision)
        if name == 'change_state':
            body = {'reason': values['reason']} if values['operation'] == 'cancel' and 'reason' in values else {}
            return self.approved(user, origin, tool, values, run_id, client, {**summary, 'operation': values['operation']},
                                 lambda: request('POST', f"{path}/{values['operation']}", body, headers)[0], revision)
        return self.approved(user, origin, tool, values, run_id, client, {**summary, 'action': values['action']},
                             lambda: request('POST', f"{path}/actions/{values['action']}", {}, headers)[0], revision)

    @staticmethod
    def describe_service(endpoint):
        import inspect
        from typing import get_type_hints
        hints = get_type_hints(endpoint.function)
        names = {str: 'string', int: 'integer', bool: 'boolean', float: 'number', RecordId: 'string'}
        parameters = list(inspect.signature(endpoint.function).parameters.values())[1:]
        return {'name': endpoint.name, 'description': inspect.getdoc(endpoint.function) or '', 'method': endpoint.method,
                'arguments': {'type': 'object', 'additionalProperties': False,
                              'properties': {parameter.name: {'type': names.get(hints.get(parameter.name), 'string')} for parameter in parameters},
                              'required': [parameter.name for parameter in parameters if parameter.default is inspect.Parameter.empty]}}

    @staticmethod
    def call_service(request, endpoint, values):
        if endpoint.method == 'GET':
            query = {key: (str(value).lower() if isinstance(value, bool) else value) for key, value in values.items()}
            return request('GET', f'/api/_services/{endpoint.name}?' + urlencode(query))[0]
        return request('POST', f'/api/_services/{endpoint.name}', values)[0]

    # ---------- approvals ----------

    def needs_approval(self, origin, group):
        """Whether a change in this group waits for a click: Eva's settings, Evlet approvals for MCP, never for the API."""
        if group is None or origin == 'api':
            return False
        if group == 'destructive':
            return True
        if origin == 'mcp':
            # Creating and changing records is asked by the client itself; finalising and actions are approved in Evlet.
            return group in ('confirm', 'action')
        return self.business.controls.settings.get('eva.confirm', {}).get(group, 'ask') == 'ask'

    def approved(self, user, origin, tool, values, run_id, client, summary, perform, revision=b''):
        """Run perform() now, or after a person approved exactly this call (same arguments and record revision)."""
        group = group_of(tool, values)
        go = perform
        if not self.needs_approval(origin, group):
            return go()
        # Nobody is asked to approve what the person's role can't do: that is refused now, as the route would.
        self.permitted(user, tool, values)
        if origin == 'schedule':
            raise ConnectorError(403, 'This change needs approval, and nobody is there to approve it during a scheduled run. It was skipped.')
        canonical = json.dumps(values, sort_keys=True, separators=(',', ':'))
        # Bound to the client that asked too: one assistant's approval can't be spent by another of the person's assistants.
        client_id = user.access.assistant if user.access is not None else None
        digest = hashlib.sha256(json.dumps([user.id, origin, client_id, tool.name, canonical, revision.decode('latin1')]).encode()).hexdigest()
        now = int(time.time())
        waiting = None
        with self.database.transaction() as tx:
            row = tx.connection.execute("SELECT * FROM evlet_tool_approvals WHERE digest=? AND user_id=? AND status IN ('pending','approved') AND expires_at>? "
                                        'ORDER BY id DESC LIMIT 1', (digest, user.id, now)).fetchone()
            # An approval is used once: a second identical call needs a new one.
            if row is not None and row['status'] == 'approved' and \
                    tx.connection.execute("UPDATE evlet_tool_approvals SET status='used' WHERE id=? AND status='approved'", (row['id'],)).rowcount == 1:
                waiting = None
            elif row is not None and row['status'] == 'pending':
                waiting = row
            else:
                approval_id = tx.connection.execute(
                    'INSERT INTO evlet_tool_approvals(user_id,origin,client,run_id,tool,arguments,digest,summary,status,created_at,expires_at) '
                    "VALUES (?,?,?,?,?,?,?,?,'pending',?,?)",
                    (user.id, origin, client, run_id, tool.name, canonical, digest, json.dumps({**summary, 'group': group}, default=str), now, now + APPROVAL_SECONDS)).lastrowid
                waiting = tx.connection.execute('SELECT * FROM evlet_tool_approvals WHERE id=?', (approval_id,)).fetchone()
        # Raised after the commit, so the pending approval is kept.
        if waiting is not None:
            raise ApprovalNeeded(self.public_approval(waiting))
        return go()

    def permitted(self, user, tool, values):
        """Refuse (403) a model change this user's role can't make, from the same schema the screens use."""
        if 'model' not in values:
            return
        entry = next((item for item in self.business.schema(user)['models'] if item['name'] == values['model']), None)
        if entry is None:
            raise ConnectorError(404, 'Model not found')
        if tool.name == 'run_action':
            allowed = any(action['name'] == values['action'] and not action.get('dialog') for action in entry.get('actions') or ())
        elif tool.name == 'change_state':
            allowed = bool((entry.get('lifecycle') or {}).get('permissions', {}).get(values['operation']))
        else:
            allowed = bool(entry['permissions'].get({'create_record': 'create', 'update_record': 'update', 'delete_record': 'delete'}.get(tool.name, ''), False))
        if not allowed:
            raise ConnectorError(403, "Your role can't make this change.")

    @staticmethod
    def public_approval(row):
        return {'id': row['id'], 'tool': row['tool'], 'origin': row['origin'], 'client': row['client'], 'run_id': row['run_id'],
                'arguments': json.loads(row['arguments']), 'summary': json.loads(row['summary']), 'status': row['status'],
                'created_at': row['created_at'], 'expires_at': row['expires_at'], 'decided_at': row['decided_at']}

    def approvals(self, user, *, pending_only=False):
        self.prune()
        with self.database._connection() as connection:
            rows = connection.execute('SELECT * FROM evlet_tool_approvals WHERE user_id=?' + (" AND status='pending'" if pending_only else '') +
                                      ' ORDER BY id DESC LIMIT 50', (user.id,)).fetchall()
        return [self.public_approval(row) for row in rows]

    def decide(self, user, approval_id, approve):
        """A person answers one of their own pending approvals. Only signed-in sessions get here (Business checks)."""
        if type(approve) is not bool:
            raise ValidationError({'approve': 'Use true or false'})
        now = int(time.time())
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_tool_approvals WHERE id=? AND user_id=?', (approval_id, user.id)).fetchone()
            if row is None:
                raise AccessError(404, 'Approval not found')
            if row['status'] != 'pending' or row['expires_at'] <= now:
                raise AccessError(409, 'This approval was already answered or has expired.')
            tx.connection.execute('UPDATE evlet_tool_approvals SET status=?, decided_at=? WHERE id=?', ('approved' if approve else 'declined', now, approval_id))
            from .controls import audit
            summary = json.loads(row['summary'])
            target = ' '.join(str(part) for part in (row['tool'], summary.get('model', ''), summary.get('id', '')) if part != '')
            audit(tx.connection, user.username, 'Eva' if row['origin'] in ('eva', 'schedule') else 'MCP', target, 'Approved' if approve else 'Declined')
            row = tx.connection.execute('SELECT * FROM evlet_tool_approvals WHERE id=?', (approval_id,)).fetchone()
        return self.public_approval(row)

    # ---------- reading the log ----------

    def calls(self, query, *, user_id=None, limit=50):
        """Tool calls, newest first: Controls (everyone) or one person's own (user_id)."""
        clauses, args = [], []
        if user_id is not None:
            clauses.append('c.user_id=?'); args.append(user_id)
        for key in ('origin', 'outcome', 'tool'):
            if query.get(key):
                clauses.append(f'c.{key}=?'); args.append(query[key])
        if query.get('q'):
            text = '%' + query['q'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            clauses.append("(u.username LIKE ? ESCAPE '\\' OR c.tool LIKE ? ESCAPE '\\' OR c.model LIKE ? ESCAPE '\\' OR c.client LIKE ? ESCAPE '\\')")
            args.extend([text] * 4)
        if query.get('before'):
            clauses.append('c.id<?'); args.append(query['before'])
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with self.database._connection() as connection:
            rows = connection.execute('SELECT c.*, u.username FROM evlet_tool_calls c LEFT JOIN evlet_users u ON u.id=c.user_id' + where +
                                      ' ORDER BY c.id DESC LIMIT ?', (*args, limit + 1)).fetchall()
        items = [{key: row[key] for key in ('id', 'at', 'username', 'origin', 'client', 'run_id', 'tool', 'model', 'record_id', 'kind', 'outcome', 'duration_ms', 'error')}
                 | {'inputs': json.loads(row['inputs']) if row['inputs'] else None} for row in rows[:limit]]
        return {'items': items, 'next': items[-1]['id'] if len(rows) > limit else None}

    # ---------- /api/_tools ----------

    def api(self, scope, body, user):
        """GET /api/_tools, POST /api/_tools/execute, GET /api/_tools/calls, GET /api/_tools/approvals, POST /api/_tools/approvals/<id>."""
        path, method = scope['path'], scope['method']
        if path == '/api/_tools' and method == 'GET':
            return 200, {'origin': origin_of(user), 'tools': [tool.public() for tool in self.listing(user)]}, []
        if path == '/api/_tools/execute' and method == 'POST':
            if not isinstance(body, dict) or set(body) - {'tool', 'arguments'} or not isinstance(body.get('tool'), str) or not isinstance(body.get('arguments', {}), dict):
                raise ValidationError({'_form': 'Send tool and arguments'})
            try:
                return 200, {'result': self.execute(user, body['tool'], body.get('arguments', {}), scope)}, []
            except ApprovalNeeded as exc:
                approval = exc.approval
                return 202, {'approval': approval, 'error': 'This change needs your approval in Evlet before it runs.',
                             'approve_at': '/settings/ai#approvals'}, []
            except ConnectorError as exc:
                return exc.status, exc.public(), []
        # A person's own approvals and calls: signed-in sessions only, never keys or assistants.
        if user.access is not None:
            raise AccessError(403, 'Sign in to Evlet to answer approvals.')
        if path == '/api/_tools/approvals' and method == 'GET':
            return 200, {'items': self.approvals(user)}, []
        match = re.fullmatch(r'/api/_tools/approvals/([1-9][0-9]{0,18})', path)
        if match and method == 'POST':
            checked_body(body, {'approve'}, 'Send approve: true or false', exact=True, field='approve')
            decided = self.decide(user, int(match[1]), body['approve'])
            eva = getattr(self.business, 'eva', None)
            if eva is not None and decided['run_id'] and decided['origin'] == 'eva':
                eva.answered(user, decided, scope)
            return 200, decided, []
        if path == '/api/_tools/calls' and method == 'GET':
            return 200, self.calls(query_of(scope), user_id=user.id), []
        raise AccessError(404, 'Route not found')


def query_of(scope):
    from urllib.parse import parse_qs
    try:
        raw = scope.get('query_string', b'')
        params = parse_qs(raw.decode('ascii'), max_num_fields=8) if len(raw) <= 1024 else None
        if params is None or set(params) - {'origin', 'outcome', 'tool', 'q', 'before'} or any(len(values) != 1 for values in params.values()):
            raise ValueError()
        query = {key: values[0] for key, values in params.items()}
        if 'before' in query:
            if not re.fullmatch('[1-9][0-9]{0,18}', query['before']):
                raise ValueError()
            query['before'] = int(query['before'])
        if query.get('origin', 'eva') not in ORIGINS or query.get('outcome', 'ok') not in OUTCOMES or len(query.get('q', '')) > 100 or len(query.get('tool', '')) > 64:
            raise ValueError()
        return query
    except (ValueError, UnicodeError):
        raise ValidationError({'query': 'Use origin, outcome, tool, q (up to 100 characters) and before'}) from None


def brief(record):
    """A short view of a record for an approval card: id and up to eight plain values."""
    if not isinstance(record, dict):
        return {}
    kept = {}
    for key, value in record.items():
        if key.startswith('_') or key in ('children', 'id') or isinstance(value, (dict, list)):
            continue
        kept[key] = value
        if len(kept) == 8:
            break
    return kept


def file_text(name, content):
    """The text of a file, or a note that it isn't text. UTF-8 (with or without a BOM) and plain ASCII only."""
    try:
        text = content.decode('utf-8-sig')
    except UnicodeError:
        return {'name': name, 'size': len(content), 'text': None, 'note': "This file isn't plain text, so its content can't be read here."}
    if any(ord(character) < 9 or 13 < ord(character) < 32 for character in text[:2000]):
        return {'name': name, 'size': len(content), 'text': None, 'note': "This file isn't plain text, so its content can't be read here."}
    return {'name': name, 'size': len(content), 'text': text[:TEXT_LIMIT], 'truncated': len(text) > TEXT_LIMIT}
