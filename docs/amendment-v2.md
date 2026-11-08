# Declarative application amendment

Baseline: `36a60c24fad714bbd378ab5e1c8fafe3b62a4d31` (1 November 2026).

## Compatibility decisions

- Logical resource IDs are separate from SQL names and existing HTTP aliases.
  Existing storage names and migration checksums retain their meaning.
- Stored records remain immutable. Editable documents use an explicit draft;
  partial previews and complete saves have different validation boundaries.
- Flat apps use resource-owned permissions with default denial. Legacy
  `AppDefinition` apps retain the existing permission defaults.
- Download inspection stays static. Importing and building application code
  requires source-specific trust. Build generations never activate apps, apply
  migrations, or rewrite instance locks.
- Build output is versioned and published atomically. Instance installation
  checks remain authoritative, including when a build has succeeded.
- Existing nested apps remain supported. New scaffolds use a single package root.
- No standalone frontend hosting, full ERP port, or Python function transpiler
  is part of this amendment.

## Authoring and commands

`evlet setup PROJECT` creates `apps/`, `instances/` and an isolated `.venv`,
installs the framework and validates the interpreter before returning success.
Use `--framework /reviewed/evlet.whl` for an offline or development framework
artifact. Failed setup removes only the directory created by that command.
Project commands automatically re-execute in this environment. Existing projects
without the environment marker retain their explicitly selected interpreter.

```sh
evlet setup example
cd example
evlet app selling
evlet build --trust
evlet instance local
evlet install selling --instance local --trust
evlet dev --instance local --watch
```

A new app has one Python package root: `apps/selling/__init__.py`, `models/`,
and `pyproject.toml`. `reports/`, `services/` and `migrations/` are
optional. Its generated setuptools configuration supports ordinary imports,
editable installation, source distributions and wheels without an import-path
change in the caller. Build/install prepare declared Python dependencies and
editable app packages only inside the managed project environment. Existing
projects must prepare their chosen environment explicitly. No global Python
environment is modified automatically.

Use `evlet app NAME --legacy` for the nested `NAME/NAME/hooks.py` scaffold.
Existing `AppDefinition`, `ChildTable`, `Action` and interval schedules continue
to work. Creating a flat app does not install it into an instance.

`evlet app --get URL` first clones and validates static metadata. It then asks
for trust before executing imports, package hooks or frontend preparation.
`--trust` supplies that authorization noninteractively. Refusing leaves the
reviewable source fetched but explicitly unbuilt. It does not activate anything.
Only the fetched app and its local dependency closure are prepared. Review
external dependencies too: this is trusted code execution, not a Python sandbox.
`evlet build --trust` prepares all local apps and publishes a generation without
opening instance databases. Pip failure leaves acquisition/build incomplete.

## Identity, discovery and builds

Models default to `app.ClassName`; `resource_id = "app.StableName"` provides an
explicit app-owned identity. `storage_name` separately controls the SQL table and
existing `/api/table` alias. It must use lowercase letters, digits and
underscores, because it is also the `/app/...` route. Existing table names do not
change automatically.
Nested child storage defaults to `parent_table_collection`. Two parents may
therefore each define a nested `Item` class.

Discovery recursively imports owning modules in `models/`, `reports/`,
`services/` and `migrations/`. Re-exporting a service does not register it again.
Service decorators require synchronous module-level functions. Unresolved
targets, resource collisions, invalid fields/expressions, dependency cycles,
invalid service signatures and cron expressions fail before activation.

`.evlet/builds/HASH/manifest.json` is private server output. It includes resolved
models/storage, client expression structures, policy references, dialogs,
reports, endpoints, schedules, native descriptors and file hashes. The build
fingerprint includes framework/compiler identity, Python version, app sources
and installed declared dependency versions. Source copies and native assets
are published together by an atomic rename, followed by `current.json`.
An unchanged generation is verified and reused. Deleted inputs change the hash.
Generated caches, tests, VCS files and build outputs do not affect app hashes.
Symlinks in included source are rejected.

