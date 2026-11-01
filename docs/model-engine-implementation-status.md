# Model engine implementation status

Ledger for the finite-v1 model engine handoff (2026-11-01). Contract:
[finite-calculations.md](finite-calculations.md).

## Baseline

| Item | Value |
| --- | --- |
| Branch | `feat/model-engine`, on top of `fix/low-severity-followups` (PR 31 review fixes and low-severity follow-ups) |
| PR 31 | Open, not merged. Head `509f7a3`; the follow-up branch adds `9f9f7aa`..`f6e3f03` |
| Engine base commit | `f6e3f03`; engine head is the latest `feat/model-engine` commit |
| Main at handoff | `36a60c2` |
| Toolchain | Python 3.12.16, Node 24.16.0, macOS arm64 |
| Baseline tests | 460 passed, 35 skipped (PostgreSQL DSN unset); frontend 198 passed |

Nothing was merged, reset, deployed or published. No customer database was touched.

## Decisions (ADR)

| Question | Decision |
| --- | --- |
| Language/profile | `finite-v1` with `finite40-hu18-v1`: 40 digits, 36 places, division to 18 places HALF_UP, explicit `round` HALF_UP, strict target scale. Legacy formulas stay `legacy-document-v2` (HALF_EVEN) through the old compiler. |
| Selection | `Meta.calculation_language`/`numeric_profile`, or any expression compute in the root tree. Mixed legacy and finite formulas in one root are rejected. |
| Field equality | Field-to-field `==` stays identity based (dictionary safety); field-to-literal builds an expression; compare two fields with `F()`. |
| Virtual precision | Only `Decimal(scale=None, stored=False, compute=...)`. |
| Module layout | One public module `evlet/calc.py` (authoring, compiler, numbers, evaluator, session) and `frontend/src/runtime/FiniteEngine.ts`. Integration lives in existing `models`, `documents`, `business`, `permissions`, `database`. |
| Numeric kernel in the browser | A HALF_UP kernel in `FiniteEngine.ts` beside the legacy HALF_EVEN `DecimalValue.ts`, which is unchanged so legacy previews keep their behaviour. |
| Nesting | Allowed only under finite-v1 roots, depth 8. Legacy roots keep one level. |
| Wire metadata | `_calculation` in POST/PATCH bodies, `calculation` in `/api/_draft`; mismatch is 409. |
| `_key` scope | Unique across the whole document so the flat `_row_ids` map cannot collide. |
| Limits | As specified (128/32 expression, 8 depth, 100 per collection, 1,000 owned rows, 50k nodes, 250k edges, 1M steps, 128-char decimals), embedded in plans and checked by both runtimes. Phase 3 (01-11-2026): each table carries its `max_rows` (up to 1,000) and roots allow 5,000 owned rows. |
| Permissions | A result is readable only when its whole input closure and complete collections are readable; otherwise withheld (no declassification policy yet). |

## Work packages

| Package | Status | Evidence |
| --- | --- | --- |
| W0 baseline | Done | This ledger. |
| W1 contracts | Done | `evlet/calc.py`, this ADR, `docs/finite-calculations.md`. |
| W2 Python compiler/numbers | Done | `tests/test_finite_expressions.py` (independent `decimal` reference, cycles, scopes, types, states). |
| W3 browser evaluator | Done | `FiniteEngine.ts`, `frontend/src/tests/finite-engine.test.ts` replaying Python fixtures; fingerprint parity. |
| W4 graph and batches | Done | Row handles, membership/filter edges, incremental settle with equality pruning, nested batches, rollback, one publication, reentrancy guard, explain, disposal; randomized incremental-vs-full checks. |
| W5 nested storage | Done (SQLite) | `tests/test_nested_documents.py`: create/reload/update/delete, owner links, rollback, hidden rows, subtree permissions, formula-upgrade guard. **PostgreSQL is not covered**: `tests/test_postgres.py` has no finite or nested cases yet. |
| W6 security/versions | Done, with limits | Computed-write guards, legacy action refusal, raw-write guard, per-record plan versions (409 + explicit `recalculate`), derivation permissions, field-filtered plan projection. Client plan pinning is optional and the SolidJS client does not send it. External `Query` sources in finite roots are deferred. |
| W7 UI bridge | Minimal | Deferred by the user: Evlet UI becomes its own framework next. The SolidJS form copies finite results from `/api/_draft` previews (two-line change, covered only by existing form tests, not browser-verified); nested tables below the first level are not editable there. `FiniteEngine.ts` is not wired into screens. |
| W8 native controls | Not started | Same as W7. |
| W9 native parity | Not started | Same as W7. |
| W10 package/perf | Done for the engine | Wheel built locally and installed in a clean venv outside the checkout; with Node off PATH, `scripts/smoke_finite.py` saved a nested document over real HTTP via uvicorn and loaded local assets. `check_package.py`, `smoke_cli.py` and `check_flat_package.py` also passed locally. The smoke step was added to CI (not yet run there). |

Gates: **engine completion** is met for SQLite with the limitations below. **Native UI
cutover** is not started.

## Tests actually run

Final run on the branch head (macOS arm64, Python 3.12.16, Node 24.16.0):

```
.venv/bin/python -m pytest                       -> 521 passed, 35 skipped (PostgreSQL DSN unset)
cd frontend && npm test                          -> 268 passed, 1 skipped (opt-in benchmark)
cd frontend && npm run build                     -> typecheck and build passed
.venv/bin/python -m build --no-isolation         -> wheel and sdist built
python -I scripts/check_package.py               -> passed (installed wheel, clean venv)
python -I scripts/smoke_cli.py                   -> passed (installed wheel)
env -i PATH=/usr/bin:/bin python -I scripts/smoke_finite.py -> passed, Node absent
python scripts/check_flat_package.py dist/*.whl  -> passed
```

PostgreSQL tests were skipped locally; CI's PostgreSQL jobs run the existing suite, which
has no finite-v1 or nested cases.

## Pre-merge review (second round)

Four independent reviewers covered the number engine, nested persistence, security and the
earlier framework changes; a fifth checked the fixes. Fixed, each with a regression test that
fails on the old code:

* Engine: compile-time IR limit check, plan arity and literal shape checks, declared bounds and
  the scaled range enforced in both runtimes, failed commands inside a batch roll back, moves
  validate before removing, async batch callbacks refused up front, field equality with
  non-operands keeps Python semantics.
* Persistence: legacy roots no longer hit the finite row cap, full finite documents can swap
  rows, `/api/_draft` no longer returns 500 for invalid values or ids.
* Security: updated child rows are re-checked against the row scope, bounded results over
  hidden inputs are calculation errors (not 500), the schema no longer describes unreadable
  tables or fields.
* Framework: exact negation in legacy formulas, `-P` for the build subprocess, removed and
  restored cron schedules re-enable, a field named `storage_name` no longer breaks registration.

## Migration effects

* No app table, migration or checksum changed. Formula-only changes do not alter
  migration definitions.
