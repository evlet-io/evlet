# Install Evlet once, create apps, create instances

The installed Python package contains the CLI, browser UI, Playground, templates
and documentation. Python 3.12 or later is required. Ordinary app development and
serving of Python-only apps need no Node.js, Vite, separate frontend server or
framework checkout. Native app extensions require Node 24 at build time.

For a step-by-step walkthrough, read [Your first site](getting-started.md).
See [What you can customize](customizing.md) and [App upgrades](app-upgrades.md) next.

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

`setup` creates `apps/`, `instances/` and a real isolated `.venv`, installs Evlet
there and verifies it. It does not create a database or start a service. Use
`--framework /reviewed/evlet.whl` for an offline/development artifact. Existing directories
are never overwritten. Commands discover the project from the current directory
or its parents. `--project /path/to/myproject` works before or after a command.

## Commands

```sh
evlet setup myproject
evlet app notes
evlet app --get https://github.com/company/notes.git
evlet app --list
evlet build --trust
evlet instance acme
evlet instance --list
evlet install notes --instance acme --trust
evlet serve acme --port 8000
evlet dev --instance acme
evlet dev-link
evlet migrate acme --trust
evlet effects check acme
evlet journal verify acme
evlet routes acme
evlet proxy acme --caddy --check
evlet link-user acme buyer customer 42
evlet controls grant acme mariam
evlet users reset-two-step acme mariam
evlet secrets rotate acme
evlet playground --port 8000
evlet docs --port 8001
```

