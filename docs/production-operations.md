# Deployment and recovery

This is an operating guide, not a production-readiness certification. Use the
[tenant deployment guide](tenancy-deployment.md) for the existing Caddy/systemd
rendering and [database adapters](database-adapters.md) for how another database
would be added. Public
DNS, certificate issuance, Linux service behavior, provider limits, and target
hardware still need verification on the actual deployment.

## Release preparation

1. Pin the application release, Python dependencies, frontend lockfile, and optional
   storage dependencies. Run the backend tests, frontend tests/build, and
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

### Service account

`evlet setup --host` and `evlet host add` write `User=` into the unit. By default that is the
login account running the command, as in every earlier release, and it keeps the account an
earlier `--apply` installed. When that account is root or in the `sudo`, `wheel` or `admin`
group, the command prints a note: a code-execution bug in an app then runs as an account that
can become root and that owns the code Evlet runs.

To run the service as a dedicated account instead, set `EVLET_SERVICE_USER` for the command.
It refuses root and members of those groups. The account must exist, read the project
(`.venv/`, `apps/`, `instances/<name>/config.py` and `routes.py`) and own only what the server
writes. For an instance `acme` kept in `/srv/evlet`:

```bash
sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin evlet-acme
sudo systemctl stop evlet-acme
sudo chown -R evlet-acme: /srv/evlet/instances/acme/data /srv/evlet/instances/acme/files \
    /srv/evlet/instances/acme/secret.key /srv/evlet/instances/acme/.evlet.lock
EVLET_SERVICE_USER=evlet-acme evlet setup /srv/evlet --instance acme --app ... --host erp.acme.com --apply
```

The code, `.venv/` and `config.py` stay owned by the deploy account and read-only to the
service. A project in a home directory is usually not readable by another account; keep it in
`/srv` or similar. Commands that open the instance's data then run as the service account,
such as `sudo -u evlet-acme .venv/bin/evlet backup acme`. `evlet secrets rotate` writes a new
`secret.key` next to `config.py`, so stop the service and run it as the deploy account, then
chown the key again; or keep the key in `EVLET_SECRET_KEY` (below). Evlet does not yet create
the account or change ownership for you.

`--apply` installs the unit and Caddy files from the text it has just rendered, piped to
`sudo install`; it never copies the files in `deploy/`, which the service account might be able
to change. The copies in `deploy/` are for review, and the printed steps install them by hand.

### Secrets in the environment

The generated unit reads `/etc/evlet/<instance>.env` when it exists (`EnvironmentFile=-...`).
Put `EVLET_SECRET_KEY`, OIDC client secrets and similar values there, never in an
`Environment=` line: unit files are world-readable and `systemctl show` prints them.

```bash
sudo install -d -m 700 /etc/evlet
sudo install -m 600 /dev/null /etc/evlet/acme.env   # root-owned; systemd reads it before starting the service
sudoedit /etc/evlet/acme.env                         # EVLET_SECRET_KEY=...
sudo systemctl restart evlet-acme
```

### Application dependencies

`evlet app --get` and `evlet install` resolve an app's declared dependency ranges when they
install, from the indexes pip is configured with, and `apps.lock` pins app source digests but not
the third-party packages. Two servers can therefore end up with different versions. For a
reproducible, reviewable install, install the dependencies yourself from a hashed requirements
file before installing the app (`.venv/bin/python -m pip install --require-hashes -r requirements.lock`,
made with a tool such as `pip-compile --generate-hashes`); pip then finds them satisfied. Evlet does
not yet read such a file itself.

### Audit log integrity

`evlet_audit` is append-only through SQLite triggers that refuse UPDATE and DELETE. That stops
framework and app code from changing history by mistake. It is not tamper evidence: anyone who
can write the database file (the service account, trusted app code, or `sqlite3` on the file)
can drop the triggers, edit rows and recreate them, and backups copy the edited table. Where
the audit must survive a compromised server, ship it off the host as it is written (journald or
syslog forwarding, or regular exports to storage the server cannot rewrite) and compare.

### Sign-in limits and password hashing

