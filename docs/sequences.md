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
    ), locked=("issued", "void"), final=("void",), voidable=("issued",))

    class Meta:
        title_field = "number"
        revisable = True
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

`{yy}` repeats every hundred years: `S26-1` of 2026 and of 2126 look the same. When a number
is already taken in its scope, the counter moves past it (see "Clashes" below), so the later
century continues the series instead of failing. Use `{year}` when that matters.

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
Link, date or Boolean fields of the same model. The model's `State` cannot be a scope field,
because it changes after the number is assigned. Two companies can both have
`INV-2026-00001`.

### When a number is assigned

`assign_on` is `"create"` (the default) or one of the states of the model's `State`.

* Before assignment the value is null.
* With `"create"`, a new record gets its number when it is saved.
* With a state, the record gets its number when a move enters that state. A new record
  whose default state is `assign_on` gets it on create.
* A record keeps its number for life. Moving back to draft and issuing again keeps the same
  number. A voided record keeps its number, and a record voided before its number was due
  stays without one. Voiding never assigns a number.
* A revision (`revise`) is a new record: its copy starts with every Sequence null. With
  `assign_on="create"`, or when the copy's starting state is `assign_on`, the engine gives
  the copy a fresh number as it creates it; otherwise the copy gets one when a move enters
  `assign_on`. The original is void and keeps its number. Until the copy is numbered its
  scope fields and date may change like those of any draft.
* Once a number is assigned, the scope fields cannot change (`This is fixed once the number
  is assigned`) and the date field cannot move to another period (`This stays in the period
  of the assigned number`). It may change within the same period. A role that cannot read
  the number gets `This cannot change now` instead, so the message does not reveal that a
  number exists. Raw `Session.update` (and legacy `Action` handlers) get the same check
  unless they run inside `tx.maintenance()`.
* The number is assigned after the `run` method, calculations, rules and validation, and
  before the record is written. Calculations, `initial` values and field rules of the same
  document cannot read a Sequence (registration fails), because they would see null on the
  write that assigns it. Move guards, `available_when`, actions and other documents through
  Link lookups can read it.
* A Sequence belongs on a root document, not on `Table` rows.

### Only the engine writes it

* HTTP create and update, `Context.create` and `Context.update`, dialogs and services:
  sending a value is a field error, `This number is assigned by the system`. Sending null on
  create, or the saved value on update, is not a change and is accepted. A role that cannot
  read the field gets 403 for any value, so it cannot test guesses.
* Python code in actions and `run` methods cannot assign it on the draft (the same error).
* Raw `Session.create` and `Session.update` (including legacy `Action` handlers) raise an
  error unless they send the saved value or run inside `tx.maintenance()` for an audited
  repair.
* A raw `Session.create` numbers a Sequence that is due on create, like `Context.create`.
  Without `date=`, its period is today in UTC, because a session has no instance timezone.
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

Each document operation (create, update, move, action, delete, void, revise) runs inside a savepoint. If
trusted code catches a failed operation inside one `Context` and the transaction still
commits, the failed operation leaves nothing behind: no counter step, no state change, no
number. A unique or foreign key violation inside an operation is raised as `ConflictError`.

### Clashes

A number can already be taken in its scope, for example after a repair inside
`tx.maintenance()`, after the format is changed back to an older one, or when `{yy}` wraps.
The engine then moves the counter past every number that a record of the same scope holds,
in the same transaction, and assigns the first free one. The skipped values are not used
later. In normal operation nothing is skipped and numbers stay gapless.

Changing the format does not reset counters. Counters are keyed by model, field, scope and
period, so a new format continues the old count. If a new format renders values the old
one produced, those values are skipped as above.

Each Sequence also has a unique index on its scope fields and the number, for non-null
numbers only (`CREATE UNIQUE INDEX ... WHERE number IS NOT NULL`). Startup and migrations
check that the index exists.

Renaming a scope field with `RenameField` is supported: the stored scope follows the new
name, and counters keep working because they store scope values, not names.

Renaming the Sequence field itself keeps the column and its index but not its counters,
which are keyed by field name. Rename the counter rows in the same maintenance window
(`UPDATE evlet_sequence SET field = 'new' WHERE model = 'invoice' AND field = 'old'`).
Otherwise a new counter starts and skips every number already held, which costs one query
per skipped number on the first assignment.

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

## Naming series

A naming series fills a record's standard `name` ([model-engine.md](model-engine.md#name-and-title)),
with rules an instance can change in Controls. It runs on the same counters as a Sequence.

```python
from evlet import Date, Decimal, Model, Naming, Select, Series


class Invoice(Model):
    total = Decimal(scale=2)
    status = Select(("draft", "sent"), default="draft")
    posting_date = Date()

    class Meta:
        naming = Naming(Series("LC-{YYYY}-{####}", when='total >= 250000 and status == "sent"', label="Large contracts"),
                        Series("INV-{YYYY}-{####}", label="Invoices"),
                        date_field="posting_date")
```

* Series are checked from the top; the first whose condition matches is used. The last series has
  no condition, and only the last, so every record gets a name.
