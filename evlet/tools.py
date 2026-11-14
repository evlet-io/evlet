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

from . import qb
from .adapter import Column, TableDefinition
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
# A rule key: it becomes one path segment of the move route.
RULE_KEY = {'type': 'string', 'pattern': '^[a-z][a-z0-9_]{0,62}$'}
PAGE = {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': SAFE_INTEGER}}
ORIGINS = ('eva', 'schedule', 'mcp', 'api')
# Confirmation groups: which kinds of allowed change can still need an approval click.
GROUPS = ('create', 'update', 'confirm', 'action', 'destructive')
OUTCOMES = ('ok', 'denied', 'failed', 'approval')
KEEP_CALLS, KEEP_APPROVALS, APPROVAL_SECONDS = 90 * 86400, 30 * 86400, 86400
# The call log. Framework step 0002_tool_calls creates it too, before moving the old MCP log in.
CALLS = TableDefinition('evlet_tool_calls', (
    Column('at', 'INTEGER', required=True), Column('user_id', 'INTEGER', required=True), Column('origin', 'TEXT', required=True),
    Column('client', 'TEXT'), Column('client_id', 'INTEGER'), Column('key_id', 'INTEGER'), Column('run_id', 'INTEGER'),
    Column('tool', 'TEXT', required=True), Column('model', 'TEXT'), Column('record_id', 'INTEGER'), Column('kind', 'TEXT', required=True),
    Column('outcome', 'TEXT', required=True), Column('duration_ms', 'INTEGER'), Column('inputs', 'TEXT'), Column('error', 'TEXT')),
    numbered=True, checks=(qb.in_('origin', ORIGINS), qb.in_('kind', ('read', 'write')), qb.in_('outcome', OUTCOMES)))
APPROVALS = TableDefinition('evlet_tool_approvals', (
    Column('user_id', 'INTEGER', required=True, references='evlet_users', on_delete='cascade'), Column('origin', 'TEXT', required=True),
    Column('client', 'TEXT'), Column('run_id', 'INTEGER'), Column('tool', 'TEXT', required=True), Column('arguments', 'TEXT', required=True),
    Column('digest', 'TEXT', required=True), Column('summary', 'TEXT', required=True), Column('status', 'TEXT', required=True),
    Column('created_at', 'INTEGER', required=True), Column('expires_at', 'INTEGER', required=True), Column('decided_at', 'INTEGER')),
    numbered=True, checks=(qb.in_('status', ('pending', 'approved', 'declined', 'used', 'expired')),))


def ensure_calls(connection):
    connection.ensure_table(CALLS)
    connection.ensure_index('evlet_tool_call_at', 'evlet_tool_calls', ('at',))
    connection.ensure_index('evlet_tool_call_user', 'evlet_tool_calls', ('user_id', 'at'))
    connection.ensure_index('evlet_tool_call_client', 'evlet_tool_calls', ('client_id', 'at'))
    connection.ensure_index('evlet_tool_call_run', 'evlet_tool_calls', ('run_id',))


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
    group: str | None = None  # a GROUPS entry; change_state decides per move
    origins: tuple = ORIGINS
    label: str = ''  # what the activity row says while it runs, such as "Reading records"
    # The result carries stored data (records, journals, reports, files, results of app code). Once an Eva
    # conversation has seen such a result, every change it makes asks first (Tools.read_content).
    content: bool = False

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
         ('model',), label='Reading {model}', content=True),
    Tool('get_record', 'Read a permitted record and its accessible child collections.', {'model': MODEL, 'id': RECORD}, ('model', 'id'),
         label='Reading {model} {id}', content=True),
    Tool('aggregate_records', 'Totals, counts, minimums and maximums over permitted records, optionally grouped. '
         'measures: comma separated field:function[:name] such as amount:sum:total,count:count. group_by: comma separated fields, date fields may add :month or :year.',
         {'model': MODEL, 'measures': {'type': 'string', 'maxLength': 500}, 'group_by': {'type': 'string', 'maxLength': 200},
          'filters': {'type': 'object'}, 'search': {'type': 'string', 'maxLength': 200}}, ('model', 'measures'), label='Totalling {model}', content=True),
    Tool('record_history', "Read a permitted record's journal: who changed what and when, notes and calls.",
         {'model': MODEL, 'id': RECORD, **PAGE}, ('model', 'id'), label='Reading the history of {model} {id}', content=True),
    Tool('list_reports', 'List the reports this person can run, with their filters and columns.', {}, label='Looking at reports'),
    Tool('run_report', 'Run a permitted report with its filters. Returns one page of rows.',
         {'report': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_.]{0,127}$'}, 'filters': {'type': 'object'}, **PAGE}, ('report',), label='Running the {report} report', content=True),
    Tool('list_files', 'List the files attached to a permitted record.', {'model': MODEL, 'id': RECORD}, ('model', 'id'), label='Listing files on {model} {id}', content=True),
    Tool('read_file', "Read the text of a file attached to a permitted record. Files that aren't text return their size only.",
         {'model': MODEL, 'id': RECORD, 'file_id': ID}, ('model', 'id', 'file_id'), label='Reading a file', content=True),
    Tool('list_services', 'List the business services this app offers to assistants, with their arguments.', {}, label='Looking at services'),
    Tool('job_status', 'Read the status of a queued action owned by this account, or accessible to its administrator.', {'id': ID}, ('id',), label='Checking a background task', content=True),
    Tool('create_record', 'Create a record using its described create schema. Backend validation and permissions apply. Do not retry an uncertain write automatically.',
         {'model': MODEL, 'values': {'type': 'object'}}, ('model', 'values'), kind='write', group='create', label='Creating {model}'),
    Tool('update_record', 'Update supplied values only. Supplied child collections replace their rows; omit children to preserve them.',
         {'model': MODEL, 'id': RECORD, 'values': {'type': 'object'}}, ('model', 'id', 'values'), kind='write', group='update', label='Updating {model} {id}'),
    Tool('change_state', "Move a document with one of its rules, such as draft to posted, or approve its waiting approval level. "
         "Use a rule's key from the record's _moves (or describe_model state.rules). remark is the note the rule asks for; "
         'values fills the fields a move asks for.',
         {'model': MODEL, 'id': RECORD, 'rule': RULE_KEY, 'remark': {'type': 'string', 'maxLength': 1000}, 'values': {'type': 'object'}},
         ('model', 'id', 'rule'), kind='write', label='Moving {model} {id} with {rule}'),
    Tool('run_action', 'Execute one registered permitted action on a record. It may modify records or enqueue work.',
         {'model': MODEL, 'id': RECORD, 'action': MODEL}, ('model', 'id', 'action'), kind='write', group='action', label='Running {action} on {model} {id}',
         content=True),
    Tool('call_service', 'Call a business service from list_services with its arguments.',
         {'service': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_.]{0,200}$'}, 'arguments': {'type': 'object'}}, ('service',),
         kind='write', group='action', label='Running {service}', content=True),
    Tool('delete_record', 'Delete a permitted record and owned children. This can permanently remove data.',
         {'model': MODEL, 'id': RECORD}, ('model', 'id'), kind='destructive', group='destructive', label='Deleting {model} {id}'),
)
BY_NAME = {tool.name: tool for tool in TOOLS}
NAMES = tuple(BY_NAME)
CONTENT = tuple(tool.name for tool in TOOLS if tool.content)


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


