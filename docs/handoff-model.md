# Handoff: model amendments

Extend the model engine so one `models.py` file can describe any major business
document or transaction: what it stores, how its values are derived, how it moves
through states, what it posts elsewhere, how it is numbered, and what its history
is. This work is backend only. Do not change the frontend, `evlet/ui` components,
views or layout code in this handoff. New metadata is exposed additively in the
neutral schema so a renderer can use it later.

Read first: `AGENTS.md`, `docs/specification.md` (sections 2, 4 and 5),
`docs/architecture.md`, `docs/model-engine.md`, `docs/business-capabilities.md`,
`evlet/models.py`, `evlet/fields.py`, `evlet/query.py`, `evlet/business.py`,
`evlet/permissions.py`, `evlet/ui/expressions.py`, `evlet/ui/metadata.py`.

## Working rules

- One phase per feature branch and pull request. Never merge or push to `main`.
- Standard library only. SQLite first; keep the PostgreSQL adapter passing.
- Every phase ships tests, docs in `docs/model-engine.md` or a new guide, and a
  Playground use of the public API where it applies.
- Keep every existing public API working: `ChildTable` and `Action` registered on
  `Application`, `Meta.layout`, the HTTP child collection replacement format, the
  schema version 1 shape. New keys are additive.
- Commits are authored as the repository owner. No co-author trailers and no
  mention of tools or assistants in commits, pull requests, comments or docs.
- Keep names short and plain (specification 2.8). No new jargon beyond the terms
  defined below.

## Design stance: do not copy Frappe

Evlet solves the same business problems with a different model. When in doubt,
pick the declarative, single-file option and let the engine do the bookkeeping.

| Frappe pattern | Evlet approach in this handoff |
|---|---|
| DocType JSON, controller `.py` and form `.js` per document | One Python class per model. No JSON, no client scripts |
| Totals recalculated by hand in Python and again in JavaScript | `compute=` expressions, evaluated by the engine on every write, shippable to any client |
| `fetch_from` overwrites a value from a linked record | `initial=` fills a value when its source changes and stays editable |
| `depends_on`, `mandatory_depends_on` as JavaScript strings | `applies_when`, `required_when`, `locked_when` as typed expressions enforced on the server |
| Child table field with `idx`, saved by full replacement | `Lines(...)` owned collection with row-level changes and stable positions |
| `docstatus` 0/1/2, cancel, amend with `-1` names | `State` field with declared moves; locked states; `void` and `revise` |
| Workflow configured as database records | Moves declared in code on the `State` field |
| `make_gl_entries` written in `on_submit` and undone in `on_cancel` | `Effects`: posted rows are a pure function of the record; the engine writes and reverses them |
| Naming series as the record name | `Sequence` field; the integer `id` stays the key |
| Version, Comment, Communication and Activity Log doctypes | One append-only `Journal` per record |
| Number cards and dashboard charts stored as records | `Measures` declared on the model, computed by one aggregate API |

## Terms

- **Expression:** a serializable rule built from `record.<field>` paths, literals
  and operators. No source code is ever evaluated.
- **Lines:** rows owned by a parent record and saved with it.
- **State:** a field whose value changes only through declared moves.
- **Locked:** a state in which ordinary writes are rejected.
- **Effect:** rows another model receives because a record reached a state.
- **Journal:** the append-only history of one record.
- **Measure:** a named aggregate over a model or a record's relations.

---

## Phase 1: Expressions evaluated in Python

Today `Expression` objects are serialized for the browser only. Add a Python
evaluator so the same expression is the rule on both sides.

API additions in `evlet/expressions.py` (core, not `evlet/ui`; keep the `evlet.ui`
imports working by re-exporting):

```python
from evlet import record, Sum, Count, Min, Max, today, days

record.qty * record.rate               # arithmetic: + - * /
record.customer.credit_days            # Link hops, at most three
record.status.eq("draft") & record.total.gt("0")
record.posting_date + days(record.customer.credit_days)
Sum(record.lines.net)                  # aggregates over Lines
record.discount.otherwise("0")         # existing choose/coalesce style
```

Rules:

