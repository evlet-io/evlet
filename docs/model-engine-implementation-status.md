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
* Void and revise (Phase 5) are not implemented; `voidable` is stored metadata only.

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
