# Tenants and single-server deployment

Milestone 5 adds a trusted tenant catalog, hostname routing, separate SQLite
files, private filesystem attachments, DNS ownership verification, and a reference
Caddy/systemd deployment. The existing single-database Playground stays available.
This is an implementation for review, not a claim of production readiness.

## Boundaries

`TenantRegistry` stores tenant names, enabled state, and domain assignments in
`registry.sqlite3`. Each tenant uses `tenants/<slug>/application.sqlite3` and
`tenants/<slug>/files/`. Its accounts, sessions, business records, migration history,
and attachment metadata are in its own database. Tenant IDs and filenames never
come from a client-supplied filesystem path.

`TenantApplication(catalog, factory)` resolves exactly one incoming `Host` header
before creating or selecting an application. It ignores `X-Forwarded-Host` and
client tenant headers. Unknown, malformed, disabled, or unverified hosts return
421, including on `/health`. Missing databases return 503 and are not recreated.
Catalog changes apply on subsequent requests without a process restart. Requests
already admitted can finish. The router does not use a mutable global tenant.

The factory receives an immutable `Tenant` with its paths, hosts, and exact origins.
It must return an authenticated `Application` using `Database(..., must_exist=True)`
and tenant-bound `LocalFileStorage(tenant.files_path)` or `S3Storage`. Configured
job queues must use that tenant slug. Production requires HTTPS origins,
Secure `__Host-evlet_session` cookies, and disabled browser setup. Development uses
explicit ports and `.localhost` names. Accounts are provisioned through trusted
Python or the management command. Each hostname has its own browser cookie, even
when several verified aliases belong to the same tenant.

The authenticated schema and session response include only a public tenant slug
and label. The frontend shows the label before and after sign-in. Tenant catalog
management has no HTTP endpoint. Python handlers, the operator account, and code
with direct filesystem/SQL access remain trusted. This is application isolation
within one service account, not OS isolation from malicious installed code.

## Try two tenants locally

Use a new state directory. These commands do not import or alter the existing
`examples/playground/.data/playground.sqlite3` database. Run from the repository
with the installed development dependencies. Passwords are prompted without echo.

```bash
.venv/bin/python -m examples.playground.manage --root examples/playground/.data/tenants init --development
.venv/bin/python -m examples.playground.manage --root examples/playground/.data/tenants create alpha 'Alpha company' alpha.localhost
.venv/bin/python -m examples.playground.manage --root examples/playground/.data/tenants provision alpha --username owner
.venv/bin/python -m examples.playground.manage --root examples/playground/.data/tenants create beta 'Beta company' beta.localhost
.venv/bin/python -m examples.playground.manage --root examples/playground/.data/tenants provision beta --username owner
EVLET_TENANT_ROOT=examples/playground/.data/tenants \
EVLET_APPLICATION=examples.playground.tenants:create_app \
EVLET_PLAYGROUND_HOST=alpha.localhost bash scripts/playground.sh
```

Open `http://alpha.localhost:5173` and `http://beta.localhost:5173` in Chrome.
Both servers bind to loopback. Vite explicitly preserves the incoming Host.
The launcher refuses occupied ports and never stops an unrelated service.

To run beside the ordinary Playground, initialize the development catalog with
`--ports 8010,5174`, then pass `EVLET_BACKEND_PORT=8010` and
`EVLET_FRONTEND_PORT=5174` to the launcher. These ports are an allowlist stored in
that catalog, not a wildcard origin policy. The logs are under
`examples/playground/.data/logs/<frontend-port>/`.

A tenant is registered disabled. `provision` creates its database, storage, and
first administrator before enabling it. A failed provisioning attempt can be
retried while it has no accounts. If provisioning succeeded but activation was
interrupted, `enable <slug>` checks the schema and accounts before enabling it.
There is no default password and no public first-administrator setup route.

## Managed and custom domains

For a production catalog, initialize with an operator-owned base domain instead
of `--development`:

```bash
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet init --base-domain apps.example.com
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet create acme 'Acme' acme.apps.example.com
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet provision acme --username owner
```