- Arithmetic on Decimal and Currency is exact (`decimal.Decimal`, precision 40).
  The result is quantized to the target field scale using the field's
  `rounding` (default `"half_up"` for Currency, `"half_even"` for Decimal).
  Division by zero is a validation error, not an exception.
- Mixed types are rejected at definition time where the types are known
  (Text + Currency, Date * Integer).
- Link hops resolve through the registry in one query per hop per evaluation
  batch. A null link yields null; arithmetic with null yields null.
- `today()` uses the instance timezone setting (default UTC); document it.
- An expression is validated against the model when the `Registry` is built.
  Unknown paths, wrong types and more than three hops fail at startup.
- Serialization format stays the existing `{op, args}` shape. Add new ops to the
  same table so a client can evaluate them later.

Tests: arithmetic exactness and rounding modes, null propagation, Link hops,
Sum over lines, type errors at registry build, serialization round trip.

## Phase 2: Derived values and field rules

Field options, valid on every field type:

```python
amount = Currency(compute=record.qty * record.rate, store=True)
due_on = Date(compute=record.posting_date + days(record.terms_days))
rate = Currency(initial=record.item.selling_rate)
lost_reason = Text(applies_when=record.stage.eq("lost"), required_when=record.stage.eq("lost"))
price = Currency(locked_when=record.status.ne("draft"))
```

- `compute`: the engine evaluates it on every create and update, in dependency
  order (build the graph at registry time, reject cycles). Computed fields are
  read only for every caller including HTTP. `store=True` writes a real column so
  it can be filtered, sorted and aggregated; without it the value is computed on
  read. Stored computed fields need a migration like any field.
- `initial`: when the source path changes (or on create), the engine sets the
  field from the expression unless the caller supplied a value in the same write.
  Later edits by the user are kept. This is a suggestion, not a binding.
- `applies_when`: when false, the field is not required, its validator does not
  run, and a submitted value is cleared to null on save. It carries meaning for
  the API and a renderer may hide it later.
- `required_when` and `locked_when`: enforced on create and update. Locked fields
  reject changed values with a field error.
- New migration operation `Recompute(model, fields)` recalculates stored computed
  values for existing rows inside the migration transaction.
- Schema metadata: each field gains `compute`, `initial`, `applies_when`,
  `required_when`, `locked_when` as serialized expressions, plus `stored`.

New field types (Specification section 4 lists them):

| Type | Storage | Rules |
|---|---|---|
| `Phone` | TEXT | digits, spaces, `+`, `-`, `()`; normalized to E.164 when it starts with `+` |
| `Percent` | scaled INTEGER | Decimal scale 2, default bounds 0 to 100 |
| `Time` | TEXT `HH:MM:SS` | no timezone |
| `Duration` | INTEGER seconds | serialized as ISO 8601 (`PT1H30M`) |
| `Tags` | TEXT (JSON array) | at most 20 tags of at most 40 characters, no duplicates; `contains` filter supported |
| `Ref` | two columns `<name>_model`, `<name>_id` | points to any registered model; existence checked on write; no SQL foreign key |

Currency gains an optional `currency=` expression or field path and a companion
`base=` declaration for documents in foreign currency. Keep it optional and
small: exchange rate is an ordinary Decimal field the developer declares.

Tests: dependency ordering and cycle rejection, initial versus user edits,
applies_when clearing, locked rejection over HTTP and Python, each new type's
validation and storage, `Recompute` migration with rollback on failure.

## Phase 3: Lines on the model

Declare owned rows as a field instead of registering `ChildTable` on the app.

```python
class InvoiceLine(Model):
    item = Link(Item, required=True)
    qty = Decimal(scale=3, minimum="0.001", default="1")
    rate = Currency(initial=record.item.selling_rate)
    net = Currency(compute=record.qty * record.rate, store=True)
    warehouse = Link(Warehouse, initial=parent.warehouse)

class Invoice(Model):
    warehouse = Link(Warehouse)
    lines = Lines(InvoiceLine, min_rows=1, max_rows=500)
    total = Currency(compute=Sum(record.lines.net), store=True)
```

- The line model stays an ordinary registered model with its own table. `Lines`
  infers the parent link (the single Link on the line model pointing to the
  parent) or creates one named `parent` for new models. Reject ambiguity.
- The engine adds and maintains a `position` column. Order is stable and
  explicit; reordering is a row change.
