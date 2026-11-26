"""Portable ASGI HTTP and lifespan support without a web framework dependency."""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from .adapter import OperationalError
from .database import DEADLINE, ConflictError, Deadline, NotFoundError
from .identity import jsonable, path_id
from .models import ValidationError
from .permissions import CALLER, CORRECTIONS, read_only
from .auth import AccessError
from .business import Business, Download
from .writes import WRITE_LABEL
from .runtime import Busy, Work, runtime
from .storage import StorageError

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 64 * 1024
# Unsaved changes may be up to 1 MB (the unsaved.entry_kb maximum); the route checks the instance limit.
UNSAVED_BODY = 1024 * 1024 + 4096
# Framework routes under /_/ that clients on the internet must reach: the remote MCP endpoint, its
# OAuth 2.1 sign-in, and the return from a personal mailbox's OAuth sign-in. The Caddy and nginx files
# that evlet proxy and evlet setup write pass exactly these and refuse every other /_/ path.
MCP_PATHS = ('/_/mcp', '/_/oauth/authorize', '/_/oauth/token', '/_/oauth/register', '/_/oauth/revoke')
MAIL_PATHS = ('/_/mail/callback', '/_/mail/continue.js')
OIDC_PATHS = ('/_/oidc/start', '/_/oidc/callback', '/_/oidc/finish')
PUBLIC_PATHS = MCP_PATHS + MAIL_PATHS + OIDC_PATHS
# The Caddy on-demand TLS check answers loopback callers only; the proxy refuses it from outside.
TLS_CHECK = '/_/tls-allowed'
# The project endpoint's management channel (evlet/manage.py): loopback with the instance's token, 404 otherwise.
MANAGE = '/_/manage/'
MANAGE_BODY_BYTES = 16 * 1024 * 1024  # site files arrive base64 encoded: a 10 MiB file and its envelope


