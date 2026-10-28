"""Run from the checkout with uvicorn examples.playground.app:create_app --factory."""

import os
from pathlib import Path

from evlet import Application, Database
from .models import registry
from .migrate import migrations


def create_app():
    path = os.environ.get("EVLET_DATABASE", str(Path(__file__).parent / ".data" / "playground.sqlite3"))
    return Application(Database(path, registry, migrations=migrations))
