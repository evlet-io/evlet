"""Controls: roles, overrides, the audit log and the config version behind /controls."""

import ipaddress
import json
import re
import sqlite3
from threading import Lock
import time
from types import MappingProxyType
from urllib.parse import parse_qs, quote

from .auth import PORTAL, RESERVED_ROLES, ROLE_ID, ROLES, AccessError, LocalAuth, User
from .controls_system import System
from .customization import Customization, activated, instance_fields
from .fields import Layer
from .fields import Link
from .database import NotFoundError
from .identity import path_id
from .models import ValidationError, identifier
from .periods import TABLE as PERIOD_LOCKS
from .permissions import CORRECTIONS, Policy, Rule, default_rule, own_customer, scope_condition

OPERATIONS = ('read', 'create', 'update', 'delete', 'journal', 'print', *CORRECTIONS)
KINDS = ('perm', 'field', 'custom', 'layout', 'list', 'naming', 'setting')
PAGES = ('overview', 'users', 'roles', 'permissions', 'security', 'connectors', 'email', 'mcp', 'eva', 'usage', 'log', 'keys', 'models', 'naming', 'periods', 'prints', 'apps', 'routing', 'jobs', 'overrides', 'audit')
SETTINGS = MappingProxyType({
    'security.password_min': 15, 'security.session_hours': 12, 'security.lockout': 5,
    'security.two_step_roles': ['admin'], 'security.controls_ips': [],
    'mcp.enabled': True, 'mcp.allow_writes': False, 'mcp.rate': 60, 'mcp.disabled_tools': [], 'mcp.hidden_models': [],
    'mail.personal': False,
    # Eva (evlet.eva). Only used when config.py turns AI on; the switch here can only narrow that.
    'eva.enabled': False, 'eva.model': '', 'eva.choose_model': False, 'eva.disabled_tools': [],
    'eva.confirm': {'create': 'never', 'update': 'never', 'confirm': 'ask', 'action': 'ask'},
    'eva.instance_limit': {'amount': None, 'unit': 'aed', 'period': 'month'},
    'eva.user_limit': {'amount': None, 'unit': 'aed', 'period': 'week'},
    'eva.person_limits': {}, 'eva.task_reserve': 10, 'eva.max_steps': 12, 'eva.max_tools': 30, 'eva.max_seconds': 120, 'eva.rates': {},
})
# Audit areas of setting overrides by key prefix; other settings are Security.
SETTING_AREAS = {'mcp': 'MCP', 'mail': 'Email', 'eva': 'Eva'}
NOT_FOUND = 'Route not found'
AREAS = {'perm': 'Permissions', 'setting': 'Security', 'field': 'Models', 'layout': 'Models', 'list': 'Models', 'naming': 'Naming'}
# A paused schedule is a setting override: schedule.<name>.active = false.
SCHEDULE = re.compile(r'schedule\.([a-z][a-z0-9_]{0,63})\.active')
ADMIN_ONLY = 'Only an admin can do this: change admin accounts, give the admin role, manage roles or decide who opens Controls.'
TWO_STEP = ('Controls needs a sign-in with two-step sign-in. Turn it on in Settings, under Sign-in & security, if you have not, '
            'then sign out and sign in again with a code.')
LABELS = {None: 'All records', 'created_by': 'Records they created', 'owner': 'Records they own',
          'account_manager': 'Customers they manage', 'assignee': 'Tasks assigned to them', 'own_customer': 'Their own customer only'}


def _field_scope(name):
    def scope(user):
        return {name: user.id}
    scope.__qualname__ = f'SCOPES[{name!r}]'
    return scope


# Named row scopes for permissions.py: Rule(..., scope=SCOPES["owner"]).
SCOPES = MappingProxyType({name: _field_scope(name) for name in ('created_by', 'owner', 'account_manager', 'assignee')})


def ensure_schema(connection):
    """Create the Controls tables on a framework or raw SQLite connection. The audit log is append-only."""
    for statement in (
        "CREATE TABLE IF NOT EXISTS evlet_overrides (kind TEXT NOT NULL CHECK(kind IN ('perm','field','custom','layout','list','naming','setting')), key TEXT NOT NULL, value TEXT NOT NULL, changed_by INTEGER, changed_at INTEGER, PRIMARY KEY (kind, key))",
        'CREATE TABLE IF NOT EXISTS evlet_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, actor TEXT NOT NULL, area TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL, before TEXT, after TEXT)',
        'CREATE TABLE IF NOT EXISTS evlet_config (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)',
        'INSERT OR IGNORE INTO evlet_config(id,version) VALUES (1,1)',
        PERIOD_LOCKS,
    ):
        connection.execute(statement)
    if isinstance(connection, sqlite3.Connection):
        refuse = "BEGIN SELECT RAISE(ABORT, 'The audit log is append-only'); END"
        connection.execute(f'CREATE TRIGGER IF NOT EXISTS evlet_audit_no_update BEFORE UPDATE ON evlet_audit {refuse}')
        connection.execute(f'CREATE TRIGGER IF NOT EXISTS evlet_audit_no_delete BEFORE DELETE ON evlet_audit {refuse}')
        connection.execute(f'CREATE TRIGGER IF NOT EXISTS evlet_audit_no_replace BEFORE INSERT ON evlet_audit WHEN EXISTS (SELECT 1 FROM evlet_audit WHERE id=NEW.id) {refuse}')
    else:
        connection.execute("CREATE OR REPLACE FUNCTION evlet_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'The audit log is append-only'; END $$")
        connection.execute('CREATE OR REPLACE TRIGGER evlet_audit_append_only BEFORE UPDATE OR DELETE ON evlet_audit FOR EACH ROW EXECUTE FUNCTION evlet_audit_append_only()')


def audit(connection, actor, area, target, detail, before=None, after=None):
    connection.execute('INSERT INTO evlet_audit(at,actor,area,target,detail,before,after) VALUES (?,?,?,?,?,?,?)', (
        int(time.time()), actor, area, target, detail,
        None if before is None else json.dumps(before, sort_keys=True), None if after is None else json.dumps(after, sort_keys=True)))


def bump(connection):
    connection.execute('UPDATE evlet_config SET version=version+1 WHERE id=1')
    return connection.execute('SELECT version FROM evlet_config WHERE id=1').fetchone()[0]


def grant(database_path, username):
    """Break glass from the server shell: make a user an active admin and clear the Controls allowlist, audited as cli."""
    if not database_path.is_file():
        raise ValueError('This instance has no database yet. Start it once first.')
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        connection.execute('BEGIN IMMEDIATE')
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_users'").fetchone():
            raise ValueError('This instance has no users yet. Open it in a browser to create the first admin.')
        ensure_schema(connection)
        row = connection.execute('SELECT id, role, active FROM evlet_users WHERE username=?', (username.lower(),)).fetchone()
        if row is None:
            raise ValueError(f'No user is named {username}.')
        connection.execute("UPDATE evlet_users SET role='admin', active=1 WHERE id=?", (row[0],))
        LocalAuth.end_sessions(connection, row[0], keys=True)
        audit(connection, 'cli', 'Users', username.lower(), 'Made an active admin from the server shell · Sessions, AI assistants and API keys ended',
              {'role': row[1], 'active': bool(row[2])}, {'role': 'admin', 'active': True})
        # An allowlist that shuts everyone out is cleared too; routes.py stays as it is.
        ips = connection.execute("SELECT value FROM evlet_overrides WHERE kind='setting' AND key='security.controls_ips'").fetchone()
        if ips is not None:
            connection.execute("DELETE FROM evlet_overrides WHERE kind='setting' AND key='security.controls_ips'")
            audit(connection, 'cli', 'Security', 'security.controls_ips', 'Allowed networks cleared from the server shell', json.loads(ips[0]), None)
        bump(connection)
        connection.execute('COMMIT')
    except BaseException:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise
    finally:
        connection.close()


def reset_two_step_from_shell(database_path, username):
    """Clear a user's two-step sign-in from the server shell and end their sessions, audited as cli."""
    from .settings import reset_two_step
    if not database_path.is_file():
        raise ValueError('This instance has no database yet. Start it once first.')
    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN IMMEDIATE')
        ensure_schema(connection)
        row = connection.execute('SELECT id, username FROM evlet_users WHERE username=?', (username.lower(),)).fetchone() \
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_users'").fetchone() else None
        if row is None:
            raise ValueError(f'No user is named {username}.')
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='evlet_user_profile'").fetchone():
            raise ValueError(f'{username} has no two-step sign-in to reset.')
        reset_two_step(connection, 'cli', row['id'], row['username'])
        connection.execute('COMMIT')
    except BaseException:
        if connection.in_transaction:
            connection.execute('ROLLBACK')
        raise
    finally:
        connection.close()


