"""Model scripts (docs/model-scripts.md): the Python model wires a .js file's methods to fields and layout blocks.

Checked at start-up, served from a hashed /assets/scripts/ address with the usual CSP, and never needed by the
server: the API works the same with or without a script. The browser side is tested in
frontend/src/evlet/tests/model-scripts.test.ts."""

from importlib.util import module_from_spec, spec_from_file_location
from itertools import count
from pathlib import Path
import sys
import textwrap

import pytest

from evlet import Database, Js, Model, Registry, Text
from evlet.ui.serving import CSP
from support import http
from test_rules_types import start

NUMBER = count()
HEADER = 'from evlet import Child, Decimal, Js, Main, Model, Rule, Section, Table, Text\n'


def models(tmp_path, source, files=None):
    """Import a models module written to its own folder, with the script files beside it."""
    name = f'scripted_{next(NUMBER)}'
    folder = tmp_path / name
    folder.mkdir()
    for path, content in (files if files is not None else {'quote.js': 'export default class Quote extends evlet.Model {}\n'}).items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(content)
    (folder / 'models.py').write_text(HEADER + textwrap.dedent(source))
    spec = spec_from_file_location(name, folder / 'models.py')
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, folder


QUOTE = '''
class Line(Child):
    qty = Decimal(scale=2, on_change='qty_changed', control='qty_input', look=Js('qty_look'))


class Quote(Model):
    permissions = {'admin': Rule(('read', 'create', 'update', 'delete'))}
    customer = Text(on_change='customer_changed', tooltip=Js('customer_tip'), look='strong')
    notes = Text(tooltip='Plain text stays as it was')
    lines = Table(Line)

    class Meta:
        script = 'quote.js'
        on_open = 'opened'
        layout = (Main(('customer',)), Main(custom='summary'), Section('Map', custom='draw_map', collapsed=False), Section('More', ('notes',)))
'''


def test_the_schema_carries_the_script_its_hooks_and_custom_blocks(tmp_path):
    module, _ = models(tmp_path, QUOTE)
    registry = Registry(module.Quote)
    entry = next(item for item in registry.ui_schema()['models'] if item['name'] == 'quote')
    assert entry['script']['on_open'] == 'opened' and entry['script']['url'].startswith('/assets/scripts/')
    assert entry['script']['url'].endswith('/quote.js')
    fields = {item['name']: item for item in entry['fields']}
    assert fields['customer']['script'] == {'on_change': 'customer_changed', 'tooltip': 'customer_tip'}
    assert fields['customer']['look'] == 'strong' and 'tooltip' not in fields['customer']
    assert fields['notes']['tooltip'] == 'Plain text stays as it was' and 'script' not in fields['notes']
    line = next(item for item in registry.ui_schema()['models'] if item['name'] == 'quote_lines')
    assert line['fields'][0]['script'] == {'on_change': 'qty_changed', 'control': 'qty_input', 'look': 'qty_look'}
    blocks = entry['record_layout']['blocks']
    assert [(block['kind'], block['name'], block['main']) for block in blocks] == [
        ('section', 'Quote', True), ('custom', 'Quote 2', True), ('custom', 'Map', False), ('section', 'More', False), ('table', 'lines', False)]
    assert blocks[2]['custom'] == 'draw_map' and blocks[2]['collapsed'] is False


def test_a_changed_script_gets_a_new_address(tmp_path):
    module, folder = models(tmp_path, QUOTE)
    first = Registry(module.Quote).scripts[module.Quote]['url']
    (folder / 'helpers.js').write_text('export const tax = 0.05;\n')
    assert Registry(module.Quote).scripts[module.Quote]['url'] != first


@pytest.mark.parametrize('script, message', [
    ('missing.js', 'Quote.Meta.script: missing.js is not a file'),
    ('quote.ts', 'name a .js file'),
    ('../quote.js', 'quote.js leaves the app package'),
    ('/etc/quote.js', 'name a .js file'),
    ('.hidden/quote.js', 'name a .js file'),
    ('lib/../../quote.js', 'leaves the app package'),
])
def test_a_missing_or_wrong_script_fails_at_start_up(tmp_path, script, message):
    module, _ = models(tmp_path, QUOTE.replace("script = 'quote.js'", f'script = {script!r}'), {'quote.js': '', 'quote.ts': ''})
    with pytest.raises(ValueError, match=message.replace('.', r'\.')):
        Registry(module.Quote)


