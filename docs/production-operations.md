# Deployment and recovery

This is an operating guide, not a production-readiness certification. Use the
[tenant deployment guide](tenancy-deployment.md) for the existing Caddy/systemd
rendering and [PostgreSQL guide](postgresql.md) for the optional adapter. Public
DNS, certificate issuance, Linux service behavior, provider limits, and target
hardware still need verification on the actual deployment.

## Release preparation

1. Pin the application release, Python dependencies, frontend lockfile, and optional
   driver/storage dependencies. Run the backend tests, frontend tests/build, and
   clean wheel installation check. Build frontend assets before maintenance.
2. Use a dedicated unprivileged service account. Keep configuration and secrets
   outside the public asset directory. Restrict application files, database files,
   backups, session files, and private attachment roots to the service/operator.
3. Configure exact owned origins. Disable browser administrator setup. Provision
   administrators through a trusted command. Use Secure, HttpOnly, host-only cookies
   over HTTPS. The supplied tenant deployment uses `__Host-evlet_session`.
4. Terminate TLS at the configured proxy, serve assets and API from the same origin,
   preserve Host, and keep the API listener on loopback/private networking. Do not
   select tenants from forwarding headers. Restrict direct API access. Apply proxy
   connection/body/time limits appropriate to 1 MiB attachment uploads and SSE.
5. Take a consistent backup, stop application writers and workers, run explicit
   migrations, and verify startup before routing traffic to the new release.
   Never allow old and new schema-dependent writers during the migration window.
   An Evlet upgrade is a migration too: an instance refuses to serve until
   `evlet migrate` has backed it up and applied the new release's framework
   migrations (see docs/app-upgrades.md). Applications built directly on
   `Application`, including tenant deployments, apply pending framework migrations
   when they start, so back up their databases before starting a new Evlet release.

Start with the documented single service process and bounded concurrency. Measure
password-hashing memory under expected sign-in traffic as well as ordinary request
load. Rate-limit sign-in at the trusted proxy and monitor rejected requests. Evlet
uses the direct client address for its own sign-in throttle; behind a proxy that
address may be shared. Do not blindly trust arbitrary forwarded IP headers.

## Restarting a service

