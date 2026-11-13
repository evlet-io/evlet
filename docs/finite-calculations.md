# Finite calculations (finite-v1)

A document's formulas are declared once in Python. Evlet compiles them into a typed,
versioned plan. The browser runs the plan as a preview. The server runs the same plan
as the authority before it saves. This guide is the public contract. The implementation
ledger is in [model-engine-implementation-status.md](internal/model-engine-implementation-status.md).

## Declaring a document

```python
from evlet import Model, Child, Table, Decimal, Currency, Boolean, Rule
from evlet.calc import D, F, parent, row, round, coalesce, safe_divide, when, days, today

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

A `Child` is registered only through a `Table`. `Registry(InvoiceItem)` is refused with
`InvoiceItem is a Child row model. Declare row models inside their document with Table(...)`.
In an app's `models.py`, where discovery registers every model class it finds, nest the row
class in its document (`class Invoice(Model): class Item(Child): ...`).

A root selects finite-v1 with `Meta.calculation_language`, or by using expression
computes, `initial=` or a field rule (`applies_when`, `required_when`, `locked_when`)
anywhere in its tree. Such a model is written through `Context` and the HTTP routes, like any
finite-v1 document. Its owned tables inherit the plan. Legacy string formulas
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
| `round(x, n)` | HALF_UP to a fixed scale 0..36, whatever the field's `rounding`. |
| `coalesce(x, fallback)` | Uses the fallback only when `x` is a ready null. |
| `safe_divide(a, b, default=d)` | Uses the default only when `b` is numeric zero. |
| `when(cond, a, b)` | `a` when `cond` is true, else `b`. `cond` is Boolean; `a` and `b` share a type. Only the chosen branch is evaluated, so only its errors count. A null or pending condition makes the result pending. |
| `date + days(n)`, `days(n) + date`, `date - days(n)` | A date. `n` must be a whole number; a fraction is `COMPUTE_TYPE_ERROR`. |
| `date - date` | The number of days between them, as a decimal. |
| `today()` | The current date, supplied by the host. |
| `F("link.field")` | A field of a linked record, through up to three Link fields. |

`D("0.05")` is an exact decimal literal. `F("name")` refers to a field on the same model,
including one declared later. `parent.name` is the immediate owner. `row.name` is only
valid inside `.where(...)`. There is no `parent.parent`, row indexing, string formula,
`if`, scan, allocation or callback. Python `if`, `and`, `or`, `not` and chained
comparisons on expressions raise `EXPRESSION_TRUTH_TEST`.

Field-to-field equality stays identity based so fields remain safe dictionary keys.
Compare two fields with `F("a") == F("b")`. `qty == 1` and `qty == None` build
expressions. A field compared with another field or `MISSING` keeps Python identity
semantics; comparing a field with any other value in plain code raises
`EXPRESSION_TRUTH_TEST` when truth-tested, so use `is` (`value is None`) there.

### Helpers

These helpers are shorthand. Each builds exactly the formula of its long form, so a model
rewritten with them keeps its plan and formula hash, and saved documents need no
recalculation. Import them from `evlet.calc`.

| Helper | Same formula as |
| --- | --- |
| `choose(F("stage"), {"won": 100, "lost": 0}, default=10)` | `when(F("stage") == "won", 100, when(F("stage") == "lost", 0, 10))`. Cases are checked in the order written. Without `default`, an unmatched value gives null. |
| `F("status").isin("new", "failed")`, or `isin(F("status"), "new", "failed")` | `(F("status") == "new") \| (F("status") == "failed")` |
| `is_empty(F("won_on"))` | `F("won_on") == None` |
| `has_value(F("contact"))` | `F("contact") != None` |
| `least(a, b, ...)` | `when(a <= b, a, b)`, repeated for each further value |
| `greatest(a, b, ...)` | `when(a >= b, a, b)`, repeated for each further value |
| `absolute(x)` | `when(x < 0, -x, x)` |

`is_empty` and `has_value` read better than `== None` and need no `# noqa: E711`. For a
Boolean filter on rows, `.where(recurring=True)` is the same formula as
`.where(row.recurring == True)`. `least`, `greatest` and `absolute` repeat their inputs in
the formula, so very long inputs count more than once toward the 128-node limit.
`absolute` is not called `abs` so importing it never hides Python's built-in `abs()`.

