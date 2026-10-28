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
