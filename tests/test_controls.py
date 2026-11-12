"""Controls: access, break glass, overrides, roles, the audit log and the config version."""

import json
import sqlite3

import pytest

from evlet import AppDefinition, Application, ControlsAccess, Database, LocalAuth, Policy, Rule
from evlet.auth import LAST_ADMIN, User
from evlet.cli.install import install
from evlet.cli.main import main
from evlet.controls import ADMIN_ONLY, TWO_STEP, grant
from evlet.routing import resolve_routes
from examples.playground.app import build_app, create_app, playground_routes
from examples.playground.migrate import configured_database
from examples.playground.models import Customer, Project, registry
from test_business import PASSWORD, client, hashed, http, secured
from test_cli_lifecycle import environment_stub, project, runtime

DESK, PORTAL_HOST = b'127.0.0.1:8000', b'portal.localhost:8000'
ENDPOINTS = (('GET', '/api/_controls/state'), ('GET', '/api/_controls/overrides'), ('POST', '/api/_controls/overrides'),
             ('GET', '/api/_controls/users'), ('POST', '/api/_controls/users'), ('PATCH', '/api/_controls/users/1'),
             ('GET', '/api/_controls/roles'), ('POST', '/api/_controls/roles'), ('PATCH', '/api/_controls/roles/editor'),
             ('DELETE', '/api/_controls/roles/editor'), ('GET', '/api/_controls/missing'))


@pytest.fixture(autouse=True)
def password(hashed):
    add_user.hashed = hashed


def add_user(app, username, role):
    with app.database.transaction() as tx:
        tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password', (username, add_user.hashed, role))


def save(c, *changes):
    return c('POST', '/api/_controls/overrides', {'changes': [{'kind': kind, 'key': key, 'value': value} for kind, key, value in changes]})


def version(app):
    with app.database._connection() as db:
        return db.execute('SELECT version FROM evlet_config').fetchone()[0]


def test_roles_without_controls_get_404_on_the_page_and_every_endpoint(secured):
    for role in ('editor', 'viewer'):
        c = client(secured, role)
        assert c('GET', '/controls')[0] == 404
        assert c('GET', '/controls/users')[0] == 404
        for method, path in ENDPOINTS:
            assert c(method, path, {})[:2] == (404, {'error': 'Route not found'}), (role, method, path)
    status, _, headers = http(secured, 'GET', '/controls/users')
    assert status == 302 and headers[b'location'] == b'/login?next=/controls/users'
    admin = client(secured)
    assert admin('GET', '/api/_controls/state')[0] == 200
    assert admin('GET', '/controls/nothing')[0] == 404
    status, _, headers = http(secured, 'GET', '/settings/accounts')
    assert status == 301 and headers[b'location'] == b'/controls/users'


def test_a_role_that_opens_controls_gets_in_and_admin_cannot_be_locked_out(secured):
    admin = client(secured)
    assert admin('POST', '/api/_controls/roles', {'id': 'ops', 'base': 'editor', 'description': 'Runs the instance'})[0] == 201
    add_user(secured, 'opsuser', 'ops')
    assert client(secured, 'opsuser')('GET', '/api/_controls/state')[0] == 404
    assert admin('PATCH', '/api/_controls/roles/ops', {'opens_controls': True})[0] == 200
    state = client(secured, 'opsuser')('GET', '/api/_controls/state')[1]
    assert state['me']['role'] == 'ops' and next(role for role in state['roles'] if role['id'] == 'ops')['opens_controls']
    status, payload, _ = admin('PATCH', '/api/_controls/roles/admin', {'opens_controls': False})
    assert status == 422 and 'Admin always opens Controls' in payload['fields']['opens_controls']


@pytest.fixture
def hosts(tmp_path, monkeypatch):
    """The Playground with its portal sub-host, from the real route table."""
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'hosts.sqlite3'))
    routes = playground_routes(8000)
    database = configured_database()
    app = build_app(database, LocalAuth(database, origins=routes.origins(), realms=routes.realms(), secure_cookies=False), routes=routes)
    app.initialize()
    for name, role in (('admin', 'admin'), ('buyer', 'portal')):
        add_user(app, name, role)
    yield app
    app.database.close()


def on_host(app, username, host):
    status, data, headers = http(app, 'POST', '/api/auth/login', {'username': username, 'password': PASSWORD}, headers={b'host': host})
    assert status == 200, data
    cookie = headers[b'set-cookie'].decode().split(';')[0]
    def call(method, path, body=None, **kwargs):
        return http(app, method, path, body, cookie=cookie, csrf=data['csrf'], headers={b'host': host, **kwargs.pop('headers', {})}, **kwargs)
    call.cookie = headers[b'set-cookie'].decode()
    return call


