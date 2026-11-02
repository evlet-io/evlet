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

from . import secrets as vault
from .auth import AccessError, User, _encode, _scrypt, client_network, hashing_slot, password_hash
from .fields import MISSING, Email, Field
from .models import ValidationError

logger = logging.getLogger(__name__)

PAGES = ('profile', 'appearance', 'security', 'notifications', 'email', 'ai')
PORTAL_PAGES = ('profile', 'security', 'notifications')
APPEARANCE = {
    'theme': 'light', 'density': 'comfortable', 'text': 'default', 'start': '', 'counts': False, 'lang': 'en',
    'tz': 'Asia/Dubai', 'date': 'dd-MM-yyyy', 'num': '1,234.56', 'week': 'mon',
}
CHOICES = {
    'theme': ('light',), 'density': ('comfortable', 'compact'), 'text': ('default', 'large'), 'lang': ('en', 'ar'),
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
            row = connection.execute('SELECT value FROM evlet_user_prefs WHERE user_id=? AND scope=? AND key=?', (self._user.id, app, key)).fetchone()
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
        return getattr(self.business, 'secret_key', None)

    def initialize(self):
        with self.database.transaction() as tx:
            for statement in (
                'CREATE TABLE IF NOT EXISTS evlet_user_profile (user_id INTEGER PRIMARY KEY REFERENCES evlet_users(id) ON DELETE CASCADE, '
                "full_name TEXT NOT NULL DEFAULT '', short_name TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', "
                "email TEXT NOT NULL DEFAULT '', photo_file INTEGER, signature TEXT NOT NULL DEFAULT '', two_step_secret BLOB, backup_codes TEXT, "
                'two_step_since INTEGER, two_step_last INTEGER, two_step_pending BLOB)',
                'CREATE TABLE IF NOT EXISTS evlet_user_prefs (user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, scope TEXT NOT NULL, '
                'key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (user_id, scope, key))',
                'CREATE TABLE IF NOT EXISTS evlet_email_tokens (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, '
                'new_email TEXT NOT NULL, expires INTEGER NOT NULL)',
                'CREATE TABLE IF NOT EXISTS evlet_app_settings (app TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, changed_by INTEGER, changed_at INTEGER, '
                'PRIMARY KEY (app, key))',
                'CREATE TABLE IF NOT EXISTS evlet_two_step_tickets (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES evlet_users(id) ON DELETE CASCADE, '
                'realm TEXT NOT NULL, expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, secret BLOB)',
            ):
                tx.connection.execute(statement)
        self.business.auth.second_step = self

    # ---------- shared values ----------

    def shared(self, app):
        """Stored "For everyone" values of an app, read again when the config version moves."""
        controls = self.business.controls
        version, cache = self._cache
        if version is None or version != controls.version:
            cache = {}
            with self.database._connection() as connection:
                for row in connection.execute('SELECT app, key, value FROM evlet_app_settings'):
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

    def core(self, user_id, connection=None):
        def read(db):
            return {row['key']: json.loads(row['value']) for row in db.execute("SELECT key, value FROM evlet_user_prefs WHERE user_id=? AND scope='core'", (user_id,))}
        if connection is not None:
            return read(connection)
        with self.database._connection() as db:
            return read(db)

    def profile(self, connection, user_id):
        connection.execute('INSERT OR IGNORE INTO evlet_user_profile(user_id) VALUES (?)', (user_id,))
        return connection.execute('SELECT * FROM evlet_user_profile WHERE user_id=?', (user_id,)).fetchone()

    def required(self, role, realm='desk'):
        return realm == 'desk' and role in self.business.controls.settings['security.two_step_roles'] and vault.available(self.key)

    def two_step_on(self, user_id):
        with self.database._connection() as connection:
            row = connection.execute('SELECT two_step_secret FROM evlet_user_profile WHERE user_id=?', (user_id,)).fetchone()
        return row is not None and row['two_step_secret'] is not None

    def me(self, scope, user):
        portal = self.portal(scope, user)
        with self.database.transaction() as tx:
            row = self.profile(tx.connection, user.id)
            prefs = self.core(user.id, tx.connection)
            sessions = tx.connection.execute('SELECT token, realm, started, expires, agent FROM evlet_sessions WHERE user_id=? AND expires>? ORDER BY started DESC',
                                             (user.id, int(time.time()))).fetchall()
            codes = len(self.codes(row)) if row['backup_codes'] else 0
        current = self.business.auth.token(scope)
        events = [{'group': group, 'items': [{'key': key, 'label': label, 'locked': locked,
                                              'channels': prefs.get('notify.' + key, [True, True])} for key, label, locked in items]}
                  for group, items in EVENTS if not portal or group in PORTAL_EVENTS]
        apps = []
        for app in self.visible_apps(scope, user):
            entry = self.apps[app]
            labels = [_label(name, field) for kind in ('settings', 'preferences') if entry[kind] for name, field in entry[kind].__fields__.items()]
            apps.append({'id': app, 'title': app.replace('_', ' ').title(), 'can_edit': self.can_edit(user, app), 'labels': labels})
        available = vault.available(self.key)
        controls = self.business.controls
        return {
            'user': user.public(), 'portal': portal, 'pages': list(self.pages(scope, user)), 'apps': apps,
            'last_page': prefs.get('last_page', 'profile'),
            'controls': not portal and controls.routes is not None and controls.allows(scope, user),
            'development': getattr(getattr(self.business, 'routes', None), 'primary', None) is None,
            'profile': {key: row[key] for key in ('full_name', 'short_name', 'title', 'phone', 'email', 'signature')} | {'photo': row['photo_file'] is not None},
            'prefs': {key: prefs.get(key, value) for key, value in APPEARANCE.items()},
            'notifications': {'events': events, 'quiet': prefs.get('quiet', QUIET)},
            'security': {
                'password_min': self.business.auth.password_min,
                'two_step': {'on': row['two_step_secret'] is not None, 'since': row['two_step_since'], 'codes': codes,
                             'required': self.required(user.role, 'portal' if portal else 'desk'), 'available': available},
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
                ('POST', '/me/two-step'): lambda: self.start_two_step(user),
                ('POST', '/me/two-step/confirm'): lambda: self.confirm_two_step(user, body),
                ('POST', '/me/two-step/codes'): lambda: self.new_codes(user),
                ('DELETE', '/me/two-step'): lambda: self.turn_off(user),
            })
        match = re.fullmatch(r'/me/sessions/([0-9a-f]{16})', path)
        if match and method == 'DELETE':
            return 200, self.end_sessions(scope, user, match[1]), []
        handler = routes.get((method, path))
        if handler is None:
            raise AccessError(404, 'Route not found')
        result = handler()
        return result if isinstance(result, tuple) else (200, result, [])

    @staticmethod
    def _body(body, allowed):
        if not isinstance(body, dict) or not body or set(body) - set(allowed):
            raise ValidationError({'_form': 'Send ' + ', '.join(allowed)})
        return body

    # ---------- profile ----------

    def save_profile(self, scope, user, body):
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
                tx.connection.execute(f'UPDATE evlet_user_profile SET {key}=? WHERE user_id=?', (value if key == 'signature' else value.strip(), user.id))
        return self.me(scope, user)['profile']

    def photo(self, user):
        from .business import Download
        with self.database._connection() as connection:
            row = connection.execute('SELECT a.* FROM evlet_user_profile p JOIN evlet_attachments a ON a.id=p.photo_file WHERE p.user_id=?', (user.id,)).fetchone()
        if row is None:
            raise AccessError(404, 'No photo')
        content = self.business.storage.read(row)
        kind = next((name for name, magic in PHOTO_TYPES.items() if content.startswith(magic)), 'application/octet-stream')
        return 200, Download(content), [(b'content-type', kind.encode()), (b'content-security-policy', b"default-src 'none'; sandbox")]

    def save_photo(self, user, body):
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
            tx.connection.execute('UPDATE evlet_user_profile SET photo_file=? WHERE user_id=?', (file, user.id))
            if old is not None:
                tx.connection.execute("DELETE FROM evlet_attachments WHERE id=? AND model='evlet_user_photo'", (old,))
        return {'photo': True}

    def remove_photo(self, user):
        with self.database.transaction() as tx:
            old = self.profile(tx.connection, user.id)['photo_file']
            tx.connection.execute('UPDATE evlet_user_profile SET photo_file=NULL WHERE user_id=?', (user.id,))
            if old is not None:
                tx.connection.execute("DELETE FROM evlet_attachments WHERE id=? AND model='evlet_user_photo'", (old,))
        return {'photo': False}

    def change_email(self, scope, user, body):
        address = self._body(body, ('email',)).get('email')
        try:
            address = Email().clean(address.strip() if isinstance(address, str) else address)
        except ValueError:
            address = None
        if not address:
            raise ValidationError({'email': 'Enter a valid email address.'})
        token, now = secrets.token_urlsafe(32), int(time.time())
        with self.database.transaction() as tx:
            tx.connection.execute('DELETE FROM evlet_email_tokens WHERE user_id=? OR expires<=?', (user.id, now))
            tx.connection.execute('INSERT INTO evlet_email_tokens(token_hash,user_id,new_email,expires) VALUES (?,?,?,?)',
                                  (hashlib.sha256(token.encode()).hexdigest(), user.id, address, now + 86400))
        from .routing import host_name
        host = host_name(dict(scope.get('headers', [])).get(b'host', b'')) or 'localhost'
        routes = self.business.routes
        origin = f'https://{host}' if routes.primary else f'http://{host}:{routes.port}'
        link = f'{origin}/settings/profile?confirm={token}'
        # Until outgoing mail is set up, the link is logged; in development the page shows it too.
        logger.info('Email change for %s to %s: %s', user.username, address, link)
        return {'sent': address, **({'link': link} if routes.primary is None else {})}

    def confirm_email(self, user, body):
        token = self._body(body, ('token',)).get('token')
        now = int(time.time())
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_email_tokens WHERE token_hash=? AND user_id=? AND expires>?',
                                        (hashlib.sha256(str(token).encode()).hexdigest(), user.id, now)).fetchone()
            if row is None:
                raise ValidationError({'token': 'This link has expired or was already used. Ask for a new one.'})
            self.profile(tx.connection, user.id)
            tx.connection.execute('UPDATE evlet_user_profile SET email=? WHERE user_id=?', (row['new_email'], user.id))
            tx.connection.execute('DELETE FROM evlet_email_tokens WHERE user_id=?', (user.id,))
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
                tx.connection.execute("INSERT INTO evlet_user_prefs(user_id,scope,key,value) VALUES (?,'core',?,?) ON CONFLICT(user_id,scope,key) DO UPDATE SET value=excluded.value",
                                      (user.id, key, json.dumps(value)))
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
            raise ValueError('Dark theme is coming in a later release.' if key == 'theme' else 'Choose one of the options.')
        if key == 'counts' and type(value) is not bool:
            raise ValueError('Use true or false.')
        if key == 'start' and (not isinstance(value, str) or value and value != 'docs' and value not in self.business.registry.models):
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
            stored = {row['key']: row['value'] for row in connection.execute('SELECT key, value FROM evlet_user_prefs WHERE user_id=? AND scope=?', (user.id, app))}
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
                row = tx.connection.execute('SELECT value FROM evlet_app_settings WHERE app=? AND key=?', (app, key)).fetchone()
                before = None if row is None else json.loads(row['value'])
                after = None if value is None or value == _default(field) else field.serialize(value)
                if before == after:
                    continue
                if after is None:
                    tx.connection.execute('DELETE FROM evlet_app_settings WHERE app=? AND key=?', (app, key))
                else:
                    tx.connection.execute('INSERT INTO evlet_app_settings(app,key,value,changed_by,changed_at) VALUES (?,?,?,?,?) ON CONFLICT(app,key) DO UPDATE SET '
                                          'value=excluded.value, changed_by=excluded.changed_by, changed_at=excluded.changed_at', (app, key, json.dumps(after), user.id, now))
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
                    tx.connection.execute('DELETE FROM evlet_user_prefs WHERE user_id=? AND scope=? AND key=?', (user.id, app, key))
                else:
                    tx.connection.execute('INSERT INTO evlet_user_prefs(user_id,scope,key,value) VALUES (?,?,?,?) ON CONFLICT(user_id,scope,key) DO UPDATE SET value=excluded.value',
                                          (user.id, app, key, json.dumps(declared.__fields__[key].serialize(value))))
        return {'saved': sorted(clean)}

    # ---------- password and sessions ----------

    def change_password(self, scope, user, body):
        from .controls import audit
        body = self._body(body, ('current', 'new'))
        current, new = body.get('current'), body.get('new')
        if not isinstance(current, str) or not current:
            raise ValidationError({'current': 'Enter your current password.'})
        if not isinstance(new, str) or len(new) < self.business.auth.password_min:
            raise ValidationError({'new': f'Your workspace needs at least {self.business.auth.password_min} characters.'})
        if new == current:
            raise ValidationError({'new': "Choose a password you haven't used here before."})
        auth, now = self.business.auth, int(time.time())
        key = hashlib.sha256(f'password:{user.id}'.encode()).hexdigest()
        with hashing_slot():
            with self.database.transaction() as tx:
                tx.connection.execute('DELETE FROM evlet_login_limits WHERE resets<=?', (now,))
                row = tx.connection.execute('SELECT attempts FROM evlet_login_limits WHERE client=?', (key,)).fetchone()
                if row and row['attempts'] >= auth.lockout:
                    raise AccessError(429, 'Too many tries. Try again in 15 minutes.')
                tx.connection.execute('INSERT INTO evlet_login_limits VALUES (?,1,?) ON CONFLICT(client) DO UPDATE SET attempts=evlet_login_limits.attempts+1', (key, now + 900))
                stored = tx.connection.execute('SELECT password FROM evlet_users WHERE id=?', (user.id,)).fetchone()['password']
            try:
                candidate = _scrypt(_encode(current, 1), stored.split(':')[1])
            except (ValueError, UnicodeError):
                candidate = ''
        if not hmac.compare_digest(candidate, stored):
            raise ValidationError({'current': 'Your current password is not right.'})
        try:
            hashed = password_hash(new, minimum=auth.password_min)
        except ValidationError:
            raise ValidationError({'new': f'Use between {auth.password_min} and 128 characters.'}) from None
        token = auth.token(scope)
        with self.database.transaction() as tx:
            if tx.connection.execute('SELECT password FROM evlet_users WHERE id=?', (user.id,)).fetchone()['password'] != stored:
                raise AccessError(409, 'Your password changed meanwhile. Reload the page.')
            tx.connection.execute('UPDATE evlet_users SET password=? WHERE id=?', (hashed, user.id))
            tx.connection.execute('DELETE FROM evlet_sessions WHERE user_id=? AND token<>?', (user.id, token))
            tx.connection.execute('DELETE FROM evlet_login_limits WHERE client=?', (key,))
            audit(tx.connection, user.username, 'Users', user.username, 'Password changed in Settings · Signed out other devices')
        return {'changed': True}

    def end_sessions(self, scope, user, session_id):
        token = self.business.auth.token(scope)
        with self.database.transaction() as tx:
            if session_id is None:
                ended = tx.connection.execute('DELETE FROM evlet_sessions WHERE user_id=? AND token<>?', (user.id, token)).rowcount
            else:
                if token.startswith(session_id):
                    raise ValidationError({'session': 'Use Sign out to end this session.'})
                ended = tx.connection.execute('DELETE FROM evlet_sessions WHERE user_id=? AND substr(token,1,16)=?', (user.id, session_id)).rowcount
        return {'ended': ended}

    # ---------- two-step: settings ----------

    def codes(self, row):
        sealed = base64.b64decode(row['backup_codes'])
        return json.loads(vault.open(self.key, sealed, f'backup_codes:{row["user_id"]}'))

    def _store_codes(self, connection, user_id):
        codes = ['-'.join(''.join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(2)) for _ in range(8)]
        sealed = vault.seal(self.key, json.dumps([_code_hash(user_id, code) for code in codes]).encode(), f'backup_codes:{user_id}')
        connection.execute('UPDATE evlet_user_profile SET backup_codes=? WHERE user_id=?', (base64.b64encode(sealed).decode(), user_id))
        return codes

    def _need_vault(self):
        if not vault.available(self.key):
            raise AccessError(409, 'Two-step sign-in needs the cryptography package and a secret key on the server. Ask your admin.')

    def _uri(self, user, raw):
        issuer = (self.business.application or {}).get('name', 'Evlet')
        return f'otpauth://totp/{quote(issuer + ":" + user.username)}?secret={_b32(raw)}&issuer={quote(issuer)}&digits=6&period=30'

    def start_two_step(self, user):
        self._need_vault()
        raw = secrets.token_bytes(20)
        with self.database.transaction() as tx:
            if self.profile(tx.connection, user.id)['two_step_secret'] is not None:
                raise ValidationError({'_form': 'Two-step sign-in is already on.'})
            tx.connection.execute('UPDATE evlet_user_profile SET two_step_pending=? WHERE user_id=?', (vault.seal(self.key, raw, f'two_step_pending:{user.id}'), user.id))
        return {'secret': _b32(raw), 'uri': self._uri(user, raw)}

    def confirm_two_step(self, user, body):
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
            tx.connection.execute('UPDATE evlet_user_profile SET two_step_secret=?, two_step_pending=NULL, two_step_since=?, two_step_last=? WHERE user_id=?',
                                  (vault.seal(self.key, raw, f'two_step:{user.id}'), int(time.time()), counter, user.id))
            codes = self._store_codes(tx.connection, user.id)
        return {'codes': codes}

    def new_codes(self, user):
        self._need_vault()
        with self.database.transaction() as tx:
            if self.profile(tx.connection, user.id)['two_step_secret'] is None:
                raise ValidationError({'_form': 'Turn on two-step sign-in first.'})
            return {'codes': self._store_codes(tx.connection, user.id)}

    def turn_off(self, user):
        if user.role in self.business.controls.settings['security.two_step_roles']:
            raise AccessError(409, 'Required for your role')
        with self.database.transaction() as tx:
            tx.connection.execute('UPDATE evlet_user_profile SET two_step_secret=NULL, backup_codes=NULL, two_step_since=NULL, two_step_last=NULL, two_step_pending=NULL WHERE user_id=?', (user.id,))
        return {'on': False}

    # ---------- two-step: sign-in (LocalAuth.second_step) ----------

    def challenge(self, tx, account, realm):
        """After a correct password: None to sign in, or a ticket asking for a code (or for set-up when the role requires it)."""
        row = self.profile(tx.connection, account['id'])
        enabled = row['two_step_secret'] is not None
        if not enabled and not self.required(account['role'], realm):
            return None
        if enabled and not vault.available(self.key):
            raise AccessError(503, 'Two-step sign-in is unavailable on this server. Ask your admin.')
        ticket, now = secrets.token_urlsafe(32), int(time.time())
        answer, secret = {'two_step': 'code', 'ticket': ticket}, None
        if not enabled:
            raw = secrets.token_bytes(20)
            secret = vault.seal(self.key, raw, f'two_step_ticket:{account["id"]}')
            user = User(account['id'], account['username'], account['role'])
            answer = {'two_step': 'setup', 'ticket': ticket, 'secret': _b32(raw), 'uri': self._uri(user, raw)}
        tx.connection.execute('DELETE FROM evlet_two_step_tickets WHERE expires<=? OR user_id=?', (now, account['id']))
        tx.connection.execute('INSERT INTO evlet_two_step_tickets(token_hash,user_id,realm,expires,attempts,secret) VALUES (?,?,?,?,0,?)',
                              (hashlib.sha256(ticket.encode()).hexdigest(), account['id'], realm, now + TICKET_SECONDS, secret))
        return answer

    def finish(self, scope, body):
        """POST /api/auth/login {ticket, code}: a code from the app, or an unused backup code, starts the session."""
        auth = self.business.auth
        ticket, code = body.get('ticket'), body.get('code')
        if not isinstance(ticket, str) or not isinstance(code, str):
            raise AccessError(401, SIGN_IN_AGAIN)
        code, now = code.strip().lower().replace(' ', ''), int(time.time())
        realm, wrong = auth.realm(scope), None
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_two_step_tickets WHERE token_hash=? AND expires>?', (hashlib.sha256(ticket.encode()).hexdigest(), now)).fetchone()
            account = row and tx.connection.execute('SELECT u.*, r.realm AS role_realm FROM evlet_users u LEFT JOIN evlet_roles r ON r.id=u.role WHERE u.id=?', (row['user_id'],)).fetchone()
            if row is None or row['realm'] != realm or not account or not account['active'] or account['role_realm'] != realm:
                raise AccessError(401, SIGN_IN_AGAIN)
            profile = self.profile(tx.connection, account['id'])
            codes, counter, used = None, None, False
            if row['secret'] is not None:
                raw = vault.open(self.key, row['secret'], f'two_step_ticket:{account["id"]}')
                counter = check_totp(raw, code) if re.fullmatch(r'[0-9]{6}', code) else None
                if counter is not None:
                    tx.connection.execute('UPDATE evlet_user_profile SET two_step_secret=?, two_step_since=?, two_step_last=?, two_step_pending=NULL WHERE user_id=?',
                                          (vault.seal(self.key, raw, f'two_step:{account["id"]}'), now, counter, account['id']))
                    codes = self._store_codes(tx.connection, account['id'])
            elif profile['two_step_secret'] is not None:
                raw = vault.open(self.key, profile['two_step_secret'], f'two_step:{account["id"]}')
                last = profile['two_step_last'] if profile['two_step_last'] is not None else -1
                counter = check_totp(raw, code, last) if re.fullmatch(r'[0-9]{6}', code) else None
                if counter is not None:
                    tx.connection.execute('UPDATE evlet_user_profile SET two_step_last=? WHERE user_id=?', (counter, account['id']))
                elif re.fullmatch(r'[a-z0-9]{4}-?[a-z0-9]{4}', code) and profile['backup_codes']:
                    hashes = self.codes(profile)
                    digest = _code_hash(account['id'], code if '-' in code else code[:4] + '-' + code[4:])
                    if digest in hashes:
                        hashes.remove(digest)
                        sealed = vault.seal(self.key, json.dumps(hashes).encode(), f'backup_codes:{account["id"]}')
                        tx.connection.execute('UPDATE evlet_user_profile SET backup_codes=? WHERE user_id=?', (base64.b64encode(sealed).decode(), account['id']))
                        used = True
            if counter is None and not used:
                attempts = row['attempts'] + 1
                if attempts >= TICKET_TRIES:
                    tx.connection.execute('DELETE FROM evlet_two_step_tickets WHERE token_hash=?', (row['token_hash'],))
                    wrong = 'That code is not right. Sign in again.'
                else:
                    tx.connection.execute('UPDATE evlet_two_step_tickets SET attempts=? WHERE token_hash=?', (attempts, row['token_hash']))
                    wrong = 'That code is not right. Try again.'
            else:
                tx.connection.execute('DELETE FROM evlet_two_step_tickets WHERE token_hash=?', (row['token_hash'],))
                address = client_network(auth.client_address(scope))
                pair = hashlib.sha256(('account:' + address + ':' + account['username']).encode()).hexdigest()
                tx.connection.execute('DELETE FROM evlet_login_limits WHERE client=?', (pair,))
                payload, headers = auth.start_session(tx, scope, account, realm)
                if codes:
                    payload['backup_codes'] = codes
                return payload, headers
        raise AccessError(401, wrong)
