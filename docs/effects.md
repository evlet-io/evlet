# Effects

An effect is a set of rows a document posts to another model when a move takes it into a
state. An issued invoice posts ledger entries and stock moves, for example. The rows are a
pure function of the record. The engine writes them in the move's transaction, and they are
read only for every caller. When the record is voided, the engine writes mirror rows that
cancel them. Original rows are never changed or deleted.

This guide covers Phase 6 of the model handoff. Read [states-actions.md](states-actions.md)
first for states, moves, void and revise.

## Declaring effects

```python
from evlet import Boolean, Currency, Decimal, Effect, Link, Model, Move, Negate, Reference, Rule, State, Swap, Text


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
    status = State(("draft", "issued", "paid", "void"),
                   moves=(Move("draft", "issued", "Issue"), Move("issued", "paid", "Mark paid")),
                   locked=("issued", "paid", "void"), final=("paid", "void"), voidable=("issued",))

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
* `on` is the state that posts the rows.
* `reverse` says how the engine cancels a row on void: `Swap("debit", "credit")` exchanges
  two fields, `Negate("qty")` (or `Negate("qty", "value")`) flips the sign of each field.
* `balanced=("debit", "credit")` requires the sum of the first field over all rows of the
  batch to equal the sum of the second, as exact decimals. Empty values count as zero.

A model declares at most one effect per target model.

## What registration checks

Startup fails with a message naming the model when:

* The model has no State, or is a `Table` row. Effects belong on root documents.
* `on` is not a state, is the default state, no declared move enters it, or no sequence of
  declared moves from the default state reaches it.
* A record could enter `on` twice, because a move leads back to it. It would post twice.
* A state a record may be in after `on` is not locked. Posted rows must keep matching a record
  that ordinary writes and deletes cannot change.
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
* `reverse` is missing while a record can be voided after posting (a voidable state is
  reachable from `on`). When the record can never be voided after posting, `reverse` is
  optional.

## Posting on a move

When a move enters `on` (over HTTP, `context.move()` or a service), after the record and its
rows are saved and before the move completes:

1. The engine calls the rows method on the saved document.
2. Each row is validated as a new record of the target. A declarative target settles like any
   new record: defaults, calculations, `required`, `required_when`, `applies_when`, validators
   and `validate()`. A plain model checks its fields and `validate()`. No role applies, because
   the engine writes the rows: every Link must name a stored record (`The linked record does
   not exist`), and Link filters are decided on that record as stored. Linked records are read
   in batches, one statement per linked model.
3. Rows may set only the target's own source fields. `source`, `reversal`, calculated fields
   and unknown names are refused (`Not a field the rows may set`).
4. The engine fills `source` with the record (`{"model": "invoice", "id": 7}`) and `reversal`
   with false, checks `balanced`, and writes the rows.

Effects run in declaration order. Any failure rolls back the whole move: the state, the
`run` method's changes and every row of every effect. A failure is a 422 field error under
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

## Void and revise

When a record is voided (or revised, which voids it), the engine writes one mirror row for
each original row of every effect with a `reverse`:

* The mirror has the original's source values with `reverse` applied, the same `source`, and
  `reversal` set to true. Calculated fields of the target are calculated again.
* Mirror rows are validated like the originals. If one is refused (a target validator that
  rejects it), the void fails with `The reversal of StockMove row 1 is not valid: ...` and
  rolls back. A balanced effect also checks the balance of the mirror rows.
* Original rows are never changed or deleted. A record whose rows were reversed already is
  not reversed again.
* Revising a void record that has no revision yet does not void it again, so nothing is
  reversed a second time. The revision is a new draft with no rows; it posts its own rows when
  it enters `on`.

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
  therefore reads the new number. A revision's copy has no number and no rows; it gets both
  when it enters that state. Reversal rows keep the values of the originals, including a
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
snapshot (a read transaction on SQLite, `REPEATABLE READ READ ONLY` on PostgreSQL), so it may
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

Which records must have posted is decided from the State's moves: a record in a state that
can only be reached through `on` must have rows. When a state could also be reached another
way, the record is compared only when it has rows. A void record that posted must also have
one mirror row for each row it should have posted.

A record changed after posting, for example by an action on a locked record, is reported as
`changed`: the saved rows no longer match the record.

## Notes and limits

* The engine indexes the target's `source`: an effect sets `index=True` on it, which creates
  one index over `(source_model, source_id)`. Details, void and the check find a record's rows
  with an index search. `Reference(index=True)` works on any model, on SQLite and PostgreSQL.
  A target table created before it was an effect target needs a migration to gain the index:

  ```python
  Migration("0007_ledger_source_index", AddIndex("ledgerentry", "source"))
  ```

  Without it, startup reports that an explicit index migration is required.
* Effects post only on a move. Creating a record in a state, or changing it inside
  `tx.maintenance()`, posts nothing.
* The SolidJS form does not show `_effects` yet.