* A trusted `Session.create` may pass a name (imports); it is kept. Without one the series names it,
  dated in the instance timezone. Sending `null` for `name` on create is the same as leaving it out;
  a client that sends a name before the series has given one gets `name: Assigned on save`.

**Other ways to name.** `Naming()` with no series calls the model's `autoname(self, context)` method
in the write transaction; it returns the name, which must be free and at most 140 characters.
`Naming(manual=True)` lets people type the name. Without `Meta.naming` the name stays empty.

```python
class Ticket(Model):
    subject = Text(required=True)

    class Meta:
        naming = Naming()

    def autoname(self, context):
        return 'T-' + self.subject.upper().replace(' ', '-')
```

**When.** `assign_on="create"` (the default), `"confirm"` for a confirmable model, or a state of the
model. A confirm-time name is taken in the confirm transaction, so drafts never use a number and the
numbers of confirmed documents have no gaps, as UAE tax invoices need. Until then the name is
empty and the form shows "Assigned on confirm".

**Format.** `{YYYY}`, `{YY}`, `{MM}`, `{DD}`, exactly one counter `{###}` to `{########}` (the `#`
count is the zero padding) and `{field}`, the value of a short text field of the model with `/` and
spaces replaced by `-`. Names are 40 characters at most after expansion: a format whose fixed part
is longer is refused when saved, and a field value that makes a name longer is refused on create. Date tokens use
`date_field`, or the day the name is assigned in the instance timezone.

**Conditions** are text, parsed by `evlet/expressions.py`: `field op value`, `field in (a, b)`,
`and`, `or`, `not` and brackets, with `==`, `!=`, `>`, `>=`, `<`, `<=`. Values are quoted text,
numbers, `true`, `false` or `empty` (matches null and ""). Numbers compare as decimals, dates as ISO
text, a Link by its id. No functions, attributes, arithmetic or other names; 500 characters and 10
levels of brackets and `not` at most. Values must suit the field: an option of a choice field, a
date as `"2026-01-31"`, and `empty` only with `==` or `!=`. Errors read like `Use == to compare, not =.`,
`totl isn't a field on this model. Did you mean total?`, `Put text values in quotes, like "export".`
and `A quote is not closed.`

**Counters.** One counter per model and resolved prefix, the expanded text before the counter
token. A new year or month gives a new prefix, so it starts at 1. A counter is a row of
`evlet_sequence` with `field` the target field (`name`), `scope_key` the prefix and an empty `period`; the
table also keeps the last name, how it last changed (`issued` or `set`), the note, who and when.
Issuing is one insert-or-increment statement in the write transaction, as for a Sequence, so
writers never get the same name. A name that is already taken is skipped, up to 1000 times.
Controls can set a counter with a reason, never below the number in the last issued name.

How it differs from a Sequence: a Sequence is a field type, fixed in code, with scope fields; a
naming series is a `Meta` declaration that fills the standard name, whose series and counters an
instance can change in Controls.

**In the browser.** The model's `/api/schema` entry has
`naming: {"field": "name", "assign_on": "create", "manual": false, "placeholder": "Assigned on save"}`
when the role can read the name. The New screen shows the name disabled with that placeholder and
never sends it. The record page shows the title as its heading and the name next to it in a
monospaced font.

**Edits, reopening and revisions.** Once a record has a name, anyone who may write the `name`
field can change it through the normal edit; the change is journaled like any field and a taken name
is refused. Links point to the id, so nothing else changes. A confirmable document keeps its name
through confirm, cancel, reopen and confirm again, and its name cannot change after its first
confirmation; Evlet does not add `-1`, `-2` suffixes ([lifecycle-specification.md](lifecycle-specification.md)).
A revision (`revise`) is a new record and gets a new name by the same rules; the voided original
keeps its own.

**Gaps.** Issuing happens in the write transaction, so a write that fails and rolls back also
rolls back its counter step, and the next committed record gets that number. Deleting a record
does not give its name back.

**The Playground.** `Invoice` is named `INV-{YYYY}-{#####}`, dated by its posting date, so the
first invoice of 2026 is `INV-2026-00001`. Migration `0008_record_names` moved the earlier `number`
field into `name` and retired `number`; the counters moved with it. `reference` stays the
customer's own reference, such as their purchase order number.

```python
class Invoice(Model):
    class Meta:
        naming = Naming(Series('INV-{YYYY}-{#####}', label='Invoices'), date_field='posting_date')
```

**Older targets.** `Naming("reference", Series(...))` still names another unique, optional Text
field instead, on create only, and keeps that field read only. To move such a field into `name`, add
a migration like the Playground's: `RetireField` the old field and a `RunData` step that calls
`session.add_standard_columns(Model)`, copies the values and moves the counters
(`UPDATE evlet_sequence SET field = 'name' WHERE model = ... AND field = '<old>'`).

## For later phases

* `evlet.sequences.clear_for_copy(model, values)` returns a copy of `values` with every
  Sequence set to null. `evlet.corrections.prepare_copy()` calls it on a revision's copy.
* `evlet.sequences.assigned(context, model, record_id, field, number)` is called once per
  assigned number after the record is saved, in its transaction. It writes the Journal's
  `number` entry ([journal.md](journal.md)).
