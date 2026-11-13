# Finance: rebuilding on the state manager (Evlet 0.4.6)

For the Finance app (`evlet-io/finance`, `work/evlet-finance`). Written 13-11-2026 from Finance at
`5fbd6e9` (the 0.4.3 merge) and Evlet's state manager PRs. Finance was not edited. Do the steps in
order on a branch; each ends with the tests named in it.

Evlet 0.4.6 removes, with no aliases: `Move`, `State(choices, moves=, locked=, final=, cancelled=)`,
Move `run=`, `system=True`, `before_close()`, `changed()`, `Model.validate()`, `editable_when_locked`,
void/revise/unpost/repost. Evlet 0.4.5 removes PostgreSQL. Finance uses `Move`, `run=`,
`system=True`, `before_close()` and the PostgreSQL adapter; it uses none of confirmable, Revise,
`record_approval` or `Effect`.

## 1. Pin and imports

- `pyproject.toml`: `evlet>=0.4.6,<0.5`. Drop the `postgres` extra and the `evlet[postgres]` dev
  dependency (step 9).
- Replace `from evlet import Move` everywhere with `State` only. Rules are `State.Rule`
  (`evlet.Rule` stays the permission rule). Hooks: `from evlet import state, validate, release`.

## 2. Every State and its replacement

| Where | Today | 0.4.6 |
|---|---|---|
| `documents/common.py` `Document.status` | `("draft","posted","cancelled")`, Post `run="post_entry"`, Cancel `run="cancel_entry"`, locked posted+cancelled, final+cancelled cancelled | Steps `draft`; `posted` `lock=True, release=True`; `cancelled` `color="rose", lock=True, final=True`. Rules `State.Rule("draft","posted","Post", key="post", primary=True)`, `State.Rule("posted","cancelled","Cancel", key="cancel", remark="required")` |
| `documents/payments.py` `Payment.status` | adds Bounce cheque `when=F("cheque_date") > F("date")`, `run="bounce_entry"`; Cancel `run="cancel_payment"` | Add step `bounced` (`color="rose", lock=True, final=True`) and `State.Rule("posted","bounced","Bounce cheque", key="bounce", when=F("cheque_date") > F("date"), remark="optional")` |
| `documents/trade.py` `Agreement.status` | `("draft","confirmed","closed")`, Confirm `run="confirm_entry"`, Close | Steps `draft`; `confirmed` `lock=True` (no release: agreements never post); `closed` `lock=True, final=True`. Rules `confirm` (primary) and `close` |
| `generator/model.py` `DataGenerator.status` | `("stopped","running")`, Start/Stop by admin with `run=` | Steps `stopped`, `running`; rules `State.Rule("stopped","running","Start", key="start", by=("admin",))`, `State.Rule("running","stopped","Stop", key="stop", by=("admin",))` |
| `generator/company/model.py` `SimulatedCompany.status` | Start from new/stopped/finished, Stop, Finish `system=True` | Rules `State.Rule(("new","stopped","finished"),"running","Start", key="start", by=("admin",))`, `stop`, and `State.Rule("running","finished","Finish", key="finish", by=())` |

Rule keys are new names. Every `ctx.move(Model, id, "<state>")` in `generator/simulate.py`,
`generator/company/day.py`, `generator/furniture/run.py`, `generator/demo.py` and
`generator/job.py` becomes `ctx.move(Model, id, "<rule key>")`, for example `"posted"` → `"post"`,
`"cancelled"` → `"cancel"`, `"bounced"` → `"bounce"`, `"closed"` → `"close"`,
`"confirmed"` → `"confirm"`, `"running"` → `"start"`, `"stopped"` → `"stop"`. A move that needs a
remark takes `remark=`: `ctx.move(SalesInvoice, id, "cancel", remark="Simulated cancellation")`.
The browser and the HTTP API use `POST /api/{model}/{id}/moves/{key}` with `{"remark": ...}`.

Step keys stay the old state names, so stored values, lists, measures (`F("status") == "posted"`),
`Sequence(assign_on="posted")` and reports keep working unchanged.

## 3. Run methods become hooks

`ForModel` exists only so a Move `run=` name could reach a module function with the model bound.
Hooks are ordinary methods, so `ForModel` and the `*_entry` class attributes go.

