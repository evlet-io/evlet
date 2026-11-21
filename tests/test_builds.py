from pathlib import Path
import json
import re
import shutil
import sys

import pytest

from evlet import Rule, User
from evlet.builds import prepare
from evlet.cli.app import create_app
from evlet.cli.instance import create_instance
from evlet.cli.install import install
from evlet.instances import load_runtime
from evlet.packages import read_package, resolve_packages
from support import http, client, PASSWORD


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


def test_flat_app_with_a_single_models_module(project, monkeypatch, tmp_path):
    # A small app needs only models.py beside __init__.py; it is discovered like models/.
    package = read_package(create_app(project, 'notes'))
    shutil.rmtree(package.path / 'models')
    (package.path / 'models.py').write_text('from evlet import Model, Rule, Text\n\n\nclass Note(Model):\n'
                                            "    permissions = {'admin': Rule(('read',))}\n    title = Text()\n")
    monkeypatch.chdir(tmp_path)
    _, manifest = prepare(project, resolve_packages(project, ['notes']))
    assert [model['resource'] for model in manifest['models']] == ['notes.Note']


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
    app = load_runtime(project, 'acme', development=True)
    app.initialize()
    assert http(app, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 201
    c = client(app)
    company = c('POST', '/api/company', {'title': 'Example'})[1]['id']
    warehouse = c('POST', '/api/warehouse', {'title': 'Stores', 'company': company})[1]['id']
    item = c('POST', '/api/itemmaster', {'title': 'Sample', 'description': 'Reference item'})[1]['id']
    bom = c('POST', '/api/bom', {'title': 'Standard', 'item': item, 'company': company})[1]['id']
    assert c('POST', '/api/stockbalance', {'item': item, 'warehouse': warehouse, 'actual_qty': '12'})[0] == 201
    values = {'title': 'SO-1', 'company': company, 'warehouse': warehouse, 'children': {'items': [{'_key': 'r1', 'item': item, 'qty': '3', 'rate': '2.25'}]}}
    preview = c('POST', '/api/_draft/salesorder', {'values': values, 'revision': 1})
    assert preview[0] == 200, preview
    assert preview[1]['values']['children']['items'][0]['available_qty'] == '12.0000'
    status, order, _ = c('POST', '/api/salesorder', values)
    assert status == 201, order
    assert order['total'] == '6.75' and order['children']['items'][0]['description'] == 'Reference item'
    source = c('GET', '/api/salesorder/' + str(order['id']))
    headers = {b'if-match': source[2][b'etag']}
    opened = c('POST', '/api/salesorder/' + str(order['id']) + '/actions/create_work_orders', {}, headers=headers)
    assert opened[0] == 200, opened
    prompt = opened[1]['_outcome']['prompt']
    row = prompt['values']['children']['items'][0]
    submission = {'values': {'children': {'items': [{'_key': row['_key'], 'selected': True, 'qty': '2', 'bom': bom}]}}}
    planned = c('POST', f"/api/_prompts/{prompt['ticket']}/submit", submission)
    assert planned[0] == 200, planned[1]
    assert c('POST', f"/api/_prompts/{prompt['ticket']}/submit", submission)[1] == planned[1]
    assert c('GET', '/api/workorder')[1]['total'] == 1
    assert c('GET', '/api/salesorder/' + str(order['id']))[2][b'etag'] == headers[b'if-match']
    # The order's key fields and its work orders, listed through the declared relation.
    order_schema = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'salesorder')
    assert order_schema['key_fields'] == ['company', 'warehouse', 'status', 'total']
    assert order_schema['relations'][0] == {'name': 'workorder.sales_order', 'label': 'Work orders', 'model': 'workorder',
                                            'link': 'sales_order', 'limit': 5, 'order': ['-production_date', '-id']}
    related = c('GET', f"/api/salesorder/{order['id']}/related/workorder.sales_order")[1]
    assert related['total'] == 1 and related['limit'] == 5 and related['items'][0]['qty'] == '2.0000'
    report = c('POST', '/api/_reports/amendment.SalesRegister', {'filters': {'company': company}})
    assert report[0] == 200, report
    assert report[1]['rows'][0]['total'] == '6.75'
    assert c('GET', f'/api/_services/amendment.services.integrations.warehouse.checks.balance_count?warehouse={warehouse}')[1] == {'count': 1}
    c('PATCH', f'/api/company/{company}', {'payment_days': 45}, headers={b'if-match': c('GET', f'/api/company/{company}')[2][b'etag']})
    quote = {'title': 'Q-1', 'inclusive': True, 'company': company, 'issued_on': '2026-11-01', 'discount_rate': '3.33',
             'children': {'lines': [{'_key': 'a', 'qty': '1', 'rate': '105'},
                                                                      {'_key': 'b', 'qty': '2', 'rate': '10', 'taxable': False}]}}
    status, saved, _ = c('POST', '/api/quote', quote)
    assert status == 201, saved
    assert (saved['net_total'], saved['vat_total'], saved['taxable_net'], saved['grand_total']) == ('120.00', '5.00', '100.00', '125.00')
    # 120.00 * 3.33% = 3.996 is rounded half up to the Currency scale; the due date follows the company Link.
    assert (saved['discount'], saved['payable'], saved['valid_until']) == ('4.00', '121.00', '2026-12-16')
    assert c('POST', '/api/quote', {'title': 'Q-2'})[1]['valid_until'] is None
    url = f"/api/quote/{saved['id']}"
    edit = lambda body: c('PATCH', url, body, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert saved['payment_days'] == 45 and edit({'payment_days': 10})[1]['valid_until'] == '2026-11-11'
    # The stage is a State: ordinary writes cannot set it, and the lost reason does not apply to a draft.
    assert edit({'docstate': 'sent'})[1]['fields'] == {'docstate': 'This changes only through its rules'}
    assert edit({'lost_reason': 'Ignored', 'labels': ['gold']})[1]['lost_reason'] is None
    # Row changes: add one line at the top without resending the others.
    kept = [row['id'] for row in c('GET', url)[1]['children']['lines']]
    added = edit({'children': {'lines': {'create': [{'_key': 'c', 'qty': '1', 'rate': '10', 'taxable': False}], 'order': ['c', *kept]}}})[1]
    assert added['grand_total'] == '135.00' and [row['id'] for row in added['children']['lines']] == [added['_row_ids']['c'], *kept]
    assert [(item['to'], item['allowed']) for item in added['_moves']] == [('sent', True)]
    move = lambda target, to: c('POST', f'{target}/moves/{to}', {}, headers={b'if-match': c('GET', target)[2][b'etag']})
    act = lambda body: c('POST', url + '/actions/extend_validity', body, headers={b'if-match': c('GET', url)[2][b'etag']})
    assert act({'extra_days': 5})[1]['error'] == 'Extend validity is not available: the condition for this action is not met'
    # The quote number is null until the quote is sent, and only the engine writes it.
    assert saved['number'] is None and edit({'number': 'Q-1'})[1]['fields'] == {'number': 'This number is assigned by the system'}
    sent = move(url, 'sent')[1]
    assert sent['docstate'] == 'sent' and [item['to'] for item in sent['_moves']] == ['won', 'lost', 'draft', 'void']
    assert re.fullmatch(r'Q-[0-9]{4}-0001', sent['number'])
    # The quote's activity: the move and its number, then a note added on the locked quote.
    number_entry, moved = c('GET', url + '/activity')[1]['items'][:2]
    assert moved['kind'] == 'moved' and moved['data']['from'] == 'draft' and moved['data']['to'] == 'sent' and moved['data']['move'] == 'Send'
    assert number_entry['data'] == {'field': 'number', 'number': sent['number']}
    status, note, _ = c('POST', url + '/activity', {'kind': 'note', 'text': 'Customer asked for the PDF'})
    assert status == 201 and note['kind'] == 'note' and c('GET', url + '/activity')[1]['items'][0]['id'] == note['id']
    assert sent['_activity']['open_follow_ups'] == 0 and c('GET', url)[1]['_activity']['count'] == sent['_activity']['count'] + 1
    assert edit({'discount_rate': '5'})[1]['fields'] == {'discount_rate': 'This record is sent, so it is locked'}
    assert act({'extra_days': 0})[1]['fields'] == {'extra_days': 'Must be at least 1'}
    extended = act({'extra_days': 5})[1]
    assert (extended['payment_days'], extended['valid_until']) == (15, '2026-11-16')
    won = move(url, 'won')[1]
    assert won['docstate'] == 'won' and move(url, 'draft')[0] == 409
    # Winning posted one read-only ledger row through the Quote's @release hook.
    ledger = c('GET', '/api/salesledger')[1]['items']
    assert [(row['amount'], row['memo'], row['source']) for row in ledger] == [(won['payable'], 'Q-1', {'model': 'quote', 'id': won['id']})]
    assert c('POST', '/api/salesledger', {'amount': '1'})[0] == 403
    assert edit({'discount_rate': '5'})[1]['fields'] == {'discount_rate': 'This record is won, so it is locked'}
    # A lost quote takes a reason while locked; reopening it clears the reason.
    lost = c('POST', '/api/quote', {'title': 'Q-3', 'children': {'lines': [{'qty': '1', 'rate': '10'}]}})[1]
    lost_url = f"/api/quote/{lost['id']}"
    assert move(lost_url, 'sent')[0] == 200 and move(lost_url, 'lost')[1]['docstate'] == 'lost'
    assert c('GET', lost_url)[1]['number'] == sent['number'][:-1] + '2'
    reason = c('PATCH', lost_url, {'lost_reason': 'Price'}, headers={b'if-match': c('GET', lost_url)[2][b'etag']})[1]
    assert reason['lost_reason'] == 'Price'
    reopened = move(lost_url, 'draft')[1]
    assert reopened['lost_reason'] is None
    # A reopened and resent quote keeps its number.
    assert move(lost_url, 'sent')[1]['number'] == reopened['number'] == sent['number'][:-1] + '2'
    quote_schema = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'quote')
    assert [step['key'] for step in quote_schema['state']['steps'] if step['final']] == ['won', 'void']
    assert quote_schema['actions'][0]['inputs'][0]['name'] == 'extra_days'
    # A sent quote is voided with a remark; it keeps its id and its number.
    sent_quote = c('POST', '/api/quote', {'title': 'Q-4', 'children': {'lines': [{'qty': '2', 'rate': '10'}, {'qty': '1', 'rate': '4'}]}})[1]
    sent_url = f"/api/quote/{sent_quote['id']}"
    sent_number = move(sent_url, 'sent')[1]['number']
    assert sent_number == sent['number'][:-1] + '3'
    void = lambda body: c('POST', sent_url + '/moves/void', body, headers={b'if-match': c('GET', sent_url)[2][b'etag']})
    assert void({})[1]['fields'] == {'remark': 'Void needs a remark'}
    voided = void({'remark': 'Customer cancelled'})[1]
    assert (voided['docstate'], voided['number']) == ('void', sent_number)
    assert quote_schema['key_fields'] == ['company', 'docstate', 'payable', 'valid_until'] and 'relations' not in quote_schema
    number = next(field for field in quote_schema['fields'] if field['name'] == 'number')
    assert (number['type'], number['read_only'], number['sequence']) == (
        'sequence', True, {'format': 'Q-{year}-{n:04}', 'scope': [], 'assign_on': 'sent', 'date': None})
    # Declared measures: Q-1 is won; Q-3 is sent; Q-2 is a draft and Q-4 is void.
    # Count() declares no condition, so the void quote is counted.
    assert [item['name'] for item in quote_schema['measures']] == ['open_total', 'won_total', 'quotes']
    assert c('GET', '/api/quote/measures')[1] == {'measures': {'open_total': '10.50', 'won_total': '135.00', 'quotes': 4}}
    by_stage = c('GET', '/api/quote/aggregate?measures=quotes,total:sum:grand_total&group_by=docstate')[1]
    assert sorted((item['docstate'], item['quotes'], item['total']) for item in by_stage['items']) == [
        ('draft', 1, '0.00'), ('sent', 1, '10.50'), ('void', 1, '25.20'), ('won', 1, '135.00')]
    calculation = next(model for model in c('GET', '/api/schema')[1]['models'] if model['name'] == 'quote')['calculation']
    assert calculation['plan']['root_resource'] == 'amendment.Quote' and saved['_calculation']['model_schema_hash'] == calculation['model_schema_hash']
    manifest = app.business.build_manifest
    # One @cron calendar and the nightly stock check declared as a module-level Schedule.
    assert len(manifest['apis']) == 1 and len(manifest['schedules']) == 2
    assert 'amendment_stock_check' in app.business.jobs.tasks
    with app.database.transaction() as tx:
        assert tx.connection.execute("SELECT count(*) FROM evlet_schedules WHERE name<>'evlet_unsaved_purge'").fetchone()[0] == 2
        assert tx.connection.execute('SELECT count(*) FROM evlet_jobs').fetchone()[0] == 0
    install(project, 'acme', trust=True)

    # Both requests start with the same source revision and one remaining unit.
    from concurrent.futures import ThreadPoolExecutor
    action = '/api/salesorder/' + str(order['id']) + '/actions/create_work_orders'
    prompts = [c('POST', action, {}, headers=headers)[1]['_outcome']['prompt'] for _ in range(2)]
    def plan_one(prompt):
        row = prompt['values']['children']['items'][0]
        return c('POST', f"/api/_prompts/{prompt['ticket']}/submit",
                 {'values': {'children': {'items': [{'_key': row['_key'], 'selected': True, 'qty': '1', 'bom': bom}]}}})[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(plan_one, prompts))
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
    (root / 'tests').mkdir(exist_ok=True)
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


