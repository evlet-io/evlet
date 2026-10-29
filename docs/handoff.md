# Continue Evlet locally

Work in the existing Mac checkout of `dxb-aslam/evlet`. Preserve local changes and
the stash named `Preserve local foundation work before Milestone 1 continuation`.
Do not create a worktree, merge, deploy, or start another milestone without a request.

## Current scope

Milestones 1 through 6 were reviewed and merged through PRs #1 through #6.
Milestone 6 merged at `28c35c3`. This feature branch implements Milestone 7 on that
base: compact discovery, permission-filtered metadata, authorized local MCP tools,
starter commands, and developer documentation.

Read `AGENTS.md`, `docs/specification.md`, `docs/architecture.md`,
[business capabilities](business-capabilities.md),
[tenancy and deployment](tenancy-deployment.md),
[jobs, realtime, and adapters](jobs-realtime.md), and
[discovery and local connectors](connector.md) before changing public APIs.
Python definitions remain authoritative. The core has no third-party runtime
dependencies; the frontend uses Vue 3 and PrimeVue 4. Boto3 is an optional storage
extra. The official MCP SDK is optional test instrumentation, not a runtime need.
Both have separate hash locks.

Milestone 7 includes:

- Authenticated compact discovery with pagination and per-model descriptions.
- JSON schemas for permitted reads, creates, updates, and child replacements;
  field constraints, query metadata, and registered action descriptions.
- Local MCP stdio tools for discovery, records, permitted actions, and job status.
- Read tools by default and an explicit process flag for writes. Every operation
  still uses the application's current HTTP permission and tenant checks.
- A bounded HTTP connector pinned to one origin and private account session,
  with no redirects, proxy inheritance, or automatic write retries.
- `new`, `login`, `describe`, `mcp`, and `logout` commands. The starter uses public
  exports, local authentication, and the existing generated browser renderer.
- A complete connector and application development guide.

This milestone provides local stdio, not a remote MCP service. It creates no
provider configuration, cloud resources, or deployment. Client approval prompts
remain the client's responsibility. A lost response can follow a committed write;
inspect state before retrying. Tool metadata is guidance, not authorization.
The connector has no arbitrary SQL, Python execution, or file-management tools.
M7 introduces no database migration and does not change the frontend UI contract.

## Preserved local state

The working database is `examples/playground/.data/playground.sqlite3`. Before
Milestone 4's `0003_project_tasks`, the migration command created a backup at
`examples/playground/.data/playground.sqlite3.20261028T200350056610Z.backup`.
The original 2 customers, 2 invoices, and 2 payments were unchanged at that upgrade.
Do not assume those counts still describe subsequent user work.

Milestone 7 tests used disposable databases, accounts, session files, and ports.
They did not provision accounts in, import into, or copy over the working database.
Do not invent the user's credentials. The browser check used a generated starter
on ports 8010 and 5174, then signed out, closed its tab, stopped its own servers,
and removed its temporary state. This run did not start or restart the ordinary app.

Start the ordinary app with `bash scripts/playground.sh`; use `--setup` only when
dependencies need installation from the existing lockfiles. The default browser
URL is `http://127.0.0.1:5173`, with logs under `.data/logs/5173/`. The launcher
starts an optional job worker alongside both servers. Set `EVLET_RUN_JOBS=0` to
skip it. The launcher refuses occupied ports and stops only its own processes.
Read the tenant guide for catalog setup and alternate ports.

For an older application database, stop both API and worker, set `EVLET_DATABASE`,
and run `.venv/bin/python -m examples.playground.migrate`. For filesystem or object
attachments, take a complete consistent backup of metadata, sidecars, bytes, and
storage markers first. Review restored pending jobs and schedules before starting
workers. A database-only backup is insufficient for external attachments.

## Validation

Milestone 7 local verification on 29 October 2026 covers:

- 275 backend tests and 80 frontend tests. New tests cover discovery, constraints,
  child schemas, row/field rules, session and tenant revocation, private session
  files, protocol bounds, blocked redirects, invalid responses, and uncertain writes.
- Official MCP client interoperability over a real stdio subprocess and HTTP server:
  initialization, tools, JSON schemas, record writes, actions, validation failures,
  and session revocation against temporary tenant databases.
- Production frontend build, source distribution, wheel, and a clean wheel install
  without development or optional dependencies. The installed package check also
  covers discovery, connector imports, protocol handling, and starter generation.
- Real two-tenant HTTP, Vite, and Caddy local HTTPS checks, including discovery,
  worker execution, events, cookie replay rejection, persistence, and revocation.
  HTTP and Vite also exercise the stdio connector. The HTTPS test CA is isolated;
  no system certificate trust was installed.
- Chrome sign-in to a disposable generated starter, Item creation through its form,
  reading and updating that record through the connector, and the updated value
  visible after browser refresh. The test then signed out and removed its state.
- Installed dependency consistency. The existing frontend bundle-size advisory
  remains nonfatal; frontend optimization is outside this milestone.

Run `.venv/bin/python -m pytest`, `npm --prefix frontend test`,
`npm --prefix frontend run build`, and `.venv/bin/python -m build --no-isolation`.
Install `requirements-s3.lock` and `requirements-mcp.lock` with `--require-hashes`
to include optional protocol tests. CI includes both on Python 3.12 and 3.13.
`python scripts/smoke_tenants.py` uses disposable state; add `--frontend` or
`--caddy` for proxy checks. The latter needs Caddy and a frontend build. Vite must
keep `changeOrigin: false` to preserve tenant Host routing.

The Milestone 7 PR needs independent review and current CI checks. Public DNS,
public certificates, Linux systemd operation, live object storage, and Lambda
operation have not been exercised on a deployment target. Milestone 8 performance,
PostgreSQL, and production hardening remain separate. No merge or deployment is
authorized. Keep subsequent milestones outside this PR.
