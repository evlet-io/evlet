"""Record the Playground's model schema and record contracts through real ASGI routes.

Writes tests/fixtures/ui-fixture.json and record-fixture.json; tests/test_ui_contracts.py compares.
Run from the repository root: .venv/bin/python -m scripts.generate_ui_fixtures
"""

import asyncio
import json
from pathlib import Path
import re
import tempfile

from evlet import Database, LocalAuth
from evlet.identity import sequential_ids
from evlet.audit import fixed_times
from examples.playground.app import build_app
from examples.playground.models import registry


async def collect(app):
    cookie, csrf = '', ''

    async def request(method, path, body=None):
        route, _, query = path.partition('?')
        headers = [(b'host', b'127.0.0.1:8000'), (b'content-type', b'application/json'),
                   (b'x-evlet-request', b'1'), (b'cookie', cookie.encode()),
                   (b'x-csrf-token', csrf.encode())]
        events = [{'type': 'http.request', 'body': json.dumps(body or {}).encode()}]
        sent = []
        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}
        async def send(message):
            sent.append(message)
        await app({'type': 'http', 'method': method, 'path': route,
                   'query_string': query.encode(), 'headers': headers,
                   'client': ('127.0.0.1', 10000), 'scheme': 'http'}, receive, send)
        payload = json.loads(b''.join(item.get('body', b'') for item in sent))
        if not 200 <= sent[0]['status'] < 300:
            raise AssertionError((method, path, sent[0]['status'], payload))
        return payload, dict(sent[0]['headers'])

    password = 'temporary-contract-fixture-password'
    await request('POST', '/api/auth/setup', {'username': 'owner', 'password': password})
    login, headers = await request('POST', '/api/auth/login', {'username': 'owner', 'password': password})
    cookie = headers[b'set-cookie'].decode().split(';', 1)[0]
    csrf = login['csrf']
    schema, _ = await request('GET', '/api/schema')
    await request('POST', '/api/customer', {'customer_name': 'Acme', 'email': 'hello@example.test'})
    created, _ = await request('POST', '/api/project', {
        'title': 'Website launch', 'customer': 1, 'budget': '1250.50',
        'children': {'tasks': [{'title': 'Prepare content'}, {'title': 'Review pages', 'done': True}]},
    })
    records, _ = await request('GET', '/api/project?limit=20&offset=0')
    detail, _ = await request('GET', f"/api/project/{created['id']}")
    events, _ = await request('GET', '/api/_events?cursor=0')
    # Cursors are opaque random tokens. Preserve their format, normalize only the value.
    assert re.fullmatch('[0-9a-f]{48}', events['cursor'])
    events['cursor'] = '0' * 48
    return {'ui-fixture.json': {'models': schema['models']},
            'record-fixture.json': {'list': records, 'detail': detail, 'events': events}}


def generate():
    with tempfile.TemporaryDirectory(prefix='evlet-ui-contract-') as directory, sequential_ids(), fixed_times():
        database = Database(Path(directory) / 'contracts.sqlite3', registry)
        auth = LocalAuth(database, origins=('http://127.0.0.1:8000',),
                         secure_cookies=False, allow_setup=True)
        app = build_app(database, auth)
        try:
            app.initialize()
            return asyncio.run(collect(app))
        finally:
            database.close()


def main():
    destination = Path(__file__).resolve().parents[1] / 'tests/fixtures'
    for name, value in generate().items():
        (destination / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print('Updated', name)


if __name__ == '__main__':
    main()
