"""Frontends, hosts and the frozen route table an instance resolves at start-up."""

from dataclasses import dataclass, field
import ipaddress
from pathlib import Path
import re
from types import BuiltinFunctionType, FunctionType, MappingProxyType, ModuleType
from urllib.parse import urlsplit

LOCKED, FRAMEWORK, PLANNED = 'locked', 'framework', 'reserved'
RESERVED = MappingProxyType({
    **dict.fromkeys(('controls', 'settings', 'api', 'app', 'assets', 'files', 'reports', 'login', 'logout',
                     'auth', 'setup', 'health', 'ws', '.well-known', 'form', 'kb'), LOCKED),
    'docs': FRAMEWORK,
    **dict.fromkeys(('report', 'dashboard', 'search', 'print'), PLANNED),
})
KINDS = ('evlet', 'static', 'server')
REALMS = ('desk', 'portal', None)
NAME = re.compile(r'[a-z][a-z0-9_]*')
PATH = re.compile(r'/[a-z0-9][a-z0-9_-]*')
HOSTNAME = re.compile(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?')
LOCAL = 'localhost'
# Record routes an ApiScope opens only when it lists them in extras.
SCOPE_EXTRAS = ('measures', 'related', 'lines', 'attachments', 'drafts')


def reserved(path):
    """Return the RESERVED status of a path's first segment, ignoring case."""
    segment = path.split('/')[1].lower() if path.startswith('/') else ''
    return LOCKED if segment.startswith('_') else RESERVED.get(segment)


def host_name(value):
    """The hostname of a Host header (host or host:port), or None unless it is plain, lowercase and valid."""
    if isinstance(value, bytes):
        value = value.decode('latin1')
    if not isinstance(value, str) or not value or value != value.lower() or any(not 32 < ord(char) < 127 for char in value):
        return None
    if value.startswith('['):
        name, bracket, rest = value[1:].partition(']')
        if not bracket or (rest and not rest.startswith(':')):
            return None
        port = rest[1:] if rest else None
        try:
            ipaddress.IPv6Address(name)
        except ValueError:
            return None
    else:
        name, colon, port = value.partition(':')
        port = port if colon else None
        if not HOSTNAME.fullmatch(name) or '..' in name:
            return None
    if port is not None and not (port.isdigit() and len(port) <= 5 and 0 < int(port) < 65536):
        return None
    return name


def authority(origin):
    """The Host header value an exact browser origin is served under."""
    return urlsplit(origin).netloc


def _strings(value, label):
    value = tuple(value)
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f'{label} must be a tuple of names')
    return value


@dataclass(frozen=True, eq=False)
class Frontend:
    name: str
    kind: str = 'evlet'
    path: str | None = None
    title: str = ''
    icon: str = ''
    auth: str | None = 'desk'
    roles: tuple = ()
    api: tuple = ()
    dist: str | None = None
    handler: object = None
    csp: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not NAME.fullmatch(self.name):
            raise ValueError(f'Frontend names are lowercase identifiers: {self.name!r}')
        if self.kind not in KINDS:
            raise ValueError(f'Frontend {self.name} kind must be one of: ' + ', '.join(KINDS))
        if not isinstance(self.path, str) or not PATH.fullmatch(self.path):
            raise ValueError(f'Frontend {self.name} needs a one-segment default path such as /{self.name.replace("_", "-")}')
        if self.auth not in REALMS:
            raise ValueError(f'Frontend {self.name} auth must be "desk", "portal" or None')
        object.__setattr__(self, 'roles', _strings(self.roles, 'roles'))
        if self.roles and self.auth is None:
            raise ValueError(f'Frontend {self.name}: roles need a sign-in realm; set auth="desk" or "portal"')
        object.__setattr__(self, 'api', _strings(self.api, 'api'))
        if (self.kind == 'static') != (self.dist is not None):
            raise ValueError(f'Frontend {self.name}: dist is required for static frontends and only allowed there')
        if self.dist is not None and (not isinstance(self.dist, str) or not self.dist or self.dist.startswith('/') or '\\' in self.dist or '..' in self.dist.split('/')):
            raise ValueError(f'Frontend {self.name} dist must be a relative folder inside the app package')
        if (self.kind == 'server') != (self.handler is not None) or (self.handler is not None and not callable(self.handler)):
            raise ValueError(f'Frontend {self.name}: a callable handler is required for server frontends and only allowed there')
        csp = dict(self.csp)
        if csp and (self.kind != 'static' or set(csp) != {'connect-src'}):
            raise ValueError(f'Frontend {self.name}: only static frontends may add connect-src hosts')
        for source in csp.get('connect-src', ()):
            if not isinstance(source, str) or not re.fullmatch(r'https://[a-z0-9.-]+(:[0-9]+)?', source):
                raise ValueError(f'Frontend {self.name}: connect-src takes https origins')
        object.__setattr__(self, 'csp', MappingProxyType({key: tuple(value) for key, value in csp.items()}))


