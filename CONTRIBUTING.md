# Work on the framework

These steps are for changing Evlet itself. To build an app, start with
[your first site](docs/getting-started.md).

## Open the Playground on macOS

Install Git, Python 3.12+, and Node 24.15+ (24.x). If you use Homebrew:

```bash
brew install git python@3.12 node@24
```

Then, in Terminal:

```bash
git clone https://github.com/evlet-io/evlet.git
cd evlet
bash scripts/playground.sh --setup
```

This private repository requires your GitHub account to have access. You can also
download the repository as a ZIP while signed in to GitHub, extract it, open
Terminal in the extracted folder, and run the same launcher command.

The launcher installs the locked dependencies, starts both servers and an optional
job worker, checks the API, and opens the Playground in your default browser. Keep Terminal open while
using the app. Press **Control-C** to stop its processes; your SQLite data remains.
On later runs, use `bash scripts/playground.sh` from the same folder. The launcher
activates Homebrew's versioned Node and Python paths for its own process only.

Logs are in `examples/playground/.data/logs/5173/` by default. If ports 8000 or 5173 are already
occupied, the launcher exits without stopping any existing service. Linux can
use the same launcher with Python and Node already installed. Set
`EVLET_OPEN_BROWSER=0` for noninteractive validation.

For an existing Milestone 1, 2, or 3 database, stop the launcher and run
`.venv/bin/python -m examples.playground.migrate` before restarting it. SQLite upgrades create
a backup and preserve existing records. When `EVLET_POSTGRES_DSN` is set, the
command upgrades PostgreSQL instead; take an operator-managed PostgreSQL backup
first, as described in the [PostgreSQL guide](docs/postgresql.md). See the [model engine guide](docs/model-engine.md)
for the API, migration operations, and current limits.

## Install for development

Requires Python 3.12+ with `venv`, Node **24.15+ in the 24.x line**, and npm.
The cloud environment was verified with Python 3.12.14 and Node 24.19.0.
Run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
npm --prefix frontend ci
```

In a managed cloud checkout, use the existing checkout at `/workspace/evlet` and set writable
package caches if the default home directory is read-only:

```bash
export PIP_CACHE_DIR=/workspace/.cache/pip
export npm_config_cache=/workspace/.cache/npm
```

The lockfiles pin development dependencies and preserve package-integrity checks.
Do not regenerate them during routine setup. To intentionally update the Python
lock with `uv` installed, run `uv pip compile pyproject.toml --extra dev --universal
--generate-hashes --output-file requirements-dev.lock`, then rerun validation.
`uv` is only a lock-maintenance tool; ordinary setup uses pip.

## Run the Playground

From the root, start the backend in one terminal:

```bash
.venv/bin/python -m uvicorn examples.playground.app:create_app --factory --host 127.0.0.1 --port 8000
```

In another terminal, start Vite:

```bash
npm --prefix frontend run dev
```

The backend listens on port 8000; Vite listens on port 5173 and proxies `/api` and
`/health` to it. Both bind to loopback. Use your development environment's normal
browser access where supported. Open http://127.0.0.1:5173 for the Evlet UI. Sign-in opens Docs.
The sidebar lists the models with their database-backed screens.
http://127.0.0.1:5173/evlet.html opens the component Library in development.

Create the first administrator account when prompted. Add Editor or Viewer
accounts in Controls at `/controls/users`. Docs opens first; choose a model in the sidebar to create a customer, an invoice referencing it, and a payment
referencing that invoice. Refresh or restart the backend to verify persistence.
The default database is `examples/playground/.data/playground.sqlite3`, ignored
by Git. `EVLET_DATABASE` can select another SQLite file. Each database has its own accounts.
Projects demonstrate child tasks, Start/Complete actions, private attachments, and
a queued **Recalculate progress** action. The launcher starts its worker; with
manual startup, run `.venv/bin/python -m examples.playground.worker` separately.

Check the live workflow from the root:

```bash
curl --fail http://127.0.0.1:8000/health
.venv/bin/python scripts/smoke.py --base-url http://127.0.0.1:5173
```

The smoke check prompts for an existing Admin or Editor account. It creates a
uniquely named customer, invoice, payment, and Project, checks
updates, queries, validation, protected deletes, then removes its own records. Use a disposable database via `EVLET_DATABASE` for validation runs when you
do not want sample records in your working database. It also checks validation,
foreign-key errors, and exact decimal serialization through real HTTP requests.

## Use the Python API

```python
from evlet import Application, Currency, Database, Link, Model, Registry, Text

class Customer(Model):
    name = Text(required=True)

