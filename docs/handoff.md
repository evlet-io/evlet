# Continue Evlet development locally

The user wants coding and the interactive Playground to run on their **Mac**.
Continue from the existing implementation; do not restart or scaffold it again.

## Repository and scope

- Repository: `dxb-aslam/evlet` (private).
- Working branch: `codex/milestone-1-foundation`.
- Last implementation commit before this handoff: `1a48911`.
- `main` contains only an empty initialization commit. Do not switch to it to run
  the application. All Milestone 1 implementation and the local launcher were
  pushed to the feature branch.
- Read `AGENTS.md`, `docs/specification.md`, `docs/architecture.md`, and `README.md`.
- The authorized framework scope is **Milestone 1 only**. Moving development
  locally does not authorize implementing later milestones, merging, or deploying.

## What exists

The Python core has no third-party runtime dependencies. It provides explicit
model registration, Text/Integer/Currency/Link fields, validation, SQLite WAL and
foreign keys, transactions, and an ASGI health/schema/create/list/detail API.
Currency uses integer minor units with two-decimal string serialization.

The Vue 3 / MIT-licensed PrimeVue 4 Playground exercises real Customer → Invoice
→ Payment persistence through the public API. It includes forms, relationship
selectors, tables, pagination, and error/loading states. Python and npm lockfiles,
package builds, GitHub Actions, and documentation are included.

There is no authentication, authorization, tenant isolation, migration engine,
update/delete API, accounting ledger, or deployment adapter. Bind servers to
loopback. Relationship selectors currently show at most 100 candidates.

## First actions in the local task

1. Inspect the local checkout, branch, status, and applicable agent instructions.
   Preserve existing edits. Fetch or update the feature branch only when safe;
   never reset local work to synchronize it. Use the existing checkout.
2. Check installed Git, Python 3.12+, and Node 24.15+ within the 24.x line. Reuse
   existing tools and GitHub authentication before requesting installation or
   login. On macOS, Homebrew's `python@3.12` and `node@24` work with the launcher.
3. From the repository root, run `bash scripts/playground.sh --setup`. It installs
   locked dependencies into `.venv` and `frontend/node_modules`, starts the ASGI
   backend on port 8000 and Vite on 5173, checks readiness, and opens the browser.
   Subsequent runs use `bash scripts/playground.sh` without `--setup`.
4. Verify the actual local browser workflow and persistence. Control-C stops both
   servers launched by the script. The script refuses occupied ports rather than
   stopping another process. Logs are in `examples/playground/.data/logs/`.
5. Run the checks below. If authorized work remains after local setup, continue it
   within Milestone 1. Do not automatically begin Milestone 2.

SQLite data is ignored by Git and stays in `examples/playground/.data/`; a fresh
local clone starts with a fresh database. Cloud test data and installed
dependencies are not transferred by Git. `EVLET_DATABASE` selects another file.

## Validation already performed in cloud Linux

- 61 pytest tests and 12 Vitest tests passed, with no skips or failures.
- Frozen dependency installation, Python source/wheel builds, an isolated wheel
  installation, and the frontend production build passed.
- Real HTTP and Chromium flows created and read linked customer/invoice/payment
  records. Persistence survived browser reload and backend restart.
- Desktop/mobile rendering and accessible relationship controls were verified.
- Launcher setup, startup, live requests, occupied-port protection, Control-C
  cleanup, and repeat startup passed. Mac verification is recorded below.
- The frontend build has a nonfatal bundle-size advisory; no scaling guarantees
  or production readiness are claimed.

From the root, run `.venv/bin/python -m pytest`, `npm --prefix frontend test`,
`npm --prefix frontend run build`, and `.venv/bin/python -m build --no-isolation`.
With the servers running, `.venv/bin/python scripts/smoke.py --base-url
http://127.0.0.1:5173` verifies the live workflow and adds uniquely named examples.

## Local Mac verification: 28 October 2026

The existing Mac checkout now uses the Milestone 1 branch. Untracked work from
`feat/framework-foundation` was preserved in a Git stash named
`Preserve local foundation work before Milestone 1 continuation`. Keep that stash
until the original work is needed; it is separate from this implementation.

- `bash scripts/playground.sh --setup` installed both locked dependency sets and
  opened the Playground in Chrome. Homebrew Python 3.12.11 and Node 24.15.0 were
  used by the launcher; frontend tests/build used the installed Node 24.16.0.
- All 61 backend tests and 12 frontend tests passed. The frontend production
  build, Python source/wheel builds, and isolated wheel installation check passed.
- Chrome created a customer, linked invoice, and linked payment with exact
  `123.45` amounts. The records survived browser reload and a full server restart.
- The live HTTP smoke check passed through the Vite proxy, including validation,
  pagination, foreign-key rejection, and decimal serialization.
- Occupied-port protection, Control-C cleanup, and repeat startup passed on macOS.
- No implementation fixes or dependency updates were needed. The documented
  nonfatal frontend bundle-size advisory remains.

## Pending review

[PR #1](https://github.com/dxb-aslam/evlet/pull/1),
“Implement Milestone 1: SQLite framework and Vue Playground”, is open as a draft
against `main`. It includes the architecture, dependencies, local validation,
startup command, limits, and Milestone 2 recommendation. Check the PR's current
GitHub Actions results separately from the local checks above. Do not merge or
deploy automatically, and do not begin Milestone 2 without a new request.

The cloud environment draft has saved install/start instructions and a requested
`api.github.com` network addition. Its publication remains separate from local
development and does not need to block work on the Mac.
