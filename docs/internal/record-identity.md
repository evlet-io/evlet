# Record identity: id, name and title

Status: in progress. The owner fixed the design; this note records how Evlet implements it.

Every model has three standard columns:

| Column | What it is | Unique | Changes |
|---|---|---|---|
| `id` | Compact, time-ordered 63-bit key. Links, child rows, revisions, the journal and URLs point to it. | yes | never |
| `name` | The human identifier, such as `INV-2026-0001`. Not on child tables. | per model (and tenant) | a normal edit, where the field is writable |
| `title` | The display text, such as a customer's full name. | no | kept in sync on every write |

## 1. `id`

**Bits.** 42 bits of milliseconds since 01-01-2026 00:00 UTC (about 139 years), then 21 random
bits: 63 bits, so it is a positive signed 64-bit integer. One helper, `evlet.identity.new_id()`,
makes every id. Within one process ids are strictly increasing: when the clock has not moved past
the last id, the next id is the last one plus one.

**Storage.** `INTEGER PRIMARY KEY` on SQLite (the rowid, so lookups keep the fast path) and
`bigint` on PostgreSQL. Table definitions do not change: the column keeps `AUTOINCREMENT`
(an identity column on PostgreSQL), so a raw insert without an id from older code or a data
migration still works, and schema checks see the same columns. The framework always supplies
the id itself. Nothing is rewritten.

**Fixtures.** `evlet.identity.sequential_ids()` makes inserts number rows 1, 2, 3 per table, as
autoincrement did, so recorded browser fixtures stay the same between runs. Tests and fixture
scripts only.

**Collisions.** The insert is `INSERT ... ON CONFLICT ("id") DO NOTHING`. When it inserts nothing,
the id was taken, so the framework draws a new id and tries again, up to 8 times. Other unique
constraints still raise as before. This works on both engines without aborting a PostgreSQL
transaction.

**Text form.** Lowercase Crockford base32, always 13 characters (13 x 5 = 65 bits, the top two are
zero), alphabet `0123456789abcdefghjkmnpqrstvwxyz`. Input accepts any case and reads `i`, `l` as `1`
and `o` as `0`. `u` and any other character are refused. The first character is always 0 to 7.

**Legacy ids.** Rows made before this change keep their small integer ids. They read and write as
the same text form: 42 is `000000000001a`. New rows sort after them.

**In Python.** Trusted Python keeps integers: `record.id` and Link values are `RecordId`, a subclass
of `int` whose `str()` is the text form. SQL parameters, comparisons, hashing and dictionary keys
behave as ints. `RecordId.parse(value)` takes an int or the text form.

**At the boundaries** only the text form is used, never a JSON number (JavaScript loses precision
above 2^53):

- HTTP JSON: record `id`, Link values, Reference `{"model", "id"}`, child rows, revisions,
  lifecycle results, journal entries and their row ids, attachments' `record_id`, events,
  jobs' `record_id`, Slips' `source`, saved filters, related lists and lines.
- URL paths: `/api/invoice/01hc1sd9vc1ye` and `/app/invoice/01hc1sd9vc1ye`. The API refuses a
  decimal path segment. The browser router turns an old decimal bookmark (`/app/invoice/42`) into
  the text form and replaces the URL.
- The neutral schema and tool schemas describe ids as `{"type": "string", "pattern": ...}`.
- Eva, MCP and the Tool Engine take and return the text form.
- The dev sign-in link and imports take the text form or, from trusted Python, an int.

Writes accept the text form. They also accept a JSON integer below 2^53 so that existing clients
keep working; responses never contain one.

**Not record ids.** Framework rows that are not models keep their own integer keys: users, API
keys, journal entry numbers, jobs, attachments, events and tool calls. Where one of them names a
record (`record_id`), that value is a record id and uses the text form.

## 2. `name`

**Column.** A nullable `TEXT` column `name` on every model that is not a child row, with a unique
index over the assigned names. Under tenancy each tenant has its own database, so uniqueness is
per tenant. Row scopes do not change uniqueness: two names never collide across scopes.

**Filled by**, in this order:

1. A trusted value: `Session.create(name=...)` (imports) or manual entry where `Meta.naming`
   allows it (`Naming(manual=True)`).
2. A naming series: `Meta.naming = Naming(Series(...), ...)`. The target defaults to `name`.
   The older form `Naming("reference", ...)` still names that field; see "Sequences" below.
3. An autoname hook: `def autoname(self, context) -> str` on the model. It runs inside the write
   transaction after values are settled. The framework checks the length (140 characters),
   that it is not blank, and uniqueness; a taken name is a validation error on `name`.

**When.** `Naming(..., assign_on="create")` (the default) or `assign_on="confirm"` for a
confirmable model. Confirm-time naming takes the number in the confirm transaction, so a
draft never uses a number: document numbers have no gaps. Until then `name` is null and forms
show "Assigned on save" or "Assigned on confirm".

**Revisions and amendments.** Following `docs/lifecycle-specification.md`, a lifecycle revision
(reopen and confirm again) is the same record, so it keeps its id and name. An amendment made by
`revise` is a new record: its `name` is cleared and assigned again by the same rules, and
`revises` links it to the original.

**Changing a name.** `id` is the primary key and the only thing Links, child rows, revisions,
the journal and URLs point to, so changing `name` is a plain one-row update with no cascade
(owner, 03-11-2026). Anyone who may write the `name` field changes it through the normal edit
path: the unique index refuses a taken name, and the journal records the change like any other
field. The usual protections stay: a confirmed (locked) document stays locked, and a name a
naming series assigns is read only in forms while it is system assigned (the server fills it).
`title` is an ordinary stored field too.

## 3. `title`

**Column.** A stored, non-unique `TEXT` column `title` on every model that is not a child row.

**Source.** `Meta.title_field = "full_name"` or a template `Meta.title = "{first_name} {last_name}"`
of the model's own short fields. The framework fills it on every create and update inside the
transaction. With neither, `title` is the name, then the text id.

**Display.** Link pickers, list rows, search, breadcrumbs, Slips and Eva show `title`. A record
page shows `title` as its heading and `name`, when there is one, as the document number next to it
in the mono font.

## Migrations

- New tables get `id`, `name` and `title` from `CreateModel`.
- Existing app tables gain `name` and `title` from a framework step in the A4 ledger
  (`evlet/framework.py`), which `evlet migrate` runs after app migrations, with its backup. The step
  adds the columns, the unique index and backfills `title`. `name` stays null unless the app
  backfills it (`evlet.sequences.backfill` or a `RunData` migration).
- A model that names a field `Sequence` or naming target other than `name` keeps working. To move a
  number into `name`, an app adds a `RunData` migration that copies it and then retires the field.
  The Playground does this for `Invoice.number`.

## Declared `name` and `title` fields

Decided by the owner on 03-11-2026 ("Yes, reserve name"):

- A model may declare its own `title` field (a short Text). That field is the standard title:
  people edit it directly, and nothing is migrated.
- `name` is reserved. A model that declares a `name` field fails at definition with a message to
  rename it (for example to `customer_name` or `full_name`) with a `RenameField` migration. The
  Playground, testcrm, the examples, the app templates and the docs are updated in the same change,
  and the CHANGELOG records it as a breaking change.
