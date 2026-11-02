"""Browser routes and UI JSON responses, separate from record dispatch."""

import ipaddress
import re
from urllib.parse import parse_qs, parse_qsl, quote

from ..business import Download


def dispatch(application, scope):
    """Return a UI response or None when the core application owns the route."""
    from ..app import HTTPError

    path, method = scope['path'], scope['method']
    host = dict(scope.get('headers', [])).get(b'host', b'')
    if path == '/_/tls-allowed':
        return _tls_allowed(application, scope)
    if path.startswith('/assets/apps/') and method == 'GET' and application.business and application.routes.scopes(host) is None:
        application.business.auth.guard(scope)
        user, _ = application.business.auth.session(scope)
        from ..native import serve
        response = serve(application.business, path, user)
        if response is None: raise HTTPError(404, 'Native asset is unavailable')
        return response
    if path in ('/api/ui', '/api/ui/resolve'):
        application._method(method, ['GET'])
        user = None
        if application.business:
            application.business.auth.guard(scope)
            user, _ = application.business.auth.session(scope)
            schema = application.business.schema(user)
        else:
            schema = application.registry.ui_schema()
        if path == '/api/ui':
            return 200, {'name': application.name, **application.ui.compile(schema, user)}, []
        try:
            query = parse_qs(scope.get('query_string', b'').decode('ascii'), strict_parsing=True, max_num_fields=2)
            if set(query) - {'frontend'} != {'path'} or any(len(values) != 1 for values in query.values()): raise ValueError()
            frontend = query.get('frontend', ['evlet.desk'])[0]
            base = application.routes.base(host, frontend)
            if base is None or frontend != 'evlet.desk' and application.routes.frontend(frontend).kind != 'evlet':
                raise LookupError()
            return 200, application.ui.resolve(query['path'][0], schema, user, frontend=frontend, base=base), []
        except (ValueError, UnicodeError):
            raise HTTPError(422, 'Supply one valid view path')
        except LookupError:
            raise HTTPError(404, 'View not found or not permitted')
    if not path.startswith('/api/') and method == 'GET':
        if application.business: application.business.auth.guard(scope)
        from .serving import static_response
        target = application.routes.redirect(host, path)
        if target:
            return 302, Download(b''), [(b'location', target.encode())]
        found = application.routes.match(host, path)
        if found is None: raise HTTPError(404, 'Route not found')
        if path == '/settings/accounts' and application.routes.scopes(host) is None:
            return 301, Download(b''), [(b'location', b'/controls/users')]
        if not found[0].startswith('evlet.'):
            return _frontend(application, scope, host, *found)
        if found[0] in ('evlet.controls', 'evlet.settings') and application.business:
            served = (application.business.controls if found[0] == 'evlet.controls' else application.business.settings).page(scope, path)
            if not isinstance(served, bool): return served
        else:
            served = _serves(application, *found)
        if not served and path.startswith('/assets/'): raise HTTPError(404, 'Route not found')
        if served and found[0] == 'evlet.docs' and application.routes.docs_roles:
            _docs_access(application, scope)
        response = static_response(path if served else '/', _config(application, scope, host, 'evlet.settings' if found[0] == 'evlet.settings' else 'evlet.desk', '/app'))
        # Unknown browser paths get the app's own not-found page, with a 404 status.
        if response is not None: return response if served else (404, *response[1:])
        if not served: raise HTTPError(404, 'Route not found')
        raise HTTPError(404, 'UI assets are not built. Run the frontend production build.')
    return None


GENERATED = re.compile(r'/app/([a-z][a-z0-9_]*)(?:/(new|[1-9][0-9]{0,15})(/view)?)?')
MODEL_PATH = re.compile(r'(?:/([a-z][a-z0-9_]*)(?:/(new|[1-9][0-9]{0,15})(/view)?)?)?/?')


def _serves(application, frontend, base, rest):
    """Whether a matched framework frontend serves the browser shell at this path."""
    from .registry import route_pattern
    path = base.rstrip('/') + rest
    if frontend == 'evlet.desk':
        # Eva's screens (/eva, /eva/tasks), only where config.py turned Eva on; the page asks /api/_eva who may use it.
        if base == '/' and (path == '/eva' or path.startswith('/eva/')) and application.business and application.business.eva is not None:
            return re.fullmatch(r'/eva(/tasks|/[1-9][0-9]{0,15})?', path) is not None
        if base == '/app':
            match = GENERATED.fullmatch(path)
            return bool(match) and match[1] in application.registry.models
        return path == '/' or any(view.route and route_pattern(view.route).fullmatch(path) for view in application.ui.views.values())
    if frontend == 'evlet.login':
        return path == '/login'
    if frontend == 'evlet.docs':
        return rest == '' or rest.startswith('/')
    if frontend == 'evlet.assets':
        return rest.startswith('/')
    if frontend == 'evlet.reports':
        return bool(application.business) and rest.startswith('/') and rest[1:] in application.business.forms.reports
    return False


