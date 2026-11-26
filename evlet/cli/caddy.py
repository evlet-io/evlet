"""Caddy through its admin API, for a project with an endpoint (docs/project-endpoint.md#caddy).

The endpoint keeps two objects in Caddy's running config and touches nothing else:

- a route with `"@id": "evlet-<project id>"`, first in the routes of Caddy's HTTPS server, that sends each host of
  each instance to that instance's port on 127.0.0.1, with the same refusals as the `evlet proxy` Caddy file;
- a TLS automation policy with `"@id": "evlet-<project id>-tls"`, first in the policies, that gets their
  certificates on demand, so Caddy asks the endpoint's `/_/tls-allowed` before it requests one.

Each instance says which hosts it serves at `GET /_/hosts` on loopback, from the same route table that answers its
own `/_/tls-allowed`: the primary host, the sub-hosts of routes.py and the active hosts from data. That table is the
one source of truth; the endpoint keeps no copy of it, only the last list each running instance gave.

Caddy applies each load atomically and keeps the last good config when it refuses one, so one bad host never stops
it. A load Caddy refuses, or an admin API that does not answer, is logged and retried with backoff; the endpoint keeps
running and project_status shows the error. A project without an endpoint never talks to the admin API.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger('evlet.caddy')

DEFAULT_ADMIN = 'http://localhost:2019'
LOOPBACK_NAMES = ('localhost', '127.0.0.1', '::1')
POLL = 2.0  # seconds between looks at the instances' host lists
VERIFY = 10.0  # seconds between checks that Caddy still holds the route (a `caddy reload` drops it)
BACKOFF = (1, 2, 5, 10, 30, 60)
TIMEOUT = 5.0
HSTS = 'max-age=31536000'


def admin_address(settings):
    """The admin API URL the endpoint pushes to, or None for no Caddy: endpoint.json `caddy_admin`, which is a
    loopback http URL, or null to turn the push off. Left out, it is localhost:2019 when the endpoint has a public
    host and None in development."""
    value = settings.get('caddy_admin', DEFAULT_ADMIN if settings.get('host') else None)
    if value is None or value is False:
        return None
    parts = urllib.parse.urlsplit(value) if isinstance(value, str) else None
    if parts is None or parts.scheme != 'http' or parts.hostname not in LOOPBACK_NAMES or parts.path not in ('', '/') \
            or parts.query or parts.fragment or parts.username:
        raise ValueError('caddy_admin in .evlet/endpoint.json must be an http URL on this machine, such as '
                         f'{DEFAULT_ADMIN}, or null')
    return value.rstrip('/')


def ids(project_id):
    return f'evlet-{project_id}', f'evlet-{project_id}-tls'


# ---------- the objects pushed ----------

def _refuse(match):
    return {'match': [match], 'handle': [{'handler': 'static_response', 'status_code': 404}], 'terminal': True}


def instance_route(port, hosts):
    """One instance's route: its hosts to 127.0.0.1:port. hosts are the /_/hosts entries ({host, sub, public})."""
    from ..app import PUBLIC_PATHS
    from .proxy import PRIMARY_ONLY
    names = [item['host'] for item in hosts]
    inner = [{'handle': [{'handler': 'headers', 'response': {'set': {'Strict-Transport-Security': [HSTS]}}}]},
             # /_/ holds framework routes: the public ones pass, the rest (such as the TLS ask) are for loopback only.
             _refuse({'path': ['/_/*'], 'not': [{'path': list(PUBLIC_PATHS)}]})]
    refused = [item['host'] for item in hosts if item['sub'] and not item['public']]
    if refused:
        inner.append(_refuse({'host': refused, 'path_regexp': {'pattern': f'(?i)^/({"|".join(PRIMARY_ONLY)})(/|$)'}}))
    inner.append({'handle': [{'handler': 'reverse_proxy', 'upstreams': [{'dial': f'127.0.0.1:{port}'}]}]})
    return {'match': [{'host': names}], 'handle': [{'handler': 'subroute', 'routes': inner}], 'terminal': True}


def objects(project_id, instances):
    """(route, policy) for {name: (port, hosts)}, or (None, None) when no instance serves a host."""
    route_id, policy_id = ids(project_id)
    served = [(name, port, hosts) for name, (port, hosts) in sorted(instances.items()) if hosts]
    names = sorted({item['host'] for _, _, hosts in served for item in hosts})
    if not names:
        return None, None
    route = {'@id': route_id, 'match': [{'host': names}],
             'handle': [{'handler': 'subroute', 'routes': [instance_route(port, hosts) for _, port, hosts in served]}],
             'terminal': True}
    return route, {'@id': policy_id, 'subjects': names, 'on_demand': True}


