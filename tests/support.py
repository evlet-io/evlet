"""Plain helpers the tests share: an ASGI caller and a signed-in Playground client (fixtures are in conftest.py)."""

import asyncio
import json
import re

PASSWORD = 'a sufficiently long test password'


def http(app, method, path, data=None, *, cookie='', csrf='', headers=None, client='127.0.0.1'):
    async def run():
        route, _, query = path.partition('?')
        raw = json.dumps({} if data is None else data).encode()
        events = [{'type': 'http.request', 'body': raw}]
        sent = []
        async def receive():
            return events.pop(0) if events else {'type': 'http.disconnect'}
        async def send(event):
            sent.append(event)
        request_headers = {b'host': b'127.0.0.1:8000', b'content-type': b'application/json', b'x-evlet-request': b'1', b'cookie': cookie.encode(), b'x-csrf-token': csrf.encode()}
        request_headers.update(headers or {})
        await app({'type': 'http', 'method': method, 'path': route, 'query_string': query.encode(), 'headers': list(request_headers.items()), 'client': (client, 1234)}, receive, send)
        status, response_headers, content = sent[0]['status'], dict(sent[0]['headers']), sent[1]['body']
        payload = json.loads(content) if content and response_headers.get(b'content-type', b'').startswith(b'application/json') else content
        return status, payload, response_headers
    return asyncio.run(run())


def client(app, role='admin'):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': role, 'password': PASSWORD})
    assert status == 200, data
    if 'two_step' in data:
        # Instances require two-step sign-in for admin: set it up with the given key, then use backup codes.
        import base64
        from evlet.settings import check_totp, totp
        import time
        codes = app.__dict__.setdefault('_test_codes', {})
        if data['two_step'] == 'setup':
            raw = base64.b32decode(data['secret'] + '=' * (-len(data['secret']) % 8))
            code = totp(raw, int(time.time() // 30))
            assert check_totp(raw, code) is not None
        else:
            code = codes[role].pop()
        status, data, headers = http(app, 'POST', '/api/auth/login', {'ticket': data['ticket'], 'code': code})
        assert status == 200, data
        codes.setdefault(role, data.get('backup_codes', []))
    cookie = headers[b'set-cookie'].decode().split(';')[0]
    def call(method, path, data=None, **kwargs):
        # Playground projects are State documents (0.4.6): their writes carry the record's current ETag.
        record = re.match(r'(/api/project/[^/?]+)', path)
        if record and method != 'GET' and 'headers' not in kwargs:
            current = http(app, 'GET', record.group(1), None, cookie=cookie, csrf=data_csrf)
            if b'etag' in current[2]:
                kwargs['headers'] = {b'if-match': current[2][b'etag']}
        return http(app, method, path, data, cookie=cookie, csrf=data_csrf, **kwargs)
    data_csrf = data['csrf']
    call.cookie, call.csrf = cookie, data_csrf
    return call