def test_controls_and_account_routes_answer_404_on_a_sub_host(hosts):
    admin, buyer = on_host(hosts, 'admin', DESK), on_host(hosts, 'buyer', PORTAL_HOST)
    assert admin('GET', '/api/_controls/state')[0] == 200 and admin('GET', '/api/auth/users')[0] == 200
    desk_cookie = admin.cookie.split(';')[0]
    for method, path in (*ENDPOINTS, ('GET', '/api/auth/users'), ('POST', '/api/auth/users'), ('PATCH', '/api/auth/users/1')):
        assert buyer(method, path, {})[0] == 404, (method, path)
        assert http(hosts, method, path, {}, cookie=desk_cookie, headers={b'host': PORTAL_HOST})[0] == 404, (method, path)
    for path in ('/controls', '/controls/users', '/settings/accounts'):
        assert buyer('GET', path)[0] == 404 and http(hosts, 'GET', path, cookie=desk_cookie, headers={b'host': PORTAL_HOST})[0] == 404
    scope = {'headers': [(b'host', PORTAL_HOST)], 'client': ('127.0.0.1', 1)}
    assert not hosts.business.controls.host_allowed(scope)


def test_portal_is_a_framework_role_and_portal_custom_roles_never_open_controls(hosts):
    admin = on_host(hosts, 'admin', DESK)
    roles = {role['id']: role for role in admin('GET', '/api/_controls/roles')[1]['items']}
    assert roles['portal'] | {'users': 0} == {**roles['portal'], 'kind': 'framework', 'base': None, 'realm': 'portal', 'opens_controls': False, 'users': 0}
    assert 'public' not in roles
    assert admin('POST', '/api/_controls/roles', {'id': 'partner', 'base': 'editor', 'realm': 'portal'})[0] == 422
    assert admin('POST', '/api/_controls/roles', {'id': 'partner', 'realm': 'portal', 'description': 'Resellers'})[0] == 201
    partner = next(role for role in admin('GET', '/api/_controls/roles')[1]['items'] if role['id'] == 'partner')
    assert (partner['base'], partner['realm'], partner['opens_controls']) == ('portal', 'portal', False)
    status, payload, _ = admin('PATCH', '/api/_controls/roles/partner', {'opens_controls': True})
    assert status == 422 and 'Portal roles can never open Controls' in payload['fields']['opens_controls']
    for role in ('public', 'portal'):
        assert admin('POST', '/api/_controls/users', {'username': 'x' + role, 'password': PASSWORD, 'role': role})[0] == (422 if role == 'public' else 201)
    assert admin('POST', '/api/_controls/users', {'username': 'reseller', 'password': PASSWORD, 'role': 'partner'})[0] == 201
    assert http(hosts, 'POST', '/api/auth/login', {'username': 'reseller', 'password': PASSWORD})[0] == 401
    reseller = on_host(hosts, 'reseller', PORTAL_HOST)
    # A role based on portal gets portal's app rules, here its own customer only, and nothing else.
    assert reseller('GET', '/api/customer')[:2] == (200, {**reseller('GET', '/api/customer')[1], 'items': []})
    policy = hosts.business.policy
    assert policy.rule(User(0, 'r', 'partner'), Customer) == policy.rule(User(0, 'b', 'portal'), Customer)
    assert policy.rule(User(0, 'r', 'partner'), Project).operations == ()


def test_own_customer_preset_uses_the_links_the_session_preloads(hosts):
    admin = on_host(hosts, 'admin', DESK)
    add_user(hosts, 'rep', 'editor')
    ids = [admin('POST', '/api/customer', {'customer_name': name, 'email': f'{name}@example.com'})[1]['id'] for name in ('Mine', 'Theirs')]
    for customer_id in ids:
        assert admin('POST', '/api/project', {'title': f'Project {customer_id}', 'customer': customer_id})[0] == 201
    rep = on_host(hosts, 'rep', DESK)
    assert len(rep('GET', '/api/project')[1]['items']) == 2
    value = {'ops': ['read', 'create', 'update', 'delete'], 'read': None, 'write': None, 'scope': 'own_customer'}
    assert save(admin, ('perm', 'editor|Project', value), ('perm', 'editor|Customer', value))[0] == 200
    assert rep('GET', '/api/project')[1]['items'] == [] and rep('GET', '/api/customer')[1]['items'] == []
    rep_id = user_id(admin, 'rep')
    hosts.business.auth.link_user(rep_id, Customer, ids[0])
    assert [row['customer'] for row in rep('GET', '/api/project')[1]['items']] == [ids[0]]
    assert [row['id'] for row in rep('GET', '/api/customer')[1]['items']] == [ids[0]]
    item = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['key'] == 'editor|Project')
    assert item['value']['scope'] == 'own_customer'


def test_session_length_applies_to_every_realm(hosts):
    admin = on_host(hosts, 'admin', DESK)
    assert save(admin, ('setting', 'security.session_hours', 2))[0] == 200
    buyer = on_host(hosts, 'buyer', PORTAL_HOST)
    assert 'Max-Age=7200' in buyer.cookie and buyer.cookie.startswith('evlet_portal_session=')
    assert 'Max-Age=7200' in on_host(hosts, 'admin', DESK).cookie
    with hosts.database._connection() as db:
        assert db.execute("SELECT max(expires) - strftime('%s','now') FROM evlet_sessions WHERE realm='portal'").fetchone()[0] <= 7200
    with hosts.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_sessions SET expires=expires-5400 WHERE realm='portal'")
    assert buyer('GET', '/api/auth/session')[1]['user']['username'] == 'buyer'
    assert save(admin, ('setting', 'security.session_hours', 1))[0] == 200
    assert buyer('GET', '/api/auth/session')[1]['user'] is None
    assert admin('GET', '/api/auth/session')[1]['user']['username'] == 'admin'


