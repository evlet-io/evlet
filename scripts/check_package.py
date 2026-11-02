"""Run with a clean wheel-installed interpreter and -I to exclude checkout imports."""

from pathlib import Path
import tempfile
import io
import json
import subprocess
import sys

import evlet
from evlet import AddField, Application, Boolean, Currency, Database, LocalAuth, Migration, Model, Registry, Text, User


def main():
    checkout = Path(__file__).resolve().parents[1]
    if Path(evlet.__file__).resolve().is_relative_to(checkout / "evlet"):
        raise RuntimeError("Package check imported the checkout instead of the installed wheel")

    from evlet.ui import PageView, Column, Text as UIText, SchemaCompiler
    from evlet.ui.serving import static_response
    compiled = SchemaCompiler().compile(PageView("example", route="/example", layout=Column(children=[UIText("Ready")])))
    assert compiled.to_dict()["layout"]["slots"]["children"][0]["props"]["text"] == "Ready"
    response = static_response("/docs")
    assert response and b'<div id="evlet-app">' in response[1].content
    assets = Path(evlet.__file__).parent / "ui/static/assets"
    assert list(assets.glob("*.js"))
    for font, licence in (("InterVariable-", "Inter-OFL.txt"), ("JetBrainsMono-", "JetBrainsMono-OFL.txt")):
        if not list(assets.glob(font + "*.woff2")) or "SIL OPEN FONT LICENSE" not in (assets / licence).read_text().upper():
            raise RuntimeError(f"Installed wheel lacks {font} fonts or {licence}")

    from evlet import PostgreSQLDatabase
    try:
        PostgreSQLDatabase('dbname=unused', Registry())
    except RuntimeError as exc:
        assert 'evlet[postgres]' in str(exc)
    else:
        raise RuntimeError('Clean wheel check unexpectedly has the optional PostgreSQL driver')

    class Product(Model):
        name = Text(required=True)
        price = Currency(required=True)

    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "package.sqlite3", Registry(Product))
        db.initialize()
        item = db.create(Product, name="Installed package", price="12.50")
        if db.get(Product, item.id).to_dict()["price"] != "12.50":
            raise RuntimeError("Installed package failed the SQLite round trip")
        UpdatedProduct = type('Product', (Model,), {
            'name': Text(required=True), 'price': Currency(required=True), 'active': Boolean(default=True),
        })
        updated = Database(db.path, Registry(UpdatedProduct))
        updated.migrate([Migration('active', AddField('product', 'active', Boolean(default=True)))])
        saved = updated.update(UpdatedProduct, item.id, price='15.25')
        if saved.to_dict()['price'] != '15.25' or not saved.active:
            raise RuntimeError('Installed package failed migration/update')
        if updated.count(UpdatedProduct, filters={'price__gte': '15.00'}) != 1:
            raise RuntimeError('Installed package failed query')
        schema = updated.registry.ui_schema()
        if schema['ui_version'] != 1 or schema['models'][0]['title_field'] != 'name':
            raise RuntimeError('Installed package failed UI metadata')
        records, total = updated.page(UpdatedProduct, search='installed', limit=1)
        if total != 1 or records[0].id != item.id:
            raise RuntimeError('Installed package failed search/count')
        auth = LocalAuth(updated, origins=('https://app.example.test',))
        app = Application(updated, auth=auth, attachments=(UpdatedProduct,))
        app.business.initialize()
        protected = app.business.schema(User(1, 'viewer', 'viewer'))
        if protected['models'][0]['permissions']['update']:
            raise RuntimeError('Installed package failed permission metadata')
        from evlet import LocalFileStorage, TenantRegistry
        storage = LocalFileStorage(Path(directory) / 'files')
        storage.initialize(updated)
        with updated.transaction() as tx:
            file_id = storage.add(tx, 'product', item.id, 'test.txt', b'installed wheel')
            row = tx.connection.execute('SELECT * FROM evlet_attachments WHERE id=?', (file_id,)).fetchone()
            assert storage.read(row) == b'installed wheel'
        catalog = TenantRegistry.create(Path(directory) / 'tenant-state', development=True)
        tenant = catalog.create_tenant('alpha', 'Alpha', 'alpha.localhost')
        assert tenant.origins and catalog.resolve('alpha.localhost') is None
        from evlet import EventStore, JobQueue, Task, LambdaHTTP, S3Storage
        events = EventStore(updated); events.initialize()
        jobs = JobQueue(updated, [Task('price', lambda tx, context, payload: tx.update(UpdatedProduct, item.id, price='20.00'))], events=events)
        jobs.initialize()
        with updated.transaction() as tx:
            jobs.enqueue(tx, 'price', {}, target=(UpdatedProduct, item.id))
        assert jobs.run_one() and updated.get(UpdatedProduct, item.id).to_dict()['price'] == '20.00'
        assert events.page(User(1, 'admin', 'admin'), app.business.policy, after='0')['items']
        assert LambdaHTTP and S3Storage
        from evlet import HTTPClient, MCPServer
        from evlet.discovery import Discovery
        description = Discovery(app.business, User(1, 'viewer', 'viewer')).describe('product')
        assert set(description['schemas']) == {'read'}
        assert description['fields'][0]['name'] == 'name'
        client = HTTPClient('http://127.0.0.1:8000')
        protocol = MCPServer(client)
        output = io.BytesIO()
        protocol.serve(io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), output)
        assert json.loads(output.getvalue())['result'] == {}
        subprocess.run([sys.executable, '-I', '-m', 'evlet', 'new', str(Path(directory) / 'starter')], check=True, stdout=subprocess.DEVNULL)
        assert (Path(directory) / 'starter/models.py').is_file()

        from evlet.cli.docs import documentation
        from evlet.cli.playground import playground_app
        assert 'cli.md' in documentation() and 'ui.md' in documentation()
        demo = playground_app(Path(directory) / 'demo')
        assert 'projects_demo' in demo.ui.views
        assert demo.database.count(demo.registry.models['customer']) == 1
        demo.database.close()
        project = Path(directory) / 'project'
        commands = [
            ['setup', str(project)],
            ['app', 'notes', '--project', str(project)],
            ['instance', 'acme', '--project', str(project)],
            ['install', 'notes', '--instance', 'acme', '--trust', '--project', str(project)],
            ['migrate', 'acme', '--trust', '--project', str(project)],
        ]
        for command in commands:
            subprocess.run([sys.executable, '-I', '-m', 'evlet', *command], check=True, stdout=subprocess.DEVNULL)
        from evlet.instances import load_runtime
        instance = load_runtime(project, 'acme')
        instance.initialize()
        assert instance.registry.models['notesitem']
        assert instance.business.auth.setup_needed()
        instance.database.close()
        updated.delete(UpdatedProduct, item.id)
    print("Installed wheel: migrations, updates, queries, deletes; import, model definition, SQLite persistence, currency, tenant catalog, private files, jobs, event replay, discovery, connector imports, starter creation, project/app/instance lifecycle, bundled Playground and offline docs passed")


if __name__ == "__main__":
    main()
