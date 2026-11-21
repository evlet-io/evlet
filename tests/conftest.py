import pytest

from evlet import Application, Database
from examples.playground.models import registry
from support import PASSWORD


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "test.sqlite3", registry)
    db.initialize()
    return db


@pytest.fixture
def application(database):
    return Application(database)


@pytest.fixture
def http_capacity(monkeypatch):
    """Explicit capacity for tests of concurrency invariants rather than default overload behavior."""
    import importlib
    module = importlib.import_module('evlet.runtime')
    pools = []
    def configure(**limits):
        pool = module.HTTPRuntime(limits)
        pools.append(pool)
        monkeypatch.setattr(module, '_runtime', pool)
    yield configure
    for pool in pools:
        pool.close()


@pytest.fixture(scope='module')
def hashed():
    from evlet.auth import password_hash
    return password_hash(PASSWORD)


@pytest.fixture
def secured(tmp_path, monkeypatch, hashed):
    """The Playground application with admin, editor and viewer users who sign in with PASSWORD (support.client)."""
    from examples.playground.app import create_app
    monkeypatch.setenv('EVLET_DATABASE', str(tmp_path / 'business.sqlite3'))
    app = create_app()
    app.database.initialize()
    app.business.initialize()
    with app.database.transaction() as tx:
        for role in ('admin', 'editor', 'viewer'):
            tx.connection.execute('INSERT INTO evlet_users(username,password,role) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password', (role, hashed, role))
    return app
