# Record identity: id, name and title

Status: implemented in two pull requests (ids, then name and title). The owner fixed the design; this note records how Evlet implements it.

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

**Column.** A nullable `TEXT` column `name` on every record model (not Table rows, Slips or dialogs),
with a unique index. Under tenancy each tenant has its own database, so uniqueness is per tenant.
In Python the standard fields are ordinary fields marked `field.standard` (`evlet/naming.py`). They
are left out of the app schema that app migrations compare, like `id`.

**Filled by** `Meta.naming`:

1. `Naming(Series(...), ...)`: a naming series. The target defaults to `name`. The older form
   `Naming("reference", ...)` still names that field, on create, and keeps it read only.
2. `Naming()` with the model's `autoname(self, context) -> str` method, run inside the write
   transaction. The framework checks it is not blank, at most 140 characters and free.
3. `Naming(manual=True)`: people type it.
4. A trusted `Session.create(name=...)` (imports) keeps the name it is given.

Without `Meta.naming` the name stays empty and read only, and forms do not show it.

**When.** `assign_on="create"` (the default) or a state of the model. A name given on entering a
state is taken in that move's transaction, so drafts never use a number. Until then forms show
"Assigned on save" or "Assigned on <state>".

**Reopening.** A document reopened and confirmed again (rules of its State) is the same record,
so it keeps its id and name. Revise and its copies were removed in 0.4.6.

**Changing a name** (owner, 03-11-2026). `id` is the primary key and the only thing Links, child
rows, revisions, the journal and URLs point to, so changing `name` is a plain one-row update with
no cascade and no separate Rename feature. A client cannot type a name a series or autoname is
about to assign (`Assigned on save`); once assigned, anyone who may write the `name` field
changes it through the normal edit. The unique index refuses a taken name and the journal records
the change like any field. A confirmed document stays locked as before.

## 3. `title`

**Column.** A stored, non-unique `TEXT` column `title` on every record model. A model may declare
its own `title` Text field: that field is the title, edited like any field.

**Source.** Otherwise `Meta.title_field = "customer_name"` or a template
`Meta.title = "{first_name} {last_name}"` of the model's own short fields. The framework sets it on
every write inside the transaction (raw sessions, the document pipeline and posted effects). With
no source, `title` is the name, then the text id.

**Display.** The schema's `title_field` is `title` for record models, so Link pickers, list rows,
search, breadcrumbs, Slips and Eva show it. A record page shows `title` as its heading and `name`,
when the model has naming, as the document number next to it in mono. Search covers `title` and
`name`.

**Permissions.** A role reads `title` only when it may read the fields it repeats (its source
fields, or `name` when it falls back to it), so a title never reveals a hidden field.

## Migrations

- Tables are created with `id`, `name` and `title`, from the models at startup or by `CreateModel`
  and `AddTable`. `CreateModel`'s checksum leaves the standard fields out, so migration histories
  applied before this change keep their checksums.
- A model that declares its own `name` field describes the schema from before records had standard
  columns (a frozen class in migration history, a legacy registry). It gets neither standard field,
  and its tables are created without them, so a later `RenameField(..., 'name', 'title')` meets the
  table as it was. Applications refuse such a model.
- Existing tables gain `name` and `title` in two places that run the same idempotent code
  (`naming.add_columns` and `naming.fill_titles`): at the end of every app migration batch, and in
  framework step `0003_record_names` of the A4 ledger (`evlet/framework.py`), which `evlet migrate`
  and `Application.initialize` run after app migrations, so an instance with no app migration to
  run gets them too. Steps may now ask for the registry (`FrameworkStep(..., registry=True)`),
  because filling titles needs the models. Until then, schema checks accept a table without both
  columns and reads treat them as empty; an instance refuses to serve while the step is pending.
- `name` stays empty for old rows unless the app fills it. A data migration that moves an older
  number into `name` calls `session.add_standard_columns(Model)` first, because the batch adds the
  columns only at its end. The Playground's `0008_record_names` does this for `Invoice.number`,
  moves its counters, and retires the old field.
- A record table turned into Table rows keeps its old `name` and `title` columns, unused; schema
  checks accept them as long as they are nullable.

## Declared `name` and `title` fields

Decided by the owner on 03-11-2026 ("Yes, reserve name"):

- A model may declare its own `title` field (a short Text). That field is the standard title:
  people edit it directly, and nothing is migrated.
- `name` is reserved. An `Application` refuses a record model that declares a `name` field, with a
  message to rename it (for example to `customer_name` or `full_name`) with a `RenameField`
  migration. The check runs when the application is built, not when the class is defined, because
  frozen classes in older migration files describe the schema of their time and must stay
  importable (the Playground's 0002 `Project` declares `name`). The Playground, testcrm, the
  examples, the app templates and the docs are updated in the same change, and the CHANGELOG
  records it as a breaking change.
