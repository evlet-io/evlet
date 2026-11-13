# finite-v1 rebuild plan

Goal: one formula language (finite-v1) with simpler engines, keeping every feature.
Source: the formulas audit of 12-11-2026 (findings 1 to 13, "Proposed target design").
Each step is one small pull request the owner can approve on its own.

## Parity contract

The Python engine (`evlet/calc.py`) is the authority. The browser engine
(`frontend/src/evlet/model/finite.ts`) previews the same results. The shared fixtures
(`tests/finite_fixtures.py`, generated into `frontend/src/evlet/tests/finite-fixtures.json`)
check, after every command:

- snapshot values and result states (ready, pending, error) with their causes and codes
- rule states (`applies`, `required`, `locked`)
- Link lookup requests
- the error code of a refused command
- which results changed (`trace.changed`)

They do **not** check internal counters (`evaluated`, `skipped`, `steps`) or the exact
`revision` number. Each engine keeps its own test that an edit to one row does not
re-evaluate other rows, and the step budget stays a safety limit in both.

Stored formula hashes and plan checksums never change for existing formulas. Finance
pins released hashes, so every step runs `test_formula_hashes_of_existing_fixture_models_are_unchanged`.

## Steps

| # | Step | Audit | Files | Risk | Share of the rebuild |
| --- | --- | --- | --- | --- | --- |
| 1 | Fixture contract | 6 | `tests/finite_fixtures.py`, `finite-engine.test.ts`, fixtures JSON, two small "no extra evaluation" tests | Low | 5% |
| 2 | Browser engine cleanup | 4, 7, 8 | `finite.ts`, `calc.py` (`move_row`, `rebase`), tests, `docs/finite-calculations.md` | Low | 10% |
| 3 | Rollback without copying the document | 5 | `finite.ts` and `calc.py` batch and commands, tests | Medium | 10% |
| 4 | Helpers that compile to existing nodes | 12, 13 | `calc.py`, `fields.py`, `evlet/__init__.py`, testcrm example, docs | Low | 10% |
| 5 | Naming-series conditions on finite IR | 10 | `expressions.py`, `sequences.py`, `customization.py` callers, tests | Low to medium | 10% |
| 6 | `query=` results as host inputs | 3 | `documents.py`, `calc.py` | Low to medium | 10% |
| 7 | Plans for Slips, Report filters and Dialogs | 1 | `registry`, `slips.py`, report and dialog settle, browser forms | Medium | 15% |
| 8 | String formulas as a syntax of finite-v1, then retire `reactivity` evaluation | 2 | text parser (shared with step 5), `reactivity.py`, `documents.py`, examples | Medium | 25% |
| 9 | Re-record formula hashes before 1.0 | 9 | migration step, `calc.formula_hash` | Medium | 5% |

Steps 1 to 5 avoid `documents.py`, so they do not collide with the 0.5 query builder branch.
Steps 6 to 8 wait for that branch and for the formula consistency fix (Slips, Reports and
Dialogs refuse finite expressions at registration). Step 9 needs Finance to agree.

### 1. Fixture contract

Drop `evaluated`, `skipped`, `steps` and `revision` from the fixture steps; keep
`trace.changed`. Add one test per engine that editing one row evaluates only that row's
formulas and the totals over it. No engine code changes.

### 2. Browser engine cleanup

Remove `FiniteDocument.explain`, `subscriptions`, `moveRow`, `replaceRows`, `sources`,
`observeTable`, `rebase` after checking that no app code calls them, and the matching
Python `move_row` and `rebase` that exist only for fixture parity (`replace_rows` stays in
Python; the server uses it). Remove `canonical`, `fingerprint` and `sha256`: the server
sends the plan and its hash together and refuses a save whose hash differs. Keep the plan
structure checks. Keep one async-batch check.

### 3. Rollback

Every command checks its inputs before it changes anything, as `set` already does. Then
the per-batch copy of the whole document goes. A callback that throws part way through a
batch keeps the commands that already ran and still publishes once. An engine failure
during settle (budget overflow) marks the document broken; the browser session rebuilds it.

### 4. Helpers

`choose(value, {case: result}, default=...)`, `F(...).isin(...)`, `is_empty(x)`,
`least(...)`, `greatest(...)`, `abs(x)`. Each compiles in Python to `when`, `eq`, `or`,
`lte`, `neg` nodes the engines already run. Existing plans and hashes are unchanged. The
testcrm `probability` formula uses `choose`.

### 5. Naming conditions

`evlet/expressions.py` keeps its tokenizer, parser and error messages, and produces finite
IR. `calc.evaluate` runs it on one row. Every condition the current grammar accepts still
parses, and every edge case where the two evaluators could differ is pinned by a test.
