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

### Background Playground

A second, smaller Playground shows a job that people start from an action and the application
carries out through system moves. It uses its own database under
`examples/playground/background/.data/`:

```bash
.venv/bin/python -m uvicorn examples.playground.background.app:create_app --factory --host 127.0.0.1
.venv/bin/python -m examples.playground.background.app   # the job worker, in a second terminal
```

Create a price list and choose **Build**. The action queues `build_price_list` with
`self.context.enqueue(...)`. The job acts as the system through
`job.business(tx, system="Price list builder", role="admin")`, makes the system moves Start and
Finish, and reports progress in five resumable steps. The record never offers Start, Finish or
Fail as buttons, and posting to `/moves/building` by hand answers 403. Its History says the moves
were made by the system via Price list builder. Reopen is an ordinary move people make.

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
outside HTTP permission enforcement. To run moves, actions and document writes with their
checks, use the job's [business context](#a-jobs-business-context).

## Queue a job from an action

An `@action` (or a Move `run` method, or any trusted code with a document context) queues a
registered task with `context.enqueue(task, payload, *, key=None, target=None, run_at=None)`:

```python
class PriceList(Model):
    @action("Build", available_when=(F("status") == "draft") & (F("requested") == False))
    def build(self):
        self.requested = True
        return self.context.enqueue("build_price_list", {"price_list": str(self.id)})
```

* The job is written in the action's own transaction. It is queued only if the action commits;
  if the action fails, or trusted code rolls the transaction back, there is no job.
* Once the transaction commits, the worker in the same process (`evlet serve`) is woken and
  claims the job at once instead of at its next half-second pass. A worker in another process
  still finds it on its next pass. Nothing else changes: no Redis, no extra worker.
* The job runs as the context's user, read again when it starts, with any API key or AI
  assistant narrowing it was queued under. A context acting as the system queues it with that
  context's role, which must then be admin, editor or viewer. `target=(Model, id)` needs update access now and when the job runs.
* An action that returns the receipt adds `_job` (`{"id", "state"}`) to its detail. It is not
  part of the ETag. `GET /api/_jobs/<id>` follows it.
* `key=` deduplicates as with `JobQueue.enqueue`. The context must not be read only.
* `run_at=` (UTC seconds) delays the job: it stays queued until then.

`JobQueue.wake()` and `JobQueue.wait(timeout)` are the signal underneath, for app workers that
run their own loop: `wait` returns True when a committed transaction queued a job since the last
wait. The Background Playground's worker uses them.

## A job's business context

A handler gets the job's transaction (`tx`) as a trusted `Session`. Writes through it skip
permissions, rules and the Journal. To run moves, actions and document writes with every check
they have over HTTP, ask the job for a document context in that same transaction:

```python
def build_price_list(tx, job, payload):
    context = job.business(tx, system="Price list builder", role="admin")
    context.move(PriceList, payload["price_list"], "building")
```

`job.business(tx, *, user=None, system=None, role=None)` returns an `evlet.documents.Context`.
Who it acts as is always explicit:

| Call | Acts as | Permissions | Recorded as |
| --- | --- | --- | --- |
| `job.business(tx)` | The account that queued the job, read again when the job started, with its API key or assistant narrowing. A job queued with a system role acts with that role. | That account's role, row scopes and field permissions | That account |
| `job.business(tx, user=7)` | Account 7 (an id or a `User`), read again now. It must be active; its current role applies. | That account's role, row scopes and field permissions | That account |
| `job.business(tx, system="Price list builder", role="admin")` | The system, under that name (1 to 60 characters) | The named role's rules. A row scope that names the account matches nothing. | No account (`engine`), with `via: "Price list builder"` in the Journal and the name in Controls audit rows |

* There is no silent bypass. A job queued without a user must pass `user=` or `system=` with
  `role=`; `role` must be one of the application's roles; `user=` and `system=` together are
  refused. An inactive account fails the job with `AccessError` (no retry).