def test_hooks_need_a_script_on_their_document(tmp_path):
    module, _ = models(tmp_path, QUOTE.replace("        script = 'quote.js'\n", ''))
    with pytest.raises(ValueError, match=r'Quote: Quote.customer \(on_change, tooltip\) names a script method, so Quote needs Meta.script'):
        Registry(module.Quote)
    module, _ = models(tmp_path, '''
class Plain(Model):
    title = Text()

    class Meta:
        layout = (Section('Drawn', custom='draw'),)
''')
    with pytest.raises(ValueError, match=r'Plain: Plain.Meta.layout \(Drawn, custom=\) names a script method'):
        Registry(module.Plain)


def test_a_script_belongs_to_a_document(tmp_path):
    module, _ = models(tmp_path, QUOTE.replace("    qty = Decimal(", "    class Meta:\n        script = 'quote.js'\n\n    qty = Decimal("))
    with pytest.raises(ValueError, match='Line: a Table row uses the script of its document; declare Meta.script on Quote'):
        Registry(module.Quote)
    with pytest.raises(ValueError, match='a ledger model has no screens or document behaviour, so Meta.script is not available'):
        models(tmp_path, '''
class Entry(Model):
    amount = Decimal(scale=2)

    class Meta:
        ledger = True
        script = 'quote.js'
''')


@pytest.mark.parametrize('declare, message', [
    (lambda: Text(on_change='save'), 'on_change= names a method of the model script'),
    (lambda: Text(control='not a name'), 'control= names a method'),
    (lambda: Text(tooltip=Js('1x')), 'Js names a method'),
    (lambda: Text(look='loud'), 'look is one of strong, muted, success, warning, danger'),
])
def test_wrong_hook_names_fail_where_they_are_declared(declare, message):
    with pytest.raises(ValueError, match=message):
        declare()


PROMPT = '''
from evlet import Prompt


class Confirm(Prompt):
    order_date = Text(on_change='date_changed', tooltip=Js('date_tip'))

    class Line(Child):
        qty = Decimal(scale=2, control='qty_input', look=Js('qty_look'))

    lines = Table(Line)

    class Meta:
        script = 'quote.js'
        on_open = 'opened'

    def submit(self):
        return None
'''


def test_a_prompt_takes_a_script_and_its_open_response_carries_it(tmp_path):
    from evlet.prompts import schema
    module, _ = models(tmp_path, QUOTE + PROMPT)
    entries = schema(Registry(module.Quote), module.Confirm, module.Confirm.__form_name__)
    prompt, line = entries[0], entries[1]
    # The same file a model names, in the shape /api/schema uses: the URL, on_open and each field's hooks.
    assert prompt['script'] == {'url': Registry(module.Quote).scripts[module.Quote]['url'], 'on_open': 'opened'}
    assert next(item for item in prompt['fields'] if item['name'] == 'order_date')['script'] == {'on_change': 'date_changed', 'tooltip': 'date_tip'}
    assert line['fields'][0]['script'] == {'control': 'qty_input', 'look': 'qty_look'} and 'script' not in line
    app = start(Database(tmp_path / 'db.sqlite3', Registry(module.Quote)))
    try:
        assert http(app, 'GET', prompt['script']['url'])[0] == 200
    finally:
        app.database.close()


@pytest.mark.parametrize('change, message', [
    (("        script = 'quote.js'\n        on_open = 'opened'\n", "        title = 'Confirm'\n"), r"Confirm.order_date names a script method, so Confirm needs Meta.script"),
    (("script = 'quote.js'", "script = 'quote.ts'"), r"Confirm.Meta.script: name a .js file"),
    (("script = 'quote.js'", "script = 'missing.js'"), r"Confirm.Meta.script: missing.js is not a file"),
])
def test_a_prompts_script_and_hooks_are_checked_when_it_is_defined(tmp_path, change, message):
    with pytest.raises(ValueError, match=message):
        models(tmp_path, PROMPT.replace(*change), {'quote.js': '', 'quote.ts': ''})


def test_a_prompt_without_a_script_has_none_and_custom_blocks_stay_refused(tmp_path):
    from evlet.prompts import check, schema
    module, _ = models(tmp_path, '''
from evlet import Prompt


class Plain(Prompt):
    note = Text()

    class Meta:
        layout = (Main(('note',)), Section('Drawn', custom='draw'))

    def submit(self):
        return None
''', {})
    assert module.Plain.__script__ is None
    with pytest.raises(ValueError, match='custom blocks are not supported in a prompt'):
        check(module.Plain)
    module.Plain.Meta.layout = (module.Main(('note',)),)
    assert 'script' not in schema(Registry(), module.Plain, module.Plain.__form_name__)[0]


