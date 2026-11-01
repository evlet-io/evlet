import asyncio
import json

import pytest

from evlet import Application, Database
from examples.playground.app import create_app
from examples.playground.models import Customer, registry


def request(app, method, path, data=None, *, body=None, content_type=b"application/json", chunks=None):
    async def run():
        route, _, query = path.partition("?")
        raw = json.dumps(data).encode() if body is None else body
        messages = []
        pieces = chunks if chunks is not None else [raw]
        events = [{"type": "http.request", "body": piece, "more_body": index < len(pieces)-1}
                  for index, piece in enumerate(pieces)]
        async def receive():
            return events.pop(0) if events else {"type": "http.disconnect"}
        async def send(event):
            messages.append(event)
        await app({"type": "http", "method": method, "path": route,
                   "query_string": query.encode(), "headers": [(b"host", b"localhost"), (b"content-type", content_type)]}, receive, send)
        return messages[0]["status"], json.loads(messages[1]["body"]) if messages[1]["body"] else None, dict(messages[0]["headers"])
    return asyncio.run(run())


def test_complete_customer_invoice_payment_api(application):
    assert request(application, "GET", "/health")[:2] == (200, {"status": "ok"})
    status, schema, _ = request(application, "GET", "/api/schema")
    assert status == 200 and len(schema["models"]) == 5
    status, customer, headers = request(application, "POST", "/api/customer", {"name": "Studio"})
    assert status == 201
    assert headers[b"location"] == f'/api/customer/{customer["id"]}'.encode()
    status, invoice, _ = request(application, "POST", "/api/invoice", {
        "customer": customer["id"], "reference": "INV-1", "total": "19.95",
    })
    assert status == 201 and invoice["total"] == "19.95"
    status, payment, _ = request(application, "POST", "/api/payment", {
        "invoice": invoice["id"], "reference": "PAY-1", "amount": "19.95",
    })
    assert status == 201 and payment["invoice"] == invoice["id"]
    assert request(application, "GET", f'/api/payment/{payment["id"]}')[1] == payment
    assert request(application, "GET", "/api/customer?limit=1&offset=0")[1]["items"] == [customer]


@pytest.mark.parametrize("method,path,data,status", [
    ("GET", "/assets/missing.js", None, 404),
    ("GET", "/api/missing", None, 404),
    ("GET", "/api/customer/99", None, 404),
    ("GET", "/api/customer/no", None, 404),
    ("DELETE", "/api/customer/1", None, 404),
    ("PUT", "/api/customer", None, 405),
    ("POST", "/health", {}, 405),
    ("POST", "/api/customer", {}, 422),
    ("POST", "/api/customer", {"name": "A", "model": "fake"}, 422),
    ("POST", "/api/customer", [], 422),
    ("POST", "/api/invoice", {"customer": 99, "reference": "A", "total": "1.00"}, 409),
    ("GET", "/api/customer?limit=0", None, 422),
    ("GET", "/api/customer?limit=101", None, 422),
    ("GET", "/api/customer?offset=-1", None, 422),
    ("GET", "/api/customer?offset=words", None, 422),
    ("GET", "/api/customer?limit=1&limit=2", None, 422),
    ("GET", "/api/customer?filter=anything", None, 422),
])
def test_error_responses(application, method, path, data, status):
    actual, payload, headers = request(application, method, path, data)
    assert actual == status
    assert "error" in payload
    assert headers[b"content-type"].startswith(b"application/json")
    if status == 405:
        assert b"allow" in headers


def test_request_bounds_validation_and_chunks(application):
    status, error, _ = request(application, "POST", "/api/customer", {"name": ""})
    assert status == 422 and "name" in error["fields"]
    for raw in (b"{", b'{"name": NaN}', b'\xff', b'[' * 2000):
        assert request(application, "POST", "/api/customer", body=raw)[0] == 400
    assert request(application, "POST", "/api/customer", body=b"x" * 65537)[0] == 413
    assert request(application, "POST", "/api/customer", {}, content_type=b"text/plain")[0] == 415
    status, record, _ = request(application, "POST", "/api/customer", chunks=[b'{"name":', b'"Chunked"}'])
    assert status == 201 and record["name"] == "Chunked"


def test_lifespan_initializes_database_and_shuts_down(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "playground.sqlite3"
    monkeypatch.setenv("EVLET_DATABASE", str(path))
    app = create_app()
    async def run():
        events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
        sent = []
        async def receive():
            return next(events)
        async def send(event):
            sent.append(event)
        await app({"type": "lifespan"}, receive, send)
        return sent
    assert asyncio.run(run()) == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]
    assert path.is_file()
    from test_business import http
    assert http(app, "POST", "/api/customer", {"name": "Ready"})[0] == 401
    app.database.create(Customer, name="Ready")

    from evlet import CreateModel, Migration, Model, Registry, Text
    from examples.playground.migrate import migrations

    class Note(Model):
        message = Text()

    next_migration = Migration('0004_notes', CreateModel(Note))
    target = Database(path, Registry(*registry.models.values(), Note))
    assert target.migrate([*migrations, next_migration]) == ['0004_notes']
    assert target.get(Customer, 1).name == 'Ready'


def test_database_failure_returns_service_unavailable(tmp_path):
    app = Application(Database(tmp_path / "missing" / "db.sqlite3", registry))
    status, error, _ = request(app, "GET", "/health")
    assert status == 503 and error == {"error": "Database temporarily unavailable"}