* Apps with finite-v1 plans get one framework table, `evlet_calculation_versions`,
  created with `CREATE TABLE IF NOT EXISTS` at startup.
* Existing finite-v1 records saved before that table existed (none outside this branch)
  would need one explicit `recalculate` write.
* `evlet_calendar_schedules` gains a `retired` column, added at startup when missing.
* Nested tables create new child tables (`<parent storage>_<table>`) through the usual
  explicit migrations.

## Performance (pure engine, no database)

macOS arm64, Python 3.12.16 (`scripts/benchmark_finite.py`) and Node 24.16.0
(`FINITE_BENCH=1 npx vitest run src/tests/finite-benchmark.test.ts`), 50 edits each:

| Case | Rows | Nodes | Python cold / p50 / p95 ms | Node cold / p50 / p95 ms | Evaluated |
| --- | --- | --- | --- | --- | --- |
| Invoice row edit | 100 | 605 | 68 / 0.78 / 0.79 | 7.5 / 0.38 / 0.46 | 9 |
| Invoice parent toggle | 100 | 605 | 68 / 5.7 / 5.7 | 4.4 / 1.2 / 1.5 | 404 |
| Project task edit | 1,000 | 1,022 | 110 / 0.65 / 0.67 | 5.2 / 0.71 / 0.81 | 2 |
| Project rate fan-out | 1,000 | 1,022 | 110 / 8.2 / 8.3 | 3.6 / 1.9 / 2.2 | 1,011 |

Both runtimes report identical evaluation and step counts. A 10,000-row case needs raised
limits, which plans refuse by design; it was not run.

## Simplification audit quick wins

* Saved documents now record a formula hash (formulas plus the type, scale and bounds of
  the fields they read or write) instead of the whole plan hash. Unrelated model edits no
  longer make every saved document return 409. At startup, rows recorded under the active
  plan's full hash move to its formula hash. Rolling back to an older release makes those
  documents return 409 until they are recalculated.
* The server checks each plan once when the registry is built, not on every save.
* `settle()` is split into named steps. On a save, a Link whose filters name a field the
  user cannot read now returns 403 before any filter is compared (previously a mismatch
  on an earlier filter could return a 422 first).
* Deleting a legacy `ChildTable` parent with more than 100 children now deletes them page
  by page. Document-pipeline deletes still refuse collections over 100 rows with a 422.
* `Report.roles` is the documented name (`permissions` stays as an alias), so a report
  field cannot be named `roles`.

## Known limitations

* `Query` sources and query fields are not supported in finite-v1 roots.
* Moving a row between parents over HTTP is delete-and-create (new IDs); the session API
  supports in-document moves.
* `tx.maintenance()` does not verify or recompute calculated values.
* No dry-run backfill command for formula or profile changes; stored values are never
  rewritten automatically.
* Client plan projection is all-or-nothing per root; partial plans are not produced.
* Hidden rows that fail calculation block saves for users who cannot see them; the error
  is reported only as a generic form error.
* Rounded allocations are not reconciled; expose a named residual field (documented).
* Nested documents load one query batch per row: a 100 x 9 tree costs about 2,200 SQL
  statements per GET and 8,600 per title-only PATCH (bounded by the 1,000-row cap).
* Acceptance items still missing or only partial: T21 handled by documentation, T26/A58
  (UI), A37/A51/A52 (browser save-mapping and async-reply flows), and partial T10, T24,
  T27, T28, A03, A06, A13, A30, A55, A56, A60.
* No native UI integration (W7-W9).

## Phase 1: expression extensions (01-11-2026)

Branch `model/01-expressions`. All additions are new operations in finite-v1; no plan,
expression or compiler version changed.

* **Auto-rounding.** `Decimal`, `Currency` and `Integer` take `rounding="half_up"` or
  `"half_even"` (defaults: Currency and Integer half up, Decimal half even). A finite result
  is quantized to the target scale instead of failing with `COMPUTE_SCALE_ERROR`; range and
  bounds errors stay errors. Each plan field carries `rounding`. `round(x, n)` still rounds
  half up. Changed test: `test_bool_casts_and_scale_targets` now expects rounded values where
  it expected `COMPUTE_SCALE_ERROR`; the shared `states` fixture changed the same way.
* **Dates.** `days(n)`, `today()`, `date +/- days(n)`, `date - date` and date comparisons.
  IR operations `date_add`, `date_sub`, `date_diff` and `today`. DateTime references keep
  their earlier rules (equality and `coalesce` only); a Link path cannot end at a DateTime.
* **Link paths.** `F("customer.region.grace")`, at most three Links, validated at registry
  build. IR operation `hop`; plan tables list their `lookups`. Hosts supply values through
  `set_lookups` / `setLookups`; the server reads them in the save transaction with one
  query per looked-up model per Link step (chunks of 100 IDs); each record is queried at
  most once per settle, found or not. `derivation_allowed` now
  requires every path field to be readable and no row scope on looked-up models.
* **`when(cond, a, b)`.** Only the chosen branch is evaluated; a null or pending condition
  is pending.
* **Hashes.** Formula hashes of existing models are unchanged: `rounding` enters the
  formula hash only when it differs from the type default. A test pins the hashes of the
  shared fixture models. Plan fingerprints (`model_schema_hash`) do change, because plan
  fields now carry `rounding`; browsers reload the plan as with any deploy.
* **Fixtures.** New shared scenarios for rounding ties and negatives, dates (leap days,
  range limits, invalid dates, `today`), Link paths (null link, a withheld lookup supplied
  as pending, rejected lookups) and `when` (pending condition, error in the unchosen branch).
  Every step also records `lookupRequests()`.

Known limitations of this phase:

* `parent.customer.x` paths are not supported; relay the Link first.
* A stored result that uses `today()` is fixed at its last save.
* Arithmetic with a null path value stays pending, so stored results over an optional Link
  need `coalesce` or `when`.
* The browser form still previews through `/api/_draft`; no browser screen calls
  `setLookups` yet.
* A stored result that reads a Link path keeps the value from its last save; it is updated
  on the next write of the document when the linked record changes.
* Child rows load with about one SELECT per row (about 1,000 statements for 90 linked
  rows); batched loading is planned for Phase 3.

## Phase 2: initial suggestions, field rules, Recompute and new types (01-11-2026)

