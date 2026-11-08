# Model engine

Milestone 2 extends the foundation with typed fields, updates and deletes,
parameterized queries, relationship traversal, and explicit SQLite migrations.
At the time the Python core had no third-party runtime dependencies; secrets at
rest later added `cryptography` (see [architecture](architecture.md)).

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
- `RichText(max_length=20000)` holds formatted text: headings, lists (nested up to three
  levels, with checklists), quotes, code, dividers, bold, italic, underline, strike, inline code,
  links (http, https and mailto) and line breaks. The value is HTML. Every write, from any route or
  from Python, goes through the server's allowlist sanitiser (`evlet/richtext.py`, standard
  library `html.parser`): other tags and attributes, scripts, styles, event handlers and unsafe
  URLs are removed, and the stored HTML is canonical. `max_length` counts the plain text; the HTML
  may be ten times as long (at least 4,096 characters). A document with no text is stored as null,
  so `required=True` works. The field uses two columns, `<name>` (HTML) and `<name>_text` (the
  plain-text extract that search, filters and list cells use); it cannot be unique or indexed.
- Omitted values use defaults; explicit `None` stays null unless required.
  Callable defaults run when a new record is constructed. A partial update keeps
  omitted values, including their original defaults.
- A field validator receives the cleaned non-null value and raises `ValueError`
  on rejection. Override `Model.validate()` for cross-field rules and raise
  `ValidationError({"field": "message"})`. Both creation and updates run validation.
  Records remain immutable snapshots; change them through the database API.

### Composite and unique indexes

`Meta.indexes` declares indexes over one or more fields, the same way on normal and ledger models.
An entry is a tuple of field names, or the tuple with `{"unique": True}`:

```python
class Sample(Model):
    station = Link(Station, required=True)
    on_date = Date(required=True)
    kind = Select(("spot", "average"), default="spot")

    class Meta:
        indexes = [(("station", "on_date", "kind"), {"unique": True}), ("on_date",)]


class Part(Child):
    code = Text(max_length=20, required=True)

    class Meta:
        indexes = [(("evlet_owner", "code"), {"unique": True})]  # a code once per record
```

- The database enforces a unique entry, on SQLite and PostgreSQL. A create or update that breaks
  one raises `ValidationError` with the same message on each of the entry's fields, such as
  `Another record already has the same station, on date and kind`. HTTP answers 422 with those
  field errors, as for any validation error. Rows that leave any of the fields empty (null) never
  clash.
- An entry names stored fields with one column each, and may name the row `id`. A `Reference`
  (two columns) and `RichText` are refused, with a message naming the field. On PostgreSQL an
  indexed `Text` needs `max_length` of 500 or less, as for `index=True`.
- A `Child` row names its owner field, `evlet_owner` unless `Table(..., owner_field=...)` says
  otherwise, to make a value unique per record. Its error says `Another row of this record
  already has the same code` and is reported on the row's own fields.
- A single field can still take `index=True` or `unique=True`. A plain `unique=True` field keeps
  its `ConflictError` (409 over HTTP).
- The saved schema records the entries, and the startup check compares them with the database:
  a missing index is a `SchemaError`. A model without `Meta.indexes` saves the same definition as
  before. `AddIndex("sample", ("station", "on_date", "kind"), unique=True)` adds an entry to an
  existing table, and `evlet migrate --plan` suggests it.

### More field types

```python
from evlet import Currency, Duration, Integer, Model, MultiSelect, Percent, Phone, Reference, Tags, Text, Time

class Visit(Model):
    phone = Phone()                       # "+971 50 123 4567" is stored as "+971501234567"
    discount = Percent(default="0")       # two places, 0 to 100 unless minimum/maximum are given
    starts = Time()                       # "09:30" or "09:30:00", no timezone
    length = Duration(default=0)          # "PT1H30M" or 5400; returned as "PT1H30M"
    labels = Tags()                       # ["vip", "north"]
    days = MultiSelect(["mon", "tue", "wed"], default=["mon"])  # ["mon", "wed"], in the order of the choices
    nights = Integer(default=1, suffix="nights")  # suffix: a unit shown after the input
    about = Reference()                   # {"model": "customer", "id": "01hc1sd9vc1ye"}, any registered model
    fee = Currency(currency="AED")        # a fixed code; see "Where a Currency field's code comes from"
    code_field = Text(default="AED")
```

