# Document numbers

A `Sequence` field holds a human document number such as `INV-2026-00042`. It is separate
from the record's integer `id`. Only the engine writes it, inside the transaction that saves
the record, so committed records get gapless numbers and a number is never used twice.

This guide covers Phase 7 of the model handoff.

## Declaring a Sequence

```python
from evlet import Date, Link, Model, Move, Sequence, State, Text


class Invoice(Model):
    company = Link(Company, required=True)
    posting_date = Date(required=True)
    number = Sequence("INV-{year}-{n:05}", scope=("company",), assign_on="issued", date="posting_date")
    status = State(("draft", "issued", "void"), moves=(
        Move("draft", "issued", "Issue"),
        Move("issued", "void", "Void"),
    ), locked=("issued", "void"), final=("void",), voidable=("issued",))

    class Meta:
        title_field = "number"
```

`Sequence(format, scope=(), assign_on="create", date=None)` also takes `label`,
`help_text`, `placeholder` and `hidden`. It takes no default, rules, calculations,
validators or indexes; the engine adds its own unique index.

### The format

The format is literal text with these tokens:

| Token | Value |
| --- | --- |
| `{n}` | the counter, required, once |
| `{n:05}` | the counter padded with zeros to at least 5 digits (1 to 18) |
| `{year}` | the four digit year |
| `{yy}` | the last two digits of the year |
| `{month}` | the two digit month |

The format is checked when the class is defined. Unknown tokens such as `{day}`, a missing
`{n}`, a token used twice, `{year}` and `{yy}` together, `{n:5}` (padding without the
leading zero), an unmatched `{` or `}`, control characters and formats longer than 120
characters are errors. `{month}` needs `{year}` or `{yy}`, otherwise March numbers would
repeat every year.

### Periods

A format with `{year}` or `{yy}` restarts the counter each year. A format with `{month}`
restarts it each month. The period comes from:

* the Date field named by `date=`, when given. It must be a stored `Date` field of the same
  model. A calculated date is fine, because the number is assigned after the document
  settles. The date must be filled in when the number is assigned, otherwise the write fails
  with `Fill in this date before the number is assigned` on that field.
* otherwise, the day the number is assigned, in the instance timezone (UTC when none is
  configured). For `assign_on="create"` that is the creation date. For a state it is the day
  the record enters that state. Records do not store a creation timestamp, so a later move
  cannot look back at the creation date; name a Date field when that matters.

`date=` without a period token is an error, because it would have no effect.

### Scopes

`scope` names fields whose values each get their own counter, for example one series per
company. Scope fields must be stored, required and not calculated, and be text, number,
Link, date or Boolean fields of the same model. Two companies can both have
`INV-2026-00001`.

### When a number is assigned

`assign_on` is `"create"` (the default) or one of the states of the model's `State`.

* Before assignment the value is null.
* With `"create"`, a new record gets its number when it is saved.
* With a state, the record gets its number when a move enters that state. A new record
  whose default state is `assign_on` gets it on create.
* A record keeps its number for life. Moving back to draft and issuing again keeps the same
  number. A voided record keeps its number.
* Once a number is assigned, the scope fields cannot change (`This is fixed once the number
  is assigned`) and the date field cannot move to another period (`This stays in the period
  of the assigned number`). It may change within the same period.
* The number is assigned after the `run` method, calculations, rules and validation, and
  before the record is written. Calculations, `initial` values and field rules of the same
  document cannot read a Sequence (registration fails), because they would see null on the
  write that assigns it. Move guards, `available_when`, actions and other documents through
  Link lookups can read it.
* A Sequence belongs on a root document, not on `Table` rows.

### Only the engine writes it

* HTTP create and update, `Context.create` and `Context.update`, dialogs and services:
  sending a value is a field error, `This number is assigned by the system`. Sending null on
  create, or the saved value on update, is not a change and is accepted.
* Python code in actions and `run` methods cannot assign it on the draft (the same error).
* Raw `Session.create` and `Session.update` (including legacy `Action` handlers) raise an
  error unless they send the saved value or run inside `tx.maintenance()` for an audited
  repair.
* Its schema entry is `read_only: true`.

## Counters

Counters live in the framework table
`evlet_sequence(model, field, scope_key, period, last)`, created at startup when any model
declares a Sequence, on SQLite and PostgreSQL. `scope_key` is the JSON list of the scope
values (empty without a scope); `period` is `YYYY`, `YYYY-MM` or empty.

Each assignment is one statement that inserts the counter or adds one to it, in the write
transaction of the record. Evlet write transactions are serialized (SQLite `BEGIN
IMMEDIATE`, a transaction lock on PostgreSQL), so parallel writers get distinct numbers.
If anything later in the transaction fails, for example validation, a `changed()` reaction
or a database constraint, the whole transaction rolls back, including the counter, and the
number is given to the next committed record. Deleting a record does not give its number
back.

Each Sequence also has a unique index on its scope fields and the number, for non-null
numbers only (`CREATE UNIQUE INDEX ... WHERE number IS NOT NULL`). Startup and migrations
check that the index exists.

Renaming a Sequence field with `RenameField` keeps the column and its index but not its
counters, which are keyed by field name. Rename the counter rows in the same maintenance
window (`UPDATE evlet_sequence SET field = 'new' WHERE model = 'invoice' AND field =
'old'`); otherwise numbering restarts and the unique index refuses the first repeated
number.

## Schema metadata

The field's entry in `/api/schema` has `type: "sequence"`, `read_only: true` and

```json
"sequence": {"format": "INV-{year}-{n:05}", "scope": ["company"], "assign_on": "issued", "date": "posting_date"}
```

A Sequence is a short text field, so it can be `Meta.title_field`, a list column and a
search field. The stored model definition records only the scope (it shapes the index).
The format, `assign_on` and `date` can change without a migration; numbers already assigned
keep their values.

## Adding a Sequence to an existing table

```python
Migration("0004_invoice_number", AddField("invoice", "number", Sequence("INV-{year}-{n:05}", scope=("company",))))
```

`AddField` adds a nullable column and the unique index. Existing rows stay null; a backfill
value is refused. New records are numbered from the counter as usual. To number existing
rows, run `evlet.sequences.backfill` once, in one transaction:

```python
from evlet.sequences import backfill

with database.transaction() as tx:
    backfill(tx, Invoice, "number", filters={"status__in": ["issued", "void"]})
```

It numbers the rows whose value is null in id order, from the same counters, so numbers
stay unique and later records continue the series. `filters` selects the rows. For a
format with a period but no `date=` field, pass `today="YYYY-MM-DD"`; otherwise each row's
date field decides its period. Run it before the application takes new writes if the
existing rows should get the lower numbers.

## For later phases

* `evlet.sequences.clear_for_copy(model, values)` returns a copy of `values` with every
  Sequence set to null. Revise (Phase 5) calls it when it prepares a revised copy, so the
  revision gets its own number when it reaches `assign_on`.
* Void (Phase 5) leaves the number unchanged.
* `evlet.sequences.assigned(context, model, record_id, field, number)` is called once per
  assigned number after the record is saved, in its transaction. The Journal (Phase 8)
  records the assignment there.
