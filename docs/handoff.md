# Continue Evlet locally

Work in the existing Mac checkout of `dxb-aslam/evlet`. Preserve local changes and
the stash named `Preserve local foundation work before Milestone 1 continuation`.
Do not create a worktree, merge, deploy, or start another milestone without a request.

## Current scope

Milestones 1 through 4 were reviewed and merged through PRs #1 through #4.
Milestone 4 merged at `7a8a83c`. This feature branch implements Milestone 5 on that
base: tenant registry, hostname routing, separate databases, custom-domain
verification, private file storage, and reference Caddy/systemd deployment.

Read `AGENTS.md`, `docs/specification.md`, `docs/architecture.md`,
[business capabilities](business-capabilities.md), and
[tenancy and deployment](tenancy-deployment.md) before changing public APIs.
Python definitions remain authoritative. The core has no third-party runtime
dependencies, and the frontend uses Vue 3 and PrimeVue 4.

Milestone 5 includes:

- A trusted SQLite tenant catalog and immutable tenant context resolved before
  database access. Unknown, malformed, unverified, or disabled hosts return 421.
- Separate tenant databases, accounts, sessions, private file directories, and
  exact browser origins. Runtime never recreates a missing tenant database.
- Local administrator provisioning with no public setup route for tenant apps.
- Fresh DNS TXT verification for custom domains and explicit host revocation.
- Local filesystem attachment storage with transactional metadata, old BLOB
  compatibility, directory binding, and explicit orphan collection.
- Reviewable Caddy and systemd configuration, a locked Uvicorn runtime subset,
  and real HTTP/Vite/local HTTPS smoke checks.
- Tenant identity in the sign-in screen and account bar.

A bare `Application(database)` remains an open development API for compatibility.
The ordinary Playground uses LocalAuth and its existing SQLite attachment adapter.
Tenant applications enforce separate storage and secure cookies in production.
Trusted Python sessions, handlers, and the service operator can bypass HTTP
permission rules; they are outside the application isolation boundary.

## Preserved local state

The working database is `examples/playground/.data/playground.sqlite3`. Before
Milestone 4's `0003_project_tasks`, the migration command created a backup at
`examples/playground/.data/playground.sqlite3.20261028T200350056610Z.backup`.
The original 2 customers, 2 invoices, and 2 payments were unchanged at that upgrade.
Do not assume those counts still describe subsequent user work.

Milestone 5 tests used disposable databases and temporary accounts. They did not
import, copy into, or provision accounts in the working Playground database.
Do not invent the user's credentials. The original local Playground can remain
running while tenant tests use separate loopback ports. Restart it normally to
load backend code changes. New internal attachment columns are additive; existing
BLOB bytes and application records are preserved.

Start the ordinary app with `bash scripts/playground.sh`; use `--setup` only when
dependencies need installation from the existing lockfiles. The default browser
URL is `http://127.0.0.1:5173`, with logs under `.data/logs/5173/`. The launcher
refuses occupied ports and does not stop other services. Control-C stops both
processes it owns. Read the tenant guide for separate catalog setup and alternate
ports. No tenant is created automatically from an incoming hostname.

For another existing database, stop its server, set `EVLET_DATABASE`, and run
`.venv/bin/python -m examples.playground.migrate`. For filesystem attachments,
take a complete stopped-state backup of the catalog, tenant databases, sidecars,
files, and storage markers first. A database-only backup is insufficient.

## Validation

Milestone 5 local verification on 29 October 2026 covers:

- The complete backend and frontend suites, production frontend build, source
  distribution, and wheel. The existing bundle-size advisory remains nonfatal.
- Clean wheel installation, including tenant catalog and private file round trips.
- Locked server runtime installation without development dependencies.
- Real two-tenant HTTP checks directly, through Vite, and through Caddy with an
  isolated local CA. No system certificate trust was installed.
- Separate cookies, same-ID records, children, actions, attachments, replay
  rejection, backend restart persistence, and tenant revocation.
- Chrome sign-in to Alpha and Beta, distinct customers/projects/tasks, an action
  affecting only Alpha, and persistence of each hostname's own session.
- Caddy configuration parsing for explicit active verified hosts.

The Vite proxy must keep `changeOrigin: false`; its string shorthand replaces Host
and prevents tenant resolution. The real frontend smoke check covers this boundary.

Run `.venv/bin/python -m pytest`, `npm --prefix frontend test`,
`npm --prefix frontend run build`, `.venv/bin/python -m build --no-isolation`, and
`python scripts/smoke_tenants.py` with the environment active. Add `--frontend` or
`--caddy` for the proxy checks. The latter needs Caddy and a frontend build.
The smoke commands create and remove their own state and processes.

The Milestone 5 PR needs independent review and current CI checks. Public domain
ownership verification, public ACME issuance/renewal, and systemd operation have
not been exercised on a deployment server. No merge or deployment is authorized.
Jobs, scheduling, realtime, object storage, and serverless adapters remain separate
Milestone 6 work.
