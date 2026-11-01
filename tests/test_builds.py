from pathlib import Path
import json
import shutil
import sys

import pytest

from evlet import User
from evlet.builds import prepare
from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install
from evlet.instances import load_runtime
from evlet.packages import read_package, resolve_packages
from test_business import http, client, PASSWORD


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    (root / 'apps').mkdir(parents=True)
    (root / 'instances').mkdir()
    yield root
    for name in list(sys.modules):
        if name.split('.')[0] in ('notes', 'amendment'):
            del sys.modules[name]


def test_flat_scaffold_discovery_and_atomic_build(project, monkeypatch, tmp_path):
    create_app(project, 'notes')
    package = read_package(project / 'apps/notes')
    assert package.layout == 'flat'
    assert not (package.path / 'hooks.py').exists()
    monkeypatch.chdir(tmp_path)
    packages = resolve_packages(project, ['notes'])
    pinned, first = prepare(project, packages)
    assert first['models'][0]['resource'] == 'notes.NotesItem'
    assert prepare(project, packages)[1]['fingerprint'] == first['fingerprint']
    pointer = (project / '.evlet/current.json').read_bytes()
    model = package.path / 'models/item.py'
    model.write_text(model.read_text() + '\nraise ValueError("broken build")\n')
    with pytest.raises(ValueError, match='broken build'): prepare(project, packages)
    assert (project / '.evlet/current.json').read_bytes() == pointer
    assert not list((project / 'instances').rglob('*.sqlite3'))
    assert not list((project / '.evlet/builds').glob('.build-*'))


def test_hashes_ignore_generated_files_and_reject_links(project, tmp_path):
    package = read_package(create_app(project, 'notes'))
    before = package.locked()
    (package.path / '__pycache__').mkdir()
    (package.path / '__pycache__/ignored.pyc').write_bytes(b'cache')
    (package.path / 'build').mkdir()
    (package.path / 'build/ignored.py').write_text('raise RuntimeError()')
    assert package.locked() == before
    (package.path / 'models/escape.py').symlink_to(tmp_path / 'elsewhere.py')
    with pytest.raises(ValueError, match='symbolic'): package.locked()


