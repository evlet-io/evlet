"""Run with a clean wheel-installed interpreter and -I to exclude checkout imports."""

from pathlib import Path
from importlib import metadata, util
import argparse
import tempfile
import tarfile
import io
import json
import subprocess
import sys

import evlet
from evlet import AddField, Application, Boolean, Currency, Database, LocalAuth, Migration, Model, Registry, Text, User


def check_pwa_package(sdist):
    """Check installed responses and both rebuildable sources and built assets in the release archive."""
    from evlet.ui.serving import static_response
    assets = Path(evlet.__file__).parent / 'ui/static/assets'
    names = ('evlet.webmanifest', 'evlet-sw.js', 'evlet-offline.html')
    for name in names:
        response = static_response('/assets/' + name, {'private_value': 'must-not-cache', 'theme': 'dark'}, theme='dark')
        assert response is not None, f'Installed wheel lacks PWA asset {name}'
        status, content, headers = response
        assert status == 200 and content.content == (assets / name).read_bytes()
        if name.endswith('.webmanifest'):
            assert dict(headers)[b'content-type'] == b'application/manifest+json'
            manifest = json.loads(content.content)
            assert manifest['start_url'] == '/app' and manifest['scope'] == '/'
            assert all(static_response(icon['src']) is not None for icon in manifest['icons'])
        elif name.endswith('.js'):
            assert dict(headers)[b'service-worker-allowed'] == b'/'
            assert dict(headers)[b'cache-control'] == b'no-cache'
        else:
            assert b'evlet-config' not in content.content and b'must-not-cache' not in content.content
            assert b'data-theme' not in content.content
    with tarfile.open(sdist) as archive:
        prefix = f'evlet-{metadata.version("evlet")}/'
        for folder in ('frontend/public/assets', 'evlet/ui/static/assets'):
            for name in names:
                path = f'{folder}/{name}'
                try:
                    source = archive.extractfile(prefix + path)
                except KeyError:
                    raise RuntimeError(f'Source distribution lacks PWA asset {path}') from None
                assert source is not None and source.read() == (assets / name).read_bytes()
        assert archive.extractfile(prefix + 'evlet/resources/bootstrap.lock').read() == \
            (Path(evlet.__file__).parent / 'resources/bootstrap.lock').read_bytes()
        # The source's development entry is typechecked by npm run build and imports this fixture.
        for path in ('frontend/evlet.html', 'frontend/src/evlet/tests/server.ts', 'frontend/src/evlet/tests/api-fixture.json'):
            assert archive.getmember(prefix + path).isfile(), f'Source distribution lacks {path}'


def main():
    checkout = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdist', type=Path, default=checkout / 'dist' / f'evlet-{metadata.version("evlet")}.tar.gz')
    args = parser.parse_args()
    if Path(evlet.__file__).resolve().is_relative_to(checkout / "evlet"):
        raise RuntimeError("Package check imported the checkout instead of the installed wheel")
    for optional in ('authlib', 'joserfc', 'boto3', 'weasyprint', 'jinja2'):
        assert util.find_spec(optional) is None, f'Clean wheel check unexpectedly has optional dependency {optional}'
    check_pwa_package(args.sdist)

    from evlet.cli.setup import installer_bootstrap
    with installer_bootstrap(sys.executable) as command:
        lock = Path(command[-1])
        assert lock.is_relative_to(Path(evlet.__file__).parent)
        assert 'pip==' in lock.read_text() and '--hash=sha256:' in lock.read_text()
        assert '--require-hashes' in command and '--only-binary=:all:' in command

    from evlet.ui.serving import static_response
    response = static_response("/docs")
    assert response and b'<div id="evlet-app">' in response[1].content
    assets = Path(evlet.__file__).parent / "ui/static/assets"
    assert list(assets.glob("*.js"))
    for font, licence in (("ibm-plex-sans-", "IBMPlexSans-OFL.txt"), ("JetBrainsMono-", "JetBrainsMono-OFL.txt")):
        if not list(assets.glob(font + "*.woff2")) or "SIL OPEN FONT LICENSE" not in (assets / licence).read_text().upper():
            raise RuntimeError(f"Installed wheel lacks {font} fonts or {licence}")
    for logo in ("evlet-logo-primary.svg", "evlet-logo-white.svg", "evlet-icon-blue.svg"):
        if not (assets / logo).read_bytes().startswith(b"<?xml"):
            raise RuntimeError(f"Installed wheel lacks the {logo} logo")

    print_fonts = Path(evlet.__file__).parent / "resources/print/fonts"
    for font in ("NotoSans-Regular", "NotoSans-Bold", "NotoSansArabic-Regular", "NotoSansArabic-Bold"):
        if not (print_fonts / (font + ".woff2")).read_bytes().startswith(b"wOF2"):
            raise RuntimeError(f"Installed wheel lacks the {font} print font")
    for licence in ("NotoSans-OFL.txt", "NotoSansArabic-OFL.txt"):
        if "SIL OPEN FONT LICENSE" not in (print_fonts / licence).read_text().upper():
            raise RuntimeError(f"Installed wheel lacks {licence}")
    from evlet.printing import available
    if available():
        raise RuntimeError('Clean wheel check unexpectedly has the optional print extra')

    class Product(Model):
        title = Text(required=True)
        price = Currency(required=True)

    with tempfile.TemporaryDirectory() as directory:
        db = Database(Path(directory) / "package.sqlite3", Registry(Product))
        db.initialize()
        item = db.create(Product, title="Installed package", price="12.50")
        if db.get(Product, item.id).to_dict()["price"] != "12.50":
            raise RuntimeError("Installed package failed the SQLite round trip")
        UpdatedProduct = type('Product', (Model,), {
            'title': Text(required=True), 'price': Currency(required=True), 'active': Boolean(default=True),
        })
        updated = Database(db.path, Registry(UpdatedProduct))
        updated.migrate([Migration('active', AddField('product', 'active', Boolean(default=True)))])
        saved = updated.update(UpdatedProduct, item.id, price='15.25')
        if saved.to_dict()['price'] != '15.25' or not saved.active:
            raise RuntimeError('Installed package failed migration/update')
        if updated.count(UpdatedProduct, filters={'price__gte': '15.00'}) != 1:
            raise RuntimeError('Installed package failed query')
        schema = updated.registry.ui_schema()
        if schema['ui_version'] != 1 or schema['models'][0]['title_field'] != 'title':
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
        assert description['fields'][0]['name'] == 'title'
        client = HTTPClient('http://127.0.0.1:8000')
        protocol = MCPServer(client)
        output = io.BytesIO()
        protocol.serve(io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'), output)
        assert json.loads(output.getvalue())['result'] == {}
        # cryptography is a dependency: secrets at rest are sealed with AES-GCM (evlet.secrets).
        from evlet.secrets import SecretKey
        key = SecretKey(Path(directory) / 'secret.key')
        assert key.decrypt(key.encrypt(b'secret', b'check'), b'check') == b'secret'
        assert (Path(directory) / 'secret.key').stat().st_mode & 0o777 == 0o600

        from evlet.cli.docs import documentation
        from evlet.cli.playground import playground_app
        assert 'cli.md' in documentation() and 'ui.md' in documentation()
        demo = playground_app(Path(directory) / 'demo')
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
    print("Installed wheel: migrations, updates, queries, deletes; import, model definition, SQLite persistence, currency, tenant catalog, private files, jobs, event replay, discovery, connector imports, project/app/instance lifecycle, bundled Playground and offline docs passed")


if __name__ == "__main__":
    main()