| Type | Storage | Rules | Filters |
| --- | --- | --- | --- |
| `Phone` | TEXT, 32 characters | digits, spaces, `+`, `-` and parentheses, 3 to 15 digits; a number starting with `+` is stored in E.164 form | text filters |
| `Percent` | scaled INTEGER like `Decimal` | scale 2, default bounds 0 to 100 | number filters |
| `Time` | TEXT `HH:MM:SS` | no timezone, whole seconds | equality and order |
| `Duration` | INTEGER seconds | ISO 8601 days, hours, minutes and seconds (`P1DT2H`, `PT90M`) or whole seconds, 0 or more | number filters; values in either form |
| `Tags` | TEXT JSON array | at most 20 distinct tags (ignoring case) of 1 to 40 characters, without quotes, backslashes, commas or control characters; a text value is split on commas | `contains` matches one whole tag exactly, including case (duplicates are detected ignoring case); equality matches the whole list |
| `MultiSelect` | TEXT JSON array, like `Tags` | some of its choices, each once, saved in the order of the choices | `contains` matches one choice; equality matches the whole list |
| `Reference` | two columns `<name>_model`, `<name>_id`, no SQL foreign key; `index=True` indexes both columns together (not `unique`) | a registered model and a record that exists, checked when the value changes | equality with `{"model", "id"}` and `isnull` |
| `State` | TEXT like `Select` | one of its states; set only to the default on create, then changed only by its `Move`s; at most one per root document; see [states-actions.md](states-actions.md) | text filters |
| `Sequence` | TEXT, null until assigned, unique with its scope fields | a document number such as `INV-{year}-{n:05}` written only by the engine on create or when a move enters `assign_on`; root documents only; see [sequences.md](sequences.md) | text filters |

Every field takes `help_text=` and `tooltip=`, both text. Forms show `help_text` in grey under
the input (an error takes its place while there is one) and `tooltip` behind a small ? icon at
the right end of the input, on hover and keyboard focus. The icon shows only when `tooltip` is
set. Controls can change both, and its code export writes them back:

```python
reference = Text(help_text="The number printed on the invoice.",
                 tooltip="Use the customer's purchase order number if they ask for one.")
```

Every field takes `suffix=` (at most 20 characters), a unit the forms show in a box after the
input and after the value at rest, such as `"%"` or `"days"`. It is display metadata only, like
`prefix` on a UI control. `Percent` fields always show `%`; a `Decimal` with `suffix="%"` looks
the same and keeps its own scale.

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

### Where a Currency field's code comes from

`currency=` on `Currency` says which currency code the forms and lists show beside the amount.
It is display metadata only: Evlet never converts amounts. It takes one of four forms:

```python
class Invoice(Model):
    company = Link(Company)
    currency_code = Select(["AED", "USD"], default="AED")
    fee = Currency(currency="AED")                               # a fixed ISO 4217 code
    total = Currency(currency="currency_code")                   # a Text or Select field on the record
    paid = Currency(currency="company.company_currency")         # a field of the record a Link points at
    budget = Currency(currency="settings.billing.currency")      # an app setting ("For everyone")
```

