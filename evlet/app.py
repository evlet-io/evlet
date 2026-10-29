"""Portable ASGI HTTP and lifespan support without a web framework dependency."""

import asyncio
import json
import logging
import sqlite3
from urllib.parse import parse_qs

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
    def __init__(self, database, *, auth=None, policy=None, actions=(), children=(), attachments=(), storage=None):
        self.database = database
        self.registry = database.registry
        if auth is None and (policy is not None or actions or children or attachments or storage is not None):
            raise ValueError("Business features require authentication")
        self.business = None if auth is None else Business(database, auth, policy=policy, actions=actions, children=children, attachments=attachments, storage=storage)

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
            logger.exception("SQLite operation failed")
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

    async def _lifespan(self, receive, send):
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                try:
                    await asyncio.to_thread(self.initialize)
                except Exception:
                    logger.exception("Database initialization failed")
                    await send({"type": "lifespan.startup.failed", "message": "Database initialization failed"})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