```python
probability = Percent(compute=choose(F("stage"), {"won": 100, "negotiation": 75, "proposal": 50},
                                     default=0))
payout = Currency(compute=least(F("claimed"), F("limit")))
```

Rounded shares are not reconciled automatically. Expose the difference as a named field
and validate or display it:

```python
allocated = Currency(compute=round(parent.amount * weight / parent.weights, 2))  # on the row
residual = Currency(compute=amount - shares.sum("allocated"))                    # on the document
```

## Dates

`Date` fields hold ISO `YYYY-MM-DD` values from 0001-01-01 to 9999-12-31. Dates compare
with `== != < <= > >=`. A result outside that range is `COMPUTE_RANGE_ERROR`. Mixed types
fail when the registry is built with `COMPUTE_TYPE_ERROR`: `date + 1`, `date * 2`,
`date + date`, `days(n)` on its own, `days(True)`, or a date compared with a number.
`DateTime` fields keep their earlier rules: they compare with `==` and `!=` and work in
`coalesce` and `when`, but they cannot be ordered or used in date arithmetic, and a Link
path cannot end at one.

```python
class Bill(Model):
    issued = Date()
    due = Date(compute=F("issued") + days(F("customer.credit_days")))
    age = Integer(compute=today() - F("issued"))
    overdue = Boolean(compute=today() > F("due"))
```

`today()` is an input, not a clock read inside the evaluator. The server supplies the date
in the instance timezone (`Instance(timezone="Asia/Dubai")` in the instance `config.py`; `UTC` by default). The browser
calls `setToday("2026-11-01")` on its document session; until it does, results that use
`today()` are pending. A stored result that uses `today()` keeps the date of its last save;
it is not refreshed when the day changes.

## Link paths

`F("customer.credit_days")` reads `credit_days` from the record that the `customer` Link
points to. A path follows at most three Links (`F("customer.region.country.vat_rate")`).
The registry rejects an unknown field (`UNSUPPORTED_EXPRESSION`), a step through a field
that is not a Link (`COMPUTE_TYPE_ERROR`), a field that is not stored, and more than three
Links (`CALCULATION_LIMIT_EXCEEDED`). Paths start on the declaring model; `parent.x.y` is
not supported, so relay the Link with `compute=parent.customer` first.

The evaluator never queries the database. Each plan table lists the `lookups` it needs
(`{"model": "customer", "fields": [...]}`), and the host supplies the values:

* The server reads them inside the save transaction, one query per looked-up model per
  Link step, before it validates the document. It reads stored values with full access, so
  a stored result never depends on who saved it.
* The browser calls `lookupRequests()` for the `[model, id, fields]` still missing and
  `setLookups(model, id, values)` with what it read. Only results on those paths are
  recalculated.

A null Link at any step gives a ready null for the path. The usual rules then apply:
arithmetic with null stays pending (`COMPUTE_INPUT_MISSING`) and does not become null, so a
stored `due` over a missing customer blocks the save. Use `coalesce` or `when` to choose a
value instead. A Link value without supplied lookups is pending.

A stored result that reads a Link path keeps the value from its last save. When the linked
record changes, the stored value is updated on the next write of the document, not before.
The formula hash does not track linked values, so such documents are not reported as
needing recalculation.

## Initial suggestions

`initial=` suggests a value for an editable field. It uses the same language as `compute=`:

```python
class InvoiceLine(Child):
    item = Link(Item)
    qty = Decimal(default=1)
    rate = Currency(initial=F("item.selling_rate"))
    amount = Currency(compute=F("qty") * F("rate"))

class Invoice(Model):
    customer = Link(Customer)
    terms_days = Integer(initial=F("customer.credit_days"))
    warehouse = Link(Warehouse)
    lines = Table(InvoiceLine)
```

* On create, and whenever an input the expression reads changes in a write, the engine sets
  the field from the expression, unless the caller supplied that field in the same write.
  Sending an input with the value it already has is not a change.
* A later edit by the user is kept until an input changes again. The field stays editable;
  the suggestion is not a binding.
* Inputs are the document's own fields, `parent.x`, collections and calculated fields. A
  linked record that changes on its own, or a new day for `today()`, does not trigger a
  suggestion: those values are read when a suggestion fires.
