"""The management channel: how the project endpoint reaches a running instance (docs/project-endpoint.md#management-channel).

`/_/manage/*` answers only a loopback caller that is not behind a proxy (no Forwarded or X-Forwarded-For header) and
sends the instance's management token as `Authorization: Bearer ...`. Anything else gets 404, as /_/tls-allowed does,
so the routes do not show from outside. The token is made by the endpoint; the instance keeps only its SHA-256 in
`instances/<name>/control.key` (0600), read on each call. An instance without that file has no management channel.

It serves health, version, apps.lock, the instance id, Controls overrides and app settings, hosts from data and site
files. It never reads or writes records. Writes to Controls overrides, app settings and site files are refused (403)
in production; host changes are allowed in both modes. Every write is audited on the instance with the actor
`project`. The token authenticates these routes only: it is no session, API key or OAuth token anywhere else.
"""

import base64
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import parse_qs

PREFIX = '/_/manage/'  # evlet.app.MANAGE
ACTOR = 'project'
# Every route, as (method, path). The route-walk test checks that nothing else accepts the token.
ROUTES = (
    ('GET', 'health'), ('GET', 'version'), ('GET', 'lock'), ('GET', 'id'),
    ('GET', 'overrides'), ('POST', 'overrides'), ('GET', 'settings'), ('POST', 'settings'),
    ('GET', 'hosts'), ('POST', 'hosts/add'), ('POST', 'hosts/remove'), ('POST', 'hosts/check'),
    ('GET', 'sites'), ('GET', 'sites/files'), ('GET', 'sites/releases'), ('POST', 'sites/files'),
    ('POST', 'sites/publish'), ('POST', 'sites/rollback'),
)
DEVELOPMENT_ONLY = {'overrides', 'settings', 'sites/files', 'sites/publish', 'sites/rollback'}
TOKEN = re.compile(r'[A-Za-z0-9_-]{32,128}')


def digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