Run management commands as the `evlet` service user on the server. Replace the
example domain with one the operator controls. Managed names must be within an
explicitly configured base domain. The operator's declaration of base-domain
ownership is trusted. Provisioning does not change DNS, start a service, or obtain
a certificate.

A customer's independent hostname requires a fresh DNS TXT challenge:

```bash
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet add-domain acme portal.customer.example
```

The command prints a TXT name `_evlet-challenge.portal.customer.example` and a
random value. The domain owner publishes that exact value, then the operator runs:

```bash
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet verify-domain portal.customer.example
```

Verification uses `dig` over TCP with a bounded timeout and the server's configured
DNS resolver. Install the OS DNS utilities package (`dnsutils` on Debian/Ubuntu).
An exact TXT value is required. Challenges expire after 24 hours, are single-use,
and are checked again transactionally after the lookup. `challenge <hostname>`
issues a new token and suspends routing until it is verified. Tests inject a
resolver; the management command always performs the real lookup.

Hostnames have one catalog owner. They cannot be silently reassigned. Verification
is proof at onboarding, not continuous ownership monitoring. Disable a hostname
when its ownership or service agreement ends. Point its A/AAAA records to the
server as a separate DNS step. TXT proof and TLS certificate issuance are separate
requirements; neither implies the other succeeded.

## Server installation and configuration review

The reference deployment uses one Linux server with Python 3.12+, SQLite on a
local disk, Uvicorn, Caddy, and systemd. It does not require Docker or Redis.
The generated files use these fixed locations:

- `/opt/evlet`: a reviewed checkout and its `.venv`.
- `/opt/evlet/frontend/dist`: the built frontend, readable by Caddy.
- `/var/lib/evlet`: the private catalog, tenant databases, and files, owned by
  `evlet:evlet` with directory mode 0700.
- `/etc/caddy/Caddyfile` and `/etc/systemd/system/evlet.service`: reviewed service
  configuration owned by the operator.