| Form | Checked | In `/api/schema` | The browser |
| --- | --- | --- | --- |
| `"AED"` | three capital letters | `"currency": "AED"` | shows it |
| `"field"` | when the registry is built: a `Text` or `Select` field of the model | `"currency_source": {"field"}` | reads the field and follows it as it changes |
| `"link.field"` | when the registry is built: a `Link` of the model and a `Text` or `Select` field of its target | `"currency_source": {"link", "field", "model"}` | reads the linked record through the list route (batched with Link titles) and follows the Link |
| `"settings.app.key"` | the app's settings are read on each `/api/schema` request | `"currency": <the setting's value>` | shows it |

The schema is resolved for the person asking. A field or linked field they cannot read is left
out, and so is a setting that is not declared (the server logs it). A value that is not three
capital letters is ignored. In every such case, and when the source is empty, the browser shows
AED. The setting's value reaches everyone who can read the model, so use a setting that names a
currency, not one that holds anything private. Before an instance is initialized the setting's
declared default is used.

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

## Record ids

Every record has an `id` that never changes. Links, child rows, revisions, the journal and
URLs all point to it.

- **In storage** it is a 63-bit integer: 42 bits of milliseconds since 01-01-2026 UTC, then
  21 random bits, so ids sort in creation order. SQLite keeps it as the `INTEGER PRIMARY KEY`
  (the rowid) and PostgreSQL as `bigint`. The framework makes every id in one helper and
  draws again if one is ever taken.
- **Everywhere else** it is 13 lowercase Crockford base32 characters, such as
  `01hc1sd9vc1ye`: JSON bodies, Link values, `{"model", "id"}` References, URLs
  (`/api/invoice/01hc1sd9vc1ye`, `/app/invoice/01hc1sd9vc1ye`), the neutral schema, Eva and
  MCP tools. Ids are never JSON numbers, because a browser cannot hold 63 bits in a number.
  Input accepts any case and reads `I` and `L` as `1` and `O` as `0`.
- **In Python** `record.id` and Link values are `evlet.identity.RecordId`, an `int` whose
  `str()` is the text form. `database.get(Invoice, "01hc1sd9vc1ye")` and
  `database.get(Invoice, record.id)` both work. `to_dict()` gives text ids.
- **Older rows** keep their small ids: 42 reads and writes as `000000000001a`. Nothing is
  rewritten when an instance upgrades.

Writes also take a JSON integer for a Link or a row id, so older clients keep working;
responses only ever send text ids. Framework rows that are not records (accounts, journal
entries, jobs, attachments) keep their own number ids.

## Name and title

Every record model also has two standard columns, next to `id`. Table rows have neither.

- **`name`** is the human identifier, such as `INV-2026-00001`: optional and unique per model.
  `Meta.naming` decides how it is filled ([sequences.md](sequences.md#naming-series)): a naming
  series, the model's `autoname(self, context)` method, or people typing it
  (`Naming(manual=True)`). Without naming it stays empty and read only. A series or autoname
  assigns it on create, on confirm, or when the record enters a state (`assign_on`); until then
  forms show "Assigned on save" or "Assigned on confirm". Once a record has a name, anyone who may
  write the `name` field can change it through the normal edit; a taken name is refused. Links
  point to the id, so changing a name updates one row and breaks no link or bookmark.
  `Naming(..., label="Project number")` labels the field for the model's domain; it is "Name"
  otherwise.
- **`title`** is the display text that Link pickers, lists, search, breadcrumbs and Eva show. Declare
  a `title` Text field and that field is the title. Otherwise the framework fills it on every
  write from `Meta.title_field = "customer_name"` or a template
  `Meta.title = "{first_name} {last_name}"`, and falls back to the name, then the text id. It is not
  unique. A role reads it only when it may read its source fields.

```python
class Customer(Model):
    customer_name = Text(required=True)

    class Meta:
        title_field = 'customer_name'
        naming = Naming(Series('CUST-{#####}'))