def _config(application, scope, host, frontend, base):
    """The page config block. realm is the host's sign-in realm, which decides the session cookie."""
    from ..routing import host_name
    routes = application.routes
    name = host_name(host) or ''
    if application.business:
        docs = application.business.docs_path(scope, _user(application, scope) if routes.docs_roles else None)
    else:
        docs = routes.docs_path if routes.docs and name not in routes.sub_hosts() else None
    return {'frontend': frontend, 'base': base, 'api': '/api', 'realm': routes.realm(host), 'host': name, 'docs': docs}


def _user(application, scope):
    from ..auth import AccessError
    if not application.business:
        return None
    try:
        return application.business.auth.session(scope)[0]
    except AccessError:
        return None


def _sign_in(scope):
    from ..app import HTTPError
    raise HTTPError(302, 'Sign in to continue', [(b'location', b'/login?next=' + quote(scope['path']).encode())])


def _docs_access(application, scope):
    from ..app import HTTPError
    user = _user(application, scope)
    if user is None:
        _sign_in(scope)
    if user.role not in application.routes.docs_roles:
        raise HTTPError(404, 'Route not found')


def _frontend(application, scope, host, identity, base, rest):
    """Serve an app frontend: the evlet shell under its base, a static dist folder or a server handler."""
    from ..app import HTTPError
    from .serving import frontend_response, static_response
    definition = application.routes.frontend(identity)
    user = _frontend_user(application, scope, definition)
    config = _config(application, scope, host, identity, base)
    if definition.kind == 'evlet':
        match = MODEL_PATH.fullmatch(rest)
        if match is None or match[1] and match[1] not in application.registry.models:
            raise HTTPError(404, 'Route not found')
        response = static_response('/', config)
    elif definition.kind == 'static':
        response = frontend_response(application.routes.dist(identity), rest, config, base, definition.csp.get('connect-src', ()))
        if response is None and rest.startswith('/assets/') and application.routes.realm(host) == 'portal':
            # The settings pages on a portal host load the framework's own assets.
            response = static_response(rest)
    else:
        return _server(application, scope, host, definition, rest, config, user)
    if response is None:
        raise HTTPError(404, 'Route not found')
    return response


def _frontend_user(application, scope, definition):
    """The signed-in user a frontend may see. A user from another realm counts as signed out. Server
    frontends with a realm, and frontends of any kind with roles=, need a matching sign-in to load."""
    from ..app import HTTPError
    user = _user(application, scope)
    # A session only holds on a host of its role's realm, so the host's realm is the user's.
    if user is not None and definition.auth is not None and application.routes.realm(dict(scope.get('headers', [])).get(b'host', b'')) != definition.auth:
        user = None
    if definition.auth is not None and (definition.kind == 'server' or definition.roles):
        if user is None:
            if definition.auth == 'desk':
                _sign_in(scope)
            raise HTTPError(401, 'Sign in to continue')
        if definition.roles and user.role not in definition.roles:
            raise HTTPError(404, 'Route not found')
    return user


def _server(application, scope, host, definition, rest, config, user):
    """Call a server frontend's handler (GET only). Application._dispatch already runs in a worker thread."""
    from ..app import HTTPError
    from ..routing import Request, Response, host_name
    from .serving import CSP, with_config
    try:
        raw = scope.get('query_string', b'').decode('ascii')
        params = dict(parse_qsl(raw, keep_blank_values=True, max_num_fields=100))
    except (ValueError, UnicodeError):
        raise HTTPError(422, 'Use a valid query string') from None
    result = definition.handler(Request(rest or '/', raw, host_name(host) or '', user, params))
    if isinstance(result, str):
        result = Response(result)
    if not isinstance(result, Response):
        raise TypeError('Server frontends return a Response or HTML text')
    body = result.body.encode() if isinstance(result.body, str) else result.body
    if result.content_type.startswith('text/html'):
        body = with_config(body, config)
    return result.status, Download(body), [(b'content-type', result.content_type.encode()), (b'content-security-policy', CSP.encode())]


def _tls_allowed(application, scope):
    """Caddy on-demand TLS asks here before it requests a certificate: 200 only for this instance's hosts."""
    from ..app import HTTPError
    try:
        peer = application.business.auth.client_address(scope) if application.business else (scope.get('client') or ('',))[0]
        loopback = ipaddress.ip_address(peer).is_loopback
        query = parse_qs(scope.get('query_string', b'').decode('ascii'), strict_parsing=True, max_num_fields=1)
    except (ValueError, UnicodeError):
        loopback, query = False, {}
    if scope['method'] != 'GET' or not loopback:
        raise HTTPError(404, 'Route not found')
    domain = query.get('domain', [''])[0].lower()
    if not application.routes.primary or domain not in application.routes.hosts():
        raise HTTPError(404, 'Not served here')
    return 200, {'allowed': domain}, []
