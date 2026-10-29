# Continue Evlet locally

Work in the existing Mac checkout of `dxb-aslam/evlet`. Preserve local changes and
the stash named `Preserve local foundation work before Milestone 1 continuation`.
Do not create a worktree, merge, deploy, or start another milestone without a request.

## Current scope

Milestones 1 through 5 were reviewed and merged through PRs #1 through #5.
Milestone 5 merged at `5f8478c`. This feature branch implements Milestone 6 on that
base: durable jobs, interval scheduling, record events, SSE with polling fallback,
a Lambda HTTP adapter, and private S3-compatible attachment storage.

Read `AGENTS.md`, `docs/specification.md`, `docs/architecture.md`,
[business capabilities](business-capabilities.md),
[tenancy and deployment](tenancy-deployment.md), and
[jobs, realtime, and adapters](jobs-realtime.md) before changing public APIs.
Python definitions remain authoritative. The core has no third-party runtime
dependencies; the frontend uses Vue 3 and PrimeVue 4. Boto3 is an optional extra
with a separate hash lock, used for the S3-compatible Playground configuration.

Milestone 6 includes:

- Transactional enqueue, persisted claim leases, retries, failure status, and
  interval schedules in each application's SQLite database.
- Current account and target permission checks before executing user jobs,
  tenant namespace binding, and a separate optional worker process.
- Bounded record invalidations with opaque replay cursors, current read checks,
  authenticated SSE, and polling fallback.
- A queued Project progress action and live updates that preserve unsaved forms.
- Private S3-compatible bytes with database-bound prefixes and explicit orphan
  collection, retaining authenticated downloads and old SQLite BLOB compatibility.
- A buffered AWS payload-v2 HTTP adapter. It supplies no persistence, lifespan,
  worker service, or infrastructure deployment.
- A rendered worker systemd unit, operator commands, and an operating guide.

The queue is for short single-node tasks. Each handler holds a SQLite write lock
and commits with its completion and target event. External effects still need
idempotency. Events are hints, not record contents or an audit log. Scoped deletion
notifications are omitted because a deleted row cannot be rechecked. WebSockets
are deferred. The Lambda adapter does not make the SQLite Playground suitable for
an ephemeral cloud filesystem; a durable database adapter is a separate requirement.

## Preserved local state

The working database is `examples/playground/.data/playground.sqlite3`. Before
Milestone 4's `0003_project_tasks`, the migration command created a backup at
`examples/playground/.data/playground.sqlite3.20261028T200350056610Z.backup`.
The original 2 customers, 2 invoices, and 2 payments were unchanged at that upgrade.
Do not assume those counts still describe subsequent user work.

Milestone 6 tests used disposable databases and temporary accounts. They did not
provision accounts in, import into, or copy over the working Playground database.
Do not invent the user's credentials. The original local Playground was left
running while browser tests used a separate catalog and ports. Restart it normally
to load backend code changes. The disposable browser test was stopped and removed.

Start the ordinary app with `bash scripts/playground.sh`; use `--setup` only when
dependencies need installation from the existing lockfiles. The default browser
URL is `http://127.0.0.1:5173`, with logs under `.data/logs/5173/`. The launcher now
starts an optional job worker alongside both servers. Set `EVLET_RUN_JOBS=0` to
skip it. The launcher refuses occupied ports and stops only its own processes.
Read the tenant guide for catalog setup and alternate ports.

This milestone adds internal metadata during initialization and preserves existing
application records and attachment BLOBs. It introduces no application migration.
For an older application database, stop both API and worker, set `EVLET_DATABASE`,
and run `.venv/bin/python -m examples.playground.migrate`. For filesystem or object
attachments, take a complete consistent backup of metadata, sidecars, bytes, and
storage markers first. Review restored pending jobs and schedules before starting
workers. A database-only backup is insufficient for external attachments.

## Validation

Milestone 6 local verification on 29 October 2026 covers:

- 246 backend tests and 80 frontend tests, including real SQLite queue recovery,
  competing claims, retry rollback, schedule coalescing, permission changes,
  disabled tenants, cursor replay, session revocation, and frontend race cases.
  Recovery regressions also cover a failed initial cursor request, temporary
  job-status failures, interrupted response-body downloads, permanent access
  failures, and retry cancellation.
- Production frontend build, source distribution, wheel, and a clean wheel install
  without development or optional dependencies. The existing bundle-size advisory
  remains nonfatal.
- Real two-tenant HTTP, Vite, and Caddy local HTTPS checks with worker execution,
  event polling/SSE, cookie replay rejection, persistence, and tenant revocation.
  The test CA was isolated; no system certificate trust was installed.
- Chrome sign-in to a disposable tenant, queued Project recalculation, completed
  job status, and progress changing from 0 to 50 without a page reload.
- The ordinary single-database Playground smoke check on a separate temporary
  database and loopback port.
- Boto3 signed requests against a local protocol server, private object ownership,
  namespace binding, orphan collection, integrity checks, and failed-transaction
  recovery. This is not evidence of live S3 or R2 operation.
- Lambda payload-v2 translation, binary bodies, cookies, authority checks, input
  bounds, and application sessions across adapter instances with local test state.

Run `.venv/bin/python -m pytest`, `npm --prefix frontend test`,
`npm --prefix frontend run build`, and `.venv/bin/python -m build --no-isolation`.
Install `requirements-s3.lock` with `--require-hashes` to include the optional SDK
protocol tests. `python scripts/smoke_tenants.py` uses disposable state; add
`--frontend` or `--caddy` for proxy checks. The latter needs Caddy and a frontend
build. Vite must keep `changeOrigin: false` to preserve tenant Host routing.

The Milestone 6 PR needs independent review and current CI checks. Public DNS,
public certificates, Linux systemd operation, live object storage, and Lambda
operation have not been exercised on a deployment target. No merge or deployment
is authorized. Keep subsequent milestones outside this PR.
