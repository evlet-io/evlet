"""Explicit service endpoint metadata and calendar schedule declarations."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import inspect
import json
import re
import time
from urllib.parse import parse_qs

from .auth import AccessError, User
from .documents import Context, typed_arguments
from .identity import RecordId
from .models import ValidationError


def api(function=None, *, method='POST', permission=None, name=None, public=False, tool=False):
    """public=True lets anyone call the endpoint without signing in. It runs as the 'public' role,
    so its permission needs a Rule for ('public', Model), and each address gets 30 calls a minute, 300 an hour.
    tool=True offers it to Eva and MCP clients through list_services and call_service, as the person, under the
    same permission; its docstring is what they read. Public endpoints can't be tools."""
    if method not in ('GET', 'POST') or permission is None or type(public) is not bool or type(tool) is not bool or (public and tool):
        raise ValueError('APIs require GET/POST and an explicit resource permission; public endpoints cannot be tools')
    def decorate(callable):
        callable.__api__ = {'method': method, 'permission': permission, 'name': name, 'public': public, 'tool': tool}
        return callable
    return decorate(function) if function is not None else decorate


def cron(expression, *, name=None, role='editor', timezone=None):
    Calendar(expression, timezone or 'UTC')
    if role not in ('admin', 'editor', 'viewer'): raise ValueError('Cron requires an explicit supported role')
    def decorate(callable):
        if list(inspect.signature(callable).parameters) != ['context']:
            raise ValueError('Cron functions accept only context')
        callable.__cron__ = {'expression': expression, 'name': name, 'role': role, 'timezone': timezone}
        return callable
    return decorate


@dataclass(frozen=True)
class Endpoint:
    name: str
    function: object
    method: str
    permission: tuple
    public: bool = False
    tool: bool = False


# Calls per window for each client address on public endpoints: (seconds, limit).
PUBLIC_LIMITS = ((60, 30), (3600, 300))


def public_limit(business, scope, endpoint):
    """Count a public call per endpoint and client address; refuse with 429 and Retry-After past either window."""
    from .app import HTTPError
    from .auth import client_network
    now = int(time.time())
    address = client_network(business.auth.client_address(scope))
    client = hashlib.sha256(f'public:{endpoint.name}:{address}'.encode()).hexdigest()
    with business.database.transaction() as tx:
        tx.connection.execute('DELETE FROM evlet_public_limits WHERE resets<=?', (now,))
        for window, limit in PUBLIC_LIMITS:
            row = tx.connection.execute('SELECT calls, resets FROM evlet_public_limits WHERE client=? AND period=?', (client, window)).fetchone()
            if row and row['calls'] >= limit:
                raise HTTPError(429, 'Too many requests. Try again later.', [(b'retry-after', str(max(1, row['resets'] - now)).encode())])
        for window, _ in PUBLIC_LIMITS:
            tx.connection.execute('INSERT INTO evlet_public_limits VALUES (?,?,1,?) ON CONFLICT(client, period) DO UPDATE SET calls=evlet_public_limits.calls+1',
                                  (client, window, now + window))


@dataclass(frozen=True)
class CalendarSchedule:
    name: str
    task: str
    expression: str
    timezone: str | None = None
    payload: object = None


class Calendar:
    """Five numeric fields; DOM/DOW use OR when both are constrained."""
    def __init__(self, expression, zone='UTC'):
        self.zone = ZoneInfo(zone)
        if not isinstance(expression, str) or len(expression) > 128 or len(expression.split()) != 5:
            raise ValueError('Use a five-field cron expression')
        self.parts = expression.split()
        self.values = []
        for text, low, high in zip(self.parts, (0, 0, 1, 1, 0), (59, 23, 31, 12, 6)):
            values = set()
            for piece in text.split(','):
                match = re.fullmatch(r'(\*|[0-9]+(?:-[0-9]+)?)(?:/([0-9]+))?', piece)
                if not match: raise ValueError('Unsupported cron syntax')
                step = int(match[2] or 1)
                if not 1 <= step <= high + 1: raise ValueError('Invalid cron step')
                bounds = (low, high) if match[1] == '*' else tuple(map(int, match[1].split('-')))
                start, end = bounds if len(bounds) == 2 else (bounds[0], bounds[0])
                if not low <= start <= end <= high: raise ValueError('Cron field is outside its range')
                values.update(range(start, end + 1, step))
            self.values.append(values)

    def next(self, after):
        local = datetime.fromtimestamp(after, timezone.utc).astimezone(self.zone)
        minutes, hours, days, months, weekdays = self.values
        for delta in range(366 * 9):
            date = local.date() + timedelta(days=delta)
            if date.month not in months: continue
            dom, dow = date.day in days, (date.weekday() + 1) % 7 in weekdays
            matches = (dom or dow) if self.parts[2] != '*' and self.parts[4] != '*' else dom and dow
            if not matches: continue
            for hour in sorted(hours):
                for minute in sorted(minutes):
                    wall = datetime(date.year, date.month, date.day, hour, minute, tzinfo=self.zone, fold=0)
                    utc = wall.astimezone(timezone.utc)
                    # Nonexistent local times fail a round trip; ambiguous times use fold=0 only.
                    if utc.astimezone(self.zone).replace(tzinfo=None) != wall.replace(tzinfo=None): continue
                    value = int(utc.timestamp())
                    if value > after: return value
        raise ValueError('Cron has no occurrence within nine years')


