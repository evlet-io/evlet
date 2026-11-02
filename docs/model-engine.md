# Model engine

Milestone 2 extends the foundation with typed fields, updates and deletes,
parameterized queries, relationship traversal, and explicit SQLite migrations.
The Python core still has no third-party runtime dependencies.

## Fields and validation

```python
from datetime import date, datetime, timezone
from evlet import Boolean, Date, DateTime, Decimal, Email, LongText, Model, Select, URL

class Contact(Model):
    active = Boolean(required=True, default=True)
    joined = Date(default=date.today)
    recorded = DateTime(default=lambda: datetime.now(timezone.utc))
    rate = Decimal(scale=4, minimum="0", maximum="100")
    category = Select(["customer", "supplier"], default="customer")
    email = Email()
    website = URL()
    notes = LongText()
```

- `Text`, `Integer`, `Currency`, and `Link` remain available. `Integer` and decimal
  fields support `minimum` and `maximum`; fields support `required`, `default`,
  `unique`, `index`, and an optional `validator` callable.
- `Decimal` has a fixed scale from 0 to 9, defaulting to 4. It stores scaled signed
  64-bit integers and returns decimal strings. Floats, nonfinite numbers, extra
  fractional digits, and out-of-range values are rejected. `Currency` fixes the
  scale at 2. There is no automatic rounding or currency conversion.
- `Boolean` accepts actual booleans. `Date` accepts a Python date or `YYYY-MM-DD`.
  `DateTime` requires a timezone-aware datetime or ISO string and normalizes it to
  UTC with six fractional digits. Naive datetimes are rejected.
- `Select` accepts a declared string choice. `Email` performs a basic address
  syntax check, not mailbox verification. `URL` accepts absolute HTTP/HTTPS URLs.
  `LongText` defaults to a 65,535-character limit, which can be overridden.
- Omitted values use defaults; explicit `None` stays null unless required.
  Callable defaults run when a new record is constructed. A partial update keeps
  omitted values, including their original defaults.
- A field validator receives the cleaned non-null value and raises `ValueError`
  on rejection. Override `Model.validate()` for cross-field rules and raise
  `ValidationError({"field": "message"})`. Both creation and updates run validation.
  Records remain immutable snapshots; change them through the database API.

### More field types

```python
from evlet import Currency, Duration, Model, Percent, Phone, Reference, Tags, Text, Time

class Visit(Model):
    phone = Phone()                       # "+971 50 123 4567" is stored as "+971501234567"
    discount = Percent(default="0")       # two places, 0 to 100 unless minimum/maximum are given
    starts = Time()                       # "09:30" or "09:30:00", no timezone
    length = Duration(default=0)          # "PT1H30M" or 5400; returned as "PT1H30M"
    labels = Tags()                       # ["vip", "north"]
    about = Reference()                   # {"model": "customer", "id": 4}, any registered model
    fee = Currency(currency="AED")        # or currency="code_field" naming a text field
    code_field = Text(default="AED")
```

| Type | Storage | Rules | Filters |
| --- | --- | --- | --- |
| `Phone` | TEXT, 32 characters | digits, spaces, `+`, `-` and parentheses, 3 to 15 digits; a number starting with `+` is stored in E.164 form | text filters |
| `Percent` | scaled INTEGER like `Decimal` | scale 2, default bounds 0 to 100 | number filters |
| `Time` | TEXT `HH:MM:SS` | no timezone, whole seconds | equality and order |
| `Duration` | INTEGER seconds | ISO 8601 days, hours, minutes and seconds (`P1DT2H`, `PT90M`) or whole seconds, 0 or more | number filters; values in either form |
| `Tags` | TEXT JSON array | at most 20 distinct tags (ignoring case) of 1 to 40 characters, without quotes, backslashes, commas or control characters; a text value is split on commas | `contains` matches one whole tag exactly, including case (duplicates are detected ignoring case); equality matches the whole list |
| `Reference` | two columns `<name>_model`, `<name>_id`, no SQL foreign key; `index=True` indexes both columns together (not `unique`) | a registered model and a record that exists, checked when the value changes | equality with `{"model", "id"}` and `isnull` |
| `State` | TEXT like `Select` | one of its states; set only to the default on create, then changed only by its `Move`s; at most one per root document; see [states-actions.md](states-actions.md) | text filters |
| `Sequence` | TEXT, null until assigned, unique with its scope fields | a document number such as `INV-{year}-{n:05}` written only by the engine on create or when a move enters `assign_on`; root documents only; see [sequences.md](sequences.md) | text filters |

Any editable field may declare `editable_when_locked=True` so that ordinary writes can still
change it while its document is in a locked State (for example remarks).

