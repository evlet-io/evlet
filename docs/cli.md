# Install Evlet once, create apps, create instances

The installed Python package contains the CLI, browser UI, Playground, templates
and documentation. Python 3.12 or later is required. Ordinary app development and
serving need no Node.js, Vite, separate frontend server or framework checkout.

## First project

```sh
python -m pip install evlet
evlet setup myproject
cd myproject
evlet app notes
evlet instance acme
evlet install notes --instance acme
evlet serve acme
```

Installation asks whether you trust the selected Python apps. In automation,
review their source first and pass `--trust`. App dependencies are included in
that confirmation. Open http://127.0.0.1:8000 and create your administrator.
There is no default password. The existing Docs, generated models, forms,
permissions, actions and private-file interfaces use the bundled browser UI.

`setup` creates only `apps/` and `instances/`. It does not create a database,
copy the framework, download dependencies or start a service. Existing directories
are never overwritten. Commands discover the project from the current directory
or its parents. `--project /path/to/myproject` works before or after a command.

## Commands

```sh
evlet setup myproject
evlet app notes
evlet app --get https://github.com/company/notes.git
evlet app --list
evlet instance acme
evlet instance --list
evlet install notes --instance acme --trust
evlet serve acme --port 8000
evlet dev --instance acme
evlet migrate acme --trust
evlet playground --port 8000
evlet docs --port 8001
```

`python -m evlet` accepts the same commands. The original `new`, `login`,
`logout`, `describe` and `mcp` commands retain their existing contracts.
`evlet dev` selects the sole instance in a project. With several instances it
asks in a terminal; automation must pass `--instance`. Development reloads Python
changes and runs the existing short-task queue. It never applies model migrations
implicitly. Stop the server and run `migrate` after adding a migration. Normal
`serve` checks locked source versions and content; run `migrate` to record an
intentional source change. Use `serve --jobs` to enable the short-task queue in
that process. No worker is mandatory.

`playground` works outside a project, uses temporary demo data, and removes that
data on exit. It runs the existing framework examples, layouts and Projects demo.
It does not read `EVLET_DATABASE`, create an app or touch customer instances.
The Projects demo retains its separate browser-local storage behavior.
`docs` serves this installed version's guides locally without a login or database.

## App package

```text
apps/notes/
  pyproject.toml
  README.md
  .gitignore
  notes/
    __init__.py
    hooks.py
    permissions.py
    models/__init__.py
    views/__init__.py
    actions/__init__.py
    migrations/__init__.py
  tests/test_models.py
```

Apps contain reusable Python definitions and can have their own Git repositories,
versions and distributions. Their `pyproject.toml` uses standard Python metadata:

```toml
[project]
name = "notes"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["evlet>=0.1.0,<0.2"]

[tool.evlet]
app = true
title = "Notes"
```

Use a lowercase Python package name, at most 48 characters, with letters, digits
and underscores. Framework and standard-library names are reserved. The current
layout is a package directly below the app directory, with a static name/version
and an explicit `hooks.py`; `src` layouts and dynamic metadata are not supported.
Keep customer data and secrets in instances, outside the app repository.

`notes/hooks.py` exports an `AppDefinition`:

```python
from evlet import AppDefinition
from .models import models
from .views import views
from .actions import actions
from .permissions import rules
from .migrations import migrations

app = AppDefinition(
    models=models,
    views=views,
    actions=actions,
    permissions=rules,
    migrations=migrations,
)
```

The contract composes the existing APIs. It also accepts `children`, `attachments`,
`tasks`, `schedules`, and a `ui=ViewRegistry(...)` for view extensions and component
contracts. `views` and UI registries supply the existing routes and navigation.
Custom renderer code still needs a framework frontend build; registering a Python
component contract does not install JavaScript. App-specific assets can remain
Python package resources; this CLI does not expose arbitrary app directories over
HTTP. Hooks, integration helpers and event-producing action handlers are ordinary
trusted Python code. No source code is sent to the browser for execution.

Models, actions, routes, permission rules and task names must not conflict across
apps. The generated starter prefixes its example model with the app name.
Permission rules use existing `(role, model): Rule(...)` pairs. Duplicates fail
installation rather than silently replacing another app's rules. HTTP clients and
cross-app user workflows must continue through permission-checked interfaces.
Direct Python sessions and handlers are trusted administrator code.

When an action needs the instance's queue or event store, `actions` may be a
factory: `def actions(services): return (Action(...),)`. It receives the selected
instance's existing `database`, `auth`, `policy`, `jobs`, `events` and `storage`
objects. Capture `services.jobs` in a handler and call its existing
`enqueue(tx, ...)` API inside the action transaction. The factory runs during
registration; it must return definitions and must not perform external effects.
Ordinary action tuples need no factory.

## Dependencies and Git

Fetching validates metadata, Python/Evlet compatibility, requirement syntax,
package paths and the resolved commit. It never imports the app, runs a build,
installs dependencies, initializes submodules or runs repository hooks. Sources
are staged and moved into `apps/` only after validation. HTTPS and `ssh://` URLs
are supported. Passwords must stay in your Git credential setup, outside URLs.
Credential helpers are not inherited by the isolated fetch command; private
repositories can use SSH with an existing agent. Local paths, submodules, direct
URL requirements and app-dependency extras are not supported in this first CLI.

