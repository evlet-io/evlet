# Effects

An effect is a set of rows a document posts to another model when a move takes it into a
state. An issued invoice posts ledger entries and stock moves, for example. The rows are a
pure function of the record. The engine writes them in the move's transaction, and they are
read only for every caller. When a rule takes the record out of that state again (void,
reopen), the engine writes mirror rows that cancel them. Original rows are never changed or
deleted.

Read [states-actions.md](states-actions.md) first for states, rules and moves.

## Declaring effects

```python
from evlet import Boolean, Currency, Decimal, Effect, Link, Model, Negate, Reference, Rule, State, Swap, Text


class LedgerEntry(Model):
    permissions = {"admin": Rule(("read",)), "editor": Rule(("read",)), "viewer": Rule(())}
    account = Link(Account, required=True)
    debit = Currency(default=0)
    credit = Currency(default=0)
    memo = Text(default="")
    source = Reference()
    reversal = Boolean(default=False)


class StockMove(Model):
    item = Text(required=True)
    qty = Decimal()
    source = Reference()
    reversal = Boolean(default=False)


class Invoice(Model):
    class Meta:
        calculation_language = "finite-v1"
        numeric_profile = "finite40-hu18-v1"
        effects = (
            Effect(LedgerEntry, rows="ledger_rows", on="issued",
                   reverse=Swap("debit", "credit"), balanced=("debit", "credit")),
            Effect(StockMove, rows="stock_rows", on="issued", reverse=Negate("qty")),
        )

    account = Link(Account)
    lines = Table(InvoiceLine)
    total = Currency(compute=lines.sum("amount"))
    status = State(
        State.Step("draft"),
        State.Step("issued", lock=True, release=True),
        State.Step("void", color="rose", lock=True, final=True),
        rules=(State.Rule("draft", "issued", "Issue", key="issue"),
               State.Rule("issued", "draft", "Reopen", key="reopen", by=("admin",), remark="required"),
               State.Rule("issued", "void", "Void", key="void", by=("admin",), remark="required")))

    def ledger_rows(self):
        rows = [{"account": self.account, "debit": line.amount, "memo": line.item} for line in self.lines]
        return rows + [{"account": self.account, "credit": self.total, "memo": "revenue"}]

    def stock_rows(self):
        return [{"item": line.item, "qty": -line.qty} for line in self.lines]
```

`Effect(target, rows=..., on=..., reverse=None, balanced=None)`:

* `target` is the model class, or its name (`"stockmove"` or a resource id).
* `rows` names a model method that takes only `self` and returns a list of dicts, one per
  row. `self` is the saved document: every stored field, stored results included, and its
  Table rows at every depth, read with full access whatever the caller may read. It is read
  only; assigning a value fails the move. Reading a virtual result (`stored=False`) fails the
  move with `Invoice.x is a virtual result (stored=False), which effect rows cannot read`;
  store the result, or calculate the value in the method. The handoff sketched `ledger_rows(self, tx)`; the method takes no transaction, so its rows
  depend only on the record and the check command can recompute them.
* `on` is the state that posts the rows: a `lock=True, release=True` step that is not the
  default. A record has live rows exactly while it is in `on`.
* `reverse` says how the engine cancels a row when a rule leaves `on`: `Swap("debit", "credit")` exchanges
  two fields, `Negate("qty")` (or `Negate("qty", "value")`) flips the sign of each field.
* `balanced=("debit", "credit")` requires the sum of the first field over all rows of the
  batch to equal the sum of the second, as exact decimals. Empty values count as zero.

A model declares at most one effect per target model.

## What registration checks

Startup fails with a message naming the model when:

* The model has no State, or is a `Table` row. Effects belong on root documents.
* `on` is not a step, is the default, or is not declared `lock=True, release=True`. Posted rows
  must keep matching a record that ordinary writes and deletes cannot change.
* `rows` is not a method of the model, or takes more than `self`.
* The target is the model itself, a `Table` row, has a State or a Table, or posts effects
  itself.
* The target does not declare `source = Reference()` and `reversal = Boolean(default=False)`.
* A field in `Swap`, `Negate` or `balanced` is not a stored number field of the target
  (Integer, Decimal, Currency or Percent).
* `Swap` names fields of different types, scales or bounds, or `Negate` names a field whose
  bounds are not symmetric (no bounds, or a minimum equal to minus the maximum). A reversal
  row must always be valid when its original was.
* `reverse` changes one field of the `balanced` pair but not the other, so reversal rows would
  not balance. Swapping the balanced pair, negating both or negating neither is fine.
* `reverse` is missing while a rule leaves `on` (the message names the rule). When `on` is
  final, `reverse` is optional.

## Posting on a move

When a move enters `on` (over HTTP, `context.move()` or a service), after the record and its
rows are saved and before the move completes:

1. The engine calls the rows method on the saved document.
2. Each row is validated as a new record of the target. A declarative target settles like any
   new record: defaults, calculations, `required`, `required_when`, `applies_when`, validators
   and `@validate`. A plain model checks its fields and `@validate`. No role applies, because
   the engine writes the rows: every Link must name a stored record (`The linked record does
   not exist`), and Link filters are decided on that record as stored. Linked records are read
   in batches, one statement per linked model.