def test_scripts_are_served_hashed_with_the_usual_csp_and_cannot_import_evlet_files(tmp_path):
    module, folder = models(tmp_path, QUOTE, {'quote.js': 'import { tax } from "./lib/tax.js";\n', 'lib/tax.js': 'export const tax = 1;\n',
                                               'notes.txt': 'not a script\n'})
    app = start(Database(tmp_path / 'db.sqlite3', Registry(module.Quote)))
    try:
        url = app.registry.scripts[module.Quote]['url']
        status, body, headers = http(app, 'GET', url)
        assert status == 200 and body.startswith(b'import { tax }')
        assert headers[b'content-type'] == b'text/javascript; charset=utf-8'
        # The CSP is the one every page gets: script-src 'self', unchanged.
        assert headers[b'content-security-policy'] == CSP.encode() and "script-src 'self';" in CSP
        base = url.rsplit('/', 1)[0]
        assert http(app, 'GET', base + '/lib/tax.js')[0] == 200
        for path in (base + '/notes.txt', base + '/../quote.js', base + '/.hidden.js', '/assets/scripts/0000000000000000/quote.js'):
            assert http(app, 'GET', path)[0] == 404, path
        # Evlet's own bundled files are not a script's to import: a request from a script address is refused.
        from evlet.ui.serving import STATIC
        bundled = next((STATIC / 'assets').glob('*.js'), None)
        if bundled is not None:
            asset = '/assets/' + bundled.name
            assert http(app, 'GET', asset)[0] == 200
            assert http(app, 'GET', asset, headers={b'referer': f'http://127.0.0.1:8000{url}'.encode()})[0] == 404
    finally:
        app.database.close()


def test_the_server_never_depends_on_the_script(tmp_path):
    # The script file may even be broken: the API, imports and other callers never load it.
    module, _ = models(tmp_path, QUOTE, {'quote.js': 'this is not JavaScript {'})
    app = start(Database(tmp_path / 'db.sqlite3', Registry(module.Quote)))
    try:
        from support import client
        c = client(app)
        status, record, _ = c('POST', '/api/quote', {'customer': 'Acme', 'children': {'lines': [{'qty': '2'}]}})
        assert status == 201, record
        status, record, headers = c('GET', f"/api/quote/{record['id']}")
        assert status == 200 and record['customer'] == 'Acme'
        status, record, _ = c('PATCH', f"/api/quote/{record['id']}", {'customer': 'Beta'}, headers={b'if-match': headers[b'etag']})
        assert status == 200 and record['customer'] == 'Beta'
    finally:
        app.database.close()


def test_plain_models_have_no_script_and_their_schema_is_unchanged():
    class Note(Model):
        title = Text(tooltip='Shown behind a ? icon')
    registry = Registry(Note)
    entry = registry.ui_schema()['models'][0]
    assert 'script' not in entry and registry.scripts == {} and registry.script_roots == {}
    assert entry['fields'][0]['tooltip'] == 'Shown behind a ? icon' and 'look' not in entry['fields'][0]
    assert Path(__file__).name  # the module itself declares no script


# Extending a model from another app (evlet.Extend): additive @validate checks and script hooks.

EXTENDS = '''
from evlet import Extend, ValidationError, validate


class First(Extend):
    @validate
    def po_needed(self):
        ORDER.append('first')
        if self.customer == 'Acme' and not self.notes:
            raise ValidationError({'notes': 'Acme needs a PO number'})

    class Meta:
        model = 'quote'
        script = 'acme.js'
        on_change = {'customer': 'check_blacklist', 'lines.qty': 'qty_seen'}
        on_open = 'opened'


class Second(Extend):
    @validate
    def last(self):
        ORDER.append('second')

    class Meta:
        model = 'quote'
'''


def extended(tmp_path):
    own = "\n    @validate\n    def own(self):\n        ORDER.append('own')\n\n    class Meta:"
    source = 'from evlet import validate\nORDER = []\n' + QUOTE.replace('\n    class Meta:', own, 1)
    module, _ = models(tmp_path, source + EXTENDS, {'quote.js': '', 'acme.js': 'export default class Acme extends evlet.Model {}\n'})
    return module