def test_reference_app_full_public_flow(project):
    source = Path(__file__).resolve().parents[1] / 'examples/amendment'
    shutil.copytree(source, project / 'apps/amendment', ignore=shutil.ignore_patterns('__pycache__', '*.egg-info', 'build', 'dist'))
    create_instance(project, 'acme')
    result = install(project, 'acme', app_name='amendment', trust=True)
    assert result['apps'] == ['amendment']
    app = load_runtime(project, 'acme')
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    c = client(app)
    company = c('POST', '/api/company', {'name': 'Example'})[1]['id']
    warehouse = c('POST', '/api/warehouse', {'name': 'Stores', 'company': company})[1]['id']
    item = c('POST', '/api/itemmaster', {'name': 'Sample', 'description': 'Reference item'})[1]['id']
    bom = c('POST', '/api/bom', {'name': 'Standard', 'item': item, 'company': company})[1]['id']
    assert c('POST', '/api/stockbalance', {'item': item, 'warehouse': warehouse, 'actual_qty': '12'})[0] == 201
    values = {'name': 'SO-1', 'company': company, 'warehouse': warehouse, 'children': {'items': [{'_key': 'r1', 'item': item, 'qty': '3', 'rate': '2.25'}]}}
    preview = c('POST', '/api/_draft/salesorder', {'values': values, 'revision': 1})
    assert preview[0] == 200, preview
    assert preview[1]['values']['children']['items'][0]['available_qty'] == '12.0000'
    status, order, _ = c('POST', '/api/salesorder', values)
    assert status == 201, order
    assert order['total'] == '6.75' and order['children']['items'][0]['description'] == 'Reference item'
    source = c('GET', '/api/salesorder/' + str(order['id']))
    headers = {b'if-match': source[2][b'etag']}
    base = '/api/_dialogs/salesorder/' + str(order['id']) + '/create_work_orders'
    dialog = c('POST', base + '/load', {}, headers=headers)
    assert dialog[0] == 200, dialog
    row = dialog[1]['values']['children']['items'][0]
    submission = {'key': 'plan-once', 'values': {'children': {'items': [{'_key': row['_key'], 'selected': True, 'qty': '2', 'bom': bom}]}}}
    planned = c('POST', base + '/submit', submission, headers=headers)
    assert planned[0] == 200, planned
    assert c('POST', base + '/submit', submission, headers=headers)[1] == planned[1]
    assert c('GET', '/api/workorder')[1]['total'] == 1
    assert c('GET', '/api/salesorder/' + str(order['id']))[2][b'etag'] == headers[b'if-match']
    report = c('POST', '/api/_reports/amendment.SalesRegister', {'filters': {'company': company}})
    assert report[0] == 200, report
    assert report[1]['rows'][0]['total'] == '6.75'
    assert c('GET', f'/api/_services/amendment.services.integrations.warehouse.checks.balance_count?warehouse={warehouse}')[1] == {'count': 1}
    c('PATCH', f'/api/company/{company}', {'payment_days': 45}, headers={b'if-match': c('GET', f'/api/company/{company}')[2][b'etag']})
    quote = {'name': 'Q-1', 'inclusive': True, 'company': company, 'issued_on': '2026-11-01', 'discount_rate': '3.33',
             'children': {'lines': [{'_key': 'a', 'qty': '1', 'rate': '105'},
                                                                      {'_key': 'b', 'qty': '2', 'rate': '10', 'taxable': False}]}}
    status, saved, _ = c('POST', '/api/quote', quote)
    assert status == 201, saved
    assert (saved['net_total'], saved['vat_total'], saved['taxable_net'], saved['grand_total']) == ('120.00', '5.00', '100.00', '125.00')
    # 120.00 * 3.33% = 3.996 is rounded half up to the Currency scale; the due date follows the company Link.
    assert (saved['discount'], saved['payable'], saved['valid_until']) == ('4.00', '121.00', '2026-12-16')
    assert c('POST', '/api/quote', {'name': 'Q-2'})[1]['valid_until'] is None
    url = f"/api/quote/{saved['id']}"
    edit = lambda body: c('PATCH', url, body, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert saved['payment_days'] == 45 and edit({'payment_days': 10})[1]['valid_until'] == '2026-11-11'
    assert edit({'stage': 'lost'})[1]['fields'] == {'lost_reason': 'This field is required'}
    assert edit({'stage': 'won', 'lost_reason': 'Ignored', 'labels': ['gold']})[1]['lost_reason'] is None
    assert edit({'discount_rate': '5'})[1]['fields'] == {'discount_rate': 'This field is locked and cannot be changed now'}
    # Row changes: add one line at the top without resending the others.
    kept = [row['id'] for row in c('GET', url)[1]['children']['lines']]
    added = edit({'children': {'lines': {'create': [{'_key': 'c', 'qty': '1', 'rate': '10', 'taxable': False}], 'order': ['c', *kept]}}})[1]
    assert added['grand_total'] == '135.00' and [row['id'] for row in added['children']['lines']] == [added['_row_ids']['c'], *kept]
    calculation = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'quote')['calculation']
    assert calculation['plan']['root_resource'] == 'amendment.Quote' and saved['_calculation']['model_schema_hash'] == calculation['model_schema_hash']
    manifest = app.business.build_manifest
    assert len(manifest['apis']) == len(manifest['schedules']) == len(manifest['native']['panels']) == 1
    schema = c('GET', '/api/schema')[1]
    native_url = schema['native_panels'][0]['url']
    assert c('GET', native_url)[0] == 200
    with app.database.transaction() as tx:
        assert tx.connection.execute('SELECT count(*) FROM evlet_schedules').fetchone()[0] == 1
        assert tx.connection.execute('SELECT count(*) FROM evlet_jobs').fetchone()[0] == 0
    install(project, 'acme', trust=True)

    # Both requests start with the same source revision and one remaining unit.
    from concurrent.futures import ThreadPoolExecutor
    remaining_dialog = c('POST', base + '/load', {}, headers=headers)[1]
    remaining_row = remaining_dialog['values']['children']['items'][0]
    def plan_one(key):
        return c('POST', base + '/submit', {'key': key, 'values': {'children': {'items': [{'_key': remaining_row['_key'], 'selected': True, 'qty': '1', 'bom': bom}]}}}, headers=headers)[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(plan_one, ['concurrent-a', 'concurrent-b']))
    assert sorted(statuses) == [200, 422]
    assert c('GET', '/api/workorder')[1]['total'] == 2


def test_active_generation_pins_late_imports_and_rejects_replacement(project):
    from evlet.instances import activate_generation, load_definitions
    from importlib import import_module
    create_app(project, 'notes')
    (project / 'apps/notes/support.py').write_text('value = "original"\n')
    create_instance(project, 'acme')
    install(project, 'acme', app_name='notes', trust=True)
    app = load_runtime(project, 'acme')
    with activate_generation(app):
        (project / 'apps/notes/support.py').write_text('value = "changed"\n')
        pinned, manifest = prepare(project, resolve_packages(project, ['notes']))
        with pytest.raises(ValueError, match='generation is active'): load_definitions(pinned, modules=manifest['modules'])
        assert import_module('notes.support').value == 'original'
    definitions = load_definitions(pinned, modules=manifest['modules'])
    assert import_module('notes.support').value == 'changed'
    assert load_definitions(pinned, modules=manifest['modules'])[0][1].models[0] is definitions[0][1].models[0]