Runtime imports use the generation's source copy, including late imports from
actions/jobs. Active ASGI/managed runtimes pin their generation; replacing that
package with another generation in the same process is rejected. Watch mode
restarts the process for Python, TS/JS, JSON/TOML/CSS changes and additions or
deletions. There is no mixed-generation last-good fallback after a failed restart.
Embedded hosts must use ASGI lifespan or `activate_generation(app)` while serving.
`serve` verifies source locks and schema before requests; it never applies app
migrations. Installing or migrating still requires the selected instance lock.

## Documents, fields and exact expressions

Stored `Model` records remain immutable snapshots. New contextual operations
construct a separate `Draft` with typed fields, parent/source references and
owned rows. Keep application code on the contextual API:

```python
with app.business.context(user) as context:
    order = context.create(SalesOrder, title="SO-1", children={"items": rows})
    context.update(SalesOrder, order["id"], warehouse=warehouse_id)
    context.action(SalesOrder, order["id"], "confirm", note="Ready")
```

`get`, `list` and `count` enforce model/row/field permissions. Lists are bounded
to 100 rows per request. `create`, `update`, `action`, `delete` and `enqueue`
share the existing transaction. Direct `Database`/`Session` use remains a trusted
low-level escape hatch, not a permission-aware business API. The context's
private members are framework internals.

`Child` plus `Table(ChildType)` creates an exclusive SQL child table with owner,
stable ID and row order. Direct child HTTP CRUD is unavailable. Omitted
collections are retained; an explicit empty collection requests removal.
Foreign/repeated IDs and owner writes are rejected. Hidden rows are retained
when the client replaces its visible subset. Collections over 100 rows fail
explicitly. `_key` identifies an unsaved row; successful saves return `_row_ids`.
PATCH/actions/deletion of new documents require the latest `If-Match`; missing
revisions return 428 and stale revisions return 409. Legacy callers retain the
existing optional revision behavior.

The common field vocabulary adds:

- `default=Ref("parent.warehouse")`: copy when creating a row.
- `compute="qty * rate"`: derive a protected value; stored unless `stored=False`.
- `fetch="item.description"`: copy a readable linked field. Editable suggestions
  preserve edits until the source changes; protected fetches are rechecked on
  save. Saved unchanged editable snapshots are retained. A newly submitted
  non-null suggestion is explicit input; an old unchanged suggestion alongside
  a changed link is refreshed.
- `filters={"company": Ref("parent.company")}`: constrain Link choices and
  verify selection in preview and save. Missing filter inputs defer search.
  The selected ID is retained and flagged, never silently cleared. The initial
  policy rechecks saved selections on every complete write, including historical
  unchanged selections. Read-only hydration does not resave them.
- `visible_if`, `required_if`, `read_only_if`: shared conditions. Required and
  read-only rules also run on the server.

Expressions use bounded literals/references, arithmetic, comparison, null checks,
lazy Boolean/conditional operations and `sum/count/min/max`. No arbitrary calls,
comprehensions, imports or source evaluation are accepted. Numeric fields use
exact decimals. Intermediates allow at most 40 significant coefficient digits
and 36 fractional places; division rounds to 18 places, half even. Target fields
round half even to their declared scale and enforce signed scaled-storage bounds.
New document integers and integer results must fit `±(2**53-1)`. Legacy database
integer storage remains 64-bit. Empty sum is zero; empty min/max is null; missing
inputs remain unknown; division by zero and overflow are errors. Shared JSON
vectors and authenticated document fixtures cover both evaluators.

## Queries and preview

```python
stock = Query(
    "stock.StockBalance",
    filters={"item": Ref("item"), "warehouse": Ref("warehouse")},
    fields=["actual_qty", "reserved_qty"],
)
available_qty = Decimal(
    compute="stock.actual_qty - stock.reserved_qty", stored=False,
)
```

The public `Query` declaration is internally `ReactiveQuery`; the existing
`evlet.query.SqlQuery` SQL compiler (formerly `evlet.query.Query`, still importable under that name) is unchanged. Model sources must match zero or
one row; multiple matches are an error. Function sources use
`Query(function, fields={"qty": Decimal(), ...})` and explicit `@depends(...)`.
A single virtual field can instead use `Decimal(query=function, stored=False)`.
`items.*.qty` includes row membership and each row quantity. Functions receive
the effective unsaved draft, immutable row collections and a read-only context.
They return declared values and cannot write/enqueue through that context.

