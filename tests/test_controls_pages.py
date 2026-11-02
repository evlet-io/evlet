"""Controls Apps, Routing, Jobs, Overrides with export as code, Audit log and the Overview attention list."""

import types

import pytest

import evlet
import evlet.ui
from evlet.cli.install import install
from evlet.ui import ViewRegistry
from test_business import client, hashed, secured  # noqa: F401
from test_cli_lifecycle import change_model, environment_stub, project, runtime  # noqa: F401
from test_controls import DESK, add_user, hosts, on_host, password, save  # noqa: F401
from test_customization import build, models

UNBUILT = {'error': 'UI assets are not built. Run the frontend production build.'}
PAGES = ('apps', 'routing', 'jobs', 'overrides', 'audit')
ENDPOINTS = (('GET', '/api/_controls/overrides/export'), ('GET', '/api/_controls/audit'),
             ('GET', '/api/_controls/routing'), ('POST', '/api/_controls/routing/check'), ('GET', '/api/_controls/jobs'),
             ('POST', '/api/_controls/jobs/schedules/nightly'), ('POST', '/api/_controls/jobs/schedules/nightly/run'),
             ('POST', '/api/_controls/jobs/1/retry'))


def test_the_new_pages_and_endpoints_are_404_without_controls(secured):
    admin = client(secured)
    for page in PAGES:
        status, payload, _ = admin('GET', f'/controls/{page}')
        assert status == 200 or payload == UNBUILT, page
    for role in ('editor', 'viewer'):
        c = client(secured, role)
        for page in PAGES:
            assert c('GET', f'/controls/{page}')[0] == 404
        for method, path in ENDPOINTS:
            assert c(method, path, {})[:2] == (404, {'error': 'Route not found'}), (role, method, path)


# ---------- audit ----------

def test_the_audit_log_pages_newest_first_with_search_and_area(secured):
    admin = client(secured)
    for index in range(55):
        assert admin('POST', '/api/_controls/roles', {'id': f'team_{index}', 'base': 'viewer'})[0] == 201
    save(admin, ('setting', 'security.lockout', 4))
    first = admin('GET', '/api/_controls/audit')[1]
    assert len(first['items']) == 50 and first['items'][0]['area'] == 'Security' and first['areas'] == ['Roles', 'Security']
    ids = [item['id'] for item in first['items']]
    assert ids == sorted(ids, reverse=True)
    rest = admin('GET', f"/api/_controls/audit?before={first['next']}")[1]
    assert len(rest['items']) == 6 and rest['next'] is None and rest['items'][0]['id'] < ids[-1]
    found = admin('GET', '/api/_controls/audit?q=TEAM_5&area=Roles')[1]['items']
    assert sorted(item['target'] for item in found) == ['team_5', 'team_50', 'team_51', 'team_52', 'team_53', 'team_54']
    assert admin('GET', '/api/_controls/audit?q=100%25')[1]['items'] == []
    assert admin('GET', '/api/_controls/audit?area=Security')[1]['items'][0]['target'] == 'security.lockout'
    assert admin('GET', '/api/_controls/audit?before=x')[0] == 422
    assert admin('GET', f'/api/_controls/audit?before={2**63}')[0] == 422
    assert admin('GET', '/api/_controls/audit?area=' + 'a' * 41)[1]['fields'] == {'area': 'Choose an area from the list.'}
    assert admin('GET', '/api/_controls/audit?page=2')[0] == 422


# ---------- jobs ----------

