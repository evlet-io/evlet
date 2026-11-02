"""A bounded HTTP client pinned to one application's authenticated session."""

import json
import os
import re
import stat
from http.cookies import SimpleCookie, CookieError
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

MAX_RESPONSE = 2 * 1024 * 1024


class ConnectorError(Exception):
    def __init__(self, status, message, fields=None):
        super().__init__(message)
        self.status, self.message, self.fields = status, message, fields

    def public(self):
        return {'status': self.status, 'error': self.message, **({'fields': self.fields} if self.fields else {})}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def origin(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError('Use an explicit HTTP loopback or HTTPS application origin')
    parsed = urlsplit(value)
    host, port = parsed.hostname, parsed.port
    if parsed.scheme not in ('http', 'https') or not host or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Use an application origin without credentials, path, query, or fragment')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or any(not part or part.startswith('-') or part.endswith('-') for part in host.split('.')):
        raise ValueError('Use a DNS hostname or IPv4 loopback address')
    local = host in ('localhost', '127.0.0.1') or host.endswith('.localhost')
    if parsed.scheme == 'http' and not local:
        raise ValueError('Plain HTTP is limited to loopback applications')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid application port')
    authority = host + (f':{port}' if port is not None else '')
    return parsed.scheme + '://' + authority


def strict_json(raw):
    def constant(value):
        raise ValueError('Non-finite JSON value')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Repeated JSON key')
            result[key] = value
        return result
    return json.loads(raw, parse_constant=constant, object_pairs_hook=pairs)


class HTTPClient:
    def __init__(self, url, *, cookie='', csrf=''):
        self.url = origin(url)
        if not isinstance(cookie, str) or (cookie and not re.fullmatch(r'[A-Za-z0-9_-]+=[A-Za-z0-9_-]{20,128}', cookie)):
            raise ValueError('Invalid application session cookie')
        if not isinstance(csrf, str) or (csrf and not re.fullmatch('[0-9a-f]{64}', csrf)):
            raise ValueError('Invalid session CSRF token')
        self.cookie, self.csrf = cookie, csrf
        # Set by MCPServer: the application then applies the workspace's AI assistant settings.
        self.assistant = False
        parsed = urlsplit(self.url)
        # .localhost is loopback by contract, without depending on system DNS.
        self.connection_url = self.url if parsed.scheme == 'https' else 'http://127.0.0.1' + (f':{parsed.port}' if parsed.port else '')
        self.authority = parsed.netloc
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None):
        if method not in ('GET', 'POST', 'PATCH', 'DELETE') or not isinstance(path, str) or not path.startswith('/api/') or any(ord(c) < 33 or ord(c) == 127 for c in path) or '#' in path:
            raise ValueError('Use a bounded application API request')
        encoded = None if body is None else json.dumps(body, allow_nan=False, separators=(',', ':')).encode()
        if encoded is not None and len(encoded) > 65536:
            raise ConnectorError(413, 'Request exceeds 64 KiB')
        headers = {'Host': self.authority, 'Origin': self.url, 'Accept': 'application/json', 'User-Agent': 'evlet-connector'}
        if self.cookie:
            headers['Cookie'] = self.cookie
        if self.assistant:
            headers['X-Evlet-Client'] = 'mcp'
        if method != 'GET':
            headers.update({'X-Evlet-Request': '1', 'X-CSRF-Token': self.csrf, 'Content-Type': 'application/json'})
        request = Request(self.connection_url + path, data=encoded, method=method, headers=headers)
        try:
            try:
                response = self.opener.open(request, timeout=10)
            except HTTPError as exc:
                response = exc
            with response:
                status, response_headers = response.status, response.headers
                if 300 <= status < 400:
                    raise ConnectorError(status, 'Application redirects are not followed')
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ConnectorError(502, 'Application response exceeds 2 MiB; request a smaller page')
                if status == 204:
                    return None, response_headers
                if response_headers.get_content_type() != 'application/json':
                    raise ConnectorError(status if status >= 400 else 502, 'Application returned an unreadable response')
                try:
                    data = strict_json(raw)
                except (ValueError, UnicodeError, RecursionError):
                    raise ConnectorError(502, 'Application returned invalid JSON') from None
                if not isinstance(data, dict):
                    raise ConnectorError(502, 'Application returned an invalid result')
                if status >= 400:
                    message = data.get('error') if isinstance(data.get('error'), str) else 'Application request failed'
                    fields = data.get('fields') if isinstance(data.get('fields'), dict) else None
                    raise ConnectorError(status, message, fields)
                return data, response_headers
        except (OSError, URLError, TimeoutError, HTTPException):
            raise ConnectorError(503, 'Application is unavailable. Reconnect before retrying; writes are never retried automatically.') from None

    def get(self, path):
        return self.request('GET', path)[0]

    def login(self, username, password, code=None):
        """Sign in; code() supplies the authenticator or backup code when the account uses two-step sign-in."""
        data, headers = self.request('POST', '/api/auth/login', {'username': username, 'password': password})
        if data.get('two_step') == 'setup':
            raise ConnectorError(403, 'Set up two-step sign-in in the browser first, then sign in here again.')
        if data.get('two_step') == 'code':
            if code is None:
                raise ConnectorError(403, 'This account uses two-step sign-in. Sign in from a terminal to enter the code.')
            data, headers = self.request('POST', '/api/auth/login', {'ticket': data['ticket'], 'code': code()})
        cookies = SimpleCookie()
        try:
            for value in headers.get_all('Set-Cookie', []):
                cookies.load(value)
        except CookieError:
            raise ConnectorError(502, 'Application returned an invalid session cookie') from None
        candidates = [item for item in cookies.values() if item['httponly'] and item['path'] == '/' and not item['domain']]
        if len(candidates) != 1 or (urlsplit(self.url).scheme == 'https' and not candidates[0]['secure']):
            raise ConnectorError(502, 'Application did not issue a host-only session cookie')
        client = HTTPClient(self.url, cookie=f'{candidates[0].key}={candidates[0].value}', csrf=data.get('csrf', ''))
        if not client.csrf:
            raise ConnectorError(502, 'Application did not issue a session CSRF token')
        return client

    def save(self, path):
        """Create a private session file once; never store the password."""
        data = json.dumps({'version': 1, 'url': self.url, 'cookie': self.cookie, 'csrf': self.csrf}).encode()
        if not self.cookie or not self.csrf:
            raise ValueError('Sign in before saving a session')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)

    @classmethod
    def load(cls, path):
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
                raise ValueError('Session file must be owned by the current user and have mode 0600')
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError('Session file is too large')
        data = strict_json(raw)
        if not isinstance(data, dict) or set(data) != {'version', 'url', 'cookie', 'csrf'} or data['version'] != 1:
            raise ValueError('Invalid session file')
        client = cls(data['url'], cookie=data['cookie'], csrf=data['csrf'])
        if not client.cookie or not client.csrf:
            raise ValueError('Session file has no credentials')
        return client
