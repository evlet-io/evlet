import base64
import copy
import json

import pytest

from evlet import LambdaHTTP
from test_business import secured, hashed, PASSWORD


def event(path='/api/auth/session', method='GET', body=None):
    return {'version': '2.0', 'rawPath': path, 'rawQueryString': '', 'headers': {'host': '127.0.0.1:8000'},
            'requestContext': {'domainName': 'app.example.test', 'http': {'method': method, 'sourceIp': '203.0.113.7'}},
            'body': None if body is None else json.dumps(body), 'isBase64Encoded': False}


def decoded(response):
    raw = base64.b64decode(response['body']) if response.get('isBase64Encoded') else response['body'].encode()
    return json.loads(raw) if response['headers'].get('content-type', '').startswith('application/json') else raw


def test_transport_preserves_body_query_cookies_and_gateway_authority():
    seen = []
    async def app(scope, receive, send):
        seen.append((scope, await receive()))
        await send({'type': 'http.response.start', 'status': 201, 'headers': [(b'set-cookie', b'a=1; Secure'), (b'set-cookie', b'b=2; Secure'), (b'content-type', b'application/octet-stream')]})
        await send({'type': 'http.response.body', 'body': b'\x00\xff\x01'})
    adapter = LambdaHTTP(app, domains=('app.example.test',))
    request = event('/api/file%20name', 'POST')
    request.update(headers={'Host': 'app.example.test', 'x-forwarded-host': 'evil.test', 'x-forwarded-for': '1.2.3.4'}, cookies=['a=one', 'b=two'], rawQueryString='q=a%26b', body=base64.b64encode(b'\x00\xff').decode(), isBase64Encoded=True)
    response = adapter(request)
    assert response['statusCode'] == 201 and decoded(response) == b'\x00\xff\x01'
    assert response['cookies'] == ['a=1; Secure', 'b=2; Secure']
    scope, message = seen[0]
    assert scope['path'] == '/api/file name' and scope['query_string'] == b'q=a%26b'
    assert scope['scheme'] == 'https' and scope['client'][0] == '203.0.113.7'
    assert dict(scope['headers'])[b'cookie'] == b'a=one; b=two'
    assert b'x-forwarded-host' not in dict(scope['headers']) and message['body'] == b'\x00\xff'


@pytest.mark.parametrize('change', [
    {'version': '1.0'}, {'headers': {'host': 'evil.test'}}, {'headers': {'host': 'app.example.test', 'Host': 'app.example.test'}},
    {'headers': {'host': 'app.example.test', 'x-test': 'bad\nvalue'}}, {'headers': None}, {'body': 0},
    {'body': 'not base64', 'isBase64Encoded': True}, {'cookies': ['bad\nvalue']}, {'rawPath': 'relative'},
    {'rawPath': '/bad%00'}, {'body': 'x' * 2000001}, {'isBase64Encoded': 'true'},
])
def test_malformed_gateway_input_never_reaches_app(change):
    called = []
    async def app(*args): called.append(True)
    request = event(); request['headers'] = {'host': 'app.example.test'}; request.update(change)
    assert LambdaHTTP(app, domains=('app.example.test',))(request)['statusCode'] == 400
    assert called == []


def test_unallowed_domain_and_sse_have_explicit_responses():
    async def app(*args): raise AssertionError('must not dispatch')
    adapter = LambdaHTTP(app, domains=('other.example.test',))
    request = event(); request['headers'] = {'host': 'app.example.test'}
    assert adapter(request)['statusCode'] == 421
    request['rawPath'] = '/api/_events/stream'
    assert LambdaHTTP(app, domains=('app.example.test',))(request)['statusCode'] == 501


def test_initialized_application_sessions_work_across_adapter_instances(secured):
    auth = secured.business.auth
    auth.origins = frozenset(('https://app.example.test',)); auth.hosts = {b'app.example.test'}
    auth.secure_cookies = True; auth.allow_setup = False; auth.cookie_name = '__Host-evlet_session'
    request = event('/api/auth/login', 'POST', {'username': 'admin', 'password': PASSWORD})
    request['headers'] = {'host': 'app.example.test', 'content-type': 'application/json', 'x-evlet-request': '1', 'origin': 'https://app.example.test'}
    response = LambdaHTTP(secured, domains=('app.example.test',))(request)
    assert response['statusCode'] == 200
    cookie = response['cookies'][0]; assert cookie.startswith('__Host-evlet_session=') and '; Secure' in cookie
    request = event('/api/schema'); request['headers'] = {'host': 'app.example.test'}; request['cookies'] = [cookie.split(';')[0]]
    assert LambdaHTTP(secured, domains=('app.example.test',))(request)['statusCode'] == 200


def test_invalid_or_oversized_asgi_response_is_not_returned():
    async def app(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200})
        await send({'type': 'http.response.body', 'body': b'x' * 4000001})
    request = event(); request['headers'] = {'host': 'app.example.test'}
    assert LambdaHTTP(app, domains=('app.example.test',))(request)['statusCode'] == 502