- `parent.<field>` is available in line expressions.
- Save protocol: accept row changes in addition to the existing full
  replacement format:
  `{"lines": {"create": [...], "update": [{"id": 7, ...}], "delete": [9], "order": [7, 12, 3]}}`.
  All changes and the parent's recomputed values commit in one transaction.
- `max_rows` default 100, configurable up to 1,000. Line list endpoint
  `GET /api/{model}/{id}/lines/{name}` with paging, so a parent detail no longer
  has to carry every row. Keep embedding rows in detail when the count is at or
  under 100 for compatibility.
- `ChildTable` registered on `Application` keeps working and maps onto the same
  machinery.
- Permissions for line rows stay as they are today (model, field and row rules on
  the line model).

Tests: inference and ambiguity errors, position ordering, mixed row changes in one
request, parent totals after each change, 1,000-row limit, paging endpoint, old
replacement format still accepted, rollback on any row error.

## Phase 4: State, moves and model actions

```python
class Invoice(Model):
    status = State(
        ("draft", "issued", "paid", "void"),
        default="draft",
        moves=(
            Move("draft", "issued", "Issue", roles=("admin", "editor"),
                 when=record.lines_count.gt(0), run="on_issue"),
            Move("issued", "paid", "Mark paid", when=record.outstanding.eq("0")),
        ),
        locked=("issued", "paid", "void"),
        final=("paid", "void"),
    )

    @action("Record payment", inputs={"amount": Currency(required=True), "paid_on": Date(required=True)},
            available_when=record.status.eq("issued"))
    def record_payment(self, tx, user, amount, paid_on):
        ...
```

- `State` is a Select whose value changes only through `Move`s or engine
  operations. Ordinary create/update cannot set it except the default.
- `POST /api/{model}/{id}/moves/{to}` performs a move. It checks the current
  state, `roles`, `when`, and update permission within the row scope, runs the
  optional `run` method in the same transaction, writes a Journal entry, then
  returns the permission-filtered detail.
- `locked` states reject ordinary writes to the record and its Lines. A field may
  opt out with `editable_when_locked=True` (for example a remarks field).
- `final` states allow no moves out except engine operations in Phase 5.
- `@action` on the model replaces registering `Action` on `Application`; both stay
  supported. `inputs` are field definitions validated before the handler runs.
  `available_when` is enforced on the server.
- Policy gains operations named after moves only through `Move.roles`; do not add
  a generic workflow permission table.
- Schema metadata: `state` (field name, states, moves with labels and roles,
  locked, final) and `actions` (name, label, inputs, available_when).

Tests: illegal moves, role and guard rejection, `run` rollback, locked writes on
record and Lines, `editable_when_locked`, model action inputs validation, legacy
`Action` registration still works.

## Phase 5: Void and revise

Corrections never edit a locked record.

- `void(record, reason)`: allowed from locked, non-final states listed in
  `State(..., voidable=(...))`. Moves the record to `void`, reverses its Effects
  (Phase 6), keeps its Sequence number, and writes a Journal entry with the
  reason. Requires the `void` operation in the policy.
- `revise(record)`: voids the record and creates a new draft copy in the same
  transaction. The copy gets `revises = Ref()` pointing at the original (the
  engine adds this field when the model declares `revisable=True`) and a fresh
  Sequence number when it is issued. No name suffixes.
- `Rule.operations` accepts `void` and `revise`. Defaults: admin only.
- HTTP: `POST /api/{model}/{id}/void` with `{"reason": "..."}` and
  `POST /api/{model}/{id}/revise`.

Tests: void reverses effects exactly, reason required, revise copies lines and
links both records, numbers are not reused, permissions.

## Phase 6: Effects

Rows a record produces in other models when it reaches a state, declared as a
pure function of the record.

```python
class Invoice(Model):
    class Meta:
        effects = (
            Effect(LedgerEntry, rows="ledger_rows", on="issued",
                   reverse=Swap("debit", "credit"), balanced=("debit", "credit")),
            Effect(StockMove, rows="stock_rows", on="issued", reverse=Negate("qty")),
        )

    def ledger_rows(self, tx):
        return [{"account": ..., "debit": self.total, "credit": "0"}, ...]
```