`Reference` is the field type; `Ref("parent.warehouse")` keeps meaning a declared path for
defaults and filters. A reference cannot be unique, indexed,
ordered or followed in a filter path, and its columns must not collide with other fields.
Reading a reference needs read access to its target record: otherwise the value is shown
as null, including the model it names. HTTP filters on a reference need read access without
row scope to the model named in the filter value (`{"about": {...}}` or `about__eq`), and to
every model for any other form such as `isnull`. Renaming a reference with `RenameField`
renames both columns. `Database.related()` does not
follow references; read the target with `get`.

`currency=` on `Currency` is display metadata in `/api/schema` (`"currency"`): an ISO 4217
code or the name of a text or select field on the same model. Evlet never converts amounts.

`Percent` and `Duration` work as numbers in finite expressions; `Phone` works as text.
`Time`, `Tags` and references cannot be used in calculations.

## Updates, deletes, and relationships

```python
invoice = database.update(Invoice, invoice.id, total="125.50")
customer = database.related(invoice, "customer")

with database.transaction() as tx:
    tx.update(Customer, customer.id, name="New name")
    tx.delete(Payment, payment.id)
```

`update` returns a new snapshot. `delete` removes one existing record or raises
`NotFoundError`. Foreign keys restrict deletion of referenced records; there is
no implicit cascade. Uniqueness and foreign-key failures raise `ConflictError`.
An exception leaving a transaction rolls back its writes together. Transactions
use `BEGIN IMMEDIATE` so validation/read/modify work is serialized with other
writers; keep transaction bodies short.

`related(record, field_name)` resolves a `Link` through the registered model API,
returning `None` for a null link. It works on both `Database` and transaction
sessions. Reverse traversal uses ordinary filters on the referencing model.

## Queries

```python
invoices = database.list(
    Invoice,
    filters={"total__gte": "100.00", "customer__name__icontains": "acme"},
    order_by=("-total", "id"),
    limit=20,
    offset=0,
)
count = database.count(Invoice, filters={"customer": customer.id})
```

Filter keys contain a field path and an optional operator separated by `__`.
A plain key means equality, including a field named `gt` or `contains`. Supported operators are `eq`, `ne`, `gt`, `gte`,
`lt`, `lte`, `in`, `isnull`, `contains`, `icontains`, and `startswith`. Filters
`ne` uses SQL inequality: it excludes NULL rows when compared with a non-NULL
value. Use `isnull` explicitly for NULL values. Filters combine with AND. Link paths allow up to three relationship hops, for example
`invoice__customer__name`. Ordering accepts the same paths and a leading `-`
for descending order. IDs break ties when no ID order is supplied.

Queries allow at most 20 filters, 100 values in an `in` filter, four order fields,
and 100 returned rows. Empty `in` lists match nothing. Use `isnull` for nulls
inside membership queries. Text matching treats `%` and `_` literally;
`icontains` uses SQLite's built-in ASCII case folding, not full Unicode folding.
Values are parameterized. Field paths and operators are checked against the
registry; callers cannot supply SQL. Query values receive type validation without
applying a field's business minimum/maximum or custom validator.

## HTTP API

The existing create, list, detail, health, and schema endpoints remain available.

- `PATCH /api/{model}/{id}` accepts a partial JSON object and returns the saved
  record (200). Unknown fields and invalid values return 422.
- `DELETE /api/{model}/{id}` returns 204 with an empty body. A missing record
  returns 404; a linked-record constraint returns 409.
- `GET /api/{model}` accepts `limit`, `offset`, `filters` (a URL-encoded JSON
  object), and `order_by` (comma-separated fields). The result keeps the existing
  `items`, `limit`, and `offset` shape. Query strings are limited to 8 KiB.