def client_ip(scope, auth):
    """The address the Controls allowlist checks: LocalAuth.client_address, through trusted proxies only."""
    try:
        address = ipaddress.ip_address(auth.client_address(scope))
    except ValueError:
        return None
    return (address.ipv4_mapped or address) if address.version == 6 else address


def _networks(values):
    return [ipaddress.ip_network(value, strict=False) for value in values]


def strictest(key, value, roles, file=None):
    """The strictest safe value of a stored setting that no longer validates; file is the starting value."""
    file = SETTINGS.get(key) if file is None else file
    number = type(value) is int
    if key in ('mcp.enabled', 'mcp.allow_writes', 'mail.personal'):
        return False
    if key == 'mcp.rate':
        return min(max(value, 1), 60) if number else 1
    if key in ('mcp.disabled_tools', 'mcp.hidden_models'):
        # Names are kept as they are; a value that is not a list turns every tool off or hides every model ('*').
        from .tools import NAMES
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
        return list(NAMES) if key == 'mcp.disabled_tools' else ['*']
    if key.startswith('eva.'):
        from .eva import strictest as eva_strictest
        return eva_strictest(key, value)
    if key == 'security.password_min':
        return min(max(value, file), 64) if number else 64
    if key == 'security.session_hours':
        return min(max(value, 1), file) if number else 1
    if key == 'security.lockout':
        return min(max(value, 3), file) if number else 3
    if key == 'security.two_step_roles':
        listed = [role for role in value if isinstance(role, str) and role in roles] if isinstance(value, list) else []
        return ['admin', *dict.fromkeys(role for role in listed if role != 'admin')]
    kept = []
    for item in value if isinstance(value, list) else []:
        try:
            kept.append(str(ipaddress.ip_network(item, strict=False)))
        except (TypeError, ValueError):
            continue
    # Nothing usable left: no address may open Controls until it is fixed or cleared from the shell.
    return kept or ['0.0.0.0/32']