* Job handlers are trusted code, so the context may make
  [system moves](states-actions.md#system-moves). Every other check stays: update permission
  and row scope, guards, `run` methods, calculations, rules, validation, closed periods and
  locks. A move's `run` method still gets `self.context.session`.
* Lifecycle entries carry source `Automation` for a user and `System` for the system, and
  correlation id `job:<id>`.
* The context shares the job's transaction. Its writes commit with the job, or with the step of
  a resumable task, and roll back with it. The context is only valid inside the handler.
* An external task has no transaction, so it has no business context. Split it into a step
  that writes and a step that calls out, or open a transaction yourself.

Handlers must be short and bounded. They hold the tenant database's SQLite write
lock for their whole execution. Split long work into a resumable task
([below](#progress-and-long-jobs)), whose steps each hold it briefly. Do not use this queue for long network calls,
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

## Progress and long jobs

A handler reports how far it is with `context.progress(done, total=None, message='')`. `done`
is a nonnegative integer, `total` an integer no smaller than `done` or `None` when it is not
known, and `message` at most 200 characters. Progress is written with the handler's own
writes, so it is only seen once that transaction commits. In an `external=True` task, which
holds no transaction, each call is saved at once in a short transaction of its own.

A plain task commits once, at the end, so its progress only shows when it is done. A long
rebuild or recalculation should be a resumable task instead. It runs in steps, each in its
own transaction:

```python
from evlet import Task

BATCH = 200

def rebuild_balances(tx, context, payload):
    # Account and Balance belong to this application; context.checkpoint is None on the first step.
    accounts = tx.list(Account, filters={'id__gt': context.checkpoint} if context.checkpoint else None,
                       order_by='id', limit=BATCH)
    for account in accounts:
        tx.lock(('balance', account.id))
        ...  # Recompute this account's balance from its ledger rows.
    done = tx.count(Account, filters={'id__lte': accounts[-1].id}) if accounts else tx.count(Account)
    context.progress(done, tx.count(Account), f'Up to account {accounts[-1].id}' if accounts else '')
    return str(accounts[-1].id) if len(accounts) == BATCH else None

jobs = JobQueue(database, [Task('rebuild_balances', rebuild_balances, resumable=True)], tenant='acme')
```

The handler is called again and again until it returns `None`. Any other return value is the
checkpoint: JSON of at most 16 KiB, committed with that step's writes and progress, and given
to the next step as `context.checkpoint`. Each step is short, so it holds the database write
lock only briefly and other requests run between steps. On SQLite, writers in one process take
turns: a job step waits behind requests that came first, but never more than about a quarter of a
second, and after each step the job gives up its turn and joins the back of the line. While people
are writing, the job rests after each step for as long as the step took, so it gets about half of the
database's time and runs at full speed again when they stop. So a request
waits at most about one step, and a step that holds the database longer than a second logs a
warning naming the job ([performance.md](performance.md#busy-writing-on-sqlite)). Every step reloads the job, checks the
lease and rechecks the account and the target record, like the first one. Each committed step
also extends the claim's lease to at least 300 seconds from that step.

A job that stops resumes from its last committed checkpoint:

- **The worker is stopped**, for example with Control-C or because the tenant was disabled.
  Between two steps the worker sees it, puts the job back in the queue without counting an
  attempt, and the next worker carries on from the checkpoint.
- **A step fails.** Only that step rolls back, its progress included. The job is retried by the
  normal rules, and the retry starts from the last committed checkpoint. A manual retry of a
  failed job does too.
- **The process dies.** The step in progress rolls back. Once the lease runs out, another worker
  reclaims the job (counting an attempt) and resumes from the checkpoint.

A step must therefore be safe to run again from the same checkpoint: it should recompute
what it writes, as a rebuild does, rather than add to it. When the job succeeds the checkpoint
is cleared, and the final progress stays. A resumable task cannot be `external`.

`GET /api/_jobs/<id>` returns `progress` as `{"done", "total", "message"}`, or `null` before the
job reports any. The checkpoint is never returned. Controls > Jobs shows progress under each
run's status, with a bar when the total is known, and reads the list again every three seconds
while a run is running. `evlet jobs list --instance NAME` prints the newest jobs with their state,
attempts and progress ([cli.md](cli.md)).

The Playground's **Rebuild all points balances** action on a customer queues `rebuild_points`,
which recomputes every customer's balance from the points ledger 20 customers per step
([ledger.md](ledger.md)).

## Follow-up runs

A job can queue its own next run with `context.follow_up(tx, payload=None, *, run_at, key=None)`.
Use it for work that comes back later, such as checking a payment provider again in ten minutes,
instead of a schedule that runs every minute whether or not there is work:

```python
import time

def check_payment(tx, context, payload):
    # Payment belongs to this application.
    payment = tx.get(Payment, payload['payment'])
    if payment.status == 'pending':
        context.follow_up(tx, run_at=int(time.time()) + 600)
```

* The next run is written in the job's own transaction (the step's, for a resumable task). It is
  queued only if this run commits; a run that fails and is retried does not leave one behind.
* `payload` defaults to this run's payload. `run_at` is UTC seconds; the worker claims it then.
* It runs with this job's identity: the same account (read again when it starts), the same API
  key or AI assistant narrowing, or the same system role. It never runs as anyone else, such as
  the Administrator.
* `key` defaults to one per job and `run_at`, so the same run queues the same follow-up only once.
  Pass your own key to merge follow-ups from several runs.
* The next run is a new job with its own attempts and its own entry in Controls > Jobs.

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
with a fresh due time. There is no separate scheduler service. Eva's scheduled tasks are
calendar schedules named `eva_task_<id>` that belong to people, so releases leave them alone
([eva.md](eva.md#scheduled-tasks)). Disable a schedule
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
`model`, `id`, and `deleted`, except `{"type": "inbox.changed"}`, which only the person whose
inbox changed receives (`EventStore.publish_user`), and `{"type": "config.changed"}`. No record fields, actor IDs, or payloads are included.
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
database adapter ([database adapters](database-adapters.md)) and a separate job
execution arrangement. Broader deployment hardening remains later work. No Lambda deployment is included.

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
