# Jobs, live updates, and optional cloud adapters

Milestone 6 adds a durable SQLite queue, interval schedules, record-change events,
SSE with polling fallback, an AWS Lambda HTTP transport, and private S3-compatible
attachments. The queue and event store use the existing database and standard
library. Boto3 is optional and is used only by the object-storage example.
Nothing in this milestone creates cloud resources or deploys the application.

## Try a background task

Start the ordinary Playground with `bash scripts/playground.sh`. The launcher now
starts the API, frontend, and a separate optional job worker. Open a Project, add
some completed and incomplete tasks, and choose **Recalculate progress**. The
action returns immediately with a queued job. The worker updates the percentage
from the saved tasks, and the page shows the job result without a page reload.
A project with no tasks gets zero progress.

`EVLET_RUN_JOBS=0 bash scripts/playground.sh` starts without the worker. Queued jobs
remain in SQLite until a worker starts. Control-C stops only the processes owned
by that launcher. An already-running backend must be restarted to load new code.
There is no application model migration in this milestone. Internal queue, event,
and storage metadata are added during initialization.

For a manually started API, run the worker separately with the same database
configuration:

```bash
.venv/bin/python -m examples.playground.worker
```

For tenants, set `EVLET_TENANT_ROOT` to the same catalog directory used by the API.
Each pass checks enabled tenants and uses their own databases, task registries,
accounts, and event stores. `--once` performs one scheduling and claim pass per
enabled tenant, then exits. It does not drain the entire queue. A disabled tenant
receives no new executions. A task already admitted may finish.

## Queue API and transaction boundaries

Register trusted handlers explicitly. There is no endpoint that accepts an
arbitrary Python function, module name, task payload, or schedule from a browser.
Actions remain the authorized entry point.

```python
from evlet import Application, EventStore, JobQueue, Task

# database, auth, policy, and Project belong to this application.
def recalculate(tx, context, payload):
    project = tx.get(Project, payload['project_id'])
    tx.update(Project, project.id, progress=100)

events = EventStore(database)
jobs = JobQueue(database, [Task('recalculate', recalculate)],
                tenant='acme', events=events, policy=policy)
app = Application(database, auth=auth, policy=policy, jobs=jobs, events=events)
app.initialize()

with database.transaction() as tx:
    receipt = jobs.enqueue(tx, 'recalculate', {'project_id': project_id},
                           user=user, target=(Project, project_id),
                           key='recalculate-request-123')

jobs.run_one()
```

The example handler is intentionally small; the Playground calculates its actual
child-task percentage. `enqueue` participates in the caller's transaction. A
rollback also removes the pending job. A registered `Action` may return the
`JobReceipt`; its HTTP response is 202 with `job` and the current `record`.
`GET /api/_jobs/<id>` returns status for the requesting account or an administrator,
subject to current read access to the target record. Payloads and exception
messages are never returned by this route.

Payloads must be JSON objects up to 16 KiB. `run_at` is an optional nonnegative UTC
Unix timestamp in whole seconds. An optional `key` of 1 to 200 characters prevents
repeated enqueue within that database. Reusing a key with another task, payload,
actor, or target fails. A completed job keeps its key. JSON key order is significant
for this comparison, so reuse the same payload representation when retrying a
request.

A claim commits a random lease token and attempt number before execution. The
handler then runs inside one SQLite write transaction. Its database writes,
success status, and target event commit together. Crashes release that transaction;
another worker can reclaim the job after its lease expires. A stale worker cannot
execute or finish a reclaimed token. `Task` defaults to 3 attempts and a 30-second
retry delay. Retry delay doubles per attempt, capped at one day. Permission,
validation, and missing-record failures are terminal. Other handler exceptions
retry until attempts are exhausted. Public status stores the exception class only;
private process logs contain the traceback.

For user-initiated jobs, execution reloads the account's current active state and
role and checks current update access to the target. `context.user` contains that
fresh user. The handler must check permissions for additional records or operations
it accesses. `user=None` creates a trusted system job. Trusted Python code remains
outside HTTP permission enforcement.

Handlers must be short and bounded. They hold the tenant database's SQLite write
lock for their whole execution. Do not use this queue for long network calls,
large exports, or distributed execution. Separate worker processes are safe to
claim jobs, but do not make writes to one SQLite database parallel. A default
claim lease is 300 seconds; no heartbeat, cancellation, priority, or dead-letter
service is included. Completed job history is retained; plan retention before
high-volume use.

External effects are at least once, not exactly once. Use
`context.idempotency_key` with an external system that supports deduplication. It
is stable across retries and includes a random database namespace. SQL rollback
cannot undo an email, HTTP request, or other external effect. Restoring a backup
also restores pending jobs and schedules, so review those before restarting workers.

## Schedules and operator commands