# ---------- the admin API ----------

class AdminError(Exception):
    pass


class Admin:
    """A small client of Caddy's admin API (https://caddyserver.com/docs/api) on loopback, with urllib."""

    def __init__(self, address, timeout=TIMEOUT):
        self.address, self.timeout = address, timeout
        # Never through an HTTP proxy from the environment: the admin API is on this machine.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, value=None, missing=False):
        """(status, decoded body). Raises AdminError when Caddy does not answer or refuses with an error; with
        missing, a 4xx (a path whose parent is missing) answers (status, None) instead."""
        data = None if value is None else json.dumps(value).encode()
        request = urllib.request.Request(self.address + path, data=data, method=method,
                                         headers={'Content-Type': 'application/json'} if data is not None else {})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
        except (OSError, ValueError) as error:
            raise AdminError(f'Caddy admin API at {self.address} does not answer: {error}') from None
        try:
            decoded = json.loads(body) if body.strip() else None
        except ValueError:
            decoded = body.decode('utf-8', 'replace')
        if missing and 400 <= status < 500 or status == 404 and path.startswith('/id/'):
            return status, None
        if status >= 400:
            reason = decoded.get('error') if isinstance(decoded, dict) else decoded
            raise AdminError(f'Caddy refused {method} {path} ({status}): {reason or "no reason given"}')
        return status, decoded

    def get(self, path):
        return self.call('GET', path)[1]

    def has(self, object_id):
        return self.call('GET', '/id/' + object_id)[0] != 404

    def probe(self, path):
        """The value at a config path, or None when the path or one of its parents is missing."""
        return self.call('GET', path, missing=True)[1]

    def place(self, value, parent, key):
        """Insert value first in the array at parent/key, making the array and its missing parents when needed."""
        path = f'{parent}/{key}'
        if isinstance(self.probe(path), list):
            self.call('PUT', path + '/0', value)
            return
        parts, made = path.split('/'), [value]
        while len(parts) > 2:  # stop at /config
            *parts, last = parts
            container = '/'.join(parts)
            if isinstance(self.probe(container), dict):
                self.call('POST', f'{container}/{last}', made)  # POST sets a key of an object
                return
            made = {last: made}
        raise AdminError('Caddy has no config loaded. Load the base config first (evlet proxy --endpoint)')

    def https_server(self):
        servers = self.probe('/config/apps/http/servers')
        if not isinstance(servers, dict) or not servers:
            raise AdminError('Caddy has no HTTP server. Load the base config first (evlet proxy --endpoint)')
        for name, server in sorted(servers.items()):
            if any(str(listen).endswith(':443') for listen in (server or {}).get('listen', ())):
                return name
        raise AdminError('Caddy has no server that listens on :443. Load the base config first (evlet proxy --endpoint)')


def push(admin, project_id, route, policy, previous=None):
    """Make Caddy hold route and policy under their ids, or neither when route is None. previous is the policy
    pushed last: a host that leaves stays on demand until the route without it is loaded, so Caddy never manages it
    outside the ask."""
    route_id, policy_id = ids(project_id)
    if route is None:
        for object_id in (route_id, policy_id):
            if admin.has(object_id):
                admin.call('DELETE', '/id/' + object_id)
        return
    wider = policy
    if previous and set(previous['subjects']) - set(policy['subjects']):
        wider = {**policy, 'subjects': sorted({*policy['subjects'], *previous['subjects']})}
    server = admin.https_server()  # before anything changes: without one, nothing is pushed
    _set(admin, policy_id, wider, '/config/apps/tls/automation', 'policies')
    _set(admin, route_id, route, f'/config/apps/http/servers/{server}', 'routes')
    if wider is not policy:
        _set(admin, policy_id, policy, '/config/apps/tls/automation', 'policies')


def _set(admin, object_id, value, parent, key):
    if admin.has(object_id):
        admin.call('PATCH', '/id/' + object_id, value)
    else:
        admin.place(value, parent, key)


# ---------- the instances ----------

def _local(port, path, timeout=2.0):
    """(status, decoded JSON) of GET http://127.0.0.1:port/path, or (0, None) when the instance does not answer."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}{path}', timeout=timeout) as response:
            return response.status, json.loads(response.read() or b'null')
    except urllib.error.HTTPError as error:
        return error.code, None
    except (OSError, ValueError):
        return 0, None


def served_hosts(port):
    """The instance's /_/hosts list, or None when it does not answer."""
    status, value = _local(port, '/_/hosts')
    return value['hosts'] if status == 200 and isinstance(value, dict) else None


