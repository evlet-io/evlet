# Completed backend milestones

These notes record the backend work already available in Evlet. Start with the
[quick start](../getting-started.md) to use it.

## Milestone 5: tenants and deployment

The [tenant and deployment guide](../tenancy-deployment.md) covers separate tenant
SQLite files, hostname routing, DNS ownership verification, private file storage,
and the Caddy/systemd reference deployment. The ordinary Playground retains its
existing database and startup command. Tenant setup uses a separate catalog and
prompts for its first administrator through the local management command.

Run `python scripts/smoke_tenants.py` with the virtual environment active to check
two disposable tenants over HTTP. Add `--frontend` to test Vite or `--caddy` to test
local HTTPS after building the frontend. No deployment runs automatically.


## Milestone 6: jobs, live updates, and adapters

The [jobs and live-update guide](../jobs-realtime.md) covers transactional jobs,
retries, schedules, permission-filtered SSE and polling, and the optional S3/R2
storage and Lambda HTTP adapters. No Redis or distributed queue is required.
Use `EVLET_RUN_JOBS=0` to run the Playground without a worker.

The Lambda adapter supplies HTTP transport only. It does not make the SQLite
Playground deployable on an ephemeral filesystem. No cloud resources are created,
and live cloud operation has not been verified. WebSockets and broader production
hardening remain deferred.


## Milestone 7: discovery and connector interfaces

The [connector guide](../connector.md) covers compact discovery, model input
schemas, a local MCP stdio connector, private session files, and starter generation.
Run `python -m evlet new ./my-app` to create a small authenticated application.
The connector starts with read tools; `--allow-writes` explicitly enables permitted
writes through the existing HTTP API. Every request retains backend permission
checks. No external provider or additional runtime dependency is required.

Install `requirements-mcp.lock` with `--require-hashes` to include the optional
official-client interoperability test. CI includes it; ordinary Playground setup
does not need it. Remote MCP hosting and production hardening remain separate work.

## Milestone 8: performance and hardening

- [PostgreSQL setup, transaction behavior, and migration limits](../postgresql.md)
- [Reproducible workload and measured adapter comparison](../performance.md)
- [Deployment, backup, recovery, and monitoring](../production-operations.md)

The existing tenant catalog remains SQLite-only. PostgreSQL supports authenticated
single-application deployments and conservatively serializes writes per database.
No production scale or parallel-write capacity is implied by this release.
