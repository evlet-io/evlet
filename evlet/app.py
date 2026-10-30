"""Portable ASGI HTTP and lifespan support without a web framework dependency."""

import asyncio
import json
import logging
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

from .database import ConflictError, NotFoundError
from .models import ValidationError
from .auth import AccessError
from .business import Business, Download
from .storage import StorageError

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 64 * 1024


class HTTPError(Exception):
    def __init__(self, status, message, headers=()):
        self.status = status
        self.message = message
        self.headers = headers


class Application:
    def __init__(self, database, *, auth=None, policy=None, actions=(), children=(), attachments=(), storage=None, jobs=None, events=None, views=(), ui=None, name="Evlet", hosts=None):
        from .ui import ViewRegistry
        if isinstance(hosts, (str, bytes)):
            raise ValueError("Pass a collection of explicit Host header values")
        self.hosts = None if hosts is None else frozenset(hosts)
        if hosts is not None and (not self.hosts or any(not isinstance(host, str) or not host for host in self.hosts)):
            raise ValueError("Configure explicit Host header values")
        self._stopping = asyncio.Event()
        self.name = name
        self.ui = ui or ViewRegistry(views)
        if ui is not None and views:
            raise ValueError("Pass either views or a UI registry")
        self.ui.validate(database.registry.ui_schema())
        self.database = database
        self.registry = database.registry
        if auth is None and (policy is not None or actions or children or attachments or storage is not None or jobs is not None or events is not None):
            raise ValueError("Business features require authentication")
        self.business = None if auth is None else Business(database, auth, policy=policy, actions=actions, children=children, attachments=attachments, storage=storage, jobs=jobs, events=events)
        if self.business:
            self.business.application = {"name": name, "playground": name == "Evlet Playground"}

    async def __call__(self, scope, receive, send):
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
            if scope['path'] == '/api/_events/stream' and self.business:
                await self._event_stream(scope, receive, send)
                return
            body = None
            if scope["method"] in ("POST", "PATCH"):
                content_type = dict(scope.get("headers", [])).get(b"content-type", b"")
                if content_type.split(b";", 1)[0].strip().lower() != b"application/json":
                    raise HTTPError(415, "Send application/json")
                data = bytearray()
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    data.extend(event.get("body", b""))
                    maximum = 1500000 if self.business and scope['path'].endswith('/attachments') else MAX_BODY_BYTES
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
        encoded = payload.content if download else b"" if status == 204 else json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
        response_headers = [(b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store"), *headers]
        if status != 204:
            if not download:
                response_headers.append((b"content-type", b"application/json; charset=utf-8"))
            response_headers.append((b"content-length", str(len(encoded)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": encoded})

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
        stopping = asyncio.create_task(self._stopping.wait())
        try:
            for iteration in range(25):
                encoded = ('id: ' + page['cursor'] + '\nevent: evlet\ndata: ' + json.dumps(page) + '\n\n').encode()
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

    @staticmethod
    def _invalid_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")

    @staticmethod
    def _method(method, allowed):
        if method not in allowed:
            raise HTTPError(405, "Method not allowed", [
                (b"allow", ", ".join(allowed).encode())
            ])

    def _dispatch(self, scope, body):
        path, method = scope["path"], scope["method"]
        if path == "/health":
            self._method(method, ["GET"])
            self.database.check()
            return 200, {"status": "ok"}, []
        if path in ('/api/ui', '/api/ui/resolve'):
            self._method(method, ['GET'])
            user = None
            if self.business:
                self.business.auth.guard(scope)
                user, _ = self.business.auth.session(scope)
                schema = self.business.schema(user)
            else:
                schema = self.registry.ui_schema()
            if path == '/api/ui':
                return 200, {'name': self.name, **self.ui.compile(schema, user)}, []
            try:
                query = parse_qs(scope.get('query_string', b'').decode('ascii'), strict_parsing=True, max_num_fields=1)
                if set(query) != {'path'} or len(query['path']) != 1: raise ValueError()
                return 200, self.ui.resolve(query['path'][0], schema, user), []
            except (ValueError, UnicodeError):
                raise HTTPError(422, 'Supply one valid view path')
            except LookupError:
                raise HTTPError(404, 'View not found or not permitted')
        if not path.startswith('/api/') and method == 'GET':
            if self.business: self.business.auth.guard(scope)
            from .ui.serving import static_response
            from .ui.registry import route_pattern
            builtin = path in ('/', '/login', '/docs', '/settings/accounts') or path.startswith('/docs/') or path.startswith('/assets/')
            generated = re.fullmatch(r'/app/([a-z][a-z0-9_]*)(?:/(new|[1-9][0-9]{0,15})(/view)?)?', path)
            known_model = generated and generated[1] in self.registry.models
            custom = any(view.route and route_pattern(view.route).fullmatch(path) for view in self.ui.views.values())
            if not (builtin or known_model or custom): raise HTTPError(404, 'Route not found')
            response = static_response(path)
            if response is not None: return response
            raise HTTPError(404, 'UI assets are not built. Run the frontend production build.')
        if self.business:
            return self.business.dispatch(scope, body)
        if path == "/api/schema":
            self._method(method, ["GET"])
            return 200, self.registry.ui_schema(), []
        parts = path.split("/")
        if len(parts) not in (3, 4) or parts[:2] != ["", "api"]:
            raise HTTPError(404, "Route not found")
        model = self.registry.models.get(parts[2])
        if model is None:
            raise HTTPError(404, "Model not found")
        if method in ('POST', 'PATCH'):
            errors = {name: 'This field is read-only' for name in body
                      if name in model.__fields__ and model.__fields__[name].read_only}
            if errors:
                raise ValidationError(errors)
        if len(parts) == 4:
            self._method(method, ["GET", "PATCH", "DELETE"])
            if not parts[3].isascii() or not parts[3].isdigit() or len(parts[3]) > 19:
                raise HTTPError(404, "Record not found")
            record_id = int(parts[3])
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
        if self.business:
            self.business.initialize()

    def stop(self):
        """End live streams before an ASGI server waits for open connections."""
        self._stopping.set()

    async def _lifespan(self, receive, send):
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                self._stopping = asyncio.Event()
                try:
                    await asyncio.to_thread(self.initialize)
                except Exception:
                    logger.exception("Database initialization failed")
                    await send({"type": "lifespan.startup.failed", "message": "Database initialization failed"})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                self.stop()
                await asyncio.to_thread(self.database.close)
                await send({"type": "lifespan.shutdown.complete"})
                return