class Invoice(Model):
    customer = Link(Customer, required=True)
    reference = Text(required=True, unique=True)
    total = Currency(required=True, minimum="0.01")

database = Database(".data/example.sqlite3", Registry(Customer, Invoice))
database.initialize()

with database.transaction() as transaction:
    customer = transaction.create(Customer, name="Acme")
    invoice = transaction.create(
        Invoice, customer=customer.id, reference="INV-001", total="100.10"
    )

assert database.get(Invoice, invoice.id).to_dict()["total"] == "100.10"
# Configure LocalAuth before serving protected records over HTTP.
app = Application(database)
```

Transactions commit on success and roll back together on failure. Use decimal
strings for currency. `Integer` is also available; `Text` supports length bounds,
required values, defaults, and uniqueness. `Link` uses actual SQLite foreign keys.
An application registers models explicitly, so unrelated classes are not exposed.
When shipping migrations, declare the complete history with
`Database(path, registry, migrations=migrations)` so fresh initialization records
its baseline. Apply later schema changes explicitly with `database.migrate(migrations)`.
See the [migration guide](docs/model-engine.md#explicit-migrations).

## Test and build

```bash
.venv/bin/python -m pytest
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python -m build --no-isolation
python3 -m venv /tmp/evlet-wheel-check
/tmp/evlet-wheel-check/bin/python -m pip install dist/evlet-*.whl
/tmp/evlet-wheel-check/bin/python -I scripts/check_package.py
/tmp/evlet-wheel-check/bin/python -I scripts/smoke_cli.py
```

The Python suite exercises real SQLite files, validation, rollback, constraints,
concurrent short writes, and full ASGI requests. The frontend suite exercises the Evlet UI framework, its components and the
generated screens in jsdom. Tests substitute HTTP responses and missing
DOM layout APIs. The live smoke check covers the actual server and Vite proxy. Package checks
verify the installed wheel outside the source tree. GitHub Actions runs Python
tests on 3.12 and 3.13, distribution checks, frontend tests/build, and a live API
smoke check. Cloud validation does not establish that remote CI has run.

Build output is in `dist/` and `frontend/dist/`. The frontend build also installs
assets into `evlet/ui/static/` for the Python wheel. After building, the backend
serves the same UI directly at http://127.0.0.1:8000; installed applications need
only Python for ordinary development. `npm --prefix frontend run preview`
serves the production frontend on port 4173 with the same local backend proxy.
This is a development preview, not a deployment recipe.

## Current boundaries

- Create, read, update, delete, typed queries, and explicit additive/rename/index
  migrations are supported. Destructive migrations and arbitrary field-type
  conversions are not supported. Startup rejects schema drift.
- Local accounts and backend model/record/field permissions are implemented.
  The Playground uses them; a bare `Application(database)` stays unauthenticated
  for compatibility. Keep development servers on loopback. SSO and email recovery
  remain outside the implemented scope.
- Currency has two decimal places and no currency-code conversion. The Playground
  is a persistence example, not a ledger or payment processor.
- Email is a bounded text field; browser email validation is convenience only.
- Lists, counts, search, filters, and relationship choices use bounded server queries.
  Text search uses SQLite ASCII case folding. Browser integer actions require safe integers.
- Forms, lists, and details are generated from renderer-neutral Python metadata.
  The production bundle contains only Evlet's own UI framework and screens; it has
  no runtime dependencies. Python pages, native panels, reports and account
  management keep their APIs but are not rendered. No performance or scalability
  guarantees are claimed.

See [Evlet UI](docs/ui.md) for the browser routes, generated screens and the Python
page API, and [UI framework](docs/ui-framework.md) for the browser code.

Read [architecture decisions](docs/architecture.md), the [full specification](docs/internal/specification.md),
and [agent instructions](AGENTS.md). The [model engine guide](docs/model-engine.md)
covers the model engine. See [application generation](docs/application-generation.md)
for Milestone 3 metadata, defaults, generated screens, and upgrade instructions.
See [business capabilities](docs/business-capabilities.md) for Milestone 4 APIs,
account setup, permissions, actions, children, attachments, and limits.



## Release versions

Keep main on the next `.dev0` version between releases. To prepare a release,
choose the next unused PyPI version, update `pyproject.toml`, build the frontend,
then build and validate a wheel and source archive from that committed source.
Use an empty output directory so the wheel glob selects only the current build.
Run the installed-package and CLI HTTP checks and require green CI before upload.
After publishing and verifying the public file hashes and installation, immediately
advance main to the following `.dev0` version. Do not rebuild different source
under an already published version. The starter template's lower bound must name
the first published Evlet version that supports its APIs.