```

`name` is reserved: an `Application` refuses a model that declares its own `name` field. Rename
it with `RenameField('customer', 'name', 'customer_name')` in an app migration. Frozen classes in
older migrations may keep their `name` field, because they describe the schema of that time.

Tables made before these columns existed get them at the end of the next app migration batch, or
from framework migration `0003_record_names`, which `evlet migrate` runs
([app-upgrades.md](app-upgrades.md#upgrade-evlet)). Every title is filled; existing rows keep an
empty name unless the app fills them. A data migration that moves an older number into `name` calls
`session.add_standard_columns(Model)` first, as the Playground's `0008_record_names` does for
invoice numbers. A model that declares its own `name` (a frozen class in older migrations) has
neither standard column, as its table had neither then.

## Record times

Every model table, Table rows included, has four more framework columns: when the record was created
and last changed, and by whom.

| Column | Holds |
|---|---|
| `evlet_created_at` | UTC time of the create, such as `2026-11-04T08:15:30.125Z` |
| `evlet_created_by` | the user id, or empty for the engine |
| `evlet_modified_at` | UTC time of the last write that changed the record |
| `evlet_modified_by` | the user id of that write, or empty for the engine |

They are not fields. They never appear in a model's fields, forms, lists, filters, imports or the
schema, and no request or `Session.create` can write them (an unknown field is refused). A model may
not declare a field with one of these names.

**Filled on every write**, in the same statement as the write and so in the same transaction:

- the record routes and the document pipeline (create, update, actions, moves, confirm and cancel),
  under the person who sent them, an API key's user included;
- the rows an [effect](effects.md) posts, under the person whose move posted them;
- void, unpost and repost, which change only the State, and revise, whose copy is a new record;
- trusted Python: `business.context(user)` writes under that user. A raw `Session` writes as the
  engine unless code sets `session.actor` to a user id first;
- imports and data migrations, which write through `Session`.

A record route or document write that changes nothing leaves the times alone. A change to a Table row counts as a
change of its document. A note, message, file or other Journal entry does not change the record, so
it does not move "Last modified". `sequences.backfill()` and `Recompute` migrations are repairs and
leave the times alone.

**Why columns and not the Journal.** The [Journal](journal.md) records who and when, but only for
models that journal (a State, effects or `Meta.journal`), and raw `Session` writes, data migrations
and `sequences.backfill()` write no entries. Columns are right for every write path and cost nothing
to read: they come with the row the detail already loads.

**Details** carry them as `_audit`, outside the record's ETag, so a person renaming themselves does not
make open records stale:

```json
"_audit": {"created_at": "2026-11-04T06:02:00.000Z", "created_by": {"id": 1, "name": "Aslam"},
           "modified_at": "2026-11-04T08:01:12.410Z", "modified_by": {"id": 2, "name": "Priya S."}}