`POST /api/_draft/TABLE` accepts `{id, values, revision}`. The server merges
authorized inputs with protected current data, tolerates incomplete input and
returns `{values, errors, states, revision}`. Values include stable row keys
and permitted `_sources`. States distinguish required/missing input, empty,
ready, denied and error; the browser tracks pending and rejects obsolete replies.
Identical declared sources are deduplicated within that authorized request.
There is no global/user-shared result cache. This batches a document evaluation;
it does not promise one SQL query for arbitrary functions or automatic external
stock subscriptions. Volatile facts are checked again on complete operations.

Models with tables, sources, build resource IDs or `permissions` (declared on
the class, a base class or a mixin) deny access to roles they do not name.
Computed or virtual fields and `@action` alone do not opt a legacy model into
this default, and only such governed models treat `hidden=True` as unreadable.
Owned children without their own `permissions` follow their document: read
with document read, create with document create or update, and update/delete
with document update, because editing lines edits the document.
Hidden fields and derivations with inaccessible dependencies are withheld from
client metadata/results. Child row scopes prevent incomplete client aggregates
from masquerading as authoritative totals. Legacy permissions remain unchanged.

## Validation, actions and changes

After merging inputs, the operation runs an explicit action if requested,
settles authoritative values, validates children/parent, persists deltas and
calls optional `changed(change)` before commit. `validate()` is the sole model
validation convention. Actions use `@action`, explicit allowed roles and typed
scalar arguments (`str/int/bool/float`); the native UI presents those inputs.
Protect status fields with `read_only=True` when an ordinary PATCH must not
bypass an action. Declaring `@action` makes a model a document: its PATCH,
actions and deletion then require `If-Match`, and it gains draft evaluation.
Legacy `Action` registrations on a document model keep their legacy route and
also require `If-Match`; their trusted handlers bypass document settlement and
`validate()`.
A legacy `ChildTable` under a document parent is saved through the document
route, which sets the parent link itself and ignores a client-supplied one.

`Change` contains operation, actor, immutable before/after snapshots, changed
stored fields and added/updated/removed/reordered child information. Deletion
uses `operation="delete"`, a before snapshot and `after=None`. A true no-op,
virtual query refresh or related-only action does not fire a source reaction.
The source draft is frozen during its reaction; resaving it and cascades deeper
than 16 operations fail. A reaction error rolls back the source, related writes
and queued intents. External I/O does not belong inside a reaction.

## Dialogs and reports

A nested `Dialog` exposes one source action without a second empty method.
Implement `load(source)`, optional `validate()` and `submit(source)`. Fields and
tables are transient, with no business SQL tables. `parent` in a dialog row is
the dialog; `source` is the authorized persisted source snapshot. Load/evaluate
never save it. Submit requires its current revision and a scoped idempotency
key. A retry returns the stored outcome only for identical input and an actor
who still has source access. Conflicting input under the same key fails.
The operation uses the existing serialized write transaction, including the
PostgreSQL advisory transaction lock.

A `Report` defines title, roles, fields, typed `Column`s, `autorun` and `run()`.
Set `roles = ('admin', 'editor')` to name the roles that may see and run it.
`permissions` is an older alias for `roles` and still works. Use one of them.
Unlike `Model.permissions`, it is a tuple of role names, not a role to `Rule` map.
Its read-only context provides `limit`/`offset`. Return a bounded
`{"rows": [...], "total": matching_count}` with declared column names/types.
`Column(total=True, unit="AED")` adds explicitly labeled current-page totals.
No mixed-unit total is inferred. Filtering/evaluation is separate from running.
Manual mode runs only on Run; old results retain their executed snapshot and a
changed-input notice. Autorun waits for valid inputs and debounced evaluation.
Pagination uses the executed snapshot. Exports, pivots and charts are outside
this initial report contract.

## Services and background work