def test_jobs_pause_run_now_and_retry_failed_jobs_only(secured):
    admin, jobs = client(secured), secured.business.jobs
    jobs.schedule('nightly', 'project_progress', {'project_id': 404}, every=3600)
    listed = admin('GET', '/api/_controls/jobs')[1]
    assert listed['available'] and listed['schedules'][0] | {'next_run': 0} == {
        'name': 'nightly', 'task': 'project_progress', 'every': 3600, 'expression': None, 'timezone': None, 'next_run': 0,
        'active': True, 'paused': False, 'app': None}

    paused = admin('POST', '/api/_controls/jobs/schedules/nightly', {'active': False})[1]
    assert paused['schedules'][0]['active'] is False and paused['schedules'][0]['paused'] is True
    item = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['key'] == 'schedule.nightly.active')
    assert (item['kind'], item['file'], item['value'], item['changed_by']) == ('setting', True, False, 'admin')
    assert jobs.tick(now=2**40) == 0
    assert admin('POST', '/api/_controls/jobs/schedules/nightly/run', {})[:2] == (409, {'error': 'nightly is paused. Resume it first.'})
    resumed = admin('POST', '/api/_controls/jobs/schedules/nightly', {'active': True})[1]['schedules'][0]
    assert resumed['active'] and not resumed['paused']
    assert admin('POST', '/api/_controls/jobs/schedules/missing', {'active': False})[0] == 404
    assert admin('POST', '/api/_controls/jobs/schedules/nightly', {'active': 'no'})[0] == 422

    ran = admin('POST', '/api/_controls/jobs/schedules/nightly/run', {})[1]
    job = ran['jobs'][0]
    assert job['id'] == ran['job'] and job['state'] == 'queued' and job['task'] == 'project_progress'
    assert admin('POST', f"/api/_controls/jobs/{job['id']}/retry", {})[:2] == (409, {'error': 'Only failed jobs can be retried.'})
    with secured.database.transaction() as tx:
        tx.connection.execute("UPDATE evlet_jobs SET dedupe='kept-key' WHERE id=?", (job['id'],))
    assert jobs.run_one()
    failed = admin('GET', '/api/_controls/jobs')[1]['jobs'][0]
    assert failed['state'] == 'failed' and failed['error'] == 'NotFoundError'
    state = admin('GET', '/api/_controls/state')[1]
    assert state['jobs'] == {'failed': 1} and any(item['title'] == '1 job failed' for item in state['attention'])
    assert admin('POST', f"/api/_controls/jobs/{job['id']}/retry", {})[1]['jobs'][0]['state'] == 'queued'
    with secured.database._connection() as db:
        assert tuple(db.execute('SELECT dedupe, attempts FROM evlet_jobs WHERE id=?', (job['id'],)).fetchone()) == ('kept-key', 0)
        audit = [tuple(row) for row in db.execute("SELECT target, detail FROM evlet_audit WHERE area='Jobs' ORDER BY id")]
    assert audit == [('nightly', 'Paused'), ('nightly', 'Resumed'), ('nightly', f"Run now · job #{job['id']}"), (f"Job #{job['id']}", 'Retried')]


def test_failed_jobs_are_counted_beyond_the_recent_list(secured):
    with secured.database.transaction() as tx:
        for state in ['failed'] + ['succeeded'] * 120:
            tx.connection.execute("INSERT INTO evlet_jobs(task,payload,state,max_attempts,run_at,created_at) VALUES ('project_progress','{}',?,3,0,0)", (state,))
    assert client(secured)('GET', '/api/_controls/state')[1]['jobs'] == {'failed': 1}


def test_a_pause_of_a_schedule_that_is_gone_is_listed_and_can_be_reset(secured):
    admin = client(secured)
    secured.business.jobs.schedule('weekly', 'project_progress', {'project_id': 1}, every=7 * 86400)
    admin('POST', '/api/_controls/jobs/schedules/weekly', {'active': False})
    with secured.database.transaction() as tx:
        tx.connection.execute("DELETE FROM evlet_schedules WHERE name='weekly'")
        tx.connection.execute('UPDATE evlet_config SET version=version+1')
    state = admin('GET', '/api/_controls/state')[1]
    assert any(item['title'] == 'The pause of weekly no longer applies' for item in state['attention'])
    assert save(admin, ('setting', 'schedule.weekly.active', None))[0] == 200
    assert not any(item['key'] == 'schedule.weekly.active' and item['value'] is not None for item in admin('GET', '/api/_controls/overrides')[1]['items'])


# ---------- routing ----------

def test_routing_lists_the_route_table_and_needs_a_primary_host_for_the_proxy(secured):
    admin = client(secured)
    routing = admin('GET', '/api/_controls/routing')[1]
    controls = next(row for row in routing['rows'] if row['path'] == '/controls')
    assert controls['locked'] and controls['serves'] == 'evlet.controls'
    assert routing['planned'] == ['/report', '/dashboard', '/search', '/print']
    message = 'Set a primary host in routes.py or Instance.domain before writing a proxy configuration'
    assert routing['proxy'] is None and routing['proxy_error'] == message
    assert admin('POST', '/api/_controls/routing/check', {})[:2] == (409, {'error': message})