3. Rows may set only the target's own source fields. `source`, `reversal`, calculated fields
   and unknown names are refused (`Not a field the rows may set`).
4. The engine fills `source` with the record (`{"model": "invoice", "id": "01hc1sd9vc1ye"}`) and `reversal`
   with false, checks `balanced`, and writes the rows.

Effects run in declaration order. Any failure rolls back the whole move: the state, the
hooks' changes and every row of every effect. A failure is a 422 field error under
`_effects`, for example:

```json
{"fields": {"_effects": "LedgerEntry rows are not balanced: debit totals 0.30 and credit totals 0.29"}}
{"fields": {"_effects": "StockMove row 2 is not valid: item: This item cannot move"}}
```

A role that cannot read the target gets `The rows this record posts to other records are not
valid` instead, and field names it cannot read are not shown. A role that cannot read both
balanced fields gets `LedgerEntry rows are not balanced` without the totals.

A rows method that raises, returns something other than a list of dicts, or changes the
record also fails the move with 422 and rolls it back:
`Invoice.stock_rows could not produce the StockMove rows`. The exception is written to the
server log and never shown.

Each row costs one INSERT statement (plus the calculation version of a finite-v1 target, and
an event when events are enabled). Posting 500 rows runs about 520 statements in all.

## Effect rows are read only

A model named as the target of an effect is an effect target. Its rows are written only by
the engine:

* HTTP create, update and delete answer 403 for every role. The schema lists `create`,
  `update` and `delete` as false, and the target's entry has `effect_target: true`.
* `Context.create`, `Context.update` and `Context.delete` answer 403, so dialogs, services and
  model actions that write through `Context` cannot change them either.
* Raw writes (`Session.create`, `Session.update`, `Session.delete`, the `Database` helpers and
  legacy `Action` handlers) raise an error, except inside `tx.maintenance()` for an audited
  repair.
* Attachments on target rows cannot be added or removed (they need update permission).

Reads follow the target's normal permissions and row scopes.

