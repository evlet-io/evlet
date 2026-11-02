"""Browser routes and UI JSON responses, separate from record dispatch."""

import re
from urllib.parse import parse_qs

from ..business import Download


def dispatch(application, scope):
    """Return a UI response or None when the core application owns the route."""
    from ..app import HTTPError

    path, method = scope['path'], scope['method']
    if path.startswith('/assets/apps/') and method == 'GET' and application.business:
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
            query = parse_qs(scope.get('query_string', b'').decode('ascii'), strict_parsing=True, max_num_fields=1)
            if set(query) != {'path'} or len(query['path']) != 1: raise ValueError()
            return 200, application.ui.resolve(query['path'][0], schema, user), []
        except (ValueError, UnicodeError):
            raise HTTPError(422, 'Supply one valid view path')
        except LookupError:
            raise HTTPError(404, 'View not found or not permitted')
    if not path.startswith('/api/') and method == 'GET':
        if application.business: application.business.auth.guard(scope)
        from .serving import static_response
        host = dict(scope.get('headers', [])).get(b'host', b'')
        target = application.routes.redirect(host, path)
        if target:
            return 302, Download(b''), [(b'location', target.encode())]
        found = application.routes.match(host, path)
        served = found is not None and _serves(application, *found)
        if not served and path.startswith('/assets/'): raise HTTPError(404, 'Route not found')
        response = static_response(path if served else '/')
        # Unknown browser paths get the app's own not-found page, with a 404 status.
        if response is not None: return response if served else (404, *response[1:])
        if not served: raise HTTPError(404, 'Route not found')
        raise HTTPError(404, 'UI assets are not built. Run the frontend production build.')
    return None


GENERATED = re.compile(r'/app/([a-z][a-z0-9_]*)(?:/(new|[1-9][0-9]{0,15})(/view)?)?')


def _serves(application, frontend, base, rest):
    """Whether a matched framework frontend serves the browser shell at this path."""
    from .registry import route_pattern
    path = base.rstrip('/') + rest
    if frontend == 'evlet.desk':
        if base == '/app':
            match = GENERATED.fullmatch(path)
            return bool(match) and match[1] in application.registry.models
        return path == '/' or any(view.route and route_pattern(view.route).fullmatch(path) for view in application.ui.views.values())
    if frontend == 'evlet.login':
        return path == '/login'
    if frontend == 'evlet.docs':
        return rest == '' or rest.startswith('/')
    if frontend == 'evlet.settings':
        return path == '/settings/accounts'
    if frontend == 'evlet.assets':
        return rest.startswith('/')
    if frontend == 'evlet.reports':
        return bool(application.business) and rest.startswith('/') and rest[1:] in application.business.forms.reports
    return False
