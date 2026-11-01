"""Browser routes and UI JSON responses, separate from record dispatch."""

import re
from urllib.parse import parse_qs


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
        from .registry import route_pattern
        builtin = path in ('/', '/login', '/docs', '/settings/accounts') or path.startswith('/docs/') or path.startswith('/assets/')
        generated = re.fullmatch(r'/app/([a-z][a-z0-9_]*)(?:/(new|[1-9][0-9]{0,15})(/view)?)?', path)
        known_model = generated and generated[1] in application.registry.models
        known_report = application.business and path.startswith('/reports/') and path.removeprefix('/reports/') in application.business.forms.reports
        custom = any(view.route and route_pattern(view.route).fullmatch(path) for view in application.ui.views.values())
        if not (builtin or known_model or known_report or custom): raise HTTPError(404, 'Route not found')
        response = static_response(path)
        if response is not None: return response
        raise HTTPError(404, 'UI assets are not built. Run the frontend production build.')
    return None
