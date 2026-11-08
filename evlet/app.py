"""Portable ASGI HTTP and lifespan support without a web framework dependency."""

import asyncio
import json
import logging
import sqlite3
from urllib.parse import parse_qs, urlsplit

from .database import ConflictError, NotFoundError
from .identity import jsonable, path_id
from .models import ValidationError
from .permissions import CALLER, CORRECTIONS, read_only
from .auth import AccessError
from .business import Business, Download
from .storage import StorageError

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 64 * 1024
# Unsaved changes may be up to 1 MB (the unsaved.entry_kb maximum); the route checks the instance limit.
UNSAVED_BODY = 1024 * 1024 + 4096


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

    def __init__(self, database, *, auth=None, policy=None, actions=(), children=(), attachments=(), storage=None, jobs=None, events=None, name="Evlet", hosts=None, reports=(), apis=(), playground=False, routes=None, connectors=(), secret_key=None, slips=(), ai=None, prints=()):
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
        self.name = name
        self.database = database
        self.registry = database.registry
        if auth is None and (policy is not None or actions or children or attachments or storage is not None or jobs is not None or events is not None):
            raise ValueError("Business features require authentication")
        self.business = None if auth is None else Business(database, auth, policy=policy, actions=actions, children=children, attachments=attachments, storage=storage, jobs=jobs, events=events)
        if self.business:
            from .routing import authority
            if auth.realms != {authority(origin).encode(): realm for origin, realm in self.routes.realms().items()}:
                raise ValueError('LocalAuth realms must match the route table: pass realms=routes.realms()')
            self.business.routes = self.routes
            self.business.application = {"name": name, "playground": bool(playground), "docs": self.routes.docs_path if self.routes.docs else None}
            from .forms import Forms
            self.business.forms = Forms(self.business, reports)
            from .slips import Slips
            self.business.slips = Slips(self.business, slips)
            from .printing import Prints
            self.business.prints = Prints(self.business, prints)
            self.business.apis = tuple(apis)
            self.business.controls.routes = self.routes
            for connector in connectors:
                self.business.connectors.register('app', connector)
            # Secrets are sealed with a key outside the database: EVLET_SECRET_KEY or secret_key, shared with
            # two-step sign-in. Without either, connector secrets use a key file next to a SQLite file.
            if secret_key is not None:
                self.business.secret_key = secret_key
            path = getattr(database, 'path', None)
            self.business.connectors.fallback = path.parent / 'secret.key' if path is not None else None
            # Eva only when config.py turns AI on: AI(enabled=True, models={...}). Otherwise nothing of Eva exists.
            if ai is not None:
                from .eva import AI, Eva
                if not isinstance(ai, AI):
                    raise ValueError('Pass ai=AI(...)')
                if ai.enabled:
                    self.business.eva = Eva(self.business, ai)
        elif reports or apis or connectors or slips or prints or ai is not None:
            raise ValueError('Reports, slips, print designs, APIs, connectors and AI require authentication')

    async def __call__(self, scope, receive, send):
        # A request is a caller: Meta.api limits what it reaches (evlet/permissions.py); app code it runs is trusted.
        token = CALLER.set(scope.get("type") == "http")
        try:
            await self._serve(scope, receive, send)
        finally:
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
        headers = []
        try:
            if self.business is None:
                self._guard_local(scope)
            self._api_scope(scope)
            if scope['path'] == '/api/_events/stream' and self.business:
                await self._event_stream(scope, receive, send)
                return
            if self.business and self.business.eva is not None and scope['path'].startswith('/api/_eva/runs/') and scope['path'].endswith('/stream'):
                await self._eva_stream(scope, receive, send)
                return
            body = None
            internal = bool(self.business) and scope['path'].startswith(('/_/', '/.well-known/'))
            if internal:
                # OAuth and the remote MCP endpoint read their own bodies (JSON or form), at most 64 KiB like /api.
                data = bytearray()
                while scope['method'] == 'POST':
                    event = await receive()
                    if event['type'] == 'http.disconnect':
                        return
                    data.extend(event.get('body', b''))
                    if len(data) > MAX_BODY_BYTES:
                        raise HTTPError(413, 'Request body exceeds the allowed size')
                    if not event.get('more_body', False):
                        break
                status, payload, headers = await asyncio.to_thread(self._internal, scope, bytes(data))
            # PUT carries a body only on the Controls routes (connectors) and unsaved changes; elsewhere it stays a 405.
            elif scope["method"] in ("POST", "PATCH") or scope["method"] == "PUT" and scope["path"].startswith(("/api/_controls/", "/api/_unsaved/")):
                content_type = dict(scope.get("headers", [])).get(b"content-type", b"")
                if content_type.split(b";", 1)[0].strip().lower() != b"application/json":
                    raise HTTPError(415, "Send application/json")
                data = bytearray()
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    data.extend(event.get("body", b""))
                    # Print designs carry their images as data: URIs (a design is at most 256 KiB).
                    # Attachment routes also take rich text images, up to the Controls image limit (as base64).
                    uploads = max(1500000, image_limit(self.business) * 4 // 3 + 4096) if self.business else 1500000
                    maximum = (uploads if scope['path'].endswith(('/attachments', '/_images')) else 1500000 if scope['path'] == '/api/_eva/files' else 2900000 if scope['path'] == '/api/_settings/me/photo'
                               else 300000 if scope['path'].startswith('/api/_controls/prints')
                               else UNSAVED_BODY if scope['path'].startswith('/api/_unsaved/') else MAX_BODY_BYTES) if self.business else MAX_BODY_BYTES
                    if len(data) > maximum:
                        raise HTTPError(413, "Request body exceeds the allowed size")
                    if not event.get("more_body", False):
                        break
                try:
                    body = json.loads(data, parse_constant=self._invalid_constant)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise HTTPError(400, "Malformed JSON") from exc
                if not isinstance(body, dict):
                    raise HTTPError(422, "Expected a JSON object")
            if not internal:
                status, payload, headers = await asyncio.to_thread(self._dispatch, scope, body)
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
            logger.exception("Attachment storage failed")
            status, payload = 503, {"error": "Attachment temporarily unavailable"}
        except sqlite3.OperationalError:
            logger.exception("Database operation failed")
            status, payload = 503, {"error": "Database temporarily unavailable"}
        except Exception:
            logger.exception("Unhandled request failure")
            status, payload = 500, {"error": "Internal server error"}
        download = isinstance(payload, Download)
        try:
            encoded = payload.content if download else b"" if status == 204 else json.dumps(jsonable(payload), ensure_ascii=True, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            # Keep the application's JSON error and headers if a payload cannot be encoded.
            logger.exception("Response encoding failed")
            status, headers, download = 500, [], False
            encoded = b'{"error": "Internal server error"}'
        response_headers = [(b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store"), *headers]
        if status != 204:
            if not download:
                response_headers.append((b"content-type", b"application/json; charset=utf-8"))
            response_headers.append((b"content-length", str(len(encoded)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": encoded})

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
        elif len(parts) == 5 and parts[4] in ('history', 'revisions', 'snapshot', 'diff'):
            need = ('extra', 'history' if parts[4] == 'history' else 'revisions')
        elif len(parts) >= 5 and parts[4] in ('related', 'lines', 'attachments', 'prints'):
            need = ('extra', parts[4])
        else:
            raise HTTPError(404, 'Route not found')
        if any(need[1] in item.extras if need[0] == 'extra' else (model, need[1]) in item.actions for item in allowed):
            return
        raise HTTPError(404, 'Route not found')

    def _guard_local(self, scope):
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
            if headers.get(b'sec-fetch-site') == b'cross-site':
                raise ValueError()
        except ValueError:
            raise AccessError(403, 'Unrecognized host or origin') from None

    async def _event_stream(self, scope, receive, send):
        page = await asyncio.to_thread(self.business.event_page, scope)
        await send({'type': 'http.response.start', 'status': 200, 'headers': [
            (b'content-type', b'text/event-stream'), (b'cache-control', b'no-store'),
            (b'x-accel-buffering', b'no'), (b'x-content-type-options', b'nosniff')]})
        async def disconnected():
            while (await receive())['type'] != 'http.disconnect':
                pass
        disconnect = asyncio.create_task(disconnected())
        self._loop = asyncio.get_running_loop()
        stopping = asyncio.create_task(self._stopping.wait())
        try:
            for iteration in range(25):
                encoded = ('id: ' + page['cursor'] + '\nevent: evlet\ndata: ' + json.dumps(jsonable(page)) + '\n\n').encode()
                await send({'type': 'http.response.body', 'body': encoded, 'more_body': True})
                done, _ = await asyncio.wait({disconnect, stopping}, timeout=0.05 if page['more'] else 1, return_when=asyncio.FIRST_COMPLETED)
                if done:
                    return
                headers = [(key, value) for key, value in scope.get('headers', []) if key != b'last-event-id']
                headers.append((b'last-event-id', page['cursor'].encode()))
                try:
                    page = await asyncio.to_thread(self.business.event_page, {**scope, 'headers': headers})
                except AccessError:
                    await send({'type': 'http.response.body', 'body': b'event: unavailable\ndata: {}\n\n', 'more_body': True})
                    return
                except Exception:
                    logger.exception('Event stream failed')
                    return
        except OSError:
            return
        finally:
            disconnect.cancel()
            stopping.cancel()
            await asyncio.gather(disconnect, stopping, return_exceptions=True)
            try:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            except OSError:
                pass

    async def _eva_stream(self, scope, receive, send):
        """One Eva run's events as SSE: stored events after Last-Event-ID, then new ones as they are written, for about
        25 seconds or until the run ends. A reconnect continues from the last id, so nothing runs twice."""
        if scope['method'] != 'GET':
            raise HTTPError(405, 'Method not allowed', [(b'allow', b'GET')])
        last = dict(scope.get('headers', [])).get(b'last-event-id', b'0')
        after = int(last) if last.isdigit() and len(last) <= 19 else 0
        self.business.controls.refresh()
        page = await asyncio.to_thread(self.business.eva.stream_page, scope, after)
        await send({'type': 'http.response.start', 'status': 200, 'headers': [
            (b'content-type', b'text/event-stream'), (b'cache-control', b'no-store'),
            (b'x-accel-buffering', b'no'), (b'x-content-type-options', b'nosniff')]})
        async def disconnected():
            while (await receive())['type'] != 'http.disconnect':
                pass
        disconnect = asyncio.create_task(disconnected())
        self._loop = asyncio.get_running_loop()
        stopping = asyncio.create_task(self._stopping.wait())
        deadline = asyncio.get_running_loop().time() + 25
        try:
            while True:
                for item in page['items']:
                    after = item['id']
                    await send({'type': 'http.response.body', 'body': f"id: {item['id']}\nevent: {item['type']}\ndata: {json.dumps(item)}\n\n".encode(), 'more_body': True})
                if page['done']:
                    await send({'type': 'http.response.body', 'body': b'event: end\ndata: {}\n\n', 'more_body': True})
                    return
                if asyncio.get_running_loop().time() > deadline:
                    return
                done, _ = await asyncio.wait({disconnect, stopping}, timeout=0.05 if len(page['items']) >= 200 else 0.25, return_when=asyncio.FIRST_COMPLETED)
                if done:
                    return
                try:
                    page = await asyncio.to_thread(self.business.eva.stream_page, scope, after)
                except AccessError:
                    await send({'type': 'http.response.body', 'body': b'event: unavailable\ndata: {}\n\n', 'more_body': True})
                    return
                except Exception:
                    logger.exception('Eva stream failed')
                    return
        except OSError:
            return
        finally:
            disconnect.cancel()
            stopping.cancel()
            await asyncio.gather(disconnect, stopping, return_exceptions=True)
            try:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            except OSError:
                pass

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
        if scope['path'] in ('/_/mail/callback', '/_/mail/continue.js'):
            return self.business.mailboxes.callback(scope, raw)
        return self.business.assistants.dispatch(scope, raw)

    def _dispatch(self, scope, body):
        path, method = scope["path"], scope["method"]
        if path == "/health":
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
            return 200, self.registry.ui_schema(), []
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
        self.database.initialize()
        # Instances apply framework steps only through evlet migrate; other applications apply them here.
        from .framework import upgrade
        upgrade(self.database, apply=not self.managed_framework)
        if self.business:
            self.business.initialize()

    def stop(self):
        """End live streams before an ASGI server waits for open connections. Safe from any thread."""
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
                try:
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
                await asyncio.to_thread(self.database.close)
                if generation: generation.__exit__(None, None, None)
                await send({"type": "lifespan.shutdown.complete"})
                return