def request_body_limit(path, image_bytes=None):
    """JSON envelope ceilings shared with reverse-proxy generation; image_bytes includes current policy."""
    if path == MANAGE + 'sites/files':
        return MANAGE_BODY_BYTES
    if image_bytes is None:
        return MAX_BODY_BYTES
    if path.endswith(('/attachments', '/_images')):
        return max(1500000, image_bytes * 4 // 3 + 4096)
    if path == '/api/_eva/files':
        return 1500000
    if path == '/api/_settings/me/photo':
        return 2900000
    if path.startswith('/api/_controls/prints'):
        return 300000
    if path.startswith('/api/_unsaved/'):
        return UNSAVED_BODY
    return MAX_BODY_BYTES


# A client that went away has its report's database work interrupted this many seconds later.
DISCONNECT_GRACE = 5.0
# Open event streams read their pages at least this often, so a signed-out session ends its stream.
STREAM_RECHECK = 5.0


def deadline_seconds(path, method):
    """The time limit of a request's database work, or None. Report runs, exports and prints
    (/api/_reports/...) only read; EVLET_REPORT_SECONDS bounds them, default 300 (0 turns it off).
    EVLET_REQUEST_SECONDS bounds every other request and is off unless set (docs/performance.md)."""
    report = method == 'POST' and path.startswith('/api/_reports/')
    name = 'EVLET_REPORT_SECONDS' if report else 'EVLET_REQUEST_SECONDS'
    raw = os.environ.get(name, '300' if report else '0').strip()
    try:
        seconds = float(raw)
        if not 0 <= seconds <= 86400:
            raise ValueError()
    except ValueError:
        raise ValueError(f'{name} must be a number of seconds from 0 to 86400') from None
    return seconds or None


def several_workers():
    """Whether this server was started with more than one worker process (uvicorn --workers or WEB_CONCURRENCY).
    Sign-in limits live in the database, but API key rate limits, the write turn and the tenant cache are kept
    per process, so each extra worker multiplies them (docs/performance.md)."""
    try:
        concurrency = int(os.environ.get('WEB_CONCURRENCY', '1') or 1)
    except ValueError:
        concurrency = 1
    argv = list(sys.argv)
    for index, item in enumerate(argv):
        value = item.partition('=')[2] if item.startswith('--workers=') else argv[index + 1] if item in ('--workers', '-w') and index + 1 < len(argv) else None
        if value is not None and value.isdigit():
            concurrency = max(concurrency, int(value))
    return concurrency > 1


class _Encoded:
    """A JSON response body already encoded in the worker thread, off the event loop."""
    __slots__ = ('content',)

    def __init__(self, content):
        self.content = content


def image_limit(business):
    from .images import limits
    return limits(business)[0]


class HTTPError(Exception):
    def __init__(self, status, message, headers=()):
        self.status = status
        self.message = message
        self.headers = headers


class Application:
    # build_instance sets this: an instance's framework steps run only inside evlet migrate.
    managed_framework = False

    def __init__(self, database, *, auth=None, policy=None, children=(), attachments=(), storage=None, jobs=None, events=None, name="Evlet", hosts=None, reports=(), apis=(), playground=False, routes=None, connectors=(), secret_key=None, ai=None, prints=(), workspaces=()):
        from .routing import RouteTable, resolve_routes
        from .naming import check_declarations
        check_declarations(database.registry, {child.model for child in database.registry.children})
        if routes is not None and not isinstance(routes, RouteTable):
            raise ValueError('Pass a RouteTable from resolve_routes')
        self.routes = resolve_routes() if routes is None else routes
        if isinstance(hosts, (str, bytes)):
            raise ValueError("Pass a collection of explicit Host header values")
        self.hosts = None if hosts is None else frozenset(hosts)
        if hosts is not None and (not self.hosts or any(not isinstance(host, str) or not host for host in self.hosts)):
            raise ValueError("Configure explicit Host header values")
        self._stopping = asyncio.Event()
        self._work = Work()
        # Deadlines of running requests, interrupted when the server stops; the shared event poller's state.
        self._deadlines = set()
        self._event_pump = None
        self._event_changed = None
        self._event_streams = 0
        self.name = name
        self.database = database
        self.registry = database.registry
        if auth is None and (policy is not None or children or attachments or storage is not None or jobs is not None or events is not None):
            raise ValueError("Business features require authentication")
        self.live_routes = None
        # evlet.manage.Manage of an instance the project endpoint runs; None answers 404 on /_/manage/.
        self.manage = None
        self.business = None if auth is None else Business(database, auth, policy=policy, children=children, attachments=attachments, storage=storage, jobs=jobs, events=events)
        if self.business:
            from .routing import authority
            if auth.realms != {authority(origin).encode(): realm for origin, realm in self.routes.realms().items()}:
                raise ValueError('LocalAuth realms must match the route table: pass realms=routes.realms()')
            self.business.routes = self.routes
            self.business.application = {"name": name, "playground": bool(playground), "docs": self.routes.docs_path if self.routes.docs else None}
            from .forms import Forms
            self.business.forms = Forms(self.business, reports)
            self.business.apis = tuple(apis)
            # Named sidebars, checked against the models, reports and endpoints above (evlet.workspaces).
            from .workspaces import Workspaces
            if isinstance(workspaces, (str, bytes)) or not isinstance(workspaces, (list, tuple)):
                raise ValueError('Pass workspaces as a list or tuple of Workspace definitions')
            self.business.workspaces = Workspaces(self.business, workspaces, name='Playground' if playground else name or 'Evlet')
            from .printing import Prints
            self.business.prints = Prints(self.business, prints)
            self.business.controls.routes = self.routes
            # Sub-hosts from evlet_hosts join the table without a restart (evlet.hosts).
            from .hosts import LiveRoutes
            self.live_routes = self.business.live_routes = LiveRoutes(self)
            for connector in connectors:
                self.business.connectors.register('app', connector)
            # Secrets are sealed with a key outside the database: EVLET_SECRET_KEY or secret_key, shared with
            # two-step sign-in. Without either, connector secrets use a key file next to a SQLite file.
            if secret_key is not None:
                self.business.secret_key = secret_key
            path = getattr(database, 'path', None)
            self.business.connectors.fallback = path.parent / 'secret.key' if path is not None else None
            self.business.oidc = None
            if getattr(auth, 'oidc', None) is not None:
                from .oidc import OIDCFlow
                self.business.oidc = OIDCFlow(self.business)
            # Eva only when config.py turns AI on: AI(enabled=True, models={...}). Otherwise nothing of Eva exists.
            if ai is not None:
                from .eva import AI, Eva
                if not isinstance(ai, AI):
                    raise ValueError('Pass ai=AI(...)')
                if ai.enabled:
                    self.business.eva = Eva(self.business, ai)
        elif reports or apis or connectors or prints or workspaces or ai is not None:
            raise ValueError('Reports, print designs, workspaces, APIs, connectors and AI require authentication')

    @staticmethod
    def _redact_oidc_query(scope):
        # Uvicorn logs its original scope after the response. Keep callback credentials
        # out of that query string, including when a tenant router copies the scope.
        # The mailbox sign-in return carries an OAuth code and state just like the OIDC callback.
        key = {'/_/oidc/callback': 'evlet.oidc_query', '/_/mail/callback': 'evlet.mail_query'}.get(scope.get('path'))
        if scope.get('type') == 'http' and key:
            scope.setdefault(key, scope.get('query_string', b''))
            scope['query_string'] = b''

    async def __call__(self, scope, receive, send):
        self._redact_oidc_query(scope)
        # A request is a caller: Meta.api limits what it reaches (evlet/permissions.py); app code it runs is trusted.
        token = CALLER.set(scope.get("type") == "http")
        # Slow-write warnings name the request (evlet/writes.py); the value follows the request into its threads.
        request_id = uuid.uuid4().hex if scope.get('type') == 'http' else None
        route = self._route_label(scope.get('path', ''))
        method = scope.get('method', '')
        method = method if method in ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS') else 'OTHER'
        label = WRITE_LABEL.set(f'{method} {route} request_id={request_id}' if request_id else '')
        started, status = time.monotonic(), None
        original_send = send
        async def correlated_send(event):
            nonlocal status
            if event['type'] == 'http.response.start':
                status = event['status']
                event = {**event, 'headers': [*event.get('headers', []), (b'x-request-id', request_id.encode())]}
            await original_send(event)
        if request_id:
            scope = {**scope, 'evlet.request_id': request_id}
            send = correlated_send
        try:
            await self._serve(scope, receive, send)
        finally:
            if request_id and runtime().log_requests:
                logging.getLogger('evlet.http').info('request complete', extra={'request_id': request_id,
                    'http_method': method, 'http_route': route, 'http_status': status,
                    'duration_ms': round((time.monotonic() - started) * 1000, 3),
                    'http_active': {name: lane.active() for name, lane in runtime().lanes.items()}})
            WRITE_LABEL.reset(label)
            CALLER.reset(token)

    async def _serve(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            raise ValueError("Unsupported ASGI scope")
        admission = deadline = None
        try:
            headers = []
            path = scope['path']
            # HEAD is GET without the body, for pages and files; the API keeps its own methods.
            head = scope['method'] == 'HEAD' and path != '/api' and not path.startswith(('/api/', '/_', '/.well-known/'))
            if head:
                scope = {**scope, 'method': 'GET'}
            host = dict(scope.get('headers', [])).get(b'host', b'')
            # A public host (its root frontend has auth=None) serves its site's files and the public endpoints its
            # scopes list. Everything else, Evlet's own paths and errors included, gets the site's not-found page.
            public = self.routes.public(host)
            service = public and self._public_service(host, path)
            try:
                if self.business is None:
                    self._guard_local(scope, navigation=public and scope['method'] == 'GET')
                if public and not service and (scope['method'] != 'GET' or path == '/api' or path.startswith(('/api/', '/_', '/.well-known'))):
                    raise HTTPError(404, 'Route not found')
                self._api_scope(scope)
                if path == '/health':
                    self._method(scope['method'], ['GET'])
                stream = bool(self.business) and (path == '/api/_events/stream' or self.business.eva is not None
                         and path.startswith('/api/_eva/runs/') and path.endswith('/stream'))
                lane = ('stream' if stream else 'probe' if path == '/health' else 'heavy' if '/prints' in path
                        else 'read' if scope['method'] in ('GET', 'HEAD', 'OPTIONS') else 'write')
                # The request's generation: a restart while its body arrives refuses it. The lane slot
                # is taken only once the body is in, so a slow client holds no scarce slot.
                ticket = self._work.ticket()
                if stream:
                    admission = await self._work.acquire(lane, ticket)
                if scope['path'] == '/api/_events/stream' and self.business:
                    await self._event_stream(scope, receive, send)
                    return
                if self.business and self.business.eva is not None and scope['path'].startswith('/api/_eva/runs/') and scope['path'].endswith('/stream'):
                    await self._eva_stream(scope, receive, send)
                    return
                body = None
                internal = bool(self.business) and scope['path'].startswith(('/_/', '/.well-known/')) and scope['path'] != TLS_CHECK \
                    and not scope['path'].startswith(MANAGE)
                if internal:
                    # OAuth and the remote MCP endpoint read their own bodies (JSON or form), at most 64 KiB like /api.
                    data = bytearray()
                    while scope['method'] == 'POST':
                        event = await self._body_event(receive)
                        if event['type'] == 'http.disconnect':
                            return
                        chunk = event.get('body', b'')
                        if len(data) + len(chunk) > MAX_BODY_BYTES:
                            raise HTTPError(413, 'Request body exceeds the allowed size')
                        data.extend(chunk)
                        if not event.get('more_body', False):
                            break
                    admission = await self._work.acquire(lane, ticket)
                    status, payload, headers = await self._work.run(lane, self._encoded, self._internal, scope, bytes(data), lease=admission)
                # PUT carries a body only on the Controls routes (connectors) and unsaved changes; elsewhere it stays a 405.
                elif scope["method"] in ("POST", "PATCH") or scope["method"] == "PUT" and scope["path"].startswith(("/api/_controls/", "/api/_unsaved/")):
                    content_type = dict(scope.get("headers", [])).get(b"content-type", b"")
                    if content_type.split(b";", 1)[0].strip().lower() != b"application/json":
                        raise HTTPError(415, "Send application/json")
                    data = bytearray()
                    while True:
                        event = await self._body_event(receive)
                        if event["type"] == "http.disconnect":
                            return
                        chunk = event.get("body", b"")
                        maximum = request_body_limit(scope['path'], image_limit(self.business) if self.business else None)
                        if len(data) + len(chunk) > maximum:
                            raise HTTPError(413, "Request body exceeds the allowed size")
                        data.extend(chunk)
                        if not event.get("more_body", False):
                            break
                    try:
                        body = json.loads(data, parse_constant=self._invalid_constant)
                    except (ValueError, UnicodeError, RecursionError) as exc:
                        raise HTTPError(400, "Malformed JSON") from exc
                    if not isinstance(body, dict):
                        raise HTTPError(422, "Expected a JSON object")
                if not internal:
                    admission = await self._work.acquire(lane, ticket)
                    seconds = deadline_seconds(path, scope['method'])
                    deadline = None if seconds is None else Deadline(seconds)
                    if deadline is None:
                        status, payload, headers = await self._work.run(lane, self._encoded, self._dispatch, scope, body, lease=admission)
                    else:
                        status, payload, headers = await self._bounded(deadline, receive, lane, scope, body, admission)
            except Busy:
                status, payload, headers = 503, {'error': 'Server busy; try again shortly'}, [(b'retry-after', b'1')]
            except AccessError as exc:
                status, payload = exc.status, {"error": exc.message}
            except HTTPError as exc:
                status, payload, headers = exc.status, {"error": exc.message}, list(exc.headers)
            except ValidationError as exc:
                status, payload = 422, {"error": "Validation failed", "fields": exc.errors}
            except NotFoundError:
                status, payload = 404, {"error": "Record not found"}
            except ConflictError as exc:
                status, payload = 409, {"error": str(exc)}
            except StorageError:
                logger.exception("Attachment storage failed", extra={'request_id': scope.get('evlet.request_id')})
                status, payload = 503, {"error": "Attachment temporarily unavailable"}
            except OperationalError:
                if deadline is not None and deadline.passed():
                    logger.warning("Request stopped at its time limit", extra={'request_id': scope.get('evlet.request_id')})
                    status, payload = 504, {"error": "The request took longer than its time limit"}
                else:
                    logger.exception("Database operation failed", extra={'request_id': scope.get('evlet.request_id')})
                    status, payload = 503, {"error": "Database temporarily unavailable"}
            except Exception:
                logger.exception("Unhandled request failure", extra={'request_id': scope.get('evlet.request_id')})
                status, payload = 500, {"error": "Internal server error"}
            if public and not service and status >= 400:
                from .ui.routes import site_not_found
                status, payload, headers = site_not_found(self, host)
            download = isinstance(payload, Download)
            try:
                encoded = payload.content if download or isinstance(payload, _Encoded) else b"" if status == 204 else json.dumps(jsonable(payload), ensure_ascii=True, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError, RecursionError):
                # Keep the application's JSON error and headers if a payload cannot be encoded.
                logger.exception("Response encoding failed", extra={'request_id': scope.get('evlet.request_id')})
                status, headers, download = 500, [], False
                encoded = b'{"error": "Internal server error"}'
            # no-store unless the response chose its own caching (a site's pages and hashed files, the manifest).
            cached = any(name == b"cache-control" for name, _ in headers)
            response_headers = [(b"x-content-type-options", b"nosniff"), *(() if cached else ((b"cache-control", b"no-store"),)), *headers]
            if status not in (204, 304):
                if not download:
                    response_headers.append((b"content-type", b"application/json; charset=utf-8"))
                response_headers.append((b"content-length", str(len(encoded)).encode()))
            await send({"type": "http.response.start", "status": status, "headers": response_headers})
            await send({"type": "http.response.body", "body": b"" if head else encoded})
        finally:
            if admission is not None:
                admission.release()

    def _encoded(self, function, *args):
        """Run a route in its worker thread and encode its JSON there too, so a large response never holds the
        event loop. A payload that cannot be encoded is returned as it is, for _serve's error path."""
        status, payload, headers = function(*args)
        if status != 204 and not isinstance(payload, Download):
            try:
                payload = _Encoded(json.dumps(jsonable(payload), ensure_ascii=True, allow_nan=False).encode('utf-8'))
            except (TypeError, ValueError, RecursionError):
                pass
        return status, payload, headers

    async def _bounded(self, deadline, receive, lane, scope, body, admission):
        """Run a request under deadline (evlet.database.Deadline). A client that disconnects moves the
        deadline to DISCONNECT_GRACE seconds later, so abandoned work stops and frees its worker."""
        async def watch():
            while (await receive())['type'] != 'http.disconnect':
                pass
            deadline.cancel(DISCONNECT_GRACE)
        watcher = asyncio.create_task(watch())
        token = DEADLINE.set(deadline)
        self._deadlines.add(deadline)
        try:
            return await self._work.run(lane, self._encoded, self._dispatch, scope, body, lease=admission)
        finally:
            DEADLINE.reset(token)
            self._deadlines.discard(deadline)
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    @staticmethod
    async def _body_event(receive):
        # An idle limit between chunks, not a total deadline: a large upload over a slow but
        # steady link completes; the size limit bounds how much one request can send.
        try:
            async with asyncio.timeout(runtime().body_idle_seconds):
                return await receive()
        except TimeoutError:
            raise HTTPError(408, 'Request body timed out') from None

    def _route_label(self, path):
        """Low-cardinality telemetry: never include queries, record ids or arbitrary paths."""
        if path == '/health':
            return path
        parts = path.split('/')
        if len(parts) > 2 and parts[1] == 'api':
            name = parts[2]
            if name in self.registry.exposed:
                return '/api/' + name + ('/{record_or_operation}' if len(parts) > 3 else '')
            if name in ('auth', '_controls', '_settings', '_events', '_eva', '_reports', '_services', '_unsaved', '_nav', '_sites'):
                return '/api/' + name + '/*'
            return '/api/{unknown}'
        if path in PUBLIC_PATHS:
            return path
        return '/{page}'

    def _public_service(self, host, path):
        """Whether path is a public service endpoint that a public host's API scopes list."""
        if not self.business or not path.startswith('/api/_services/'):
            return False
        name = path.removeprefix('/api/_services/')
        return (any(name in item.endpoints for item in self.routes.scopes(host) or ())
                and any(endpoint.name == name and endpoint.public for endpoint in self.business.apis))

    def _api_scope(self, scope):
        """On a sub-host, /api serves sign-in and its frontends' API scopes; everything else is 404."""
        path = scope['path']
        if path != '/api' and not path.startswith('/api/'):
            return
        host = dict(scope.get('headers', [])).get(b'host', b'')
        scopes = self.routes.scopes(host)
        if scopes is None or path in ('/api/auth/session', '/api/auth/login', '/api/auth/logout'):
            return
        # Portal hosts serve the personal settings subset; the settings routes refuse the rest there.
        if path.startswith('/api/_settings/me') and self.routes.realm(host) == 'portal':
            return
        parts = path.split('/')
        if path.startswith('/api/_services/'):
            if any(path.removeprefix('/api/_services/') in item.endpoints for item in scopes):
                return
            raise HTTPError(404, 'Route not found')
        draft = len(parts) == 4 and parts[2] == '_draft'
        model = self.registry.exposed.get(parts[3] if draft else parts[2]) if len(parts) >= 3 else None
        allowed = [item for item in scopes if model in item.models]
        if model is None or not allowed:
            raise HTTPError(404, 'Route not found')
        # Each route family must be listed: records come with the model, the rest by action or extra.
        if draft:
            need = ('extra', 'drafts')
        elif len(parts) == 3 or len(parts) == 4 and path_id(parts[3]) is not None:
            return
        elif len(parts) == 4 and parts[3] in ('measures', 'aggregate') or len(parts) == 5 and parts[4] == 'measures':
            need = ('extra', 'measures')
        elif len(parts) == 5 and parts[4] in CORRECTIONS:
            need = ('action', parts[4])
        elif len(parts) == 6 and parts[4] in ('actions', 'moves'):
            need = ('action', parts[5])
        elif len(parts) == 5 and parts[4] == 'history':
            need = ('extra', 'history')
        elif len(parts) >= 5 and parts[4] == 'versions':
            need = ('extra', 'versions')
        elif len(parts) >= 5 and parts[4] in ('related', 'lines', 'attachments', 'prints'):
            need = ('extra', parts[4])
        else:
            raise HTTPError(404, 'Route not found')
        if any(need[1] in item.extras if need[0] == 'extra' else (model, need[1]) in item.actions for item in allowed):
            return
        raise HTTPError(404, 'Route not found')

    def _guard_local(self, scope, navigation=False):
        headers = {}
        for key, value in scope.get('headers', []):
            key = key.lower()
            if key in (b'host', b'origin', b'sec-fetch-site') and key in headers:
                raise AccessError(403, 'Repeated security header')
            headers[key] = value
        authority = headers.get(b'host', b'').decode('latin1')
        try:
            host = urlsplit('//' + authority)
            port = host.port
            if not authority or any(ord(char) <= 32 for char in authority) or host.username or host.password or host.path or host.query or host.fragment:
                raise ValueError()
            allowed = authority in self.hosts if self.hosts is not None else host.hostname in ('localhost', '127.0.0.1', '::1')
            if not allowed:
                raise ValueError()
            if b'origin' in headers:
                origin = urlsplit(headers[b'origin'].decode('latin1'))
                scheme = scope.get('scheme', 'http')
                if origin.scheme != scheme or origin.hostname != host.hostname or origin.port != port or origin.username or origin.password or origin.path or origin.query or origin.fragment:
                    raise ValueError()
            # A public host's pages may be opened from any other site.
            if headers.get(b'sec-fetch-site') == b'cross-site' and not navigation:
                raise ValueError()
        except ValueError:
            raise AccessError(403, 'Unrecognized host or origin') from None

    async def _sse(self, receive, send, name, frames):
        """Send frames(wait) as text/event-stream. frames is an async generator of SSE frames; between pages it
        awaits wait(timeout, *tasks), which is True once the client has gone or the application is stopping."""
        await send({'type': 'http.response.start', 'status': 200, 'headers': [
            (b'content-type', b'text/event-stream'), (b'cache-control', b'no-store'),
            (b'x-accel-buffering', b'no'), (b'x-content-type-options', b'nosniff')]})
        async def disconnected():
            while (await receive())['type'] != 'http.disconnect':
                pass
        disconnect = asyncio.create_task(disconnected())
        self._loop = asyncio.get_running_loop()
        stopping = asyncio.create_task(self._stopping.wait())

        async def wait(timeout, *tasks):
            done, _ = await asyncio.wait({disconnect, stopping, *tasks}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            return disconnect in done or stopping in done

        stream = frames(wait)
        try:
            try:
                async for body in stream:
                    try:
                        await send({'type': 'http.response.body', 'body': body, 'more_body': True})
                    except OSError:
                        return
            except (AccessError, Busy):
                await send({'type': 'http.response.body', 'body': b'event: unavailable\ndata: {}\n\n', 'more_body': True})
            except Exception:
                logger.exception('%s failed', name)
        except OSError:
            return
        finally:
            await stream.aclose()
            disconnect.cancel()
            stopping.cancel()
            await asyncio.gather(disconnect, stopping, return_exceptions=True)
            try:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            except OSError:
                pass

    async def _event_stream(self, scope, receive, send):
        page = await self._work.run('read', self.business.event_page, scope)

        async def frames(wait):
            current, ends = page, asyncio.get_running_loop().time() + 25
            while True:
                yield ('id: ' + current['cursor'] + '\nevent: evlet\ndata: ' + json.dumps(jsonable(current)) + '\n\n').encode()
                remaining = ends - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return
                # Sleep until the shared poller sees a new event (or briefly, while a backlog remains), and
                # read a page at least every STREAM_RECHECK seconds so an ended session ends the stream.
                changed = asyncio.create_task(self._event_changed.wait())
                try:
                    if await wait(0.05 if current['more'] else min(STREAM_RECHECK, remaining), changed):
                        return
                finally:
                    changed.cancel()
                headers = [(key, value) for key, value in scope.get('headers', []) if key != b'last-event-id']
                headers.append((b'last-event-id', current['cursor'].encode()))
                current = await self._work.run('read', self.business.event_page, {**scope, 'headers': headers})

        self._event_streams += 1
        pump = self._event_pump
        if pump is None or pump.done() or pump.get_loop() is not asyncio.get_running_loop():
            self._event_changed = asyncio.Event()
            self._event_pump = asyncio.create_task(self._pump_events())
        try:
            await self._sse(receive, send, 'Event stream', frames)
        finally:
            self._event_streams -= 1

    def _latest_event(self):
        with self.business.gate.shared():
            return self.business.events.latest()

    async def _pump_events(self):
        """One poller per application (so per database) for all its open event streams: it reads the newest
        event number once a second and wakes every stream when it moves. Streams then read their own
        permission-filtered pages; an idle stream costs no database work between rechecks."""
        seen = None
        try:
            while True:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=1)
                    return
                except asyncio.TimeoutError:
                    pass
                if self._event_streams <= 0:
                    return
                try:
                    latest = await self._work.run('read', self._latest_event)
                except Busy:
                    continue
                except Exception:
                    logger.exception('Event poller failed')
                    return
                if seen is not None and latest != seen:
                    changed, self._event_changed = self._event_changed, asyncio.Event()
                    changed.set()
                seen = latest
        finally:
            if self._event_pump is asyncio.current_task():
                self._event_pump = None

    def _eva_page(self, scope, after):
        self.business.controls.refresh()
        return self.business.eva.stream_page(scope, after)

    async def _eva_stream(self, scope, receive, send):
        """One Eva run's events as SSE: stored events after Last-Event-ID, then new ones as they are written, for about
        25 seconds or until the run ends. A reconnect continues from the last id, so nothing runs twice."""
        self._method(scope['method'], ('GET',))
        last = dict(scope.get('headers', [])).get(b'last-event-id', b'0')
        after = int(last) if last.isdigit() and len(last) <= 19 else 0
        page = await self._work.run('read', self._eva_page, scope, after)

        async def frames(wait):
            current, seen, deadline = page, after, asyncio.get_running_loop().time() + 25
            while True:
                for item in current['items']:
                    seen = item['id']
                    yield f"id: {item['id']}\nevent: {item['type']}\ndata: {json.dumps(item)}\n\n".encode()
                if current['done']:
                    yield b'event: end\ndata: {}\n\n'
                    return
                if asyncio.get_running_loop().time() > deadline:
                    return
                if await wait(0.05 if len(current['items']) >= 200 else 0.25):
                    return
                current = await self._work.run('read', self._eva_page, scope, seen)

        await self._sse(receive, send, 'Eva stream', frames)

    @staticmethod
    def _invalid_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")

    @staticmethod
    def _method(method, allowed):
        if method not in allowed:
            raise HTTPError(405, "Method not allowed", [
                (b"allow", ", ".join(allowed).encode())
            ])

    def _internal(self, scope, raw):
        self.business.controls.refresh()
        if scope['path'] in OIDC_PATHS:
            flow = self.business.oidc
            if flow is None:
                raise HTTPError(404, 'Route not found')
            return flow.dispatch(scope, raw)
        if scope['path'] in MAIL_PATHS:
            return self.business.mailboxes.callback(scope, raw)
        return self.business.assistants.dispatch(scope, raw)

    def _dispatch(self, scope, body):
        path, method = scope["path"], scope["method"]
        if path.startswith(MANAGE):
            if self.manage is None:
                raise HTTPError(404, 'Route not found')
            return self.manage.dispatch(self, scope, body)
        if path == "/health" and not self.routes.public(dict(scope.get('headers', [])).get(b'host', b'')):
            self._method(method, ["GET"])
            self.database.check()
            return 200, {"status": "ok"}, []
        if self.business:
            self.business.controls.refresh()
        from .database import using
        from .ui.routes import dispatch
        with using(self.database):
            response = dispatch(self, scope)
        if response is not None:
            return response
        if self.business:
            return self.business.dispatch(scope, body)
        if path == "/api/schema":
            self._method(method, ["GET"])
            return 200, {**self.registry.ui_schema(), 'timezone': getattr(self.database, 'timezone', None) or 'UTC'}, []
        parts = path.split("/")
        if len(parts) not in (3, 4) or parts[:2] != ["", "api"]:
            raise HTTPError(404, "Route not found")
        model = self.registry.exposed.get(parts[2])
        if model is None:
            raise HTTPError(404, "Model not found")
        if method != "GET" and self.registry.api.get(model) == "read":
            raise HTTPError(403, read_only(model))
        if method in ('POST', 'PATCH'):
            errors = {name: 'This field is read-only' for name in body
                      if name in model.__fields__ and model.__fields__[name].read_only}
            if errors:
                raise ValidationError(errors)
        if len(parts) == 4:
            self._method(method, ["GET", "PATCH", "DELETE"])
            record_id = path_id(parts[3])
            if record_id is None:
                raise HTTPError(404, "Record not found")
            if method == "DELETE":
                self.database.delete(model, record_id)
                return 204, None, []
            record = (self.database.update(model, record_id, **body) if method == "PATCH"
                      else self.database.get(model, record_id))
            return 200, record.to_dict(), []
        self._method(method, ["GET", "POST"])
        if method == "POST":
            record = self.database.create(model, **body)
            location = f"/api/{parts[2]}/{record.id}".encode()
            return 201, record.to_dict(), [(b"location", location)]
        try:
            if len(scope.get("query_string", b"")) > 8192:
                raise ValueError("Query exceeds 8 KiB")
            query = parse_qs(scope.get("query_string", b"").decode("ascii"),
                             keep_blank_values=True, max_num_fields=10)
            if set(query) - {"limit", "offset", "filters", "order_by", "q"} or any(len(v) != 1 for v in query.values()):
                raise ValueError("Unknown or repeated query parameters")
            limit = int(query.get("limit", ["50"])[0])
            offset = int(query.get("offset", ["0"])[0])
            filters = json.loads(query["filters"][0], parse_constant=self._invalid_constant) if "filters" in query else None
            order_by = query["order_by"][0].split(",") if "order_by" in query else None
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise HTTPError(422, "Use valid limit, offset, filters JSON, and order_by parameters") from exc
        records, total = self.database.page(model, limit=limit, offset=offset, filters=filters, order_by=order_by, search=query.get("q", [""])[0])
        return 200, {"items": [record.to_dict() for record in records],
                     "limit": limit, "offset": offset, "total": total}, []

    def initialize(self):
        # Moving stored amounts to 6 places (evlet.migrations.money_scale) is a framework step too: inside evlet migrate for instances.
        self.database.upgrade_money = not self.managed_framework
        self.database.initialize()
        # Instances apply framework steps only through evlet migrate; other applications apply them here.
        from .framework import upgrade
        upgrade(self.database, apply=not self.managed_framework)
        if self.business:
            self.business.initialize()
        if self.live_routes is not None:
            self.live_routes.refresh(force=True)

    def stop(self):
        """Stop admission and end live streams before server shutdown. Safe from any thread."""
        self._work.stop()
        # Running report work is interrupted, so a restart does not wait for it (or find the database locked).
        for deadline in list(self._deadlines):
            deadline.cancel()
        loop = getattr(self, '_loop', None)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and loop is not running and not loop.is_closed():
            loop.call_soon_threadsafe(self._stopping.set)
        else:
            self._stopping.set()

    async def _lifespan(self, receive, send):
        generation = None
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                self._stopping = asyncio.Event()
                self._work.start()
                try:
                    runtime()  # Validate process limits before accepting traffic.
                    deadline_seconds('/api/_reports/', 'POST'), deadline_seconds('/', 'GET')
                    if several_workers():
                        logger.warning('Evlet runs as one web process per database: with several workers, API key '
                                       'rate limits, the write turn and the tenant cache are counted per worker. '
                                       'Start one worker (docs/performance.md).')
                    from .instances import activate_generation
                    generation = activate_generation(self)
                    generation.__enter__()
                    await asyncio.to_thread(self.initialize)
                except Exception:
                    if generation: generation.__exit__(None, None, None)
                    logger.exception("Database initialization failed")
                    await send({"type": "lifespan.startup.failed", "message": "Database initialization failed"})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                self.stop()
                await self._work.drain()
                await asyncio.to_thread(self.database.close)
                if generation: generation.__exit__(None, None, None)
                await send({"type": "lifespan.shutdown.complete"})
                return
