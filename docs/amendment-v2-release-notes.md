# Declarative application amendment: unreleased

This change adds flat apps, discovered resources and declarative document behavior
to the existing framework. It is based on
`36a60c24fad714bbd378ab5e1c8fafe3b62a4d31`. The package version remains
`0.1.3.dev0`; this is a review branch, not a published release.

## Changes and compatibility

- New `evlet app NAME` projects have a single package root. Nested apps and
  `AppDefinition` remain supported; `--legacy` creates the old scaffold.
- `evlet setup` now prepares a project-local Python environment. Project
  commands delegate to it. Legacy projects without its marker keep the
  interpreter explicitly selected by the operator.
- Trusted builds discover models, reports, nested APIs/crons and native modules.
  Generations include pinned source, metadata and assets. Failed builds keep
  the published generation intact; active runtimes cannot switch generations
  in place. Build and acquisition do not install or migrate an instance.
- New resource declarations require explicit permissions. Legacy permission
  defaults remain unchanged. Logical resource IDs do not rename SQL tables or
  existing HTTP aliases.
- Immutable stored records are retained. Draft operations add owned children,
  row order, exact calculations, conditional fields, Link filters, controlled
  fetching, virtual queries and partial preview. Complete saves recompute and
  validate on the server.
- New document PATCH, actions and deletion require `If-Match`. Legacy routes
  retain their existing optional revision contract. A rendered record is not
  dirty merely because a volatile preview refreshed.
- Typed actions, immutable change sets and source-bound dialogs share existing
  write transactions. Dialog retries use actor/source/input-scoped idempotency.
  Related-only operations do not falsely change the source revision.
- Manual and autorun reports use the same transient field engine. Manual filter
  editing does not execute a report. Results and pagination retain the executed
  filters; optional totals explicitly cover the current page.
- Explicit service endpoints retain authentication, origin/CSRF and policy
  checks. Calendar scheduling extends the existing durable queue and preserves
  disabled schedules and history. External-effect tasks release the database
  transaction while doing I/O and fence completion by lease.
- App-owned native modules add panels, actions and neutral layouts to the
  existing renderer. They do not replace backend validation.

The working reference is [examples/amendment](../examples/amendment/README.md).
Its Sales Order includes owned items, totals, stock lookup, Work Order planning,
manual/automatic reports, a nested service/cron and an additive native panel.

## Plan-to-source map

| Phase | Owning implementation and evidence |
| --- | --- |
| P0 | Audited baseline above; existing tests and distribution checks ran before changes. Compatibility decisions are in [the authoring guide](amendment-v2.md). |
| P1 | `packages.py`, `cli/setup.py`, `cli/app.py`, flat templates; lifecycle and outside-checkout package checks. |
| P2 | `builds.py`, `instances.py`, CLI build/watch integration; atomic build, reuse, pinning and late-import tests. |
| P3 | `declarations.py`, `models.py`, `fields.py`, `documents.py`, `permissions.py`; authenticated document and permission tests. |
| P4 | Owned tables, `reactivity.py`, `DocumentState.ts`, `DecimalValue.ts`, `RecordForm.tsx`; shared arithmetic/HTTP fixtures and legacy child conversion. |
| P5 | Draft preview, named sources, `@depends`, dynamic Link filters and fetches; boundary tests and browser stock feedback. |
| P6 | Typed actions/change reactions, `forms.py`, `TransientForm.tsx`; retry, rollback, revision and concurrent planning coverage. |
| P7 | Report definitions/routes and `ReportView.tsx`; manual/autorun, readiness and executed-filter tests. |
| P8 | `services.py`, calendar registration and queue adapters; endpoint, DST, reconciliation, identity and isolation checks. |
| P9 | `native.py`, `NativePanels.tsx`, reference TS panel; compiled manifest and authenticated asset checks. |
| P10 | Full backend/frontend regressions, PostgreSQL, tenant/connector HTTP smoke, installed distribution checks and manual browser checks below. |

This map groups evidence. It does not claim a separate executed test for every
numbered acceptance item in the amendment plan.

## Upgrading an existing app

1. Back up the selected instance and its `apps.lock`; stop its server and worker
   before installation or migration. Use the established recovery procedure for
   the selected adapter.
2. Keep the existing nested layout if no conversion is needed. Existing
   `AppDefinition`, `ChildTable`, `Action` and interval definitions still work.
3. For a deliberate flat conversion, move the package root and add installable
   package metadata. Preserve class/storage names and historical migrations.
   Supply explicit inline permissions for every new declaration.
4. To convert existing owned rows, set
   `Table(Row, storage_name="existing_table", owner_field="existing_owner")`.
   Add `evlet_position` with an explicit `AddField` migration and a zero default.
   Existing IDs and owner links remain intact; initial ties sort by ID. Do not
   rewrite the checksums of migrations already applied.
5. Review source and dependencies, then run `evlet build --trust`. Inspect its
   output before running `evlet migrate INSTANCE --trust` for the selected
   instance. A changed source layout changes its fingerprint even if SQL is
   unchanged. `serve` verifies locks/schema and refuses to migrate implicitly.
