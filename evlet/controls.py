"""Controls: roles, overrides, the audit log and the config version behind /controls."""

import ipaddress
import json
import re
import sqlite3
from threading import Lock
import time
from types import MappingProxyType
from urllib.parse import parse_qs, quote

from .auth import PORTAL, RESERVED_ROLES, ROLE_ID, ROLES, AccessError
from .customization import Customization, attach
from .fields import Link
from .models import ValidationError
from .permissions import CORRECTIONS, Policy, Rule, default_rule, own_customer

OPERATIONS = ('read', 'create', 'update', 'delete', 'journal', *CORRECTIONS)
KINDS = ('perm', 'field', 'custom', 'layout', 'list', 'naming', 'setting')
PAGES = ('overview', 'users', 'roles', 'permissions', 'security', 'models', 'naming')
SETTINGS = MappingProxyType({
    'security.password_min': 15, 'security.session_hours': 8, 'security.lockout': 10,
    'security.two_step_roles': ['admin'], 'security.controls_ips': [],
})
NOT_FOUND = 'Route not found'
AREAS = {'perm': 'Permissions', 'setting': 'Security', 'field': 'Models', 'layout': 'Models', 'list': 'Models', 'naming': 'Naming'}
ADMIN_ONLY = 'Only an admin can do this: change admin accounts, give the admin role, manage roles or decide who opens Controls.'
TWO_STEP = 'Controls needs two-step sign-in, which this instance does not have yet. Remove require_two_step from routes.py for now.'
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
        connection.execute('DELETE FROM evlet_sessions WHERE user_id=?', (row[0],))
        audit(connection, 'cli', 'Users', username.lower(), 'Made an active admin from the server shell',
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


def client_ip(scope, auth):
    """The address the Controls allowlist checks: LocalAuth.client_address, through trusted proxies only."""
    try:
        address = ipaddress.ip_address(auth.client_address(scope))
    except ValueError:
        return None
    return (address.ipv4_mapped or address) if address.version == 6 else address


def _networks(values):
    return [ipaddress.ip_network(value, strict=False) for value in values]


def strictest(key, value, roles):
    """The strictest safe value of a stored setting that no longer validates."""
    number = type(value) is int
    if key == 'security.password_min':
        return min(max(value, 15), 64) if number else 64
    if key == 'security.session_hours':
        return min(max(value, 1), SETTINGS[key]) if number else 1
    if key == 'security.lockout':
        return min(max(value, 3), SETTINGS[key]) if number else 3
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
        self.roles, self.settings, self.problems = {}, dict(SETTINGS), []
        self.custom = Customization(self)

    # ---------- set-up ----------

    def load(self, definitions, packages=()):
        """App metadata from build_instance: names, versions, models, declared roles and scopes."""
        versions = {package.name: getattr(package, 'version', '') for package in packages}
        for name, definition in definitions:
            self.apps.append({'id': name, 'version': versions.get(name, ''), 'models': [model.__name__ for model in definition.models]})
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
        """Build the Policy, settings and model customization. An override that no longer validates fails
        closed and is reported. Instance fields are attached first, so the rest sees them."""
        problems, perms, settings = [], {}, dict(SETTINGS)
        self.roles = roles
        if not self.business.tenant:
            attach(self.registry, fields)
        self.custom.apply([row for row in stored if row['kind'] in ('field', 'layout', 'list', 'naming')], problems)
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
                    settings[row['key']] = strictest(row['key'], value, roles)
        try:
            policy = Policy(self.registry, rules=self.file.declared, roles=self.role_bases(roles), overrides=perms, read_only=self.custom.read_only)
        except ValueError as exc:
            problems.append({'kind': 'perm', 'key': 'all', 'error': f'{exc}. Every role except admin is denied until this is fixed.'})
            policy = Policy(self.registry, rules=self.file.declared, roles=self.role_bases(roles), read_only=self.custom.read_only,
                            overrides={(role, model): Rule(()) for role in roles if role != 'admin' for model in self.registry.models.values()})
        self.business.policy = policy
        if self.business.jobs is not None:
            self.business.jobs.policy = policy
        auth = self.business.auth
        auth.password_min, auth.session_hours, auth.lockout = settings['security.password_min'], settings['security.session_hours'], settings['security.lockout']
        self.settings, self.problems, self.version = settings, problems, version

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
        if not self.host_allowed(scope) or role is None or role['realm'] != 'desk':
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
        if self.routes.controls.require_two_step:
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
        return {model.__name__: model for model in self.registry.models.values() if model not in children}

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
        if read is not None and not read:
            raise ValueError('Keep at least one readable field, or turn off read instead.')
        if read is not None and write is not None and set(write) - set(read):
            raise ValueError('Fields people can write must also be readable.')
        file = self.file_rule(role, model)
        if set(ops) - set(file.operations):
            missing = ', '.join(op for op in OPERATIONS if op in set(ops) - set(file.operations))
            raise ValueError(f'The app does not give {role} {missing} on {model.__name__}. An override can only take access away.')
        readable = set(fields if file.read_fields is None else file.read_fields)
        writable = set(fields if file.write_fields is None else file.write_fields) & readable
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
        if key not in SETTINGS:
            raise ValueError('Choose a known setting.')
        file = SETTINGS[key]
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
            if key not in SETTINGS:
                raise ValueError('Choose a known setting.')
            if value is None:
                return None
            value = self.check_setting(key, value)
            return None if value == SETTINGS[key] else value
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
        for key, file in SETTINGS.items():
            items.append({'kind': 'setting', 'key': key, 'file': file, 'value': value('setting', key), **meta('setting', key)})
        for kind, key, file in self.custom.items():
            items.append({'kind': kind, 'key': key, 'file': file, 'value': value(kind, key), **meta(kind, key)})
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
                area, target = AREAS[kind], key.replace('|', ' · ')
                audit(connection, user.username, area, target, 'Reset to the file value' if value is None else 'Changed', before, value)
                if key == 'security.session_hours':
                    # Sessions keep their start: a shorter length ends the ones already older than it.
                    shorter = self.settings[key] - (SETTINGS[key] if value is None else value)
                    if shorter > 0:
                        connection.execute('UPDATE evlet_sessions SET expires=expires-? WHERE expires>?', (shorter * 3600, now))
            version = self.publish(tx)
        self.refresh()
        return {'version': version, 'saved': len(checked)}

    def audit(self, *row):
        audit(*row)

    def publish(self, tx):
        version = bump(tx.connection)
        if self.business.events is not None:
            self.business.events.publish_config(tx, version)
        return version

    # ---------- users and roles ----------

    def users(self, offset):
        page = self.business.auth.user_page(offset)
        with self.database._connection() as connection:
            sessions = {row[0]: row[1] for row in connection.execute('SELECT user_id, count(*) FROM evlet_sessions WHERE expires>? GROUP BY user_id', (int(time.time()),))}
        for item in page['items']:
            item['active'], item['sessions'] = bool(item['active']), sessions.get(item['id'], 0)
        return page

    def add_user(self, actor, body):
        if not isinstance(body, dict) or set(body) - {'username', 'password', 'role'}:
            raise ValidationError({'_form': 'Use username, password and role'})
        if body.get('role') == 'admin' and actor.role != 'admin':
            raise AccessError(403, ADMIN_ONLY)
        return self.business.auth.add_user(dict(body), lambda connection, user: audit(
            connection, actor.username, 'Users', user.username, f'Added as {user.role}', None, {'role': user.role}))

    def change_user(self, actor, user_id, body):
        if not isinstance(body, dict) or not body or set(body) - {'role', 'active', 'password', 'sessions'}:
            raise ValidationError({'_form': 'Use role, active, password or sessions'})
        if 'sessions' in body and body['sessions'] != 'end':
            raise ValidationError({'sessions': 'Use "end" to sign out every session'})
        changes = {key: value for key, value in body.items() if key != 'sessions'}

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
            tx.connection.execute('DELETE FROM evlet_sessions WHERE user_id=?', (user_id,))
            after()

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
        pages = {'perm': 'permissions', 'setting': 'security', 'naming': 'naming'}
        attention = [{'tone': 'amber', 'icon': 'alert', 'page': pages.get(item['kind'], 'models'),
                      'title': f"An override no longer applies: {item['key'].replace('|', ' · ')}", 'text': item['error']} for item in self.problems]
        controls = self.routes.controls
        return {
            'instance': {'name': self.business.application.get('name', 'Evlet') if self.business.application else 'Evlet',
                         'host': self.routes.primary or host_name(dict(scope.get('headers', [])).get(b'host', b'')) or 'localhost', 'evlet': version, 'version': self.version},
            'me': user.public(), 'apps': self.apps, 'models': models, 'roles': self.role_list(),
            'routes': [{'host': row.host, 'path': row.path, 'serves': row.serves, 'realm': row.realm, 'source': row.source, 'locked': row.locked} for row in self.routes.rows()],
            'scopes': {key or '': label for key, label in LABELS.items()} | {key: key.replace('_', ' ').capitalize() for key in self.scopes},
            'settings': {key: {'file': SETTINGS[key], 'value': self.settings[key]} for key in SETTINGS},
            'controls': {'allow_ips': list(controls.allow_ips), 'require_two_step': controls.require_two_step},
            'attention': attention, 'overrides': overrides, 'users': {'active': active or 0, 'total': total},
            'audit': recent, 'two_step': False, 'client': str(client_ip(scope, self.business.auth) or ''),
            'remove_fields': self.custom.drop, 'instance_fields': not self.business.tenant,
        }

    # ---------- HTTP ----------

    def dispatch(self, scope, body, user):
        self.check(scope, user)
        path, method = scope['path'].removeprefix('/api/_controls'), scope['method']
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
        match = re.fullmatch(r'/roles/([a-z][a-z0-9_]{1,30})', path)
        if match and method == 'PATCH':
            self.change_role(user, match[1], body)
            return 200, {'items': self.role_list()}, []
        if match and method == 'DELETE':
            self.delete_role(user, match[1])
            return 200, {'items': self.role_list()}, []
        raise AccessError(404, NOT_FOUND)


class _Probe:
    id, username, role, links = 0, 'probe', 'viewer', ()