`@api(permission=(Model, "read"), method="GET")` exposes a typed, authenticated
read-only endpoint. POST is the default. Routes are
`/api/_services/app.services.relative.module.function`; an explicit `name`
provides an app-qualified stable alias. Only compiled endpoints are callable.
Input is bounded by the existing 64 KiB body limit, output by 1 MiB, and existing
CSRF/origin/session/tenant guards apply. Capability permission does not grant
access to every row; use the supplied contextual API for reads and writes.

`@cron("0 8 * * 1-5", role="editor")` uses the existing durable queue.
Instance `timezone` defaults to UTC; a job can override it. The grammar supports
numeric lists, ranges, steps and `*` in five fields, Sunday 0. Restricted day of
month and weekday use OR. Nonexistent DST times are skipped; repeated times run
at their first occurrence. Downtime coalesces to one occurrence, then the next
future time. Activation registers schedules without executing/enqueueing them.
Reload preserves next-run state and operator-disabled schedules; changed
definitions reconcile forward, removed calendars are disabled, and history is
retained. The schedule context is a synthetic identity with the declared role,
not an implicit administrator. Queued effect intents retain that limited role;
the worker rechecks target access under current policies before execution.

Short `Task` handlers retain `handler(tx, job_context, payload)`. For external
effects use `Task(..., external=True)` with `handler(job_context, payload)`.
Enqueue its intent within the source transaction. The worker validates actor
and target before starting, releases the database transaction during external
I/O, then records completion only under its current unexpired lease. Supply
finite network deadlines below the lease and pass `job_context.idempotency_key`
to the external receiver. Leases fence completion; they cannot undo an external
effect or guarantee exactly-once delivery. Retries and receiver deduplication
remain necessary. No new broker or worker service is required.

## Upgrade and recovery

Keep existing migrations frozen. Moving an app from nested to flat layout changes
its source fingerprint and requires explicit trust/migration of the selected
instance, even when its SQL schema is identical. Keep class/storage names and
historical migrations stable. When converting an existing child table, specify
`Table(Row, storage_name="existing_table", owner_field="existing_owner")` and
add `evlet_position` with an explicit `AddField` migration/default of zero.
Existing IDs/owner links remain intact; ties initially sort by ID. The migration
test retains the old history checksum and records.

Installation stages a database copy, preserves a backup and journals the final
database/lock publication. Interrupted installs use the existing recovery path.
Restore the matching database and `apps.lock` together. Never treat successful
build/acquisition as migration or activation. Stop serving before installing.
No live deployment, customer migration or package publication is part of this
change.

## Reference and verification

`examples/amendment` is a flat, installable app with masters, Sales Order/owned
items, exact totals, stock preview, a source-bound Work Order planning dialog,
manual/automatic reports, nested services and cron. It is a
framework fixture, not a complete manufacturing product. Planning rechecks row
ownership, item/company/unit/BOM, remaining non-cancelled commitments, source
revision and idempotency, then creates all selected Work Orders together.

The acceptance groups are covered by these focused suites in addition to the
existing regressions:

| Plan area | Verification |
| --- | --- |
| Packaging, environment, discovery | `test_cli_lifecycle.py`, `test_builds.py`, installed `check_package.py`, `smoke_cli.py`, `check_flat_package.py` |
| Definitions, expressions, children, permissions | `test_documents.py`, `test_document_boundaries.py`, shared document fixtures/vectors, existing business/security suites |
| Dialogs, reports, APIs, cron | `test_forms_services.py`, reference app end-to-end test, native document/report tests, existing queue tests |
| Build failure, generation pinning, recovery | `test_builds.py`, existing install/backup/recovery and reload tests |
| Adapter compatibility | Full Python suite, PostgreSQL regression suite plus declarative document rollback/revision test |
| UI and distribution | Native frontend suite/typecheck/build, installed-wheel HTTP smoke, clean flat-app wheel/sdist/editable imports |

Run `python -m scripts.generate_document_fixtures` only after an intentional
contract change and inspect the diff. Release checks and exact run results are
recorded in [the release notes](internal/amendment-v2-release-notes.md). This amendment does not establish production
readiness or replace the existing deployment limits.
