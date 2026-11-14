"""Settings (/settings): personal pages, app settings and preferences, and two-step sign-in."""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import struct
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

from . import qb, secrets as vault
from .adapter import Column, TableDefinition
from .auth import (ADMINISTRATOR, ADMINISTRATOR_NAME, AccessError, User, _encode, _scrypt, client_network, hashing_slot, is_administrator,
                   password_hash)
from .fields import MISSING, Email, Field
from .models import ValidationError, checked_body

logger = logging.getLogger(__name__)

_USER = {'references': 'evlet_users', 'on_delete': 'cascade'}
TABLES = (
    TableDefinition('evlet_user_profile', (
        Column('user_id', 'INTEGER', primary=True, **_USER),
        Column('full_name', 'TEXT', required=True, default=''),
        Column('short_name', 'TEXT', required=True, default=''),
        Column('title', 'TEXT', required=True, default=''),
        Column('phone', 'TEXT', required=True, default=''),
        Column('email', 'TEXT', required=True, default=''),
        Column('photo_file', 'INTEGER'),
        Column('signature', 'TEXT', required=True, default=''),
        Column('two_step_secret', 'BLOB'),
        Column('backup_codes', 'TEXT'),
        Column('two_step_since', 'INTEGER'),
        Column('two_step_last', 'INTEGER'),
        Column('two_step_pending', 'BLOB'),
    )),
    TableDefinition('evlet_user_prefs', (
        Column('user_id', 'INTEGER', required=True, **_USER),
        Column('scope', 'TEXT', required=True),
        Column('key', 'TEXT', required=True),
        Column('value', 'TEXT', required=True),
    ), primary_key=('user_id', 'scope', 'key')),
    TableDefinition('evlet_email_tokens', (
        Column('token_hash', 'TEXT', primary=True),
        Column('user_id', 'INTEGER', required=True, **_USER),
        Column('new_email', 'TEXT', required=True),
        Column('expires', 'INTEGER', required=True),
    )),
    TableDefinition('evlet_app_settings', (
        Column('app', 'TEXT', required=True),
        Column('key', 'TEXT', required=True),
        Column('value', 'TEXT', required=True),
        Column('changed_by', 'INTEGER'),
        Column('changed_at', 'INTEGER'),
    ), primary_key=('app', 'key')),
    TableDefinition('evlet_two_step_tickets', (
        Column('token_hash', 'TEXT', primary=True),
        Column('user_id', 'INTEGER', required=True, **_USER),
        Column('realm', 'TEXT', required=True),
        Column('expires', 'INTEGER', required=True),
        Column('attempts', 'INTEGER', required=True, default=0),
        Column('secret', 'BLOB'),
    )),
)

PAGES = ('profile', 'appearance', 'security', 'notifications', 'email', 'ai', 'keys')
PORTAL_PAGES = ('profile', 'security', 'notifications')
APPEARANCE = {
    'theme': 'system', 'density': 'comfortable', 'text': 'default', 'start': '', 'counts': False, 'lang': 'en',
    'tz': 'Asia/Dubai', 'date': 'dd-MM-yyyy', 'num': '1,234.56', 'week': 'mon',
    # Enter moves to the next field in forms (Shift+Enter to the one before), for fast data entry.
    'enter_next': False,
    # Keep unsaved changes when I leave a record (evlet.unsaved); off brings back the leave prompt.
    'keep_unsaved': True,
}
CHOICES = {
    'theme': ('light', 'dark', 'system'), 'density': ('comfortable', 'compact'), 'text': ('default', 'large'), 'lang': ('en', 'ar'),
    'date': ('d MMM yyyy', 'dd-MM-yyyy', 'yyyy-MM-dd', 'MM/dd/yyyy'), 'num': ('1,234.56', '1.234,56', '1 234.56'),
    'week': ('mon', 'sun', 'sat'),
}
# (group, [(event, label, locked by email)]). Locked events always send email: security mail cannot be turned off.
EVENTS = (
    ('Records', (('assigned', 'A record is assigned to me', False), ('mention', 'Someone mentions me', False),
                 ('job_failed', 'A job I started fails', False))),
    ('Account', (('new_device', 'Sign-in from a new device', True), ('password', 'My password is changed', True))),
)
PORTAL_EVENTS = ('Account',)
QUIET = {'on': False, 'from': '20:00', 'to': '07:00'}
PHOTO_TYPES = {'image/png': b'\x89PNG\r\n\x1a\n', 'image/jpeg': b'\xff\xd8\xff', 'image/webp': b'RIFF'}
PHOTO_LIMIT = 2 * 1024 * 1024
CODE_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
TICKET_SECONDS, TICKET_TRIES = 300, 5
SIGN_IN_AGAIN = 'Your sign-in expired. Sign in again.'
KEY_PROBLEM = 'Two-step sign-in is unavailable: the secret key is missing or changed. Ask your admin.'
CODE_TRIES, CODE_WINDOW = 10, 900
# A code checked on a session counts for changes in Controls for this long.
VERIFIED_SECONDS = 600
VERIFY = 'VERIFY_TWO_STEP: Enter a code from your authenticator app to make this change.'
ENTER_CODE = 'Enter a current code from your app, or a backup code.'
TOO_MANY_CODES = 'Too many wrong codes. Try again in 15 minutes.'
NO_EMAIL = "Email is not set up yet, so your address can't be changed here. Ask your admin."
FIXED_PROFILE = 'The Administrator profile is fixed. You can add an email address for notifications.'
PASSWORD_SET = 'This account already has a password. Change it in Settings, under Sign-in & security.'




class _Declared:
    """Collects the Field attributes of a settings class in declaration order."""
    __fields__ = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        fields = dict(getattr(cls.__mro__[1], '__fields__', {}))
        fields.update({name: value for name, value in vars(cls).items() if isinstance(value, Field)})
        for name, field in fields.items():
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}', name):
                raise ValueError(f'{cls.__name__}.{name}: use a lowercase name')
            if field.compute is not None or field.query is not None or not field.stored or field.kind in ('link', 'reference', 'sequence', 'state'):
                raise ValueError(f'{cls.__name__}.{name}: settings take plain fields, such as Text, Decimal, Select or Boolean')
            if field.default is not MISSING and not callable(field.default):
                field.clean(field.default)
        cls.__fields__ = fields


class AppSettings(_Declared):
    """Shared settings of an app ("For everyone"). Roles in editors, and admin, change them."""
    editors = ()
    sections = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        from .auth import ROLE_ID
        if isinstance(cls.editors, str) or not isinstance(cls.editors, (tuple, list)) or any(not isinstance(role, str) or not ROLE_ID.fullmatch(role) for role in cls.editors):
            raise ValueError(f'{cls.__name__}.editors takes a tuple of role ids')
        sections = cls.sections if cls.sections is not None else {'General': tuple(cls.__fields__)}
        named = [name for names in sections.values() for name in names] if isinstance(sections, dict) else None
        if named is None or any(not isinstance(title, str) or not title for title in sections) or sorted(named) != sorted(cls.__fields__):
            raise ValueError(f'{cls.__name__}.sections maps titles to field names; name every field once')
        cls.__sections__ = {title: tuple(names) for title, names in sections.items()}