Each password hash (sign-in, password change, new account) takes about 0.3 s of CPU and
128 MiB of memory. A process hashes at most `EVLET_PASSWORD_SLOTS` passwords at once (1 to 8;
default two, or one on a single-CPU host). A request that finds every slot busy waits its turn
for up to 3 seconds; past that, or when the line is full, it gets 503 "Password service is
busy". A waiting sign-in holds a thread of the HTTP write lane, so the line is only as long as
leaves one write thread free for saves (`EVLET_HTTP_WRITE` minus the slots minus one; none on a
one- or two-CPU host by default). Raise `EVLET_HTTP_WRITE` to let more sign-ins wait their turn. On a 1 GB host with more than one Evlet
process, set `EVLET_PASSWORD_SLOTS=1`: each process then needs at most 128 MiB for hashing.

Every attempt is counted before any hashing, per 15 minutes:

- **Account from one network:** `Security(lockout=5)` failures, then 429 for 15 minutes.
- **Network:** 100 attempts, then 429 for everyone on it. A sign-in that finishes (with its
  two-step code, when it needs one) gives its attempt back, so an office behind one address is
  limited by failures only. An attempt refused with 503 because hashing is busy still costs
  the network one, so holding the slots is not free; it does not count against the account.
- **Account from every network:** after 50 failures the account is slowed, not locked. Networks
  it has not signed in from in the last 30 days get one attempt every 10 seconds between them
  (429 otherwise), which keeps spread-out guessing slow. People signing in from a network
  they used before are not held up. An attacker who keeps the account above 50 failures and
  takes each 10-second attempt can still keep it from signing in from a new network (a new
  device, a mobile network whose address changes) while the attack lasts; a network the person
  signed in from before still works.

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
Activity, jobs, accounts and settings), `sessions.sqlite3`, `logs.sqlite3` and `eva.sqlite3`.
They are one set: back them up, restore them and move them together. Writers in one process
take turns per file, requests before job steps, and a job step never waits long. A write that
holds a file longer than a second logs a warning naming the request or the job; watch for it
and split the job into shorter steps. After a crash `database.sqlite3` is atomic as before;
the other three are best effort for the last moment of writes, so a session started or a log
line written just before the crash may be missing. Separate processes still wait for each
other through SQLite. See [Busy writing on SQLite](performance.md#busy-writing-on-sqlite).

Adding API workers does not remove the one-writer-per-file limit. Avoid aggressive
retries when the database is busy. HTTP database
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
hold only database files, plus the instance's `sites/` folder when it has [sites](sites.md): keep
`secret.key`, `files/` and the app sources with them.

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

## Hosts without a restart

Sub-hosts in `routes.py` need a restart; hosts from data do not
([routing.md](routing.md#hosts-from-data)). `evlet host add NAME HOST --root APP.FRONTEND --live`
adds one as pending; it is served, and Caddy's on-demand TLS gets its certificate, once its DNS
check passes. Each request reads the `evlet_hosts_version` row with the Controls config version (one
statement), so every worker picks a change up on its next request. The `evlet_host_check` job checks
pending hosts every minute and needs the job queue (`evlet serve --jobs`, as the unit `evlet setup`
writes runs it); without it, run `evlet host check NAME`. A row the route table can't serve never
stops the instance from starting: it is skipped and listed under Needs attention. Back up and
restore `evlet_hosts` with the database like any framework table.

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

## Bounded HTTP work and correlation

Each process shares a small set of HTTP worker lanes across its applications and tenants.
A request **reads its body first** and only then takes a slot in its lane, so a slow client
holds no slot. When a lane is full the request waits in that lane's queue, first come first
served, for up to `EVLET_HTTP_QUEUE_SECONDS`; if no slot frees in time the API returns 503
with `Retry-After: 1`. A burst larger than a lane therefore queues briefly instead of being
refused. The queue itself is bounded: once `EVLET_HTTP_QUEUE` times the lane's limit are
waiting, a further request gets that 503 at once, so a surge is shed early instead of holding
every connection for the full queue time. A body whose next chunk does not arrive within `EVLET_HTTP_BODY_IDLE_SECONDS`
returns 408; there is no total body deadline, so a large upload over a slow but steady link
completes, and the route's size limit bounds what one request can send. Size/type/permission
checks still apply. Public sites keep their own not-found response rather than exposing
framework errors.

Set these environment variables before starting the process; restart to change them:

| Variable | Default | Work admitted |
|---|---:|---|
| `EVLET_HTTP_READ` |32| Total admitted reads: running, queued for a worker and sending responses |
| `EVLET_HTTP_READ_WORKERS` |8 (2 per CPU)| Concurrent ordinary GET/HEAD/OPTIONS and individual stream polls |
| `EVLET_HTTP_WRITE` |4 (CPUs + 1)| Mutating HTTP requests, including sign-in and uploads, once their body has arrived |
| `EVLET_HTTP_PROBE` |2| Health checks, isolated from blocked writes |
| `EVLET_HTTP_HEAVY` |2 (1 per CPU)| Print routes, including PDF rendering |
| `EVLET_HTTP_STREAM` |64| Open event/Eva streams; sleeping streams hold no worker |
| `EVLET_HTTP_ROUTE` |8| Tenant-catalog lookups (one short catalog read per tenant request) |
| `EVLET_HTTP_INIT` |8 (2 per CPU)| Cold tenant initialization; cached tenants, and requests waiting for a tenant another request is building, bypass this lane |
| `EVLET_HTTP_QUEUE` |4| Waiting requests per lane, as a multiple of its limit (0 to 64; 0 refuses instead of queueing) |
| `EVLET_HTTP_QUEUE_SECONDS` |5| Longest wait for a lane slot before 503, greater than 0 and at most 60 |
| `EVLET_HTTP_BODY_IDLE_SECONDS` |30| Longest wait for the next body chunk before 408, greater than 0 and at most 3600 |

Lane limits accept integers 1–256; read workers cannot exceed read admission. Defaults in
brackets scale with the CPUs the process may use, up to the number shown: on one CPU extra
threads only contend for Python's interpreter lock, and a writer holding the database then
waits behind readers. Measured on one pinned CPU with 100 users and 20% writes, 8 read and 4
write threads saved in 1,529 ms (p50) while 2 and 2 saved in 13 ms with 11% more throughput
and less memory. The default read lane admits at most 32 requests with 8 workers on four or
more CPUs (at most 24 queued for a worker); reducing read admission also lowers the implicit worker limit. Other worker lanes admit only their
worker count; requests beyond a lane's limit wait in its queue. Threads start only when needed; limits are process-wide,
not multiplied by the tenant count. There are no extra services or runtime dependencies.
These are admission ceilings, not workload capacity promises. On a small host begin with
fewer read/write/print workers, then measure real requests and sign-in memory. Additional
server processes multiply these limits and memory budgets; SQLite still has one writer
per file. Bodies are read before admission, so the memory held by bodies in flight is bounded
by the route size limits times the open connections: the reverse proxy should separately bound
connections, header time, body size and slow clients (the generated nginx files set body limits).

A request retains its slot through response delivery. Cancelling its ASGI task does not
stop an already running Python thread or undo a committed operation: the underlying work
retains its slot until it actually finishes. Retrying a write must follow the API's revision
and idempotency rules. Shutdown rejects new work, ends streams and drains tracked synchronous
work before closing databases. A hung application callback can therefore delay shutdown;
use bounded I/O and an operator-defined graceful shutdown deadline.

Every application HTTP response carries a generated 32-character `X-Request-ID`. Slow-write
warnings include this ID in their existing request label, and application error logs attach
`request_id`. Set `EVLET_HTTP_LOG_REQUESTS=1` and configure the standard-library `evlet.http`
logger at INFO to receive completion records with `request_id`, `http_method`, `http_route`,
`http_status`, `duration_ms` and a fixed-size `http_active` lane snapshot. A normal logging
handler can forward those fields to the operator's metrics/tracing system. The default install
requires no collector or OpenTelemetry dependency. Route labels omit record IDs, arbitrary
paths and queries; bodies, cookies and tokens are never included. Keep request IDs as log
correlation values, not metric labels. Job age/retry state remains available through Controls;
this change does not claim distributed tracing of external jobs.

Generated nginx files use route-specific body ceilings shared with the HTTP implementation.
Image routes allow the largest supported encoded image envelope so changing the Controls
image limit needs no proxy edit; the application continues enforcing the configured limit.
Deployments using an older generated file must regenerate/review it and reload nginx.
