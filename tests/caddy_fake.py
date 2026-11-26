"""A small stand-in for Caddy's admin API (https://caddyserver.com/docs/api): a real HTTP server on loopback holding
a JSON config tree, with the /config/<path> and /id/<@id> traversal rules of Caddy's own admin API, recording every
request. refusing makes every write answer 400, as Caddy does when it cannot load a config."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def find(tree, object_id, path=()):
    """The path of the object whose "@id" is object_id, or None."""
    if isinstance(tree, dict):
        if tree.get('@id') == object_id:
            return path
        items = tree.items()
    elif isinstance(tree, list):
        items = enumerate(tree)
    else:
        return None
    for key, value in items:
        found = find(value, object_id, (*path, key))
        if found is not None:
            return found
    return None


class FakeCaddy:
    def __init__(self, config):
        self.config = {'apps': config} if 'apps' not in config else config
        self.requests = []  # (method, path, body)
        self.refusing = False
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *arguments):
                pass

            def handle_any(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                status, answer = fake.handle(self.command, self.path, body)
                data = b'' if answer is None and status == 200 and self.command != 'GET' else json.dumps(answer).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://localhost:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def writes(self):
        with self.lock:
            return [request for request in self.requests if request[0] != 'GET']

    def object(self, object_id):
        with self.lock:
            path = find(self.config, object_id)
            return None if path is None else self._at(path)

    def _at(self, path):
        value = self.config
        for part in path:
            value = value[part]
        return value

    def handle(self, method, path, body):
        with self.lock:
            self.requests.append((method, path, body))
            try:
                if path.startswith('/id/'):
                    found = find(self.config, path[4:].split('/')[0])
                    if found is None:
                        raise Refused(404, f'unknown object ID {path[4:]!r}')
                    parts = list(found) + [part for part in path[4:].split('/')[1:] if part]
                elif path.startswith('/config'):
                    parts = [part for part in path[len('/config'):].split('/') if part]
                else:
                    raise Refused(404, 'not found')
                if method != 'GET' and self.refusing:
                    raise Refused(400, 'loading new config: http app module: start: listening on :443: address already in use')
                return 200, self.apply(method, parts, body)
            except Refused as error:
                return error.status, {'error': str(error)}

    def apply(self, method, parts, body):
        parent, value = None, self.config
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            if isinstance(value, list):
                try:
                    part = int(part)
                except ValueError:
                    raise Refused(400, f'invalid array index {part!r}') from None
            elif not isinstance(value, dict):
                raise Refused(400, f'invalid traversal path at: {"/".join(map(str, parts[:index + 1]))}')
            if last:
                return self.change(method, value, part, body)
            parent, value = value, (value[part] if isinstance(value, list) or part in value else None)
            if value is None:
                raise Refused(400, f'invalid traversal path at: {"/".join(map(str, parts[:index + 1]))}')
        if method == 'GET':
            return self.config
        raise Refused(400, 'unsupported on the root')

    @staticmethod
    def change(method, container, key, body):
        if method == 'GET':
            return container[key] if isinstance(container, list) else container.get(key)
        if isinstance(container, list):
            if method == 'PUT':
                container.insert(key, body)
            elif method == 'PATCH':
                container[key] = body
            elif method == 'DELETE':
                del container[key]
            elif method == 'POST' and isinstance(container[key], list):
                container[key].append(body)
            else:
                container[key] = body
            return None
        if method == 'POST':
            if isinstance(container.get(key), list):
                container[key].append(body)
            else:
                container[key] = body
        elif method == 'PUT':
            if key in container:
                raise Refused(409, f'[{key}] key already exists')
            container[key] = body
        elif method == 'PATCH':
            if key not in container:
                raise Refused(404, f'[{key}] key does not exist')
            container[key] = body
        elif method == 'DELETE':
            if key not in container:
                raise Refused(404, f'[{key}] key does not exist')
            del container[key]
        return None


def caddyfile_config(endpoint_host='project.example.com', endpoint_port=7999):
    """What Caddy holds after loading a Caddyfile with the endpoint's site and its global options: one :443 server
    with the endpoint's route, and the on-demand permission but no automation policies yet."""
    return {'apps': {
        'http': {'servers': {'srv0': {'listen': [':443'], 'routes': [
            {'match': [{'host': [endpoint_host]}], 'handle': [{'handler': 'subroute', 'routes': [
                {'handle': [{'handler': 'reverse_proxy', 'upstreams': [{'dial': f'127.0.0.1:{endpoint_port}'}]}]}]}],
             'terminal': True}]}}},
        'tls': {'automation': {'on_demand': {'permission': {'module': 'http', 'endpoint': f'http://127.0.0.1:{endpoint_port}/_/tls-allowed'}}}},
    }}
