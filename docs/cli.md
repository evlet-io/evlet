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

### Setup in one go

`setup` can also run the commands above for you:

```sh
evlet setup myproject --app notes,crm --instance acme --dev
evlet setup myproject --get https://github.com/co/notes.git,ssh://git@github.com/co/crm.git --instance acme --dev
evlet setup myproject --app notes --instance acme --host erp.example.com
```

The steps run in this order, each through the code of its own command:

1. Create the project, as `evlet setup` does.
2. `--app` creates new apps, as `evlet app NAME` does.
3. `--get` fetches apps from Git, as `evlet app --get URL` does: static validation
   first, then a trust question before any app code runs or is built.
4. `--instance` creates the instance, as `evlet instance NAME` does.
5. One `evlet install` installs every app with its dependencies in order. It asks
   once whether you trust them and writes one backup.
6. `--dev` starts `evlet dev` on 127.0.0.1, which prints a one-time sign-in link for
   the built-in Administrator. `--host` prepares a public site
   ([below](#serve-a-site-on-a-domain-in-one-go)). With neither, it stops after
   installing and prints the command that starts the site.

`--app` and `--get` take comma lists, can be repeated and can be combined. Git URLs
follow the [fetch rules](#dependencies-and-git): HTTPS or `ssh://`, no passwords;
write `ssh://git@github.com/co/crm.git`, not `git@github.com:co/crm.git`. `--trust`
answers the trust questions with yes for the whole run; review the apps first.
Without it each question is asked in the terminal, and with no terminal the run
stops. `--port` picks the port for `--dev` or the service (default 8000).

If a step fails, the message names it. Fix the cause and run the same command
again. Steps already done are skipped: an existing project, app or instance is
reused, apps already in `apps.lock` are not installed again, and an app fetched from
the same URL is not fetched again. Without any of these options, `evlet setup` works
exactly as before and refuses an existing folder.

## Commands

```sh
evlet setup myproject
evlet setup myproject --app notes,crm --instance acme --dev
evlet setup myproject --app notes --instance acme --host erp.example.com
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
evlet migrate acme --plan --trust
evlet migrate acme --trust --allow-destructive
evlet makemigration people --instance acme --name dependents --trust
evlet effects check acme
evlet ids check acme
evlet journal verify acme
evlet ledger list --instance acme
evlet jobs list --instance acme
evlet routes acme
evlet proxy acme --caddy --check
evlet link-user acme buyer customer 42
evlet controls grant acme mariam
evlet users reset-two-step acme mariam
evlet users set-password acme mariam
evlet secrets rotate acme
evlet playground --port 8000
evlet docs --port 8001
```

`python -m evlet` accepts the same commands. It drops the working directory from
`sys.path` before loading apps, but modules Python imports at startup can still be
shadowed by files there; run it with `python -P -m evlet` (or set
`PYTHONSAFEPATH=1`) when working inside an app folder. Project commands that
delegate to `.venv` already use `-P`. The original `new` command keeps its
existing contract. `evlet controls grant` makes a user an active admin from the server shell when nobody can open Controls ([controls.md](controls.md)). `evlet users set-password <instance> <username>` asks twice for a new password (no echo, never printed), applies the password minimum (config.py's `Security(password_min=...)` for admins, the Controls setting for everyone else) and the normal password rules, ends the user's sessions, AI assistants and API keys, and is audited as `cli`. `evlet users reset-two-step` clears a user's two-step sign-in so they set it up again ([settings.md](settings.md#set-up-at-sign-in-trust-on-first-use)). `evlet secrets rotate` re-encrypts connector secrets, mailbox sign-ins, two-step secrets and backup codes under a new `secret.key` ([controls.md](controls.md)). `login`, `logout`, `describe` and `mcp` are the connector
commands described in the [connector guide](connector.md). Each one shows its
options with `--help`, for example `evlet login --help`.
`evlet dev` selects the sole instance in a project. With several instances it
asks in a terminal; automation must pass `--instance`. Development rebuilds/reloads
Python, TS/JS and configuration changes, including file additions/deletions, and
runs the existing queue. It watches `apps/` and the instance folder; when Evlet itself
runs from a source checkout or an editable install (`pip install -e`), it watches the
Evlet package too, so framework edits reload the same way. It never applies model migrations
implicitly. Stop the server and run `migrate` after adding a migration.
`migrate --plan` writes nothing: it builds the apps against a temporary copy of the
instance database and prints the pending migrations, the pending Evlet framework migrations
([app-upgrades.md](app-upgrades.md#upgrade-evlet)), each model, field and property that
differs from the migrations, and for each app a suggested migration to review
([model-engine.md](model-engine.md#planning-a-migration)). `evlet makemigration <app>
--instance <instance>` writes that suggestion into a flat app's `migrations/` folder as
`m<number>_<name>.py` (`--name` sets the name, default `changes`). It refuses when nothing
changed and never overwrites a file. Review the file, fill in any `...` or `#` note, then run
`migrate`. Like `migrate`, both import the apps, so changed sources need `--trust`.
`migrate --allow-destructive` lets `DropField` delete the columns and values of retired
fields; without it such a migration is refused. The backup `migrate` writes before every change
holds the deleted values. `migrate --allow-maintenance` lets `RunData` steps declared with
`maintenance=True` write confirmed, tracked and posted records; each one is recorded in the Controls
audit log ([model-engine.md](model-engine.md#data-steps)).
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
The administrator account has no password yet. Open this link to sign in and choose one (works once, for 10 minutes):
  http://127.0.0.1:8000/_dev/signin?token=...
```

Every instance has the built-in System Administrator (`administrator`), with no password at
first ([controls.md](controls.md#the-administrator)). The first link signs it in and opens a
"Set your password" screen that follows config.py's admin minimum. When admins need two-step
sign-in (`Security(admin_two_step=True)`, the default), that session then ends and the
authenticator set-up follows, as at a normal sign-in; finishing it opens the app. Later starts
print `Sign in as administrator (works once, for 10 minutes): ...` and the link signs straight
in, sent to `/`. The sign-in page never offers a "Sign in as administrator" button: the link is
only printed in the terminal of the person who started the server.

`evlet dev` prints a new link each time it reloads. Run `evlet dev-link` from
another terminal for a fresh link when the first is used or expired. Inside a
project it serves the project's instance (`--instance` picks one, `--port` matches
`evlet dev --port`). Outside a project, or with `--playground`, it finds the running
`evlet playground` (`--port` picks one when several run).

- The link signs in the user named by `--user NAME`. Without it, the link uses
  the built-in Administrator.
- Nothing creates accounts any more: the instance has the Administrator from the start.
  Instances that already have other admins (such as `admin` or `demo-admin` from 0.4.1 and
  earlier) keep them unchanged.
- `--no-link` on `evlet dev` or `evlet playground` skips the startup link. The browser setup
  page then asks for the first admin; choosing the username `administrator` there gives the
  Administrator its password.
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
  instance's files can make a link, which is as strong as a second step. The first password
  is the exception above: the account then sets up two-step sign-in when its role needs it.
- Every account, tenant, row, field and action check still applies to the user.
- Passwords are never printed. The token is printed because it is single-use,
  local and short-lived.

`scripts/playground.sh` prints the same kind of link for the contributor Playground;
`bash scripts/playground.sh --link` prints a fresh one.

`ids check` reports model rows whose id is empty, zero or negative, which only raw SQL can write, and
model tables whose database does not yet refuse id changes (run `evlet migrate`). It reads without
writing, works before `evlet migrate`, and exits with 1 when there is any problem. It never rewrites an
id; see [model-engine.md](model-engine.md#only-evlet-makes-ids).

`journal verify` checks the hash chains of tamper-evident record journals in an
instance's database, read only and in one snapshot. It prints one line per break and
exits with 1 when there is any; see the [journal guide](journal.md).

`ledger list` prints each [ledger model](ledger.md) with its table, row count and its
`append_only`, `journal`, `timestamps` and `period` settings, read from one snapshot without writing.

`jobs list` prints the newest 100 background jobs with their state, attempts and the progress
they last committed, such as `40 of 100 (40%) Up to customer 40`. It runs while the instance
serves. Payloads, checkpoints and exception messages are not shown; see
[jobs-realtime.md](jobs-realtime.md#progress-and-long-jobs).

`playground` works outside a project, uses temporary demo data, and removes that
data on exit. It runs the existing framework examples with their generated screens.
It does not read `EVLET_DATABASE`, create an app or touch customer instances.
`docs` serves this installed version's guides locally without a login or database.
`effects check` recomputes the rows records post to other models and reports differences.
It reads one snapshot without writing or taking the write lock, so it may run beside a server;
it exits with status 1 when there is any difference. See [effects.md](effects.md).
`routes` loads the installed apps and the optional `routes.py` the same way `serve`
does, then prints the resolved hosts and paths, marking locked rows and where each
row came from. See [routing.md](routing.md).
`proxy` prints a Caddy (`--caddy`) or nginx (`--nginx`) configuration for the
instance hosts, refusing `/controls` and `/docs` on sub-hosts and framework `/_/`
paths other than the MCP, OAuth and mailbox sign-in routes. `--write PATH` writes
it to that file instead; nothing else is written and no service is reloaded.
`--check` reports where each host's A and AAAA records point. It warns when a host
points at a public address this server cannot see as its own (behind NAT), and exits
with status 1 only when a host has no record, or points only at private addresses while
this server has a public one. `--ip` names this server's public address.
`link-user` links an account to a record, such as a portal user to their customer,
for row scopes like `own_customer`, or a staff user to their employee record for a
team scope (`Hierarchy.team`). `--remove` removes the link. Both are written to the Controls
audit log as `cli`.

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

Models, reports, nested services and workspaces are discovered automatically. Permissions are
declared on resources, with default denial. See the
[declarative guide](amendment-v2.md) for the complete contract and optional
`reports/`, `services/` and `migrations/` directories. An optional `workspaces.py`
declares the app's sidebars ([ui.md](ui.md#workspaces)).

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
dependencies = ["evlet>=0.4.0,<0.5"]

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
from .actions import actions
from .permissions import rules
from .migrations import migrations

app = AppDefinition(
    models=models,
    actions=actions,
    permissions=rules,
    migrations=migrations,
)
```

The contract composes the existing APIs. It also accepts `children`, `attachments`,
`tasks` and `schedules`. Apps scaffolded before 0.4 pass `views=views` with an empty
`views`; that still works, but Python pages were removed in 0.4. App directories and
Python source stay private. Hooks, helpers and action handlers are trusted code.

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

Next to `instance`, config.py sets the sign-in rules. `evlet instance` writes them with their defaults and a
short comment:

```python
security = Security(password_min=15, admin_two_step=True, two_step=False, admin_sign_in=False)
```

- `password_min`: 1 to 64 characters (15). 1 means no rule: even "1" is a password.
- `admin_two_step`: admins, the built-in Administrator too, must use two-step sign-in (on).
- `two_step`: everyone else must use it too (off). Controls can turn it on or off.
- `admin_sign_in`: the sign-in page opens with the username `administrator` filled in (off).
- `session_hours` 1 to 168 (12) and `lockout` 3 to 50 failed sign-ins (5).

Admins always follow config.py. Controls can change the password minimum and two-step sign-in
for everyone else in either direction, and can only shorten sessions and the lockout
([controls.md](controls.md#security-rules)). A `config.py` from 0.4.1 or earlier without
`security` keeps the defaults.

`database` and `storage` are reserved and optional. They default to `"sqlite"` and
`"local"`, the only values accepted today. New `config.py` files leave them out.
Older files that set them to those values keep working.

The CLI supports SQLite/local storage. Existing PostgreSQL, tenant routing, S3 and
other framework APIs remain available through explicit Python configuration.
PostgreSQL instance creation is intentionally rejected until its isolation strategy
is specified. Do not point the new installer at an existing unmanaged database.
It will reject it rather than adopt it. Existing Playground data stays in place.

`evlet serve` never prints sign-in links. While the built-in Administrator has no password
it prints `The built-in Administrator has no password yet. Set one with: evlet users set-password
acme administrator` at start.

For a conventional deployment, provision the administrator locally first, then
set `domain="app.example.com"`. This selects that exact HTTPS origin, secure
cookies and disables browser administrator setup. Terminate TLS with a reverse
proxy and manage the process through normal infrastructure. A non-loopback bind
requires a configured domain. The CLI does not configure DNS, TLS, system services,
proxy trust or public deployment. This is not a production-readiness claim.

### Serve a site on a domain in one go

```sh
evlet setup myproject --app notes --instance acme --host erp.example.com
```

Run it on the Linux server as the user that will run the service, not as root.
After installing the apps it:

1. Asks in the terminal for a password for the built-in Administrator (twice, not
   shown). A site on a domain cannot set its first password in the browser, so this
   happens here, with the admin password rule from config.py. It is audited as `cli` and
   skipped when an account can already sign in.
2. Sets `domain="erp.example.com"` and `trusted_proxies=("127.0.0.1", "::1")` in
   `instances/acme/config.py`. If you edited that file, it stops and asks you to set
   the domain yourself.
3. Writes `deploy/evlet-acme.caddy` (the `evlet proxy acme --caddy` output) and
   `deploy/evlet-acme.service`, a systemd unit like the one below that runs
   `evlet serve acme --jobs` on 127.0.0.1. Caddy is the only public listener.
4. Checks DNS like `evlet proxy acme --check`. `--ip` is never required. A host that
   resolves to an address of this server passes. Behind NAT, as on most cloud servers,
   the server sees only a private address, so a host that points at a public address
   gets one warning line, such as `erp.example.com points at 84.12.169.35. If that is
   not this server, fix its A record. Ports 80 and 443 must be open.`, and setup goes on;
   Caddy keeps retrying the certificate. It stops only when the host has no A or AAAA
   record, or points only at private addresses while this server has a public one.
   `--ip` names this server's public address when you want an exact match. Once a
   check passes with it, the address is kept in `deploy/server.json` and used by later
   runs; a new `--ip` replaces it.
5. Prints the privileged steps: copy the two files into `/etc/systemd/system/`
   and `/etc/caddy/`, add an `import` line to `/etc/caddy/Caddyfile`, then
   `systemctl daemon-reload`, `systemctl enable --now evlet-acme` and
   `systemctl reload caddy`, each with `sudo`.

Review the two files, then run those steps yourself, or run the same command again
with `--apply`. Every run writes both files again from the current Evlet. When the copy
in `/etc/caddy/` or `/etc/systemd/system/` differs, it says the copy is out of date, and
`--apply` installs the new one and reloads Caddy. When the unit changed, it also restarts
the service. After Caddy reloads, `--apply` asks
`https://erp.example.com/.well-known/oauth-protected-resource` a few times over about half
a minute and prints whether the site answers. This is information only: while Caddy is
still getting the certificate it says so and the command still succeeds. `--apply` shows each step before running it, and `sudo` asks for
your password; nothing runs with `sudo` without it. Caddy must already be installed;
Evlet does not install it. `--dev` and `--host` cannot be combined: development
servers and sign-in links stay on loopback. This prepares a single-server site; it
does not make Evlet production ready.

The Caddy site refuses `/_/*` from outside except the routes MCP clients and mailbox
sign-in need: `/_/mcp`, `/_/oauth/authorize`, `/_/oauth/token`, `/_/oauth/register`,
`/_/oauth/revoke`, `/_/mail/callback` and `/_/mail/continue.js`
([routing.md](routing.md#evlet-proxy)). The site file written by 0.4.0 refused all of
`/_/*`, so remote MCP got 404. To upgrade such a site, as the service user:

```sh
sudo systemctl stop evlet-acme
myproject/.venv/bin/python -m pip install -U evlet
evlet migrate acme --project myproject
evlet setup myproject --app notes --instance acme --host erp.example.com --apply
```

The last step writes the new Caddy site, installs it, reloads Caddy and starts the
service again.

`evlet serve` logs one warning line at start-up while the installed
`/etc/caddy/evlet-acme.caddy` still blocks one of those routes. It only reads that file;
nothing outside `--apply` changes `/etc` or calls `sudo`. Controls, AI, MCP has a
**Check** button that tests the public address from the server.

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
    @internal {
        path /_/*
        not path /_/mcp /_/oauth/authorize /_/oauth/token /_/oauth/register /_/oauth/revoke /_/mail/callback /_/mail/continue.js
    }
    respond @internal 404
    reverse_proxy 127.0.0.1:8000
}
```

`evlet proxy acme --caddy` prints this block for your hosts.

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