ABSTRACT = '''from evlet import Child, Decimal, Integer, Link, Model, Rule, Table, Text
from evlet.calc import F


class TradeDocument(Model):
    """A shared base in models/: fields, a Table and derive(), but no table of its own."""
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}

    class Meta:
        abstract = True
        calculation_language = 'finite-v1'

    class Line(Child):
        qty = Decimal(scale=2, default=1)
        rate = Decimal(scale=2, default=0)
        amount = Decimal(scale=2, compute=F('qty') * F('rate'))

    party = Text()
    lines = Table(Line)
    total = Decimal(scale=2, compute=lines.sum('amount'))
    line_count = Integer(derived=True)

    def derive(self):
        self.line_count = len(self.lines)


class SalesOrder(TradeDocument):
    class Meta:
        calculation_language = 'finite-v1'


class PurchaseOrder(TradeDocument):
    """Inherits the base's Meta without repeating abstract: concrete."""
'''


def test_abstract_models_are_bases_without_tables(project, monkeypatch, tmp_path):
    from evlet import Database, Link, Model, Registry, Text
    from evlet.builds import discover
    from test_rules_types import start
    package = read_package(create_app(project, 'notes'))
    shutil.rmtree(package.path / 'models')
    (package.path / 'models.py').write_text(ABSTRACT)
    monkeypatch.syspath_prepend(str(package.path.parent))
    definition = discover(package)
    assert [model.__name__ for model in definition.models] == ['PurchaseOrder', 'SalesOrder']
    module = sys.modules['notes.models']
    base = module.TradeDocument
    assert base.__abstract__ and not module.SalesOrder.__abstract__ and not module.PurchaseOrder.__abstract__
    registry = Registry(*definition.models)
    tables = {entry['name'] for entry in registry.schema()['models']}
    assert {'salesorder', 'purchaseorder'} <= tables and not any('tradedocument' in name for name in tables)
    app = start(Database(tmp_path / 'abstract.sqlite3', registry))
    try:
        with app.business.context(User(1, 'admin', 'admin')) as context:
            for model in (module.SalesOrder, module.PurchaseOrder):
                record = context.create(model, party='Acme', children={'lines': [{'qty': '2', 'rate': '5'}, {}]})
                record = context.get(model, record.id)
                assert (str(record.total), record.line_count) == ('10.00', 2)
        assert app.database.count(module.SalesOrder) == app.database.count(module.PurchaseOrder) == 1
        with app.business.context(User(1, 'admin', 'admin')) as context, \
                pytest.raises(ValueError, match=r'TradeDocument is abstract \(Meta.abstract = True\) and has no table'):
            context.create(base, party='Acme')
    finally:
        app.database.close()
    with pytest.raises(ValueError, match=r'TradeDocument is abstract .*; register the models built from it'):
        Registry(base)

    class Payment(Model):
        permissions = {'admin': Rule(('read',))}
        document = Link(base)
    with pytest.raises(ValueError, match=r'Link Payment.document: TradeDocument is abstract'):
        Registry(module.SalesOrder, Payment)
    # Class-creation checks still run on an abstract class.
    with pytest.raises(ValueError):
        class Broken(Model):
            class Meta:
                abstract = True
            x_field = Text()