def test_extensions_run_after_the_model_in_install_order_and_add_their_script(tmp_path):
    module = extended(tmp_path)
    registry = Registry(module.Quote, extensions=(module.First, module.Second))
    entry = next(item for item in registry.ui_schema()['models'] if item['name'] == 'quote')
    # The model's own script, then each extension's that has one; the extension's hooks travel with its script.
    assert entry['script']['url'].endswith('/quote.js')
    assert [item['url'].rsplit('/', 1)[1] for item in entry['extensions']] == ['acme.js']
    assert entry['extensions'][0]['on_change'] == {'customer': 'check_blacklist', 'lines.qty': 'qty_seen'}
    assert entry['extensions'][0]['on_open'] == 'opened'
    app = start(Database(tmp_path / 'db.sqlite3', registry))
    try:
        from support import client
        c = client(app)
        module.ORDER.clear()
        status, body, _ = c('POST', '/api/quote', {'customer': 'Acme'})
        assert status == 422 and body['fields']['notes'] == 'Acme needs a PO number'
        assert module.ORDER == ['own', 'first']
        module.ORDER.clear()
        assert c('POST', '/api/quote', {'customer': 'Acme', 'notes': 'PO 7'})[0] == 201
        assert module.ORDER == ['own', 'first', 'second']
        # The extension's script is served like the model's.
        assert http(app, 'GET', entry['extensions'][0]['url'])[0] == 200
    finally:
        app.database.close()


def test_without_the_extension_the_model_is_as_it_was(tmp_path):
    module = extended(tmp_path)
    registry = Registry(module.Quote)
    assert 'extensions' not in next(item for item in registry.ui_schema()['models'] if item['name'] == 'quote')
    assert registry.extensions == {}


@pytest.mark.parametrize('change, message', [
    (("model = 'quote'\n        script", "model = 'invoice'\n        script"), r"First.Meta.model: there is no model 'invoice'"),
    (("'lines.qty': 'qty_seen'", "'lines.price': 'qty_seen'"), r"First.Meta.on_change: Quote has no field 'lines.price'"),
    (("'customer': 'check_blacklist'", "'client': 'check_blacklist'"), r"First.Meta.on_change: Quote has no field 'client'"),
    (("script = 'acme.js'", "script = 'other.js'"), r'First.Meta.script: other.js is not a file'),
    (("model = 'quote'\n        script", "model = 'quote_lines'\n        script"), r'Line is a Table row; extend its document'),
])
def test_names_that_do_not_exist_fail_at_start_up(tmp_path, change, message):
    module, _ = models(tmp_path, QUOTE + EXTENDS.replace(*change).replace('ORDER.append', 'print'),
                       {'quote.js': '', 'acme.js': ''})
    with pytest.raises(ValueError, match=message):
        Registry(module.Quote, extensions=(module.First,))


@pytest.mark.parametrize('body, message', [
    ("    title = Text()\n", 'First.title: an Extend adds only @validate checks and script hooks'),
    ("    def helper(self):\n        pass\n", 'First.helper: an Extend adds only'),
    ("    class Meta:\n        model = 'quote'\n        layout = ()\n", r'First.Meta supports model, script, on_change, on_open, not layout'),
    ("    class Meta:\n        model = 'quote'\n        on_open = 'opened'\n", 'its hooks name methods of a script; declare Meta.script'),
    ("    pass\n", 'name the model it extends'),
])
def test_an_extension_only_adds(body, message):
    from evlet import Extend
    namespace = {'Extend': Extend, 'Text': Text}
    source = 'class First(Extend):\n' + body + ("    class Meta:\n        model = 'quote'\n" if 'Meta' not in body and body.strip() != 'pass' else '')
    with pytest.raises(ValueError, match=message):
        exec(source, namespace)


def test_a_flat_app_extends_another_apps_model_in_install_order(tmp_path):
    from evlet.cli.app import create_app
    from evlet.cli.instance import create_instance
    from evlet.cli.install import install
    from evlet.instances import load_runtime
    project = tmp_path / 'project'
    (project / 'apps').mkdir(parents=True)
    (project / 'instances').mkdir()
    create_app(project, 'sales')
    acme = create_app(project, 'acme')
    # The extending app needs no model of its own.
    (acme / 'models/item.py').unlink()
    (acme / 'models/extend.py').write_text(textwrap.dedent('''
        from evlet import Extend, ValidationError, validate


        class AcmeItem(Extend):
            @validate
            def no_blanks(self):
                if self.title.startswith(' '):
                    raise ValidationError({'title': 'Acme titles start with a letter'})

            class Meta:
                model = 'sales.SalesItem'
                script = 'acme_item.js'
                on_change = {'title': 'title_changed'}
        '''))
    (acme / 'models/acme_item.js').write_text('export default class AcmeItem extends evlet.Model {}\n')
    create_instance(project, 'acme')
    install(project, 'acme', app_name='sales', trust=True)
    install(project, 'acme', app_name='acme', trust=True)
    app = load_runtime(project, 'acme')
    try:
        model = app.database.registry.resolve('sales.SalesItem')
        (extension, entry), = app.database.registry.extensions[model]
        assert extension.__name__ == 'AcmeItem' and entry['on_change'] == {'title': 'title_changed'}
        assert entry['url'].endswith('/acme_item.js')
    finally:
        app.database.close()
        for name in list(sys.modules):
            if name.split('.')[0] in ('sales', 'acme'):
                del sys.modules[name]


