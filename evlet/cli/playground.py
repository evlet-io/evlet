"""Run the framework demo independently of projects or customer instances."""

from importlib import import_module
from pathlib import Path
import sys
import tempfile

from .. import Database, LocalAuth
from .serve import ManagedRuntime, run_server


def modules():
    try:
        return tuple(import_module('evlet.resources.playground.' + name) for name in ('app', 'models', 'migrate'))
    except ModuleNotFoundError as exc:
        if exc.name not in ('evlet.resources.playground', 'evlet.resources.playground.app'):
            raise
        # Contributors use the same source examples that the build bundles.
        root = Path(__file__).resolve().parents[2]
        if not (root / 'examples/playground/app.py').is_file():
            raise ValueError('Playground resources are missing from this installation') from exc
        # `python -m evlet` no longer puts the working directory on sys.path.
        if str(root) not in sys.path:
            sys.path.append(str(root))
        return tuple(import_module('examples.playground.' + name) for name in ('app', 'models', 'migrate'))


def playground_app(directory, port=8000):
    app_module, models, migrate = modules()
    database = Database(Path(directory) / 'playground.sqlite3', models.registry, migrations=migrate.migrations)
    auth = LocalAuth(database, origins=(f'http://127.0.0.1:{port}', f'http://localhost:{port}'), secure_cookies=False, allow_setup=True)
    app = app_module.build_app(database, auth)
    app.initialize()
    customer = database.create(models.Customer, name='Example customer', email='hello@example.test')
    project = database.create(models.Project, name='Explore Evlet', customer=customer.id)
    database.create(models.ProjectTask, project=project.id, title='Try models, actions and private files')
    return app


def playground(*, port=8000):
    with tempfile.TemporaryDirectory(prefix='evlet-playground-') as directory:
        print(f'Playground: http://127.0.0.1:{port}\nDemo data is temporary. Create an administrator to explore the existing UI.', flush=True)
        run_server(ManagedRuntime(lambda: playground_app(directory, port), jobs=True), host='127.0.0.1', port=port, lifespan='on', proxy_headers=False)