The check is the same rule as `Meta.api = "read"` ([model-engine.md](model-engine.md#how-much-of-a-model-callers-reach)),
which an app uses for rows its own posting code writes instead of the engine.

## Posted records keep their totals

Once a record has posted (it has live effect rows: the latest row of some effect is an
original, not a reversal), its stored calculated values are
frozen for every writer, so the rows it posted keep matching it:

* Moves and actions on it keep the saved calculated values at every depth, like ordinary
  writes on a locked record, even when a linked record or a formula changed since. They need
  no recalculation request after a deploy that changes formulas.
* Its actions, hooks and move values may not change a source field or the Table rows its
  stored results read; the change is refused with 409, for example `Quantity feeds posted
  totals. To change it, move the record out of its posted state.` Fields no stored result
  reads still change. See [states-actions.md](states-actions.md#ordinary-writes-and-locks).
* `"_calculation": {"recalculate": true}` on it is refused with 409: `This record has
  postings. To change its totals, move it out of its posted state.`
* A draft preview (`/api/_draft`) of it shows the saved values, as the save would keep them.
* The `Recompute` migration operation skips it, and a record that posted and is now in a final
  state (void), and reports how many records it kept. Locked records that never posted, and
  records that left `on` for a step that is not final, are recalculated. An admin may include
  posted records with `evlet migrate <instance> --recompute-posted`; each one that changes gets
  a `recomputed` Journal entry with `"posted": true`, and its saved rows no longer match it until
  it leaves `on` and enters it again (the check below reports them as `changed`).

To correct the totals of a posted record, move it out of `on` with a rule (Reopen), correct it
and move it back in, or void it.

## Leaving the posting state

When a rule leaves `on`, before any `@state.exit` hook runs, the engine writes one mirror row
for each live original row of every effect with a `reverse`:

* The mirror has the original's source values with `reverse` applied, the same `source`, and
  `reversal` set to true. Calculated fields of the target are calculated again.
* Mirror rows are validated like the originals. If one is refused (a target validator that
  rejects it), the move fails with `The reversal of StockMove row 1 is not valid: ...` and
  rolls back. A balanced effect also checks the balance of the mirror rows.
* Original rows are never changed or deleted.
* The record's [Journal](journal.md) gets an `effects` entry with `"reversal": true`, and a
  move that posts rows gets one with `"reversal": false`, after the `moved` entry.

A record that is reopened and moved into `on` again posts fresh rows from the record as it is
then. Its rows are therefore a series of postings, each a batch of originals followed, once it
is cancelled, by its batch of mirror rows:

```
originals (issue) | mirrors (reopen) | originals (issue) | mirrors (void)
```

* The live rows are the originals after the last mirror batch, so nothing is reversed twice.
* `_effects` counts every original and every mirror row of the record.
* A record that ever posted is never deleted: its rows still name it as their source. Delete
  answers 409.

## Closed periods

When a target declares `Meta.period` ([model-engine.md](model-engine.md#closed-periods)),
its rows obey the period lock like any record:

* **Posting.** A move that would write a row dated in a closed period is refused. The error
  is on the posting document's own period field when it declares `Meta.period`, otherwise on
  `_effects`, for example `"Posting rows: This date is in a closed period (locked up to
  31-03-2026)"`.
* **Mirror rows.** When a rule leaves `on`, a mirror row keeps the original row's date while that
  date is open. When it is closed, the mirror takes the first open date instead: the day after
  the lock that applies to the row, or today in the instance timezone when that is later. The
  cancellation is then booked in the first period still open, and the closed one is not
  touched.
* **The document itself.** When the posting document declares `Meta.period` and is in a
  closed period, its moves are refused, whatever its rows' dates.

## Details and schema

Detail responses of a model with effects add `_effects`, outside the ETag, with one entry per
target the role can read fully (read permission and no row scope on the target):

```json
{"_effects": [{"target": "ledgerentry", "rows": 3, "reversals": 0, "balanced": true},
              {"target": "stockmove", "rows": 2, "reversals": 0}]}
```

* `rows` counts original rows and `reversals` the mirror rows posted for this record.
* `balanced` is shown only for an effect that declares it and a role that can read both
  fields. It is true when the original rows balance and the mirror rows balance.
* A role that cannot read a target gets no entry for it, and no `_effects` at all when it
  reads none.

`/api/schema` entries of a model with effects gain `effects`, one item per target the role
reads fully: `target`, `on` (null when the role cannot read the State), `balanced` (the two
field names, or null) and `reverse` (`{"swap": [...]}` or `{"negate": [...]}`, or null when
the role cannot read those fields).

## With numbers, measures and related records

* **Document numbers.** When a `Sequence` has `assign_on` set to the state an effect posts
  on, both happen in the same move: the number is assigned first, when the move settles and
  before the record is written, and the effects post after the write. The rows method
  therefore reads the new number. Reversal rows keep the values of the originals, including a
  number they copied.
* **Caught failures.** Each `Context` operation runs in a savepoint. When trusted code
  catches a failed move, the rows already written by earlier effects of that move are rolled
  back with it, and the number it took is given back.
* **Measures.** An effect target is an ordinary model for reads, so it can declare
  `Meta.measures` and be summed by another model's per-record measures (for example a ledger
  total per account). Measures and the aggregate route apply the caller's row scope and field
  rules on the target. Reversal rows are counted like any row; filter or group on `reversal`
  to separate them.
* **Related records.** Relations come from Link fields only. The `source` Reference of a
  target is not a Link, so the posting document has no relation listing its rows; its detail
  shows `_effects` counts instead. Links on the target (for example an account) are relations
  of the linked model as usual.

## Checking effects

```sh
evlet effects check acme
```

The command recomputes the rows of every record of every model with effects and compares
them with the saved rows. It writes nothing and takes no write lock: it reads one consistent
snapshot (a read transaction on SQLite), so it may
run while the instance serves. It prints one line per difference and exits with status 1 when
there is any, or prints `No differences` and exits with 0.

From Python, `evlet.effects.check(application)` returns the differences as a list of dicts
with `model`, `record`, `target`, `kind`, `reversal` and, where they apply, `expected`,
`actual` (serialized values), `row` (the saved row id) and `message`. Kinds:

* `missing`: a row the record should have posted is not saved.
* `extra`: a saved row the record should not have, including rows of a record that has not
  entered `on` and rows whose source record no longer exists.
* `changed`: a saved row differs from the one the record produces now. Rows that match
  exactly are paired first; the rest are paired in order.
* `unbalanced`: the saved original rows (or mirror rows) of a balanced effect do not balance.
* `invalid`: the record's rows cannot be produced now: the rows method raises (the message
  names the exception) or a row is not valid. The check goes on with the next record.

A record in `on` must have live rows that match the rows it produces now. A record in any
other state must have no live rows: each posting it made must be cancelled by its mirror rows.
The rows are split into postings (above), and each cancelled posting is checked on its own: its
mirror rows must mirror its originals.

A posted record keeps its saved totals (see above), so moves, actions and `Recompute`
leave the check clean. A record changed after posting by other means, for example a
maintenance write, is reported as `changed`: the saved rows no longer match the record.

## Notes and limits

* The engine indexes the target's `source`: an effect sets `index=True` on it, which creates
  one index over `(source_model, source_id)`. Details, reversals and the check find a record's rows
  with an index search. `Reference(index=True)` works on any model.
  A target table created before it was an effect target needs a migration to gain the index:

  ```python
  Migration("0007_ledger_source_index", AddIndex("ledgerentry", "source"))
  ```

  Without it, startup reports that an explicit index migration is required.
* Effects post only on a move. Creating a record in a state, or changing it
  inside `tx.maintenance()`, posts nothing.
* The record view shows `_effects` in its Postings tab; the connector and the MCP server
  do not show them yet.