@dataclass(frozen=True)
class Host:
    name: str
    root: Frontend | None = None
    also_on_primary: bool = False

    def __post_init__(self):
        if not isinstance(self.name, str) or not HOSTNAME.fullmatch(self.name) or '..' in self.name:
            raise ValueError(f'Host names are lowercase hostnames without a scheme, path or port: {self.name!r}')
        if self.root is not None and not isinstance(self.root, Frontend):
            raise ValueError(f'Host {self.name} root must be a Frontend')


@dataclass(frozen=True)
class Docs:
    enabled: bool = True
    path: str = '/docs'
    roles: tuple = ()

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ValueError('Docs enabled is True or False')
        object.__setattr__(self, 'roles', _strings(self.roles, 'Docs roles'))
        if not isinstance(self.path, str) or not PATH.fullmatch(self.path) or (self.path != '/docs' and reserved(self.path)):
            raise ValueError(f'Docs path must be one free segment such as /help: {self.path!r}')


@dataclass(frozen=True)
class ApiScope:
    """What a frontend may call from a sub-host: records of models, the named (Model, action) pairs (also
    moves, "void" and "revise"), service endpoints, and the extra record routes listed in extras."""
    name: str
    models: tuple = ()
    actions: tuple = ()
    endpoints: tuple = ()
    extras: tuple = ()

    def __post_init__(self):
        if not isinstance(self.name, str) or not NAME.fullmatch(self.name):
            raise ValueError(f'API scope names are lowercase identifiers: {self.name!r}')
        object.__setattr__(self, 'models', tuple(self.models))
        object.__setattr__(self, 'actions', tuple(self.actions))
        object.__setattr__(self, 'endpoints', _strings(self.endpoints, 'endpoints'))
        object.__setattr__(self, 'extras', _strings(self.extras, 'extras'))
        if set(self.extras) - set(SCOPE_EXTRAS):
            raise ValueError(f'API scope {self.name} extras are some of: ' + ', '.join(SCOPE_EXTRAS))
        if any(not isinstance(model, type) for model in self.models):
            raise ValueError(f'API scope {self.name} models must be Model classes')
        if any(not isinstance(item, tuple) or len(item) != 2 or item[0] not in self.models or not isinstance(item[1], str) for item in self.actions):
            raise ValueError(f'API scope {self.name} actions are (Model, "action") pairs of its own models')


@dataclass(frozen=True)
class ControlsAccess:
    allow_ips: tuple = ()
    require_two_step: bool = False

    def __post_init__(self):
        networks = []
        for value in self.allow_ips:
            try:
                networks.append(str(ipaddress.ip_network(value, strict=False)))
            except (TypeError, ValueError):
                raise ValueError(f'ControlsAccess allow_ips takes IP addresses and CIDR ranges: {value!r}') from None
        object.__setattr__(self, 'allow_ips', tuple(networks))
        if not isinstance(self.require_two_step, bool):
            raise ValueError('ControlsAccess require_two_step is True or False')


@dataclass(frozen=True)
class Request:
    """What a server frontend's handler receives. path is relative to the frontend's base."""
    path: str
    query: str
    host: str
    user: object
    params: dict


@dataclass(frozen=True)
class Response:
    body: str | bytes
    status: int = 200
    content_type: str = 'text/html; charset=utf-8'

    def __post_init__(self):
        if not isinstance(self.body, (str, bytes)) or type(self.status) is not int or not 200 <= self.status <= 599:
            raise ValueError('Response takes text or bytes and an HTTP status')
        if not isinstance(self.content_type, str) or not re.fullmatch(r'[a-z]+/[a-z0-9.+-]+(; ?charset=[a-z0-9-]+)?', self.content_type):
            raise ValueError('Response content_type must be a media type')