def instance_allows(port, domain):
    return _local(port, '/_/tls-allowed?' + urllib.parse.urlencode({'domain': domain}))[0] == 200


class CaddySync:
    """The endpoint's thread that keeps Caddy's routes in step with the instances' hosts."""

    def __init__(self, supervisor, project_id, address, *, poll=POLL, verify=VERIFY, backoff=BACKOFF):
        self.supervisor, self.project_id = supervisor, project_id
        self.admin = Admin(address)
        self.poll, self.verify, self.backoff = poll, verify, tuple(backoff)
        self.known = {}  # name: the hosts it served when last asked
        self.pushed = None  # (route, policy) Caddy holds
        self.failures = 0
        self.retry_at = 0.0
        self.verified_at = 0.0
        self.state = {'admin': address, 'state': 'starting', 'error': None, 'pushed_at': None, 'hosts': 0}
        self.stopping = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='evlet-caddy', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=10)

    def status(self):
        with self.lock:
            return dict(self.state)

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception:  # never let the thread die: the next tick tries again
                logger.exception('Caddy sync failed')
            self.stopping.wait(self.poll)

    def collect(self):
        """{name: (port, hosts)}: a running instance is asked; one that restarts keeps its last list; a stopped one
        has none, so Caddy stops routing to it."""
        for name, child in list(self.supervisor.children.items()):
            if not child.wanted:
                self.known.pop(name, None)
            elif child.state == 'running':
                hosts = served_hosts(child.port)
                if hosts is not None:
                    self.known[name] = hosts
        return {name: (self.supervisor.children[name].port, hosts) for name, hosts in self.known.items()
                if name in self.supervisor.children}

    def tick(self):
        route, policy = objects(self.project_id, self.collect())
        now = time.monotonic()
        changed = self.pushed is None or self.pushed != (route, policy)
        if self.failures and now < self.retry_at:
            return
        try:
            if not changed and now - self.verified_at >= self.verify:
                # Caddy dropped the route (a reload of its Caddyfile, a restart): push it again.
                changed = route is not None and not self.admin.has(ids(self.project_id)[0])
                self.verified_at = now
            if not changed:
                return
            previous = self.pushed[1] if self.pushed else None
            push(self.admin, self.project_id, route, policy, previous)
        except AdminError as error:
            pause = self.backoff[min(self.failures, len(self.backoff) - 1)]
            self.failures += 1
            self.retry_at = now + pause
            logger.warning('%s; trying again in %s s', error, pause)
            with self.lock:
                self.state.update(state='failed', error=f'{error}; trying again in {pause} s')
            return
        self.pushed, self.failures, self.verified_at = (route, policy), 0, now
        count = len(policy['subjects']) if policy else 0
        logger.info('Caddy routes %d host%s of this project', count, '' if count == 1 else 's')
        with self.lock:
            self.state.update(state='ok', error=None, pushed_at=int(time.time()), hosts=count)


# ---------- the one TLS ask ----------

def allowed(supervisor, own_host, domain):
    """Whether Caddy may get a certificate for domain: the endpoint's own host, or a host a running instance serves
    now, asked at that instance's /_/tls-allowed, so the answer is always its route table's."""
    if not domain:
        return False
    if own_host and domain == own_host:
        return True
    return any(child.state == 'running' and instance_allows(child.port, domain) for child in list(supervisor.children.values()))


# ---------- the one-time base config ----------

def base_config(endpoint_port, admin=DEFAULT_ADMIN):
    """The Caddyfile global options an endpoint needs once: the admin API on loopback, the one on-demand TLS ask,
    and strict SNI, as in the `evlet proxy` files."""
    listen = urllib.parse.urlsplit(admin).netloc
    return f'''# Evlet project endpoint: Caddy's global options, set once. The endpoint adds the instances' hosts itself
# through the admin API; this file never changes when a host or an instance does.
{{
\tadmin {listen}
\ton_demand_tls {{
\t\task http://127.0.0.1:{endpoint_port}/_/tls-allowed
\t}}
\tservers :443 {{
\t\tstrict_sni_host on
\t}}
}}
'''


def global_options(root):
    """The text of deploy/evlet-global.caddy: today's strict SNI block, or with an endpoint that pushes to Caddy,
    the base config above, which holds the same block. One file, so the Caddyfile never gets two global blocks."""
    from .endpoint import read_settings
    from .oplog import exists
    from .proxy import GLOBAL_OPTIONS
    if not exists(root):
        return GLOBAL_OPTIONS
    settings = read_settings(root)
    address = admin_address(settings)
    return GLOBAL_OPTIONS if address is None else base_config(settings['port'], address)
