"""Portable ASGI HTTP and lifespan support without a web framework dependency."""

import asyncio
import json
import logging
import sqlite3
from urllib.parse import parse_qs

from .database import ConflictError, NotFoundError
from .models import ValidationError

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 64 * 1024


class HTTPError(Exception):
    def __init__(self, status, message, headers=()):
        self.status = status
        self.message = message
        self.headers = headers


class Application:
    def __init__(self, database):
        self.database = database
        self.registry = database.registry

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
            if scope["method"] == "POST":
                content_type = dict(scope.get("headers", [])).get(b"content-type", b"")
                if content_type.split(b";", 1)[0].strip().lower() != b"application/json":
                    raise HTTPError(415, "Send application/json")
                data = bytearray()
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    data.extend(event.get("body", b""))
                    if len(data) > MAX_BODY_BYTES:
                        raise HTTPError(413, "Request body exceeds 64 KiB")
                    if not event.get("more_body", False):
                        break
                try:
                    body = json.loads(data, parse_constant=self._invalid_constant)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise HTTPError(400, "Malformed JSON") from exc
                if not isinstance(body, dict):
                    raise HTTPError(422, "Expected a JSON object")
            status, payload, headers = await asyncio.to_thread(self._dispatch, scope, body)
        except HTTPError as exc:
            status, payload, headers = exc.status, {"error": exc.message}, list(exc.headers)
        except ValidationError as exc:
            status, payload = 422, {"error": "Validation failed", "fields": exc.errors}
        except NotFoundError:
            status, payload = 404, {"error": "Record not found"}
        except ConflictError as exc:
            status, payload = 409, {"error": str(exc)}
        except sqlite3.OperationalError:
            logger.exception("SQLite operation failed")
            status, payload = 503, {"error": "Database temporarily unavailable"}
        except Exception:
            logger.exception("Unhandled request failure")
            status, payload = 500, {"error": "Internal server error"}
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(encoded)).encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"cache-control", b"no-store"), *headers,
        ]})
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
        if path == "/api/schema":
            self._method(method, ["GET"])
            return 200, self.registry.schema(), []
        parts = path.split("/")
        if len(parts) not in (3, 4) or parts[:2] != ["", "api"]:
            raise HTTPError(404, "Route not found")
        model = self.registry.models.get(parts[2])
        if model is None:
            raise HTTPError(404, "Model not found")
        if len(parts) == 4:
            self._method(method, ["GET"])
            if not parts[3].isascii() or not parts[3].isdigit() or len(parts[3]) > 19:
                raise HTTPError(404, "Record not found")
            record = self.database.get(model, int(parts[3]))
            return 200, record.to_dict(), []
        self._method(method, ["GET", "POST"])
        if method == "POST":
            record = self.database.create(model, **body)
            location = f"/api/{parts[2]}/{record.id}".encode()
            return 201, record.to_dict(), [(b"location", location)]
        try:
            query = parse_qs(scope.get("query_string", b"").decode("ascii"),
                             keep_blank_values=True, max_num_fields=10)
            if set(query) - {"limit", "offset"} or any(len(v) != 1 for v in query.values()):
                raise ValueError("Unknown or repeated query parameters")
            limit = int(query.get("limit", ["50"])[0])
            offset = int(query.get("offset", ["0"])[0])
        except (ValueError, UnicodeError) as exc:
            raise HTTPError(422, "Use integer limit and offset query parameters") from exc
        records = self.database.list(model, limit=limit, offset=offset)
        return 200, {"items": [record.to_dict() for record in records],
                     "limit": limit, "offset": offset}, []

    async def _lifespan(self, receive, send):
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                try:
                    await asyncio.to_thread(self.database.initialize)
                except Exception:
                    logger.exception("Database initialization failed")
                    await send({"type": "lifespan.startup.failed", "message": "Database initialization failed"})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