| Today | 0.4.6 |
|---|---|
| `post_entry` (Post `run=`): `document.posting = post_document(tx, document, model).posting_id` | `@state("posted") def post_entry(self): self.posting = post_document(self.context.session, self, self.model).posting_id` (`self.model` is the model class on a hook's draft); see Postings and releases below |
| `cancel_entry` (Cancel `run=`) | `@state.exit("posted") def reverse_posting(self): cancel_document(self.context.session, self)` |
| `cancel_payment` | `Payment`: `@state.exit("posted")` that reverses the clearing (if any) and then the posting, as `cancel_payment` does today |
| `bounce_entry` → `bounce_pdc` | Not needed: leaving `posted` for `bounced` runs the same `@state.exit("posted")`. `bounce_pdc` reverses on the posting's own date while `cancel_document` picks the open date; if that difference matters, keep the date rule in the exit hook by reading `self.before` (the posted document) and put anything only a bounce does in `@state("bounced")` |
| `confirm_entry` (Agreement Confirm) | `@state("confirmed") def confirm_entry(self): prepare(self.context.session, self.model, self)` |
| `start_entry` / `stop_entry` (generators) | `@state("running")` and `@state("stopped")` |

Order on a move: rule check; `@state.exit(old)`; the state changes; `@state(new)`; calculations
(and `derive()`); `@validate`; numbers; then, entering a `release=True` step whose content changed
since the last release, `@release`, calculations and `@validate` again; write; journal. A
`ValidationError` anywhere rolls back the whole move, including what hooks wrote through
`self.context.session`. `self.before` is the document as last saved, `self.is_new` is false on a
move.

### Postings and releases

`posted` is a `release=True` step, so each Post whose content changed makes a numbered version
(v0, v1, ...), and `@release` runs only then. The reversal is in `@state.exit("posted")`, which runs
on every way out. Together these would leave a document posted with no live posting if it were
reopened and posted again unchanged: the exit reverses, and no release (so no `@release`) follows.
So post in `@state("posted")`, which runs on every entry, and reverse in `@state.exit("posted")`.
The ledger then always matches the document. Use `@release` only for what belongs to a version,
not for the ledger. Evlet's own `Effect(on=...)` works the same way (it posts on every entry into
`on`). This differs from the brief's "an unchanged reissue posts nothing" and is flagged to the
owner in state manager PR 3.

Finance has no reopen rule today. If it adds one
(`State.Rule("posted","draft","Reopen", key="reopen", by=("admin",), remark="required")`), the exit
hook reverses the posting, and the next Post posts again on the same document id and number. That
Post makes v1 only if the content changed.

## 4. `before_close()` becomes `@state(...)`

`TradeDocument.before_close(to)` refuses Cancel and Close while later documents exist
(`block_later`), and `Agreement.before_close` refuses Close while drafts exist (`block_drafts`).

- Cancel and Close: `@state("cancelled", "closed")` calling `block_later(self.context.session,
  self.model, self, "Cancel" if self.status == "cancelled" else "Close")`; `Agreement` overrides it
  by name with `block_drafts` for `closed`. A `ValidationError` there refuses the move.
- Delete: there is no delete hook. A draft is the only document that can be deleted (posted and
  confirmed ones are locked), and nothing comes from a draft, so the delete case needs no check.
  Keep any real dependency as a `Link`, which the database refuses to orphan.

## 5. `posting`, `clearing` and the `Posting.reverses` chain

- `Document.posting = Link(Posting, copy=False)` and `Payment.clearing` record where a posted
  document is in its process. Declare both `versioned=False`: hooks set them on a locked record,
  they stay out of the release content hash, and a restore keeps their current values.
- `Posting.reverses = Link("posting", unique=True)` and `StockLine.reverses` are Finance's own
  ledger chains and do not change. A release is not a posting: Evlet's `evlet_releases` keeps the
  document's version (number, hash, state, actor, time), and the `posting` Link keeps the ledger
  entry. After a reverse, `posting` keeps pointing at the original posting; the reversal is found by
  `reverses`. If the screen should show the live posting only, clear `self.posting` in the exit hook.
- `engine/repost.py` (the repost queue) is Finance's own cache rebuild, unrelated to Evlet's removed
  Repost operation. Keep it.

## 6. Generator models

`DataGenerator` and `SimulatedCompany` have no release step: running and stopping is not a
version of a document. Declare no `release=True` steps there. `Finish` is an application rule
(`by=()`): only the job makes it (`ctx.move(SimulatedCompany, id, "finish")`), it is never offered
and every HTTP caller is refused. The jobs already run as trusted code, so nothing else changes.

## 7. Frozen migrations (owner's choice: a or b)

Released migration checksums stay pinned (`tests/test_upgrades.py::RELEASED`, checked by
`test_released_migrations_keep_their_checksums`), and new migrations use frozen model copies, never
the live classes. `frozen.py` imports `Move` for `DataGenerator0001` (migration `0001_data_generator`)
and `SimulatedCompany0006` (`0006_simulated_company`), so on 0.4.6 `evlet_finance.migrations` no
longer imports. Evlet adds no `Move` shim. One of these has to happen:

**(a) Rebaseline.** Delete `frozen.py` and the nine migrations, and start a new `0001` baseline whose
`CreateModel`s use frozen copies written on the state manager. Pin its new checksums in `RELEASED`.
- Cost: the least code. One baseline file, a new frozen module, and new pinned checksums. The
  upgrade tests from earlier Finance releases (`test_upgrades.py`) are deleted or rewritten from the
  new baseline.
- Breaks: every existing Finance database. Its `evlet_migrations` ledger names the old migrations
  and checksums, so `evlet migrate` refuses it as "migrated by a different release". Each instance
  must be recreated and its data regenerated. The demo instance is a simulated company, so it can be
  reset; any data entered by hand is lost unless it is exported first.

**(b) Keep the chain; rewrite the two frozen copies so the old files import again.** In `frozen.py`,
replace the two `status = State((...), moves=(Move(...), ...))` declarations with `State.Step` keys
equal to the old choices, in the same order, with the same `default` and **no `color=`**. Leave the
rules out (a frozen copy is never moved) or keep them with `by=`. Keep `start_entry`/`stop_entry` as
plain methods. Later schema changes go in new migrations with their own new frozen copies, as
today.
- Why the checksums hold: a migration's checksum covers each field's signature. A State's signature
  is its choices, `required`, `max_length` and the `state` flag. Rules, step labels, `lock`,
  `release` and `final` are not in it. Checked 13-11-2026: the old and new declarations of
  `SimulatedCompany0006.status` give the same signature. `versioned=False` on `posting`/`clearing`
  is not in it either, so no migration is needed for that.
- Cost: about 20 changed lines in `frozen.py`, plus running `test_released_migrations_keep_their_checksums`.
- Breaks: nothing at upgrade, as long as the pinned checksums still match. The risks are adding
  `color=` or a step, or reordering or renaming a choice, in a released copy; any of these changes
  the checksum. The pinning test fails on it, and an instance on that checksum would refuse to
  migrate. The copies' `run=` methods never ran in a migration, so nothing behaves differently.

## 8. What the framework step does on upgrade

`evlet migrate` runs framework step `0011_state_manager` (one transaction, after a backup):

1. adds the journal's `rule`, `remark`, `level`, `base` and `rules_version` columns and creates
   `evlet_releases`;
2. converts confirmable lifecycle and revision rows (Finance has none);
3. backfills a v0 release, with no snapshot, for every record of a State model that is in a
   `release=True` step and has no release: every posted Finance document gets v0, hashed from its
   current content (`posting` and `clearing` excluded once they are `versioned=False`).

Nothing is posted or reversed by the step. Finance's own migrations are not touched. A posted
document's first Cancel snapshots v0 and reverses through the exit hook, as a new one would.

## 9. Remove the PostgreSQL option

- `pyproject.toml`: remove the `postgres` extra and `evlet[postgres]` from `dev`, and the
  `postgres` pytest marker.
- `cli.py`: remove `PostgreSQLDatabase`, `EVLET_FINANCE_POSTGRES_DSN` and the "leave out
  --database" branch; `--database PATH` becomes required.
- Delete the PostgreSQL-only tests (`-m postgres`) and the README sections about them
  (`EVLET_TEST_POSTGRES_DSN`, the `[postgres]` install line).

## 10. Test checklist

- [ ] `.venv/bin/python -m pytest -q` passes on SQLite.
- [ ] Each document type: create a draft, Post (`post`), the posting exists and `posting` is set;
      the journal's `moved` entry has `rule == "post"` and `data.release == 0`, and `_version` is v0.
- [ ] Cancel needs a remark (422 without one); with one, the posting is reversed on the right date
      and the document is cancelled and locked; the reversal's `reverses` points at the original.
- [ ] Payment: Bounce cheque is offered only when `cheque_date > date`; it reverses the clearing
      then the payment; Cancel of a cleared payment does the same.
- [ ] A failing reversal (closed period, settled since) refuses the move and leaves the document
      posted with nothing written.
- [ ] Trade documents: Cancel and Close are refused while later documents exist, with the same
      message as today; Agreement Close is refused while drafts exist.
- [ ] Sequence numbers are taken on Post and kept by a cancelled document.
- [ ] Generators: Start and Stop by admin; Finish is not in `_moves` and HTTP gets 403; the job
      finishes the run.
- [ ] Simulations (`generator/simulate.py`, company and furniture runs) complete with the new rule
      keys.
- [ ] Upgrade: copy a 0.4.4 Finance database, run `evlet migrate`, then every posted document has a
      v0 release (`GET /api/{model}/{id}/versions`), its ledger is unchanged, and `evlet-finance
      verify` reports nothing.
- [ ] `evlet migrate --plan` reports no difference; the pinned migration checksums still match.
- [ ] `evlet journal verify` reports no breaks.