Branch `model/02-rules`, stacked on `model/01-expressions`. Contract:
[finite-calculations.md](finite-calculations.md#initial-suggestions) and
[model-engine.md](model-engine.md#more-field-types).

* **`initial=`.** A finite expression that suggests a value on create and when a document
  input it reads changes, unless the caller supplied the field in the same write. Lookups and
  `today()` do not trigger it. Pending suggestions wait for their value; locked fields are
  not overwritten. On an update the server builds the session from the saved document and
  applies the write as one batch, so it decides like a browser session.
* **Rules.** `applies_when`, `required_when` and `locked_when` on editable fields, enforced
  on create and update through HTTP, `Context` and actions. `applies_when` false clears the
  value inside the session, so dependent calculations see null. Undecided rules fail closed:
  the field applies, is required and is locked. Locks compare the caller's values (after
  input validation, before engine writes) with the saved row.
* **Plans.** Initial suggestions and rules are plan entries with a `kind` key, named
  `field:kind` in the graph and ordered with the calculations. Plans and formula hashes of
  documents without them are unchanged. Formula hashes include `applies_when` (it can clear a
  value calculations read) but not `initial`, `required_when` or `locked_when`, so changing
  those does not ask saved documents to recalculate. `ruleStates`/`rule_states` and
  `observeRules`/`observe_rules` expose rule states per row.
* **Permissions.** `Policy.expression_allowed` checks a role against every input of a
  calculation, initial or rule. An initial over unreadable inputs withholds its field, like a
  calculation. A rule over unreadable inputs is enforced but not shown in `/api/schema` and
  not shipped in the browser plan; leaving out a suggestion or `applies_when` on a readable
  field stops the plan from shipping. Lock messages explain a pending rule only to roles that
  can read its inputs.
* **Schema.** Every field in `/api/schema` gains `stored` and `compute`, `initial`,
  `applies_when`, `required_when`, `locked_when` (finite IR or null). The registry's neutral
  schema (`ui_schema`) is unchanged except `currency` on Currency fields that declare it.
* **`Recompute(model, fields)`.** Runs after the batch's structural changes inside the
  migration transaction; failures roll back the batch. Updates recorded formula versions
  when a batch recalculates every stored result of a document.
* **Types.** `Phone`, `Percent`, `Time`, `Duration`, `Tags` and `Reference` (two
  columns and no SQL foreign key; wire type `reference`). Fields can now occupy more than one column
  (`Field.columns`, `to_columns`, `from_columns`); SQLite and PostgreSQL schemas, checks,
  `AddField`, writes and filters use them. `Currency(currency=...)` is metadata only.
* **Fixtures.** Shared scenarios `initial-and-rules` and `loaded-offer` record rule states at
  every step; the TypeScript engine matches them and rebuilds to the same snapshots.
  Generated fixtures for `/api/schema` (`test-business-schema.json`, `ui-fixture.json`,
  `document-contract.json`) gained the additive field keys.
* **Changed tests.** `tests/test_builds.py` adds assertions for the amendment Quote's new
  fields; no existing assertion changed.

Review fixes (01-11-2026):

* Locks on an update are decided on the document as saved, in the server and in sessions
  (`locks`, `rebase()`); a write that unlocks and changes a field is refused. On create a locked
  field accepts its default or its initial suggestion.
* Setting a source to its current value is not a change, so resending an input does not
  suggest again (HTTP, `Context` and sessions).
* A `ChildTable` whose rows have a finite plan is refused at registration.
* Shipped plans name only lookup fields and owned tables the role can read; the schema `order`
  is filtered the same way.
* A change that would clear a locked field through `applies_when` is refused, and sessions keep
  the saved value.
* Recompute stores not-applicable values as null; `RenameField` renames both columns of a
  `Reference`; the `Ref()` field shortcut was removed and the wire type is `reference`.
* HTTP filters `about` and `about__eq` on a reference check the named model; other forms need
  every model.

Known limitations of this phase:

* `required_when` over an empty number is required until the number is filled in; use
  `coalesce` when that is not wanted.
* A saved value is cleared when `applies_when` turns false, so toggling a condition back does
  not restore it.
* The SolidJS form does not call `ruleStates` and does not refresh suggested values from the
  preview; the new UI will.
* Recompute uses the UTC date for `today()` and is not replayed on baseline adoption.
* Reference fields are not validated against a list of allowed models.

## Phase 3: owned tables (01-11-2026)

Branch `model/03-lines`, stacked on `model/02-rules`. The owner kept the name `Table(Child)`;
there is no `Lines` alias.

* **Options.** `Table(Child, min_rows=0, max_rows=100)`. `max_rows` goes up to 1,000;
  `required=True` means `min_rows=1`. Conflicts and bad values fail at declaration; a
  `max_rows` above 100 under a legacy root fails at registration. Legacy roots and
  `ChildTable` keep 100.
* **Engine limits.** `collection_rows` is now 1,000 (the most a table may declare) and
  `document_rows` 5,000, in both runtimes. Plan table entries carry `min_rows` and
  `max_rows`; both engines enforce each table's `max_rows` and reject invalid values in a
  plan. This changes every plan's `model_schema_hash` but not formula hashes, so saved
  documents do not need recalculation. Row placement while loading a document is now linear.
* **Row changes.** Collections accept `{create, update, delete, order}` change sets in
  addition to full lists, at every depth, in one transaction. `order` rearranges the listed
  rows within the places they hold. Positions follow the final order (stored values kept where they fit); rows whose
  only change is their position are written in one statement per chunk. Hidden rows keep
  their places and stored positions.
* **Reading.** Details embed at most 100 rows per collection plus `children_pages` for larger
  ones. New route `GET /api/{model}/{id}/lines/{name}[/{row id}/{name}...]?limit=&offset=`.
  `/api/schema` child entries gain `min_rows` and `max_rows`.
* **Batching.** Saved rows load one table level at a time (`owned_rows`, window functions for
  per-owner limits), with one row-scope statement per level (`Policy.visible`). Serialization
  checks Link and Reference targets per model (`Policy.serialize_many`). Link targets for
  validation, fetches and submitted values are read in batches (`LinkTargets`,
  `Policy.records`). Subtree deletes and position updates use `IN` lists. Permission rules are
  unchanged; per-row checks became per-collection checks with the same outcome.
* **Statement counts** (SQLite, authenticated HTTP, including session checks). Before, with
  100 rows: GET 607, title PATCH 2,021, one-row change 2,227. Before, 100 x 9 nested: GET
  2,407, title PATCH 9,221. After (with the review fixes): GET of 1,000 rows 9, title PATCH 30,
  one-row change set 32; 100 x 9 nested: GET 8, title PATCH 29. Tests assert upper bounds.
* **Changed tests.** `test_row_limits_apply_to_finite_roots_only_and_allow_full_swaps` now
  adds a 101st task to reach the per-table limit, because one extra row no longer exceeds
  the document limit. Generated fixtures (`finite-fixtures.json`, `test-business-schema.json`,
  `document-contract.json`, `ui-fixture.json`) gained the new keys, limits and the
  `table-limits` scenario. `tests/test_builds.py` adds a row-change step for the amendment
  Quote, whose lines now allow 500 rows.

Behaviour changes to note:

* A detail of a collection with more than 100 rows no longer fails with 422; it embeds the
  first 100 and reports the rest in `children_pages`.
* `Change.children[...]["updated"]` no longer lists rows whose only change is their position;
  `reordered` still reports the reorder.
* When a full list is saved, rows hidden from the user keep their places; before this phase
  they moved to the end.

Review fixes (01-11-2026):

* ETags (`Business.record_revision`) cover every readable row again, read with the batched
  loaders; write routes compute them from the full detail and embed the first page. A stale
  `If-Match` after a change to row 101 or later fails with 409.
* A full list is refused (422) for a collection with more than 100 readable rows; use a
  change set.
* Hidden rows keep their places and stored positions for full lists and change-set deletes;
  other rows keep their stored positions when they still fit (`positions()`).
* The calculation session gets internal row keys, so a client `_key` equal to a saved row ID
  no longer fails with "Repeated row key".
* Plans with `min_rows` above `max_rows` are rejected by both engines.
* The row-limit side channel for hidden rows is documented with the other residual signals.

Known limitations:

* Change sets update one row per statement.
* Creating a document still inserts one row per statement (about 1,000 statements for 1,000
  rows), and the 64 KiB request body limit allows only short rows in one request.
* The paging route reads one collection; it has no filters or sorting other than position.
* The SolidJS form does not use change sets, paging or `children_pages`; the new UI will.

## Phase 4: states, moves and model actions (01-11-2026)

Branch `model/04-state`, stacked on `model/03-lines`. Guide: [states-actions.md](states-actions.md).

* **State and Move.** `State(choices, default=..., moves=..., locked=..., final=...,
  voidable=...)` is a read-only `Select` (at most one per root document, never on Table
  rows). `Move("draft", "issued", "Issue", roles=..., when=..., run=...)` takes its states
  positionally. Unknown states, moves out of final states, duplicate moves between two
  states and voidable states that are not locked or are final fail at declaration; a `run`
  that is not a model method fails at registration. A model with a State is a document
  (`__declaration_mode__`) but does not become finite-v1 by itself.
* **Ordinary writes** may create a record only in the default state and never set it
  afterwards (field errors). A locked state refuses ordinary changes to the record and to
  its Table rows at every depth, through HTTP, `Context`, dialogs and services, except fields
  with `editable_when_locked=True`. Deleting a locked record is refused with 409.
* **Moves.** `POST /api/{model}/{id}/moves/{to}` and `Context.move()`. Order of checks:
  update permission and row scope as for updates, then unknown target 404, final state 409,
  no such move 409, role 403, guard 409. The move, `run`, settlement and rows commit in one
  transaction. `changed()` receives `"move"`. `evlet.states.moved()` is the empty hook
  where Phase 8 will write the Journal entry.
* **Actions.** `@action("Label", roles=..., inputs={...}, available_when=...)`. With
  `inputs`, fields clean the values and the signature is not read for types; without, the
  typed-signature rule stays. `available_when` failing is 409. Moves and actions bypass the
  State lock but not `locked_when` or any other rule. Legacy `Action` is unchanged.
* **Guards** compile with `compile_expression` against the root and are evaluated over an
  engine session of the saved document (with today and Link lookups read in the
  transaction). Roots without finite features get a plan compiled with
  `compile_plan(force=True)` only for this; `Registry.plans` and all hashes are unchanged.
* **Responses and schema.** Details of models with a State or a guarded action add
  `_moves`, `_actions` and `_locked`, outside the ETag; reasons over unreadable inputs are
  generic. `/api/schema` adds `state`, and action entries add `roles`, `inputs`,
  `available_when`, `guarded` and derived `arguments`. `Policy.reads_allowed()` is the shared
  readability check for rules and guards.
* **Playground.** The amendment Quote's `stage` is now a State (Send when the total is
  positive, Mark won, Mark lost, Reopen for administrators; sent, won and lost are locked; won
  is final) and the Quote has an `Extend validity` action with an input. Its lost reason is no
  longer required when lost, because it can only be entered after the move, and it is
  `editable_when_locked`.