- On entering `on`, the engine calls the method, validates every row as a normal
  record of the target model, checks `balanced` (sums equal per row batch), and
  writes them in the same transaction.
- The target model must declare `source = Ref()`; the engine fills it. Effect rows
  are read only for every caller, including direct `Database.update`, except the
  engine itself.
- On void, the engine writes mirror rows using `reverse` (`Swap` exchanges two
  fields, `Negate` flips signs) and links them to the same source. Originals are
  never deleted.
- `evlet effects check <instance>` recomputes each source's rows and reports
  differences without writing.

Tests: rows written atomically, validation failure rolls back the move, balance
check, mirror rows on void, read-only enforcement, check command.

## Phase 7: Sequences

A human document number as a field, separate from the integer `id`.

```python
number = Sequence("INV-{year}-{n:05}", scope=("company",), assign_on="issued")
```

- Tokens: `{year}`, `{yy}`, `{month}`, `{n}` with optional zero padding, and plain
  field paths in `scope`. Including `{year}` or `{month}` resets the counter per
  period, using the record's date field named by `date=` (default creation date).
- Counter table `evlet_sequence(model, field, scope_key, period, last)` updated
  inside the write transaction (`BEGIN IMMEDIATE` already serializes writers), so
  numbers are gapless for committed records.
- `assign_on` is a state or `"create"`. Before assignment the value is null.
  Unique index on (field, scope).
- Voided records keep their number. Revisions get a new one.

Tests: concurrency (parallel writers get distinct gapless numbers), period reset,
scope separation, rollback does not consume a number, PostgreSQL adapter.

## Phase 8: Journal

One append-only history per record, replacing the need for separate version,
comment and message tables.

```python
class Meta:
    journal = Journal(tamper_evident=True)
```

- Default on for models with a `State` or `Effects`, off otherwise. `Journal()`
  turns it on explicitly.
- Table `evlet_journal(id, model, record_id, kind, actor, at, data, prev_hash, hash)`.
- Engine-written kinds: `created`, `changed` (field, old, new for every changed
  field including lines), `moved`, `voided`, `revised`, `effects`, `file`.
- User-written kinds through HTTP: `note`, `message` (a logged message; sending
  email is out of scope), `call`, `follow_up` (text, due date, assignee).
  Completing a follow up appends `follow_up_done` referencing it. Nothing is
  edited or deleted.
- `GET /api/{model}/{id}/journal` (paged, newest first) requires read access and
  removes old/new values of fields the caller cannot read. `POST` requires a new
  `journal` operation in `Rule` (default admin and editor).
- `tamper_evident=True` chains SHA-256 hashes per record. `evlet journal verify
  <instance>` reports breaks.
- `EventStore` stays for live updates; it is not history.

Tests: diffs for fields and lines, field permission filtering on read, append-only
enforcement, follow up completion, hash chain verification and tamper detection.

## Phase 9: Measures and the aggregate API

```python
total = database.aggregate(
    Invoice,
    measures={"amount": Sum("total"), "count": Count()},
    group_by=("status", Month("posting_date")),
    filters={"customer": 4},
)

class Meta:
    measures = {
        "outstanding": Sum("outstanding", where=record.status.eq("issued")),
        "payments": Count(Payment, link="invoice"),   # per record, over a relation
    }
```

- Functions: `Count`, `CountDistinct`, `Sum`, `Min`, `Max`. Sums of Decimal and
  Currency stay exact (sum of scaled integers, overflow is an error).
- Group by plain fields and `Day`, `Month`, `Year` of a date. At most four group
  fields, eight measures and 500 groups.
- Named `Meta.measures` are model-level (over many records) or per record when
  they name a related model and link.
- HTTP: `GET /api/{model}/aggregate?measures=...&group_by=...&filters=...` and
  `GET /api/{model}/{id}/measures`. Both apply row scope and field permissions;
  measures over unreadable fields are rejected.

Tests: exact sums, grouping by month, limits, permission scope in aggregates,
per-record relation measures, PostgreSQL parity.

## Phase 10: Model meaning metadata

Information about the model that any client, report or connector can use.

```python
class Meta:
    key_fields = ("customer", "status", "total", "due_on")
    relations = (Related(Payment, "invoice", label="Payments"),)
    attachments = Attachments(max_files=10, max_size=1_048_576)
```