* A suggestion is written when its result is ready, including a ready null (an empty Link
  clears `rate`). While it is pending, for example waiting for a Link lookup, it waits and is
  written when the value arrives, unless the field is set first. An error leaves the field
  unchanged. A locked field is never overwritten by its suggestion: a suggestion that meets a
  lock is dropped, not kept for later, and fires again only when an input changes again.
* `initial` cannot be combined with `compute`, `default`, `fetch` or `query` on the same
  field. `fetch="link.field"` and `default=Ref("parent.x")` keep working as before.
* A suggestion is quantized like a calculation, but the field's bounds and validator apply
  to the value as a normal edit.

On an update the server starts from the saved document and applies the write's changes as
one batch, exactly as a browser session does, so both decide the same way.

## Field rules

Three Boolean expressions describe when an editable field applies, is required, or is
locked. They are the way forward; the legacy strings `visible_if`, `required_if` and
`read_only_if` keep working for legacy documents.

```python
class Deal(Model):
    stage = Select(("open", "won", "lost"), default="open")
    lost_reason = Text(applies_when=F("stage") == "lost", required_when=F("stage") == "lost")
    discount = Percent(locked_when=F("stage") == "won")
```

The server enforces them on every create and update, including `Context` writes and model
actions. Legacy `ChildTable` rows do not run finite rules or calculations, so registering a
`ChildTable` whose rows declare any is refused; declare such rows with `Table(Child)`.

* **applies_when** false: the field is not required, its validator does not run, and its
  value is stored as null. The engine clears it as soon as the rule turns false, so
  calculations that read it see null in the browser and on the server.
* **required_when** true: an empty value is the field error `This field is required`.
* **locked_when** true: a changed value is the field error `This field is locked and cannot be
  changed now`. Only the caller's changes count (request body, `Context` values, action
  handlers); values the engine writes do not, with one exception: a write whose change makes a
  locked field stop applying would clear it, so it is refused with `This field is locked, so a
  change that would clear it is not allowed`, and a session keeps the saved value. On an update the lock is decided on the document
  as saved, before this write's changes, so one write cannot unlock a field and change it, and a
  lock that reads its own field (`locked_when=F("price") > 100`) keeps a saved 500. A new record,
  and a new row added to a saved record, have no saved state, so nothing on them is locked: their
  fields may be filled in any order, and initial suggestions fill them as usual. A field that
  locks once another is set (`locked_when=F("signed_at") != None`) can be entered in the same
  create as that other field; it is locked from the next write on. The browser follows the same
  rule. Saving the unchanged value is allowed.

A rule with no ready true or false value (pending, an error, or null) is undecided.
Undecided rules fail closed:

| Rule | Undecided means |
| --- | --- |
| `applies_when` | the field applies; nothing is cleared |
| `required_when` | the field is required |
| `locked_when` | the field is locked |

So `required_when=F("amount") > 1000` makes the field required while `amount` is empty.
Use `coalesce(F("amount"), 0) > 1000` when an empty value should not require it. A pending
lock explains itself (`This field is locked until the values its lock rule reads are filled
in`) only to a role that can read every input of the rule.

Rules cannot be declared on calculated fields. `applies_when` cannot be combined with
`required=True` (use `required_when`), `required_when` with `required_if`, or `locked_when`
with `read_only_if`. A rule must be Boolean (`COMPUTE_TYPE_ERROR` otherwise).

### Order and cycles

Initial suggestions and rules join the template graph. A field's value is written by its
calculation, or by its `initial` and then its `applies_when`; readers follow the last writer.
An `initial` waits for its own field's `locked_when`. So these fail at registration with
`MODEL_DEPENDENCY_CYCLE`: a calculation and an initial that read each other, an initial that
reads its own field, and an `applies_when` that reads its own field.

### Permissions

Rules may read fields or Link paths the user cannot read; the server enforces them anyway.

* `/api/schema` lists a rule's expression only when the role can read every input. The
  shipped browser plan leaves such a rule out, together with lookup fields and owned tables the
  role cannot read, and the schema `order` names only readable fields and tables.
* An initial suggestion over inputs a role cannot read makes its field unreadable for that
  role, exactly like a calculated result, because the value would reveal them. The server
  still applies it on save.