@dataclass(frozen=True)
class Route:
    host: str
    path: str
    serves: str
    realm: str
    source: str

    @property
    def locked(self):
        return self.source == LOCKED


_FRAMEWORK = (
    ('/controls', 'evlet.controls', 'control'),
    ('/settings', 'evlet.settings', 'desk'),
    ('/app', 'evlet.desk', 'desk'),
    ('/reports', 'evlet.reports', 'desk'),
    ('/login', 'evlet.login', 'public'),
    ('/assets', 'evlet.assets', 'public'),
    ('/health', 'evlet.health', 'public'),
    ('/api/*', 'evlet.api', 'desk'),
)
# Reserved first segments a sub-host's root frontend serves itself, such as a built app's assets.
SUB_HOST_OWNS = ('assets', 'login', 'logout')
_EMPTY = MappingProxyType({})


@dataclass(frozen=True)
class RouteTable:
    primary: str | None
    port: int
    docs: bool
    controls: ControlsAccess
    _rows: tuple
    _segments: MappingProxyType
    _root: str | None
    docs_path: str = '/docs'
    docs_roles: tuple = ()
    _subs: MappingProxyType = _EMPTY
    _frontends: MappingProxyType = _EMPTY
    _dists: MappingProxyType = _EMPTY
    _scopes: MappingProxyType = _EMPTY

    def rows(self):
        return self._rows

    def hosts(self):
        return ((self.primary,) if self.primary else ('127.0.0.1', LOCAL)) + tuple(self._subs)

    def sub_hosts(self):
        return tuple(self._subs)

    def origins(self):
        if self.primary:
            return tuple('https://' + host for host in self.hosts())
        return tuple(f'http://{host}:{self.port}' for host in self.hosts())

    def authorities(self):
        """The exact Host header values of the origins; LocalAuth accepts the same set."""
        return tuple(authority(origin) for origin in self.origins())

    def realm(self, host):
        """The sign-in realm of a host: desk on the primary host, the root frontend's on a sub-host."""
        root = self._subs.get(host_name(host))
        return 'desk' if root is None else self._frontends[root].auth or 'public'

    def realms(self):
        """Origins whose realm is not desk, for LocalAuth."""
        return {origin: self.realm(origin.split('//', 1)[1]) for origin in self.origins() if self.realm(origin.split('//', 1)[1]) != 'desk'}

    def frontend(self, identity):
        return self._frontends.get(identity)

    def dist(self, identity):
        return self._dists.get(identity)

    def scopes(self, host):
        """The API scopes a sub-host serves, or None on the primary host, which serves the whole API."""
        name = host_name(host)
        return self._scopes.get(name, ()) if name in self._subs else None

    def base(self, host, identity):
        """Where a frontend is mounted on a host, or None."""
        name = host_name(host)
        if name in self._subs:
            return '/' if self._subs[name] == identity else None
        return next((segment for segment, serves in self._segments.items() if serves == identity), None)

    def redirect(self, host, path):
        """The root redirect target for a host, or None when the host root serves a frontend."""
        return self._root if path == '/' and self._known(host) and host_name(host) not in self._subs else None

    def match(self, host, path):
        """Return (frontend id, base, rest) for the longest mounted base on a host, or None."""
        if not self._known(host) or not path.startswith('/'):
            return None
        root = self._subs.get(host_name(host))
        if root is not None:
            # Portal hosts serve the personal settings subset at /settings.
            if (path == '/settings' or path.startswith('/settings/')) and self.realm(host) == 'portal':
                return 'evlet.settings', '/settings', path.removeprefix('/settings')
            # Framework pages such as /controls and /docs live on the primary host only.
            if reserved(path) and path.split('/')[1].lower() not in SUB_HOST_OWNS:
                return None
            return root, '/', path
        segment = '/' + path.split('/')[1]
        serves = self._segments.get(segment)
        if serves is None:
            # Python page routes and the host root belong to the desk shell, mounted at /.
            return 'evlet.desk', '/', path
        return serves, segment, path[len(segment):]

    def _known(self, host):
        if host_name(host) is None:
            return False
        # Without a primary host the table serves any valid host the application's guard accepts.
        return self.primary is None or (host.decode('latin1') if isinstance(host, bytes) else host) in self.authorities()

    def text(self):
        header = ('HOST', 'PATH', 'SERVES', 'SIGN-IN', 'SOURCE')
        rows = [header, *((row.host, row.path, row.serves, row.realm, row.source) for row in self._rows)]
        widths = [max(len(row[index]) for row in rows) + 3 for index in range(4)]
        return '\n'.join(''.join(value.ljust(width) for value, width in zip(row, widths)) + row[4] for row in rows)


