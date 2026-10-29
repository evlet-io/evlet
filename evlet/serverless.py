"""Buffered AWS HTTP API v2 / Lambda URL transport for an explicit ASGI app.

This adapter supplies no database, filesystem, worker, or lifespan persistence.
The application must provide durable state independently of the Lambda process.
"""

import asyncio
import base64
import binascii
import ipaddress
import json
import re
from urllib.parse import unquote

from .tenancy import hostname


class LambdaHTTP:
    def __init__(self, app, *, domains):
        self.app = app
        self.domains = frozenset(hostname(value) for value in domains)
        if not self.domains:
            raise ValueError('Configure explicit gateway domain names')

    def __call__(self, event, context=None):
        try:
            scope, body = self._request(event)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, binascii.Error):
            return self._error(400, 'Invalid gateway request')
        if scope['server'][0] not in self.domains:
            return self._error(421, 'Unrecognized gateway domain')
        if scope['path'] == '/api/_events/stream':
            return self._error(501, 'Use the event polling endpoint on this transport')
        return asyncio.run(self._invoke(scope, body))

    @staticmethod
    def _error(status, message):
        return {'statusCode': status, 'headers': {'content-type': 'application/json', 'cache-control': 'no-store'}, 'body': json.dumps({'error': message}), 'isBase64Encoded': False}

    def _request(self, event):
        if event['version'] != '2.0':
            raise ValueError()
        context = event['requestContext']
        domain = hostname(context['domainName'])
        http = context['http']
        method = http['method']
        if not isinstance(method, str) or not re.fullmatch('[A-Z]{1,16}', method):
            raise ValueError()
        client = str(ipaddress.ip_address(http['sourceIp']))
        raw_path, query = event['rawPath'], event.get('rawQueryString', '')
        if not isinstance(raw_path, str) or not raw_path.startswith('/') or len(raw_path) > 8192 or not isinstance(query, str) or len(query) > 8192:
            raise ValueError()
        path = unquote(raw_path, errors='strict')
        if any(ord(char) < 32 for char in path) or '?' in raw_path or '#' in raw_path:
            raise ValueError()
        headers = {}
        for key, value in event.get('headers', {}).items():
            if not re.fullmatch("[!#$%&'*+.^_`|~0-9a-zA-Z-]+", key) or not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError()
            key = key.lower()
            if key in headers:
                raise ValueError()
            headers[key] = value
        if headers.get('host', domain).lower() not in (domain, domain + ':443'):
            raise ValueError()
        # Routing authority comes from the gateway context, never forwarded headers.
        headers['host'] = domain
        headers.pop('x-forwarded-host', None)
        headers.pop('x-forwarded-for', None)
        headers.pop('x-forwarded-proto', None)
        cookies = event.get('cookies', [])
        if not isinstance(cookies, list) or any(not isinstance(cookie, str) or any(ord(char) < 32 or ord(char) == 127 for char in cookie) for cookie in cookies):
            raise ValueError()
        if cookies:
            if 'cookie' in headers:
                raise ValueError()
            headers['cookie'] = '; '.join(cookies)
        if type(event.get('isBase64Encoded', False)) is not bool:
            raise ValueError()
        raw = event.get('body')
        if raw is None:
            raw = ''
        if not isinstance(raw, str) or len(raw) > 2000000:
            raise ValueError()
        body = base64.b64decode(raw, validate=True) if event.get('isBase64Encoded', False) else raw.encode('utf-8')
        if len(body) > 1500000:
            raise ValueError()
        headers.pop('transfer-encoding', None)
        headers.pop('connection', None)
        headers['content-length'] = str(len(body))
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'scheme': 'https',
                 'method': method, 'path': path, 'raw_path': raw_path.encode('ascii'), 'query_string': query.encode('ascii'),
                 'root_path': '', 'server': (domain, 443), 'client': (client, 0),
                 'headers': [(key.encode('ascii'), value.encode('latin1')) for key, value in headers.items()]}
        return scope, body

    async def _invoke(self, scope, body):
        sent = False
        response = None
        content = bytearray()
        ended = False
        disconnect = asyncio.Event()
        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            await disconnect.wait()
            return {'type': 'http.disconnect'}
        async def send(event):
            nonlocal response, ended
            if event['type'] == 'http.response.start':
                if response is not None:
                    raise ValueError('Response already started')
                response = event
            elif event['type'] == 'http.response.body':
                if response is None or ended:
                    raise ValueError('Invalid response sequence')
                content.extend(event.get('body', b''))
                if len(content) > 4000000:
                    raise ValueError('Buffered response exceeds 4 MB')
                ended = not event.get('more_body', False)
                if ended:
                    disconnect.set()
            else:
                raise ValueError('Unsupported ASGI response event')
        try:
            await self.app(scope, receive, send)
            if response is None or not ended:
                raise ValueError('Incomplete response')
        except Exception:
            return self._error(502, 'Application response unavailable')
        headers, cookies = {}, []
        for key, value in response.get('headers', []):
            key, value = key.decode('ascii').lower(), value.decode('latin1')
            if key == 'set-cookie':
                cookies.append(value)
            elif key not in ('content-length', 'transfer-encoding', 'connection'):
                headers[key] = headers[key] + ', ' + value if key in headers else value
        return {'statusCode': response['status'], 'headers': headers, 'cookies': cookies,
                'body': base64.b64encode(content).decode('ascii'), 'isBase64Encoded': True}
