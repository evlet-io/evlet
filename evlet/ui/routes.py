"""Browser routes and UI JSON responses, separate from record dispatch."""

import ipaddress
import re
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, parse_qsl, quote

from ..auth import has_role
from ..business import Download
from ..identity import path_id


def dispatch(application, scope):
    """Return a UI response or None when the core application owns the route."""
    from ..app import HTTPError, TLS_CHECK

    path, method = scope['path'], scope['method']
    host = dict(scope.get('headers', [])).get(b'host', b'')
    if path == TLS_CHECK:
        return _tls_allowed(application, scope)
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


# A record segment: new, a 13-character text id (any case), or a decimal id from an older link.
RECORD = r'new|[0-9A-Za-z]{13}|[1-9][0-9]{0,11}'
GENERATED = re.compile(rf'/app/([a-z][a-z0-9_]*)(?:/({RECORD})(/view)?)?')
MODEL_PATH = re.compile(rf'(?:/([a-z][a-z0-9_]*)(?:/({RECORD})(/view)?)?)?/?')


def _record_segment(segment):
    """Whether a matched record segment names a record: new, a decimal id, or a text id that decodes."""
    return segment is None or segment == 'new' or len(segment) < 13 or path_id(segment) is not None


def _serves(application, frontend, base, rest):
    """Whether a matched framework frontend serves the browser shell at this path."""
    path = base.rstrip('/') + rest
    if frontend == 'evlet.desk':
        # Eva's screens (/eva, /eva/tasks), only where config.py turned Eva on; the page asks /api/_eva who may use it.
        if base == '/' and (path == '/eva' or path.startswith('/eva/')) and application.business and application.business.eva is not None:
            return re.fullmatch(r'/eva(/tasks|/[1-9][0-9]{0,15})?', path) is not None
        if base == '/app':
            match = GENERATED.fullmatch(path)
            return bool(match) and match[1] in application.registry.exposed and _record_segment(match[2])
        return path == '/'
    if frontend == 'evlet.login':
        return path == '/login'
    if frontend == 'evlet.docs':
        return rest == '' or rest.startswith('/')
    if frontend == 'evlet.assets':
        return rest.startswith('/')
    if frontend == 'evlet.reports':
        # /reports lists the reports, /reports/<name> runs one; the page asks the schema which this person may run.
        reports = application.business.forms.reports if application.business else {}
        return bool(reports) and (rest == '' or rest.startswith('/') and rest[1:] in reports)
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
    return {'frontend': frontend, 'base': base, 'api': '/api', 'realm': routes.realm(host), 'host': name, 'docs': docs,
            'theme': _theme(application, scope)}


THEMES = ('light', 'dark', 'system')


def _theme(application, scope):
    return page_theme(application.business, scope, _user(application, scope))


def page_theme(business, scope, user):
    """The theme a page is painted in before any script runs: the signed-in person's Settings choice,
    else this browser's last one from the evlet_theme cookie the app leaves. Presentation only."""
    if business is not None and user is not None:
        return business.settings.theme(user)
    cookie = SimpleCookie()
    try:
        cookie.load(dict(scope.get('headers', [])).get(b'cookie', b'').decode('latin1'))
    except CookieError:
        return 'system'
    value = cookie['evlet_theme'].value if 'evlet_theme' in cookie else 'system'
    return value if value in THEMES else 'system'


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
    if not has_role(user, application.routes.docs_roles):
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
        if match is None or match[1] and match[1] not in application.registry.exposed or not _record_segment(match[2]):
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
        if definition.roles and not has_role(user, definition.roles):
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