def _views(definition):
    views = list(definition.views)
    if definition.ui is not None:
        views.extend(definition.ui.views.values())
    return [view for view in views if getattr(view, 'route', None)]


ROUTES_NAMES = {'primary': (Host, 'Host("erp.example.com")'), 'hosts': (tuple, 'a tuple of Host(...)'),
                'paths': (dict, 'a dict of {frontend: "/path"}'), 'docs': (Docs, 'Docs(...)'),
                'controls': (ControlsAccess, 'ControlsAccess(...)')}


def _routes_names(namespace, where):
    """The known routes.py names, type-checked. Imports and _private names are skipped; anything else fails."""
    found = {}
    for name, value in dict(namespace).items():
        if name.startswith('_') or isinstance(value, (ModuleType, type, Frontend, FunctionType, BuiltinFunctionType)):
            continue
        if name not in ROUTES_NAMES:
            raise ValueError(f'{where}: unknown name {name!r}. Use primary, hosts, paths, docs or controls.')
        kind, shape = ROUTES_NAMES[name]
        if not isinstance(value, kind):
            raise ValueError(f'{where}: {name} must be {shape}')
        found[name] = value
    if any(not isinstance(host, Host) for host in found.get('hosts', ())):
        raise ValueError(f'{where}: hosts must be {ROUTES_NAMES["hosts"][1]}')
    return found