SHARED_LINE = '''from evlet import Child, Decimal, Text


class LineBase(Child):
    """A shared row base in models/: not a model of its own, and not marked abstract."""
    qty = Decimal(scale=2, default=1)
    rate = Decimal(scale=2, default=0)


class PurchaseLine(LineBase):
    """A concrete row that two documents' Tables use."""
    item = Text(max_length=80)
'''

LINE_DOCUMENTS = '''from evlet import Model, Rule, Table, Text

from .lines import PurchaseLine


class PurchaseOrder(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    supplier = Text()
    lines = Table(PurchaseLine)


class PurchaseReturn(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    supplier = Text()
    lines = Table(PurchaseLine)
'''


def test_discovery_skips_child_rows_declared_in_models(project, monkeypatch, tmp_path):
    from evlet import Database, Registry
    from evlet.builds import discover
    from test_rules_types import start
    for name in [name for name in sys.modules if name == 'notes' or name.startswith('notes.')]:
        monkeypatch.delitem(sys.modules, name)
    package = read_package(create_app(project, 'notes'))
    shutil.rmtree(package.path / 'models')
    (package.path / 'models').mkdir()
    (package.path / 'models/__init__.py').write_text('')
    (package.path / 'models/lines.py').write_text(SHARED_LINE)
    (package.path / 'models/documents.py').write_text(LINE_DOCUMENTS)
    monkeypatch.syspath_prepend(str(package.path.parent))
    definition = discover(package)
    assert [model.__name__ for model in definition.models] == ['PurchaseOrder', 'PurchaseReturn']
    registry = Registry(*definition.models)
    documents = sys.modules['notes.models.documents']
    app = start(Database(tmp_path / 'lines.sqlite3', registry))
    try:
        with app.business.context(User(1, 'admin', 'admin')) as context:
            for model in (documents.PurchaseOrder, documents.PurchaseReturn):
                record = context.create(model, supplier='Gulf', children={'lines': [{'item': 'Bolt', 'qty': '3', 'rate': '2'}]})
                rows = context.get(model, record.id).lines
                assert [(row.item, str(row.qty), str(row.rate)) for row in rows] == [('Bolt', '3.00', '2.00')]
    finally:
        app.database.close()
    for name in [name for name in sys.modules if name == 'notes' or name.startswith('notes.')]:
        del sys.modules[name]


def test_first_admin_setup_over_http_is_development_only(project):
    """Under evlet serve the Administrator is created with evlet users set-password, never by the first local caller."""
    source = Path(__file__).resolve().parents[1] / 'examples/amendment'
    shutil.copytree(source, project / 'apps/amendment', ignore=shutil.ignore_patterns('__pycache__', '*.egg-info', 'build', 'dist'))
    create_instance(project, 'acme')
    install(project, 'acme', app_name='amendment', trust=True)
    served = load_runtime(project, 'acme')
    served.initialize()
    assert http(served, 'POST', '/api/auth/setup', {'username': 'admin', 'password': PASSWORD})[0] == 403