* Error text never names a hidden input or value. The residual signal is the save outcome:
  a writer learns that a hidden rule is true (or undecided) when a change is rejected as
  locked or required, and that `applies_when` is false when a saved value comes back empty.
  Avoid rules over inputs a writer must not learn about.
* Row limits count every row, including rows a row scope hides. A writer can therefore learn
  roughly how many hidden rows a collection holds by adding rows until "Use at most N rows"
  (or removing them until "Add at least N rows"). The messages name only the declared limit,
  never a count. Avoid tight `max_rows` or `min_rows` on collections whose hidden row count
  must stay private.

### Move guards and action availability

`Move(..., when=...)` and `@action(..., available_when=...)` use the same language and are
decided like `locked_when`: on the record as saved, in the write's transaction, with pending
or error results blocking. They are compiled with the document at registration but are not
entries of the shipped plan, so they do not change any plan or formula hash. A State field is
an ordinary Select in the plan, so rules and calculations read it. The same readability rule
applies: the schema shows a guard only to roles that can read its inputs, and the reasons
other roles get are generic. See [states-actions.md](states-actions.md).

## Field types in expressions

`Percent` and `Duration` are numbers: a percentage is its value (`5` for 5%), so divide by
100 yourself; a duration is whole seconds (`PT1H30M` is 5400), in a browser session too.
`Phone` is text. `Time`, `Tags` and `Reference` fields cannot be read by, or be the
target of, a calculation or an initial (`UNSUPPORTED_EXPRESSION`), and a Link path cannot end
at one. Rules can be declared on them; the rule reads other fields.

## Writers, scales and rounding

* A computed field has one writer: it cannot also have a default, fetch, query or initial.
* A result is quantized to the target field's scale with the field's `rounding`:
  `"half_up"` (ties away from zero) or `"half_even"` (ties to the even digit). The default
  is `"half_up"` for `Currency` and `Integer` and `"half_even"` for `Decimal`. So
  `Currency(compute=qty * rate)` that yields `1.005` stores `1.01`, and
  `Decimal(scale=2, compute=x)` stores `0.12` for `0.125`. Set it per field:
  `Currency(rounding="half_even", compute=...)`.