* **Changed tests.** `tests/test_builds.py`: the Quote steps that set `stage` by PATCH now
  expect the State field error and use moves; the row-change step runs while the quote is a
  draft. The generated `document-contract.json` gained the additive action keys. New tests
  in `tests/test_states.py` and a PostgreSQL variant in `tests/test_postgres.py`.

Known limitations:

* A guard that reads owned rows or virtual results loads the whole saved document for a
  detail response when the caller cannot read every row (hidden collections or a row
  scope); otherwise it reuses the rows the detail read. Other guards read only the root.
* Draft previews do not report the State lock. The connector, the MCP server and the
  SolidJS form do not offer moves.
* Void and revise were added in Phase 5 (below).

Review fixes (01-11-2026):

* Ordinary writes on a locked record keep every saved value except `editable_when_locked`
  fields, at every depth, so linked prices and `today()` no longer reprice a locked record.
  A stored result reading an `editable_when_locked` field (finite or legacy, at any depth)
  fails at registration. `recalculate` on a locked record is 409. Moves and actions still
  settle normally.
* Attachment upload works on document models (it answered 405 before for models with
  permissions or calculations). Upload and delete are refused on locked records (409).
* Messages do not name the State to roles that cannot read it. Those roles still get
  `_actions`.
* Guards work on roots with legacy string calculations (legacy results are read as saved
  values). Guards that read no owned rows and no virtual results are decided on the saved
  root values without loading rows: a GET of a 1,000-row stated document now runs 8
  statements against 7 without a State (it was 15 to 17).
* `Session.create` and `Session.update` refuse State changes outside `maintenance()`.
* Action input names `name`, `model`, `record_id` and `self` are rejected; input fields are
  copied rather than renamed in place.

Lock and leak fixes (01-11-2026):

* The registration check for stored results follows legacy string formulas, virtual
  chains, Query fields and fetches through the legacy template graph's value inputs
  (`evlet.reactivity.template`), and finite results through their plan reads, including
  reads of fetched copies. `sum(lines.extra)`, `v = remarks * 2` (virtual) read by a stored
  `s = v + 1`, and `fetch="cust.name"` over an `editable_when_locked` Link now fail.
* Ordinary writes on a locked record settle with a kept plan (`Registry.kept_plans`) in
  which stored results are saved values, and legacy roots skip stored formulas and fetches.
  Rules and validation therefore read the values the record keeps; a linked limit that
  changed after locking no longer makes a field required.
* Those writes skip the formula-version check and leave the recorded version unchanged, so
  a locked record saved under older formulas stays editable. Moves and actions still check
  the version; the guide says how to recalculate.
* A role that cannot read the State gets one answer (409 `This record has no such move
  now`) for unknown targets, missing moves, role refusals and unmet guards.
* Guards that read rows reuse the rows the GET detail loaded when the caller reads every
  row without a row scope. A 1,000-row document with such a guard now costs 8 statements
  against 7 without a State (it was 13).

## Phase 5: void and revise (01-11-2026)

Branch `model/05-void-revise`, stacked on `model/04-state`. Guide: the "Void and revise"
section of [states-actions.md](states-actions.md).

* **Declarations.** A State with `voidable` states must have a `void` state that is locked
  and final, and no declared Move may go to `void`. `Meta.revisable = True` needs such a
  State; the engine then adds a read-only, indexed `revises = Link(<the model>, index=True)`
  field (each subclass gets its own), and `revises` and `revised_by` are reserved on that
  model. A unique and required field on a revisable model or its rows is rejected. `Meta`
  accepts `revisable`.