def resolve_routes(definitions=(), instance_config=None, routes_module=None, *, port=8000, roots=None):
    """Resolve framework, app and routes.py entries into one frozen RouteTable; fail on any clash.

    roots maps an app name to its package folder, which holds the dist folders of its static frontends.
    """
    where = f'instances/{instance_config.name}/routes.py' if instance_config else 'routes.py'
    routes = _routes_names(routes_module or {}, where)
    primary, sub_hosts = routes.get('primary'), routes.get('hosts', ())
    if primary is not None and primary.also_on_primary:
        raise ValueError(f'{where}: primary is the primary host; also_on_primary is for sub-hosts')
    domain = getattr(instance_config, 'domain', None)
    if domain and primary and primary.name != domain:
        raise ValueError(f'config.py sets domain {domain} but {where} sets primary {primary.name}. Use one name.')
    host = primary.name if primary else domain
    label = host or LOCAL
    docs = routes.get('docs')
    docs_on = docs.enabled if docs else host is None
    docs_path = docs.path if docs else '/docs'
    controls = routes.get('controls', ControlsAccess())

    # 1. Framework rows.
    rows, owners, segments = [], {}, {}
    for path, serves, realm in _FRAMEWORK:
        rows.append(Route(label, path, serves, realm, LOCKED))
        if not path.startswith('/api'):
            segments[path] = serves

    def claim(path, owner, hint, page=False):
        key = path.split('/')[1].lower()
        if reserved(path) and not (owner == 'evlet.docs' and key == 'docs'):
            raise ValueError(f'{owner} wants {path} on {label}, which is reserved for the framework. {hint}')
        if key in owners and not (page and owners[key][1]):
            raise ValueError(f'{owners[key][0]} and {owner} both want /{key} on {label}. Move one in {where}.')
        owners.setdefault(key, (owner, page))

    if docs_on:
        claim(docs_path, 'evlet.docs', f'Choose another path in {where}.')
        rows.insert(3, Route(label, docs_path, 'evlet.docs', 'desk', FRAMEWORK if docs is None else 'routes.py'))
        segments[docs_path] = 'evlet.docs'

    # 2. App frontends at their default paths, in apps.lock order.
    frontends, scopes, dists, moves = {}, {}, {}, dict(routes.get('paths') or {})
    for app, definition in definitions:
        for scope in getattr(definition, 'api_scopes', ()):
            if f'{app}.{scope.name}' in scopes:
                raise ValueError(f'API scope {app}.{scope.name} is registered twice')
            scopes[f'{app}.{scope.name}'] = scope
        for frontend in getattr(definition, 'frontends', ()):
            identity = f'{app}.{frontend.name}'
            if frontend in frontends or identity in frontends.values():
                raise ValueError(f'Frontend {identity} is registered twice')
            frontends[frontend] = identity
            if frontend.kind == 'static' and roots and app in roots:
                folder = (Path(roots[app]) / frontend.dist).resolve()
                if not folder.is_relative_to(Path(roots[app]).resolve()):
                    raise ValueError(f'Frontend {identity} dist must stay inside the app package')
                dists[identity] = folder
    for frontend, identity in frontends.items():
        for name in frontend.api:
            if name not in scopes:
                raise ValueError(f'Frontend {identity} uses API scope {name}, which no installed app defines')
    # 3. Moves and hosts from routes.py.
    for frontend, path in moves.items():
        if frontend not in frontends:
            raise ValueError(f'{where} moves {getattr(frontend, "name", frontend)!r}, which no installed app registers')
        if not isinstance(path, str) or not PATH.fullmatch(path):
            raise ValueError(f'{where}: move {frontends[frontend]} to a one-segment path such as /sales')
    subs, off_primary = {}, set()
    for sub in sub_hosts:
        if sub.name == host or sub.name in subs:
            raise ValueError(f'{where}: {sub.name} is listed twice')
        if (host is None) != sub.name.endswith('.localhost'):
            raise ValueError(f'{where}: sub-host {sub.name} must be a .localhost name in development and a public name with a primary host')
        if sub.root is None or sub.root not in frontends:
            raise ValueError(f'{where}: sub-host {sub.name} needs root=, a frontend of an installed app')
        if sub.root.kind == 'evlet':
            raise ValueError(f'{where}: sub-host {sub.name} can serve static and server frontends; evlet frontends stay on the primary host')
        subs[sub.name] = frontends[sub.root]
        if not sub.also_on_primary:
            off_primary.add(sub.root)
    # 4. Clash check across the table.
    for frontend, identity in frontends.items():
        if frontend in off_primary:
            continue
        path = moves.get(frontend, frontend.path)
        claim(path, identity, f'Move it in {where}.')
        segments['/' + path.split('/')[1]] = identity
        rows.append(Route(label, path, identity, frontend.auth or 'public', 'routes.py' if frontend in moves else 'app ' + identity.split('.')[0]))
    for app, definition in definitions:
        for view in _views(definition):
            first = view.route.split('/')[1] if view.route.startswith('/') else ''
            if not first or first.startswith('{'):
                raise ValueError(f'{app} page {view.name} route {view.route} must start with a fixed segment')
            claim(view.route, f'{app} page {view.name}', f'Change the route in app {app}.', page=True)
            rows.append(Route(label, view.route, 'evlet.desk', 'desk', 'app ' + app))
    root = None
    if primary and primary.root is not None:
        if primary.root not in frontends or primary.root in off_primary:
            raise ValueError(f'{where}: the primary root is not a frontend of an installed app on the primary host')
        root = moves.get(primary.root, primary.root.path)
        rows.append(Route(label, '/', '-> ' + root, primary.root.auth or 'public', 'routes.py'))
    else:
        rows.append(Route(label, '/', 'evlet.desk', 'desk', FRAMEWORK))
    by_id = {identity: frontend for frontend, identity in frontends.items()}
    for name, identity in subs.items():
        served = by_id[identity]
        realm = served.auth or 'public'
        rows.append(Route(name, '/', identity, realm, 'routes.py'))
        rows.append(Route(name, '/api/*', ', '.join(served.api) + ' (scoped)' if served.api else 'sign-in only', realm, 'routes.py'))
    # 5. Freeze.
    return RouteTable(host, port, docs_on, controls, tuple(rows), MappingProxyType(segments), root, docs_path,
                      docs.roles if docs else (), MappingProxyType(subs), MappingProxyType(by_id), MappingProxyType(dists),
                      MappingProxyType({name: tuple(scopes[item] for item in by_id[identity].api) for name, identity in subs.items()}))
