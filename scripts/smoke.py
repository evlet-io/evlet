"""Exercise the real running API. Each run creates, validates, and removes a uniquely named workflow."""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from uuid import uuid4


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    def request(path, data=None, expected=200, method=None):
        body = None if data is None else json.dumps(data).encode()
        req = Request(base + path, data=body, method=method, headers={"Content-Type": "application/json"})
        try:
            response = urlopen(req, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            payload = None if response.status == 204 else json.load(response)
            check(response.status == expected, f"{path}: expected {expected}, got {response.status}: {payload}")
            return payload

    check(request("/health") == {"status": "ok"}, "Health check failed")
    schema = request("/api/schema")
    check(schema["version"] == 1, "Unexpected schema version")
    check({item["name"] for item in schema["models"]} == {"customer", "invoice", "payment"}, "Missing Playground models")
    suffix = uuid4().hex[:12]
    customer = request("/api/customer", {"name": f"Smoke {suffix}"}, 201)
    invoice = request("/api/invoice", {
        "customer": customer["id"], "reference": f"SMOKE-INV-{suffix}", "total": "123.45",
    }, 201)
    payment = request("/api/payment", {
        "invoice": invoice["id"], "reference": f"SMOKE-PAY-{suffix}", "amount": "123.45",
    }, 201)
    check(invoice["total"] == "123.45" and payment["amount"] == "123.45", "Currency lost precision")
    check(request(f'/api/customer/{customer["id"]}')["name"] == customer["name"], "Customer read failed")
    check(request(f'/api/payment/{payment["id"]}') == payment, "Payment read failed")
    check(request("/api/customer?limit=1")["items"][0]["id"] == customer["id"], "Paginated list failed")
    error = request("/api/customer", {"name": ""}, 422)
    check("name" in error["fields"], "Missing field validation")
    request("/api/invoice", {"customer": 2**63-1, "reference": f"ORPHAN-{suffix}", "total": "1.00"}, 409)
    updated = request(f'/api/invoice/{invoice["id"]}', {
        'total': '124.56', 'posting_date': '2026-10-28', 'status': 'sent', 'tax_rate': '5.1234',
    }, method='PATCH')
    check(updated['total'] == '124.56' and updated['tax_rate'] == '5.1234', 'Update lost precision')
    query = urlencode({'filters': json.dumps({'invoice__customer__name': customer['name']}), 'order_by': 'amount'})
    check(request('/api/payment?' + query)['items'][0]['id'] == payment['id'], 'Relationship query failed')
    request(f'/api/customer/{customer["id"]}', expected=409, method='DELETE')
    request(f'/api/payment/{payment["id"]}', expected=204, method='DELETE')
    request(f'/api/invoice/{invoice["id"]}', expected=204, method='DELETE')
    request(f'/api/customer/{customer["id"]}', expected=204, method='DELETE')
    request(f'/api/customer/{customer["id"]}', expected=404)
    print("Live smoke passed: updates, typed fields, relationship queries, protected deletes, cleanup; health, schema, Customer → Invoice → Payment, reads, pagination, validation, foreign keys")


if __name__ == "__main__":
    main()