class Controls:
    def __init__(self, business):
        self.business, self.database, self.registry = business, business.database, business.registry
        self.file = self.base = business.policy
        self.routes = None
        self.ready, self.version, self.lock = False, None, Lock()
        self.apps, self.app_of, self.app_roles, self.scopes = [], {}, {}, {}
        self.roles, self.settings, self.problems = {}, self.files(), []
        self.custom = Customization(self)
        self.system = System(self)

    # ---------- set-up ----------

    def load(self, definitions, packages=(), path=None):
        """App metadata from build_instance: names, versions, models, declared roles and scopes."""
        packages = {package.name: package for package in packages}
        self.system.load(path)
        for name, definition in definitions:
            package = packages.get(name)
            self.apps.append({'id': name, 'title': getattr(package, 'title', '') or name, 'version': getattr(package, 'version', ''),
                              'layout': getattr(package, 'layout', 'legacy'), 'models': [model.__name__ for model in definition.models],
                              'connectors': [getattr(item, 'name', str(item)) for item in getattr(definition, 'connectors', ())],
                              'permissions': len(definition.permissions), 'schedules': [schedule.name for schedule in definition.schedules]})
            for model in definition.models:
                self.app_of[model.__name__] = name
            for role in getattr(definition, 'roles', {}):
                self.app_roles[role] = (name, definition.roles[role])
            for key, scope in getattr(definition, 'scopes', {}).items():
                if key in self.scopes or key in LABELS:
                    raise ValueError(f'Row scope {key} is registered twice')
                self.scopes[key] = scope

    def initialize(self):
        if not self.ready:
            self.file = self.business.policy
        with self.database.transaction() as tx:
            ensure_schema(tx.connection)
            self.custom.initialize(tx.connection)
            # Roles the app files name are created when missing, with the base the app declares.
            for role, base in self.file.roles.items():
                if role not in (*ROLES, PORTAL):
                    app = self.app_roles.get(role, ('an installed',))[0]
                    tx.connection.execute("INSERT OR IGNORE INTO evlet_roles(id,kind,base,realm,opens_controls,description) VALUES (?,'custom',?,?,0,?)",
                                          (role, base, 'portal' if base == PORTAL else 'desk', f'From the {app} app. Starts from {base}.'))
        self.ready = True
        self.refresh(force=True)

    def refresh(self, force=False):
        """Rebuild the Policy and settings when another process saved a change (one indexed read)."""
        if not self.ready:
            return
        with self.database._connection() as connection:
            version = connection.execute('SELECT version FROM evlet_config WHERE id=1').fetchone()[0]
        if version == self.version and not force:
            return
        with self.lock:
            with self.database._connection() as connection:
                connection.execute('BEGIN')
                version = connection.execute('SELECT version FROM evlet_config WHERE id=1').fetchone()[0]
                if version == self.version and not force:
                    return
                roles = {row['id']: dict(row) for row in connection.execute('SELECT * FROM evlet_roles')}
                stored = [dict(row) for row in connection.execute('SELECT kind,key,value FROM evlet_overrides ORDER BY kind,key')]
                fields = [(row[0], row[1], json.loads(row[2])) for row in connection.execute('SELECT model,name,definition FROM evlet_custom_fields ORDER BY created_at, name')]
            self.apply(version, roles, stored, fields)

    def apply(self, version, roles, stored, fields=()):
        """Build the Policy, settings and model customization, then swap in the database's new Layer.

        An override that no longer validates fails closed and is reported. Everything is built against
        the new instance fields, which become live for requests that start after the swap."""
        problems = []
        self.roles = roles
        previous = self.database.layer
        fields = instance_fields(self.registry, fields, previous.fields)
        with activated(self.database, Layer(fields, previous.choices, previous.naming)):
            self.custom.apply([row for row in stored if row['kind'] in ('field', 'layout', 'list', 'naming')], problems)
        layer = Layer(fields, self.custom.choices, self.custom.naming)
        with activated(self.database, layer):
            policy, settings = self.build(roles, stored, problems)
        self.database.layer = layer
        self.business.policy = policy
        if self.business.jobs is not None:
            self.business.jobs.policy = policy
        auth = self.business.auth
        auth.password_min, auth.session_hours, auth.lockout = settings['security.password_min'], settings['security.session_hours'], settings['security.lockout']
        self.settings, self.problems, self.version = settings, problems, version

    def build(self, roles, stored, problems):
        perms, settings = {}, self.files()
        self.base = Policy(self.registry, rules=self.file.declared, roles=self.role_bases(roles))
        for row in stored:
            try:
                value = json.loads(row['value'])
            except ValueError:
                value = None
            try:
                if row['kind'] == 'perm':
                    role, model = self.perm_key(row['key'])
                    perms[role, model] = self.rule(role, model, self.check_perm(row['key'], value))
                elif row['kind'] == 'setting':
                    settings[row['key']] = self.check_setting(row['key'], value)
                elif row['kind'] not in ('field', 'layout', 'list', 'naming'):
                    raise ValueError('This kind of change is not available yet.')
            except (ValueError, KeyError, TypeError) as exc:
                problems.append({'kind': row['kind'], 'key': row['key'], 'error': str(exc)})
                if row['kind'] == 'perm':
                    try:
                        perms[self.perm_key(row['key'])] = Rule(())
                    except ValueError:
                        pass
                elif row['kind'] == 'setting' and row['key'] in SETTINGS:
                    settings[row['key']] = strictest(row['key'], value, roles, self.files()[row['key']])
        try:
            policy = Policy(self.registry, rules=self.file.declared, roles=self.role_bases(roles), overrides=perms, read_only=self.custom.read_only)
        except ValueError as exc:
            problems.append({'kind': 'perm', 'key': 'all', 'error': f'{exc}. Every role except admin is denied until this is fixed.'})
            policy = Policy(self.registry, rules=self.file.declared, roles=self.role_bases(roles), read_only=self.custom.read_only,
                            overrides={(role, model): Rule(()) for role in roles if role != 'admin' for model in self.registry.models.values()})
        return policy, settings

    def files(self):
        """Each setting's starting value: the framework default, with config.py's Security(...) for sign-in rules."""
        security = getattr(self.business.auth, 'security', None)
        values = dict(SETTINGS)
        if security is not None:
            values.update({'security.session_hours': security.session_hours, 'security.lockout': security.lockout,
                           'security.password_min': security.password_min})
        return values

    @staticmethod
    def role_bases(roles):
        return {role: role if row['kind'] == 'framework' else row['base'] for role, row in roles.items()}

    # ---------- access ----------

    def host_allowed(self, scope):
        """The primary host only. Without one (development, or a tenant whose factory leaves the route table
        default), every host the auth accepts that is not a sub-host counts, so each tenant's own host works."""
        from .routing import host_name
        host = dict(scope.get('headers', [])).get(b'host', b'')
        if self.routes is None or host not in self.business.auth.hosts or self.routes.scopes(host) is not None:
            return False
        return self.routes.primary is None or host_name(host) == self.routes.primary

    def allows(self, scope, user):
        role = self.roles.get(user.role)
        # API keys never open Controls.
        if user.access is not None or not self.host_allowed(scope) or role is None or role['realm'] != 'desk':
            return False
        if user.role != 'admin' and not role['opens_controls']:
            return False
        address = client_ip(scope, self.business.auth)
        for allowed in (self.routes.controls.allow_ips, self.settings['security.controls_ips']):
            if allowed and (address is None or not any(address in network for network in _networks(allowed))):
                return False
        return True

    def check(self, scope, user):
        if not self.allows(scope, user):
            raise AccessError(404, NOT_FOUND)
        # The session must have finished two-step sign-in, not only the account have it on.
        required = self.routes.controls.require_two_step or user.role in self.settings['security.two_step_roles'] and self.business.settings.possible()
        if required and not user.two_step:
            raise AccessError(403, TWO_STEP)

    def page(self, scope, path):
        """GET /controls[/page]: anonymous users go to sign-in; True serves the page, False is a 404."""
        from .business import Download
        rest = path.removeprefix('/controls').strip('/')
        if rest and rest not in PAGES:
            return False
        auth = self.business.auth
        try:
            user, _ = auth.session(scope)
        except AccessError as exc:
            if exc.status != 401:
                raise
            return 302, Download(b''), [(b'location', ('/login?next=' + quote(path, safe='/')).encode())]
        return self.allows(scope, user)

    # ---------- permissions ----------

    def models(self):
        children = self.business.child_models
        # A model with Meta.api = None has no surface, so Controls has nothing to customize on it.
        return {model.__name__: model for model in self.registry.exposed.values() if model not in children}

    def perm_key(self, key):
        role, _, name = key.partition('|') if isinstance(key, str) else ('', '', '')
        model = self.models().get(name)
        if role not in self.roles or model is None:
            raise ValueError('Choose an existing role and model.')
        if role == 'admin':
            raise ValueError('Admin always has full access. Its rules cannot be changed.')
        return role, model

    def scope_id(self, scope):
        if scope is None:
            return None
        for key, preset in (*SCOPES.items(), *self.scopes.items()):
            if scope is preset:
                return key
        return getattr(scope, 'preset', 'app')

    def presets(self, model):
        found = [None]
        fields = model.__declared_fields__
        found += [key for key in SCOPES if key in fields]
        if self.registry.name_for(model) == 'customer' or isinstance(fields.get('customer'), Link):
            found.append('own_customer')
        for key, scope in self.scopes.items():
            try:
                probe = scope(_Probe())
                if isinstance(probe, dict) and all(name.split('__')[0] in {'id', *fields} for name in probe):
                    found.append(key)
                elif not isinstance(probe, dict):
                    # A team or any_of scope fits when it compiles for this model.
                    scope_condition(self.registry, model, probe)
                    found.append(key)
            except Exception:
                continue
        return found

    def compile_scope(self, key, model):
        if key in SCOPES:
            return SCOPES[key]
        if key in self.scopes:
            return self.scopes[key]
        # The same scope as permissions.own_customer, over the links the session preloads.
        scope = own_customer('id' if self.registry.name_for(model) == 'customer' else 'customer')
        scope.preset = 'own_customer'
        return scope

    def file_value(self, role, model):
        rule = self.file_rule(role, model)
        return self.value(model, rule)

    def file_rule(self, role, model):
        found = self.base.file_rules.get((role, model))
        if found is not None:
            return found
        return default_rule(self.role_bases(self.roles).get(role))

    def value(self, model, rule):
        order = list(model.__declared_fields__)
        fields = lambda names: None if names is None else [name for name in order if name in names]
        return {'ops': [op for op in OPERATIONS if op in rule.operations], 'read': fields(rule.read_fields),
                'write': fields(rule.write_fields), 'scope': self.scope_id(rule.scope)}

    def check_perm(self, key, value):
        role, model = self.perm_key(key)
        if not isinstance(value, dict) or set(value) != {'ops', 'read', 'write', 'scope'}:
            raise ValueError('Send ops, read, write and scope.')
        ops, read, write, scope = value['ops'], value['read'], value['write'], value['scope']
        fields = list(model.__declared_fields__)
        strings = lambda items: isinstance(items, list) and all(isinstance(item, str) for item in items)
        if not strings(ops) or any(names is not None and not strings(names) for names in (read, write)) or not (scope is None or isinstance(scope, str)):
            raise ValueError('Use lists of names for ops, read and write, and a name or null for scope.')
        if not isinstance(ops, list) or any(op not in OPERATIONS for op in ops) or len(set(ops)) != len(ops):
            raise ValueError('Use read, create, update and delete, each once.')
        for names in (read, write):
            if names is not None and (not isinstance(names, list) or any(name not in fields for name in names) or len(set(names)) != len(names)):
                raise ValueError(f'Name fields of {model.__name__}, each once.')
        if ops and 'read' not in ops:
            raise ValueError('Create, update and delete need read access.')
        if not ops and (read is not None or write is not None or scope is not None):
            raise ValueError('Without read access, fields and rows must be empty.')
        if not set(ops) - {'read'} and write is not None:
            raise ValueError('Write fields need create or update access.')
        if 'revise' in ops and 'create' not in ops:
            raise ValueError('Revise needs create access.')
        if 'repost' in ops and 'update' not in ops:
            raise ValueError('Repost needs update access.')
        if read is not None and not read:
            raise ValueError('Keep at least one readable field, or turn off read instead.')
        if read is not None and write is not None and set(write) - set(read):
            raise ValueError('Fields people can write must also be readable.')
        file = self.file_rule(role, model)
        if set(ops) - set(file.operations):
            missing = ', '.join(op for op in OPERATIONS if op in set(ops) - set(file.operations))
            raise ValueError(f'The app does not give {role} {missing} on {model.__name__}. An override can only take access away.')
        # Stricter only covers fields the app declares. Instance fields belong to Controls, so an override may grant them.
        owned = {name for name in fields if getattr(model.__declared_fields__[name], 'instance', None)}
        readable = set(fields if file.read_fields is None else file.read_fields) | owned
        writable = (set(fields if file.write_fields is None else file.write_fields) | owned) & readable
        if read is not None and set(read) - readable or read is None and readable != set(fields):
            raise ValueError(f'The app hides some fields of {model.__name__} from {role}. An override can only hide more.')
        if set(ops) - {'read'} and (set(fields if write is None else write) & set(fields if read is None else read)) - writable:
            raise ValueError(f'The app does not let {role} write some of these fields. An override can only allow fewer.')
        file_scope = self.scope_id(file.scope)
        if file_scope is not None and ops and scope != file_scope:
            raise ValueError(f'The row filter for {role} on {model.__name__} comes from the app and cannot be changed here.')
        if file_scope is None and scope is not None:
            if scope not in self.presets(model):
                raise ValueError(f'{LABELS.get(scope, scope)} is not available for {model.__name__}.')
        return {'ops': [op for op in OPERATIONS if op in ops], 'read': None if read is None else [name for name in fields if name in read],
                'write': None if write is None else [name for name in fields if name in write], 'scope': scope}

    def rule(self, role, model, value):
        file = self.file_rule(role, model)
        scope = None if value['scope'] is None else file.scope if value['scope'] == self.scope_id(file.scope) else self.compile_scope(value['scope'], model)
        return Rule(tuple(value['ops']), None if value['read'] is None else tuple(value['read']),
                    None if value['write'] is None else tuple(value['write']), scope)

    # ---------- settings ----------

    def check_setting(self, key, value):
        match = SCHEDULE.fullmatch(key)
        if match:
            if match[1] not in self.system.schedule_names():
                raise ValueError(f'No installed app declares the schedule {match[1]} any more.')
            if type(value) is not bool:
                raise ValueError('Use true to run the schedule or false to pause it.')
            return value
        if key not in SETTINGS:
            raise ValueError('Choose a known setting.')
        file = self.files()[key]
        number = type(value) is int
        if key == 'security.password_min' and not (number and file <= value <= 64):
            raise ValueError('Minimum password length can only go up, to 64 at most.')
        if key == 'security.session_hours' and not (number and 1 <= value <= file):
            raise ValueError(f'Session length can only go down, from {file} hours to 1.')
        if key == 'security.lockout' and not (number and 3 <= value <= file):
            raise ValueError(f'Failed sign-ins before lockout can only go down, from {file} to 3.')
        if key == 'security.two_step_roles':
            if not isinstance(value, list) or any(role not in self.roles or self.roles[role]['realm'] != 'desk' for role in value) or len(set(value)) != len(value):
                raise ValueError('Choose existing roles, each once.')
            if 'admin' not in value:
                raise ValueError('Admin always needs two-step sign-in.')
            value = sorted(value, key=lambda role: (role not in ROLES, ROLES.index(role) if role in ROLES else role))
        if key == 'security.controls_ips':
            if not isinstance(value, list) or len(value) > 50:
                raise ValueError('Use a list of at most 50 addresses.')
            networks = []
            for item in value:
                try:
                    if not isinstance(item, str) or not re.fullmatch(r'[0-9A-Fa-f:.]+(/[0-9]{1,3})?', item.strip()):
                        raise ValueError()
                    networks.append(str(ipaddress.ip_network(item.strip(), strict=False)))
                except ValueError:
                    raise ValueError(f"{item} isn't an IP address or range.") from None
            value = list(dict.fromkeys(networks))
        if key in ('mcp.enabled', 'mcp.allow_writes', 'mail.personal') and type(value) is not bool:
            raise ValueError('Use on or off.')
        if key == 'mcp.rate' and not (number and 1 <= value <= 60):
            raise ValueError('Use 1 to 60 tool calls a minute. The framework allows at most 60.')
        if key.startswith('eva.'):
            from .eva import check_setting as eva_setting
            return eva_setting(key, value, self.business)
        if key in ('mcp.disabled_tools', 'mcp.hidden_models'):
            from .tools import NAMES
            known = list(NAMES) if key == 'mcp.disabled_tools' else list(self.models())
            if not isinstance(value, list) or any(item not in known for item in value) or len(set(value)) != len(value):
                raise ValueError('Choose known tools, each once.' if key == 'mcp.disabled_tools' else 'Choose models from this instance, each once.')
            value = [item for item in known if item in value]
        return value

    # ---------- overrides ----------

    def check_change(self, kind, key, value):
        if kind not in KINDS:
            raise ValueError('Choose a known kind of change.')
        if kind == 'perm':
            role, model = self.perm_key(key)
            if value is None:
                return None
            value = self.check_perm(key, value)
            return None if value == self.file_value(role, model) else value
        if kind == 'setting':
            if key not in SETTINGS and not SCHEDULE.fullmatch(key):
                raise ValueError('Choose a known setting.')
            if value is None:
                return None
            value = self.check_setting(key, value)
            return None if value == self.files().get(key, True) else value
        if kind == 'custom':
            raise ValueError('Add and remove instance fields with Add field and Remove field.')
        return self.custom.check(kind, key, value)

    def overrides(self):
        with self.database._connection() as connection:
            stored = {(row['kind'], row['key']): row for row in connection.execute(
                'SELECT o.*, u.username FROM evlet_overrides o LEFT JOIN evlet_users u ON u.id=o.changed_by')}
        items = []
        meta = lambda kind, key: {'changed_by': stored[kind, key]['username'], 'changed_at': stored[kind, key]['changed_at']} if (kind, key) in stored else {'changed_by': None, 'changed_at': None}
        value = lambda kind, key: json.loads(stored[kind, key]['value']) if (kind, key) in stored else None
        bases = self.role_bases(self.roles)
        for role in self.roles:
            for name, model in self.models().items():
                key = f'{role}|{name}'
                declared = any((each, model) in self.file.declared or each in getattr(model, 'permissions', {}) for each in (role, bases[role]))
                items.append({'kind': 'perm', 'key': key, 'file': self.file_value(role, model), 'value': value('perm', key),
                              'source': 'app' if declared else 'framework', **meta('perm', key)})
        for key, file in self.files().items():
            items.append({'kind': 'setting', 'key': key, 'file': file, 'value': value('setting', key), **meta('setting', key)})
        for name in self.system.schedule_names():
            key = f'schedule.{name}.active'
            items.append({'kind': 'setting', 'key': key, 'file': True, 'value': value('setting', key), **meta('setting', key)})
        for kind, key, file in self.custom.items():
            items.append({'kind': kind, 'key': key, 'file': file, 'value': value(kind, key), **meta(kind, key)})
        # Instance fields are listed for export; they are added and removed on the Models page.
        with self.database._connection() as connection:
            rows = connection.execute('SELECT model, name, definition, created_by, created_at FROM evlet_custom_fields ORDER BY created_at, name').fetchall()
        tables = {self.registry.name_for(model): name for name, model in self.models().items()}
        for table in dict.fromkeys(row['model'] for row in rows if row['model'] in tables):
            mine = [row for row in rows if row['model'] == table]
            items.append({'kind': 'custom', 'key': tables[table], 'file': None, 'value': [{'name': row['name'], **json.loads(row['definition'])} for row in mine],
                          'changed_by': mine[-1]['created_by'], 'changed_at': mine[-1]['created_at']})
        # Saved overrides whose target is gone stay listed, so they can be reset.
        listed = {(item['kind'], item['key']) for item in items}
        items += [{'kind': kind, 'key': key, 'file': None, 'value': value(kind, key), **meta(kind, key)} for kind, key in stored if (kind, key) not in listed]
        return {'items': items, 'problems': self.problems, 'version': self.version}

    def save(self, user, body):
        changes = body.get('changes') if isinstance(body, dict) and set(body) == {'changes'} else None
        if not isinstance(changes, list) or not changes or len(changes) > 500:
            raise ValidationError({'changes': 'Send between 1 and 500 changes.'})
        errors, checked, seen = {}, [], set()
        for index, change in enumerate(changes):
            try:
                if not isinstance(change, dict) or set(change) != {'kind', 'key', 'value'} or not isinstance(change['kind'], str) or not isinstance(change['key'], str):
                    raise ValueError('Send kind, key and value.')
                if (change['kind'], change['key']) in seen:
                    raise ValueError('Change each item once.')
                seen.add((change['kind'], change['key']))
                checked.append((change['kind'], change['key'], self.check_change(change['kind'], change['key'], change['value'])))
            except (ValueError, TypeError) as exc:
                errors[f'changes.{index}'] = str(exc) if isinstance(exc, ValueError) else 'Send kind, key and value.'
        if errors:
            raise ValidationError(errors)
        now = int(time.time())
        with self.database.transaction() as tx:
            connection = tx.connection
            for kind, key, value in checked:
                row = connection.execute('SELECT value FROM evlet_overrides WHERE kind=? AND key=?', (kind, key)).fetchone()
                before = None if row is None else json.loads(row['value'])
                if before == value:
                    continue
                if value is None:
                    connection.execute('DELETE FROM evlet_overrides WHERE kind=? AND key=?', (kind, key))
                else:
                    connection.execute('INSERT INTO evlet_overrides(kind,key,value,changed_by,changed_at) VALUES (?,?,?,?,?) ON CONFLICT(kind,key) DO UPDATE SET value=excluded.value, changed_by=excluded.changed_by, changed_at=excluded.changed_at',
                                       (kind, key, json.dumps(value, sort_keys=True), user.id, now))
                schedule = SCHEDULE.fullmatch(key)
                if schedule:
                    audit(connection, user.username, 'Jobs', schedule[1], 'Resumed' if value is None else 'Paused', before, value)
                else:
                    audit(connection, user.username, SETTING_AREAS.get(key.split('.')[0], 'Security') if kind == 'setting' else AREAS[kind], key.replace('|', ' · '), 'Reset to the file value' if value is None else 'Changed', before, value)
                if key == 'security.session_hours':
                    # Sessions keep their start: a shorter length ends the ones already older than it.
                    shorter = self.settings[key] - (self.files()[key] if value is None else value)
                    if shorter > 0:
                        connection.execute('UPDATE evlet_sessions SET expires=expires-? WHERE expires>?', (shorter * 3600, now))
            version = self.publish(tx)
        self.refresh()
        for kind, key, value in checked:
            if kind == 'setting' and SCHEDULE.fullmatch(key):
                self.system.apply_schedule(SCHEDULE.fullmatch(key)[1], value is None)
        return {'version': version, 'saved': len(checked)}

    def audit(self, *row):
        audit(*row)

    def publish(self, tx):
        version = bump(tx.connection)
        if self.business.events is not None:
            self.business.events.publish_config(tx, version)
        return version

    # ---------- period locks ----------

    def period_scopes(self):
        """{name: model} of the models whose records each carry their own lock (Meta.period_scope targets)."""
        result = {}
        for model, period in self.registry.periods.items():
            if period.scope is not None and model in self.registry.exposed.values():
                target = model.__fields__[period.scope].target
                result[self.registry.name_for(target)] = target
        return result

    def periods(self):
        """The models with Meta.period, every saved lock and the latest changes (evlet/periods.py)."""
        from .naming import has_title
        models = []
        for model, period in self.registry.periods.items():
            if model not in self.registry.exposed.values():
                continue
            scope = model.__fields__[period.scope].target if period.scope else None
            models.append({'name': self.registry.name_for(model), 'label': model.__name__, 'field': period.field,
                           'scope': None if scope is None else {'field': period.scope, 'model': self.registry.name_for(scope), 'label': scope.__name__}})
        with self.database._connection() as connection:
            rows = connection.execute('SELECT p.scope, p.lock_date, p.changed_at, u.username FROM evlet_period_locks p '
                                      'LEFT JOIN evlet_users u ON u.id=p.changed_by ORDER BY p.scope').fetchall()
            history = [dict(row) for row in connection.execute("SELECT id,at,actor,target,detail,before,after FROM evlet_audit "
                                                              "WHERE area='Periods' ORDER BY id DESC LIMIT 50")]
            titles, scopes = {}, []
            for name, target in self.period_scopes().items():
                title = 'title' if has_title(target) else None
                records = connection.execute(f'SELECT id{", title" if title else ""} FROM {identifier(name)} ORDER BY id LIMIT 200').fetchall()
                choices = [{'id': str(row['id']), 'title': (row['title'] if title else None) or str(row['id'])} for row in records]
                titles.update({f'{name}:{item["id"]}': item['title'] for item in choices})
                scopes.append({'model': name, 'label': target.__name__, 'records': choices})
        locks = [{'scope': row['scope'], 'label': 'All records' if row['scope'] == '' else titles.get(row['scope'], row['scope']),
                  'date': row['lock_date'], 'changed_by': row['username'], 'changed_at': row['changed_at']} for row in rows]
        return {'models': models, 'scopes': scopes, 'locks': locks, 'history': history, 'can_change': True}

    def set_period(self, user, body):
        """Set or clear one lock: {"scope": null or {"model", "id"}, "date": "YYYY-MM-DD" or null}. Admin only."""
        from datetime import date
        from .periods import scope_key, set_lock
        if user.role != 'admin':
            raise AccessError(403, 'Only an admin can change period locks.')
        if not isinstance(body, dict) or set(body) != {'scope', 'date'}:
            raise ValidationError({'_form': 'Send scope and date.'})
        try:
            value = None if body['date'] is None else date.fromisoformat(body['date']) if isinstance(body['date'], str) and len(body['date']) == 10 else ''
        except ValueError:
            value = ''
        if value == '':
            raise ValidationError({'date': 'Use a date such as 2026-03-31, or null to open every period.'})
        scope = body['scope']
        if scope is None:
            key = ''
        else:
            target = self.period_scopes().get(scope.get('model')) if isinstance(scope, dict) and set(scope) == {'model', 'id'} else None
            record_id = path_id(str(scope.get('id'))) if target is not None else None
            if record_id is None:
                raise ValidationError({'scope': 'Choose a record of a model that periods are locked by.'})
            with self.database.transaction() as tx:
                try:
                    tx.get(target, record_id)
                except NotFoundError:
                    raise ValidationError({'scope': 'That record does not exist.'}) from None
            key = scope_key(self.registry, target, record_id)
        with self.database.transaction() as tx:
            set_lock(tx.connection, key, value, user)

    # ---------- users and roles ----------

    def users(self, offset):
        page = self.business.auth.user_page(offset)
        with self.database._connection() as connection:
            sessions = {row[0]: row[1] for row in connection.execute('SELECT user_id, count(*) FROM evlet_sessions WHERE expires>? GROUP BY user_id', (int(time.time()),))}
            two_step = {row['user_id']: {'since': row['two_step_since'], 'agent': row['two_step_agent']} for row in connection.execute(
                'SELECT user_id, two_step_since, two_step_agent FROM evlet_user_profile WHERE two_step_secret IS NOT NULL')}
            assistants = {row[0]: row[1] for row in connection.execute('SELECT user_id, count(*) FROM evlet_mcp_clients WHERE revoked_at IS NULL GROUP BY user_id')}
            keys = {row[0]: row[1] for row in connection.execute('SELECT user_id, count(*) FROM evlet_api_keys WHERE revoked_at IS NULL GROUP BY user_id')}
            ai = {row['id']: (bool(row['allow_eva']), bool(row['allow_mcp'])) for row in connection.execute('SELECT id, allow_eva, allow_mcp FROM evlet_users')}
        for item in page['items']:
            item['active'], item['sessions'] = bool(item['active']), sessions.get(item['id'], 0)
            item['allow_eva'], item['allow_mcp'] = ai.get(item['id'], (False, False))
            item['two_step'] = two_step.get(item['id'])
            item['assistants'] = assistants.get(item['id'], 0)
            item['keys'] = keys.get(item['id'], 0)
        return page

    def add_user(self, actor, body):
        if not isinstance(body, dict) or set(body) - {'username', 'password', 'role'}:
            raise ValidationError({'_form': 'Use username, password and role'})
        if body.get('role') == 'admin' and actor.role != 'admin':
            raise AccessError(403, ADMIN_ONLY)
        return self.business.auth.add_user(dict(body), lambda connection, user: audit(
            connection, actor.username, 'Users', user.username, f'Added as {user.role}', None, {'role': user.role}))

    def change_user(self, actor, user_id, body):
        if isinstance(body, dict) and body and not set(body) - {'allow_eva', 'allow_mcp'}:
            return self.ai_access(actor, user_id, body)
        if not isinstance(body, dict) or not body or set(body) - {'role', 'active', 'password', 'sessions', 'revoke_keys', 'two_step'}:
            raise ValidationError({'_form': 'Use role, active, password, sessions, revoke_keys, two_step, or allow_eva and allow_mcp on their own'})
        if 'sessions' in body and body['sessions'] != 'end':
            raise ValidationError({'sessions': 'Use "end" to sign out every session'})
        revoke = body.get('revoke_keys', False)
        if 'revoke_keys' in body and (type(revoke) is not bool or 'sessions' not in body):
            raise ValidationError({'revoke_keys': 'Use true or false, with "sessions": "end"'})
        if 'two_step' in body:
            if body != {'two_step': 'reset'}:
                raise ValidationError({'two_step': 'Send only {"two_step": "reset"}'})
            if actor.role != 'admin':
                raise AccessError(403, ADMIN_ONLY)
            from .settings import reset_two_step
            with self.database.transaction() as tx:
                row = tx.connection.execute('SELECT id, username FROM evlet_users WHERE id=?', (user_id,)).fetchone()
                if row is None:
                    raise AccessError(404, 'Account not found')
                reset_two_step(tx.connection, actor.username, row['id'], row['username'])
            return
        changes = {key: value for key, value in body.items() if key not in ('sessions', 'revoke_keys')}

        def check(connection, row, values):
            if actor.role != 'admin' and (row['role'] == 'admin' or values.get('role') == 'admin'):
                raise AccessError(403, ADMIN_ONLY)
            details = []
            if 'role' in values and values['role'] != row['role']:
                details.append(f"Role: {row['role']} → {values['role']}")
            if 'active' in values and values['active'] != bool(row['active']):
                details.append('Activated' if values['active'] else 'Deactivated')
            if 'password' in values:
                details.append('Password set')
            if 'sessions' in body:
                details.append('Signed out everywhere')
            if revoke:
                keys = connection.execute('SELECT count(*) FROM evlet_api_keys WHERE user_id=? AND revoked_at IS NULL', (row['id'],)).fetchone()[0]
                details.append(f'Revoked {keys} API key' + ('' if keys == 1 else 's'))
            before = {'role': row['role'], 'active': bool(row['active'])}
            after = {key: values[key] for key in ('role', 'active') if key in values}
            return lambda: audit(connection, actor.username, 'Users', row['username'], ' · '.join(details), before, after) if details else None

        if changes:
            self.business.auth.update_user(user_id, changes, check)
            return
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_users WHERE id=?', (user_id,)).fetchone()
            if row is None:
                raise AccessError(404, 'Account not found')
            after = check(tx.connection, row, {})
            self.business.auth.end_sessions(tx.connection, user_id, keys=revoke)
            after()

    def ai_access(self, actor, user_id, body):
        """Allow Eva and Allow MCP on one person. Turning MCP off signs out their assistants at once."""
        if any(type(value) is not bool for value in body.values()):
            raise ValidationError({key: 'Use true or false' for key, value in body.items() if type(value) is not bool})
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_users WHERE id=?', (user_id,)).fetchone()
            if row is None:
                raise AccessError(404, 'Account not found')
            if actor.role != 'admin' and row['role'] == 'admin':
                raise AccessError(403, ADMIN_ONLY)
            details = []
            for key, label in (('allow_eva', 'Allow Eva'), ('allow_mcp', 'Allow MCP')):
                if key in body and body[key] != bool(row[key]):
                    tx.connection.execute(f'UPDATE evlet_users SET {key}=? WHERE id=?', (int(body[key]), user_id))
                    details.append(f"{label}: {'on' if body[key] else 'off'}")
            if body.get('allow_mcp') is False:
                for client in tx.connection.execute('SELECT id FROM evlet_mcp_clients WHERE user_id=? AND revoked_at IS NULL', (user_id,)).fetchall():
                    self.business.assistants.end(tx.connection, client[0])
                tx.connection.execute('DELETE FROM evlet_oauth_codes WHERE user_id=?', (user_id,))
            if details:
                audit(tx.connection, actor.username, 'Users', row['username'], ' · '.join(details),
                      {key: bool(row[key]) for key in body}, dict(body))

    def role_list(self):
        with self.database._connection() as connection:
            counts = {row[0]: row[1] for row in connection.execute('SELECT role, count(*) FROM evlet_users GROUP BY role')}
            rows = [dict(row) for row in connection.execute("SELECT * FROM evlet_roles ORDER BY kind DESC, CASE id WHEN 'admin' THEN 0 WHEN 'editor' THEN 1 WHEN 'viewer' THEN 2 ELSE 3 END, id")]
        return [{**row, 'opens_controls': bool(row['opens_controls'] or row['id'] == 'admin'), 'users': counts.get(row['id'], 0),
                 'app': self.app_roles.get(row['id'], (None,))[0], 'declared': row['id'] in self.file.roles} for row in rows]

    def create_role(self, actor, body):
        if actor.role != 'admin':
            raise AccessError(403, ADMIN_ONLY)
        if not isinstance(body, dict) or set(body) - {'id', 'base', 'realm', 'description'}:
            raise ValidationError({'_form': 'Use id, base, realm and description'})
        realm = body.get('realm', 'desk')
        role, base, description = body.get('id'), body.get('base', PORTAL if realm == 'portal' else 'editor'), body.get('description', '')
        if not isinstance(role, str) or not ROLE_ID.fullmatch(role):
            raise ValidationError({'id': 'Use 2 to 31 lowercase letters, digits or underscores, starting with a letter.'})
        if realm not in ('desk', 'portal'):
            raise ValidationError({'realm': 'Use desk or portal.'})
        # Portal roles start from portal, which sees nothing until an app grants it, and never open Controls.
        if realm == 'desk' and base not in ('editor', 'viewer') or realm == 'portal' and base != PORTAL:
            raise ValidationError({'base': 'Start from editor or viewer.' if realm == 'desk' else 'Portal roles start from portal.'})
        if not isinstance(description, str) or len(description) > 200:
            raise ValidationError({'description': 'Use at most 200 characters.'})
        with self.database.transaction() as tx:
            if role in RESERVED_ROLES or tx.connection.execute('SELECT 1 FROM evlet_roles WHERE id=?', (role,)).fetchone():
                raise ValidationError({'id': 'That name is taken.'})
            tx.connection.execute("INSERT INTO evlet_roles(id,kind,base,realm,opens_controls,description) VALUES (?,'custom',?,?,0,?)",
                                  (role, base, realm, description.strip() or f'Starts from {base}.'))
            audit(tx.connection, actor.username, 'Roles', role, f'Created from {base}', None, {'base': base, 'realm': realm})
            self.publish(tx)
        self.refresh()

    def change_role(self, actor, role, body):
        if not isinstance(body, dict) or not body or set(body) - {'opens_controls', 'description'}:
            raise ValidationError({'_form': 'Use opens_controls or description'})
        if 'opens_controls' in body and type(body['opens_controls']) is not bool:
            raise ValidationError({'opens_controls': 'Use true or false'})
        if 'description' in body and (not isinstance(body['description'], str) or len(body['description']) > 200):
            raise ValidationError({'description': 'Use at most 200 characters.'})
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_roles WHERE id=?', (role,)).fetchone()
            if row is None:
                raise AccessError(404, 'Role not found')
            if actor.role != 'admin' and ('opens_controls' in body or row['kind'] == 'framework'):
                raise AccessError(403, ADMIN_ONLY)
            details = []
            if 'opens_controls' in body and body['opens_controls'] != bool(row['opens_controls']):
                if role == 'admin':
                    raise ValidationError({'opens_controls': 'Admin always opens Controls, so the instance cannot be locked out.'})
                if row['realm'] != 'desk':
                    raise ValidationError({'opens_controls': 'Portal roles can never open Controls.'})
                tx.connection.execute('UPDATE evlet_roles SET opens_controls=? WHERE id=?', (int(body['opens_controls']), role))
                details.append('Opens Controls: ' + ('no → yes' if body['opens_controls'] else 'yes → no'))
            if 'description' in body and body['description'] != row['description']:
                tx.connection.execute('UPDATE evlet_roles SET description=? WHERE id=?', (body['description'].strip(), role))
                details.append('Description changed')
            if details:
                audit(tx.connection, actor.username, 'Roles', role, ' · '.join(details))
                self.publish(tx)
        self.refresh()

    def delete_role(self, actor, role):
        if actor.role != 'admin':
            raise AccessError(403, ADMIN_ONLY)
        with self.database.transaction() as tx:
            row = tx.connection.execute('SELECT * FROM evlet_roles WHERE id=?', (role,)).fetchone()
            if row is None:
                raise AccessError(404, 'Role not found')
            if row['kind'] == 'framework':
                raise AccessError(409, 'Framework roles cannot be deleted.')
            if role in self.file.roles:
                raise AccessError(409, f"The {self.app_roles.get(role, ('installed',))[0]} app declares this role. Remove it from the app first.")
            count = tx.connection.execute('SELECT count(*) FROM evlet_users WHERE role=?', (role,)).fetchone()[0]
            if count:
                raise AccessError(409, f"{count} {'user has' if count == 1 else 'users have'} the {role} role. Move them to another role first.")
            removed = tx.connection.execute("DELETE FROM evlet_overrides WHERE kind='perm' AND key LIKE ? ESCAPE '\\'", (role.replace('_', '\\_') + '|%',)).rowcount
            two_step = tx.connection.execute("SELECT value FROM evlet_overrides WHERE kind='setting' AND key='security.two_step_roles'").fetchone()
            if two_step and role in json.loads(two_step['value']):
                kept = [item for item in json.loads(two_step['value']) if item != role]
                if kept == SETTINGS['security.two_step_roles']:
                    tx.connection.execute("DELETE FROM evlet_overrides WHERE kind='setting' AND key='security.two_step_roles'")
                else:
                    tx.connection.execute("UPDATE evlet_overrides SET value=? WHERE kind='setting' AND key='security.two_step_roles'", (json.dumps(kept),))
            tx.connection.execute('DELETE FROM evlet_roles WHERE id=?', (role,))
            audit(tx.connection, actor.username, 'Roles', role, 'Deleted' + (f' with {removed} permission override{"s" if removed != 1 else ""}' if removed else ''),
                  {'base': row['base'], 'realm': row['realm']}, None)
            self.publish(tx)
        self.refresh()

    def connector(self, user, method, row_id, action, body):
        connectors = self.business.connectors
        if action is None and method == 'GET':
            return 200, connectors.get(row_id), []
        if action is None and method == 'PUT':
            return 200, connectors.save(user, row_id, body), []
        if action is None and method == 'DELETE':
            connectors.remove(user, row_id)
            return 204, None, []
        if action == '/test' and method == 'POST':
            return 200, connectors.test(user, row_id, body), []
        if action == '/default' and method == 'POST':
            return 200, connectors.make_default(user, row_id), []
        raise AccessError(404, NOT_FOUND)

    # ---------- API keys ----------

    def key_scope(self, owner, value):
        """Check a key's scope against what its user can do now: a key never does more than its user."""
        if not isinstance(value, dict) or set(value) != {'access', 'models'} or value['access'] not in ('read', 'write'):
            raise ValidationError({'scope': 'Send access (read or write) and models (a list, or null for all).'})
        models = self.models()
        names = value['models']
        if names is not None and (not isinstance(names, list) or not names or len(names) > 50
                                  or any(not isinstance(name, str) or name not in models for name in names) or len(set(names)) != len(names)):
            raise ValidationError({'scope': 'Choose models from this instance, each once.'})
        user = User(owner['id'], owner['username'], owner['role'])
        chosen = [models[name] for name in names] if names is not None else list(models.values())
        hidden = [model.__name__ for model in chosen if not self.business.policy.allows(user, model, 'read')]
        if names is not None and hidden:
            raise ValidationError({'scope': f"{owner['username']} can't read {', '.join(hidden)}. A key can never do more than the user it acts as."})
        if value['access'] == 'write' and not any(self.business.policy.allows(user, model, op) for model in chosen for op in ('create', 'update', 'delete')):
            raise ValidationError({'scope': f"{owner['username']} can only read these, so the key can only read."})
        return {'access': value['access'], 'models': None if names is None else [name for name in models if name in names]}

    def create_key(self, actor, body, personal=False):
        allowed = {'name', 'scope'} if personal else {'name', 'scope', 'user'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValidationError({'_form': 'Use ' + ', '.join(sorted(allowed))})
        name = body.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValidationError({'name': 'Give the key a name so you can recognise it later.'})
        if len(name.strip()) > 60:
            raise ValidationError({'name': 'Use at most 60 characters.'})
        with self.database._connection() as connection:
            owner = connection.execute('SELECT id, username, role, active FROM evlet_users WHERE username=?',
                                       (actor.username if personal else str(body.get('user', '')).lower(),)).fetchone()
            count = connection.execute('SELECT count(*) FROM evlet_api_keys WHERE user_id=? AND revoked_at IS NULL', (owner['id'] if owner else 0,)).fetchone()[0]
        if owner is None or not owner['active']:
            raise ValidationError({'user': 'Choose an active user.'})
        if (self.roles.get(owner['role']) or {}).get('realm') != 'desk':
            raise ValidationError({'user': f"{owner['username']} signs in on the portal. API keys are for desk users only."})
        if not personal and owner['role'] == 'admin' and actor.role != 'admin':
            raise AccessError(403, ADMIN_ONLY)
        if count >= 20:
            raise ValidationError({'_form': 'This user already has 20 keys. Revoke one first.'})
        scope = self.key_scope(owner, body.get('scope', {'access': 'read', 'models': None}))
        detail = 'Created' if personal else f"Created for {owner['username']}"
        row, token = self.business.auth.create_key(owner['id'], name.strip(), scope, lambda connection, row: audit(
            connection, actor.username, 'API keys', row['name'], detail, None, {'user': owner['username'], 'prefix': row['prefix'], 'scope': scope}))
        return {'key': row, 'token': token}

    def revoke_key(self, actor, key_id, personal=False):
        def check(connection, row):
            if not personal and row['role'] == 'admin' and actor.role != 'admin':
                raise AccessError(403, ADMIN_ONLY)
            audit(connection, actor.username, 'API keys', row['name'], 'Revoked', {'user': row['username'], 'prefix': row['prefix']}, None)
        self.business.auth.revoke_key(key_id, actor.id if personal else None, check)

    def my_keys(self, scope, body, user):
        """/api/_settings/me/keys: a person's own keys. Signed-in desk sessions only."""
        path, method = scope['path'].removeprefix('/api/_settings/me/keys'), scope['method']
        if self.business.settings.portal(scope, user):
            raise AccessError(404, NOT_FOUND)
        if path == '' and method == 'GET':
            # The models a key may be narrowed to: the ones this person can read.
            models = [{'name': name, 'label': getattr(model.Meta, 'label', None) or name} for name, model in self.models().items()
                      if self.business.policy.allows(user, model, 'read')]
            return 200, {'items': self.business.auth.keys(user.id), 'models': models}, []
        if path == '' and method == 'POST':
            # A new key is a sensitive personal setting: a current code with two-step on, the password without.
            body = dict(body) if isinstance(body, dict) else {}
            proof = {key: body.pop(key) for key in ('password', 'code') if key in body}
            self.business.settings.reauthenticate(scope, user, proof)
            return 201, self.create_key(user, body, personal=True), []
        match = re.fullmatch(r'/([1-9][0-9]{0,18})', path)
        if match and method == 'DELETE' and int(match[1]) <= 2**63 - 1:
            self.revoke_key(user, int(match[1]), personal=True)
            return 200, {'items': self.business.auth.keys(user.id)}, []
        raise AccessError(404, NOT_FOUND)

    # ---------- state ----------

    def state(self, user, scope):
        from importlib import metadata
        from .routing import host_name
        try:
            version = metadata.version('evlet')
        except metadata.PackageNotFoundError:
            version = ''
        with self.database._connection() as connection:
            overrides = connection.execute('SELECT count(*) FROM evlet_overrides').fetchone()[0]
            active, total = connection.execute('SELECT sum(active), count(*) FROM evlet_users').fetchone()
            recent = [dict(row) for row in connection.execute('SELECT id,at,actor,area,target,detail FROM evlet_audit ORDER BY id DESC LIMIT 5')]
        models = []
        for name, model in self.models().items():
            fields = model.__declared_fields__
            models.append({'name': name, 'label': getattr(model.Meta, 'label', None) or name, 'app': self.app_of.get(name),
                           'fields': [{'name': key, 'label': field.label or key.replace('_', ' ').capitalize(), 'read_only': bool(field.read_only)}
                                      for key, field in fields.items() if not key.startswith('evlet_')],
                           'scopes': self.presets(model), **self.custom.model_state(model)})
        apps = self.system.apps()
        failed = self.system.failed_jobs()
        attention = self.system.attention(apps, failed)
        connectors = self.business.connectors
        for item in connectors.listing()['items']:
            if item['status'] == 'error':
                title = item['account'] or connectors.declared[item['connector']].title
                attention.append({'tone': 'red', 'icon': 'link', 'page': 'email' if item['account'] else 'connectors',
                                  'title': f"{title} isn't connected", 'text': 'The last test failed. Check the details and test again.'})
        attention += self.proxy_attention()
        controls = self.routes.controls
        return {
            'instance': {'name': self.business.application.get('name', 'Evlet') if self.business.application else 'Evlet',
                         'host': self.routes.primary or host_name(dict(scope.get('headers', [])).get(b'host', b'')) or 'localhost', 'evlet': version, 'version': self.version,
                         'slug': self.system.instance_name(), 'managed': self.system.path is not None},
            'me': user.public(), 'apps': apps, 'models': models, 'roles': self.role_list(), 'jobs': {'failed': failed},
            'routes': [{'host': row.host, 'path': row.path, 'serves': row.serves, 'realm': row.realm, 'source': row.source, 'locked': row.locked} for row in self.routes.rows()],
            'scopes': {key or '': label for key, label in LABELS.items()} | {key: key.replace('_', ' ').capitalize() for key in self.scopes},
            'settings': {key: {'file': file, 'value': self.settings[key]} for key, file in self.files().items()},
            'controls': {'allow_ips': list(controls.allow_ips), 'require_two_step': controls.require_two_step},
            'attention': attention, 'overrides': overrides, 'users': {'active': int(active or 0), 'total': total},
            'audit': recent, 'two_step': self.business.settings.possible(), 'client': str(client_ip(scope, self.business.auth) or ''),
            'remove_fields': self.custom.drop, 'instance_fields': True,
        }

    def proxy_attention(self):
        """One Needs attention item per peer on this machine or network that sends forwarded headers but is not a trusted
        proxy. Any device on the network can send such headers, so each address is shown on its own, never merged."""
        from .auth import trusted_proxies_line
        auth = self.business.auth
        name = self.system.instance_name()
        where = f'Instance(...) in instances/{name}/config.py' if name else 'LocalAuth(...) where the application is built'
        items = []
        for peer in auth.forwarded_peers()[:5]:
            line = trusted_proxies_line(auth.trusted_proxies, [peer])
            items.append({'tone': 'amber', 'icon': 'globe', 'page': 'security', 'title': f'Requests from {peer} name a forwarded client address',
                          'text': f'Evlet ignores the forwarded address because {peer} is not a trusted proxy, so sign-in limits and allowed networks '
                                  f'see {peer} instead of each person. Add this only if {peer} is your reverse proxy: put {line} in {where} and restart. '
                                  'Any other device can send these headers, so otherwise leave it out.',
                          'line': line})
        return items

    # ---------- HTTP ----------

    def dispatch(self, scope, body, user):
        self.check(scope, user)
        path, method = scope['path'].removeprefix('/api/_controls'), scope['method']
        # Every change asks for a recent two-step code; reading, trying a naming series and previewing a print design do not.
        if method not in ('GET', 'HEAD') and not re.fullmatch(r'/naming/[A-Za-z][A-Za-z0-9_]{0,63}/try', path) and path != '/prints/preview':
            self.business.settings.recently_verified(user)
        if (path == '/prints' or path.startswith('/prints/')) and getattr(self.business, 'prints', None) is not None:
            response = self.business.prints.designer.dispatch(path, method, body, user)
            if response is not None:
                return response
        if path == '/state' and method == 'GET':
            return 200, self.state(user, scope), []
        if path == '/overrides':
            if method == 'GET':
                return 200, self.overrides(), []
            if method == 'POST':
                return 200, self.save(user, body), []
        if path == '/users':
            if method == 'GET':
                try:
                    query = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True, max_num_fields=1)
                    if set(query) - {'offset'}:
                        raise ValueError()
                    offset = int(query.get('offset', ['0'])[0])
                except (ValueError, UnicodeError):
                    raise ValidationError({'offset': 'Use a valid offset'})
                return 200, self.users(offset), []
            if method == 'POST':
                return 201, self.add_user(user, body).public(), []
        match = re.fullmatch(r'/users/([1-9][0-9]{0,18})', path)
        if match and method == 'PATCH' and int(match[1]) <= 2**63 - 1:
            self.change_user(user, int(match[1]), body)
            return 204, None, []
        if path == '/roles':
            if method == 'GET':
                return 200, {'items': self.role_list()}, []
            if method == 'POST':
                self.create_role(user, body)
                return 201, {'items': self.role_list()}, []
        match = re.fullmatch(r'/fields/([A-Za-z][A-Za-z0-9_]{0,63})(?:/(x_[a-z0-9_]{1,38}))?', path)
        if match and method == 'POST' and not match[2]:
            return 201, self.custom.add_field(user, match[1], body), []
        if match and method == 'DELETE' and match[2]:
            return 200, self.custom.remove_field(user, match[1], match[2]), []
        match = re.fullmatch(r'/naming/([A-Za-z][A-Za-z0-9_]{0,63})/(counters|try)', path)
        if match and match[2] == 'counters' and method == 'GET':
            return 200, self.custom.counters(match[1]), []
        if match and match[2] == 'counters' and method == 'POST':
            return 200, self.custom.set_counter(user, match[1], body), []
        if match and match[2] == 'try' and method == 'POST':
            return 200, self.custom.try_name(match[1], body), []
        if path == '/periods' and method == 'GET':
            return 200, self.periods(), []
        if path == '/periods' and method == 'POST':
            self.set_period(user, body)
            return 200, self.periods(), []
        if path == '/overrides/export' and method == 'GET':
            from .controls_export import export
            return 200, export(self, params(scope, 'kind', 'key')), []
        if path == '/audit' and method == 'GET':
            return 200, self.system.audit(params(scope, 'area', 'q', 'before')), []
        if path == '/routing' and method == 'GET':
            return 200, self.system.routing(), []
        if path == '/routing/check' and method == 'POST':
            return 200, self.system.check_hosts(user), []
        if path == '/jobs' and method == 'GET':
            return 200, self.system.jobs(), []
        match = re.fullmatch(r'/jobs/schedules/([a-z][a-z0-9_]{0,63})(/run)?', path)
        if match and method == 'POST':
            return 200, self.system.run_schedule(user, match[1]) if match[2] else self.system.set_schedule(user, match[1], body), []
        match = re.fullmatch(r'/jobs/([1-9][0-9]{0,18})/retry', path)
        if match and method == 'POST' and int(match[1]) <= 2**63 - 1:
            return 200, self.system.retry(user, int(match[1])), []
        if path == '/connectors' and method == 'GET':
            return 200, self.business.connectors.listing(), []
        match = re.fullmatch(r'/connectors/([a-z0-9:_-]{2,72})(/test|/default)?', path)
        if match:
            from .connectors import SecretKeyError
            try:
                return self.connector(user, method, match[1], match[2], body)
            except SecretKeyError as exc:
                raise AccessError(503, str(exc)) from None
        if path == '/mcp' and method == 'GET':
            return 200, self.business.assistants.overview(scope), []
        if path == '/tools' and method == 'GET':
            # The Tool log: every call from Eva, MCP clients and the API.
            from .tools import query_of
            return 200, self.business.tools.calls(query_of(scope)), []
        if path in ('/eva', '/eva/usage') and method == 'GET':
            from .eva import configuration, usage_report
            eva = self.business.eva
            if path == '/eva':
                return 200, configuration(self.business), []
            if eva is None:
                raise AccessError(404, 'Eva is turned off in the application configuration.')
            return 200, usage_report(eva, scope), []
        match = re.fullmatch(r'/mcp/([1-9][0-9]{0,18})', path)
        if match and method == 'DELETE' and int(match[1]) <= 2**63 - 1:
            self.business.assistants.revoke(user, int(match[1]), personal=False)
            return 200, self.business.assistants.overview(scope), []
        if path == '/keys':
            if method == 'GET':
                return 200, {'items': self.business.auth.keys()}, []
            if method == 'POST':
                return 201, self.create_key(user, body), []
        match = re.fullmatch(r'/keys/([1-9][0-9]{0,18})', path)
        if match and method == 'DELETE' and int(match[1]) <= 2**63 - 1:
            self.revoke_key(user, int(match[1]))
            return 200, {'items': self.business.auth.keys()}, []
        match = re.fullmatch(r'/roles/([a-z][a-z0-9_]{1,30})', path)
        if match and method == 'PATCH':
            self.change_role(user, match[1], body)
            return 200, {'items': self.role_list()}, []
        if match and method == 'DELETE':
            self.delete_role(user, match[1])
            return 200, {'items': self.role_list()}, []
        raise AccessError(404, NOT_FOUND)


def params(scope, *names):
    """The named query parameters of a request, each once; anything else is refused."""
    try:
        found = parse_qs(scope.get('query_string', b'').decode('ascii'), keep_blank_values=True, max_num_fields=len(names))
    except (ValueError, UnicodeError):
        raise ValidationError({'_query': 'Use valid query parameters'}) from None
    if set(found) - set(names) or any(len(values) > 1 for values in found.values()):
        raise ValidationError({'_query': 'Use only ' + ', '.join(names)})
    return {key: values[0] for key, values in found.items()}


class _Probe:
    id, username, role, links = 0, 'probe', 'viewer', ()