def test_controls_reads_the_client_address_through_trusted_proxies_only(tmp_path, hashed):
    def instance(name, proxies):
        database = Database(tmp_path / f'{name}.sqlite3', registry)
        app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, trusted_proxies=proxies))
        app.initialize()
        add_user(app, 'admin', 'admin')
        admin = client(app)
        assert save(admin, ('setting', 'security.controls_ips', ['10.0.0.0/8']))[0] == 200
        return app, admin
    forwarded = lambda value: {b'x-forwarded-for': value}
    _, admin = instance('proxied', ('127.0.0.1',))
    assert admin('GET', '/api/_controls/state', headers=forwarded(b'10.1.1.1'))[0] == 200
    assert admin('GET', '/api/_controls/state', headers=forwarded(b'10.1.1.1'))[1]['client'] == '10.1.1.1'
    # A client adds its own entry on the left; the proxy appends the real address, which is not allowed.
    assert admin('GET', '/api/_controls/state', headers=forwarded(b'10.1.1.1, 203.0.113.5'))[0] == 404
    assert admin('GET', '/api/auth/users', headers=forwarded(b'203.0.113.5'))[0] == 403
    # From a peer that is not a trusted proxy, the header is ignored.
    assert admin('GET', '/api/_controls/state', client='203.0.113.9', headers=forwarded(b'10.1.1.1'))[0] == 404
    _, direct = instance('direct', ())
    assert direct('GET', '/api/_controls/state', headers=forwarded(b'10.1.1.1'))[0] == 404
    assert direct('GET', '/api/_controls/state', client='10.1.1.1')[0] == 200


def test_forwarded_headers_from_an_untrusted_proxy_are_ignored_and_shown_under_needs_attention(tmp_path, hashed, monkeypatch):
    import evlet.auth as auth_module
    database = Database(tmp_path / 'proxy.sqlite3', registry)
    app = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False))
    app.initialize()
    add_user(app, 'admin', 'admin')
    admin = client(app)
    forwarded = {b'x-forwarded-for': b'203.0.113.5'}
    def peers():
        with database._connection() as db:
            return [row[0] for row in db.execute('SELECT peer FROM evlet_forwarded_peers ORDER BY peer')]
    globe = lambda: [item for item in admin('GET', '/api/_controls/state')[1]['attention'] if item['icon'] == 'globe']
    # Normal use: the header is ignored, so the proxy's own address is the client.
    assert admin('GET', '/api/_controls/state', headers=forwarded)[1]['client'] == '127.0.0.1'
    # A few requests are not a proxy: nothing is noted until a steady stream arrives.
    for _ in range(auth_module.FORWARDED_REQUESTS - 2):
        admin('GET', '/api/schema', headers={b'forwarded': b'for=203.0.113.5'})
    assert peers() == [] and globe() == []
    admin('GET', '/api/schema', headers=forwarded)
    [item] = globe()
    assert item['page'] == 'security' and item['title'] == 'Requests from 127.0.0.1 name a forwarded client address'
    assert item['line'] == 'trusted_proxies=("127.0.0.1",)' and 'Add this only if 127.0.0.1 is your reverse proxy' in item['text']
    assert 'put trusted_proxies=("127.0.0.1",) in LocalAuth(...)' in item['text']
    # Further requests stay in memory: no write until ten minutes have passed.
    with database._connection() as db:
        before = db.execute('SELECT last_seen FROM evlet_forwarded_peers').fetchone()[0]
    for _ in range(30):
        admin('GET', '/api/schema', headers=forwarded)
    with database._connection() as db:
        assert db.execute('SELECT last_seen FROM evlet_forwarded_peers').fetchone()[0] == before
    # Another device on the network, even without signing in, gets its own item and line, never a merged one.
    for _ in range(auth_module.FORWARDED_REQUESTS):
        http(app, 'GET', '/api/auth/session', client='10.0.0.7', headers=forwarded)
    items = globe()
    assert sorted(item['line'] for item in items) == ['trusted_proxies=("10.0.0.7",)', 'trusted_proxies=("127.0.0.1",)']
    assert all(item['text'].count('Add this only if') == 1 for item in items)
    # Public peers are people behind their own proxies.
    for _ in range(auth_module.FORWARDED_REQUESTS):
        admin('GET', '/api/schema', client='93.184.216.34', headers={b'x-forwarded-for': b'10.0.0.1'})
    assert peers() == ['10.0.0.7', '127.0.0.1']
    # Once the proxy is listed its item goes away, without waiting for the note to expire.
    database.close()
    database = Database(tmp_path / 'proxy.sqlite3', registry)
    trusted = Application(database, auth=LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False, trusted_proxies=('127.0.0.1',)))
    trusted.initialize()
    state = client(trusted)('GET', '/api/_controls/state', headers={b'x-forwarded-for': b'10.9.9.9'})[1]
    assert state['client'] == '10.9.9.9' and [item['line'] for item in state['attention'] if item['icon'] == 'globe'] == ['trusted_proxies=("127.0.0.1", "10.0.0.7")']
    database.close()