- `key_fields`: up to eight fields that summarize the record.
- `relations`: reverse Links are discovered automatically; `Related` names,
  orders or limits them. Include them in the schema with counts available through
  Phase 9.
- `Meta.attachments` replaces `attachments=(Model,)` on `Application`; both stay
  supported. Limits are configurable within the current maximums.
- Extend `view_for` to accept these keys and the keys from earlier phases
  (`effects`, `measures`, `journal`). Keep rejecting unknown keys.

Tests: discovery of reverse links, schema output, attachment limits from Meta.

---

## Complete example

```python
class InvoiceLine(Model):
    item = Link(Item, required=True)
    qty = Decimal(scale=3, minimum="0.001", default="1")
    rate = Currency(initial=record.item.selling_rate)
    vat_rate = Percent(initial=record.item.vat_rate)
    net = Currency(compute=record.qty * record.rate, store=True)
    vat = Currency(compute=record.net * record.vat_rate / 100, store=True)


class Invoice(Model):
    number = Sequence("INV-{year}-{n:05}", scope=("company",), assign_on="issued")
    company = Link(Company, required=True)
    customer = Link(Customer, required=True)
    posting_date = Date(required=True, default=today)
    terms_days = Integer(initial=record.customer.credit_days, minimum=0)
    due_on = Date(compute=record.posting_date + days(record.terms_days), store=True)
    lines = Lines(InvoiceLine, min_rows=1, max_rows=500)
    net_total = Currency(compute=Sum(record.lines.net), store=True)
    vat_total = Currency(compute=Sum(record.lines.vat), store=True)
    total = Currency(compute=record.net_total + record.vat_total, store=True)
    paid = Currency(default="0", locked_when=record.status.ne("issued"))
    outstanding = Currency(compute=record.total - record.paid, store=True)
    remarks = LongText(editable_when_locked=True)
    status = State(
        ("draft", "issued", "paid", "void"), default="draft",
        moves=(Move("draft", "issued", "Issue"), Move("issued", "paid", "Mark paid", when=record.outstanding.eq("0"))),
        locked=("issued", "paid", "void"), final=("paid", "void"), voidable=("issued",),
    )

    class Meta:
        title_field = "number"
        key_fields = ("customer", "status", "total", "due_on")
        effects = (Effect(LedgerEntry, rows="ledger_rows", on="issued", reverse=Swap("debit", "credit"), balanced=("debit", "credit")),)
        measures = {"outstanding": Sum("outstanding", where=record.status.eq("issued"))}
        journal = Journal(tamper_evident=True)
        revisable = True

    def validate(self):
        ...

    def ledger_rows(self, tx):
        ...

    @action("Record payment", inputs={"amount": Currency(required=True)}, available_when=record.status.eq("issued"))
    def record_payment(self, tx, user, amount):
        tx.update(Invoice, self.id, paid=self.paid + amount)
```

## Order and dependencies

1. Expressions (everything else uses them)
2. Derived values and field rules
3. Lines
4. State, moves and model actions
5. Void and revise (needs 4)
6. Effects (needs 4, reverses in 5)
7. Sequences (needs 4 for `assign_on`)
8. Journal (records events from 2 to 7)
9. Measures and aggregates
10. Model meaning metadata

Phases 7, 8 and 9 can run in parallel after 4.

## Out of scope

Frontend and renderer work, form layout (sections, tabs, side panel, summary
strip), print output, board and calendar views, saved views, sending email,
notification delivery, multi-currency revaluation, nested Lines.

## Decisions taken (change only with the owner)

- Currency rounds half up; Decimal rounds half to even. Always explicit per field.
- Lines default to 100 rows, configurable up to 1,000.
- Journal is on by default only for models with a State or Effects.
- Tamper-evident hashing is opt in.
- Locked records are corrected only by void or revise, never by editing.
- Numbers are gapless for committed records and never reused.

## Definition of done per phase

`.venv/bin/python -m pytest` passes, including the PostgreSQL tests where the
adapter is available. `npm test` and `npm run build` in `frontend/` still pass
without frontend changes. Docs updated, Playground demonstrates the public API,
and the pull request lists the API added, the migration impact and the tests.
