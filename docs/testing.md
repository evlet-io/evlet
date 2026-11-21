# Testing an app

`evlet.testing` tests an app the way an instance runs it. `TestApp` builds a
temporary instance from your app on a new SQLite database, with the same steps as
`evlet install`: the app's models, migrations, reports, APIs, permissions and
workspaces. A `Client` sends JSON requests through the real ASGI application, so
every account, role, row, field and action check runs. Nothing is mocked, and
nothing in your project or its instances is written.

`evlet app NAME` writes a first test, `apps/NAME/tests/test_NAME.py`. Run it from
the project folder:

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest apps/notes/tests
```

`evlet.testing` itself needs no test framework; pytest is only the usual runner.

## A test

```python
from pathlib import Path

from evlet.testing import TestApp
import pytest

APP = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    with TestApp(APP, directory=tmp_path) as running:
        yield running


def test_an_editor_creates_an_item(app):
    editor = app.client(role='editor')
    created = editor.post('/api/notesitem', {'title': 'First item'})
    assert created.status == 201
    assert editor.get('/api/notesitem/' + created.data['id']).data['title'] == 'First item'


def test_a_viewer_cannot_create(app):
    assert app.client(role='viewer').post('/api/notesitem', {'title': 'No'}).status == 403
```

## TestApp

`TestApp(path, *, apps=None, instance=None, directory=None)`

- `path` is an app folder (the one with `pyproject.toml`) or a project folder. For
  a project, `apps` names the apps to install together (default: every app in
  `apps/`), so apps that depend on each other are tested as one instance.
- `instance` names an instance of the project whose `config.py` and `routes.py`
  are used, for example its time zone, hosts and `Security` rules. Its database,
  files and secret key are not opened. Without it, the test instance is called
  `test`, and admins sign in without two-step.
- `directory` holds the temporary instance. Pass pytest's `tmp_path`; without it
  a new temporary directory is used and `close()` removes it.

Use it as a context manager, or call `close()`. Each `TestApp` is a new, empty
database, so tests do not see each other's records.

| Member | What it does |
| --- | --- |
| `client(user=None, *, role=None)` | A `Client` signed in as `user` (a username or `User`), or as an account with `role`, created when missing. Without either, an anonymous client. |
| `as_admin()` | `client(role='admin')`. |
| `user(username=None, *, role='admin', password=None)` | The account, created with `role` when it does not exist yet. Give `password` to sign in with it through `Client.sign_in`. |
| `context(user=None, *, role='admin')` | A business context for Python-level tests: `ctx.create(...)`, `ctx.update(...)`, actions, with that account's permissions. |
| `model(name)` | A model class by class name (`NotesItem`) or table (`notesitem`), as the instance loaded it. Use it with `context()` instead of importing the app's modules. |
| `application`, `database`, `business` | The ASGI application, its `Database` and its business layer. |

The client's session is made directly for the account; it is the same session a
sign-in creates. To test sign-in itself, create the account with a password and
use `Client.sign_in`.

## Client

`get(path)`, `post(path, data)`, `put(path, data)`, `patch(path, data)`,
`delete(path)` and `request(method, path, data=None, *, headers=())` return a
`TestResponse` with `status`, `data` (the decoded JSON, or `None`), `headers` (lower
case names) and the raw `body`. Writes carry the request protection header
(`X-Evlet-Request: 1`) and the session's CSRF token, as the browser interface
sends them. `sign_in(username, password)` signs in through `POST /api/auth/login`
and keeps the session.

The routes are the ones described in [the UI and HTTP API](ui.md): `/api/<table>`
lists and creates, `/api/<table>/<id>` reads, changes and deletes, and a model's
table is its class name in lower case (`NotesItem` is `notesitem`). Validation
errors answer 422 with the fields that failed; a refused permission answers 403.

## Limits

- The test instance is a development instance on loopback. It does not start the
  job worker; jobs are queued in its database.
- An app imported straight from its folder (pytest imports the app's
  `__init__.py` while collecting `apps/NAME/tests`) is dropped from
  `sys.modules` when a `TestApp` starts: the instance imports its own build of
  the same source. Get model classes with `app.model(...)`.
- Tests run app code: test only apps you trust, as with `evlet install`.