class Manage:
    """The channel of one instance: key is the path of control.key, mode() the project's mode, lock the apps.lock path."""

    def __init__(self, key, *, mode, lock):
        self.key, self.mode, self.lock = Path(key), mode, Path(lock)

    def authorized(self, scope):
        """Whether scope comes from loopback, directly, with this instance's token."""
        headers = dict(scope.get('headers') or ())
        if b'forwarded' in headers or b'x-forwarded-for' in headers:
            return False
        try:
            if not ipaddress.ip_address((scope.get('client') or ('',))[0]).is_loopback:
                return False
        except ValueError:
            return False
        sent = headers.get(b'authorization', b'').decode('latin-1')
        if not sent.startswith('Bearer ') or not TOKEN.fullmatch(sent[7:]):
            return False
        try:
            stored = self.key.read_text(encoding='ascii').strip()
        except (OSError, UnicodeError):
            return False
        return hmac.compare_digest(stored, digest(sent[7:]))

    def dispatch(self, application, scope, body):
        from .app import HTTPError
        from .auth import AccessError
        route = (scope['method'], scope['path'].removeprefix(PREFIX))
        if application.business is None or not self.authorized(scope) or route not in ROUTES:
            raise HTTPError(404, 'Route not found')
        if route[0] == 'POST' and route[1] in DEVELOPMENT_ONLY and self.mode() != 'development':
            raise AccessError(403, 'Not available in production')
        query = {key: values[0] for key, values in parse_qs(scope.get('query_string', b'').decode('ascii', 'replace')).items()}
        try:
            return 200, getattr(self, 'do_' + route[0].lower() + '_' + route[1].replace('/', '_'))(application.business, body or {}, query), []
        except (ValueError, LookupError) as exc:
            from .models import ValidationError
            if isinstance(exc, ValidationError):
                raise
            raise HTTPError(422, str(exc)) from None

    # ---------- reads ----------

    def do_get_health(self, business, body, query):
        business.database.check()
        return {'status': 'ok'}

    def do_get_version(self, business, body, query):
        from .framework import state
        return state()

    def do_get_lock(self, business, body, query):
        try:
            return json.loads(self.lock.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'apps': {}}

    def do_get_id(self, business, body, query):
        from .framework import instance_identity
        with business.database._connection() as connection:
            identity = instance_identity(connection)
        return {**(identity or {}), 'project': business.controls.system.project_id(), 'mode': self.mode()}

    def do_get_overrides(self, business, body, query):
        return business.controls.overrides()

    def do_get_settings(self, business, body, query):
        settings = business.settings
        return {'apps': {app: settings.shared(app) for app in sorted(settings.apps) if settings.apps[app]['settings'] is not None}}

    def do_get_hosts(self, business, body, query):
        from .hosts import list_hosts
        return {'hosts': list_hosts(business)}

    def do_get_sites(self, business, body, query):
        from .sites import list_sites
        return {'sites': list_sites(business)}

    def do_get_sites_files(self, business, body, query):
        from .sites import list_files
        return {'site': query.get('site', ''), 'files': list_files(business, query.get('site', ''))}

    def do_get_sites_releases(self, business, body, query):
        from .sites import list_releases
        return {'site': query.get('site', ''), 'releases': list_releases(business, query.get('site', ''))}

    # ---------- writes, audited as `project` ----------

    def do_post_overrides(self, business, body, query):
        return business.controls.save(project_user(), body)

    def do_post_settings(self, business, body, query):
        app = body.get('app')
        if not isinstance(app, str) or app not in business.settings.apps:
            raise ValueError('Name an app with settings')
        return business.settings.save_app(project_user(), app, {'values': body.get('values')})

    def do_post_hosts_add(self, business, body, query):
        from .hosts import add_host, check_hosts
        host, target = text(body, 'host'), text(body, 'target')
        add_host(business, host, target, actor=ACTOR)
        return {'host': check_hosts(business, [host], actor=ACTOR)[0]}

    def do_post_hosts_remove(self, business, body, query):
        from .hosts import remove_host
        remove_host(business, text(body, 'host'), actor=ACTOR)
        return {'removed': body['host']}

    def do_post_hosts_check(self, business, body, query):
        from .hosts import check_hosts
        hosts = body.get('hosts')
        if hosts is not None and (not isinstance(hosts, list) or not all(isinstance(item, str) for item in hosts)):
            raise ValueError('hosts is a list of host names')
        return {'hosts': check_hosts(business, hosts or None, actor=ACTOR)}

    def do_post_sites_files(self, business, body, query):
        from .sites import delete_file, write_files
        site, files, remove = text(body, 'site'), body.get('files') or {}, body.get('delete') or []
        if not isinstance(files, dict) or not isinstance(remove, list) or not files and not remove:
            raise ValueError('Send files as {path: base64 content} and/or delete as a list of paths')
        try:
            contents = {path: base64.b64decode(value, validate=True) for path, value in files.items() if isinstance(value, str)}
        except ValueError:
            raise ValueError('Each file is base64 encoded') from None
        if len(contents) != len(files):
            raise ValueError('Each file is base64 encoded')
        written = write_files(business, site, contents, actor=ACTOR) if contents else []
        for path in remove:
            delete_file(business, site, text({'path': path}, 'path'), actor=ACTOR)
        return {'site': site, 'written': written, 'deleted': remove}

    def do_post_sites_publish(self, business, body, query):
        from .sites import publish
        return publish(business, text(body, 'site'), actor=ACTOR, note=body.get('note') or '')

    def do_post_sites_rollback(self, business, body, query):
        from .sites import rollback
        return rollback(business, text(body, 'site'), body.get('release') or None, actor=ACTOR)


def project_user():
    """Who a management write is: the project, with the admin role, and no account (its id is 0)."""
    from .auth import User
    return User(0, ACTOR, 'admin', system=ACTOR)


def text(body, key):
    value = body.get(key)
    if not isinstance(value, str) or not value or len(value) > 500:
        raise ValueError(f'Send {key}')
    return value
