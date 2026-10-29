# Continue Evlet locally

Use the existing checkout of `dxb-aslam/evlet` on `milestone-8-hardening`. Preserve
the stash named `Preserve local foundation work before Milestone 1 continuation`.
Do not create a worktree, merge, deploy, or expand the milestone without a request.

## Current scope

Milestones 1 through 7 were reviewed and merged. Milestone 7 merged at `d722e1e`.
This branch implements Milestone 8 performance and hardening:

- Optional PostgreSQL database/session API, native schema checks, atomic migrations,
  authenticated business routes, and a bounded pool with session cleanup.
- Shared ownership checks for private storage, jobs, and events.
- Explicit null ordering, portable empty filters, and literal-safe parameter binding.
- Session revocation/expiry and login cleanup indexes; duplicate security-header rejection.
- Real PostgreSQL parity, rollback, snapshot, concurrency, and pool-isolation tests.
- Concurrent cross-tenant cookie replay and warmed-route revocation tests.
- Reproducible large-ledger benchmarks and measured adapter comparisons.
- PostgreSQL CI, dependency hash lock, and deployment/recovery documentation.

Read `AGENTS.md`, `docs/architecture.md`, `docs/postgresql.md`,
`docs/performance.md`, and `docs/production-operations.md` before continuing.
Existing business, tenancy, jobs, and connector guides remain applicable.

PostgreSQL is optional. SQLite remains the default. The existing tenant catalog
and provisioning commands are SQLite-only. PostgreSQL framework writes serialize
per database; higher parallel write capacity remains unimplemented. Do not describe
this release as production ready. No public schema/UI contract was changed.

## Preserved state

The working database is `examples/playground/.data/playground.sqlite3`. It was not
opened or changed for Milestone 8. Previous migration backups, local accounts,
attachments, and the preserved stash remain in place. Do not assume historical
record counts still describe the user's current data.

Validation uses disposable databases. PostgreSQL tests create uniquely named
`evlet_test_*` databases on the configured test cluster and remove only those.
Benchmark databases contain synthetic records only. No production database, public
DNS, cloud storage, or deployment was changed. No user credentials were invented.

## Commands

```sh
.venv/bin/python -m pip install --require-hashes -r requirements-postgres.lock
.venv/bin/python -m pytest
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python -m build --no-isolation
```

Set `EVLET_TEST_POSTGRES_DSN` to a disposable PostgreSQL cluster to include all
PostgreSQL tests. Its test role needs CREATE/DROP DATABASE permission. Without that
environment setting the optional PostgreSQL tests skip; the ordinary suite still
runs without the driver. CI adds PostgreSQL 14 and 18 jobs with the driver installed.

For the Playground, set `EVLET_POSTGRES_DSN` only when intentionally selecting a
separate PostgreSQL database. The normal launcher and SQLite state remain unchanged.
Read `docs/postgresql.md` before using this option or adding worker processes.

## Validation status

The full 306-test backend suite plus the added pool-timeout regression passed,
as did all 80 frontend tests, the build, and clean base/optional wheel checks.
Both full sustained benchmarks completed with zero errors. Detailed evidence is
recorded in `docs/performance.md`.
Review current CI on the PR before merging. Local PostgreSQL testing uses version
14; version 18 is covered by the configured CI job, not inferred from local tests.
The frontend still emits its existing large-chunk advisory. No frontend redesign
is included. Public TLS/DNS, Linux services, live S3, and production hardware remain
unverified. Source changes are prepared for independent review, not automatic merge.