def test_routing_uses_evlet_proxy_when_it_is_installed(secured, monkeypatch):
    import evlet.cli
    import evlet.controls_system as system
    fake = types.SimpleNamespace(caddy=lambda routes, port, name, trusted: f'caddy {port} {name} {trusted}', nginx=lambda routes, port, name, trusted: 'nginx',
                                 check=lambda routes: ('This server has a private address. Pass --ip with its public address.\n'
                                                       '127.0.0.1   127.0.0.1                       points here\n'
                                                       'localhost   -                               no A or AAAA record\n'
                                                       'portal.localhost   -                        no A or AAAA record', False))
    monkeypatch.setitem(__import__('sys').modules, 'evlet.cli.proxy', fake)
    monkeypatch.setattr(evlet.cli, 'proxy', fake, raising=False)
    monkeypatch.setattr(system, 'certificate', lambda host: {'certificate': 'valid', 'expires': 1800000000})
    admin = client(secured)
    assert admin('GET', '/api/_controls/routing')[1]['proxy'] == {'caddy': 'caddy 8000 <name> ()', 'nginx': 'nginx'}
    checked = admin('POST', '/api/_controls/routing/check', {})[1]
    assert admin('POST', '/api/_controls/routing/check', {})[:2] == (429, {'error': 'Hosts were just checked. Try again in a few seconds.'})
    assert checked == {'ok': False, 'notes': ['This server has a private address. Pass --ip with its public address.'], 'hosts': [
        {'host': '127.0.0.1', 'records': '127.0.0.1', 'dns': 'points here', 'certificate': 'valid', 'expires': 1800000000},
        {'host': 'localhost', 'records': '', 'dns': 'no A or AAAA record', 'certificate': None, 'expires': None},
        {'host': 'portal.localhost', 'records': '', 'dns': 'no A or AAAA record', 'certificate': None, 'expires': None}]}


# ---------- with sub-hosts and realms ----------

def test_routing_finds_evlet_proxy_and_lists_sub_hosts_with_their_sign_in(hosts):
    import evlet.cli.proxy
    from evlet.controls_system import System
    assert System.proxy() is evlet.cli.proxy
    admin = on_host(hosts, 'admin', DESK)
    routing = admin('GET', '/api/_controls/routing')[1]
    assert routing['hosts'] == [{'host': '127.0.0.1', 'primary': False, 'sub': False, 'realm': 'desk'},
                                {'host': 'localhost', 'primary': False, 'sub': False, 'realm': 'desk'},
                                {'host': 'portal.localhost', 'primary': False, 'sub': True, 'realm': 'portal'}]
    assert {row['realm'] for row in routing['rows'] if row['host'] == 'portal.localhost'} == {'portal'}
    # Without a primary host the proxy refuses, and the page says why.
    assert routing['proxy'] is None and routing['proxy_error'].startswith('Set a primary host')


def test_routing_writes_the_proxy_config_and_checks_hosts_with_evlet_proxy(hosts, monkeypatch):
    import evlet.cli.proxy as proxy
    import evlet.controls_system as system
    from evlet import AppDefinition, Frontend, Host
    from evlet.routing import resolve_routes
    portal = Frontend('portal', kind='static', dist='dist', path='/portal', auth='portal')
    routes = resolve_routes([('shop', AppDefinition(frontends=(portal,)))], None,
                            {'primary': Host('erp.acme.com'), 'hosts': (Host('portal.acme.com', root=portal),)})
    controls = hosts.business.controls
    monkeypatch.setattr(controls, 'routes', routes)
    routing = controls.system.routing()
    assert routing['hosts'] == [{'host': 'erp.acme.com', 'primary': True, 'sub': False, 'realm': 'desk'},
                                {'host': 'portal.acme.com', 'primary': False, 'sub': True, 'realm': 'portal'}]
    assert routing['proxy_error'] is None and routing['proxy'] == {'caddy': proxy.caddy(routes, routes.port, '<name>'),
                                                                   'nginx': proxy.nginx(routes, routes.port, '<name>')}
    # Controls and Docs stay on the primary host: the sub-host block refuses them.
    assert routing['proxy']['caddy'].count('respond @primary_only 404') == 1
    records = {'erp.acme.com': '9.9.9.9', 'portal.acme.com': '1.1.1.1'}
    monkeypatch.setattr(proxy, 'server_addresses', lambda: {'9.9.9.9'})
    monkeypatch.setattr(system, 'bounded', lambda resolve: lambda host, port, **options: [(2, 1, 6, '', (records[host], 0))])
    monkeypatch.setattr(system, 'certificate', lambda host: {'certificate': 'valid', 'expires': 1800000000})
    checked = controls.system.check_hosts(types.SimpleNamespace(id=1))
    assert checked == {'ok': False, 'notes': [], 'hosts': [
        {'host': 'erp.acme.com', 'records': '9.9.9.9', 'dns': 'points here', 'certificate': 'valid', 'expires': 1800000000},
        {'host': 'portal.acme.com', 'records': '1.1.1.1', 'dns': 'points elsewhere', 'certificate': None, 'expires': None}]}


