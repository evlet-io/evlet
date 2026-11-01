# Model engine implementation status

Ledger for the finite-v1 model engine handoff (2026-11-01). Contract:
[finite-calculations.md](finite-calculations.md).

## Baseline

| Item | Value |
| --- | --- |
| Branch | `feat/model-engine`, on top of `fix/low-severity-followups` (PR 31 review fixes and low-severity follow-ups) |
| PR 31 | Open, not merged. Head `509f7a3`; the follow-up branch adds `9f9f7aa`..`f6e3f03` |
| Engine base commit | `f6e3f03` |
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
| W5 nested storage | Done (SQLite) | `tests/test_nested_documents.py`: create/reload/update/delete, owner links, rollback, hidden rows, subtree permissions. PostgreSQL relies on CI. |
| W6 security/versions | Done | Computed-write guards, legacy action refusal, raw-write guard, plan pinning, derivation permissions, schema projection. External `Query` sources in finite roots are deferred. |
| W7 UI bridge | Not started | Deferred by the user: Evlet UI becomes its own framework next. Current SolidJS screens keep working; finite fields preview through `/api/_draft`. |
| W8 native controls | Not started | Same as W7. |
| W9 native parity | Not started | Same as W7. |
| W10 package/perf | Done for the engine | Wheel built and installed outside the checkout with Node off PATH; `scripts/smoke_finite.py` saved a nested document over real HTTP and loaded local assets. Added to CI. |

Gates: **engine completion** is met for SQLite with the limitations below. **Native UI
cutover** is not started.

## Tests actually run

```
.venv/bin/python -m pytest            -> see the final report for the last count
cd frontend && npm test && npm run build
.venv/bin/python -m build --no-isolation
python -I scripts/check_package.py   (installed wheel, clean venv)
env -i PATH=/usr/bin:/bin python -I scripts/smoke_finite.py   (installed wheel)
```

PostgreSQL tests were skipped locally (no DSN); CI runs them.

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
* No native UI integration (W7-W9).
