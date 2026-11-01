# Finite calculations (finite-v1)

A document's formulas are declared once in Python. Evlet compiles them into a typed,
versioned plan. The browser runs the plan as a preview. The server runs the same plan
as the authority before it saves. This guide is the public contract. The implementation
ledger is in [model-engine-implementation-status.md](model-engine-implementation-status.md).

## Declaring a document

```python
from evlet import Model, Child, Table, Decimal, Currency, Boolean, Rule
from evlet.calc import D, F, parent, row, round, coalesce, safe_divide

class InvoiceItem(Child):
    qty = Decimal(default=1, minimum="0")
    rate = Currency(default=0, minimum="0")
    vat_rate = Decimal(scale=4, default=5)
    taxable = Boolean(default=True)
    effective_rate = Decimal(scale=4, stored=False, compute=taxable * vat_rate)
    entered_amount = Currency(compute=round(qty * rate, 2))
    net_raw = Decimal(scale=None, stored=False,
                      compute=entered_amount / (1 + parent.inclusive * effective_rate / 100))
    net = Currency(compute=round(net_raw, 2))
    vat = Currency(compute=parent.inclusive * (entered_amount - net)
                   + (1 - parent.inclusive) * round(net * effective_rate / 100, 2))
    total = Currency(compute=net + vat)

class Invoice(Model):
    class Meta:
        calculation_language = "finite-v1"
        numeric_profile = "finite40-hu18-v1"
    permissions = {"admin": Rule(("read", "create", "update", "delete"))}
    inclusive = Boolean(default=True)
    items = Table(InvoiceItem)
    net_total = Currency(compute=items.sum("net"))
    taxable_net = Currency(compute=items.where(taxable=True).sum("net"))
    grand_total = Currency(compute=items.sum("total"))
```

A root selects finite-v1 with `Meta.calculation_language`, or by using expression
computes anywhere in its tree. Its owned tables inherit the plan. Legacy string formulas
(`compute="qty * rate"`) keep the legacy compiler and HALF_EVEN profile. One root cannot
mix the two. Finite-v1 roots do not support `Query` sources yet.

## Operators

The language is closed:

| Operation | Contract |
| --- | --- |
| `+ - * /`, unary `-` | Exact decimals. Division gives 18 places, HALF_UP. |
| Boolean in arithmetic | A Boolean field becomes exactly 0 or 1. Null is not False. |
| `table.sum("f")`, `count()`, `min("f")`, `max("f")` | Over the complete collection. `sum` of no rows is 0; `min`/`max` of no rows is null. |
| `.where(flag=True)`, `.where(row.amount > F("limit"))` | Typed predicates; several filters combine with AND. |
| `== != < <= > >=` | Typed. Ordering needs numbers. `True == 1` is a type error. |
| `& | ~` | Boolean predicates only. |
| `round(x, n)` | HALF_UP to a fixed scale 0..36. |
| `coalesce(x, fallback)` | Uses the fallback only when `x` is a ready null. |
| `safe_divide(a, b, default=d)` | Uses the default only when `b` is numeric zero. |

`D("0.05")` is an exact decimal literal. `F("name")` refers to a field on the same model,
including one declared later. `parent.name` is the immediate owner. `row.name` is only
valid inside `.where(...)`. There is no `parent.parent`, row indexing, string formula,
`if`/`when`, scan, allocation or callback. Python `if`, `and`, `or`, `not` and chained
comparisons on expressions raise `EXPRESSION_TRUTH_TEST`.

Field-to-field equality stays identity based so fields remain safe dictionary keys.
Compare two fields with `F("a") == F("b")`. `qty == 1` and `qty == None` build
expressions. A field compared with another field or `MISSING` keeps Python identity
semantics; comparing a field with any other value in plain code raises
`EXPRESSION_TRUTH_TEST` when truth-tested, so use `is` (`value is None`) there.

Rounded shares are not reconciled automatically. Expose the difference as a named field
and validate or display it:

```python
allocated = Currency(compute=round(parent.amount * weight / parent.weights, 2))  # on the row
residual = Currency(compute=amount - shares.sum("allocated"))                    # on the document
```

## Writers, scales and storage

* A computed field has one writer: it cannot also have a default, fetch or query.
* Results must fit the target exactly. `Currency(compute=qty * rate)` that yields
  `1.005` is a `COMPUTE_SCALE_ERROR`; write `round(qty * rate, 2)`.
* `Decimal(scale=None, stored=False, compute=...)` keeps the full bounded value for
  intermediates. It cannot be stored or edited.