def test_flat_sources_skip_dotfiles_and_virtual_environments(project):
    create_app(project, 'notes')
    root = project / 'apps/notes'
    (root / '.env').write_text('SECRET=1')
    venv = root / 'venv'
    (venv / 'lib').mkdir(parents=True)
    (venv / 'pyvenv.cfg').write_text('home = /usr/bin')
    (venv / 'lib64').symlink_to(venv / 'lib')
    (root / 'tests').mkdir()
    (root / 'tests/test_item.py').write_text('')
    (root / 'services/build').mkdir(parents=True)
    (root / 'services/build/steps.py').write_text('')
    files = {str(path.relative_to(root)) for path in read_package(root).source_files()}
    assert '.env' not in files and not any(name.startswith(('venv', 'tests')) for name in files)
    assert 'services/build/steps.py' in files
    assert 'models/item.py' in files


def test_legacy_lock_digest_keeps_original_file_selection(tmp_path):
    import hashlib
    from evlet.packages import Package
    root = tmp_path / 'legy'
    (root / 'legy/build').mkdir(parents=True)
    (root / 'pyproject.toml').write_text('[project]\nname = "legy"\n')
    (root / 'legy/__init__.py').write_text('')
    (root / 'legy/build/__init__.py').write_text('')
    (root / 'legy/.DS_Store').write_bytes(b'x')
    digest = hashlib.sha256()
    for path in [root / 'pyproject.toml', *sorted((root / 'legy').rglob('*'))]:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + b'\0')
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    package = Package(root, 'legy', '1.0', 'Legy', ())
    assert package.locked()['sha256'] == digest.hexdigest()


def test_concurrent_build_reuses_published_generation(project, monkeypatch, tmp_path):
    import os
    from evlet import builds
    create_app(project, 'notes')
    monkeypatch.chdir(tmp_path)
    packages = resolve_packages(project, ['notes'])
    _, first = prepare(project, packages)
    published = project / '.evlet/builds' / first['fingerprint']
    hidden = published.with_name('hidden')
    published.rename(hidden)
    real_rename = os.rename
    def racing_rename(source, target):
        # The other process publishes just before this one renames its stage.
        real_rename(hidden, target)
        return real_rename(source, target)
    monkeypatch.setattr(builds.os, 'rename', racing_rename)
    assert prepare(project, packages)[1]['fingerprint'] == first['fingerprint']


def test_native_modules_with_clashing_outputs_are_rejected(tmp_path):
    from evlet.native import build_assets
    from evlet.packages import Package
    views = tmp_path / 'clash/views'
    views.mkdir(parents=True)
    (tmp_path / 'clash/pyproject.toml').write_text('[project]\nname = "clash"\n')
    (views / 'panel.ts').write_text('export default {}')
    (views / 'Panel.js').write_text('export default {}')
    package = Package(tmp_path / 'clash', 'clash', '1.0', 'Clash', (), layout='flat')
    with pytest.raises(ValueError, match='overwrite each other'):
        build_assets((package,), tmp_path / 'stage', set())


def test_delegated_commands_keep_the_working_directory_off_sys_path(tmp_path, monkeypatch):
    import json, os
    from evlet.cli import setup
    root = tmp_path / 'project'
    (root / '.venv').mkdir(parents=True)
    (root / '.venv/evlet-project.json').write_text(json.dumps({'version': 1}))
    executable = setup.python_in(root)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text('')
    calls = []
    monkeypatch.setattr(os, 'execve', lambda path, argv, env: calls.append(argv))
    monkeypatch.delenv('EVLET_DELEGATED_PROJECT', raising=False)
    setup.delegate(root, ['serve'])
    assert calls[0][1:] == ['-P', '-m', 'evlet', 'serve']


def test_build_subprocess_ignores_modules_in_the_working_directory(project, monkeypatch, tmp_path):
    create_app(project, 'notes')
    cwd, marker = tmp_path / 'elsewhere', tmp_path / 'shadowed'
    cwd.mkdir()
    (cwd / 'decimal.py').write_text(f'open({str(marker)!r}, "w").close()\nraise SystemExit("shadow module imported")\n')
    monkeypatch.chdir(cwd)
    prepare(project, resolve_packages(project, ['notes']))
    assert not marker.exists()
