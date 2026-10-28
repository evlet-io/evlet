# Continue Evlet locally

Work in the existing Mac checkout of `dxb-aslam/evlet`. Preserve local changes and
the stash named `Preserve local foundation work before Milestone 1 continuation`.
Do not create a worktree, merge, deploy, or start another milestone without a request.

## Current scope

Milestones 1, 2, and 3 were reviewed and merged through PRs #1, #2, and #3.
The current feature branch contains Milestone 4, based on main at `27e81d8`.
The user approved local username/password accounts, Admin/Editor/Viewer roles,
and private local attachments. Milestone 5 remains separate.

Read `AGENTS.md`, `docs/specification.md`, `docs/architecture.md`, and
[business capabilities](business-capabilities.md) before changing public APIs.
Python definitions remain authoritative. The core has no third-party runtime
dependencies, and the frontend uses Vue 3 and PrimeVue 4.

Milestone 4 includes:

- Local account setup, sign-in/out, account administration, and revocable sessions.
- Backend model, field, and row rules covering schema, queries, counts, CRUD,
  links, children, actions, and attachments.
- ProjectTask child rows saved with their Project in one transaction.
- Registered Start/Complete Project actions, including task validation and rollback.
- Private attachments stored in SQLite with checked downloads and deletion.
- Python-defined form groups and generated child/action/attachment controls.

A bare `Application(database)` remains an open development API for compatibility.
The Playground always supplies LocalAuth. Trusted Python sessions and action
handlers can bypass HTTP permission rules; this boundary is intentional and
explained in the business guide. There is no tenant isolation or production hosting.

## Local state and upgrade

The working database is `examples/playground/.data/playground.sqlite3`. Before
applying `0003_project_tasks`, a SQLite backup was created at
`examples/playground/.data/playground.sqlite3.20261028T200350056610Z.backup`.
Every original Customer, Invoice, Payment, and Project row was compared before and
after migration. The 2 customers, 2 invoices, and 2 payments were unchanged; there
were no Projects. Foreign-key checks passed.

No test account or test record was added to this working database. On first opening
the upgraded Playground, the user creates their own administrator and password.
Do not enter or invent their credentials. Browser testing used a separate disposable
database with temporary accounts and a sample Project.

Start with `bash scripts/playground.sh`; use `--setup` when dependencies need to be
installed from the existing lockfiles. The launcher checks readiness without an
unauthenticated schema request, binds both servers to loopback, and opens
`http://127.0.0.1:5173`. It refuses occupied ports and does not stop other services.
Control-C stops both processes. Logs are in `examples/playground/.data/logs/`.

For another existing database, stop its server, set `EVLET_DATABASE` if needed,
and run `.venv/bin/python -m examples.playground.migrate`. The command backs up an
existing file first. Startup never silently changes application model tables.

## Validation

Local checks on 28/29 October 2026:

- 163 backend tests and 42 frontend tests pass.
- Production frontend, source distribution, and wheel builds pass. The existing
  bundle-size advisory remains; it is not a build failure.
- Clean wheel installation passes import, SQLite, migration, query, UI schema,
  authentication initialization, and permission metadata checks.
- Authenticated live HTTP checks pass through Vite, including customer/invoice/payment
  persistence, children, actions, attachments, validation, and cleanup.
- A fresh disposable server passes initial account setup, the complete smoke
  workflow, restart, migration-history validation, and test-record cleanup.
- Chrome verifies Editor sign-in, grouped Project creation with a child task,
  persisted task edits, the completion rejection message, successful completion,
  attachment upload, Viewer read-only controls, and private attachment download.
- Viewer sessions and Project/task/action/attachment data survive backend restart.
  The browser is returned to the working database's initial administrator screen.

Run `.venv/bin/python -m pytest`, `npm --prefix frontend test`,
`npm --prefix frontend run build`, and `.venv/bin/python -m build --no-isolation`.
The ordinary smoke command prompts for an existing Admin or Editor account.
Use `scripts/smoke.py --setup` only on an empty disposable database. CI does this
and generates a temporary password without printing it. Never put real passwords
in shell history, source files, test output, or PR descriptions.

The Milestone 4 PR should receive independent review. Check the current remote CI
results before approval. No automatic merge or deployment is authorized.