```

`name` is the person's short name, then full name, then username, as Settings shows it. `created_by`
and `modified_by` are `null` for engine writes, and always `null` for portal roles, who may see when a
record changed but not which staff member changed it. Every desk reader of the record sees them, as
the Journal already shows actors to readers. Lists do not carry `_audit`. Discovery's read schema describes
`_audit`, so MCP clients and the connector can check record output against it.

**Existing databases.** Framework migration `0004_record_times` (`evlet migrate`) adds the columns and
fills them: from the record's Journal where it has one (the first entry that changed the record, and
the last), otherwise from the time in the record's [id](#record-ids), with no one as who. Rows whose
id holds no time (small ids from before time-ordered ids) stay empty, and screens show nothing for
them. The next app migration batch adds them too, before its first step, so data migrations can write.

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
`lt`, `lte`, `between`, `in`, `nin`, `isnull`, `contains`, `icontains`, `nicontains` and `startswith`.
`ne` uses SQL inequality: it excludes NULL rows when compared with a non-NULL
value. `nin` (not in) and `nicontains` (not like) also match NULL, as "is not one of these" reads.
`between` takes `[first, last]`, both included. Use `isnull` explicitly for NULL values. Filters combine with AND. Link paths allow up to three relationship hops, for example
`invoice__customer__name`. Ordering accepts the same paths and a leading `-`
for descending order. IDs break ties when no ID order is supplied.

### Filter lists and child tables

Filters can also be a list. A list can name the same field twice and can match rows of a
child table (a `Table` declaration or a `ChildTable`):

```python
database.list(Item, filters=[
    ["disabled", "eq", False],
    ["standard_rate", "between", ["50.00", "900.00"]],
    # One price row must be wholesale and above 100.
    {"child": "prices", "match": "any", "where": [["price_list", "eq", "wholesale"], ["rate", "gt", "100.00"]]},
    # No barcode row may be a UPC.
    {"child": "barcodes", "match": "none", "where": [["barcode_type", "eq", "UPC"]]},
    # Shorthand for a group of one condition that any row meets.
    ["prices.customer", "eq", customer.id],
])
```

Each entry is `[field, operator, value]` or a child group. A group compiles to a correlated
`EXISTS` (`"match": "any"`, the default) or `NOT EXISTS` (`"none"`) on the child table's
indexed parent link, so a parent appears once however many rows match, and one row must meet
every condition of its group. Two groups on the same table are independent: each may be met by
a different row. Groups do not nest. There is no OR between conditions.

Each field kind takes these operators (the schema sends them as each field's `operators`):

| Kind | Operators |
|---|---|
| Text, LongText, Email, URL, Phone, Sequence | `eq` `ne` `in` `nin` `contains` `icontains` `nicontains` `startswith` `isnull` |
| Select, State, Link | `eq` `ne` `in` `nin` `isnull` |
| Integer, Decimal, Currency, Percent, Duration | `eq` `ne` `gt` `gte` `lt` `lte` `between` `in` `nin` `isnull` |
| Date, DateTime, Time | `eq` `ne` `gt` `gte` `lt` `lte` `between` `isnull` |
| Boolean | `eq` `ne` `isnull` |
| Tags, MultiSelect | `contains` `isnull` |
| Reference | `eq` `isnull`, and `ne` with null |
| RichText | `contains` `icontains` `nicontains` `isnull`, on its plain text |

Any other operator for a field is refused with 422. `evlet.query.operators_for(model, name)`
returns the same list.

Queries allow at most 50 conditions (counting those inside child groups), 100 values in an `in`
or `nin` filter, four order fields,
and 100 returned rows. Empty `in` lists match nothing; empty `nin` lists match everything. Use `isnull` for nulls
inside membership queries. Text matching treats `%` and `_` literally;
`icontains` uses SQLite's built-in ASCII case folding, not full Unicode folding.
Values are parameterized. Field paths and operators are checked against the
registry; callers cannot supply SQL. Query values receive type validation without
applying a field's business minimum/maximum or custom validator.

## HTTP API

The existing create, list, detail, health, and schema endpoints remain available.

- `{id}` in these routes is the record's text id ([record ids](#record-ids)); a decimal
  id is not a route.
- `PATCH /api/{model}/{id}` accepts a partial JSON object and returns the saved
  record (200). Unknown fields and invalid values return 422.
- `DELETE /api/{model}/{id}` returns 204 with an empty body. A missing record
  returns 404; a linked-record constraint returns 409.
- `GET /api/{model}` accepts `limit`, `offset`, `filters` (a URL-encoded JSON
  object or list, as above), and `order_by` (comma-separated fields). The aggregate and
  measures routes take the same `filters`. The result keeps the existing
  `items`, `limit`, and `offset` shape. Query strings are limited to 8 KiB.
- Document POST and PATCH accept owned rows as full lists or as change sets
  (`{"create": [...], "update": [...], "delete": [...], "order": [...]}`). A detail embeds
  at most 100 rows per collection and reports larger ones in `children_pages`.
  `GET /api/{model}/{id}/lines/{name}?limit=&offset=` pages one collection. See
  [owned tables](finite-calculations.md#owned-tables).

The neutral schema remains version 1 with additional field types and metadata.
The Playground now generates forms, lists, and details from the extended UI
metadata described in [application generation](application-generation.md).

## How much of a model callers reach

`Meta.api` says which surfaces a model gets. Callers are every HTTP route, MCP clients, Eva
and the connector. Trusted Python is everything else: database sessions, `Context` in
scripts, model actions and moves, legacy `Action` handlers, `changed` reactions, dialogs,
`@api` services, jobs and migrations.

| `Meta.api` | Callers | Trusted Python |
|---|---|---|
| `"full"` (default) | Read and write, by role | Everything |
| `"read"` | Read, by role. No write of any kind, for any role, admin included | Everything |
| `None` | Nothing. Every route answers 404 and no list names the model | Everything |

```python
class LedgerEntry(Model):
    class Meta:
        ledger = True          # lean rows, no public surface (Meta.api is None)
        append_only = True     # the database refuses UPDATE and DELETE

    invoice = Link(Invoice, required=True)
    posting_date = Date(required=True)
    amount = Currency(required=True)


class Receipt(Model):
    class Meta:
        api = "read"           # callers read receipts; only the posting code below writes them

    invoice = Link(Invoice, required=True)
    amount = Currency(required=True)


