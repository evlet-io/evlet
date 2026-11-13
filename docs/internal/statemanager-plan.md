# State manager plan (0.4.6)

Status: plan, 13-11-2026. Built on the query-builder branch (#183). Sources: the owner's brief
(`work/specs/statemanager-brief.md`), the Approval Rules spec and its review. The brief wins
where they differ.

One module, `evlet/statemanager.py` (standard library only), replaces State/Move, the
confirmable lifecycle, Revise, Unpost/Repost, Void as an operation, `track_changes` and every old
hook. Nothing is deprecated: it is removed, with no aliases or shims.

## Budget

| Measure | Budget |
|---|---|
| `evlet/statemanager.py` after PR 5 | at most 950 lines (aim 900) |
| Framework Python (`evlet/`), net | at least 2,000 lines fewer than today |
| Framework Python added outside `statemanager.py` | at most 250 lines |
| Frontend, net | fewer lines than today |

Every PR states lines deleted and added (from `git diff --shortstat`, framework and tests apart).

## What is deleted, file by file

Line counts are today's, on the #183 base.

| File | Today | Deleted | What goes |
|---|---|---|---|
| `evlet/lifecycle.py` | 458 | 458 (file) | confirmable, `@validate.confirm/.cancel`, `@action.confirm/.cancel`, `check_lifecycle`, `record_approval`, `BoundDocument` and `context.document()`, revisions, snapshot, lifecycle diff, `reference_effect`, `evlet_document_lifecycle`/`_revisions` tables (the step converts them) |
| `evlet/corrections.py` | 686 | ~620, file removed | void, revise and the revision copy, unpost, repost, posting-state search, their availability and schema keys, the HTTP dispatch. `dependents()` and its message (about 70 lines) move to `statemanager.py` for restore |
| `evlet/states.py` | 749 | ~330 | `Move` handling, `begin_move`, `checked_move`, `moved`, move asks, `state_schema`, move availability, `check_delete`'s posting text, `check_posted` messages that name void/revise. State locks move to `statemanager.py`. The rest (guards, action inputs and schema, kept plans, stored reads) is renamed `evlet/guards.py`, since it no longer holds state logic |
| `evlet/declarations.py` | 230 | ~45 | `Move` |
| `evlet/fields.py` | 1042 | ~80 | `State` (moves, locked, final, voidable, cancelled; moves to `statemanager.py`); `editable_when_locked` becomes `versioned=False` |
| `evlet/models.py` | 647 | ~70 | `Meta.revisable` and the `revises` field, `unique_and_required`, `before_close` checks, `closes()`, `Model.validate()`, the `confirmable`/`track_changes` triggers of declaration mode |
| `evlet/documents.py` | 2106 | ~230 | `Context.confirm/cancel/reopen/revisions/snapshot/diff/record_approval/reference_effect/lifecycle_execution/document/void/revise/unpost/repost/_correct`, `Change` and the `changed()` calls (write, delete), `check_close`/`before_close`, lifecycle branches of `operate`, the `'confirmed'` reason of `keeps_saved_results`, `Model.validate()` calls in `validate_tree` |
| `evlet/effects.py` | 943 | ~60 | `check_state`'s move-graph search (replaced by "`on` is a release state"), void handling in `has_postings`, unpost wording |
| `evlet/journal.py` | 842 | ~50 | `voided`, `revised`, `unposted`, `reposted`, `post_confirmation_edit`, `confirmed`/`reconfirmed`/`cancelled`/`reopened` writers, `event_data` calls |
| `evlet/sequences.py` | 700 | ~15 | `assign_on="confirm"` and lifecycle naming |
| `evlet/business.py`, `permissions.py`, `routing.py`, `eva.py`, `tools.py`, `mcp.py`, `ui/metadata.py` | | ~150 | routes `/confirm`, `/cancel`, `/reopen`, `/revise`, `/void`, `/unpost`, `/repost`, `/revisions`, `/snapshot`, lifecycle `/diff`; `_void`/`_revise`/`_unpost`/`_repost`/`_lifecycle_actions`/`revised_by`; operations `confirm`, `cancel`, `void`, `revise`, `unpost`, `repost` in permissions; Eva `change_state` arguments |
| `evlet/__init__.py` | | exports | `Move`, `validate` namespace, `BoundDocument` |
| frontend `record-page.ts`, `meta.ts`, `api.ts`, `client.ts`, `ui/doc.ts` | 1420 + 368 + … | ~300 | `_lifecycle_actions` path, revise/unpost/repost/void dialogs, Revisions card, `revised_by` banner, the name-regex colour guess |
| Tests | | ~2,400 | `test_unpost_repost.py` (521), `test_lifecycle.py` (630), most of `test_corrections.py` (595); `before_close`/`changed` parts of `test_derive_close.py`. Ported, not deleted: `test_states.py`, `test_effects.py`, `test_journal.py`, `test_sequences.py`, `test_system_moves_jobs.py`, `test_move_values.py`, `test_status_display.py`, `test_handler_session.py`, `test_testcrm.py`, `test_tools.py` and the other files that use `Move(` incidentally (31 files) |
| Docs | | rewritten | `states-actions.md` (772), `document-lifecycle.md` (216), `lifecycle-specification.md` (549, deleted), `effects.md`, `journal.md`, `amendment-v2.md`, `tools.md`, API docs, CHANGELOG |

Correction to the brief: `tests/test_cli_lifecycle.py` tests the package and instance lifecycle
(`evlet install`, `setup`, locks), not documents. It stays. There is no document-lifecycle CLI
command to remove.

## `evlet/statemanager.py`, section by section

| Section | Contents | Lines |
|---|---|---|
| 1. Declarations | `State(*steps, rules=(), show=, default=)` (a `Select`), `State.Step(key, label, color=, lock=, release=, final=)`, `Rule(from_, to, label, *, key=, by=, when=, levels=, self_approve=, remark=, ask=, primary=, reject=)`, `Level(by, when=, pending=)`, `Remark(mode, label=, required_when=)`; declaration checks (unknown states, final sources, duplicate keys, `key` required when two rules share from and to) | 190 |
| 2. Hooks | `validate`, `state("a", "b")`, `state.exit("a")`, `release` decorators; collecting them per model in class order with override by name; running one kind with `self.is_new`, `self.before`, `self.context`, `self.release` | 70 |
| 3. Build | `configure(model)` from the model build step; `compile(registry)`: rule and level guards on the existing guard engine, remark `required_when`, asks (derived from `applies_when`/`required_when`, plus `ask=`), `by` resolved to roles and user fields | 90 |
| 4. Locks and ordinary writes | state field never written by ordinary writes, lock checks on records and rows, `versioned=False` exceptions, delete and attachment refusals | 110 |
| 5. Moves | rule choice, the eight-step order (rule check, exit hooks, state change, enter hooks, settle, validate, release, write and log), remark checks, journal entry with rule key, remark and rule-set version | 130 |
| 6. Approvals (PR 2) | levels decided at entry and logged, current level from the log of this stay, one person per level, creator rule, reject rules, `pending`, `awaiting(context, model)` | 110 |
| 7. Releases, snapshots, versions (PR 3) | content hash (versioned fields and rows), release on entering a release state only when the hash changed, manual release, lazy snapshot with drift check, `_version`, versions list, one version (permission-projected), diff | 150 |
| 8. Restore (PR 4) | forward-only restore with its conflict cases, `dependents()` moved from corrections | 100 |
| 9. Schema and availability | the one state schema and `_moves` | 70 |
| 10. Runtime rules (PR 5) | `evlet_overrides` kinds `state` and `rule`, merge by key, turn off, added rules, save-time checks, orphans, compile per `evlet_config.version` | 120 |
| Total | | about 940 |

No registries, plugin points, base classes or configuration objects. Rules compile into plain
dicts on the registry; runtime rule sets are cached by config version in one dict on the
business.

### Declarations

```python
status = State(
    State.Step("draft", "Draft"),
    State.Step("review", "In review", lock=True),
    State.Step("issued", "Issued", color="green", lock=True, release=True),
    State.Step("void", "Void", color="red", lock=True, final=True),
    rules=(
        Rule("draft", "review", "Submit", by=("editor",), primary=True),
        Rule("review", "issued", "Approve", key="approve",
             levels=(Level(by=("manager",), pending="Pending manager approval"),
                     Level(by=("cfo",), when=F("total") >= 10000, pending="Pending CFO approval"))),
        Rule("review", "draft", "Reject", reject=True, remark="required"),
        Rule("issued", "draft", "Reopen", by=("admin",), remark="required"),
        Rule(("draft", "issued"), "void", "Void", by=("admin",), remark="required"),
    ),
    show="bar",
)
```

- `from_` is a state or a tuple of states. `key` defaults to `<from>_to_<to>` (sources joined by
  `_` for a tuple).
- `by` takes role names (built-in or custom Controls roles) and user fields (`F("approver")`, a
  Link to the users model or a Text holding a username). Default `("admin", "editor")`. `by=()`
  is a rule only application code makes (today's `system=True`), never offered or accepted over
  HTTP.
- `reject=True` marks a rule back out of a state that has an approval rule: it is offered only to
  the current level's approvers (owner decision 9). It is the one option the spec does not list;
  without it Reject could not be told from Withdraw.
- `remark` is `None`, `"optional"`, `"required"` or `Remark(...)`.
- `ask=` adds fields to the derived asks.
- `primary=True` marks the main button. Without it, the first allowed rule is primary.
- Colours are named tokens (`green`, `red`, `amber`, `blue`, `grey`, …) the UI maps to theme
  colours. A step without one is neutral; the UI never guesses from the name.
- A State needs at least one step and its default (first step unless `default=`) must not be final.
  Any state that is `final` has no rule out. Void is a final state reached by a rule.

### Hooks and order

`@validate` runs on every save (insert, update, move, restore) with the document read only;
assigning raises. `@state("x", …)` runs on entering, `@state.exit("x", …)` on leaving, both with
`self.context.session` open, like a Move `run` method today. `@release` runs when a release is
made. Hooks of one kind run in class order; a subclass overrides one by name.

Order on a move, as the spec: 1 rule check, 2 snapshot if leaving a release state, then
`@state.exit(old)`, 3 state changes, 4 `@state(new)`, 5 settle, 6 `@validate`, 7 if the new state
is a release state and the content hash differs from the last release: `@release`, settle,
`@validate`, insert the release, 8 write, journal, effects. A `ValidationError` (or `ValueError`)
anywhere rolls back the operation's savepoint: the move, the release, the snapshot and every
record the hooks wrote.

Old hooks and their replacements, for the docs and the Finance guide:

| Removed | Use |
|---|---|
| `Move(..., run="m")` | `@state("to")` |
| `before_close(self, to)` | `@state.exit(...)` / `@validate` |
| `changed(self, change)` | `@state(...)`, `@release`, or a job queued from a hook |
| `Model.validate()` | `@validate` |
| `check_lifecycle`, `@validate.confirm/.cancel` | `@validate` with `self.before.status` |
| `@action.confirm/.cancel` | `@state("confirmed")`, `@state.exit("confirmed")` |
| `record_approval()` | approval levels |
| `Effect(on="posted")` | unchanged declaration; `on` must be a release state; posts on release, reversed on exit |

### Effects and postings (decision 10)

`Effect(on=...)` keeps its declaration and runs through the release path. It is less code: the
row building, validation, balance, period and reversal code in `effects.py` stays as it is, and
only the trigger changes (about 20 lines), instead of every app rewriting its postings as
`@release` hooks. Rules: `on` must be a `release=True` state. Effect rows are written when a
release is made in `on` (so an unchanged reissue posts nothing) and reversed when a rule leaves
`on`, before `@state.exit` hooks run. Apps that post by hand use `@release` and `@state.exit`.

## Journal and model changes

`evlet_journal` gains five nullable scalar columns: `rule` (rule key), `remark`, `level`
(approval level index), `base` (latest release number when the entry was written) and
`rules_version` (`evlet_config.version`). Entry kinds added: `approved`, `rejected` (already
known), `released`, `restored`, `snapshot`; moves keep `moved`. Kinds removed from writers:
`voided`, `revised`, `unposted`, `reposted`, `confirmed`, `reconfirmed`, `cancelled`, `reopened`,
`post_confirmation_edit` (old entries stay readable). Per-field and per-row records, the hash
chain and the append-only triggers stay. The hash of an entry with any scalar set covers them
too; entries without them hash as today, so old chains verify.

Releases live in `evlet_releases` (append-only, generalising `evlet_document_revisions`):
`model, record_id, number, snapshot (0 release / 1 snapshot), at, actor, state, hash, drifted,
data`. A release row stores hash, state, actor, time and `{approvals, remark}`; no copy of the
document. A snapshot row is appended, once, before the first divergence from that release, with
the document tree and `drifted` when its hash differs from the release hash. Both kinds are
append-only, so nothing is ever updated.

The latest version number is read from `evlet_releases` by its primary key; that table is the
side table, and no app table gains a column. If lists later need the number for many rows at
once, a `max(number)` grouped read serves them; a separate counter table is added only if that
proves slow.

Model build: `statemanager.configure(cls)` replaces `lifecycle.configure_model` in the model
class build and attaches `__state__`, the hooks and the steps. The registry build compiles rules
(`registry.rules`) where it compiles guards today. `versioned=False` replaces
`editable_when_locked` in one rename across fields, checks, docs and tests.

## Schema

`state_schema` (in the model schema, for roles that can read the State field):

```json
{"field": "status", "default": "draft", "show": "bar",
 "steps": [{"key": "draft", "label": "Draft", "color": null, "lock": false, "release": false, "final": false}],
 "rules": [{"key": "approve", "from": ["review"], "to": "issued", "label": "Approve", "by": ["manager"],
            "guarded": false, "primary": false, "reject": false, "remark": {"mode": "optional", "label": null},
            "levels": [{"by": ["manager"], "pending": "Pending manager approval", "guarded": false}]}]}
```

Record detail:

```json
"_moves": [{"key": "approve", "to": "issued", "label": "Approve", "allowed": true, "reason": null,
            "asks": [], "remark": {"mode": "optional", "label": null, "required": false},
            "primary": true, "level": 1, "pending": "Pending CFO approval", "can_approve": true}],
"_locked": true,
"_version": {"number": 2, "released": false, "state": "issued"}
```

## Routes

| Route | Purpose |
|---|---|
| `POST /api/{model}/{id}/moves/{key}` | Make a move or approve the current level; body `{remark?, ...ask fields}`; If-Match required |
| `POST /api/{model}/{id}/release` | Manual release; `{remark?}`; `release` permission (PR 3) |
| `POST /api/{model}/{id}/restore` | `{version, remark}`; `restore` permission (PR 4) |
| `GET /api/{model}/{id}/versions` | Releases, newest first (PR 3) |
| `GET /api/{model}/{id}/versions/{n}` | One version, field and row permissions applied (PR 3) |
| `GET /api/{model}/{id}/versions/{a}/diff/{b or current}` | Compare (PR 3) |

`/moves/{key}` replaces `/moves/{to}`. The history route (`/history`) stays. Eva's
`change_state` tool takes `{model, id, rule, remark, values}` and calls the same
`context.move()`. MCP and the connector use the same routes. The Eva write-confirmation feature
keeps its own name; docs call it "Eva write confirmations" wherever it could be read as Approval
Rules.

Python: `context.move(model, id, key, *, remark=None, **values)`, `context.release(...)`,
`context.restore(model, id, *, version, remark)`, `context.versions(...)`,
`context.version(model, id, n)`, `context.version_diff(model, id, a, b)`.

## Migration step

`FrameworkStep('0011_state_manager', ..., registry=True)`:
1. Add the five journal columns where missing; create `evlet_releases`.
2. Copy `evlet_document_revisions` rows into `evlet_releases` as release `revision - 1` plus its
   snapshot row (the revision stored the full document), hashed with the new content hash.
3. Write `evlet_document_lifecycle` states into the model's State column when the model now has a
   State with that step (`draft`, `confirmed`, `cancelled`); refuse with a named list when a model
   with lifecycle rows has no such State, so nothing is lost silently.
4. Backfill a v0 release (actor `engine`, no snapshot) for every record of a State model whose
   state is a release state and that has no release.
5. Drop `evlet_document_lifecycle` and `evlet_document_revisions`.

Tested on a 0.4.4 database fixture (built by the 0.4.4 code paths, stored as SQL in
`tests/fixtures/`), including a Finance-like posted document.

## Test plan

New `tests/test_statemanager.py`, real SQLite, no mocks:
- every Rule, Step, Level and Remark option and their declaration errors;
- moves by key, the first matching rule, `by` roles, custom roles and user fields, `when`, `by=()`;
- remarks: required, optional, `required_when`, refused when the rule has none; asks with `ask=`;
- approval matrix: all levels, skipped levels, one person per level, creator and `self_approve`,
  reject by the current level only, expiry on leaving, a resubmit starting at level 1, pending text;
- hook order on a move (recorded list) and rollback from each hook, including records hooks wrote;
- `@validate` read only, on insert, update, move and restore; `is_new`, `before`, `context`;
- release only when content changed; unchanged reissue posts nothing; manual release;
- snapshot exactly once before leaving a release, never on release; first edit after a manual
  release snapshots; drift detection;
- restore and its conflict cases (removed field, added field, deleted rows, unique value,
  dependents, unversioned fields, locked state, permission, remark);
- unversioned fields: editable while locked, outside the hash, kept by restore;
- runtime overrides: labels, colours, rule override by key, off, added rule, orphans, save-time
  checks, recompile on config version;
- the migration step on a 0.4.4 fixture;
- query budgets of a move and of `_moves` (counted statements);
- Eva and MCP moves go through the same checks;
- field permissions on old versions.

Ported to the new API: locks, guards, postings, journal, sequences on state entry, system
moves, move values, status display, handler sessions, testCRM. Deleted: the tests of removed
features. Every PR runs the full `pytest`, `npm test` and `npm run build`, and checks the
Playground (sign-in lands on the first list, a record moves and saves, `/docs` reachable).

## PR split

All stacked drafts on `agent/remove-postgres-adapter-seam-2b0e25`, retargeted to `main` when
#183 merges:

1. **Engine, rules, hooks and removals.** Sections 1–5 and 9; all deletions above; journal
   columns; `/moves/{key}`; Eva `change_state`; `versioned=False`; effects post on entering and
   reverse on leaving `on`; frontend kept working and its deletions; examples and docs.
2. **Approval levels.** Section 6 and its tests and docs.
3. **Releases, lazy snapshots and versions routes.** Section 7, `evlet_releases`, `_version`,
   effects only on a real release, the migration step.
4. **Restore.** Section 8.
5. **Runtime overrides backend.** Section 10 (no Controls page).

The split costs no extra code: each layer only adds to `statemanager.py`. The only interim
behaviour is in PR 1–2, where effects post on every entry into `on` as they do today.

For #190/#192 (package layers): this work removes `lifecycle.py` and `corrections.py`, renames
`states.py` to `guards.py`, adds `statemanager.py`, and replaces the `lifecycle.configure_model`
build step with `statemanager.configure`. Phase 3 should start from the merged result.

## Hidden dependencies the inventory found

- `GET /history` (the journal) is dispatched through `lifecycle.route` (`business.py:808`); it moves
  to `journal.route` before `lifecycle.py` goes.
- `Context.__init__` imports `SOURCES` from `lifecycle.py`; the tuple moves to `documents.py`.
- `permissions.CORRECTIONS` feeds `controls.OPERATIONS` and the API-scope gate (`app.py:266`);
  `confirm`, `cancel`, `reopen`, `void`, `revise`, `unpost`, `repost` leave it, `release` and
  `restore` join it (PR 3, PR 4). Saved Controls permission rows naming removed operations are
  ignored and reported as problems, as for any unknown operation today.
- `database.py:533` refuses direct Session writes to confirmable/tracked models; it keeps the
  State check only.
- The frontend fixtures (`api-fixture.json`, `lifecycle-fixture.json`, `testcrm-fixture.json`) are
  regenerated from the backend, not edited by hand.

## Risks

- The `versioned=False` rename and `Move` removal touch 31 test files and every example; done
  mechanically, then reviewed.
- Finance's frozen migration copies import `Move`; Finance must replace those copies before it
  moves to 0.4.6 (covered in `finance-statemanager-rebuild.md`).
- `_version.released` hashes the saved document on detail; it is measured in the query budget
  test and served from rows the detail already loaded.
