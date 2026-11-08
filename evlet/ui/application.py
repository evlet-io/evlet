"""A small development entry point for Python-authored applications."""

from pathlib import Path
import re
from ..app import Application
from ..database import Database
from ..models import Registry
from ..auth import LocalAuth


class App(Application):
    """Local application defaults. Supply database/auth explicitly for a deployed site."""

    def __init__(
        self, name, *, models=(), database=None, auth=None, **options
    ):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise ValueError(
                "Use an application name containing letters, numbers, underscores or hyphens"
            )
        if database is None:
            folder = Path(".data")
            folder.mkdir(exist_ok=True)
            database = Database(folder / (name + ".sqlite3"), Registry(*models))
        elif models:
            raise ValueError("Register models on the supplied database registry")
        if auth is None:
            auth = LocalAuth(
                database,
                origins=("http://127.0.0.1:8000", "http://localhost:8000"),
                secure_cookies=False,
                allow_setup=True,
            )
        super().__init__(database, auth=auth, name=name, **options)