def test_export_writes_the_own_customer_preset_with_the_public_function(hosts):
    from evlet import own_customer
    from evlet.auth import User
    from examples.playground.models import Customer, Project
    admin = on_host(hosts, 'admin', DESK)
    value = {'ops': ['read'], 'read': None, 'write': None, 'scope': 'own_customer'}
    assert save(admin, ('perm', 'editor|Project', value), ('perm', 'editor|Customer', value))[0] == 200
    code = next(item['code'] for item in admin('GET', '/api/_controls/overrides/export')[1] if item['file'].endswith('permissions.py'))
    assert "('editor', Project): Rule(operations=('read',), read_fields=None, write_fields=None, scope=own_customer())," in code
    assert "('editor', Customer): Rule(operations=('read',), read_fields=None, write_fields=None, scope=own_customer(field='id'))," in code
    names = {**{key: getattr(evlet, key) for key in evlet.__all__}, 'Customer': Customer, 'Project': Project}
    loaded = {}
    exec(code, names, loaded)
    # The exported scopes are the ones Controls applies, over the user's links.
    user = User(5, 'rep', 'editor', links=(('customer', 7),))
    for model in (Project, Customer):
        assert loaded['rules'][('editor', model)].scope(user) == controls_scope(hosts, model)(user) != {}
    assert own_customer()(user) == {'customer__in': [7]}


def controls_scope(app, model):
    return app.business.controls.compile_scope('own_customer', model)


# ---------- apps ----------

def test_apps_show_a_waiting_migration_and_the_shell_command(project, hashed):
    install(project, 'acme', app_name='notes', trust=True)
    app = runtime(project)
    try:
        add_user(app, 'admin', 'admin')
        admin = client(app)
        notes = admin('GET', '/api/_controls/state')[1]['apps'][0]
        assert notes['id'] == 'notes' and notes['source'] == 'apps/notes' and notes['update'] is None and not notes['restart']

        change_model(project)
        state = admin('GET', '/api/_controls/state')[1]
        assert state['apps'][0]['update'] == {'from': '0.1.0', 'to': '0.1.0', 'error': None}
        assert state['instance']['slug'] == 'acme' and state['instance']['managed']
        waiting = next(item for item in state['attention'] if item['page'] == 'apps')
        assert 'waiting to be migrated' in waiting['title'] and 'evlet migrate acme' in waiting['text'] and 'Stop the server' in waiting['text']
        # The browser never migrates: there is no route for it.
        assert admin('POST', '/api/_controls/apps/migrate', {})[:2] == (404, {'error': 'Route not found'})

        install(project, 'acme', trust=True)
        notes = admin('GET', '/api/_controls/state')[1]['apps'][0]
        assert notes['update'] is None and notes['restart']
    finally:
        app.database.close()


# ---------- overrides and export ----------