- Document POST and PATCH accept owned rows as full lists or as change sets
  (`{"create": [...], "update": [...], "delete": [...], "order": [...]}`). A detail embeds
  at most 100 rows per collection and reports larger ones in `children_pages`.
  `GET /api/{model}/{id}/lines/{name}?limit=&offset=` pages one collection. See
  [owned tables](finite-calculations.md#owned-tables).

The neutral schema remains version 1 with additional field types and metadata.
The Playground now generates forms, lists, and details from the extended UI
metadata described in [application generation](application-generation.md).

## Explicit migrations

Startup creates a fresh database or verifies an existing schema. It does not
silently alter existing tables. A model change raises `SchemaError` until its
explicit migration has been applied.

```python
from evlet import AddField, AddIndex, Boolean, Database, Migration, RenameField

migrations = [Migration(
    "0001_contact_details",
    RenameField("customer", "name", "display_name"),
    AddField("customer", "active", Boolean(required=True), backfill=True),
    AddIndex("customer", "display_name"),
)]

database = Database("app.sqlite3", registry, migrations=migrations)
database.initialize()  # Fresh setup or verification; never upgrades existing tables.
```

Declare the complete shipped history on `Database` so fresh startup records its
baseline along with the latest schema. Pass this database to `Application` as in
the Playground. When the schema changes, run `database.migrate(migrations)`
explicitly before startup, using the complete history including the new steps.

The current model definitions must describe the target schema, including
`display_name = Text(...)`, `active = Boolean(required=True)`, and
`index=True` on `display_name`. `CreateModel(HistoricalModel)` adds a model;
`AddIndex(..., unique=True)` adds a uniqueness constraint. Keep historical field
and model definitions in migration files unchanged. Do not point an old
`CreateModel` operation at a model definition that will keep changing.

Migrations run under one SQLite write transaction. The complete pending batch,
its history entries, and the schema snapshot commit together after structure,
record validation, and foreign-key checks pass. Failures roll everything back.
Applied names and operation checksums prevent silent edits, omissions, or
reordering. Repeated runs skip applied steps. Concurrent runs serialize.

Fresh initialization with declared migrations creates the latest schema and
records that history atomically. Later upgrades skip those steps and apply only
new migrations, just as on a database upgraded from an older version. Startup
checks declared history for changed, removed, reordered, or unapplied entries.

For a database previously initialized without a migration list, initialize it
with its existing models and their complete history before changing those models.
This validates its columns, constraints, and data before adopting the baseline;
it does not replay operations or backfills. `database.migrate(migrations)` can
also create or adopt the current baseline explicitly. Without a declared list,
`initialize()` cannot infer migration names or checksums from model definitions.
A legacy database without schema metadata must first be initialized with its
original models, then explicitly upgraded.

Supported operations are deliberately bounded: add a model, add a field, rename
a field, add an index, and recalculate stored values. Fixed backfills are validated; required added fields
need a value. Callable or NUL-containing backfills, non-null Link backfills, removals, arbitrary
type changes, decimal scale changes, and reverse migrations are not supported.
They fail instead of rebuilding tables or guessing how to transform existing data.
Changes to runtime defaults alone do not require a database migration.

### Recalculating stored values

```python
from evlet import Migration, Recompute

Migration("0004_vat_rounding",
          Recompute("invoice_items", ("net", "vat")),
          Recompute("invoice", ("net_total", "grand_total")))
```

`Recompute(model, fields)` recalculates stored calculated fields of every existing row with
the current formulas. The model is a storage name, including an owned table such as
`invoice_items`; each whole document is loaded and settled, so totals see the new row
values, Link paths are read in the same transaction and results round with each field's
`rounding`. `today()` is the UTC date. Recompute runs inside the migration transaction after
the batch's structural changes, in declaration order. A result that is pending, an error or
outside the field's bounds fails the migration and rolls back the whole batch. It is part of
the migration checksum like any operation. When the batch recalculates every stored result
of a finite-v1 document, its saved documents are marked as calculated with the active
formulas, so they save without a recalculation request. On the recalculated rows, values of
fields whose `applies_when` is false are stored as null, matching the results. String formulas are supported on
models without tables or query sources. Like other operations, Recompute is not replayed
when a database adopts its baseline (a fresh database, or one initialized without history).

Recompute skips records whose document has posted effects, voided or not: they keep their
saved results and their recorded formula version, so the effect rows they posted still match.
A locked record that never posted is recalculated and moves to the active formula version. `database.migrate()` returns the applied migration names as a list whose `skipped`
attribute counts the records kept, and the `evlet.migrations` logger reports the count. Each
record Recompute changes gets a `recomputed` [Journal](journal.md) entry when its model
journals.

Back up existing databases before migrating. A backup is not a rollback command:
restoring it after later writes would discard those newer writes.

## Playground upgrade

Stop the launcher before upgrading an existing Playground database, then run:

```bash
.venv/bin/python -m examples.playground.migrate
bash scripts/playground.sh
```

`EVLET_DATABASE` selects the file for both commands. The migration command creates
a timestamped SQLite backup beside an existing database using SQLite's backup API,
validates the frozen Milestone 1 definitions when necessary, and applies
`0001_model_engine` and `0002_projects`. IDs, original values, and links stay intact. Fresh installations
can still use `bash scripts/playground.sh --setup` directly.

The Playground demonstrates active flags and notes on customers, dates/statuses
and exact tax rates on invoices, and timezone-aware timestamps on payments. Its
edit, confirmed delete, search, and ordering controls use the public HTTP API.
Tax rate and status are stored example fields; they do not calculate tax, settle
invoices, post a ledger, or trigger workflow actions. Existing email remains Text
so this upgrade does not retroactively reject previously accepted addresses.

Stored rows are decoded without rerunning custom field validators or
`Model.validate()`. New records and updates still run those rules, so an older row
can be read and repaired after a rule is tightened. Text fields reject NUL
characters on both SQLite and PostgreSQL. Text collation and Unicode case folding
can differ by database backend; explicit nullable ordering is shared.
