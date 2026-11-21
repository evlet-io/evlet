"""A small development entry point for Python-authored applications."""

from pathlib import Path
import re
from ..app import Application
from ..database import Database
from ..models import Registry
from ..auth import LocalAuth


class DevApp(Application):
    """Local development defaults for a script that serves models without a project: a SQLite file in .data/
    and loopback sign-in with first-admin setup. Supply database/auth explicitly for a deployed site.
    Named App before 0.4.x; that name still works and warns (docs/api-stability.md)."""

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


def __getattr__(name):
    if name == 'App':
        from ..fields import renamed_warning
        renamed_warning('App', 'DevApp')
        return DevApp
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