def layouts(compiled):
    """(title, field names) of each FormLayout in a compiled view."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get('type') == 'FormLayout':
                found.append((node['props'].get('title'), [child['props']['field'] for child in node['slots']['children']]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(compiled)
    return found


def test_export_compiles_and_loading_it_gives_the_same_effective_values(tmp_path, hashed):
    app = build(tmp_path, *models())
    add_user(app, 'admin', 'admin')
    admin = client(app)
    sections = [{'title': 'Main', 'fields': [{'name': 'title', 'width': 6}, {'name': 'source', 'width': 6}]},
                {'title': 'More', 'fields': [{'name': 'value', 'width': 6}, {'name': 'customer', 'width': 6}, {'name': 'notes', 'width': 12}]}]
    series = [{'id': 'large', 'label': 'Large', 'format': 'LG-{YYYY}-{####}', 'when': 'total >= 1000'},
              {'id': 'default', 'label': 'Invoices', 'format': 'INV-{YYYY}-{####}', 'when': ''}]
    perm = {'ops': ['read', 'update'], 'read': None, 'write': ['title', 'value'], 'scope': None}
    assert admin('POST', '/api/_controls/fields/Lead', {'name': 'lpo', 'type': 'Text', 'label': 'LPO number'})[0] == 201
    status, payload, _ = save(admin, ('perm', 'editor|Lead', perm), ('perm', 'viewer|Customer', {'ops': [], 'read': None, 'write': None, 'scope': None}),
                              ('field', 'Lead.value', {'required': True, 'label': 'Expected value'}),
                              ('field', 'Lead.source', {'options_add': ['exhibition'], 'default': 'web'}),
                              ('field', 'Lead.x_lpo', {'help_text': 'From the purchase order'}),
                              ('layout', 'Lead', sections), ('list', 'Lead', {'columns': ['title', 'value'], 'page_size': 25, 'sort': 'title'}),
                              ('naming', 'Invoice', series), ('setting', 'security.controls_ips', ['127.0.0.0/8']), ('setting', 'security.lockout', 4))
    assert status == 200, payload
    exported = {item['file']: item['code'] for item in admin('GET', '/api/_controls/overrides/export')[1]}
    folder = 'test_customization'
    assert sorted(exported) == ['instances/<name>/config.py', 'instances/<name>/routes.py', f'{folder}.py',
                                f'{folder}/permissions.py', f'{folder}/views/__init__.py']
    for file, code in exported.items():
        compile(code, file, 'exec')

    Customer, Lead, Invoice, _ = (app.registry.models[name] for name in ('customer', 'lead', 'invoice', 'order'))
    names = {**{key: getattr(evlet, key) for key in evlet.__all__}, 'Customer': Customer, 'Lead': Lead, 'Invoice': Invoice}
    rules = {}
    exec(exported[f'{folder}/permissions.py'], names | {'rules': None}, rules)
    controls = app.business.controls
    assert controls.value(Lead, rules['rules'][('editor', Lead)]) == perm
    assert controls.value(Customer, rules['rules'][('viewer', Customer)])['ops'] == []

    fields = {}
    # Apps can't declare x_ names: the code says to rename the field while moving it.
    exec(exported[f'{folder}.py'].replace('x_lpo', 'lpo'), names, fields)
    lead, invoice = vars(fields['Lead']), fields['Invoice']
    assert (lead['value'].label, lead['value'].required, lead['value'].scale) == ('Expected value', True, 2)
    assert lead['source'].choices == ('web', 'walk_in', 'referral', 'exhibition') and lead['source'].default == 'web'
    assert (type(lead['lpo']).__name__, lead['lpo'].label, lead['lpo'].help_text) == ('Text', 'LPO number', 'From the purchase order')
    assert list(invoice.Meta.naming.series) == series and invoice.Meta.naming.date_field == 'posting_date'

    views = {}
    exec(exported[f'{folder}/views/__init__.py'], {key: getattr(evlet.ui, key) for key in dir(evlet.ui)} | {'Lead': Lead}, views)
    schema = admin('GET', '/api/schema')[1]
    compiled = {view['name']: view for view in ViewRegistry([views['lead_form'], views['lead_list']]).compile(schema)['views']}
    assert layouts(compiled['lead.form']) == [(section['title'], [item['name'] for item in section['fields']]) for section in sections]
    assert compiled['lead.list']['options'] == {'columns': ['title', 'value'], 'page_size': 25, 'order_by': 'title'}

    routes = {}
    exec(exported['instances/<name>/routes.py'], names, routes)
    assert routes['controls'].allow_ips == ('127.0.0.0/8',)
    assert 'security = Security(lockout=4)' in exported['instances/<name>/config.py']

    one = admin('GET', '/api/_controls/overrides/export?kind=list&key=Lead')[1]
    assert one == [{'file': f'{folder}/views/__init__.py',
                    'code': "lead_list = ListView(model=Lead, columns=['title', 'value'], page_size=25, order_by='title')\n"}]
    custom = next(item for item in admin('GET', '/api/_controls/overrides')[1]['items'] if item['kind'] == 'custom')
    assert custom['key'] == 'Lead' and custom['value'][0]['name'] == 'x_lpo' and custom['changed_by'] == 'admin'
    assert admin('GET', '/api/_controls/overrides/export?kind=perm')[0] == 200
    assert admin('GET', '/api/_controls/overrides/export?other=1')[0] == 422


def test_dns_lookups_in_the_host_check_time_out(monkeypatch):
    import threading
    import evlet.controls_system as system
    monkeypatch.setattr(system, 'LOOKUP_SECONDS', 0.05)
    release = threading.Event()
    slow = system.bounded(lambda host, port, **options: release.wait(5))
    with pytest.raises(OSError):
        slow('example.com', None)
    release.set()


def test_export_writes_callables_as_set_in_code_and_names_the_real_file(tmp_path, hashed):
    from evlet import Decimal, Model, Text
    from evlet.controls_export import Exporter, literal

    class Gadget(Model):
        name = Text(default=lambda: 'x', validator=lambda value: None)
        weight = Decimal(scale=2)

    app = build(tmp_path, Gadget)
    add_user(app, 'admin', 'admin')
    admin = client(app)
    assert save(admin, ('field', 'Gadget.name', {'label': 'Title'}))[0] == 200
    code = next(item['code'] for item in admin('GET', '/api/_controls/overrides/export')[1] if item['file'].endswith('test_controls_pages.py'))
    compile(code, 'models.py', 'exec')
    assert "name = Text(default=..., validator=..., label='Title')  # ... is set in code in models.py: copy it from there" in code
    # A calculated field's expression has no Python form here either.
    assert literal(object()) == '...' and literal((len, lambda: 1)) == '(..., ...)'
    exporter = Exporter(app.business.controls)
    for layout, module, init, expected in (('flat', 'notes.models.item', False, 'apps/notes/models/item.py'),
                                           ('legacy', 'notes.models', True, 'apps/notes/notes/models/__init__.py')):
        app.business.controls.apps = [{'id': 'notes', 'layout': layout}]
        app.business.controls.app_of['Gadget'] = 'notes'
        assert exporter.models_file(Gadget, module, init) == expected
    assert exporter.folder(Gadget) == 'apps/notes/test_controls_pages'


def test_every_setting_kind_exports_under_its_own_prefix_and_round_trips(secured):
    admin = client(secured)
    secured.business.jobs.schedule('nightly', 'project_progress', {'project_id': 404}, every=3600)
    values = {'security.password_min': 20, 'security.session_hours': 4, 'security.lockout': 4, 'security.two_step_roles': ['admin', 'editor'],
              'security.controls_ips': ['127.0.0.0/8'], 'mcp.enabled': False, 'mcp.allow_writes': True, 'mcp.rate': 30,
              'mcp.disabled_tools': ['delete_record'], 'mcp.hidden_models': ['Customer'], 'mail.personal': True, 'schedule.nightly.active': False}
    status, payload, _ = save(admin, *(('setting', key, value) for key, value in values.items()))
    assert status == 200, payload
    exported = {item['file']: item['code'] for item in admin('GET', '/api/_controls/overrides/export')[1]}
    config, routes = exported['instances/<name>/config.py'], exported['instances/<name>/routes.py']
    assert 'Security() is not read from config.py yet' in config and 'not read from config.py yet' in config.split('security =')[1]
    names, found = {'Security': lambda **options: options, 'ControlsAccess': lambda **options: options}, {}
    exec(config, names, found)
    exec(routes, names, found)
    back = {f'security.{key}': list(value) if isinstance(value, tuple) else value for key, value in found['security'].items()}
    back |= found['assistants'] | found['mail'] | {'security.controls_ips': list(found['controls']['allow_ips'])}
    paused = [item['code'] for item in admin('GET', '/api/_controls/overrides/export')[1] if item['file'] == 'hooks.py']
    assert paused == ['# nightly is paused on this instance. Remove its Schedule here to stop it everywhere.\n']
    assert back | {'schedule.nightly.active': False} == values
    # Nothing loses its prefix: a Security() argument is never an assistant or mail setting.
    assert set(found['security']) == {'password_min', 'session_hours', 'lockout', 'two_step_roles'}
    assert config.count("'mail.personal': True") == 1