# One app, one script root: a script may import any .js file of its app package (docs/model-scripts.md).

def app_package(tmp_path, monkeypatch, script="purchase_order.js"):
    """An app package with a shared trade.js at its root and a purchase/ folder whose model imports it."""
    name = f'scripted_app_{next(NUMBER)}'
    root = tmp_path / name
    (root / 'purchase').mkdir(parents=True)
    (root / '__init__.py').write_text('')
    (root / 'purchase' / '__init__.py').write_text('')
    (root / 'trade.js').write_text('export const trade = 1;\n')
    (root / 'purchase' / 'purchase_order.js').write_text('import { trade } from "../trade.js";\nexport default class PurchaseOrder {}\n')
    (root / 'purchase' / 'models.py').write_text(HEADER + textwrap.dedent(f'''
    class PurchaseOrder(Model):
        permissions = {{'admin': Rule(('read', 'create', 'update', 'delete'))}}
        supplier = Text(on_change='supplier_changed')

        class Meta:
            script = {script!r}
    '''))
    monkeypatch.syspath_prepend(str(tmp_path))
    from importlib import import_module
    return import_module(f'{name}.purchase.models'), root


def test_a_script_imports_another_folder_of_its_app(tmp_path, monkeypatch):
    module, root = app_package(tmp_path, monkeypatch)
    app = start(Database(tmp_path / 'db.sqlite3', Registry(module.PurchaseOrder)))
    try:
        url = app.registry.scripts[module.PurchaseOrder]['url']
        assert url.endswith('/purchase/purchase_order.js')
        base = url.removesuffix('purchase/purchase_order.js')
        status, body, _ = http(app, 'GET', url)
        assert status == 200 and body.startswith(b'import { trade } from "../trade.js"')
        # ../trade.js from purchase/ is the app's trade.js, served at the same hash.
        assert http(app, 'GET', base + 'trade.js')[:2] == (200, b'export const trade = 1;\n')
        # The hash covers every .js file of the app: changing the shared one changes the address.
        (root / 'trade.js').write_text('export const trade = 2;\n')
        assert Registry(module.PurchaseOrder).scripts[module.PurchaseOrder]['url'] != url
    finally:
        app.database.close()


def test_meta_script_may_go_up_inside_the_app(tmp_path, monkeypatch):
    module, _ = app_package(tmp_path, monkeypatch, script='../trade.js')
    assert Registry(module.PurchaseOrder).scripts[module.PurchaseOrder]['url'].endswith('/trade.js')


def test_a_script_outside_the_app_is_refused_at_start_up(tmp_path, monkeypatch):
    (tmp_path / 'outside.js').write_text('')
    module, _ = app_package(tmp_path, monkeypatch, script='../../outside.js')
    with pytest.raises(ValueError, match=r'PurchaseOrder.Meta.script: \.\./\.\./outside\.js leaves the app package'):
        Registry(module.PurchaseOrder)


def test_a_link_out_of_the_app_is_neither_a_script_nor_served(tmp_path, monkeypatch):
    outside = tmp_path / 'secret.js'
    outside.write_text('export const secret = 1;\n')
    module, root = app_package(tmp_path, monkeypatch)
    (root / 'purchase' / 'linked.js').symlink_to(outside)
    app = start(Database(tmp_path / 'db.sqlite3', Registry(module.PurchaseOrder)))
    try:
        base = app.registry.scripts[module.PurchaseOrder]['url'].removesuffix('purchase/purchase_order.js')
        assert http(app, 'GET', base + 'purchase/linked.js')[0] == 404
    finally:
        app.database.close()
    module.PurchaseOrder.Meta.script = 'linked.js'
    with pytest.raises(ValueError, match='linked.js leaves the app package'):
        Registry(module.PurchaseOrder)
