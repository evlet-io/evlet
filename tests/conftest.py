import pytest

from evlet import Application, Database
from examples.playground.models import registry


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