* `stored=False` fields are calculated but are not SQL columns.
* `default=Ref("parent.warehouse")` copies the value once on creation; it is not live.
  Use `compute=parent.warehouse` for a live relay.

## Results and states

Each calculated value is `ready` (possibly null), `pending` or `error`:

* Null arithmetic and ordered comparisons with null are pending
  (`COMPUTE_INPUT_MISSING`), never zero.
* `&` and `|` evaluate both sides; an error beats pending.
* A row a filter definitely excludes does not block the aggregate. A selected null
  value blocks it.
* Division by zero, range overflow and scale mismatches are errors.

A save requires every stored result to be ready. Any error blocks the save. A pending
virtual result that nothing stored depends on may stay pending.

## Graph and cycles

Each root compiles a template graph over every owned table. Value cycles fail at
registration, even for empty tables or currently false filters, with a closed path:

```text
MODEL_DEPENDENCY_CYCLE: lines[*].allocated -> unit -> allocated_total -> lines[*].allocated
```

Owned tables can nest up to 8 levels under finite-v1 roots. A table that contains its own
model type fails with `MODEL_CONTAINMENT_CYCLE`.

## Server writes

All ordinary writes settle the document in the existing transaction:

* Routes, `@action`, dialogs and services write through `Context`, which loads the whole
  tree, merges permitted source changes, settles, validates and persists parent before
  child. Any failure rolls back everything.
* Nested rows use the same `children` envelope at every depth. `_key` values must be
  unique across the document; `_row_ids` maps them to new IDs.
* Clients send source fields only. A submitted calculated value, even an equal one, is
  `COMPUTED_FIELD_WRITE`. Actions cannot assign calculated fields either.
* Removing a row removes its subtree and needs delete permission on every descendant.
* A client may pin the plan it previewed with `"_calculation": {"model_schema_hash": ...}`
  in POST/PATCH bodies (or `"calculation"` in `/api/_draft`). A different active plan is
  a 409 `SCHEMA_VERSION_MISMATCH`. Responses include the active `_calculation`.
* Legacy `Action` handlers cannot be registered on finite-v1 roots.
* `Session.create/update/delete` refuse finite-v1 models unless wrapped in
  `with tx.maintenance():` for an audited repair. The caller must restore calculated
  values afterwards; maintenance does not recompute them.

Reading a saved record never recalculates or rewrites it. Each saved finite-v1 document
records the plan it was calculated with (`evlet_calculation_versions`, created at startup
for apps with finite plans; a document with no recorded plan adopts the active one on its
next write). Writing a document saved under another plan is a 409
`SCHEMA_VERSION_MISMATCH` until the client resubmits with
`"_calculation": {"model_schema_hash": <active>, "recalculate": true}`. Actions, dialogs
and services that write such a document get the same 409; recalculate it through the
route first. Formula changes never backfill stored totals on their own.

## Permissions and the browser plan

A calculated result is readable only when every input it reads is readable and every
collection it aggregates is complete for that user (no child row scope). Otherwise the
result is withheld. A withheld result still has to be valid for a save, so a user who can
write its sources learns whether a save failed: avoid bounds such as `minimum` on results
over inputs a writer cannot read. `/api/schema` reports, per finite root, the plan hash and a
capability per calculated field the user can see (`local_universal` or `unavailable`). The plan is included only when every
calculated field is local for that user. A shipped plan lists only fields that user can
read (no hidden names, bounds or defaults); it carries its own hash and
`source_plan_hash`, the active plan's hash. The existing SolidJS form previews finite
results through `/api/_draft`; it does not edit nested tables below the first level.

## Browser engine

`frontend/src/runtime/FiniteEngine.ts` evaluates the same plan with no SolidJS or DOM
imports. A `FiniteDocument` session offers `set`, `addRow`, `removeRow`, `replaceRows`,
`reorder`, `moveRow`, nested `batch`, `observe`, `observeTable`, `explain`, `snapshot`
and `dispose`. A batch publishes once; observers cannot mutate during publication
(`REACTIVE_REENTRANCY`); an async batch callback is rejected. Python's
`evlet.calc.Document` has the same interface in snake_case.

Shared fixtures (`frontend/src/tests/finite-fixtures.json`) are generated by
`scripts/generate_finite_fixtures.py` from Python plans and results. The browser replays
them and must match snapshots, states, causes, revisions and evaluation counts.

## Limits

Expressions: 128 nodes, depth 32. Tables: depth 8, 100 rows per collection, 1,000 owned
rows per root. Graph: 50,000 nodes, 250,000 edges. Evaluation: 1,000,000 steps per batch.
Decimal text: 128 characters. Plans carry these limits and both runtimes refuse others.