def post_invoice(tx, user, invoice):           # Action(Invoice, "post", "Post to ledger", post_invoice)
    tx.create(LedgerEntry, invoice=invoice.id, posting_date=invoice.posting_date, amount=invoice.total)
    tx.create(Receipt, invoice=invoice.id, amount=invoice.total)
```

A table that only trusted code writes, at high volume, is usually a [ledger model](ledger.md):
`Meta.ledger = True` gives it an integer id and its declared columns only, and no surface at all.

**`api = "read"`.** Every write route answers 403 "Receipt records are read only here. The
application changes them.": create, update, delete, drafts, actions, moves, void, revise,
unpost, repost, confirm, cancel, reopen, journal entries, attachments and filing a print. So
do the MCP and Eva write tools, which call the same routes. The schema carries `"api": "read"`
and every write permission as false, so screens show no New, Edit, Delete, actions or moves.
Controls overrides cannot grant a write back: the rule is checked before any `Rule`. It is the
same rule that keeps effect rows read only ([effects.md](effects.md#effect-rows-are-read-only)).

**`api = None`.** `/api/<model>` and every route under it answer 404, as for a model that does
not exist. The model is left out of the schema, the sidebar, generated screens, Discovery,
search, measures, Controls, the print catalog and the tool lists. It stays a full model for
migrations, validation, transactions and Python queries. A Python report may read it: its
`run()` is trusted code, and the report's own `roles` decide who sees the columns and totals it
returns ([reports.md](reports.md#models-callers-cannot-reach)). A Link from a model callers
reach to an `api = None` model must be `hidden=True`, and registration fails otherwise, naming
the model and field. A caller cannot write a Reference that names such a model.

`Meta.api` is checked at registration: `"full"`, `"read"` or `None`. Owned rows follow their
document.

## Ledger models

`Meta.ledger = True` makes a lean model for high-volume rows that trusted Python writes, such as
movements and balances: an integer `id` the database assigns, the declared columns and nothing
else, no public surface, and optional `append_only`, `journal`, `timestamps`, `indexes` and
`period`. Transactions also take named locks on business keys with `tx.lock(...)`, and reserve
ledger ids with `tx.reserve_ids(model, count)` so the rows of one batch can link to each other. See
[ledger.md](ledger.md).

## Closed periods

`Meta.period` names the stored Date or DateTime field that places a record in a period.
A lock date closes every period on or before it. `Meta.period_scope` optionally names a stored
Link whose records each have their own lock too, such as one per company.

```python
class Invoice(Model):
    class Meta:
        period = "posting_date"
        period_scope = "company"   # optional
```

Admins set the locks in Controls, Periods ([controls.md](controls.md#periods)). The lock that
applies to a record is the later of the instance lock and its scope record's lock. A
DateTime is read in the instance timezone.

Every writer is refused, trusted Python included, when the saved date or the new date is on or
before the lock: creating a record dated in a closed period, changing or deleting one in it,
moving a date into or out of it, moves and corrections on such a document, and effect rows
dated in it. The error is on the period field: `"This date is in a closed period (locked up to
31-03-2026)"`, a 422 over HTTP and a `ValidationError` in Python. A record with no date is in
no period.

For a migration or a data repair, trusted code opens closed periods for one block. The reason
is required, logged on the `evlet.periods` logger and written to the audit log. No route
reaches it.

```python
with database.transaction() as tx, tx.unlocked_periods("Load March from the old system"):
    tx.create(Invoice, posting_date=date(2026, 3, 10), ...)

with business.context(user) as context, context.unlocked_periods("Fix a mistyped date"):
    context.update(Invoice, invoice_id, posting_date="2026-03-11")
```