def collect_service(package, module):
    endpoints, tasks, schedules = [], [], []
    seen = set()
    from .jobs import Task
    import ast
    from pathlib import Path
    tree = ast.parse(Path(module.__file__).read_text())
    top = {id(node) for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = target.id if isinstance(target, ast.Name) else target.attr if isinstance(target, ast.Attribute) else ''
                if name in ('api', 'cron') and (id(node) not in top or isinstance(node, ast.AsyncFunctionDef)):
                    raise ValueError(f'{module.__file__}:{node.lineno}: services require synchronous module-level functions')
    for name, function in sorted(vars(module).items()):
        if not inspect.isfunction(function) or function.__module__ != module.__name__: continue
        if id(function) in seen: continue
        seen.add(id(function))
        if '.' in function.__qualname__ and (hasattr(function, '__api__') or hasattr(function, '__cron__')):
            raise ValueError('Decorated services must be module-level functions')
        identity = module.__name__ + '.' + function.__name__
        declaration = getattr(function, '__api__', None)
        if declaration:
            from typing import get_type_hints
            parameters = list(inspect.signature(function).parameters.values())
            annotations = get_type_hints(function)
            if not parameters or parameters[0].name != 'context' or any(parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY) or annotations.get(parameter.name) not in (str, int, bool, float, RecordId) for parameter in parameters[1:]):
                raise ValueError('API functions require context followed by explicitly typed scalar inputs')
            if not isinstance(declaration['permission'], tuple) or len(declaration['permission']) != 2:
                raise ValueError('API permission must be a resource/operation pair')
            if declaration['method'] == 'GET' and declaration['permission'][1] != 'read':
                raise ValueError('GET endpoints require a read capability')
            alias = declaration['name'] or identity.removeprefix(package.name + '.')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]*', alias): raise ValueError('Invalid endpoint name')
            endpoints.append(Endpoint(package.name + '.' + alias, function, declaration['method'], declaration['permission'], declaration.get('public', False),
                                      declaration.get('tool', False)))
        declaration = getattr(function, '__cron__', None)
        if declaration:
            identity = package.name + '.' + declaration['name'] if declaration['name'] else identity
            task_name = 'cron_' + hashlib.sha256(identity.encode()).hexdigest()[:48]
            def handler(tx, job_context, payload, function=function, role=declaration['role']):
                business = getattr(tx, '_business', None)
                if business is None: raise ValueError('Cron requires an instance-bound business context')
                context = Context(business, tx, User(0, 'schedule', role))
                context.idempotency_key = job_context.idempotency_key
                return function(context)
            tasks.append(Task(task_name, handler))
            schedules.append(CalendarSchedule(task_name, task_name, declaration['expression'], declaration['timezone']))
    return endpoints, tasks, schedules


def endpoint_for(business, path):
    return next((item for item in business.apis if item.name == path.removeprefix('/api/_services/')), None)


def dispatch(business, scope, body, user):
    """Run an endpoint for the signed-in user. Public endpoints always run as the public role."""
    endpoint = endpoint_for(business, scope['path'])
    if endpoint is None: raise AccessError(404, 'Endpoint is unavailable')
    if scope['method'] != endpoint.method: raise AccessError(405, 'Method not allowed')
    if endpoint.public:
        public_limit(business, scope, endpoint)
        from .auth import PUBLIC
        user = User(0, 'public', PUBLIC)
    permission_model, operation = endpoint.permission
    model = business.registry.resolve(permission_model)
    business.policy.require(user, model, operation)
    values = body or {}
    if endpoint.method == 'GET':
        try:
            query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True, max_num_fields=32)
            if any(len(value) != 1 for value in query.values()): raise ValueError('Repeated arguments')
            values = {key: value[0] for key, value in query.items()}
            from typing import get_type_hints
            for key, kind in get_type_hints(endpoint.function).items():
                if key in values and kind in (int, float, RecordId): values[key] = kind(values[key])
                elif key in values and kind is bool:
                    if values[key] not in ('true', 'false'): raise ValueError('Invalid boolean')
                    values[key] = values[key] == 'true'
        except (ValueError, UnicodeError): raise ValidationError({'arguments': 'Invalid query arguments'}) from None
    arguments = typed_arguments(endpoint.function, values, skip=1)
    with business.context(user, readonly=endpoint.method == 'GET') as context:
        result = endpoint.function(context, **arguments)
        encoded = json.dumps(result, allow_nan=False)
        if len(encoded.encode()) > 1024 * 1024: raise ValueError('Endpoint result exceeds 1 MiB')
    return 200, result, []