* A `Currency` result rounds to the decimal places of its own currency, resolved for each row
  from its currency source: `Currency(currency="currency", compute=qty * rate)` stores `2.000` in
  KWD, `2.00` in AED and `2` in JPY for `1.9995`, and a change of the currency recalculates it.
  Intermediate values stay exact, so nothing rounds twice. Prefer an intermediate Currency field
  to `round(x, 2)`, which hard-codes the places
  ([model-engine.md](model-engine.md#decimal-places-per-currency)).
* Range errors stay errors: a value that does not fit the column, or a rounded value
  outside the field's `minimum` or `maximum`, is `COMPUTE_RANGE_ERROR`.
* `round(x, n)` always rounds half up. Use it for an intermediate step or a different scale.
* `Decimal(scale=None, stored=False, compute=...)` keeps the full bounded value for
  intermediates. It is never rounded, and it cannot be stored or edited.
* Legacy string formulas keep their own rules and do not use `rounding`.
* `stored=False` fields are calculated but are not SQL columns.
* `default=Ref("parent.warehouse")` copies the value once on creation; it is not live.
  `default=CurrentUser` copies the signed-in user (see the Permissions section of
  [business-capabilities.md](business-capabilities.md)).
  Use `compute=parent.warehouse` for a live relay.

## Results and states

Each calculated value is `ready` (possibly null), `pending` or `error`:

* Null arithmetic, date arithmetic and ordered comparisons with null are pending
  (`COMPUTE_INPUT_MISSING`), never zero or null.
* `&` and `|` evaluate both sides; an error beats pending.
* A row a filter definitely excludes does not block the aggregate. A selected null
  value blocks it.
* Division by zero, range overflow, invalid dates and fractional day counts are errors.

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

## Owned tables

```python
lines = Table(InvoiceItem, min_rows=1, max_rows=500)
```

* `max_rows` (default 100) bounds one collection, up to 1,000. Values above 1,000, below 1,
  or below `min_rows` are rejected when the model is declared. Tables under a legacy (non
  finite-v1) root keep at most 100 rows; a larger `max_rows` there is rejected at registration.
* `min_rows` (default 0) is checked on every save: "Add at least one row" or "Add at least N
  rows". `required=True` still works and means `min_rows=1`; `required=True` with
  `min_rows=0` is rejected.
* The plan records `min_rows` and `max_rows` on each table entry. The Python and browser
  engines refuse a collection above its `max_rows` with `CALCULATION_LIMIT_EXCEEDED`.
* Rows keep an explicit, stable order in the framework column `evlet_position`. New rows
  without a place append. Reordering is a row change (below).
* `/api/schema` lists `min_rows` and `max_rows` with each child collection. A legacy
  `ChildTable` reports 0 and 100.

### Row changes

POST and PATCH accept each collection either as the full list of rows (rows left out are
removed, as before) or as a change set that names only what changed:

```json
{"children": {"lines": {
  "create": [{"_key": "n1", "qty": "2"}],
  "update": [{"id": "01hc1sd9vc1ye", "qty": "3", "children": {"parts": {"delete": ["01hc1sdb0j8m4"]}}}],
  "delete": ["01hc1sd9vc1yf"],
  "order": ["n1", "01hc1sd9vc1yg", "01hc1sd9vc1ye"]
}}}
```

* Rows the change set does not mention keep their values and their place.
* `create` rows take no `id`. A `_key` works as before: unique in the document, mapped to the
  new ID in `_row_ids`, and used to address errors (`lines.n1.qty`).
* `update` rows carry the `id` of a saved row of this collection. `delete` lists saved IDs.
  An ID may appear once across `update` and `delete`.
* `order` lists saved IDs and the `_key` of rows created in the same request. The listed
  rows take that order within the places they already hold; rows not listed stay where they
  are. Sending every visible row always gives exactly that order. A created row that is not
  listed appends.
* Rows inside `create` and `update` may carry their own `children` in either format, at
  every depth.
* Everything, including recalculated parent totals, commits in one transaction. Any row
  error rolls back the whole request; errors use the same keys as the full format.
* Permissions are the same as for the full format: rows hidden by a row scope are kept and
  cannot be updated, deleted or ordered (the request fails as if the ID did not belong to the
  collection); a delete needs delete permission on the whole subtree; changed rows need
  update permission and must stay inside the row scope; created rows need create permission.
  Rows a request places with `order` (or by a full list) need update permission when their
  position changes. Positions of other rows shift without a check.
* A legacy `ChildTable` registered on `Application` accepts `create`, `update` and `delete`
  on document models. It has no stored order, so `order` is rejected there.
* Request bodies are limited to 64 KiB. Clients that build large collections send rows in
  several change sets.
* A full list is refused with 422 when the collection holds more than 100 rows the user can
  read, because a detail embeds only the first 100 and a list built from it would remove the
  rest. Use a change set for such collections; full lists keep working up to 100 rows.
* Rows hidden by a row scope keep their places and their stored positions, for full lists and
  change sets alike. A full list fills the places of the visible rows in order; extra rows
  append. Other rows keep their stored position when it still fits the new order. Only rows
  saved with equal positions can force a renumbering of the whole collection.

### Reading large tables

A detail response embeds at most 100 rows per collection, at every depth. Collections with
at most 100 readable rows look exactly as before. A larger collection embeds its first 100
rows, and the record gains `children_pages`:

```json
"children_pages": {"lines": {"total": 1000, "limit": 100, "partial": true}}
```

`GET /api/{model}/{id}/lines/{name}?limit=&offset=` returns one page of a collection in
position order: `{"items": [...], "total": n, "limit": l, "offset": o}`. `limit` is 1 to
100 (default 100). Nested collections alternate names and row IDs:
`/api/project/01hc1sd9vc1ye/lines/phases/01hc1sdb0j8m4/tasks`. Every row on the path must be readable and belong
to the row before it. Items have the same shape as embedded rows (readable fields only,
no `evlet_owner` or `evlet_position`), embed their own collections and carry their
virtual (`stored=False`) results, calculated over the whole saved document. `total` counts the
rows this user can read. `Context.get` in Python returns every readable row.

The ETag of a document covers every row the user can read, not only the embedded page, so a
change to row 101 or later (its values or its place) makes an older `If-Match` fail with 409.
Write responses carry the new ETag.

Loading a document costs one statement per table level and chunk of 500 owners for the
rows, plus one per Link target model for permission checks, whatever the row count. See
[performance.md](performance.md#owned-tables).

## Server writes

All ordinary writes settle the document in the existing transaction:

* Routes, `@action`, dialogs and services write through `Context`, which loads the whole
  tree, merges permitted source changes, settles, validates and persists parent before
  child. Any failure rolls back everything.
* Nested rows use the same `children` envelope at every depth, as full lists or change sets
  (see [Row changes](#row-changes)). `_key` values must be unique across the document;
  `_row_ids` maps them to new IDs.
* Clients send source fields only. A submitted calculated value, even an equal one, is
  `COMPUTED_FIELD_WRITE`. Actions cannot assign calculated fields either.
* A document's `derive()` runs before calculations on every write and preview, so formulas
  read the values and rows it builds. Its fields and Tables (`Meta.derives`) are refused to
  clients with `DERIVED_FIELD_WRITE` ([model-engine.md](model-engine.md#derived-values)).
* Removing a row removes its subtree and needs delete permission on every descendant.
* A client may pin the plan it previewed with `"_calculation": {"model_schema_hash": ...}`
  in POST/PATCH bodies (or `"calculation"` in `/api/_draft`). A different active plan is
  a 409 `SCHEMA_VERSION_MISMATCH`. Responses include the active `_calculation`.
* Legacy `Action` handlers cannot be registered on finite-v1 roots.
* `Session.create/update/delete` refuse finite-v1 models unless wrapped in
  `with tx.maintenance():` for an audited repair. The caller must restore calculated
  values afterwards; maintenance does not recompute them.

Reading a saved record never recalculates or rewrites it. Each saved finite-v1 document
records the formula version it was calculated with (`evlet_calculation_versions`, created at
startup for apps with finite plans; a document with no recorded version adopts the active one
on its next write). The formula version covers only the formulas and the fields they read or
write (type, scale, bounds). It also covers `applies_when`, which can clear a value
calculations read, but not `initial`, `required_when` or `locked_when`, which never change
a saved result. Adding an unrelated field or changing a default, label or text
limit does not change it. Writing a document saved under other formulas is a 409
`SCHEMA_VERSION_MISMATCH` until the client resubmits with
`"_calculation": {"model_schema_hash": <active>, "recalculate": true}`. Actions, dialogs
and services that write such a document get the same 409; recalculate it through the
route first. Formula changes never backfill stored totals on their own; use the `Recompute`
migration operation ([model-engine.md](model-engine.md#recalculating-stored-values)) to
recalculate them explicitly.

Locked records and records with posted [effects](effects.md) are the exception. A locked
record keeps its saved results on ordinary writes. A record with postings keeps them on
every write, moves and actions included, and needs no recalculation request; asking for
one is refused with 409 (`This record has postings. To change its totals, void or revise
it.`). Its actions and moves may not change what those results read (409, naming the
field). `Recompute` skips posted records only and reports how many it kept, so the effect
rows a record posted always match its saved totals; a locked record that never posted is
recalculated. An admin may include posted records for one migration run with
`evlet migrate <instance> --recompute-posted`; each changed one is journaled with
`"posted": true` ([model-engine.md](model-engine.md#recalculating-stored-values)). An admin
can also unpost a posted record, correct it and repost it
([states-actions.md](states-actions.md#unpost-and-repost)). Draft previews of a locked or posted record show the values a save keeps.

## Permissions and the browser plan

A calculated result is readable only when every input it reads is readable and every
collection it aggregates is complete for that user (no child row scope). A Link path also
needs every field on the path to be readable and no row scope on any looked-up model.
Otherwise the result is withheld. A withheld result still has to be valid for a save, so a user who can
write its sources learns whether a save failed: avoid bounds such as `minimum` on results
over inputs a writer cannot read. This also applies to values read through Link paths from
other records. A writer can learn from whether a save succeeds that an unreadable linked
value makes the result pending, out of bounds or out of range. Avoid bounds and non-null
requirements on results over linked fields a writer cannot read. `/api/schema` reports, per finite root, the plan hash and a
capability per calculated field the user can see (`local_universal` or `unavailable`). The plan is included only when every
calculated field is local for that user. A shipped plan lists only fields that user can
read (no hidden names, bounds or defaults); it carries its own hash and
`source_plan_hash`, the active plan's hash. Plan entries without `kind` are calculations;
entries with `kind` (`initial`, `applies_when`, `required_when`, `locked_when`) are a field's
suggestion and rules, named `field:kind` in the graph. The record view evaluates the shipped
plan in the browser and the server checks every write again.

A hidden unstored intermediate is a field declared `hidden=True, stored=False` with a finite
`compute=` on a model with `permissions` (where `hidden=True` makes a field unreadable), for example
a line's tax before the document rounds it once:

```python
class Line(Child):
    net = Currency(currency="code", compute=F("qty") * F("price"))
    tax_exact = Decimal(scale=None, stored=False, hidden=True, compute=F("net") * F("tax_code.rate") / 100)

class Bill(Model):
    tax_total = Currency(currency="code", compute=lines.sum("tax_exact"))
```

For a role that may read every input of its formula (and whose `read_fields`, if declared,
name it), the intermediate does not withhold the results that read it: `tax_total` is readable
and local. The shipped plan carries the intermediate's formula so the browser works out every
formula on the row, but the intermediate is never named in `capabilities`, never in the model's
schema fields, and never in a record, list or preview response. Its formula reaches the browser,
so do not use it to keep a rate or constant secret; for that, keep it out of the role's
`read_fields`, which withholds the plan as before. A write that names the intermediate (or a
result that reads it) is refused with its own error, such as `COMPUTED_FIELD_WRITE: this value
is calculated`, not the general "One or more values are invalid".

## Browser engine

`frontend/src/evlet/model/finite.ts` evaluates the same plan with no imports and no DOM. A
`FiniteDocument` session offers `set`, `addRow`, `removeRow`, `reorder`, nested `batch`,
`observe`, `snapshot` and `dispose`, plus the host inputs `setToday`, `setLookups` and
`lookupRequests`.
`ruleStates(handle)` returns `{field: {applies, required, locked}}` for a row's fields that
declare rules, already resolved with the undecided rules above, and `observeRules(handle,
listener)` calls the listener after each batch that changes one of them. A field the host sets
in a batch, or supplies with a new row, is touched for that batch: its suggestion does not
replace it. Values supplied when a session is created count as touched for new rows only.
Setting a field to the value it already has changes nothing. `ruleStates` reports a saved row's
lock as decided when the session was loaded (refined by lookups and `setToday` until the first
edit), like the server. After a save the record is loaded again and gets a new session, so the
saved state moves on. A batch publishes once; observers cannot mutate during publication
(`REACTIVE_REENTRANCY`); a batch callback that returns a promise is rejected
(`ASYNC_BATCH_UNSUPPORTED`).

Every command checks its input before it changes anything, so a refused command (an unknown
field, a repeated row key, a full table) leaves the document exactly as it was. Batches do not
copy the document to undo a failure. If a batch callback throws after some commands ran, those
commands are kept, the document settles and publishes once, and the error is thrown again. If
settling itself fails, for example on the evaluation budget or the graph limit, the session is
marked `broken` and refuses every later command; the record session then builds a new one from
the current rows. Python's `Document.batch` follows the same rules.

The browser checks a plan's shape (versions, limits, table limits, expression nodes and
currency sources) before it uses it. It does not hash the plan again: the plan and its
`model_schema_hash` come from the server in one response, a save sends the hash back, and the
server refuses a save whose hash differs from its own plan.

Python's `evlet.calc.Document` has the same commands, host inputs and `rule_states` in snake_case,
plus `replace_rows`, which the server uses to apply a saved table. Observation (`observe`,
`observeRules`) and `dispose` are browser-only, since the server settles a document once and
reads its snapshot.

Shared fixtures (`frontend/src/evlet/tests/finite-fixtures.json`) are generated by
`scripts/generate_finite_fixtures.py` from Python plans and results. The browser replays
them and must match what users see: values, states, causes, rule states, Link lookup
requests, refused commands and which results changed. Internal counters (formulas evaluated
or skipped, budget steps, revision numbers) are not compared, so each engine may order its
work its own way. Each engine has its own test that editing one row does not re-evaluate the
other rows.

## Limits

Expressions: 128 nodes, depth 32. Tables: depth 8, each collection its `max_rows` (default
100, at most 1,000), 5,000 owned rows per root. Graph: 50,000 nodes, 250,000 edges. Evaluation: 1,000,000 steps per batch.
Decimal text: 128 characters. Plans carry these limits and both runtimes refuse others.