`python -m evlet` accepts the same commands. It drops the working directory from
`sys.path` before loading apps, but modules Python imports at startup can still be
shadowed by files there; run it with `python -P -m evlet` (or set
`PYTHONSAFEPATH=1`) when working inside an app folder. Project commands that
delegate to `.venv` already use `-P`. The original `new` command keeps its
existing contract. `evlet controls grant` makes a user an active admin from the server shell when nobody can open Controls ([controls.md](controls.md)). `evlet users reset-two-step` clears a user's two-step sign-in so they set it up again ([settings.md](settings.md#set-up-at-sign-in-trust-on-first-use)). `evlet secrets rotate` re-encrypts connector secrets, mailbox sign-ins, two-step secrets and backup codes under a new `secret.key` ([controls.md](controls.md)). `login`, `logout`, `describe` and `mcp` are the connector
commands described in the [connector guide](connector.md). Each one shows its
options with `--help`, for example `evlet login --help`.
`evlet dev` selects the sole instance in a project. With several instances it
asks in a terminal; automation must pass `--instance`. Development rebuilds/reloads
Python, TS/JS and configuration changes, including file additions/deletions, and
runs the existing queue. It never applies model migrations
implicitly. Stop the server and run `migrate` after adding a migration.
`migrate --recompute-posted` lets the `Recompute` operations of that run recalculate posted
records too; it prints how many changed, and each one gets a journal entry
([model-engine.md](model-engine.md#recalculating-stored-values)). Without the flag, posted
records keep their saved results. Normal
`serve` checks locked source versions and content; run `migrate` to record an
intentional source change. Use `serve --jobs` to enable the short-task queue in
that process. No worker is mandatory.

### Development sign-in links

`evlet dev` and `evlet playground` print a one-time sign-in link at startup, like
`bench browse --user` in Frappe:

```text
Sign in as mariam (works once, for 10 minutes):
  http://127.0.0.1:8000/_dev/signin?token=...
```

Open the link and you are signed in, then sent to `/`. `evlet dev` prints a new
link each time it reloads. Run `evlet dev-link` from
another terminal for a fresh link when the first is used or expired. Inside a
project it serves the project's instance (`--instance` picks one, `--port` matches
`evlet dev --port`). Outside a project, or with `--playground`, it finds the running
`evlet playground` (`--port` picks one when several run).

- The link signs in the user named by `--user NAME`. Without it, the link uses
  the only active admin, or the only active user. With several, pass `--user`.
- On a Playground with no accounts, the first link creates the administrator
  `demo-admin` with a random password that is never printed. `evlet dev` never
  creates accounts: create the first admin in the browser.
- `--no-link` on `evlet dev` or `evlet playground` skips the startup link.
- The app itself serves `/_dev/signin` on the same host and port. The route exists
  only under `evlet dev` and `evlet playground`. `evlet serve`, tenant applications
  and deployments answer 404.
- The server must be bound to loopback and the request must come from a loopback
  client. Host, origin and cross-site checks are the same as for every request.
- Only a SHA-256 hash of the token is stored, in `evlet_dev_links`. A token works
  once and expires after 10 minutes.
- Each use starts a normal session through the framework's session code, with the
  usual cookie (HttpOnly, SameSite=Strict), CSRF token and session length. The
  audit log records it as "Signed in with a development link".
- The session counts as a finished two-step sign-in. Only someone with this
  instance's files can make a link, which is as strong as a second step.
- Every account, tenant, row, field and action check still applies to the user.
- Passwords are never printed. The token is printed because it is single-use,
  local and short-lived.

`scripts/playground.sh` prints the same kind of link for the contributor Playground;
`bash scripts/playground.sh --link` prints a fresh one.

`journal verify` checks the hash chains of tamper-evident record journals in an
instance's database, read only and in one snapshot. It prints one line per break and
exits with 1 when there is any; see the [journal guide](journal.md).

`playground` works outside a project, uses temporary demo data, and removes that
data on exit. It runs the existing framework examples with their generated screens.
It does not read `EVLET_DATABASE`, create an app or touch customer instances.
Its Python pages (the Projects demo and the Layouts page) are registered in `/api/ui`
but not rendered by the browser interface.
`docs` serves this installed version's guides locally without a login or database.
`effects check` recomputes the rows records post to other models and reports differences.
It reads one snapshot without writing or taking the write lock, so it may run beside a server;
it exits with status 1 when there is any difference. See [effects.md](effects.md).
`routes` loads the installed apps and the optional `routes.py` the same way `serve`
does, then prints the resolved hosts and paths, marking locked rows and where each
row came from. See [routing.md](routing.md).
`proxy` prints a Caddy (`--caddy`) or nginx (`--nginx`) configuration for the
instance hosts, refusing `/controls` and `/docs` on sub-hosts. `--write PATH` writes
it to that file instead; nothing else is written and no service is reloaded.
`--check` reports whether each host's A and AAAA records point at this server and
exits with status 1 when one does not; pass `--ip` when the public address cannot
be detected.
`link-user` links an account to a record, such as a portal user to their customer,
for row scopes like `own_customer`. `--remove` removes the link.

## App package

The default scaffold is a single package root:

```text
apps/notes/
  __init__.py
  pyproject.toml
  setup.py
  models/__init__.py
  models/item.py
```

Models, reports and nested services are discovered automatically. Permissions are
declared on resources, with default denial. See the
[declarative guide](amendment-v2.md) for the complete contract and optional
`reports/`, `services/`, `migrations/` and `views/` directories.

The following nested layout remains available with `evlet app notes --legacy`:

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
dependencies = ["evlet>=0.2.0,<0.3"]

[tool.evlet]
app = true
title = "Notes"
```

Use a lowercase Python package name, at most 48 characters, with letters, digits
and underscores. Framework, standard-library and installed-library names are reserved. The current
legacy layout is a package directly below the app directory, with a static name/version
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
Registering a Python component contract alone does not install JavaScript. The
new `views/` native extension contract packages explicit TS/JS client modules
under generation-specific authenticated URLs; arbitrary app directories and
Python source remain private. Hooks, helpers and action handlers are trusted code.

Models, actions, routes, permission rules and task names must not conflict across
apps. The generated starter preserves the complete app name, including underscores,
in its example model prefix. Handwritten models must still have unique names.
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

The static fetch phase validates metadata, Python/Evlet compatibility, requirement
syntax, package paths and the resolved commit without executing app code. Sources
are staged and moved into `apps/` only after validation. HTTPS and `ssh://` URLs
are supported. Passwords must stay in your Git credential setup, outside URLs.
Credential helpers are not inherited by the isolated fetch command; private
repositories can use SSH with an existing agent. Local paths, submodules, direct
URL requirements and app-dependency extras are not supported in this first CLI.

After static fetch, `app --get` requires trust before preparing dependencies and
building immediately. Refusal leaves a clearly unbuilt source checkout. Build
and install resolve local apps in dependency order and prepare external Python
dependencies/editable app packages only in the managed project `.venv`.
Legacy projects retain explicit environment management. Circular or incompatible
dependencies fail. Merely passing static validation does not establish trust.

## Instance configuration and isolation

```text
instances/acme/
  config.py
  routes.py        (optional)
  apps.lock
  data/database.sqlite3
  data/backups/
  files/private/
  secret.key       (URL-safe base64 keys, mode 0600; created when a secret is first kept)
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

instance = Instance(name="acme", domain=None, timezone="UTC")
```

`routes.py` is optional trusted Python that moves app frontends, sets the primary
host and sub-hosts, and moves Docs or turns them on or off. It must be a regular file inside the instance folder,
not a symbolic link. Without it every frontend mounts at its default path and the
URLs are unchanged. Docs are on in development and off once a domain or primary host
is set. See [routing.md](routing.md).

Behind a reverse proxy, set `trusted_proxies=("127.0.0.1", "::1")` (addresses or
CIDR ranges) so client addresses come from `X-Forwarded-For`. It is empty by default.

Next to `instance`, config.py may set the sign-in rules the instance starts from:
`security = Security(session_hours=8, lockout=4, password_min=16)`. Session length is 1 to
168 hours (12 by default), the lockout 3 to 50 failed sign-ins (5) and the minimum password
length 15 to 64 (15). Controls can only make them stricter ([controls.md](controls.md#overrides)).

`database` and `storage` are reserved and optional. They default to `"sqlite"` and
`"local"`, the only values accepted today. New `config.py` files leave them out.
Older files that set them to those values keep working.

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

### Serve an instance behind Caddy

This is a minimal manual setup for one instance on one Linux server. It assumes
a project at `/srv/evlet` made with `evlet setup`, an installed instance `acme`,
and a system user `evlet` that owns the project. Create the administrator while
`domain` is still `None`, then set `domain="app.example.com"` in
`instances/acme/config.py`.

Keep the server on loopback. Caddy is the only public listener. Save this as
`/etc/systemd/system/evlet-acme.service`:

```ini
[Unit]
Description=Evlet instance acme
After=network.target

[Service]
User=evlet
Group=evlet
WorkingDirectory=/srv/evlet
ExecStart=/srv/evlet/.venv/bin/python -P -m evlet serve acme --jobs --project /srv/evlet --host 127.0.0.1 --port 8000
Restart=on-failure
UMask=0077

[Install]
WantedBy=multi-user.target
```

`--jobs` runs the short-task queue inside this one process. Run
only one process per instance. Add this site to `/etc/caddy/Caddyfile`:

```text
app.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy obtains the certificate and passes the original Host header, which must
match `domain`. Then run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now evlet-acme
sudo systemctl reload caddy
```

To apply app changes, stop the service, run `evlet migrate acme --trust` as the
`evlet` user, then start it again. The server does not read forwarded headers,
so every request appears to come from `127.0.0.1`. Login rate limits therefore
count all clients together. Back up `instances/acme/` while the service is
stopped. For several customers routed by Host, see
[Tenants and single-server deployment](tenancy-deployment.md).

## Migrations, lockfiles and recovery

Each app supplies its complete ordered history of existing `Migration` objects.
Use `CreateModel` for new app models and the existing explicit operations for
schema changes. Keep published migration schemas frozen. The generated initial
legacy migration contains a frozen copy of its starter model, separate from the live
model definition. The installer prefixes migration
names with the app identity and preserves the combined database's applied order.
A later release of one app can append migrations after another app's history.
Checksums reject changed, removed or reordered applied migrations.

A changed installation validates contributions, creates a SQLite backup, and runs the
migration batch against a staged copy, followed by any pending Evlet framework
migrations. It initializes the existing services and
registers schedules there. Only after validation does it publish the database,
record the installed manifest and atomically replace `apps.lock`. A failed staged
migration leaves the active database, lock and private files intact. Backups stay
under `data/backups/`; unchanged migrations validate without another backup; retain matching source revisions and private files as part
of your backup policy. SQL backups alone do not include uploaded file bytes, and a database
restored without its `secret.key` can't open two-step sign-in secrets; keep the key with it.

The lock records each app's version, source, full Git commit when available, and
SHA-256 of included source files. Its `evlet` entry records the Evlet version and
last framework migration; an instance with a different entry refuses to serve until
`evlet migrate` runs (see docs/app-upgrades.md). Flat apps include their package root; legacy apps
include `pyproject.toml` and the nested package. Legacy apps skip only
`__pycache__` and `.pyc` files, so existing locks stay valid. Flat apps also skip
dotfiles and dot-folders (such as `.env` and `.git`), `node_modules`, `*.egg-info`,
folders containing `pyvenv.cfg`, and root-level `build`, `dist` and `tests`.
Skipped files are neither hashed nor copied into a build, so they cannot be imported. The database stores the actual installed
manifest separately. Git revisions identify repository sources; the content hash
also detects uncommitted changes. Reproducing an uncommitted/local app requires
preserving those exact source files. The lock is not a source-code archive. Local sources use project-relative paths.
Older absolute local paths remain readable when they identify the same app package;
`migrate` converts them and the database manifest together. Trusted Python code can
still import installed dependencies and access its environment; the lock is not a sandbox.

Imports inside registered app packages compile their Python source, including helpers first
loaded by actions or jobs. Stale bytecode caches are ignored and sourceless `.pyc`
helpers are rejected. Unrelated Python dependencies keep their normal import
behavior.

Python dependency constraints are verified against the active environment;
retain that environment's dependency lock for reproducible deployment.

Lifecycle commands and CLI servers hold an instance file lock. Stop servers and
other Python writers before installing or migrating. Direct Python consumers
must cooperate with that operational rule; arbitrary external writers cannot be
made safe by a CLI file lock. Several CLI server processes may share a running
instance on POSIX and Windows; migration needs the exclusive lock.

An interrupted database/lock publication leaves a recovery journal. Serving
refuses that state. Run `evlet migrate acme --trust` after reviewing the current
sources. Recovery reconciles the lock only when the database matches one of the
recorded states, then retries normal validation. Unknown mismatches fail closed
and require restoring the matching database, files, lock and app source together.

There are no automatic app update/removal, source checkout, rollback or backup/
restore commands. Use [App upgrades](app-upgrades.md) for the supported workflow.
Do not remove app source used by an instance. Calendar `@cron` schedules reconcile
changes while preserving operator-disabled state and history. Changing a legacy
interval schedule's task, payload or interval needs a later explicit
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

Expected startup failures print a short message. Use `evlet serve acme --debug`
or `evlet dev --instance acme --debug` for a traceback. CLI shutdown and reload
close live-update streams before waiting for active requests.

Private Git repositories require an `ssh://` URL and configured SSH authentication.
Fetch disables global Git configuration, including HTTPS credential helpers, to
avoid running checkout hooks and filters.