How unpost and void date their mirror rows in a closed period is in
[effects.md](effects.md#closed-periods).

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
`AddIndex(..., unique=True)` adds a uniqueness constraint, and `AddIndex(table, ("a", "b"),
unique=True)` adds a [`Meta.indexes`](#composite-and-unique-indexes) entry. Keep historical field
and model definitions in migration files unchanged. Do not point an old
`CreateModel` operation at a model definition that will keep changing.
Migrations compare stored fields only, so a frozen class may have any Python name:
`CreateModel` uses its `storage_name` (or lowercased class name) and its fields.

### Adding a child table

`AddTable(parent, name, ChildClass)` adds an owned table to an existing model. `parent`
is the owning model's storage name, `name` the `Table` attribute and `ChildClass` a frozen
copy of the row model. The engine adds the hidden owner Link and row position itself, the
same way the registry does for `Table(ChildClass)`, so the migration never declares
`evlet_owner` or `evlet_position`.

```python
from evlet import AddTable, Child, Integer, Migration, Text


class Dependent(Child):          # frozen copy of Employee.Dependent
    full_name = Text(required=True, max_length=120)
    age = Integer()


migration = Migration("0002_dependents", AddTable("employee", "dependents", Dependent))
```

Pass `storage_name=` or `owner_field=` when the live `Table(...)` sets them. A nested
table (a table inside the new row model) is its own `AddTable` whose parent is the new
table's storage name, such as `AddTable("employee_dependents", "documents", Document)`.

### Planning a migration

`database.plan(migrations)` reports what `migrate(migrations)` would do without writing
anything. On SQLite it opens the file read-only; on PostgreSQL it only reads.

```python
plan = database.plan(migrations)
print(plan.text())
```

`MigrationPlan.pending` lists the migrations not yet applied. `differences` lists each
`(model, field, text)` that still separates the schema those migrations produce from the
current models, for example `employee.days: type is 'integer' after the migrations and
'decimal' in the models`. `suggestion` is Python for one migration covering what can be
inferred: new models (`CreateModel` with a frozen class), new child tables (`AddTable`),
new fields (`AddField` with a frozen copy of the field), a single renamed field
(`RenameField`), removed fields (`RetireField`), safe type changes (`AlterField`) and new
indexes (`AddIndex`). A required field without a fixed default gets `backfill=...`, and changes
it cannot write become `#` comments; fill both in by hand. A removed field's values are not
moved anywhere: add a `RunData` step when they belong in new fields.
`migrate()` refuses a mismatch with the same list of differences, so the error names the
model, field and property that differs.

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

Supported operations are deliberately bounded: add a model, add a child table, add a field, rename
a field, add an index, retire or drop a field, convert a field safely, run a data step, and recalculate
stored values. Fixed backfills are validated; required added fields need a value. Callable or
NUL-containing backfills, non-null Link backfills, removing a model, other type changes, narrowing
changes, and reverse migrations are not supported. They fail instead of rebuilding tables or guessing
how to transform existing data. Changes to runtime defaults alone do not require a database migration.

### Retiring and dropping fields

`RetireField(model, name)` takes a field out of the models: remove it from `models.py` and the
field leaves schema checks, the HTTP API and every screen. Its column and stored values stay, and
the column becomes nullable so new records leave it empty. Data in revisions and the Journal is
not touched. Use it whenever a field stops being used; nothing is lost.

`DropField(model, name)` later deletes the column and its values. It needs a field an earlier
migration retired (not one retired in the same migration, so the values can be checked or moved
first; a later migration in the same run is fine, since data steps run with their own migration), and it destroys data, so `migrate()` refuses it unless called with
`allow_destructive=True`. From the CLI, `evlet migrate <instance> --allow-destructive` runs it
after writing the usual backup to `data/backups/`.

A retired Link loses its foreign key, so the records it pointed at can be deleted later; its
stored ids stay as they were.

On SQLite, retiring a field and dropping a Link field edit the table's stored definition with
SQLite's documented in-place procedure for constraint changes (`writable_schema` with a schema
version bump), inside the migration transaction. Stored rows are not rewritten. A SQLite build in
defensive mode refuses this, and the migration then fails with a message saying so.

### Changing a field's type

`AlterField(model, name, new_field)` accepts only conversions every stored value survives:

| From | To |
|---|---|
| `Integer` | `Decimal`, `Currency` or `Percent` (values are multiplied by 10^scale) |
| `Decimal` (any decimal kind) | a decimal kind with the same or a wider scale |
| `Text` | `Text` or `LongText` with the same or a longer `max_length` |
| `Select` | `Select` with the same choices plus new ones |

`minimum` and `maximum` may change too. `required`, `unique`, `index` and Link targets may not.
Each stored row is converted and validated against the new definition inside the migration
transaction; a value that overflows or fails (a bound, a length) names the record and rolls the
batch back. Revisions and Journal entries keep the values they recorded.

```python
from evlet import AlterField, Decimal, Migration

Migration("0003_half_days", AlterField("leaveapplication", "days", Decimal(scale=1)))
```

### Data steps

`RunData(function)` runs trusted Python that moves or fills values, for example when one field is
split into two:

```python
from evlet import AddField, Migration, RetireField, RunData, Text


def split_names(session):
    Employee = session.registry.models["employee"]
    for record_id, full_name in session.connection.execute('SELECT "id", "full_name" FROM "employee"').fetchall():
        first, _, last = full_name.partition(" ")
        session.update(Employee, record_id, first_name=first, last_name=last)


migration = Migration(
    "0004_split_name",
    AddField("employee", "first_name", Text(default="")),
    AddField("employee", "last_name", Text(default="")),
    RetireField("employee", "full_name"),
    RunData(split_names),
)
```

Each data step runs right after its own migration's structural operations, in declaration
order, so a later migration in the same run (a `DropField`, say) cannot remove what it reads.
`Recompute` still runs once, after every pending migration. `session` is a trusted Session over
the current models, so a step that runs before a later migration adds a field should not read or
write that model through the session; use `session.connection` with parameterized SQL instead.
Read retired columns the same way.

By default the session refuses writes to confirmed, tracked, posted and finite-v1 documents, as any
trusted session does. A step that must change them is declared `RunData(function, maintenance=True)`
and runs only when the admin allows it for that run: `evlet migrate <instance> --allow-maintenance`,
or `migrate(migrations, allow_maintenance=True)` from Python. Each such step writes a Controls
audit row (actor `cli`, area `Records`, the migration's name) and logs a warning. Their revisions
and Journal entries stay as they were; the step writes no Journal entries of its own.

The migration checksum covers the function's source and `maintenance`, so keep the function in
the migration file and never change it once applied. Any exception rolls back the whole run,
structural changes included. Like `Recompute`, data steps do not run when a database adopts its
baseline.

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
A locked record that never posted, or was unposted, is recalculated and moves to the active
formula version. `database.migrate()` returns the applied migration names as a list whose
`skipped` attribute counts the records kept, and the `evlet.migrations` logger reports the
count. Each record Recompute changes gets a `recomputed` [Journal](journal.md) entry when its
model journals.

Posted records are recalculated only when an admin asks for it, for one migration run:
`evlet migrate <instance> --recompute-posted`, or `database.migrate(migrations,
recompute_posted=True)` from Python. The default stays off. Every Recompute operation of that
run then includes posted records; each one that changes gets a `recomputed` entry with
`"posted": true` (void records are still skipped, so their rows and reversals keep matching
them), the run writes one Controls audit row (actor `cli`, area `Records`) and logs
a warning with the count, and `posted` on the returned list counts them. Their effect rows are
not rewritten, so `evlet effects check` reports them as `changed` until each is unposted and
reposted. Browser requests never run migrations, so Controls has no such switch.

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
the pending migrations, from `0001_model_engine` to `0006_invoice_number`. IDs, original values, and links stay intact. Fresh installations
can still use `bash scripts/playground.sh --setup` directly.

The Playground demonstrates active flags and notes on customers, dates/statuses,
exact tax rates and naming series numbers (`INV-2026-00001`, [sequences.md](sequences.md#naming-series)) on invoices, and timezone-aware timestamps on payments. Its
edit, confirmed delete, search, and ordering controls use the public HTTP API.
Tax rate and status are stored example fields; they do not calculate tax, settle
invoices, post a ledger, or trigger workflow actions. Existing email remains Text
so this upgrade does not retroactively reject previously accepted addresses.

Stored rows are decoded without rerunning custom field validators or
`Model.validate()`. New records and updates still run those rules, so an older row
can be read and repaired after a rule is tightened. Text fields reject NUL
characters on both SQLite and PostgreSQL. Text collation and Unicode case folding
can differ by database backend; explicit nullable ordering is shared.