`jobs.schedule(name, task, payload, every=seconds, start_at=timestamp, user=user,
target=(Model, id))` creates an interval schedule. Intervals range from 60 seconds
to one year. `start_at` defaults to one interval from now. Names are unique and
use lowercase letters, digits, and underscores, beginning with a letter.
Calendar schedules pass `calendar=(expression, timezone)` so the schedule and its
calendar are written in one transaction; apps normally declare `CalendarSchedule`
instead of calling this directly.

The worker calls `tick()` before claiming work. Each tick processes at most 100 due
schedules. An occurrence and its next due time commit together. After downtime,
missed intervals coalesce into one job and the next time advances to the next
anchored interval. An unknown task disables its schedule and writes a private log
entry. `CalendarSchedule` (or `@cron`) takes a five-field numeric cron expression
(`*`, lists, ranges, and steps) evaluated in an IANA time zone, the instance zone by
default; nonexistent local times are skipped and ambiguous ones run once. Each
release reconciles declared calendar schedules: a changed expression, zone, or task
is applied, a removed schedule is disabled, and declaring it again re-enables it
with a fresh due time. There is no separate scheduler service. Disable a schedule
with `set_schedule_active(name, False)`; this does not cancel jobs already queued,
and reconciliation never re-enables a schedule an operator disabled.

For the tenant Playground, replace the paths, tenant, record, and account IDs with
values from that catalog:

```bash
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet jobs acme
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet schedule-progress acme daily_progress 1 --every 86400 --actor-id 1
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet disable-schedule acme daily_progress
.venv/bin/python -m examples.playground.manage --root /var/lib/evlet retry-job acme 7
```

Only failed jobs can be manually retried. Retrying resets the attempt count and
keeps the same external idempotency key. The account selected for a schedule is
checked by the worker at execution time, even if the CLI was run by an operator.

Deployment rendering now also writes `evlet-worker.service`. It shares the API's
service user, catalog path, private umask, and filesystem restrictions. Both units
optionally read `/etc/evlet/server.env`. Review and apply them through the existing
deployment process; rendering does not start them. Stop both API and worker before
backups, database upgrades, or restoring state. See the
[tenant deployment guide](tenancy-deployment.md) for the server boundary.

## Events, SSE, and polling

`EventStore(database, retention=1000)` stores bounded record invalidations in the
same transaction as a write. Retention can range from 100 to 100,000 events. HTTP
CRUD, parent/child saves, registered actions, and attachment changes publish the
affected parent record. A successful job publishes its declared target. Trusted
code writing directly through `Database` must call
`events.publish(tx, Model, id, deleted=False)` explicitly for other changes.

Authenticated routes are:

- `GET /api/_events`: establish a current cursor without replaying history.
- `GET /api/_events?cursor=<cursor>`: poll or replay a page.
- `GET /api/_events/stream?cursor=<cursor>`: stream named `evlet` SSE messages.

Each page contains `items`, `cursor`, `reset`, and `more`. An item contains only
`model`, `id`, and `deleted`. No record fields, actor IDs, or payloads are included.
Cursors are opaque random tokens. A stale or foreign cursor returns `reset: true`
with a current watermark; clients reload their authorized records. Cursor `0`
requests the retained beginning. Pages scan at most 100 events and advance even
when permissions filter all their records. Follow `more` to drain a backlog.

The stream honors `Last-Event-ID` on reconnect, sends a heartbeat page about once
per second, and closes after a bounded run of about 25 seconds. It rechecks the
session, account, tenant/host activation, and read permissions during delivery.
Revocation closes the stream with a named `unavailable` event. Browser clients
must still use authenticated reads to retrieve the latest values.

Current row scopes filter replay. Deleted rows cannot be checked against a current
scope, so deletion notifications are omitted for scoped rules. A record moved out
of a user's scope is also omitted. Such clients need an ordinary list refresh to
remove stale local rows. This event log is an invalidation hint, not an audit log
or a source of record contents.

The Playground establishes its cursor before its initial list read, reconnects
SSE automatically, and falls back to polling after stream failures. Polling is
roughly every three seconds when healthy. If the initial cursor request fails,
its first successful recovery refreshes the displayed records before continuing
from the new cursor. Live refresh updates the current list and open details while
preserving unsaved form values. Navigating away or signing
out closes subscriptions and pending status checks. Job-status reads retry network
failures, including interrupted response-body downloads after HTTP 200, as well
as HTTP 408/429 and server errors after three seconds. Permanent access or
missing-record errors stop tracking; a new record or job cancels pending retries.
WebSockets are deferred;
these updates only require server-to-browser delivery.

## Private S3-compatible attachments

`S3Storage(client, bucket=..., prefix=..., tenant=...)` implements the same private
attachment interface as local storage. The core accepts a configured SDK client
and does not import boto3. Install the optional Playground SDK with its tested
hash lock:

```bash
.venv/bin/python -m pip install --require-hashes -r requirements-s3.lock
```

The tenant factory enables this adapter when `EVLET_OBJECT_BUCKET` is set. Other
settings are `EVLET_OBJECT_PREFIX` (default `evlet`), `EVLET_OBJECT_REGION`
(default `us-east-1`), and optional `EVLET_OBJECT_ENDPOINT` (HTTPS only). Use the
SDK's normal credential provider chain. API, worker, and management processes must
use identical storage configuration. The ordinary single-database Playground
continues to use SQLite attachments.

For R2, supply the account's S3 endpoint and region `auto`. No real S3 or R2 account
has been exercised by this milestone. Check the provider's support for conditional
PUT, object metadata, GET, listing, and deletion before configuring an environment.
The local protocol tests use the real boto3 client and signed requests against a
disposable HTTP object server.

Objects live under `<prefix>/<tenant>/objects/<random-key>`. A conditional namespace
marker binds that prefix to one database. Keep the bucket private and credentials
restricted to the intended prefix and operations. Downloads continue through the
authenticated application; there are no public object URLs or presigned links.
Size limits and record permissions are unchanged. Reads check size and SHA-256
metadata; failures return a generic storage-unavailable response.

Upload bytes precede the metadata commit. Failed transactions can leave orphan
objects; deleting an attachment removes metadata first. The existing explicit
`collect-files` command removes only generated unreferenced keys under that bound
prefix while holding the same SQLite writer lock. No lifecycle deletion policy is
installed. Existing SQLite BLOBs remain readable. Existing local filesystem files
require their original adapter; selecting S3 does not migrate them. Changing an
already-bound bucket or prefix is rejected.

Back up metadata, the namespace marker, and referenced objects consistently while
writes and workers are stopped. Do not run collection against a prefix needed by
an older database backup. Do not attach two writable restored database copies to
the same object prefix. Object storage does not store the tenant database or make
SQLite suitable for a transient serverless filesystem.

## AWS Lambda HTTP transport

Status: experimental. The tests use synthetic gateway events only. There is no
end-to-end test against a real Lambda or API Gateway deployment. The live event
stream (SSE) returns 501 on this transport.

`LambdaHTTP(app, domains=('app.example.com',))` wraps an explicitly initialized ASGI
application as an AWS HTTP API payload-v2 or Lambda Function URL handler. It checks
gateway domain and host agreement, constructs an HTTPS ASGI request, preserves
query strings, cookies, and binary bodies, and returns buffered binary responses
and separate Set-Cookie values. Forwarded host/address headers cannot select the
tenant. Allowed gateway domains are explicit; there is no wildcard fallback.

```python
from evlet import LambdaHTTP

# app is initialized by the consumer and uses independently durable state.
handler = LambdaHTTP(app, domains=('app.example.com',))
```

The adapter bounds requests to 1.5 MB and buffered responses to 4 MB. It does not
run lifespan, start workers, stream responses, provision infrastructure, manage
secrets, or migrate data. The SSE route returns 501; the existing event polling
route is the fallback. API Gateway base-path mapping must deliver the paths the
application expects.

This is a transport adapter, not a deployable Lambda version of the SQLite
Playground. Lambda instance-local files do not provide the required persistent
application database. A live serverless consumer needs an independently durable
database adapter and a separate job execution arrangement. PostgreSQL support and
broader deployment hardening remain later work. No Lambda deployment is included.

The protocol follows the official [HTTP API payload-v2 contract](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-develop-integrations-lambda.html)
and [Function URL format](https://docs.aws.amazon.com/lambda/latest/dg/urls-invocation.html).
Object behavior follows [S3 PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html)
and the published [R2 S3 compatibility list](https://developers.cloudflare.com/r2/api/s3/api/).

## Verification

Run the existing backend/frontend tests and builds. Install `requirements-s3.lock`
first to include the SDK protocol tests; they otherwise skip when boto3 is absent.
`python scripts/smoke_tenants.py` now also starts a worker and checks real queued
execution, event polling, SSE, cookie replay rejection, and persistence. Its
`--frontend` and `--caddy` variants test Vite and local HTTPS. All use disposable
state and stop their own processes. Local validation does not establish public
cloud operation, production capacity, or deployment readiness.

A known database failure before a task handler starts, or a graceful stop after
claiming, returns the job to the queue without consuming an attempt. Handler
failures still use the task retry policy. A crashed worker retains the existing
lease recovery and attempt limit. CLI servers end live event streams before
waiting for graceful shutdown; other ASGI hosts can call `Application.stop()`, or
`TenantApplication.stop()` for every tenant, when beginning their shutdown drain.
Uvicorn drains connections before lifespan shutdown, so call it from the host's
shutdown hook rather than relying on lifespan.