def test_forwarded_peer_notes_stay_bounded(tmp_path, monkeypatch):
    import evlet.auth as auth_module
    database = Database(tmp_path / 'bounded.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    auth.initialize()
    scope = lambda index: {'client': (f'10.1.{index // 250}.{index % 250 + 1}', 1), 'headers': []}
    # The in-memory cache drops the peer it saw longest ago, so a busy proxy keeps its count.
    for index in range(auth_module.FORWARDED_CACHE + 6):
        auth.note_forwarded(scope(0))
        auth.note_forwarded(scope(index + 1))
    assert len(auth._forwarded) == auth_module.FORWARDED_CACHE and '10.1.0.1' in auth._forwarded and '10.1.0.2' not in auth._forwarded
    # The table keeps the newest peers only.
    monkeypatch.setattr(auth_module, 'FORWARDED_REQUESTS', 1)
    for index in range(auth_module.FORWARDED_ROWS + 10):
        auth.note_forwarded(scope(500 + index))
    database.logs.flush()  # the notes are saved in batches (evlet/writes.py LogBuffer)
    with database._connection() as db:
        assert db.execute('SELECT count(*) FROM evlet_forwarded_peers').fetchone()[0] == auth_module.FORWARDED_ROWS
    database.close()


def test_allowed_networks_and_two_step_requirement(secured, tmp_path):
    admin = client(secured)
    assert save(admin, ('setting', 'security.controls_ips', ['10.0.0.0/8', '192.168.1.5']))[0] == 200
    assert admin('GET', '/api/_controls/state')[0] == 404
    assert admin('GET', '/api/_controls/state', client='10.2.3.4')[0] == 200
    assert admin('GET', '/api/_controls/state', client='::ffff:192.168.1.5')[0] == 200
    database = Database(tmp_path / 'two.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    app = Application(database, auth=auth, routes=resolve_routes(routes_module={'controls': ControlsAccess(require_two_step=True)}))
    app.initialize()
    add_user(app, 'admin', 'admin')
    assert client(app)('GET', '/api/_controls/state')[:2] == (403, {'error': TWO_STEP})


def test_break_glass_grant_makes_an_active_admin_with_a_cli_audit_row(project):
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    try:
        mariam = app.business.auth.create_user(username='mariam', password=PASSWORD, role='viewer')
        app.business.auth.update_user(mariam.id, {'active': False})
    finally:
        app.database.close()
    with pytest.raises(SystemExit):
        main(['controls', 'grant', 'acme', 'nobody', '--project', str(project)])
    main(['controls', 'grant', 'acme', 'mariam', '--project', str(project)])
    with sqlite3.connect(project / 'instances/acme/data/database.sqlite3') as db:
        assert db.execute("SELECT role, active FROM evlet_users WHERE username='mariam'").fetchone() == ('admin', 1)
        assert db.execute('SELECT actor, area, target FROM evlet_audit').fetchall() == [('cli', 'Users', 'mariam')]


def test_an_override_set_is_atomic_and_reports_each_invalid_item(secured):
    admin = client(secured)
    before = version(secured)
    status, payload, _ = save(admin, ('perm', 'editor|Customer', {'ops': ['read'], 'read': None, 'write': None, 'scope': None}),
                              ('setting', 'security.password_min', 65), ('perm', 'admin|Customer', {'ops': [], 'read': None, 'write': None, 'scope': None}))
    assert status == 422 and set(payload['fields']) == {'changes.1', 'changes.2'}
    assert payload['fields']['changes.2'] == 'Admin always has full access. Its rules cannot be changed.'
    assert version(secured) == before
    assert all(item['value'] is None for item in admin('GET', '/api/_controls/overrides')[1]['items'])
    assert client(secured, 'editor')('POST', '/api/customer', {'customer_name': 'Still allowed'})[0] == 201


@pytest.mark.parametrize('kind,key,value,message', [
    ('perm', 'viewer|Customer', {'ops': ['read', 'create'], 'read': None, 'write': None, 'scope': None}, 'can only take access away'),
    ('perm', 'clerk|Customer', {'ops': ['read'], 'read': ['customer_name', 'email', 'notes'], 'write': None, 'scope': None}, 'can only hide more'),
    ('perm', 'clerk|Customer', {'ops': ['read', 'update'], 'read': ['customer_name'], 'write': ['customer_name'], 'scope': None}, 'can only allow fewer'),
    ('perm', 'clerk|Project', {'ops': ['read'], 'read': None, 'write': None, 'scope': None}, 'comes from the app'),
    ('perm', 'editor|Customer', {'ops': ['create'], 'read': None, 'write': None, 'scope': None}, 'need read access'),
    ('perm', 'editor|Customer', {'ops': ['read'], 'read': None, 'write': ['customer_name'], 'scope': None}, 'need create or update'),
    ('perm', 'editor|Customer', {'ops': [], 'read': None, 'write': None, 'scope': 'owner'}, 'must be empty'),
    ('perm', 'editor|Customer', {'ops': ['read', 'update'], 'read': ['customer_name'], 'write': ['email'], 'scope': None}, 'must also be readable'),
    ('perm', 'editor|Customer', {'ops': ['read'], 'read': [], 'write': None, 'scope': None}, 'at least one readable field'),
    ('perm', 'editor|Customer', {'ops': ['read'], 'read': None, 'write': None, 'scope': 'owner'}, 'not available for Customer'),
    ('setting', 'security.password_min', 0, 'from 1 to 64'),
    ('setting', 'security.password_min', 65, 'from 1 to 64'),
    ('setting', 'security.two_step', 'yes', 'on or off'),
    ('setting', 'security.session_hours', 13, 'can only go down'),
    ('setting', 'security.lockout', 6, 'can only go down'),
    ('setting', 'security.lockout', 2, 'to 3'),
    ('setting', 'security.two_step_roles', ['nobody'], 'Choose existing roles'),
    ('setting', 'security.controls_ips', ['example.com'], "isn't an IP address or range"),
])
def test_stricter_only_rules_reject_the_looser_value(tmp_path, hashed, kind, key, value, message):
    database = Database(tmp_path / 'strict.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    rules = {('clerk', Customer): Rule(('read', 'update'), read_fields=('customer_name', 'email'), write_fields=('email',)),
             ('clerk', Project): Rule(('read',), scope=lambda user: {'customer': user.id})}
    app = Application(database, auth=auth, policy=Policy(registry, rules=rules, roles={'admin': 'admin', 'editor': 'editor', 'viewer': 'viewer', 'clerk': 'editor'}))
    app.initialize()
    add_user(app, 'admin', 'admin')
    status, payload, _ = save(client(app), (kind, key, value))
    assert status == 422 and message in payload['fields']['changes.0'], payload


def test_saved_overrides_apply_and_an_equal_value_is_not_stored(secured):
    admin = client(secured)
    assert save(admin, ('perm', 'editor|Customer', {'ops': ['read'], 'read': ['customer_name'], 'write': None, 'scope': None}),
                ('setting', 'security.password_min', 20), ('setting', 'security.lockout', 5))[0] == 200
    items = {(item['kind'], item['key']): item for item in admin('GET', '/api/_controls/overrides')[1]['items']}
    assert items['perm', 'editor|Customer']['value']['read'] == ['customer_name'] and items['perm', 'editor|Customer']['changed_by'] == 'admin'
    assert items['setting', 'security.lockout']['value'] is None and items['setting', 'security.password_min']['value'] == 20
    editor = client(secured, 'editor')
    assert editor('POST', '/api/customer', {'customer_name': 'Blocked'})[0] == 403
    assert admin('POST', '/api/customer', {'customer_name': 'Visible'})[0] == 201
    assert set(editor('GET', '/api/customer')[1]['items'][0]) == {'id', 'customer_name'}
    status, payload, _ = admin('POST', '/api/_controls/users', {'username': 'short', 'password': 'x' * 19, 'role': 'viewer'})
    assert status == 422 and payload['fields']['password'] == 'Use between 20 and 128 characters'
    assert save(admin, ('perm', 'editor|Customer', None))[0] == 200
    assert editor('POST', '/api/customer', {'customer_name': 'Allowed again'})[0] == 201
    with secured.database._connection() as db:
        audit = db.execute('SELECT actor, area, target, detail FROM evlet_audit ORDER BY id').fetchall()
    assert [tuple(row) for row in audit][-1] == ('admin', 'Permissions', 'editor · Customer', 'Reset to the file value')
    events = admin('GET', '/api/_events?cursor=0')[1]['items']
    assert {'type': 'config.changed', 'version': version(secured)} in events


def test_a_second_application_on_the_same_database_picks_up_the_new_policy(secured, tmp_path, monkeypatch):
    other = create_app()
    other.database.initialize()
    other.business.initialize()
    editor = client(other, 'editor')
    assert editor('POST', '/api/customer', {'customer_name': 'Before'})[0] == 201
    assert save(client(secured), ('perm', 'editor|Customer', {'ops': ['read'], 'read': None, 'write': None, 'scope': None}))[0] == 200
    assert editor('POST', '/api/customer', {'customer_name': 'After'})[0] == 403
    assert other.business.controls.version == version(secured)


def test_a_custom_role_falls_back_to_its_base(secured):
    admin = client(secured)
    assert admin('POST', '/api/_controls/roles', {'id': 'clerk', 'base': 'viewer'})[0] == 201
    assert admin('POST', '/api/_controls/roles', {'id': 'sales', 'base': 'editor'})[0] == 201
    add_user(secured, 'clerkuser', 'clerk')
    add_user(secured, 'salesuser', 'sales')
    clerk, sales = client(secured, 'clerkuser'), client(secured, 'salesuser')
    assert clerk('GET', '/api/customer')[0] == 200 and clerk('POST', '/api/customer', {'customer_name': 'No'})[0] == 403
    assert sales('POST', '/api/customer', {'customer_name': 'Yes'})[0] == 201
    assert save(admin, ('perm', 'sales|Customer', {'ops': ['read'], 'read': None, 'write': None, 'scope': None}))[0] == 200
    assert sales('POST', '/api/customer', {'customer_name': 'Not now'})[0] == 403
    assert client(secured, 'editor')('POST', '/api/customer', {'customer_name': 'Editor still can'})[0] == 201
    for role_id, message in (('Sales', 'Use 2 to 31'), ('controls', 'That name is taken'), ('sales', 'That name is taken')):
        status, payload, _ = admin('POST', '/api/_controls/roles', {'id': role_id, 'base': 'editor'})
        assert status == 422 and message in payload['fields']['id']


def test_an_app_rule_for_a_custom_role(tmp_path, hashed):
    database = Database(tmp_path / 'app-rule.sqlite3', registry)
    auth = LocalAuth(database, origins=('http://127.0.0.1:8000',), secure_cookies=False)
    roles = {'admin': 'admin', 'editor': 'editor', 'viewer': 'viewer', 'sales': 'editor'}
    app = Application(database, auth=auth, policy=Policy(registry, rules={('sales', Customer): Rule(('read',))}, roles=roles))
    app.initialize()
    add_user(app, 'admin', 'admin')
    add_user(app, 'salesuser', 'sales')
    sales = client(app, 'salesuser')
    assert sales('POST', '/api/customer', {'customer_name': 'Refused'})[0] == 403
    assert sales('POST', '/api/project', {'title': 'From editor'})[0] == 201
    roles = client(app)('GET', '/api/_controls/roles')[1]['items']
    assert next(role for role in roles if role['id'] == 'sales')['base'] == 'editor'
    status, payload, _ = client(app)('DELETE', '/api/_controls/roles/sales')
    assert status == 409 and 'declares this role' in payload['error']


def test_deleting_a_role_still_assigned_is_refused_and_removes_its_overrides(secured):
    admin = client(secured)
    admin('POST', '/api/_controls/roles', {'id': 'sales', 'base': 'editor'})
    add_user(secured, 'salesuser', 'sales')
    assert save(admin, ('perm', 'sales|Customer', {'ops': ['read'], 'read': None, 'write': None, 'scope': None}),
                ('setting', 'security.two_step_roles', ['admin', 'sales']))[0] == 200
    assert admin('DELETE', '/api/_controls/roles/sales')[:2] == (409, {'error': '1 user has the sales role. Move them to another role first.'})
    assert admin('DELETE', '/api/_controls/roles/editor')[:2] == (409, {'error': 'Framework roles cannot be deleted.'})
    user_id = next(user['id'] for user in admin('GET', '/api/_controls/users')[1]['items'] if user['username'] == 'salesuser')
    assert admin('PATCH', f'/api/_controls/users/{user_id}', {'role': 'viewer'})[0] == 204
    assert admin('DELETE', '/api/_controls/roles/sales')[0] == 200
    with secured.database._connection() as db:
        assert db.execute('SELECT count(*) FROM evlet_overrides').fetchone()[0] == 0


def test_users_wrap_local_auth_with_the_last_admin_guard(secured):
    # An ordinary admin, the only one that counts: the built-in Administrator has no password here.
    add_user(secured, 'owner', 'admin')
    with secured.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_users SET password='' WHERE username='admin'")
    admin = client(secured, 'owner')
    users = admin('GET', '/api/_controls/users')[1]['items']
    me = next(user for user in users if user['username'] == 'owner')
    assert me['sessions'] == 1 and me['active'] is True
    assert admin('PATCH', f"/api/_controls/users/{me['id']}", {'role': 'viewer'})[:2] == (409, {'error': LAST_ADMIN})
    assert admin('PATCH', f"/api/_controls/users/{me['id']}", {'role': 'nobody'})[0] == 422
    assert admin('POST', '/api/_controls/users', {'username': 'fatima', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    fatima = client(secured, 'fatima')
    fatima_id = next(user['id'] for user in admin('GET', '/api/_controls/users')[1]['items'] if user['username'] == 'fatima')
    assert admin('PATCH', f'/api/_controls/users/{fatima_id}', {'sessions': 'end'})[0] == 204
    assert fatima('GET', '/api/schema')[0] == 401
    with secured.database._connection() as db:
        details = [row[0] for row in db.execute("SELECT detail FROM evlet_audit WHERE area='Users'")]
    assert details == ['Added as editor', 'Signed out everywhere']


def test_the_audit_log_is_append_only(secured):
    admin = client(secured)
    admin('POST', '/api/_controls/roles', {'id': 'sales', 'base': 'editor'})
    with secured.database._connection() as db:
        row_id = db.execute('SELECT id FROM evlet_audit').fetchone()[0]
        for statement in ('UPDATE evlet_audit SET detail=? WHERE id=?', 'DELETE FROM evlet_audit WHERE id=? OR detail=?',
                          "INSERT OR REPLACE INTO evlet_audit(id,at,actor,area,target,detail) VALUES (?,0,'x','x','x',?)"):
            values = ('changed', row_id) if statement.startswith('UPDATE') else (row_id, 'x')
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                db.execute(statement, values)
        assert tuple(db.execute('SELECT actor, detail FROM evlet_audit').fetchone()) == ('admin', 'Created from editor')


def stale(app, key, value):
    with app.database.transaction() as tx:
        tx.connection.execute('UPDATE evlet_overrides SET value=? WHERE key=?', (json.dumps(value), key))
        tx.connection.execute('UPDATE evlet_config SET version=version+1')


def test_an_override_that_no_longer_validates_fails_closed_and_is_reported(secured):
    admin, editor = client(secured), client(secured, 'editor')
    assert save(admin, ('perm', 'editor|Customer', {'ops': [], 'read': None, 'write': None, 'scope': None}),
                ('setting', 'security.lockout', 4), ('setting', 'security.password_min', 30))[0] == 200
    stale(secured, 'editor|Customer', {'ops': ['read'], 'read': ['gone'], 'write': None, 'scope': None})
    stale(secured, 'security.lockout', 99)
    stale(secured, 'security.password_min', 'long')
    assert editor('GET', '/api/customer')[0] == 403 and editor('POST', '/api/customer', {'customer_name': 'x'})[0] == 403
    assert editor('GET', '/api/project')[0] == 200
    assert (secured.business.auth.lockout, secured.business.auth.password_min) == (5, 64)
    attention = admin('GET', '/api/_controls/state')[1]['attention']
    assert {item['title'] for item in attention} >= {'No access for editor on Customer until this is fixed', 'security.lockout uses its strictest value until this is fixed'}


def test_a_policy_that_cannot_be_built_denies_everyone_but_admin(secured, monkeypatch):
    from evlet import controls
    admin, editor = client(secured), client(secured, 'editor')
    real = controls.Policy

    def policy(*args, **kwargs):
        if kwargs.get('overrides') and not all(rule == Rule(()) for rule in kwargs['overrides'].values()):
            raise ValueError('Broken rule')
        return real(*args, **kwargs)
    monkeypatch.setattr(controls, 'Policy', policy)
    assert save(admin, ('perm', 'viewer|Customer', {'ops': ['read'], 'read': ['customer_name'], 'write': None, 'scope': None}))[0] == 200
    assert editor('GET', '/api/project')[0] == 403
    assert admin('GET', '/api/project')[0] == 200
    assert any(item['title'] == 'Every role but admin is denied' for item in admin('GET', '/api/_controls/state')[1]['attention'])


def ops_user(app):
    admin = client(app)
    assert admin('POST', '/api/_controls/roles', {'id': 'ops', 'base': 'viewer'})[0] == 201
    assert admin('PATCH', '/api/_controls/roles/ops', {'opens_controls': True})[0] == 200
    add_user(app, 'opsuser', 'ops')
    return admin, client(app, 'opsuser')


def user_id(c, name):
    return next(user['id'] for user in c('GET', '/api/_controls/users')[1]['items'] if user['username'] == name)


def test_only_admins_manage_admins_roles_and_who_opens_controls(secured):
    admin, ops = ops_user(secured)
    refused = (403, {'error': ADMIN_ONLY})
    me, root, editor = user_id(admin, 'opsuser'), user_id(admin, 'admin'), user_id(admin, 'editor')
    assert ops('PATCH', f'/api/_controls/users/{me}', {'role': 'admin'})[:2] == refused
    assert ops('PATCH', f'/api/_controls/users/{root}', {'password': 'z' * 30})[:2] == refused
    assert ops('PATCH', f'/api/_controls/users/{root}', {'sessions': 'end'})[:2] == refused
    assert ops('POST', '/api/_controls/users', {'username': 'backdoor', 'password': PASSWORD, 'role': 'admin'})[:2] == refused
    assert ops('PATCH', '/api/_controls/roles/editor', {'opens_controls': True})[:2] == refused
    assert ops('PATCH', '/api/_controls/roles/ops', {'opens_controls': False})[:2] == refused
    assert ops('PATCH', '/api/_controls/roles/editor', {'description': 'Mine'})[:2] == refused
    assert ops('POST', '/api/_controls/roles', {'id': 'more', 'base': 'editor'})[:2] == refused
    assert ops('DELETE', '/api/_controls/roles/ops')[:2] == refused
    assert ops('POST', '/api/auth/users', {'username': 'backdoor', 'password': PASSWORD, 'role': 'admin'})[:2] == refused
    with secured.database._connection() as db:
        assert db.execute("SELECT role FROM evlet_users WHERE username='opsuser'").fetchone()[0] == 'ops'
        assert not db.execute("SELECT 1 FROM evlet_users WHERE username='backdoor'").fetchone()
    assert ops('PATCH', f'/api/_controls/users/{editor}', {'role': 'viewer'})[0] == 204
    assert ops('POST', '/api/_controls/users', {'username': 'helper', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    assert ops('PATCH', '/api/_controls/roles/ops', {'description': 'Runs the instance'})[0] == 200
    assert save(ops, ('setting', 'security.lockout', 4))[0] == 200


def test_the_older_account_routes_use_controls_access_and_the_audit_log(secured):
    admin = client(secured)
    assert admin('POST', '/api/auth/users', {'username': 'fatima', 'password': PASSWORD, 'role': 'editor'})[0] == 201
    assert admin('PATCH', f"/api/auth/users/{user_id(admin, 'fatima')}", {'role': 'viewer'})[0] == 204
    with secured.database._connection() as db:
        assert [row[0] for row in db.execute("SELECT detail FROM evlet_audit WHERE target='fatima'")] == ['Added as editor', 'Role: editor → viewer']
    assert save(admin, ('setting', 'security.controls_ips', ['10.0.0.0/8']))[0] == 200
    assert admin('GET', '/api/auth/users')[0] == 403
    assert admin('GET', '/api/auth/users', client='10.0.0.9')[0] == 200


def test_a_refused_user_change_writes_nothing(secured):
    admin = client(secured)
    root = user_id(admin, 'admin')
    with secured.database._connection() as db:
        before = db.execute('SELECT count(*) FROM evlet_audit').fetchone()[0]
    assert admin('PATCH', f'/api/_controls/users/{root}', {'active': False, 'password': 'z' * 30})[0] == 409
    with secured.database._connection() as db:
        assert db.execute('SELECT count(*) FROM evlet_audit').fetchone()[0] == before


def test_break_glass_clears_the_controls_allowlist(secured):
    admin = client(secured)
    assert save(admin, ('setting', 'security.controls_ips', ['10.0.0.0/8']))[0] == 200
    assert admin('GET', '/api/_controls/state', headers={b'x-forwarded-for': b'10.1.1.1'})[0] == 404
    grant(secured.database.path, 'admin')
    assert client(secured)('GET', '/api/_controls/state')[0] == 200
    with secured.database._connection() as db:
        assert [tuple(row) for row in db.execute("SELECT actor, target FROM evlet_audit WHERE area='Security' ORDER BY id")][-1] == ('cli', 'security.controls_ips')


def test_a_shorter_session_length_ends_sessions_older_than_it(secured):
    admin, editor = client(secured), client(secured, 'editor')
    with secured.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_sessions SET expires=expires-7200 WHERE user_id=(SELECT id FROM evlet_users WHERE username='editor')")
    assert save(admin, ('setting', 'security.session_hours', 4))[0] == 200
    assert admin('GET', '/api/schema')[0] == 200 and editor('GET', '/api/schema')[0] == 200
    assert save(admin, ('setting', 'security.session_hours', 1))[0] == 200
    assert editor('GET', '/api/schema')[0] == 401 and admin('GET', '/api/schema')[0] == 200


@pytest.mark.parametrize('change', [
    {'kind': [], 'key': 'x', 'value': None}, {'kind': {}, 'key': 'x', 'value': None}, {'kind': 'perm', 'key': 5, 'value': None},
    {'kind': 'perm', 'key': 'editor|Customer', 'value': {'ops': ['read'], 'read': None, 'write': None, 'scope': []}},
    {'kind': 'perm', 'key': 'editor|Customer', 'value': {'ops': ['read'], 'read': None, 'write': None, 'scope': {}}},
    {'kind': 'perm', 'key': 'editor|Customer', 'value': {'ops': [['read']], 'read': None, 'write': None, 'scope': None}},
    {'kind': 'perm', 'key': 'editor|Customer', 'value': {'ops': ['read'], 'read': [['customer_name']], 'write': None, 'scope': None}},
    {'kind': 'setting', 'key': 'security.controls_ips', 'value': [['1.2.3.4']]},
])
def test_malformed_items_are_refused_not_crashing(secured, change):
    status, payload, _ = client(secured)('POST', '/api/_controls/overrides', {'changes': [change]})
    assert status == 422 and 'changes.0' in payload['fields']


def test_unknown_user_fields_and_reserved_roles_are_refused(secured):
    admin = client(secured)
    for body in ({'username': 'abc', 'password': PASSWORD, 'self': 1}, {'username': 'abc', 'password': PASSWORD, 'check': 'x'},
                 {'username': 'abc', 'password': PASSWORD, 'role': ['admin']}):
        assert admin('POST', '/api/_controls/users', body)[0] == 422
    for role in ('portal', 'public', 'controls'):
        assert admin('POST', '/api/_controls/roles', {'id': role, 'base': 'editor'})[0] == 422
    with pytest.raises(ValueError):
        AppDefinition(roles={'portal': 'viewer'})


def test_a_role_on_an_unknown_base_gets_nothing_by_default():
    from evlet.auth import User
    from evlet.permissions import default_rule
    assert Policy(registry).rule(User(9, 'x', 'guest'), Customer).operations == ()
    assert default_rule('portal').operations == () and default_rule('editor').operations == ('read', 'create', 'update', 'delete')


def test_security_defaults_are_twelve_hour_sessions_and_five_failed_sign_ins(secured):
    from evlet.controls import SETTINGS
    auth = secured.business.auth
    assert (SETTINGS['security.session_hours'], SETTINGS['security.lockout']) == (12, 5)
    assert (auth.session_hours, auth.lockout) == (12, 5)
    status, _, headers = http(secured, 'POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD})
    assert status == 200 and b'Max-Age=43200' in headers[b'set-cookie']
    admin = client(secured)
    for key, value in (('security.session_hours', 13), ('security.lockout', 6), ('security.lockout', 2)):
        assert save(admin, ('setting', key, value))[0] == 422, (key, value)
    assert save(admin, ('setting', 'security.lockout', 3), ('setting', 'security.session_hours', 8))[0] == 200
    assert (auth.session_hours, auth.lockout) == (8, 3)
