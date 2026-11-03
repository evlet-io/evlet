"""Exercise the real running API. Each run creates, validates, and removes a uniquely named workflow."""

import argparse
import base64
import getpass
import os
import secrets
from http.cookiejar import CookieJar
import json
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.parse import urlencode
from uuid import uuid4


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument('--setup', action='store_true', help='Create a temporary initial administrator in an empty disposable database')
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    csrf = ''

    def request(path, data=None, expected=200, method=None):
        body = None if data is None else json.dumps(data).encode()
        req = Request(base + path, data=body, method=method, headers={"Content-Type": "application/json", "X-Evlet-Request": "1", "X-CSRF-Token": csrf})
        try:
            response = opener.open(req, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            payload = None if response.status == 204 else json.load(response)
            check(response.status == expected, f"{path}: expected {expected}, got {response.status}: {payload}")
            return payload

    check(request("/health") == {"status": "ok"}, "Health check failed")
    state = request('/api/auth/session')
    if args.setup:
        check(state.get('setup'), '--setup requires an empty disposable account database')
        username, password = 'smoke-admin', secrets.token_urlsafe(32)
        request('/api/auth/setup', {'username': username, 'password': password}, 201)
    else:
        username = os.environ.get('EVLET_USERNAME') or input('Username: ')
        password = os.environ.get('EVLET_PASSWORD') or getpass.getpass('Password: ')
    csrf = request('/api/auth/login', {'username': username, 'password': password})['csrf']
    schema = request("/api/schema")
    check(schema["version"] == 1 and schema["ui_version"] == 1, "Unexpected schema version")
    check({item["name"] for item in schema["models"]} == {"customer", "invoice", "payment", "project", "projecttask", "employee"}, "Missing Playground models")
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
    request("/api/invoice", {"customer": 2**63-1, "reference": f"ORPHAN-{suffix}", "total": "1.00"}, 404)
    updated = request(f'/api/invoice/{invoice["id"]}', {
        'total': '124.56', 'posting_date': '2026-10-28', 'status': 'sent', 'tax_rate': '5.1234',
    }, method='PATCH')
    check(updated['total'] == '124.56' and updated['tax_rate'] == '5.1234', 'Update lost precision')
    query = urlencode({'filters': json.dumps({'invoice': invoice['id']}), 'order_by': 'amount'})
    check(request('/api/payment?' + query)['items'][0]['id'] == payment['id'], 'Relationship query failed')
    project = request('/api/project', {'name': f'Project {suffix}', 'customer': customer['id'], 'budget': '5000.55'}, 201)
    check(project['status'] == 'planned' and project['progress'] == 0, 'Generated form defaults failed')
    view = next(model for model in schema['models'] if model['name'] == 'project')
    check(view['title_field'] == 'name' and 'budget' in view['list_fields'], 'Missing generated view metadata')
    project = request(f'/api/project/{project["id"]}', {'progress': 30, 'notes': 'Partial update'}, method='PATCH')
    check(project['budget'] == '5000.55' and project['progress'] == 30, 'Partial project update lost data')
    request(f'/api/project/{project["id"]}', {'progress': 101}, 422, 'PATCH')
    page = request('/api/project?' + urlencode({'q': suffix, 'filters': json.dumps({'progress__gte': 30}), 'limit': 1}))
    check(page['total'] == 1 and page['items'][0]['id'] == project['id'], 'Search, filter, or count failed')
    project = request(f'/api/project/{project["id"]}', {'children': {'tasks': [{'title': 'Verify', 'done': False}]}}, method='PATCH')
    request(f'/api/project/{project["id"]}/actions/start', {})
    request(f'/api/project/{project["id"]}/actions/complete', {}, 422)
    tasks = [{**row, 'done': True} for row in project['children']['tasks']]
    request(f'/api/project/{project["id"]}', {'children': {'tasks': tasks}}, method='PATCH')
    completed = request(f'/api/project/{project["id"]}/actions/complete', {})
    check(completed['status'] == 'done' and completed['progress'] == 100, 'Transactional action failed')
    attachment = request(f'/api/project/{project["id"]}/attachments', {'name': 'smoke.txt', 'content': base64.b64encode(b'private smoke file').decode()}, 201)
    check(request(f'/api/project/{project["id"]}/attachments')['items'][0]['id'] == attachment['id'], 'Attachment read failed')
    request(f'/api/project/{project["id"]}', expected=204, method='DELETE')
    request(f'/api/customer/{customer["id"]}', expected=409, method='DELETE')
    request(f'/api/payment/{payment["id"]}', expected=204, method='DELETE')
    request(f'/api/invoice/{invoice["id"]}', expected=204, method='DELETE')
    request(f'/api/customer/{customer["id"]}', expected=204, method='DELETE')
    request(f'/api/customer/{customer["id"]}', expected=404)
    request('/api/auth/logout', {}, 204)
    print("Live smoke passed: sign-in, children, actions, attachments, sign-out; generated metadata, Project CRUD, search/filter/count; updates, typed fields, relationship queries, protected deletes, cleanup; health, schema, Customer → Invoice → Payment, reads, pagination, validation, foreign keys")


if __name__ == "__main__":
    main()
