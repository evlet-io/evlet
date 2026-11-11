# Model meaning: key fields, relations and attachments

Three `Model.Meta` keys tell any client, report or connector what a record means:
which fields summarize it, which other records point to it, and whether it holds
files. They change no stored data and need no migration.

```python
from evlet import Attachments, Currency, Date, Link, Model, Related, Text


class Invoice(Model):
    customer = Link(Customer, required=True)
    total = Currency(default=0)
    due_on = Date()

    class Meta:
        key_fields = ("customer", "total", "due_on")
        relations = (Related("payment", "invoice", label="Payments", limit=5, order="-paid_on"),)
        attachments = Attachments(max_files=5, max_size=262_144)


class Payment(Model):
    invoice = Link(Invoice, required=True)
    amount = Currency(default=0)
    paid_on = Date()
```

The UI uses `key_fields` for the Key fields block and `relations` for the Related
block of the record view.

## Key fields

`Meta.key_fields` names up to eight distinct visible fields of the model. Hidden
fields, unknown names and Tables are rejected when the registry is built. Tables
are listed under `children` in the schema.

The schema entry has `key_fields` only when the model declares them, and lists only
the fields the caller may read.

## Cards

`Meta.cards` names up to five derived values shown as cards at the top of a compact record
page: fields with `compute=`, `fetch=` or `query=`, fields `derive()` sets (`Meta.derives`),
and per-record measures. Like key fields,
the schema lists only the cards the caller may read. See
[ui.md](ui.md#compact-record-page).

## Status

`Meta.status` names a visible Select or State of the model shown as its status badge in the
record header and in lists. See [states-actions.md](states-actions.md#meta-status).

## Relations

A relation is a reverse Link: the records of another model whose Link holds this
record's id.

**Discovery.** Every Link on a root model is a relation of the model it points to.
This includes several Links from one model and Links from a model to itself. A
model with `Meta.revisable = True` gets a relation named `Revisions` through its
`revises` Link. Not discovered:

- Links on Table rows. Rows belong to their document, which has no route of its
  own for them, so they cannot be listed as related records.
- Links on models registered as a `ChildTable`. Their rows are already listed under
  the parent's `children`.
- Hidden Links. A `Related` cannot name one.

Because every other Link is discovered, a model that many others point to (a
customer, a company) gets many relations. Hide the ones readers do not need with
`Related(model, link, hidden=True)`, and set a small `limit` on large ones. A role
sees only the relations it can read (see Schema below).

**Names and defaults.** A relation is named `{model}.{link}`, for example
`payment.invoice`. Its default label is the other model's plural label. When that
model has more than one Link to this model, the field label is added:
`Payments (Refund of)`. The default page size is 10 and the default order is
newest first (`-id`).

**`Related`.** `Related(model, link, *, label=None, limit=10, order="-id", hidden=False)`
changes one discovered relation:

- `model` is the class or its model name or resource id; `link` is its Link field to
  this model.
- `label` replaces the default label.
- `limit` is the API's default page size, 1 to 100. The record page always shows five rows a page.
- `order` is one to three stored fields of the other model, each optionally with a
  leading `-` for descending order. Reference fields cannot be used.
- `hidden=True` removes the relation from the schema and the related route.

In `Meta.relations`, `Related` names its Link and takes no `filters` or `fields`. The same class
is also a record layout block in `Meta.layout`, where `link` may be left out and `filters`, `fields`
and `limit` shape one list on the record page; see [ui.md](ui.md#record-layouts). A model whose
layout declares a `Related` block no longer shows the automatic Related row unless
`Meta.side_blocks` names `related`; the relations stay in the schema and the related
route.

Declared relations come first, in the order of `Meta.relations`; the others follow
by name. Registration fails when a `Related` names something that is not a Link
from a root model to this model, names a hidden Link, is declared twice, orders by
an unknown field, or names a `ChildTable` model.

**Subclasses.** A model that inherits its parent's `Meta` applies only the inherited
`Related` entries whose Link points to the subclass itself; the others describe the
parent and are skipped. A subclass that declares its own `Meta` is checked strictly.

**Schema.** Each schema entry lists in `relations` the relations the caller can use:

```json
{"name": "payment.invoice", "label": "Payments", "model": "payment",
 "link": "invoice", "limit": 5, "order": ["-paid_on"]}
```

A relation is shown only when the caller may read the other model and its Link
field. `relations` is left out when no relation is shown. `order` is the order the
related route applies for this caller (see below), so it never names a field the
caller cannot read.

**Listing related records.** `GET /api/{model}/{id}/related/{name}` returns one page:

```json
{"items": [...], "total": 3, "limit": 5, "offset": 0}
```

It accepts `limit` (1 to 100, default the relation's limit) and `offset`. The caller
must be able to read the owning record (otherwise 404). The rows are those the caller
may read under the other model's row scope, with only readable fields. Order fields
follow the rule of the list route's `order_by`: a field the caller cannot read, or a
Link to a model on which the caller has a row scope, is skipped, because ordering by
it would reveal values the caller cannot see. The order falls back to `-id` when
nothing is left. Unknown or hidden relations answer 404.

The list route also works: `GET /api/payment?filters={"invoice": 7}` (URL encoded).
It is refused with 403 when the caller has a row scope on the invoice model, because
a filter on that Link could test invoices the caller cannot see. The related route
does not have this limit, because it first checks that the caller can read the
invoice.

**Counts.** `total` on the related route is the count for the caller. The schema
does not carry counts. For a count beside the record, declare a per-record measure
on the same Link, such as `Count(Payment, link="invoice")` in `Meta.measures`, and
read `GET /api/invoice/{id}/measures`. It applies the caller's row scope on the
related model, like the related route (see [measures.md](measures.md)).

## Attachments

`Meta.attachments = Attachments(max_files=10, max_size=1_048_576)` enables private
attachments on a model's records. `max_files` is 1 to 10 and `max_size` is 1 to
1,048,576 bytes; these are the framework maximums and the defaults.

`Application(attachments=(Model,))` keeps working and enables the default limits.
A model can appear in both places; then the limits from `Meta.attachments` apply.

Table rows and `ChildTable` models cannot declare `Meta.attachments`,
`Meta.key_fields` or `Meta.relations`; registration fails. Naming a `ChildTable`
model in `Application(attachments=...)` is still accepted, as in earlier releases,
but its rows have no route of their own, so no files can be added to them.

A revision (see [states-actions.md](states-actions.md)) starts with no attachments:
`revise` does not copy them. The original keeps its files.

Uploads above the limits answer 422 with a plain message, for example
`Use a nonempty file up to 256 KiB` or `Use at most 5 attachments per record`.
The routes, storage and permissions are unchanged; see
[business-capabilities.md](business-capabilities.md#private-attachments).

The schema entry keeps `attachments` (true or false) and adds
`attachment_limits: {"max_files": 5, "max_size": 262144}` when it is true.