On a server set up with `evlet setup --host --apply`, restart an instance with
`evlet restart <instance>` rather than `sudo systemctl restart` directly. It first checks
that the instance loads, its apps match `apps.lock` and import, its routes resolve, no
framework or app migration is pending, and its systemd unit runs `evlet serve` for that
instance. Any failure stops it before the service is touched, so the running site stays up.
Pending migrations are refused with `Run evlet migrate <instance> first`; the command never
migrates. After the checks it shows and runs `sudo systemctl restart evlet-<instance>`,
waits up to 30 seconds for `/health`, and prints the last 20 journal lines when the site
does not come back. `evlet restart <instance> --check` runs the checks alone, which is a
useful step before planned maintenance. Scripts pass `--yes`. See
[cli.md](cli.md#restart-a-site-safely).

A restart still drops requests in flight for the moment the process is down.

## Upgrading Evlet

`evlet upgrade <instance>` installs a newer Evlet in the project's `.venv` with pip, prints the
migration plan, then after one question backs up the instance, stops its service, migrates it
and runs `evlet restart --yes`. Each step after pip runs as a new `.venv/bin/python -P -m evlet`
process, so no step mixes old and new code. On 0.x it stays within the current minor (0.4.\*),
from 1.0 within the current major; `--to VERSION` picks another version after you read its
release notes. The site is down from the stop until the restart answers. When a step fails it
prints the rollback commands: stop, pip back to the old version, `evlet restore` the backup,
start. See [cli.md](cli.md#upgrade-evlet-on-a-server).

## Database choices

SQLite uses a local durable filesystem, WAL, foreign keys, and short serialized
writes. Avoid network filesystems and independent replicas of a live file. Include
its WAL state through a consistent backup rather than copying only the main file.
There is no automatic failover.

From 0.4.3 an instance keeps four SQLite files in `data/`: `database.sqlite3` (records, audit,
journal, jobs, accounts and settings), `sessions.sqlite3`, `logs.sqlite3` and `eva.sqlite3`.
They are one set: back them up, restore them and move them together. Writers in one process
take turns per file, requests before job steps, and a job step never waits long. A write that
holds a file longer than a second logs a warning naming the request or the job; watch for it
and split the job into shorter steps. After a crash `database.sqlite3` is atomic as before;
the other three are best effort for the last moment of writes, so a session started or a log
line written just before the crash may be missing. Separate processes still wait for each
other through SQLite. See [Busy writing on SQLite](performance.md#busy-writing-on-sqlite).

PostgreSQL requires a dedicated UTF-8 database and role per application. Give the
runtime role only the privileges it needs, with no superuser or CREATEDB permission.
Use a separate migration operator where practical. Configure certificate-verified
TLS for remote connections, for example `sslmode=verify-full` and an appropriate
CA source. Restrict database ingress and configure backup retention, WAL archiving,
capacity alerts, and recovery objectives with the database operator. The supplied
adapter still serializes framework writes per database. Adding API workers will
not remove that limit.

Set explicit service connection limits below PostgreSQL's connection budget.
Account for application processes, job workers, migrations, monitoring, and admin
access. Avoid aggressive retries during lock/connection failures. HTTP database
failures return a generic 503; inspect protected server diagnostics without exposing
connection strings, record values, or credentials to users.

## Backup and restore

Define and test an acceptable recovery point and recovery time before launch.
Encrypt backups, limit access, retain multiple generations, and keep a copy outside
the application host. Retention alone is insufficient without restore tests.

For SQLite, `evlet backup --instance NAME` copies the four database files as one set into
`data/backups/` (or `--to DIR`) with SQLite's online backup API, from one read snapshot, so a
running server may keep working. `evlet restore BACKUP --instance NAME` puts a set back with the
server stopped, after keeping the files it replaces; it also restores a single-file backup from
before 0.4.3, which the next `evlet migrate` splits ([cli.md](cli.md#back-up-and-restore)). Backups
hold only database files: keep `secret.key`, `files/` and the app sources with them.

For PostgreSQL, use a supported `pg_dump`/`pg_restore` pair
for logical backups, or the provider's tested physical/PITR service. Do not use a
client older than the server for logical dumps. Avoid secrets in command arguments;
use a protected service configuration or password file.

Database attachment bytes are transactional with their metadata. Local/S3 file
attachments require a coordinated backup of database metadata, namespace markers,
and all referenced bytes. Stop uploads and orphan collection while making a
consistent filesystem copy; preserve object versions/retention for remote bytes.
Restoring database metadata alone can leave missing or mismatched attachments.

Restore first into an isolated database and private storage namespace with workers,
schedules, outgoing integrations, and public routing disabled. Verify:

- Schema and migration history match the release.
- Representative records, exact numeric totals, and links are intact.
- Authentication, permissions, private attachments, and tenant boundaries behave as expected.
- Pending/running jobs and missed schedules are reviewed before enabling workers.
- Restored session tokens are revoked when the incident/recovery policy requires it.

Only route traffic after the restored application passes health and authenticated
smoke checks. Preserve the failed state for investigation. A code rollback is safe
only when its models accept the current schema; otherwise restore the matching
backup in isolation and follow the same cutover checks.

## Monitoring and capacity

Monitor request/error latency, 401/403/429/503 rates, CPU, memory, open connections,
lock waits, queue age, failed jobs, disk/WAL growth, backup age, and restore results.
Protect logs and avoid request bodies, cookies, authorization headers, passwords,
and connection strings. `/health` is a database probe, not a full business or
recovery check. Add external authenticated checks for critical workflows.

Some limits are counted per process, not per instance: API key requests (300 a minute per
key), invalid API keys (20 a minute per network and prefix, 100 per network), connection
tests (two at a time, 10 a minute per person) and the MCP connector's tool calls. With several
worker processes behind a proxy, each process has its own count, so the effective limits
are that many times higher. Put a rate limit in the proxy when that matters.

Keep `instances/<name>/secret.key` out of version control and out of copies of the database.
A backup needs both: the database for the encrypted connector secrets and the key to read
them. Store the key backup separately from the database backup.

Reproduce the [benchmark](performance.md) on deployment hardware before sizing it.
Use indexes justified by actual queries and inspect plans after realistic data
loading. Test the sustained concurrent request mix and reports together with
workers, file storage, proxy/TLS, sign-ins, and failure recovery. The local report
has no public-network latency, failover, or long-duration load evidence. Its numbers
are not service-level guarantees or evidence for the specification's larger tenant
and employee-count targets.