6. Update custom callers of new document operations to send current revisions.
   Test authorization, preview/save, actions and restore on a disposable copy
   before using the change with business data.

See [the CLI guide](cli.md) for command options and
[the declarative guide](amendment-v2.md) for public contracts and recovery.

## Limits and deferred work

- SQLite remains the default. PostgreSQL retains its database-local advisory
  write lock, bounded pool and existing schema restrictions. This change does
  not introduce parallel writes within one PostgreSQL database.
- Managed instance files, hostname tenant provisioning and staged instance
  installation remain SQLite-specific. PostgreSQL applications use their
  existing explicit configuration and migration path. No adapter conversion
  or automatic PostgreSQL tenant catalog is included.
- Existing serverless and optional storage/connector behavior is covered by the
  local regression suite. No live cloud deployment, external service delivery
  or hosted connector end-to-end certification was performed.
- Document collections and report pages are bounded at 100 rows. Report totals
  are page totals with declared units. Exports, pivots and charts are deferred.
- New reactive integer values fit the browser-safe integer range. Decimal
  intermediates and rounding have explicit shared bounds; legacy stored
  integer semantics remain unchanged.
- Service/action inputs are typed scalars. Query reuse is request-local; there
  is no cross-user query cache or automatic external stock subscription.
- Native sources use Node 24 and relative `.ts/.js` modules. JSX and arbitrary
  npm dependency bundling require a compatible prebuilt module. Independent
  custom frontends, SSR hosts and alternate frontend frameworks are deferred.
- External effects remain at-least-once work. Receivers need idempotency and
  finite deadlines; a lease cannot undo an external side effect.
- No customer database was changed. No package publication, merge or deployment
  is included, and these checks do not establish production readiness.

## Verification ledger

Executed locally on 1 November 2026 using macOS 27.0.1 (arm64), Python 3.12.16,
Node 24.16.0 and a disposable UTF-8 PostgreSQL 14.18 cluster on loopback port
56439. Tests created their own SQLite files and PostgreSQL databases. No
production credentials or data were used.

Baseline commands from the repository root:

```sh
.venv/bin/python -m pytest
cd frontend
npm test
npm run build
```

Baseline results: 408 Python tests passed; 34 PostgreSQL tests skipped because
the test DSN was not configured. All 180 frontend tests and the frontend build
passed. Baseline distribution and installed-wheel checks also passed.

Final verification commands, from the repository root unless noted:

```sh
EVLET_TEST_POSTGRES_DSN='host=127.0.0.1 port=56439 dbname=postgres user=evlet_test' .venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke_tenants.py
cd frontend
npm test
npm run build
```

The final backend suite passed all 468 tests, including 35 PostgreSQL tests,
with no skips, in 81.48 seconds. The frontend suite passed all 195 tests;
the production build and TypeScript check passed.
The two-tenant HTTP smoke passed sign-in, records, children, actions, private
files, discovery, durable jobs, event replay, restart, revocation and connector
reads.

Distribution checks used the following commands. `REPO` is the checkout root;
the `-I` checks ran with `/private/tmp` as their working directory:

```sh
.venv/bin/python -m build --no-isolation --outdir /private/tmp/evlet-v2-dist
/private/tmp/evlet-v2-wheel-env/bin/python -m pip install --disable-pip-version-check --no-deps --force-reinstall /private/tmp/evlet-v2-dist/evlet-0.1.3.dev0-py3-none-any.whl
/private/tmp/evlet-v2-wheel-env/bin/python -I "$REPO/scripts/check_package.py"
/private/tmp/evlet-v2-wheel-env/bin/python -I "$REPO/scripts/smoke_cli.py"
.venv/bin/python scripts/check_flat_package.py /private/tmp/evlet-v2-dist/evlet-0.1.3.dev0-py3-none-any.whl
```

All distribution checks passed. The wheel verification environment was created
with `python -m venv` and initially installed with the wheel and its required
dependencies, without optional PostgreSQL/S3 extras. The installed CLI exercised
real HTTP setup/sign-in, record persistence across restart, live development
reload, instance locks, Playground and offline docs. The generated flat app
was built and installed separately as wheel, source distribution and editable
package; all three imported from outside their source directory.

Manual browser checks passed for the reference app's sign-in/Docs, generated
Sales Order editing, live total and stock feedback, save, Work Order dialog and
manual report. The installed-wheel Playground also passed sign-in to Docs,
Projects demo list/preview, Layouts & state binding/dialog, and a legacy
generated Project child edit/save. Permission and concurrency matrices were
tested programmatically, not exhaustively repeated in the browser.

An initial local PostgreSQL cluster used SQL_ASCII and failed string comparisons
because driver results were bytes. It was replaced with the documented UTF-8
cluster before the final run. Browser testing also exposed initial query-state
hydration and child-field focus regressions; these were fixed and added to the
native regression tests. No unresolved failures remain in the checks reported
as passed. PostgreSQL 18, Python 3.13, Windows, live cloud services and the remote
CI matrix were not executed locally. CI retains those existing platform jobs;
Node setup now precedes native-extension backend tests, and CI runs the generated
flat-package check.