* **Void.** `POST /api/{model}/{id}/void` with `{"reason": "..."}` and `Context.void()`.
  Checks: model supports it (404), `void` permission and row scope (403 or 404), `If-Match`
  (428 or 409), body and reason (required, trimmed, up to 1,000 characters; 422), voidable saved state
  (409, without the state name for roles that cannot read it). One UPDATE writes the `void`
  state; nothing is recalculated and the plan version row is not touched. `changed()`
  receives `"void"`.
* **Revise.** `POST /api/{model}/{id}/revise` and `Context.revise()`, same checks with the
  `revise` permission plus create permission, reason required. Starts from a voidable state,
  or from `void` when the record has no revision (409 `This record already has a revision`
  otherwise). Predicts whether the caller's copy can be created (every row visible, create
  permission for rows, every Link readable) and answers 403 with a plain reason if not. Voids
  the original (unless already void), then builds a draft copy of every stored source value and Table row at every depth (rows read
  with full access, one statement per table level and chunk, positions kept), sets the
  default state and `revises`, clears unique fields, settles it through the normal pipeline and writes it with the
  usual create checks for the record and its rows. Answers 201 with the copy's detail,
  `Location` and `_original`. The original's detail gains `revised_by`, found through the
  index on `revises` (an index search on SQLite and PostgreSQL, tested). Attachments are not
  copied; the copy includes fields the caller cannot read or write.
* **Permissions.** `Rule.operations` accepts `void` and `revise` on models that support them.
  Per model and operation: admin (when its Rule allows read and update, plus create for
  revise) and every role whose Rule names it; naming a role never removes admin. A Rule
  naming `revise` without `create` fails at startup. `Policy.corrections` holds the result; `Policy.rules`
  is unchanged, and a test pins a hash of every create, read, update, delete and field
  permission of the Playground and Phase 4 test models to the value recorded before this
  phase.
* **Details and schema.** `_void` and `_revise` (allowed, reason) outside the ETag, for roles
  that can read the State. For those roles only, entries of voidable models gain `voidable`,
  `revisable`, and `void` (and `revise`) in `permissions`; other entries are unchanged, so the shared frontend
  fixtures did not change.
* **Hooks** in `evlet/corrections.py`, all empty and called inside the transaction:
  `reverse_effects(context, model, record_id, previous)` after the void state is written
  (Phase 6), `prepare_copy(context, model, original, copy)` before the copy settles (Phase 7
  clears Sequence numbers here; no longer empty once Phase 7 is stacked), `voided(context, model, record_id, previous, reason)` and
  `revised(context, model, original_id, copy_id, reason)` once each operation is saved
  (Phase 8 Journal; `voided` is skipped when a void record is revised again). A failure in any of them rolls the whole operation back (tested).
* **Playground.** The amendment Quote has a `void` state (locked and final), `sent` is
  voidable and the Quote is revisable; administrators may void or revise a sent quote.
* **Changed tests.** `tests/test_builds.py`: the Quote's final states are now `won` and
  `void`, and the flow revises a sent quote. `tests/test_states.py`: the Phase 4 Invoice keeps
  its declared move to `void` and so no longer declares voidable states. New tests in `tests/test_corrections.py` and
  PostgreSQL variants in `tests/test_postgres.py`.

Known limitations:

* The void reason is passed to the Journal hook but not stored until Phase 8.
* Other reasons a copy can fail (a row scope on the root that the copy's values leave, a
  fetched field the caller cannot read) are not predicted; they roll back with 403, 404 or 422.
* Revising a document copies its rows one statement per row, like creating one.
* The connector, the MCP server and the SolidJS form do not offer void or revise.

## Phase 7: document numbers (01-11-2026)

Branch `model/07-sequences`, stacked on `model/04-state`; restacked on `model/05-void-revise`
as `model/07-stacked`. Guide: [sequences.md](sequences.md).

* **Sequence.** `Sequence(format, scope=(), assign_on="create", date=None)` in
  `evlet/sequences.py`, exported from `evlet`. A read-only, nullable short text field with
  schema type `sequence`. The format (tokens `{n}`, `{n:0W}`, `{year}`, `{yy}`, `{month}`)
  is checked at declaration; scope fields (stored, required, not calculated), the `date`
  field (a stored Date) and `assign_on` (`"create"` or a state of the model) are checked at
  registration. A Sequence on Table rows, or read by a calculation, `initial` or field rule
  of the same document, fails at registration. A model with a Sequence is a document.
* **Assignment.** `sequences.assign()` runs in `operate()` after settlement and before the
  record is written: on create for `"create"` (or when the default state is `assign_on`),
  and on a move into `assign_on`. A number, once assigned, is kept (reopen, void) and fixes
  its scope fields and the period of its date field.
* **Counters.** `evlet_sequence(model, field, scope_key, period, last)` is created by
  `Business.initialize()` when a Sequence exists. One upsert with `RETURNING` takes the next
  value in the write transaction, so a rollback consumes nothing. Writers were already
  serialized on both adapters.
* **Read only.** Ordinary writes (HTTP, `Context`, dialogs, services) get a field error
  unless they send null on create or the saved value; draft assignment in actions and `run`
  is refused; raw `Session` writes raise outside `maintenance()`.
* **Storage.** A partial unique index on (scope fields, number) `WHERE number IS NOT NULL`
  is created with the table and by `AddField`, on SQLite and PostgreSQL, and the schema
  check reports a missing one. `AddField` refuses a non-null backfill;
  `sequences.backfill()` numbers existing rows from the same counters. The stored model
  definition records the scope only, so the format, `assign_on` and `date` can change
  without a migration.
* **Schema.** Field entries gain `sequence: {format, scope, assign_on, date}`. The SolidJS
  schema check accepts the `sequence` type (`frontend/src/schema.js`), so generated screens
  load models that declare one.
* **Hooks for later phases.** `sequences.clear_for_copy(model, values)` for revise
  (Phase 5); `sequences.assigned(...)`, an empty hook called after the write, for the
  Journal (Phase 8). Void keeps the number with no extra code.
* **Playground.** The amendment Quote has `number = Sequence("Q-{year}-{n:04}",
  assign_on="sent")`; `tests/test_builds.py` checks it is null until sent, refused on PATCH,
  kept when a lost quote is reopened and resent, and described in the schema.
* **Tests.** `tests/test_sequences.py` (format and registration checks, assignment on
  create and on a move, year and month resets from a date field, scope separation,
  rollback after assignment, deletes, read-only enforcement over HTTP, `Context`, actions
  and `Session`, uniqueness, parallel writers with separate connections, `AddField` and
  backfill, a missing index) and PostgreSQL variants in `tests/test_postgres.py`, including
  parallel sessions.

Known limitations:

* Without `date=`, the period is the day of assignment, not the creation date, for numbers
  assigned on a state. Records store no creation timestamp.
* `RenameField` of a Sequence does not rename its counter rows (documented).
* `{yy}` repeats every century and the unique index would then refuse a repeated number.
* The connector, the MCP server and the SolidJS form show the number but do not explain
  when it will be assigned.

Review fixes (01-11-2026):

* Every document operation (create, update, move, action, delete) runs in a savepoint on
  SQLite and PostgreSQL. A failure caught by trusted code inside one `Context` rolls back
  that operation only, so it leaves no counter step, state change or number. Integrity
  errors inside an operation are raised as `ConflictError`.
* A Sequence value sent by a role that cannot read the field is refused with 403 whatever
  it is; the scope and period errors are generic for such roles.
* `RenameField` of a scope field updates the stored scope. The State cannot be a scope.
* `next_number` skips numbers already held in the scope, so a planted number, a format
  change or a `{yy}` wrap no longer blocks the series.
* Raw `Session.update` refuses scope and period changes of an assigned number outside
  `maintenance()`, and raw `Session.create` assigns numbers due on create (period day in
  UTC).

Connection with void and revise (01-11-2026):

* `corrections.prepare_copy()` calls `sequences.clear_for_copy()`, so a revision starts
  without the original's number. `corrections.revise()` then runs `sequences.assign()` on
  the settled copy like a create: a number due on create (or on the copy's starting state)
  is assigned before the copy is written, and `sequences.assigned()` is called after it.
  Other copies are numbered by the move into `assign_on`.
* Void writes only the State, so the number stays, and a record voided before its number
  was due stays null. Neither operation goes through raw `Session` writes, so the Session
  guards on scope, period and State do not apply to them.
* `Context.void()` and `Context.revise()` run in the same per-operation savepoint as the
  other operations; a revise that fails after its copy was numbered and is caught by
  trusted code gives the number back.
* The Phase 7 test Invoice now reaches `void` through `void()` only, because Phase 5
  rejects a declared move into `void` when voidable states exist. New tests in
  `tests/test_sequences.py` (with PostgreSQL variants) and `tests/test_builds.py` cover a
  revised copy's fresh number, numbers kept on void, a void before assignment, a revisable
  model numbered on create, and gapless numbers after a caught revise failure.

## Phase 9: measures and the aggregate API (01-11-2026)

Branch `model/09-measures`, stacked on `model/04-state` (built alongside Phases 5 and
7); restacked on `model/07-stacked` as `model/09-stacked`. Guide: [measures.md](measures.md).

* **Engine.** `evlet/measures.py`. `Count`, `CountDistinct`, `Sum`, `Min`, `Max`, and
  `Day`, `Month`, `Year` for grouping, are top-level exports. One SQL statement per call.
  Sums add stored scaled integers; SQLite's integer overflow and PostgreSQL results
  outside the signed 64-bit range are a 422 field error, never a float. Groups are
  fetched with `LIMIT 501` and sorted in Python (empty first) so both databases agree.
* **where=.** Compiled with `compile_expression` (so the IR and type rules are finite-v1)
  and then to parameterized SQL that yields 1, 0 or NULL. `&` is a product, `|` is
  `(a + b + 1) / 2` and `~` is `1 - a`, so an undecided part keeps the whole condition
  undecided, as finite-v1 does; only true rows count. Literals with more places than the
  field are compared exactly (floor or ceiling). Arithmetic, Link paths, `today()` and
  the other functions are rejected.
* **Meta.measures.** Compiled in `Registry` (`registry.measures`). Model-level, or per
  record with `model=` and `link=` (`Count(Payment, link="invoice")`,
  `Sum("amount", model=Payment, link="invoice")`). `view_for` now accepts the
  `measures` Meta key.
* **APIs.** `Database.aggregate()`, `Database.measures()` (trusted), `Context.aggregate()`,
  `Context.measures()` (permission-aware). HTTP: `GET /api/{model}/aggregate`,
  `GET /api/{model}/measures` (with list `filters`) and `GET /api/{model}/{id}/measures`.
  These GET routes sit beside `POST /api/{model}/{id}/void`, `/revise` and `/moves/{to}`
  without overlap (tested on one model).
  Row scopes apply to the model and to related models. Unreadable measure, `where`
  and group fields are 403 with one generic message; declared measures a role cannot
  read are left out. `/api/schema` adds `measures` only to models that declare them.
* **Playground.** The amendment Quote declares `open_total`, `won_total` and `quotes`;
  `tests/test_builds.py` reads them and groups quotes by stage.
* **Tests.** `tests/test_measures.py` runs every case on SQLite and, with
  `EVLET_TEST_POSTGRES_DSN`, on PostgreSQL: exact sums over 1,000 rows and at the 64-bit
  edge, overflow, empty results, grouping by day, month and year across years and
  process timezones, limits, filters, 28 `where` cases against finite-v1 semantics,
  rejected expressions, registration errors, row scope and field rules through
  `Context` and HTTP, per-record measures, malformed parameters and schema output.

Known limitations:

* Per-record measures run one statement per related model and link, for one record at a
  time; there is no batched list form yet (Phase 10 relation counts may need one).
* Text minimums and maximums and grouping `DateTime` fields by part are not offered.
* The connector, the MCP server and the SolidJS screens do not use measures yet.

Review fixes (01-11-2026):

* A Link to a model with a row scope for the caller is refused as a measure field,
  `where=` input or group field (ad hoc, declared and `Context`); `CountDistinct` of such
  a link counted hidden records before. The Link of a per-record measure is exempt.
* Sums add the high and low 32-bit halves of each value separately and recombine them
  in Python, so only the final total is checked. SQLite no longer fails on an
  intermediate overflow that PostgreSQL accepted. The stored range is now
  -2^63 to 2^63 - 1 for sums and `where=` literals.
* Unknown and unreadable `where=` fields and declared measure names give the same 403
  through HTTP and `Context` (before, unknown names were 422).
* `/api/{model}/measures` checks its filters even when no measure is available.
* `group_by` of the wrong type names the accepted forms.

## Phase 10: model meaning metadata (01-11-2026)

Branch `model/10-meaning`, stacked on `model/05-void-revise`; restacked on
`model/09-stacked` as `model/10-stacked`. Public API in [meaning.md](meaning.md).

* **Key fields.** `Meta.key_fields`: up to eight distinct visible fields, checked when the
  registry is built. Schema entries carry `key_fields` (readable fields only) when declared.
* **Relations.** Every Link on a root model is a relation of its target, named
  `{model}.{link}`, including several Links from one model and self Links (a revisable
  model gets `Revisions`). Links on Table rows, on `ChildTable` models and hidden Links
  are not relations. `Related(model, link, label=, limit=, order=, hidden=)` names,
  orders, limits or hides one. Schema entries carry `relations` the caller can use (other
  model and its Link readable). `GET /api/{model}/{id}/related/{name}` lists one page
  under the caller's row scope after checking the owning record is readable; order
  fields the caller cannot read are skipped.
* **Attachments.** `Meta.attachments = Attachments(max_files=, max_size=)` within the
  existing maximums (10 files, 1 MiB). `Application(attachments=...)` keeps working with
  the defaults; when a model is in both, the Meta limits apply. Schema entries add
  `attachment_limits` when attachments are enabled. Upload errors state the limit.
* **Code.** `evlet/meaning.py` (validation, discovery, `Attachments`, `Related`), called
  once from `Registry`; `Business` holds per-model limits and visible relations and serves
  the related route. `view_for` accepts the three keys; its error message now lists the
  allowed keys from the set, so later phases change one line.
* **Playground.** The amendment `SalesOrder` declares key fields and a `Work orders`
  relation; the `Quote` declares key fields and lists its revisions.
  `tests/test_builds.py` checks both through HTTP.
* **Fixtures.** `test-business-schema.json` and `ui-fixture.json` gained `relations` and
  `attachment_limits`. New tests in `tests/test_meaning.py`.

Known limitations:

* The schema carries no relation counts. The related route returns `total` for the
  caller, and a per-record measure on the same Link (`Count(Payment, link="invoice")`)
  gives the count through `GET /api/{model}/{id}/measures`.
* Related rows are listed without their owned Table rows, like the list route.
* The connector, the MCP server and the SolidJS UI do not use these keys yet.

Review fixes (01-11-2026):

* The related route and the schema use one order rule, the same as the list route's
  `order_by`: unreadable fields and Links to models row scoped for the caller are skipped,
  falling back to `-id`. The schema no longer names fields the caller cannot read.
* `key_fields` of the wrong types raise `ValueError`; the size message says `1 byte`.
* `ChildTable` models cannot declare `Meta.key_fields` or `Meta.relations` either;
  `Application(attachments=...)` on them is unchanged.
* A subclass inheriting its parent's `Meta` applies only the inherited `Related` entries
  that point to it, instead of failing the registry build.
* The generated fixtures (`test-business-schema.json`, `ui-fixture.json`) will be
  regenerated after the phase branches are stacked.

Restacked on Phase 9 (01-11-2026):

* `view_for` accepts `measures` beside `key_fields`, `relations` and `attachments`.
* `GET /api/{model}/{id}/related/{name}` sits beside `/measures`, `/lines/{name}`,
  `/void`, `/revise` and `/moves/{to}` without overlap; `tests/test_measures.py` hits
  each on one model, on SQLite and PostgreSQL, and compares a per-record `Count` on the
  revision Link with the `Revisions` relation total.
* The amendment Quote keeps its sequence number and measures, and adds key fields and
  its revisions relation.
* The generated fixtures were regenerated on the stacked branch with
  `scripts.generate_ui_fixtures`, `scripts.generate_document_fixtures` and
  `scripts.generate_finite_fixtures`; they match the committed files.

## Phase 6: effects (01-11-2026)

Branch `model/06-effects`, stacked on `model/05-void-revise`; restacked on
`model/10-stacked` as `model/06-stacked`. Guide: [effects.md](effects.md).

* **Declaration.** `Meta.effects = (Effect(Target, rows="method", on="state", reverse=Swap(a, b) or
  Negate(*fields), balanced=(a, b)), ...)`, exported from `evlet` with `Swap` and `Negate`. The
  engine lives in `evlet/effects.py`; `Registry` compiles the effects into `registry.effects` and
  `registry.effect_targets` at startup.
* **Decisions.**
  * The rows method takes only `self`, like a `run` method, instead of the handoff's
    `(self, tx)`. `self` is the saved document read with full access and frozen, so the rows are a
    pure function of the record and the check command can recompute them. Virtual results are
    not available to it.
  * A target declares `source = Reference()` and `reversal = Boolean(default=False)`. Reversal rows
    are marked by the flag, keep the original's `source`, and originals are never changed. The
    Draft interface reserves `source`, so a flat model (no Table) may now name a Reference field
    `source`; on such a draft the field wins.
  * Any model named by an Effect is an effect target. `Policy.allows` refuses create, update and
    delete on it for every role, which covers HTTP, `Context`, dialogs, services and attachments,
    and `Session` raw writes raise outside `tx.maintenance()`.
  * Registration requires `on` to be entered by a declared move, not the default, never entered
    twice, with every later state locked; `reverse` is required when a voidable state is reachable
    after `on`; Swap fields must match in type, scale and bounds and Negate bounds must be
    symmetric, so reversal rows are always valid. One effect per target per model.
  * Rows are validated as new records of the target: declarative targets settle through the normal
    pipeline, plain models through their fields and `validate()`. Link values are checked with the
    admin role's read access. Balance sums are exact decimals.
  * Posting runs in `documents.operate` after a move is saved; reversal runs from
    `corrections.reverse_effects`. Any failure rolls back the move or void.
* **Details and schema.** `_effects` (target, rows, reversals, balanced) outside the ETag, and
  schema `effects` (target, on, balanced, reverse) on sources plus `effect_target: true` on
  targets, only for roles that read the target without a row scope.
* **Check.** `evlet.effects.check(application)` and `evlet effects check INSTANCE` (shared instance
  lock, nothing written, exit status 1 on differences) report missing, extra, changed,
  unbalanced and invalid rows, including mirror rows of void records and rows of deleted sources.
  Whether a record must have posted is derived from the State's moves.
* **Playground.** The amendment Quote posts one `SalesLedger` row when it is marked won;
  `tests/test_builds.py` checks the row, the 403 on create and a clean check.
* **Shared files touched:** `models.py` (registry hook, `source` exemption), `permissions.py`
  (`allows`), `database.py` (`_guard`), `documents.py` (post after a move, Draft `source`),
  `corrections.py` (`reverse_effects` body), `business.py` (details and schema), `ui/metadata.py`
  (`Meta.effects` allowed), `cli/main.py`, `__init__.py`.
* **Tests.** `tests/test_effects.py` (26 tests on SQLite) and eight PostgreSQL variants in
  `tests/test_postgres.py`, run against a local PostgreSQL 14 cluster.

Known limitations:

* Reference fields cannot be indexed, so finding a record's rows scans the target table.
* The check holds one write transaction for the whole run.
* Effects post only on a move; raw writes inside `tx.maintenance()` post nothing.
* The SolidJS form, the connector and the MCP server do not show `_effects`.

### Phase 6 review findings (01-11-2026)

* Effect rows are validated without any role: an engine policy reads linked records as stored,
  in batches, so Link existence and Link filters do not depend on the admin role's scope or
  read access. A Link to a missing record is a 422 under `_effects` for plain and declarative
  targets.
* A `reverse` that changes one field of the `balanced` pair is rejected at registration, and a
  void checks the balance of the mirror rows too. Roles that cannot read both balanced fields get
  the balance error without totals.
* A failing rows method is a 422 that rolls back (the exception is logged, not shown) and an
  `invalid` difference in the check. Reading a virtual result is an error with a clear message.
* An `on` state that no sequence of moves from the default reaches is rejected.
* The check reads one snapshot (deferred SQLite transaction, `REPEATABLE READ READ ONLY` on
  PostgreSQL) without the write lock, and scans for orphans in batches.
* `Reference(index=True)` creates one index over both columns on SQLite and PostgreSQL (create,
  AddField, AddIndex, rename, schema check). Effects index the target's `source`; an existing
  table gains it with `AddIndex("<target>", "source")`. EXPLAIN shows an index search.
* Posting writes one INSERT per row with linked records read in batches and no reload: 500
  ledger rows take 524 statements for the move (about 1,500 before) and 518 for the void.
* Events: `EventStore.publish` inserts into `evlet_events` in the operation's transaction, so an
  event for an effect row is discarded with any rollback, including a savepoint rollback.

Restacked on `model/10-stacked` as `model/06-stacked` (02-11-2026):

* `corrections.reverse_effects()` now writes the mirror rows; `Meta.effects` joins the Meta
  keys of Phases 9 and 10; the `Reference(index=True)` composite index sits beside the
  Sequence unique index in `migrations.py` and `postgres_schema.py`; raw `Session` writes
  refuse effect targets beside the finite document, State and Sequence guards.
* In `operate()` a Sequence is assigned before the write and effects post after it, so a
  move into `assign_on` that also posts rows numbers the record first and the rows can read
  the number.
* An effect target's `source` Reference is not a relation (relations come from Links).
* `tests/test_effects.py` adds checks on SQLite and PostgreSQL: a caught move failure rolls
  back an earlier effect's rows and the number to the savepoint; revise reverses the original
  and the numbered copy posts again; measures on a target and per-record measures over it
  follow row scope; the source Reference is not a relation.
* The amendment Quote keeps its number, measures and key fields and adds the SalesLedger
  effect. The generated fixtures were regenerated and match the committed files.

## Phase 8: the Journal (02-11-2026)

Branch `model/08-journal`, stacked on `model/10-stacked`. Guide: [journal.md](journal.md).

* **Declaration.** `Meta.journal = Journal(tamper_evident=False)`, exported from `evlet`.
  Left out, it is on for models with a State and off otherwise; `journal = None` is the one
  way to turn it off. `Journal()` on a model without a State makes it a document. Row models
  cannot declare one. `Registry.journals` holds the result; `view_for` accepts the key.
* **Storage.** `evlet_journal(id, model, record_id, kind, actor, at, data, prev_hash, hash)`
  with an index on (model, record_id, id), created by `Business.initialize()` when any model
  journals. Triggers refuse UPDATE and DELETE (and TRUNCATE on PostgreSQL). No route, Session
  method, connector or MCP tool writes the table.
* **Engine entries** in the operation's transaction and savepoint: `created` and `changed`
  (field old and new values, and Table rows at every depth: added, updated with field diffs,
  removed with their last values, reordered), `moved` (from, to, label, through
  `states.moved()`), `voided` (with the reason, through `corrections.voided()`), `revised` (the
  copy's id, through `corrections.revised()`; the copy starts with `created`), `number` (its own
  kind, through `sequences.assigned()`; numbers are left out of field diffs), `file`
  (attachment added or removed through the HTTP routes) and `deleted`. `journal.effects_posted()`
  is the hook for Effects (Phase 6) and has no caller yet. The actor is the user id, or
  `engine` without a signed in user.
* **User entries.** `POST /api/{model}/{id}/journal` and `journal.post()`: `note`, `message`
  (logged, nothing is sent), `call`, `follow_up` (text, due date, assignee who can read the
  record) and `follow_up_done` (once per follow up, 409 after). A new `journal` operation in
  `Rule` (defaults changed by the review fixes below). `Policy.journalers` holds the result.
* **Reading.** `GET /api/{model}/{id}/journal` and `journal.entries()`: paged, newest first,
  read access and row scope required. Every entry is listed; unreadable fields, unreadable or
  out-of-scope rows (all removed rows under a row scope) and Link values to hidden records are
  removed and the entry is marked `partial`. Entries about the State or a number the caller
  cannot read are shown as `changed`. Row and Link checks are batched per page.
* **Details, schema, events.** Details carry `_journal` (`count`, `open_follow_ups`), outside
  the ETag. Schema entries carry `journal` (`tamper_evident`, the kinds the role may post).
  User entries publish an event with `"journal": true` (stored as `deleted = 2` in
  `evlet_events`, no schema change).
* **Tamper evidence.** SHA-256 over `[model, record_id, kind, actor, at, data, prev_hash]`,
  chained per record. `evlet journal verify <instance>` reads the SQLite database read only in
  one snapshot, prints breaks and exits 1; `journal.verify(database)` works on both databases.
* **Playground.** The amendment Quote declares `Journal(tamper_evident=True)`;
  `tests/test_builds.py` reads its `moved` and `number` entries and posts a note on the sent
  quote.
* **Tests.** `tests/test_journal.py`: declarations and defaults, field and nested row diffs,
  reorder, actions, moves, numbers, void reason, revise link, attachments, deletion, no entry
  for an unchanged write, field, row, Link and State filtering for viewers, row scope, paging,
  user entry validation per kind, follow up completion once by permitted roles, the `journal`
  Rule operation, the Python API, the journal event, triggers, hash chains with three kinds of
  tampering, the verify command, rollback of a caught failure, and a PATCH of a 1,000-row
  document (at most three more statements than without a journal). PostgreSQL variants in
  `tests/test_postgres.py`.
* **Changed tests.** Two statement budgets in `tests/test_states.py` for stated documents
  allow one more statement, the `_journal` summary, since a State now journals by default.

Known limitations:

* Rename migrations do not rewrite stored entries.
* The hash chain does not detect a deliberate rewrite of every hash or the removal of a
  record's latest entries.
* The connector, the MCP server and the SolidJS screens do not show the journal yet.

Review fixes (02-11-2026):

* A SQLite trigger refuses inserts that reuse an id, since `INSERT OR REPLACE` replaced an entry
  without firing the DELETE trigger. It is created idempotently, so older databases get it.
* Journal text with a lone surrogate is a 422 (`Expected valid Unicode text`), not a 500.
* `journal.post()` runs in the per-operation savepoint with the recursion guard.
* Owner decision: admin always keeps the `journal` operation (when it reads the model), the
  editor has it when its Rule reads and updates the model, and a Rule naming `journal` only adds
  roles, like void and revise.
* `verify` knows the tamper-evident models (from the registry and `evlet_journal_hashed`,
  written at startup) and reports their unhashed entries. The CLI falls back to a
  `query_only` connection when a WAL database cannot be opened read only; tenant and
  PostgreSQL databases use `journal.verify(database)`.
* An action's name is left out of entries for roles that cannot read the State. The guide
  documents what stays visible of hidden changes and the request size limit.
* The 1,000-row PATCH budget is four statements (the SQLite guard trigger is traced as one).