Prepare a release in the checkout. The runtime lock contains only Uvicorn and its
runtime dependencies, using the same pins as the tested development lock:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-server.lock
npm --prefix frontend ci
npm --prefix frontend run build
```

The reference service runs the example application from the checkout. A packaged
consumer can supply its own ASGI factory. The framework wheel intentionally does
not install the Playground as a production application.

Create the non-login `evlet` service user and private state directory through the
server's normal administration process. Provision the catalog and administrator
as that user. Keep the application source and static assets read-only to the
service; only `/var/lib/evlet` is writable. Copy a locally prepared catalog only
while stopped and with all its tenant directories and file-store marker files.

Render into a new review directory:

```bash
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet render --output /tmp/evlet-release-config
caddy adapt --config /tmp/evlet-release-config/Caddyfile --adapter caddyfile
systemd-analyze verify /tmp/evlet-release-config/evlet.service
```

Rendering includes only enabled tenants and verified enabled hostnames. It never
runs systemctl, reloads Caddy, or changes DNS. The files always target the fixed
server paths above, even when rendered from a local staging catalog. Review the
paths, host list, release contents, DNS, firewall, backup, and restore procedure
before applying them through the server's deployment process.

The renderer (`evlet.deployment`) is written for the example tenant app in this
repository. Its units run `examples.playground.tenants:create_app` and
`examples.playground.worker` from a source checkout in `/opt/evlet`. If you
installed Evlet as a package, these modules are not there. Change each
`ExecStart` command to start your own tenant factory and worker, and adjust the
paths. For a project made with `evlet setup`, see
[Serve an instance behind Caddy](cli.md#serve-an-instance-behind-caddy) instead.

Caddy serves only the built frontend and proxies `/api`, `/api/*`, and `/health`
to loopback Uvicorn. The attachment directory is never a static root. The service
uses one worker, a private umask, restricted filesystem writes, and restart on
failure. Uvicorn trusts proxy information only from loopback Caddy. No other
reverse proxy or CDN is included in this configuration. Changing that boundary
requires a separate review of client-address forwarding and login throttling.

Caddy preserves Host for this HTTP upstream and sets trusted forwarding headers.
Its explicit site names enable automatic certificate issuance, renewal, and HTTP
to HTTPS redirects. Public DNS must point to this server, ports 80/443 must be
reachable, and Caddy's own certificate storage must persist. There is no wildcard
or on-demand certificate endpoint. Consult the official
[proxy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
and [HTTPS requirements](https://caddyserver.com/docs/automatic-https).

Adding or disabling a domain requires rendering, reviewing, and applying an
updated Caddyfile. The backend rejects a disabled host immediately on subsequent
requests, even while an old Caddy configuration still serves its static frontend.
Disabling a tenant or domain does not delete its records or revoke existing
sessions; re-enabling restores access for sessions that have not expired. Use
account administration to revoke a particular user's sessions.

## Private file storage and recovery

`Application(..., storage=SQLiteStorage())` is the existing default. The new
`LocalFileStorage(root)` stores immutable bytes in a private directory and keeps
attachment IDs, names, ownership, generated keys, and lengths in SQLite. The
business endpoint performs the same record and role checks before accessing either
adapter. Existing BLOB attachments remain readable after selecting file storage.
There is no automatic BLOB export or reverse migration to SQLite storage.

Both adapters implement `initialize(database)`, `add(tx, model, record_id, name,
content)`, and `read(row)`. Metadata deletion and ownership checks remain in the
business layer. An adapter must participate in that metadata transaction. Storage
failures raise `StorageError` and become a generic HTTP 503 without filesystem paths.
The file adapter binds a private directory to one database using a random namespace
and `.evlet-store` marker. Never share a file directory between tenant databases.

Writes use generated keys, exclusive private files, no symbolic links, and fsync
before committing the metadata. Existing attachment size and count limits remain.
A failed transaction or crash can leave an unreferenced file, but cannot publish
it as a successful attachment. Record/file deletion removes metadata in the same
SQLite transaction; physical files remain until explicit collection:

```bash
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet collect-files acme
```

Collection holds the database write lock and removes only generated filenames
that have no metadata reference. It cannot collect a file awaiting upload commit.
It is an operator action, not a scheduled background job. Do not run it on backups
or a file directory paired with the wrong database.

For a consistent backup, stop the application and job-worker services, prevent management writes,
and copy the complete private state directory, including the catalog, SQLite
files and sidecars, file directories, and `.evlet-store` markers. Keep that backup
private. Restart the service only after the copy completes. Back up Caddy's TLS
state separately using its documented service data location. A database-only backup
is insufficient for tenants using filesystem attachments.

Restore into a separate private directory first. With the service stopped, check
SQLite integrity and foreign keys for the catalog and every tenant database,
initialize each tenant application to validate schema/history and file binding,
and verify representative authenticated downloads before switching the service to
the restored state. Do not mix a database from one backup with files from another.
The existing explicit Playground migration command can be used per tenant by
setting `EVLET_DATABASE` to its database path; stop tenant service access, take a
complete backup first, migrate each database, validate it, then restart. Startup
does not silently apply application migrations.

## Verification and remaining work

```bash
.venv/bin/python -m pytest
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python -m build --no-isolation
.venv/bin/python scripts/smoke_tenants.py
.venv/bin/python scripts/smoke_tenants.py --frontend
.venv/bin/python scripts/smoke_tenants.py --caddy
```

The smoke command creates and removes its own databases, accounts, files, and
loopback server processes. The frontend option requires installed npm dependencies.
The Caddy option requires Caddy and a frontend build. It uses an isolated local CA
with hostname verification in the test client, does not install system trust, and
does not contact a public certificate authority. It verifies sessions, same-ID
record isolation, children, actions, files, replay rejection, restart, and disable.

Public DNS proof, public ACME issuance/renewal, and systemd service operation still
need validation on the chosen Linux server and owned domains. Local testing cannot
establish those facts. See [Milestone 6](jobs-realtime.md) for the worker service,
job and event boundaries, optional object storage, and Lambda HTTP transport.
Broader production hardening remains Milestone 8.