class UserPreferences(_Declared):
    """Settings each person chooses for themselves in an app ("Just for you")."""


def _label(name, field):
    return field.label or name.replace('_', ' ').capitalize()


def _default(field):
    value = field.default() if callable(field.default) else field.default
    return None if value is MISSING or value is None else field.clean(value)


def _schema(name, field):
    from .ui.metadata import field_schema
    return {**field_schema(field), 'name': name, 'label': _label(name, field), 'default': field.serialize(_default(field))}


def _stored(field, raw):
    """A stored JSON value, cleaned again; the default when it no longer validates."""
    try:
        return field.clean(json.loads(raw))
    except (ValueError, TypeError):
        return _default(field)


# ---------- two-step sign-in ----------

def totp(secret, counter):
    digest = hmac.new(secret, struct.pack('>Q', counter), 'sha1').digest()
    offset = digest[-1] & 15
    return '%06d' % ((struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000)


def check_totp(secret, code, last=-1, now=None):
    """The time step a 6-digit code belongs to (one step either side), if newer than last; else None."""
    step = int((time.time() if now is None else now) // 30)
    for counter in (step - 1, step, step + 1):
        if counter > last and hmac.compare_digest(totp(secret, counter), code):
            return counter
    return None


def _b32(raw):
    return base64.b32encode(raw).decode().rstrip('=')


def _code_hash(user_id, code):
    return hashlib.sha256(f'{user_id}:{code}'.encode()).hexdigest()


class Values:
    """services.settings: an app's shared settings, cached per config version."""

    def __init__(self, settings):
        self._settings = settings

    def get(self, app, key):
        declared = self._settings.declared(app, 'settings')
        if key not in declared.__fields__:
            raise KeyError(f'{app} has no setting {key}')
        return self._settings.shared(app).get(key, _default(declared.__fields__[key]))


class Preferences:
    """services.preferences(user): a person's app preferences, or the declared defaults."""

    def __init__(self, settings, user):
        self._settings, self._user = settings, user

    def get(self, app, key):
        declared = self._settings.declared(app, 'preferences')
        field = declared.__fields__.get(key)
        if field is None:
            raise KeyError(f'{app} has no preference {key}')
        with self._settings.database._connection() as connection:
            row = connection.run(qb.select('evlet_user_prefs', 'value').where(user_id=self._user.id, scope=app, key=key)).one()
        return _default(field) if row is None else _stored(field, row['value'])


class Settings:
    def __init__(self, business):
        self.business, self.database = business, business.database
        self.apps = {}
        self.values = Values(self)
        self._cache = (None, {})

    def register(self, app, settings=None, preferences=None, models=()):
        """Declare an app's settings and preferences; a person sees them when they can read one of models."""
        for value, base in ((settings, AppSettings), (preferences, UserPreferences)):
            if value is not None and not (isinstance(value, type) and issubclass(value, base) and value is not base):
                raise ValueError(f'Register a subclass of {base.__name__}')
        if app in self.apps or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', app):
            raise ValueError(f'App settings for {app} are invalid or registered twice')
        self.apps[app] = {'settings': settings, 'preferences': preferences, 'models': tuple(models)}

    def preferences(self, user):
        return Preferences(self, user)

    def declared(self, app, kind):
        found = self.apps.get(app, {}).get(kind)
        if found is None:
            raise KeyError(f'{app} declares no {kind}')
        return found

    @property
    def key(self):
        return vault.secret_key(getattr(self.business, 'secret_key', None))

    def sealed_exists(self, connection):
        """Whether anything is sealed with the secret key; then a missing key is an error, never a new key."""
        return vault.sealed_exists(connection)

    def seal(self, connection, value, purpose):
        return vault.seal(self.key, value, purpose, create=not self.sealed_exists(connection))

    def possible(self):
        """Two-step sign-in can run here: a secret key is configured and cryptography is installed."""
        return self.key.configured and vault.cryptography_installed()

    def required_roles(self):
        """The desk roles that must use two-step sign-in now."""
        controls = self.business.controls
        return [role for role, row in controls.roles.items() if row.get('realm') == 'desk' and controls.two_step_required(role)]

    def two_step_problem(self):
        """Why roles that require two-step sign-in sign in with a password only on this instance, or None.
        An application without a secret key (not an instance) has no two-step sign-in by design."""
        if not self.key.configured or not self.required_roles():
            return None
        if not vault.cryptography_installed():
            return 'The cryptography package is not installed on the server, so these roles sign in with a password only. Install it and restart.'
        try:
            self.key.check()
        except (vault.SecretKeyError, OSError) as exc:
            return str(exc)
        return None

    def ready(self, connection=None):
        """The key can be used now. With sealed data it must already exist."""
        if connection is None:
            with self.database._connection() as db:
                return self.ready(db)
        return self.possible() and vault.available(self.key, existing=self.sealed_exists(connection))

    def initialize(self):
        with self.database.transaction() as tx:
            for table in TABLES:
                tx.connection.ensure_table(table)
            if 'two_step_agent' not in tx.connection.columns('evlet_user_profile'):
                tx.connection.add_column('evlet_user_profile', Column('two_step_agent', 'TEXT', required=True), default='')
            # The Administrator's name never changes.
            row = tx.connection.run(qb.select('evlet_users', 'id').where(username=ADMINISTRATOR, role='admin')).one()
            if row is not None:
                self.profile(tx.connection, row['id'])
                tx.connection.run(qb.update('evlet_user_profile', full_name=ADMINISTRATOR_NAME, short_name='Administrator')
                                  .where(qb.or_(qb.ne('full_name', ADMINISTRATOR_NAME), qb.ne('short_name', 'Administrator')), user_id=row['id']))
        self.business.auth.second_step = self
        if self.key.configured and self.required_roles():
            if not vault.cryptography_installed():
                logger.warning('Roles %s need two-step sign-in, but the cryptography package is not installed. They sign in with a password only '
                               'until it is.', ', '.join(self.required_roles()))
            else:
                try:
                    self.key.check()
                except (vault.SecretKeyError, OSError) as exc:
                    logger.warning('Two-step sign-in is unavailable: %s', exc)

    # ---------- shared values ----------

    def shared(self, app):
        """Stored "For everyone" values of an app, read again when the config version moves."""
        controls = self.business.controls
        version, cache = self._cache
        if version is None or version != controls.version:
            cache = {}
            with self.database._connection() as connection:
                for row in connection.run(qb.select('evlet_app_settings', 'app', 'key', 'value')):
                    declared = self.apps.get(row['app'], {}).get('settings')
                    if declared is not None and row['key'] in declared.__fields__:
                        cache.setdefault(row['app'], {})[row['key']] = _stored(declared.__fields__[row['key']], row['value'])
            if controls.version is not None:
                self._cache = (controls.version, cache)
        return cache.get(app, {})

    # ---------- who sees what ----------

    def portal(self, scope, user):
        role = self.business.controls.roles.get(user.role) or {}
        return self.business.auth.realm(scope) == 'portal' or role.get('realm') == 'portal'

    def pages(self, scope, user):
        return PORTAL_PAGES if self.portal(scope, user) else PAGES

    def visible(self, user, app):
        entry = self.apps[app]
        policy = self.business.policy
        if entry['models']:
            return any(policy.allows(user, model, 'read') for model in entry['models'])
        return self.can_edit(user, app)

    def can_edit(self, user, app):
        declared = self.apps[app]['settings']
        return declared is not None and (user.role == 'admin' or user.role in declared.editors)

    def visible_apps(self, scope, user):
        if self.portal(scope, user):
            return []
        return [app for app in self.apps if self.visible(user, app)]

    def page(self, scope, path):
        """GET /settings[/...]: True serves the page, False is a 404, a tuple redirects."""
        from .business import Download
        rest = path.removeprefix('/settings').strip('/')
        portal_host = self.business.auth.realm(scope) == 'portal'
        if rest and rest not in (PORTAL_PAGES if portal_host else PAGES) and (portal_host or not re.fullmatch(r'app/[a-z][a-z0-9_]{0,62}', rest)):
            return False
        try:
            user, _ = self.business.auth.session(scope)
        except AccessError as exc:
            if exc.status != 401:
                raise
            target = '/' if self.business.auth.realm(scope) == 'portal' else '/login?next=' + quote(path, safe='/')
            return 302, Download(b''), [(b'location', target.encode())]
        pages = self.pages(scope, user)
        if not rest:
            last = self.core(user.id).get('last_page') or ''
            last = last if last in pages or (last.startswith('app/') and last[4:] in self.visible_apps(scope, user)) else 'profile'
            return 302, Download(b''), [(b'location', f'/settings/{last}'.encode())]
        return rest in pages or rest.startswith('app/') and rest[4:] in self.visible_apps(scope, user)

    # ---------- personal values ----------

    def theme(self, user):
        """The person's theme for the page the server sends: light, dark or system."""
        value = self.core(user.id).get('theme')
        return value if value in CHOICES['theme'] else APPEARANCE['theme']

    def core(self, user_id, connection=None):
        def read(db):
            return {row['key']: json.loads(row['value']) for row in db.run(qb.select('evlet_user_prefs', 'key', 'value').where(user_id=user_id, scope='core'))}
        if connection is not None:
            return read(connection)
        with self.database._connection() as db:
            return read(db)

    def profile(self, connection, user_id):
        connection.run(qb.insert('evlet_user_profile', user_id=user_id).on_conflict().do_nothing())
        return connection.run(qb.select('evlet_user_profile').where(user_id=user_id)).one()

    def required(self, role, realm='desk'):
        return realm == 'desk' and self.business.controls.two_step_required(role) and self.possible()

    def two_step_on(self, user_id):
        with self.database._connection() as connection:
            row = connection.run(qb.select('evlet_user_profile', 'two_step_secret').where(user_id=user_id)).one()
        return row is not None and row['two_step_secret'] is not None

    def me(self, scope, user):
        portal = self.portal(scope, user)
        with self.database.transaction() as tx:
            row = self.profile(tx.connection, user.id)
            prefs = self.core(user.id, tx.connection)
            sessions = tx.connection.run(qb.select('evlet_sessions', 'token', 'realm', 'started', 'expires', 'agent')
                                         .where(qb.gt('expires', int(time.time())), user_id=user.id).order_by('-started')).all()
            available = self.ready(tx.connection)
            keys = 0 if portal else tx.connection.run(qb.select('evlet_api_keys', qb.count()).where(user_id=user.id, revoked_at=None)).value()
            try:
                codes = len(self.codes(row)) if row['backup_codes'] and available else 0
            except vault.SecretKeyError:
                codes, available = 0, False
        current = self.business.auth.token(scope)
        events = [{'group': group, 'items': [{'key': key, 'label': label, 'locked': locked,
                                              'channels': prefs.get('notify.' + key, [True, True])} for key, label, locked in items]}
                  for group, items in EVENTS if not portal or group in PORTAL_EVENTS]
        apps = []
        for app in self.visible_apps(scope, user):
            entry = self.apps[app]
            labels = [_label(name, field) for kind in ('settings', 'preferences') if entry[kind] for name, field in entry[kind].__fields__.items()]
            apps.append({'id': app, 'title': app.replace('_', ' ').title(), 'can_edit': self.can_edit(user, app), 'labels': labels})
        controls = self.business.controls
        return {
            'user': user.public(), 'portal': portal, 'pages': list(self.pages(scope, user)), 'apps': apps,
            'last_page': prefs.get('last_page', 'profile'),
            'controls': not portal and controls.routes is not None and controls.allows(scope, user),
            # The workspace override routes (/api/_controls/workspaces): the same check they use. A session that
            # still has to give a two-step code reports true; the routes ask for the code.
            'customize_workspaces': not portal and controls.customizes(scope, user),
            'development': getattr(getattr(self.business, 'routes', None), 'primary', None) is None,
            # The built-in Administrator: only its email address can change in its profile.
            'builtin': is_administrator(user),
            'profile': {key: row[key] for key in ('full_name', 'short_name', 'title', 'phone', 'email', 'signature')} | {'photo': row['photo_file'] is not None},
            'prefs': {key: prefs.get(key, value) for key, value in APPEARANCE.items()},
            'notifications': {'events': events, 'quiet': prefs.get('quiet', QUIET)},
            # Unsaved changes kept on leave (evlet.unsaved): limits and how many this person has.
            **({} if portal else {'unsaved': self.business.unsaved.info(user)}),
            'security': {
                'password_min': self.business.auth.minimum(user.role),
                'two_step': {'on': row['two_step_secret'] is not None, 'since': row['two_step_since'], 'agent': row['two_step_agent'], 'codes': codes,
                             'required': self.required(user.role, 'portal' if portal else 'desk'), 'available': available,
                             'verified': user.two_step},
                'api_keys': keys,
                'sessions': [{'id': item['token'][:16], 'current': item['token'] == current, 'started': item['started'], 'expires': item['expires'],
                              'agent': item['agent'], 'realm': item['realm']} for item in sessions],
            },
        }

    # ---------- HTTP ----------

    def dispatch(self, scope, body, user):
        path, method = scope['path'].removeprefix('/api/_settings'), scope['method']
        portal = self.portal(scope, user)
        match = re.fullmatch(r'/app/([a-z][a-z0-9_]{0,62})', path)
        if match and not portal and match[1] in self.apps and self.visible(user, match[1]):
            if method == 'GET':
                return 200, self.app_state(user, match[1]), []
            if method == 'PATCH':
                return 200, self.save_app(user, match[1], body), []
        match = re.fullmatch(r'/me/app/([a-z][a-z0-9_]{0,62})', path)
        if match and method == 'PATCH' and not portal and match[1] in self.apps and self.visible(user, match[1]):
            return 200, self.save_preferences(user, match[1], body), []
        routes = {
            ('GET', '/me'): lambda: self.me(scope, user),
            ('PATCH', '/me/profile'): lambda: self.save_profile(scope, user, body),
            ('PATCH', '/me/prefs'): lambda: self.save_prefs(scope, user, body),
            ('GET', '/me/photo'): lambda: self.photo(user),
            ('POST', '/me/photo'): lambda: self.save_photo(user, body),
            ('DELETE', '/me/photo'): lambda: self.remove_photo(user),
            ('POST', '/me/email'): lambda: self.change_email(scope, user, body),
            ('POST', '/me/email/confirm'): lambda: self.confirm_email(user, body),
            ('POST', '/me/password'): lambda: self.change_password(scope, user, body),
            ('DELETE', '/me/sessions'): lambda: self.end_sessions(scope, user, None),
        }
        if not portal:
            routes.update({
                ('POST', '/me/two-step'): lambda: self.start_two_step(scope, user, body),
                ('POST', '/me/two-step/confirm'): lambda: self.confirm_two_step(scope, user, body),
                ('POST', '/me/two-step/codes'): lambda: self.new_codes(scope, user, body),
                ('POST', '/me/two-step/off'): lambda: self.turn_off(scope, user, body),
                ('POST', '/me/two-step/verify'): lambda: self.verify(scope, user, body),
            })
        match = re.fullmatch(r'/me/sessions/([0-9a-f]{16})', path)
        if match and method == 'DELETE':
            return 200, self.end_sessions(scope, user, match[1]), []
        handler = routes.get((method, path))
        if handler is None:
            raise AccessError(404, 'Route not found')
        try:
            result = handler()
        except vault.SecretKeyError:
            raise AccessError(503, KEY_PROBLEM) from None
        return result if isinstance(result, tuple) else (200, result, [])

    @staticmethod
    def _body(body, allowed):
        return checked_body(body, allowed, 'Send ' + ', '.join(allowed), nonempty=True)

    # ---------- profile ----------

    def save_profile(self, scope, user, body):
        if is_administrator(user):
            raise AccessError(409, FIXED_PROFILE)
        limits = {'full_name': 120, 'short_name': 40, 'title': 120, 'phone': 32, 'signature': 1000}
        body, errors = self._body(body, limits), {}
        for key, value in body.items():
            if not isinstance(value, str) or len(value) > limits[key] or '\x00' in value:
                errors[key] = f'Use at most {limits[key]} characters.'
            elif key in ('full_name', 'short_name') and not value.strip():
                errors[key] = "This can't be empty."
            elif key == 'phone' and value.strip() and not re.fullmatch(r'\+?[0-9 ()-]{3,31}', value.strip()):
                errors[key] = 'Use digits, spaces, +, - and brackets only.'
        if errors:
            raise ValidationError(errors)
        with self.database.transaction() as tx:
            self.profile(tx.connection, user.id)
            for key, value in body.items():
                tx.connection.run(qb.update('evlet_user_profile', **{key: value if key == 'signature' else value.strip()}).where(user_id=user.id))
        return self.me(scope, user)['profile']

    def photo(self, user):
        from .business import Download
        with self.database._connection() as connection:
            row = connection.run(qb.select(qb.table('evlet_user_profile', 'p'), qb.all_columns('a'))
                                 .join(qb.table('evlet_attachments', 'a'), qb.eq('a.id', qb.col('p.photo_file')))
                                 .where(qb.eq('p.user_id', user.id))).one()
        if row is None:
            raise AccessError(404, 'No photo')
        content = self.business.storage.read(row)
        kind = next((name for name, magic in PHOTO_TYPES.items() if content.startswith(magic)), 'application/octet-stream')
        return 200, Download(content), [(b'content-type', kind.encode()), (b'content-security-policy', b"default-src 'none'; sandbox")]

    def save_photo(self, user, body):
        if is_administrator(user):
            raise AccessError(409, FIXED_PROFILE)
        body = self._body(body, ('type', 'data'))
        kind = body.get('type')
        try:
            content = base64.b64decode(body.get('data', ''), validate=True) if isinstance(body.get('data'), str) else b''
        except ValueError:
            content = b''
        if kind not in PHOTO_TYPES or not content.startswith(PHOTO_TYPES[kind]) or kind == 'image/webp' and content[8:12] != b'WEBP':
            raise ValidationError({'photo': 'Use a PNG, JPG or WebP image.'})
        if len(content) > PHOTO_LIMIT:
            raise ValidationError({'photo': 'That photo is over 2 MB. Choose a smaller one.'})
        with self.database.transaction() as tx:
            old = self.profile(tx.connection, user.id)['photo_file']
            file = self.business.storage.add(tx, 'evlet_user_photo', user.id, 'photo' + {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}[kind], content)
            tx.connection.run(qb.update('evlet_user_profile', photo_file=file).where(user_id=user.id))
            removed = self._drop_photo(tx.connection, old)
        self._remove_file(removed)
        return {'photo': True}

    def _drop_photo(self, connection, file):
        if file is None:
            return None
        row = connection.run(qb.select('evlet_attachments', 'storage_key', 'storage_kind').where(id=file, model='evlet_user_photo')).one()
        connection.run(qb.delete('evlet_attachments').where(id=file, model='evlet_user_photo'))
        return row

    def _remove_file(self, row):
        """Delete a replaced photo's file once its row is gone."""
        remove = getattr(self.business.storage, 'remove', None)
        if row is not None and row['storage_key'] is not None and remove is not None:
            try:
                remove(row['storage_key'])
            except Exception:
                logger.warning('A replaced photo file could not be removed; storage collection removes it later.')

    def remove_photo(self, user):
        with self.database.transaction() as tx:
            old = self.profile(tx.connection, user.id)['photo_file']
            tx.connection.run(qb.update('evlet_user_profile', photo_file=None).where(user_id=user.id))
            removed = self._drop_photo(tx.connection, old)
        self._remove_file(removed)
        return {'photo': False}

    def administrator_email(self, scope, user, body):
        """The Administrator's email for notifications: set at once on any instance, after its current password (and a
        code when two-step sign-in is on). Audited."""
        from .controls import audit
        body = self._body(body, ('email', 'password', 'code'))
        address = body.get('email')
        try:
            address = Email().clean(address.strip() if isinstance(address, str) else address)
        except ValueError:
            address = None
        if not address:
            raise ValidationError({'email': 'Enter a valid email address.'})
        password = body.get('password')
        if not isinstance(password, str) or not password:
            raise ValidationError({'password': 'Enter your current password.'})
        self._check_password(user, password, 'password')
        if self.two_step_on(user.id):
            self.check_code(scope, user, body.get('code'))
        with self.database.transaction() as tx:
            before = self.profile(tx.connection, user.id)['email']
            tx.connection.run(qb.update('evlet_user_profile', email=address).where(user_id=user.id))
            tx.connection.run(qb.delete('evlet_email_tokens').where(user_id=user.id))
            audit(tx.connection, user.username, 'Users', user.username, 'Email for notifications changed in Settings',
                  {'email': before or None}, {'email': address})
        return {'email': address}

    def change_email(self, scope, user, body):
        if is_administrator(user):
            return self.administrator_email(scope, user, body)
        routes = self.business.routes
        if routes.primary is not None:
            raise AccessError(409, NO_EMAIL)
        body = self._body(body, ('email', 'password', 'code'))
        address = body.get('email')
        try:
            address = Email().clean(address.strip() if isinstance(address, str) else address)
        except ValueError:
            address = None
        if not address:
            raise ValidationError({'email': 'Enter a valid email address.'})
        self.reauthenticate(scope, user, body)
        token, now = secrets.token_urlsafe(32), int(time.time())
        with self.database.transaction() as tx:
            tx.connection.run(qb.delete('evlet_email_tokens').where(qb.or_(qb.eq('user_id', user.id), qb.le('expires', now))))
            tx.connection.run(qb.insert('evlet_email_tokens', token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                                        new_email=address, expires=now + 86400))
        from .routing import host_name
        host = host_name(dict(scope.get('headers', [])).get(b'host', b'')) or 'localhost'
        link = f'http://{host}:{routes.port}/settings/profile?confirm={token}'
        # Development only, until outgoing mail is set up: the link is logged and shown on the page.
        logger.info('Email change for %s to %s: %s', user.username, address, link)
        return {'sent': address, 'link': link}

    def confirm_email(self, user, body):
        token = self._body(body, ('token',)).get('token')
        now = int(time.time())
        with self.database.transaction() as tx:
            row = tx.connection.run(qb.select('evlet_email_tokens').where(qb.gt('expires', now), token_hash=hashlib.sha256(str(token).encode()).hexdigest(),
                                                                          user_id=user.id)).one()
            if row is None:
                raise ValidationError({'token': 'This link has expired or was already used. Ask for a new one.'})
            self.profile(tx.connection, user.id)
            tx.connection.run(qb.update('evlet_user_profile', email=row['new_email']).where(user_id=user.id))
            tx.connection.run(qb.delete('evlet_email_tokens').where(user_id=user.id))
        return {'email': row['new_email']}

    # ---------- appearance, region and notifications ----------

    def save_prefs(self, scope, user, body):
        portal = self.portal(scope, user)
        if not isinstance(body, dict) or not body or len(body) > 40:
            raise ValidationError({'_form': 'Send the preferences to change'})
        events = {key: locked for group, items in EVENTS if not portal or group in PORTAL_EVENTS for key, _, locked in items}
        errors, clean = {}, {}
        for key, value in body.items():
            try:
                clean[key] = self.check_pref(key, value, events, portal, scope, user)
            except ValueError as exc:
                errors[key] = str(exc)
        if errors:
            raise ValidationError(errors)
        with self.database.transaction() as tx:
            for key, value in clean.items():
                tx.connection.run(qb.insert('evlet_user_prefs', user_id=user.id, scope='core', key=key, value=json.dumps(value))
                                  .on_conflict('user_id', 'scope', 'key').do_update(value=qb.col('excluded.value')))
            if clean.get('keep_unsaved') is False:
                # Turning it off forgets what was kept, in the same transaction.
                from .unsaved import Unsaved
                Unsaved.clear_user(tx.connection, user.id)
        return {'saved': sorted(clean)}

    def check_pref(self, key, value, events, portal, scope, user):
        if key == 'last_page':
            if not isinstance(value, str) or not (value in self.pages(scope, user) or value.startswith('app/') and value[4:] in self.visible_apps(scope, user)):
                raise ValueError('Choose a settings page.')
            return value
        if key == 'quiet':
            if not isinstance(value, dict) or set(value) != {'on', 'from', 'to'} or type(value['on']) is not bool or \
                    any(not isinstance(value[part], str) or not re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]', value[part]) for part in ('from', 'to')):
                raise ValueError('Send on, from and to, with times as HH:MM.')
            return value
        if key.startswith('notify.') and key[7:] in events:
            if not isinstance(value, list) or len(value) != 2 or any(type(item) is not bool for item in value):
                raise ValueError('Send two switches: in Evlet and email.')
            return [value[0], True] if events[key[7:]] else value
        if portal or key not in APPEARANCE:
            raise ValueError('Choose a known preference.')
        if key in CHOICES and value not in CHOICES[key]:
            raise ValueError('Choose one of the options.')
        if key in ('counts', 'enter_next', 'keep_unsaved') and type(value) is not bool:
            raise ValueError('Use true or false.')
        if key == 'start' and (not isinstance(value, str) or value and value != 'docs' and (
                value not in self.business.registry.models or not self.business.policy.allows(user, self.business.registry.models[value], 'read'))):
            raise ValueError('Choose a page you can open.')
        if key == 'tz':
            try:
                if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_]+(/[A-Za-z_+-]+){0,2}', value):
                    raise ValueError()
                ZoneInfo(value)
            except Exception:
                raise ValueError('Choose a time zone.') from None
        return value

    # ---------- app settings ----------

    def app_state(self, user, app):
        entry = self.apps[app]
        declared, mine = entry['settings'], entry['preferences']
        values = self.shared(app) if declared else {}
        with self.database._connection() as connection:
            stored = {row['key']: row['value'] for row in connection.run(qb.select('evlet_user_prefs', 'key', 'value').where(user_id=user.id, scope=app))}
        sections = [{'title': title, 'fields': [{**_schema(name, declared.__fields__[name]),
                                                 'value': declared.__fields__[name].serialize(values.get(name, _default(declared.__fields__[name])))} for name in names]}
                    for title, names in declared.__sections__.items()] if declared else []
        preferences = [{**_schema(name, field), 'value': field.serialize(_stored(field, stored[name]) if name in stored else _default(field))}
                       for name, field in mine.__fields__.items()] if mine else []
        return {'id': app, 'title': app.replace('_', ' ').title(), 'editors': list(declared.editors) if declared else [],
                'can_edit': self.can_edit(user, app), 'sections': sections, 'preferences': preferences}

    def _clean_values(self, fields, values):
        errors, clean = {}, {}
        for key, value in values.items():
            field = fields.get(key)
            if field is None:
                errors[key] = 'Choose a known setting.'
                continue
            if field.read_only:
                errors[key] = 'This setting is read only.'
                continue
            try:
                clean[key] = None if value is None else field.clean(value)
            except (ValueError, TypeError) as exc:
                errors[key] = str(exc) if isinstance(exc, ValueError) else 'Use a valid value.'
        if errors:
            raise ValidationError(errors)
        return clean

    def save_app(self, user, app, body):
        from .controls import audit
        declared = self.apps[app]['settings']
        if declared is None:
            raise AccessError(404, 'Route not found')
        if not self.can_edit(user, app):
            raise AccessError(403, 'You can see these settings but not change them.')
        values = self._body(body, ('values',)).get('values')
        if not isinstance(values, dict) or not values:
            raise ValidationError({'values': 'Send the settings to change.'})
        clean = self._clean_values(declared.__fields__, values)
        now, saved = int(time.time()), 0
        with self.database.transaction() as tx:
            for key, value in clean.items():
                field = declared.__fields__[key]
                row = tx.connection.run(qb.select('evlet_app_settings', 'value').where(app=app, key=key)).one()
                before = None if row is None else json.loads(row['value'])
                after = None if value is None or value == _default(field) else field.serialize(value)
                if before == after:
                    continue
                if after is None:
                    tx.connection.run(qb.delete('evlet_app_settings').where(app=app, key=key))
                else:
                    tx.connection.run(qb.insert('evlet_app_settings', app=app, key=key, value=json.dumps(after), changed_by=user.id, changed_at=now)
                                      .on_conflict('app', 'key').do_update(value=qb.col('excluded.value'), changed_by=qb.col('excluded.changed_by'),
                                                                            changed_at=qb.col('excluded.changed_at')))
                audit(tx.connection, user.username, 'Settings', f'{app}.{key}', 'Reset to the default' if after is None else 'Changed', before, after)
                saved += 1
            if saved:
                self.business.controls.publish(tx)
        self.business.controls.refresh()
        return {**self.app_state(user, app), 'saved': saved}

    def save_preferences(self, user, app, body):
        declared = self.apps[app]['preferences']
        if declared is None or not isinstance(body, dict) or not body:
            raise ValidationError({'_form': 'Send the preferences to change.'})
        clean = self._clean_values(declared.__fields__, body)
        with self.database.transaction() as tx:
            for key, value in clean.items():
                if value is None:
                    tx.connection.run(qb.delete('evlet_user_prefs').where(user_id=user.id, scope=app, key=key))
                else:
                    tx.connection.run(qb.insert('evlet_user_prefs', user_id=user.id, scope=app, key=key, value=json.dumps(declared.__fields__[key].serialize(value)))
                                      .on_conflict('user_id', 'scope', 'key').do_update(value=qb.col('excluded.value')))
        return {'saved': sorted(clean)}

    # ---------- two-step: settings ----------

    def codes(self, row):
        sealed = base64.b64decode(row['backup_codes'])
        return json.loads(vault.open(self.key, sealed, f'backup_codes:{row["user_id"]}'))

    def _store_codes(self, connection, user_id, hashes=None):
        codes = [] if hashes is not None else ['-'.join(''.join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(2)) for _ in range(8)]
        hashes = hashes if hashes is not None else [_code_hash(user_id, code) for code in codes]
        connection.run(qb.update('evlet_user_profile', backup_codes=self._sealed_codes(connection, user_id, hashes)).where(user_id=user_id))
        return codes

    def _sealed_codes(self, connection, user_id, hashes):
        sealed = self.seal(connection, json.dumps(hashes).encode(), f'backup_codes:{user_id}')
        return base64.b64encode(sealed).decode()

    def _spend_backup_code(self, connection, user_id, before, hashes):
        """Write back the backup codes left after one was used, only if nobody else changed them since we read
        them. Two requests racing with the same code then cannot both use it."""
        after = self._sealed_codes(connection, user_id, hashes)
        changed = connection.run(qb.update('evlet_user_profile', backup_codes=after)
                                 .where(qb.eq('user_id', user_id), qb.eq('backup_codes', qb.value(before)))).rowcount
        return changed == 1

    def _need_vault(self):
        if not self.possible():
            raise AccessError(409, 'Two-step sign-in needs the cryptography package and a secret key on the server. Ask your admin.')
        if not self.ready():
            raise AccessError(503, KEY_PROBLEM)

    def _uri(self, user, raw):
        issuer = (self.business.application or {}).get('name', 'Evlet')
        return f'otpauth://totp/{quote(issuer + ":" + user.username)}?secret={_b32(raw)}&issuer={quote(issuer)}&digits=6&period=30'

    @staticmethod
    def _agent(scope):
        agent = dict(scope.get('headers', [])).get(b'user-agent', b'').decode('latin1')
        return 'the command line' if 'evlet-connector' in agent else 'a phone browser' if re.search('iPhone|Android', agent) else 'a browser' if agent else 'an unknown device'

    # ---------- wrong codes and passwords, per account ----------

    @staticmethod
    def _tries(connection, key):
        now = int(time.time())
        connection.run(qb.delete('evlet_login_limits').where(qb.le('resets', now)))
        row = connection.run(qb.select('evlet_login_limits', 'attempts').where(client=key)).one()
        return row['attempts'] if row else 0

    @staticmethod
    def _miss(connection, key):
        connection.run(qb.insert('evlet_login_limits', client=key, attempts=1, resets=int(time.time()) + CODE_WINDOW)
                       .on_conflict('client').do_update(attempts=qb.col('evlet_login_limits.attempts') + 1))

    @staticmethod
    def _code_key(user_id):
        return hashlib.sha256(f'code:{user_id}'.encode()).hexdigest()

    def _use_code(self, connection, user_id, profile, code):
        """Check a code from the app (each once) or an unused backup code, and record its use."""
        code = code.strip().lower().replace(' ', '') if isinstance(code, str) else ''
        raw = vault.open(self.key, profile['two_step_secret'], f'two_step:{user_id}')
        if re.fullmatch(r'[0-9]{6}', code):
            counter = check_totp(raw, code, profile['two_step_last'] if profile['two_step_last'] is not None else -1)
            # Conditional, so two requests racing with the same code cannot both use it.
            if counter is not None and connection.run(qb.update('evlet_user_profile', two_step_last=counter).where(
                    qb.or_(qb.is_null('two_step_last'), qb.lt('two_step_last', counter)), user_id=user_id)).rowcount == 1:
                return True
        elif re.fullmatch(r'[a-z0-9]{4}-?[a-z0-9]{4}', code) and profile['backup_codes']:
            hashes = self.codes(profile)
            digest = _code_hash(user_id, code if '-' in code else code[:4] + '-' + code[4:])
            if digest in hashes:
                hashes.remove(digest)
                return self._spend_backup_code(connection, user_id, profile['backup_codes'], hashes)
        return False

    def _check_password(self, user, password, field):
        auth, key = self.business.auth, hashlib.sha256(f'password:{user.id}'.encode()).hexdigest()
        with hashing_slot():
            with self.database.transaction() as tx:
                if self._tries(tx.connection, key) >= auth.lockout:
                    raise AccessError(429, 'Too many tries. Try again in 15 minutes.')
                self._miss(tx.connection, key)
                stored = tx.connection.run(qb.select('evlet_users', 'password').where(id=user.id)).one()['password']
            try:
                if not stored.startswith('scrypt:'):
                    raise ValueError()
                candidate = _scrypt(_encode(password, 1), stored.split(':')[1])
            except (ValueError, UnicodeError):
                candidate = ''
        if not hmac.compare_digest(candidate, stored):
            raise ValidationError({field: 'Your current password is not right.' if field == 'current' else 'Your password is not right.'})
        with self.database.transaction() as tx:
            tx.connection.run(qb.delete('evlet_login_limits').where(client=key))
        return stored

    def reauthenticate(self, scope, user, body):
        """Sensitive personal changes: with two-step sign-in on, a current code or a backup code only;
        without it, the password."""
        if self.two_step_on(user.id):
            self.check_code(scope, user, body.get('code'))
            return
        password = body.get('password')
        if not isinstance(password, str) or not password:
            raise ValidationError({'password': 'Enter your password.'})
        self._check_password(user, password, 'password')

    def check_code(self, scope, user, code):
        """Check a code from the app or a backup code against the person's two-step sign-in, counting wrong
        codes per account. A right code also marks this session as just verified (see recently_verified)."""
        if not isinstance(code, str) or not code.strip():
            raise ValidationError({'code': ENTER_CODE})
        with self.database.transaction() as tx:
            profile = self.profile(tx.connection, user.id)
            if profile['two_step_secret'] is None:
                raise ValidationError({'code': 'Turn on two-step sign-in first.'})
            key = self._code_key(user.id)
            if self._tries(tx.connection, key) >= CODE_TRIES:
                raise AccessError(429, TOO_MANY_CODES)
            right = self._use_code(tx.connection, user.id, profile, code)
            if right:
                tx.connection.run(qb.delete('evlet_login_limits').where(client=key))
                self._mark_verified(tx.connection, scope, user)
            else:
                self._miss(tx.connection, key)
        if not right:
            raise ValidationError({'code': ENTER_CODE})

    def _mark_verified(self, connection, scope, user):
        """Only a session that finished two-step sign-in is refreshed; a password-only session stays one until the person signs in again."""
        connection.run(qb.update('evlet_sessions', two_step=int(time.time()))
                       .where(qb.not_null('two_step'), token=qb.value(self.business.auth.token(scope)), user_id=user.id))

    def verify(self, scope, user, body):
        """POST /api/_settings/me/two-step/verify {code}: a fresh code for this session, asked before changes in Controls.
        The session must have finished two-step sign-in."""
        from .controls import TWO_STEP
        self._need_vault()
        if not user.two_step:
            raise AccessError(403, TWO_STEP)
        self.check_code(scope, user, self._body(body, ('code',)).get('code'))
        now = int(time.time())
        return {'verified': now, 'until': now + VERIFIED_SECONDS}

    def recently_verified(self, user):
        """Before a change in Controls: a person with two-step sign-in on gives a code at most every 10 minutes
        per session (signing in with a code counts). Without two-step sign-in nothing is asked here."""
        from .controls import TWO_STEP
        if user.access is not None or not self.two_step_on(user.id):
            return
        if not user.two_step:
            raise AccessError(403, TWO_STEP)
        if user.verified is None or int(time.time()) - user.verified > VERIFIED_SECONDS:
            raise AccessError(403, VERIFY)

    # ---------- password and sessions ----------

    def change_password(self, scope, user, body):
        from .controls import audit
        body = self._body(body, ('current', 'new', 'code', 'revoke_keys'))
        current, new, revoke = body.get('current'), body.get('new'), body.get('revoke_keys', False)
        if type(revoke) is not bool:
            raise ValidationError({'revoke_keys': 'Use true or false.'})
        auth = self.business.auth
        minimum = auth.minimum(user.role)
        if not isinstance(current, str) or not current:
            raise ValidationError({'current': 'Enter your current password.'})
        if not isinstance(new, str) or len(new) < minimum:
            raise ValidationError({'new': too_short(minimum)})
        if new == current:
            raise ValidationError({'new': "Choose a password you haven't used here before."})
        stored = self._check_password(user, current, 'current')
        if self.two_step_on(user.id):
            self.check_code(scope, user, body.get('code'))
        try:
            hashed = password_hash(new, minimum=minimum)
        except ValidationError:
            raise ValidationError({'new': f'Use between {minimum} and 128 characters.'}) from None
        token = auth.token(scope)
        with self.database.transaction() as tx:
            if tx.connection.run(qb.select('evlet_users', 'password').where(id=user.id)).one()['password'] != stored:
                raise AccessError(409, 'Your password changed meanwhile. Reload the page.')
            keys = tx.connection.run(qb.select('evlet_api_keys', qb.count()).where(user_id=user.id, revoked_at=None)).value() if revoke else 0
            tx.connection.run(qb.update('evlet_users', password=hashed).where(id=user.id))
            auth.end_sessions(tx.connection, user.id, keep=token, keys=revoke)
            audit(tx.connection, user.username, 'Users', user.username, 'Password changed in Settings · Signed out other devices and AI assistants'
                  + (f' · Revoked {keys} API key{"s" if keys != 1 else ""}' if revoke else ''))
        return {'changed': True, 'revoked_keys': keys}

    def first_password(self, scope, user, body):
        """POST /api/auth/password {password}: the first password of an account that has none (the Administrator after a
        development sign-in link). When its role needs two-step sign-in that is not set up yet, this session ends and the
        answer is the set-up ticket, as at sign-in; finishing it starts a new session."""
        from .controls import audit
        auth = self.business.auth
        if user.access is not None:
            raise AccessError(403, 'API keys cannot be used here. Sign in instead.')
        password = self._body(body, ('password',)).get('password')
        minimum = auth.minimum(user.role)
        if not isinstance(password, str) or len(password) < minimum:
            raise ValidationError({'password': too_short(minimum)})
        hashed = password_hash(password, minimum=minimum)
        realm = auth.realm(scope)
        with self.database.transaction() as tx:
            account = tx.connection.run(qb.select('evlet_users').where(id=user.id)).one()
            if account is None or account['password'] != '':
                raise AccessError(409, PASSWORD_SET)
            tx.connection.run(qb.update('evlet_users', password=hashed).where(id=user.id))
            audit(tx.connection, user.username, 'Users', user.username, 'First password set')
            account = tx.connection.run(qb.select('evlet_users').where(id=user.id)).one()
            challenge = self.challenge(tx, account, realm) if not self.two_step_on_row(tx.connection, user.id) else None
            if challenge is None:
                return {'password': True}, []
            tx.connection.run(qb.delete('evlet_sessions').where(token=qb.value(auth.token(scope))))
        return challenge, [auth.cookie('', realm)]

    @staticmethod
    def two_step_on_row(connection, user_id):
        row = connection.run(qb.select('evlet_user_profile', 'two_step_secret').where(user_id=user_id)).one()
        return row is not None and row['two_step_secret'] is not None

    def end_sessions(self, scope, user, session_id):
        token = self.business.auth.token(scope)
        with self.database.transaction() as tx:
            if session_id is None:
                ended = tx.connection.run(qb.delete('evlet_sessions').where(qb.ne('token', qb.value(token)), user_id=user.id)).rowcount
            else:
                if token.startswith(session_id):
                    raise ValidationError({'session': 'Use Sign out to end this session.'})
                ended = tx.connection.run(qb.delete('evlet_sessions').where(qb.eq(qb.substr('token', 1, 16), session_id), user_id=user.id)).rowcount
        return {'ended': ended}

    # ---------- two-step: settings ----------

    def start_two_step(self, scope, user, body):
        self._need_vault()
        with self.database._connection() as connection:
            if self.profile_read(connection, user.id)['two_step_secret'] is not None:
                raise ValidationError({'_form': 'Two-step sign-in is already on.'})
        self.reauthenticate(scope, user, body or {})
        raw = secrets.token_bytes(20)
        with self.database.transaction() as tx:
            self.profile(tx.connection, user.id)
            tx.connection.run(qb.update('evlet_user_profile', two_step_pending=self.seal(tx.connection, raw, f'two_step_pending:{user.id}')).where(user_id=user.id))
        return {'secret': _b32(raw), 'uri': self._uri(user, raw)}

    @staticmethod
    def profile_read(connection, user_id):
        return connection.run(qb.select('evlet_user_profile').where(user_id=user_id)).one() or {'two_step_secret': None}

    def _enrol(self, connection, scope, account, raw, counter):
        from .controls import audit
        agent = self._agent(scope)
        connection.run(qb.update('evlet_user_profile', two_step_secret=self.seal(connection, raw, f'two_step:{account["id"]}'), two_step_pending=None,
                                 two_step_since=int(time.time()), two_step_last=counter, two_step_agent=agent).where(user_id=account['id']))
        audit(connection, account['username'], 'Users', account['username'], f'Two-step sign-in set up from {agent}')
        return self._store_codes(connection, account['id'])

    def confirm_two_step(self, scope, user, body):
        self._need_vault()
        code = str(self._body(body, ('code',)).get('code', '')).strip()
        with self.database.transaction() as tx:
            row = self.profile(tx.connection, user.id)
            if row['two_step_pending'] is None:
                raise ValidationError({'code': 'Start the set-up again.'})
            raw = vault.open(self.key, row['two_step_pending'], f'two_step_pending:{user.id}')
            counter = check_totp(raw, code) if re.fullmatch(r'[0-9]{6}', code) else None
            if counter is None:
                raise ValidationError({'code': 'Enter the 6 digits from the app.'})
            codes = self._enrol(tx.connection, scope, {'id': user.id, 'username': user.username}, raw, counter)
        return {'codes': codes}

    def new_codes(self, scope, user, body):
        self._need_vault()
        with self.database._connection() as connection:
            if self.profile_read(connection, user.id)['two_step_secret'] is None:
                raise ValidationError({'_form': 'Turn on two-step sign-in first.'})
        self.reauthenticate(scope, user, body or {})
        with self.database.transaction() as tx:
            return {'codes': self._store_codes(tx.connection, user.id)}

    def turn_off(self, scope, user, body):
        from .controls import audit
        if self.business.controls.two_step_required(user.role):
            raise AccessError(409, 'Required for your role')
        self.reauthenticate(scope, user, body or {})
        with self.database.transaction() as tx:
            clear_two_step(tx.connection, user.id)
            audit(tx.connection, user.username, 'Users', user.username, 'Two-step sign-in turned off')
        return {'on': False}

    # ---------- two-step: sign-in (LocalAuth.second_step) ----------

    def challenge(self, tx, account, realm):
        """After a correct password: None to sign in, or a ticket asking for a code (or for set-up when the role requires it).
        Set-up at sign-in is trust on first use: whoever signs in first with the password binds the authenticator app."""
        row = self.profile(tx.connection, account['id'])
        enabled = row['two_step_secret'] is not None
        if not enabled and not self.required(account['role'], realm):
            return None
        if not self.ready(tx.connection):
            raise AccessError(503, KEY_PROBLEM)
        ticket, now = secrets.token_urlsafe(32), int(time.time())
        answer, secret = {'two_step': 'code', 'ticket': ticket}, None
        if not enabled:
            raw = secrets.token_bytes(20)
            secret = self.seal(tx.connection, raw, f'two_step_ticket:{account["id"]}')
            user = User(account['id'], account['username'], account['role'])
            answer = {'two_step': 'setup', 'ticket': ticket, 'secret': _b32(raw), 'uri': self._uri(user, raw)}
        tx.connection.run(qb.delete('evlet_two_step_tickets').where(qb.or_(qb.le('expires', now), qb.eq('user_id', account['id']))))
        tx.connection.run(qb.insert('evlet_two_step_tickets', token_hash=hashlib.sha256(ticket.encode()).hexdigest(), user_id=account['id'], realm=realm,
                                    expires=now + TICKET_SECONDS, attempts=0, secret=secret))
        return answer

    def finish(self, scope, body):
        """POST /api/auth/login {ticket, code}: a code from the app, or an unused backup code, starts a verified session."""
        auth = self.business.auth
        ticket, code = body.get('ticket'), body.get('code')
        if not isinstance(ticket, str) or not isinstance(code, str):
            raise AccessError(401, SIGN_IN_AGAIN)
        code, now = code.strip().lower().replace(' ', ''), int(time.time())
        realm, wrong = auth.realm(scope), None
        try:
            with self.database.transaction() as tx:
                row = tx.connection.run(qb.select('evlet_two_step_tickets').where(qb.gt('expires', now), token_hash=hashlib.sha256(ticket.encode()).hexdigest())).one()
                account = row and tx.connection.run(
                    qb.select(qb.table('evlet_users', 'u'), qb.all_columns('u'), qb.named('r.realm', 'role_realm'))
                    .join(qb.table('evlet_roles', 'r'), qb.eq('r.id', qb.col('u.role')), left=True)
                    .where(qb.eq('u.id', row['user_id']))).one()
                if row is None or row['realm'] != realm or not account or not account['active'] or account['role_realm'] != realm:
                    raise AccessError(401, SIGN_IN_AGAIN)
                limit = self._code_key(account['id'])
                if self._tries(tx.connection, limit) >= CODE_TRIES:
                    raise AccessError(429, TOO_MANY_CODES)
                profile = self.profile(tx.connection, account['id'])
                codes, right = None, False
                if row['secret'] is not None:
                    raw = vault.open(self.key, row['secret'], f'two_step_ticket:{account["id"]}')
                    counter = check_totp(raw, code) if re.fullmatch(r'[0-9]{6}', code) else None
                    if counter is not None:
                        codes, right = self._enrol(tx.connection, scope, account, raw, counter), True
                elif profile['two_step_secret'] is not None:
                    right = self._use_code(tx.connection, account['id'], profile, code)
                if not right:
                    self._miss(tx.connection, limit)
                    attempts = row['attempts'] + 1
                    if attempts >= TICKET_TRIES:
                        tx.connection.run(qb.delete('evlet_two_step_tickets').where(token_hash=row['token_hash']))
                        wrong = 'That code is not right. Sign in again.'
                    else:
                        tx.connection.run(qb.update('evlet_two_step_tickets', attempts=attempts).where(token_hash=row['token_hash']))
                        wrong = 'That code is not right. Try again.'
                else:
                    tx.connection.run(qb.delete('evlet_two_step_tickets').where(token_hash=row['token_hash']))
                    address = client_network(auth.client_address(scope))
                    pair = hashlib.sha256(('account:' + address + ':' + account['username']).encode()).hexdigest()
                    tx.connection.run(qb.delete('evlet_login_limits').where(qb.in_('client', (pair, limit))))
                    payload, headers = auth.start_session(tx, scope, account, realm, verified=True)
                    if codes:
                        payload['backup_codes'] = codes
                    return payload, headers
        except vault.SecretKeyError:
            raise AccessError(503, KEY_PROBLEM) from None
        raise AccessError(401, wrong)


def too_short(minimum):
    return 'Enter a password.' if minimum <= 1 else f'Your workspace needs at least {minimum} characters.'


def clear_two_step(connection, user_id):
    connection.run(qb.update('evlet_user_profile', two_step_secret=None, backup_codes=None, two_step_since=None, two_step_last=None,
                             two_step_pending=None, two_step_agent='').where(user_id=user_id))
    connection.run(qb.delete('evlet_two_step_tickets').where(user_id=user_id))


def reset_two_step(connection, actor, user_id, username):
    """Clear a person's two-step sign-in and revoke every credential, API keys included; they set it up again at their next sign-in. Audited."""
    from .auth import LocalAuth
    from .controls import audit
    clear_two_step(connection, user_id)
    LocalAuth.end_sessions(connection, user_id, keys=True)
    audit(connection, actor, 'Users', username, 'Two-step sign-in reset. Sessions, AI assistants and API keys ended. They set up two-step again at their next sign-in.')