def log_inputs(values):
    """Keep a bounded valid JSON object, never a cut JSON token or arbitrary rejected arguments."""
    if not isinstance(values, dict):
        return None
    kept = redacted(values)
    try:
        encoded = json.dumps(kept, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode()) <= 4000:
            return encoded
    except (ValueError, TypeError):
        pass
    # Preserve useful field names, not an unbounded preview of potentially sensitive input.
    return json.dumps({'truncated': True, 'fields': [str(key)[:80] for key in list(values)[:30]]}, separators=(',', ':'))


def saved_inputs(value):
    """Old releases cut serialized JSON; one damaged historical row must not hide the log."""
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {'truncated': True, 'unavailable': 'Historical input details could not be decoded.'}


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
            ensure_calls(connection)
            connection.ensure_table(APPROVALS)
            connection.ensure_index('evlet_tool_approval_user', 'evlet_tool_approvals', ('user_id', 'status'))
            connection.ensure_index('evlet_tool_approval_digest', 'evlet_tool_approvals', ('digest',))
        self.prune()

    def prune(self):
        """Remove calls after 90 days and decided approvals after 30; expire pending ones. At most once an hour."""
        now = time.time()
        if now - self.pruned < 3600:
            return
        self.pruned = now
        now = int(now)
        with self.database.transaction() as tx:
            tx.connection.run(qb.delete('evlet_tool_calls').where(qb.lt('at', now - KEEP_CALLS)))
            tx.connection.run(qb.update('evlet_tool_approvals', status='expired')
                              .where(qb.in_('status', ('pending', 'approved')), qb.le('expires_at', now)))
            tx.connection.run(qb.delete('evlet_tool_approvals').where(qb.lt('created_at', now - KEEP_APPROVALS)))

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
        # A State with rules, on a model this role may change.
        states = any((entry.get('state') or {}).get('rules') and entry['permissions'].get('update') for entry in schema['models'])
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
            if tool.name == 'run_action' and not actions or tool.name == 'change_state' and not states:
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
        if (tool is None or origin not in tool.origins) and origin != 'mcp':
            raise ConnectorError(404, 'Unknown tool')
        access = user.access
        client = client or self.client_label(user)
        client_id = access.assistant if access is not None and access.assistant else None
        started = time.monotonic()
        call_id = None
        record_id = values.get('id') if isinstance(values, dict) and type(values.get('id')) is int else None
        model = values.get('model') if isinstance(values, dict) and isinstance(values.get('model'), str) else None
        try:
            # Even malformed/disabled/unknown MCP requests spend a slot before rejection logging.
            logged = tool or Tool(str(name)[:200], '', {}, ())
            call_id = self.admit(user, origin, client, client_id, run_id, logged, values)
            if tool is None or origin not in tool.origins:
                raise ConnectorError(404, 'Unknown tool')
            disabled, writes = self.policy(origin)
            if name in disabled:
                raise ConnectorError(403, 'This tool is turned off for this workspace.')
            if tool.writes and not writes:
                raise ConnectorError(403, 'Your admin has turned off changes from AI assistants. They can read and search only.')
            if tool.writes and access is not None and not access.write:
                raise ConnectorError(403, "This assistant can't make changes. You can allow it in Settings, under AI." if origin == 'mcp' else 'This key can only read.')
            values = arguments(tool, values)
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
            # Admission failure never creates an uncounted log row.
            if call_id is not None:
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
                row = connection.run(qb.select('evlet_mcp_clients', 'client_name').where(id=access.assistant)).one()
            return row[0] if row else None
        if access.key_id is not None:
            with self.database._connection() as connection:
                row = connection.run(qb.select('evlet_api_keys', 'name').where(id=access.key_id)).one()
            return row[0] if row else None
        return None

    def admit(self, user, origin, client, client_id, run_id, tool, values):
        """Record the call before it runs. MCP clients get their per-minute rate, counted across processes."""
        now = int(time.time())
        # The log has its own file, so a long write elsewhere never holds up a tool call.
        with self.database.transaction(store='logs') as tx:
            connection = tx.connection
            if origin == 'mcp':
                rate = min(60, self.business.controls.settings['mcp.rate'])
                recent = connection.run(qb.select('evlet_tool_calls', qb.count()).where(
                    qb.eq(qb.coalesce('client_id', 0), client_id or 0), qb.gt('at', now - 60), origin='mcp', user_id=user.id)).value()
                if recent >= rate:
                    raise ConnectorError(429, f'Use at most {rate} tool calls per minute.')
                if client_id:
                    # A busy count: saved with the next batch (evlet/writes.py LogBuffer).
                    self.database.logs.add(None, qb.update('evlet_mcp_clients', calls=qb.col('calls') + 1, last_used=now).where(id=client_id))
            return self.insert(connection, now, user, origin, client, client_id, run_id, tool, values, 'ok')

    def insert(self, connection, now, user, origin, client, client_id, run_id, tool, values, outcome, error=None, model=None, record_id=None, duration=None):
        access = user.access
        if isinstance(values, dict):
            model = values.get('model') if isinstance(values.get('model'), str) else model
            record_id = values.get('id') if type(values.get('id')) is int else record_id
        # Admission must survive hostile metadata before normal argument validation.
        model = model[:128] if isinstance(model, str) else None
        record_id = record_id if type(record_id) is int and 0 < record_id <= 2**63 - 1 else None
        inputs = log_inputs(values)
        return connection.run(qb.insert(
            'evlet_tool_calls', at=now, user_id=user.id, origin=origin, client=client, client_id=client_id,
            key_id=None if access is None else access.key_id, run_id=run_id, tool=tool.name, model=model, record_id=record_id,
            kind='write' if tool.writes else 'read', outcome=outcome, duration_ms=duration, inputs=inputs,
            error=None if error is None else str(error)[:500]).returning_id()).id

    def log(self, user, origin, client, client_id, run_id, tool, values, outcome, started, error=None, model=None, record_id=None):
        """Log a finished call in its own short write to the logs file, so it never waits on a long write to records.
        The rate of MCP calls and the order of the log read these rows, so they are not batched."""
        with self.database.transaction(store='logs') as tx:
            self.insert(tx.connection, int(time.time()), user, origin, client, client_id, run_id, tool, values, outcome, error, model, record_id,
                        int((time.monotonic() - started) * 1000))

    def finish(self, call_id, outcome, started, *, record_id=None, error=None):
        if call_id is None:
            return
        with self.database.transaction(store='logs') as tx:
            tx.connection.run(qb.update('evlet_tool_calls', outcome=outcome, duration_ms=int((time.monotonic() - started) * 1000),
                                        error=None if error is None else str(error)[:500], record_id=qb.coalesce('record_id', record_id))
                              .where(id=call_id))

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
            return self.change_state(user, origin, tool, values, run_id, client, request, path, record, revision, summary)
        return self.approved(user, origin, tool, values, run_id, client, {**summary, 'action': values['action']},
                             lambda: request('POST', f"{path}/actions/{values['action']}", {}, headers)[0], revision)

    def change_state(self, user, origin, tool, values, run_id, client, request, path, record, revision, summary):
        """A move through POST .../moves/{rule}. A move into a final state is in the destructive group, so it
        always asks; other moves are in the confirm group."""
        headers = [(b'if-match', revision)] if revision else []
        entry = self.model_entry(user, values['model'])
        state = entry.get('state') or {}
        # The route decides what this role may do and what a move needs; the schema only sorts the approval group.
        target = next((rule['to'] for rule in state.get('rules', ()) if rule['key'] == values['rule']), None)
        final = any(step['key'] == target and step['final'] for step in state.get('steps', ()))
        body = {**values.get('values', {}), **({'remark': values['remark']} if values.get('remark') else {})}
        # The confirmation card shows operation as the action and values as the new values.
        shown = {**summary, 'operation': values['rule'], **({'values': body} if body else {})}
        return self.approved(user, origin, tool, values, run_id, client, shown,
                             lambda: request('POST', f"{path}/moves/{values['rule']}", body, headers)[0], revision,
                             group='destructive' if final else 'confirm', record=record)

    def model_entry(self, user, model):
        """The model's schema entry for this user, as the screens get it. 404 when the user can't see the model."""
        entry = next((item for item in self.business.schema(user)['models'] if item['name'] == model), None)
        if entry is None:
            raise ConnectorError(404, 'Model not found')
        return entry

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

    def needs_approval(self, origin, group, read_content=False):
        """Whether a change in this group waits for a click: Eva's settings, Evlet approvals for MCP, never for the API.

        read_content: this Eva conversation has seen stored data, which could carry text written to steer Eva,
        so every change asks whatever the confirm settings say."""
        if group is None or origin == 'api':
            return False
        if group == 'destructive':
            return True
        if origin in ('eva', 'schedule') and read_content:
            return True
        if origin == 'mcp':
            # Creating and changing records is asked by the client itself; finalising and actions are approved in Evlet.
            return group in ('confirm', 'action')
        return self.business.controls.settings.get('eva.confirm', {}).get(group, 'ask') == 'ask'

    def approved(self, user, origin, tool, values, run_id, client, summary, perform, revision=b'', *, group=None, record=None):
        """Run perform() now, or after a person approved exactly this call (same arguments and record revision)."""
        group = group or tool.group
        go = perform
        read_content = origin in ('eva', 'schedule') and self.read_content(run_id)
        if not self.needs_approval(origin, group, read_content):
            return go()
        # Nobody is asked to approve what the person's role can't do: that is refused now, as the route would.
        self.permitted(user, tool, values, record)
        if read_content:
            summary = {**summary, 'after_reading': True}
        if origin == 'schedule':
            raise ConnectorError(403, 'This change needs approval, and nobody is there to approve it during a scheduled run. It was skipped.')
        canonical = json.dumps(values, sort_keys=True, separators=(',', ':'))
        # Bound to the client that asked too: one assistant's approval can't be spent by another of the person's assistants.
        client_id = user.access.assistant if user.access is not None else None
        digest = hashlib.sha256(json.dumps([user.id, origin, client_id, tool.name, canonical, revision.decode('latin1')]).encode()).hexdigest()
        now = int(time.time())
        waiting = None
        with self.database.transaction() as tx:
            # Eva approvals belong to this run; cancelling it must not leave a reusable write grant
            # for a later run with identical arguments. Remote-client approvals retain their contract.
            run_filter = qb.eq('run_id', run_id) if origin == 'eva' else qb.TRUE
            row = tx.connection.run(qb.select('evlet_tool_approvals').where(qb.in_('status', ('pending', 'approved')), qb.gt('expires_at', now),
                                                                            run_filter, digest=digest, user_id=user.id).order_by('-id').limit(1)).one()
            # An approval is used once: a second identical call needs a new one.
            if row is not None and row['status'] == 'approved' and \
                    tx.connection.run(qb.update('evlet_tool_approvals', status='used').where(id=row['id'], status='approved')).rowcount == 1:
                waiting = None
            elif row is not None and row['status'] == 'pending':
                waiting = row
            else:
                approval_id = tx.connection.run(qb.insert(
                    'evlet_tool_approvals', user_id=user.id, origin=origin, client=client, run_id=run_id, tool=tool.name, arguments=canonical,
                    digest=digest, summary=json.dumps({**summary, 'group': group}, default=str), status='pending', created_at=now,
                    expires_at=now + APPROVAL_SECONDS).returning_id()).id
                waiting = tx.connection.run(qb.select('evlet_tool_approvals').where(id=approval_id)).one()
        # Raised after the commit, so the pending approval is kept.
        if waiting is not None:
            raise ApprovalNeeded(self.public_approval(waiting))
        return go()

    def read_content(self, run_id):
        """Whether an Eva conversation has seen stored data: a content tool call that worked in this run or an
        earlier run of the same conversation. Earlier results stay in what the model reads, so they count too.
        A finished call has its duration, so the call asking now never counts itself."""
        if run_id is None:
            return False
        conversation = qb.select(qb.table('evlet_eva_runs', 'current'), 'current.conversation_id').where(qb.eq('current.id', run_id))
        runs = qb.select('evlet_eva_runs', 'id').where(qb.eq('conversation_id', conversation))
        with self.database._connection() as connection:
            if connection.columns('evlet_eva_events'):
                events = qb.select('evlet_eva_events', 'data').where(qb.in_('run_id', runs))
                if connection.run(events.where(type='content.read').limit(1)).one() is not None:
                    return True
                # Upgrade compatibility: completed content events live with the conversation, unlike logs.
                # Only text deltas are discarded on completion; historical tool activity remains stored.
                for event in connection.run(events.where(type='tool.completed')):
                    try:
                        if json.loads(event['data']).get('tool') in CONTENT:
                            return True
                    except (ValueError, TypeError, AttributeError):
                        continue
            row = connection.run(qb.select('evlet_tool_calls', qb.value(1)).where(
                qb.eq('outcome', 'ok'), qb.not_null('duration_ms'), qb.in_('tool', tuple(CONTENT)), qb.in_('run_id', runs)).limit(1)).one()
        return row is not None

    def permitted(self, user, tool, values, record=None):
        """Refuse (403) a model change this user's role can't make, from the same schema the screens use."""
        if 'model' not in values:
            return
        entry = self.model_entry(user, values['model'])
        if tool.name == 'run_action':
            allowed = any(action['name'] == values['action'] and not action.get('dialog') for action in entry.get('actions') or ())
        elif tool.name == 'change_state':
            # The record's _moves say which moves this role has now, and why one is blocked.
            move = next((item for item in (record or {}).get('_moves') or () if item['key'] == values['rule']), None)
            if move is None:
                raise ConnectorError(409, 'This record has no such move now')
            if not move['allowed'] and move.get('reason') != 'Your role cannot use this move':
                raise ConnectorError(409, move.get('reason') or 'This move is not possible now')
            allowed = move['allowed']
        else:
            allowed = bool(entry['permissions'].get({'create_record': 'create', 'update_record': 'update', 'delete_record': 'delete'}.get(tool.name, ''), False))
        if allowed and tool.name in ('create_record', 'update_record'):
            # A field the role can't write is refused as the route refuses it (Policy.check_values).
            model = self.business.registry.resolve(values['model'])
            if set(values['values']) & set(model.__declared_fields__) - self.business.policy.fields(user, model, write=True):
                raise ConnectorError(403, 'One or more fields cannot be written')
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
            pending = qb.eq('status', 'pending') if pending_only else qb.TRUE
            cancelled = set()
            eva = getattr(self.business, 'eva', None) is not None
            if eva and pending_only:
                stopped = qb.select('evlet_eva_runs', 'id').where(user_id=user.id, status='cancelled')
                pending = qb.and_(pending, qb.or_(qb.is_null('run_id'), qb.not_in('run_id', stopped)))
            rows = connection.run(qb.select('evlet_tool_approvals').where(pending, user_id=user.id).order_by('-id').limit(50)).all()
            runs = tuple({row['run_id'] for row in rows if row['run_id'] is not None})
            if eva and runs:
                cancelled = {row[0] for row in connection.run(qb.select('evlet_eva_runs', 'id').where(qb.in_('id', runs), status='cancelled'))}
        return [self.public_approval({**dict(row), 'status': 'declined'} if row['run_id'] in cancelled and row['status'] in ('pending', 'approved') else row)
                for row in rows]

    def decide(self, user, approval_id, approve):
        """A person answers one of their own pending approvals. Only signed-in sessions get here (Business checks)."""
        if type(approve) is not bool:
            raise ValidationError({'approve': 'Use true or false'})
        now = int(time.time())
        with self.database.transaction() as tx:
            row = tx.connection.run(qb.select('evlet_tool_approvals').where(id=approval_id, user_id=user.id)).one()
            if row is None:
                raise AccessError(404, 'Approval not found')
            if row['status'] != 'pending' or row['expires_at'] <= now:
                raise AccessError(409, 'This approval was already answered or has expired.')
            eva = getattr(self.business, 'eva', None)
            if eva is not None and row['run_id'] and row['origin'] == 'eva':
                eva.reserve_answer(tx.connection, user, row)
            tx.connection.run(qb.update('evlet_tool_approvals', status='approved' if approve else 'declined', decided_at=now).where(id=approval_id))
            from .controls import audit
            summary = json.loads(row['summary'])
            target = ' '.join(str(part) for part in (row['tool'], summary.get('model', ''), summary.get('id', '')) if part != '')
            audit(tx.connection, user.username, 'Eva' if row['origin'] in ('eva', 'schedule') else 'MCP', target, 'Approved' if approve else 'Declined')
            row = tx.connection.run(qb.select('evlet_tool_approvals').where(id=approval_id)).one()
        return self.public_approval(row)

    # ---------- reading the log ----------

    def calls(self, query, *, user_id=None, limit=50):
        """Tool calls, newest first: Controls (everyone) or one person's own (user_id)."""
        conditions = []
        if user_id is not None:
            conditions.append(qb.eq('c.user_id', user_id))
        for key in ('origin', 'outcome', 'tool'):
            if query.get(key):
                conditions.append(qb.eq('c.' + key, query[key]))
        if query.get('q'):
            conditions.append(qb.or_(*(qb.contains_ci(name, query['q']) for name in ('u.username', 'c.tool', 'c.model', 'c.client'))))
        if query.get('before'):
            conditions.append(qb.lt('c.id', query['before']))
        with self.database._connection() as connection:
            rows = connection.run(qb.select(qb.table('evlet_tool_calls', 'c'), qb.all_columns('c'), 'u.username')
                                  .join(qb.table('evlet_users', 'u'), qb.eq('u.id', qb.col('c.user_id')), left=True)
                                  .where(*conditions).order_by('-c.id').limit(limit + 1)).all()
        items = [{key: row[key] for key in ('id', 'at', 'username', 'origin', 'client', 'run_id', 'tool', 'model', 'record_id', 'kind', 'outcome', 'duration_ms', 'error')}
                 | {'inputs': saved_inputs(row['inputs'])} for row in rows[:limit]]
        return {'items': items, 'next': items[-1]['id'] if len(rows) > limit else None}

    # ---------- /api/_tools ----------

    def api(self, scope, body, user):
        """GET /api/_tools, POST /api/_tools/execute, GET /api/_tools/calls, GET /api/_tools/approvals, POST /api/_tools/approvals/<id>."""
        path, method = scope['path'], scope['method']
        if path == '/api/_tools' and method == 'GET':
            return 200, {'origin': origin_of(user), 'tools': [tool.public() for tool in self.listing(user)]}, []
        if path == '/api/_tools/execute' and method == 'POST':
            if not isinstance(body, dict) or set(body) - {'tool', 'arguments'} or not isinstance(body.get('tool'), str):
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
