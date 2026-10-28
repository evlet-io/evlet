# Continue Evlet development locally

The user wants coding and the interactive Playground to run on their **Mac**.
Continue from the existing implementation; do not restart or scaffold it again.

## Repository and scope

- Repository: `dxb-aslam/evlet` (private).
- Working branch: `codex/milestone-3-app-generation`, based on `main` at `849d95e`.
- Milestones 1 and 2 were reviewed and merged through PR #1 and PR #2.
- The user authorized Milestone 3 and wants to follow all milestones in order.
- Read `AGENTS.md`, `docs/specification.md`, `docs/architecture.md`, and
  [application generation](application-generation.md).
- Keep Milestone 3 on its own branch and PR. Do not merge, deploy, or start
  Milestone 4 automatically.

## Milestone 3 continuation

Python `Model.Meta` and field display options now define the generated screens.
`Registry.ui_schema()` is separate from migration snapshots. Navigation, forms,
lists, detail views, search, typed filters, ordering, and pagination consume this
neutral contract. Link choices search and page on the server, with selected-record
resolution and batched list labels. Forms preserve exact decimals and omit
untouched defaults or unchanged edit values. Unsupported metadata fails before
writes are enabled. Read-only fields reject HTTP writes; hidden fields are only
presentation settings, not permissions.

Project is the fourth Playground model and uses the same frontend components.
The explicit `0002_projects` migration creates its table. The Mac database was
backed up before applying it. All existing 2 customers, 2 invoices, and 2 payments
were compared before and after the migration and remained identical.

Local validation: 141 backend tests and 33 frontend tests pass. Production frontend
and source/wheel builds pass; a clean wheel installation passes its checks.
Fresh startup and restart over real HTTP record both migration baselines. Live HTTP checks cover the fourth model, UI metadata,
search/filter counts, partial updates, defaults, validation, and cleanup. Chrome
verified Project creation, customer search/selection, details, validation errors,
edits, and persistence after reload. Browser and smoke test records were removed;
the original records were compared again and remain unchanged. The existing frontend bundle-size advisory
remains. Check current CI separately before approving the PR.

The browser integer range is narrower than Python/SQLite's signed 64-bit range;
unsafe integer values and record IDs cannot be silently rounded into writes.
The renderer has no authentication, permissions, tenant isolation, attachments,
or custom business actions. Those belong to later milestones.

The relationship navigation review fix cancels pending record opens when navigation
changes, including switching away and back. Regression tests also cover unmounting.

The older untracked work remains in the named Git stash described below. Keep it.

## Milestone 1 baseline

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
   within Milestone 3. Do not automatically begin Milestone 4.

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

[PR #3](https://github.com/dxb-aslam/evlet/pull/3) targets `main` from the Milestone 3
branch. The relationship navigation race is fixed and ready for re-review.
Obtain independent review and check the final CI results. Do not merge or deploy
automatically, or begin Milestone 4.

The cloud environment draft has saved install/start instructions and a requested
`api.github.com` network addition. Its publication remains separate from local
development and does not need to block work on the Mac.