Fetching reports declared dependencies. Installation resolves app dependencies
from the project's `apps/` directories in dependency order, checks Python
packages already installed in the current environment, and rejects missing,
incompatible or circular dependencies. It does not run pip implicitly. Fetch
missing app sources or install the required Python dependency explicitly, then
retry. Merely passing validation does not make downloaded code trustworthy.

## Instance configuration and isolation

```text
instances/acme/
  config.py
  apps.lock
  data/database.sqlite3
  data/backups/
  files/private/
```

Creation writes only `config.py` and an empty `apps.lock`. The database and private
files are created when needed. Public files and log directories are reserved for
future use; they are not exposed automatically. Process logs go to the terminal
or the configured process supervisor. An instance owns its accounts, sessions,
records, attachments, jobs, schedules, events and migration history. Several apps
share those services inside one instance. Different instances have separate
SQLite databases and bound private-file directories.

```python
from evlet import Instance

instance = Instance(name="acme", database="sqlite", storage="local", domain=None)
```

The CLI supports SQLite/local storage. Existing PostgreSQL, tenant routing, S3 and
other framework APIs remain available through explicit Python configuration.
PostgreSQL instance creation is intentionally rejected until its isolation strategy
is specified. Do not point the new installer at an existing unmanaged database.
It will reject it rather than adopt it. Existing Playground data stays in place.

For a conventional deployment, provision the administrator locally first, then
set `domain="app.example.com"`. This selects that exact HTTPS origin, secure
cookies and disables browser administrator setup. Terminate TLS with a reverse
proxy and manage the process through normal infrastructure. A non-loopback bind
requires a configured domain. The CLI does not configure DNS, TLS, system services,
proxy trust or public deployment. This is not a production-readiness claim.

## Migrations, lockfiles and recovery

Each app supplies its complete ordered history of existing `Migration` objects.
Use `CreateModel` for new app models and the existing explicit operations for
schema changes. Keep published migration schemas frozen. The generated initial
migration contains a frozen copy of its starter model, separate from the live
model definition. The installer prefixes migration
names with the app identity and preserves the combined database's applied order.
A later release of one app can append migrations after another app's history.
Checksums reject changed, removed or reordered applied migrations.

Installation validates contributions, creates a SQLite backup, and runs the
migration batch against a staged copy. It initializes the existing services and
registers schedules there. Only after validation does it publish the database,
record the installed manifest and atomically replace `apps.lock`. A failed staged
migration leaves the active database, lock and private files intact. Backups stay
under `data/backups/`; retain matching source revisions and private files as part
of your backup policy. SQL backups alone do not include uploaded file bytes.

The lock records each app's version, source, full Git commit when available, and
SHA-256 of metadata and package contents. The database stores the actual installed
manifest separately. Git revisions identify repository sources; the content hash
also detects uncommitted changes. Reproducing an uncommitted/local app requires
preserving those exact source files. The lock is not a source-code archive.

Registered app imports always compile their Python source, including helpers first
loaded by actions or jobs. Stale bytecode caches are ignored and sourceless `.pyc`
helpers are rejected. Unrelated Python dependencies keep their normal import
behavior.

Python dependency constraints are verified against the active environment;
retain that environment's dependency lock for reproducible deployment.

Lifecycle commands and CLI servers hold an instance file lock. Stop servers and
other Python writers before installing or migrating. Direct Python consumers
must cooperate with that operational rule; arbitrary external writers cannot be
made safe by a CLI file lock. Several CLI server processes may share a running
instance on POSIX; migration needs the exclusive lock.

An interrupted database/lock publication leaves a recovery journal. Serving
refuses that state. Run `evlet migrate acme --trust` after reviewing the current
sources. Recovery reconciles the lock only when the database matches one of the
recorded states, then retries normal validation. Unknown mismatches fail closed
and require restoring the matching database, files, lock and app source together.

App updates/removal, automatic source checkout, rollback commands and backup/
restore commands remain later lifecycle work. Do not remove app source used by an
instance. Changing a schedule's task, payload or interval needs a later explicit
schedule-update API; unchanged schedules retain their due time and activation.

## Schedules

```python
from evlet import AppDefinition, Schedule, Task


def refresh(tx, context, payload):
    pass  # Use the existing transaction API for short, bounded work.

app = AppDefinition(
    tasks=(Task("notes_refresh", refresh),),
    schedules=(Schedule("notes_hourly", "notes_refresh", every=3600),),
)
```

Schedule registration does not execute jobs. `dev` or `serve --jobs` runs the
existing queue and scheduler. System tasks are trusted; user-initiated actions and
jobs retain the existing account, row and field checks. See the jobs guide for
lease, retry and external-effect limits.

## Troubleshooting

An app source mismatch requires reviewing the changes and running an explicit
migration, even when only non-model source changed. A changed model without its
migration fails validation and preserves the installed state. A busy instance
requires stopping its server before retrying. Missing dependencies must be
resolved in the current Python environment or fetched into this project.

Framework contributors still need Node.js to build browser assets before creating
a wheel. Run the frontend tests/build, Python tests, `python -m build`, and the
installed-package checks. Packaging copies the maintained source examples and
guides into the wheel; generated projects contain no framework copies.
