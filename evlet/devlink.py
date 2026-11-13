"""One-time development sign-in links, like `bench browse --user`: development servers only, loopback only."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time
from urllib.parse import parse_qs

from . import qb
from .adapter import Column, TableDefinition

PATH = '/_dev/signin'
MINUTES = 10
UNAVAILABLE = 'This sign-in link has expired or was already used. Run evlet dev-link for a new one.'

LINKS = TableDefinition('evlet_dev_links', (
    Column('hash', 'TEXT', primary=True), Column('user_id', 'INTEGER', required=True, references='evlet_users', on_delete='cascade'),
    Column('expires', 'INTEGER', required=True)))


def ensure(connection):
    connection.ensure_table(LINKS)


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _choose(connection, username):
    """The account a link signs in: username, else the built-in Administrator, else the only active admin or user."""
    from .auth import ADMINISTRATOR
    rows = connection.run(qb.select(qb.table('evlet_users', 'u'), 'u.id', 'u.username', 'u.role', 'u.password')
                          .join(qb.table('evlet_roles', 'r'), qb.eq('r.id', qb.col('u.role')))
                          .where(qb.eq('u.active', 1), qb.eq('r.realm', 'desk')).order_by('u.id')).all()
    names = ', '.join(row['username'] for row in rows[:10]) or 'none'
    if username is not None:
        found = [row for row in rows if row['username'] == username.lower()]
        if not found:
            raise ValueError(f'No active desk user named {username}. Users here: {names}')
        return found[0]
    builtin = [row for row in rows if row['username'] == ADMINISTRATOR and row['role'] == 'admin']
    if builtin:
        return builtin[0]
    admins = [row for row in rows if row['role'] == 'admin']
    if len(admins) == 1:
        return admins[0]
    if len(rows) == 1:
        return rows[0]
    raise ValueError(f'Choose a user with --user NAME. Users here: {names}')


def issue(database, user=None, *, minutes=MINUTES):
    """Store the hash of a new single-use token for user (the Administrator by default) and return (token, username).

    The token itself is never stored."""
    token, username, _ = _issue(database, user, minutes)
    return token, username


def _issue(database, user, minutes=MINUTES):
    with database.transaction() as tx:
        connection = tx.connection
        ensure(connection)
        now = int(time.time())
        connection.run(qb.delete('evlet_dev_links').where(qb.le('expires', now)))
        if connection.run(qb.select('evlet_users', 'id').limit(1)).one() is None:
            raise ValueError('This app has no users yet. Start it with evlet dev, which creates the Administrator.')
        account = _choose(connection, user)
        token = secrets.token_urlsafe(32)
        connection.run(qb.insert('evlet_dev_links', hash=_digest(token), user_id=account['id'], expires=now + minutes * 60))
    return token, account['username'], account['password'] == ''


def local_origin(auth):
    """The desk origin a printed link uses: 127.0.0.1 first, then localhost."""
    from urllib.parse import urlsplit
    from .routing import authority
    desk = [origin for origin in auth.origins if authority(origin).encode() not in auth.realms]
    for host in ('127.0.0.1', 'localhost'):
        for origin in sorted(desk):
            if urlsplit(origin).hostname == host:
                return origin
    raise ValueError('No loopback origin for a development sign-in link')


def link(origin, token):
    return f'{origin}{PATH}?token={token}'


def _loopback(address):
    try:
        ip = ipaddress.ip_address(address)
    except (TypeError, ValueError):
        return False
    return (ip.ipv4_mapped or ip).is_loopback if ip.version == 6 else ip.is_loopback


def sign_in(business, scope):
    """GET /_dev/signin?token=...: use the token once and start a normal session. Returns (status, body, headers)."""
    from .auth import AccessError
    from .controls import audit
    auth = business.auth
    peer = (scope.get('client') or (None,))[0]
    bound = (scope.get('server') or (None,))[0]
    if scope['method'] != 'GET' or not _loopback(peer) or not _loopback(auth.client_address(scope)) or not (bound is None or _loopback(bound)):
        return 404, b'Not found', []
    try:
        auth.guard(scope)
    except AccessError as exc:
        return exc.status, exc.message.encode(), []
    values = parse_qs(scope.get('query_string', b'').decode('latin1')).get('token', [])
    token = values[0] if len(values) == 1 else ''
    realm = auth.realm(scope)
    with business.database.transaction() as tx:
        connection = tx.connection
        ensure(connection)
        now = int(time.time())
        connection.run(qb.delete('evlet_dev_links').where(qb.le('expires', now)))
        digest = _digest(token)
        row = connection.run(qb.select('evlet_dev_links', 'hash', 'user_id').where(hash=digest)).one() if token else None
        if row is None or not hmac.compare_digest(row['hash'], digest):
            return 403, UNAVAILABLE.encode(), []
        connection.run(qb.delete('evlet_dev_links').where(hash=row['hash']))
        account = connection.run(qb.select(qb.table('evlet_users', 'u'), qb.all_columns('u'), qb.named('r.realm', 'role_realm'))
                                 .join(qb.table('evlet_roles', 'r'), qb.eq('r.id', qb.col('u.role')), left=True)
                                 .where(qb.eq('u.id', row['user_id']))).one()
        if account is None or not account['active'] or account['role_realm'] != realm:
            return 403, UNAVAILABLE.encode(), []
        # The link comes from someone with this development instance's files, as strong as a finished second step.
        _, headers = auth.start_session(tx, scope, account, realm, verified=True)
        audit(connection, account['username'], 'Users', account['username'], 'Signed in with a development link')
    return 302, b'', [*headers, (b'location', b'/')]


def announce(application, user=None):
    """Print a fresh link for a started development application; problems are printed, not raised.
    When the account has no password yet (the Administrator on a new instance), the link leads to setting one."""
    try:
        token, username, first = _issue(application.database, user)
        url = link(local_origin(application.business.auth), token)
        if first:
            print(f'The {username} account has no password yet. Open this link to sign in and choose one (works once, for {MINUTES} minutes):\n  {url}',
                  flush=True)
            return
        print(f'Sign in as {username} (works once, for {MINUTES} minutes):\n  {url}', flush=True)
    except ValueError as exc:
        print(f'No sign-in link: {exc}', flush=True)


class DevSignIn:
    """Serve one-time sign-in links at /_dev/signin. Wrap development applications only, never one you serve.

    Other requests and the lifespan pass through unchanged. With announce=True a link is printed once startup completes,
    for user or, by default, the built-in Administrator."""

    def __init__(self, app, *, announce=False, user=None, create_admin=None):
        if create_admin is not None:
            import warnings
            warnings.warn('DevSignIn(create_admin=...) is ignored and will be removed in 0.5: every instance has the built-in '
                          'Administrator, and the link signs it in by default.', DeprecationWarning, stacklevel=2)
        self.app, self.announce, self.user = app, announce, user

    def application(self):
        target = self.app
        while target is not None and getattr(target, 'business', None) is None:
            target = getattr(target, 'app', None)
        return target

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['path'] == PATH:
            application = self.application()
            if application is None:
                status, body, headers = 404, b'Not found', []
            else:
                status, body, headers = await asyncio.to_thread(sign_in, application.business, scope)
            response = [(b'content-type', b'text/plain; charset=utf-8'), (b'cache-control', b'no-store'), (b'referrer-policy', b'no-referrer'),
                        (b'x-content-type-options', b'nosniff'), (b'content-length', str(len(body)).encode()), *headers]
            await send({'type': 'http.response.start', 'status': status, 'headers': response})
            await send({'type': 'http.response.body', 'body': body})
            return
        if scope['type'] == 'lifespan' and self.announce:
            async def started(message):
                if message['type'] == 'lifespan.startup.complete':
                    application = self.application()
                    if application is not None:
                        await asyncio.to_thread(announce, application, self.user)
                await send(message)
            await self.app(scope, receive, started)
            return
        await self.app(scope, receive, send)


# ---------- finding a running `evlet playground` ----------

def _owned(path):
    """A folder of this user that no one else can write, so another account cannot plant a Playground."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and (not hasattr(os, 'getuid') or info.st_uid == os.getuid()) and not info.st_mode & 0o022


def write_marker(directory, port):
    target = Path(directory) / 'server.json'
    target.write_text(json.dumps({'port': port, 'pid': os.getpid()}), encoding='utf-8')


def running_playgrounds():
    """(port, directory) of each `evlet playground` of this user still running, newest first."""
    found = []
    for marker in Path(tempfile.gettempdir()).glob('evlet-playground-*/server.json'):
        if not _owned(marker.parent):
            continue
        try:
            data = json.loads(marker.read_text(encoding='utf-8'))
            os.kill(int(data['pid']), 0)
            found.append((marker.stat().st_mtime, int(data['port']), marker.parent))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return [(port, directory) for _, port, directory in sorted(found, reverse=True)]
