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
| Limits | As specified (128/32 expression, 8 depth, 100 per collection, 1,000 owned rows, 50k nodes, 250k edges, 1M steps, 128-char decimals), embedded in plans and checked by both runtimes. |
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
