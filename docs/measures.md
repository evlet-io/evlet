# Measures and the aggregate API

A measure is a total, count, minimum or maximum over records. You can ask for
measures ad hoc, grouped by fields or by part of a date, or declare named measures on
a model so lists and records can show them. An aggregate runs one SQL statement, applies
the caller's row scope and field permissions, and gives the same results on SQLite and
PostgreSQL.

This guide covers Phase 9 of the model handoff.

## Functions

```python
from evlet import Count, CountDistinct, Sum, Min, Max, Day, Month, Year
```

| Function | Counts or reads | Fields | Result with no values |
|---|---|---|---|
| `Count()` | records | none | `0` |
| `CountDistinct("field")` | distinct non-empty values | any field except `Tags`, `Reference` and `LongText` | `0` |
| `Sum("field")` | non-empty values, added exactly | `Integer`, `Decimal`, `Currency`, `Percent`, `Duration` | zero at the field's scale, for example `Decimal("0.00")` |
| `Min("field")`, `Max("field")` | non-empty values | numbers, `Date`, `DateTime` | `None` |

Every function takes `where=`, a condition on the record (see below). The field must be
stored: a virtual or `stored=False` field cannot be measured.

Sums are exact. Decimal and Currency values are stored as scaled integers and the
database adds those integers, so `1000 x 0.10` is exactly `100.00`. Only the final
total is checked: it must lie in the signed 64-bit range of stored values, from
-2^63 to 2^63 - 1 scaled units (for Currency, about 92 million billion either way).
A larger total is an error, never a rounded or floating point number. Intermediate
totals never fail, so the result does not depend on the order of rows and SQLite and
PostgreSQL agree. With `group_by`, one group out of range fails the whole request.

## Ad hoc aggregates in Python

```python
from evlet import Count, Month, Sum
from evlet.calc import F

rows = database.aggregate(
    Invoice,
    measures={"amount": Sum("total"), "count": Count(), "open": Sum("outstanding", where=F("status") == "issued")},
    group_by=("status", Month("posting_date")),
    filters={"customer": 4},
)
# [{"status": "draft", "posting_date__month": "2026-01", "amount": Decimal("20.00"), "count": 1, "open": Decimal("0.00")}, ...]
```

* `measures` maps a result name to a measure, or to the name of a declared
  model-level measure (`{"open": "outstanding"}`). Names use lowercase letters, digits
  and underscores. Use one to eight measures.
* `group_by` is a field name, `Day("date")`, `Month("date")` or `Year("date")`, or a
  tuple or list of up to four of them. Group keys are the field name, or
  `<field>__day`, `<field>__month` and `<field>__year`. Measure names must differ from
  group keys. Fields that cannot be grouped are `Tags`, `Reference` and `LongText`.
* `filters` uses the [list filter syntax](business-capabilities.md), for example
  `{"status": "issued", "total__gte": "10"}`.
* The result is a list of dictionaries, one per group, with group keys and then measure
  names. Without `group_by` it is one dictionary, even when no record matches.
* Only groups that have records appear. An empty value (`None`) forms its own group.
  Groups are sorted by their keys in order, empty values first.
* A query with more than 500 groups is an error. Add filters or group by fewer fields.

Group values are Python values: the field's value for a plain field, a `date` for
`Day`, `"YYYY-MM"` text for `Month` and an integer for `Year`. Dates are plain
calendar dates, so the server or user timezone never moves a record into another day,
month or year. `DateTime` fields cannot be grouped by part, because a day depends on a
timezone.

Inside a transaction, `tx.aggregate(...)` takes the same arguments and returns the same
result, and it sees the transaction's own uncommitted writes:

```python
with database.transaction() as tx:
    tx.create(Invoice, customer=4, total="10.00")
    [row] = tx.aggregate(Invoice, measures={"amount": Sum("total")}, group_by="customer", filters={"customer": 4})
```

Use it in trusted code that checks a total before it writes, such as a job or a data step.
It replaces calling `evlet.measures.aggregate(tx, ...)`, which is not public.

`database.aggregate` and `tx.aggregate` are trusted: they apply no permissions. In request handlers,
actions and services, use the permission-aware form on `Context`:

```python
rows = context.aggregate(Invoice, measures={"amount": Sum("total")}, group_by="status")
```

It returns the same shape and applies the caller's row scope to every record counted.

## where= conditions

`where=` takes a [finite-v1](finite-calculations.md) Boolean expression. A record
counts only when the condition is true.

```python
Sum("outstanding", where=F("status") == "issued")
Count(where=(F("total") > D("1000")) & ~F("approved"))
Max("due_on", where=(F("status") != "paid") | (F("total") >= F("paid")))
```

Supported:

* Comparisons `==`, `!=`, `<`, `<=`, `>`, `>=` between a field and a value, or between
  two fields of the same kind. Number fields compared with each other need the same
  scale. Values are text, numbers (`10` or `D("1.005")`), `True`, `False` and `None`.
  Ordered comparisons need two numbers or two dates.
* A `Boolean` field on its own, and `&`, `|` and `~`.
* Fields of the measured model that are stored.
* A `Date` field compared with `today()`, `today() + days(n)`, `today() - days(n)` (a whole
  number `n`) or `month_start()`, the first day of the current month. They are query
  parameters decided when the measure runs, in the instance timezone for `Context` and HTTP
  calls (UTC for `Database.aggregate` and `Database.measures`).

```python
from evlet.calc import F, days, month_start, today

Sum("total", where=(F("stage") == "won") & (F("won_on") >= month_start()))
Count(where=F("due_on") < today())
Count(where=F("posting_date") >= today() - days(30))
```

`month_start()` belongs only in a measure's `where=`. A `compute=`, rule or guard that uses it is
rejected when the model is registered, with a message that names the field.

Not supported, and rejected with a clear message when the measure is declared (or
when an ad hoc call is made): arithmetic (`F("total") - F("paid") > 0`), Link paths
(`F("customer.name")`), `today()` outside a Date comparison, `when()`, `coalesce()`, `round()`, table aggregates,
comparisons with no field, and `Tags`, `Reference` and `Time` fields. Put a calculation
in a stored field and compare that field instead.

Field kinds have these limits, as in finite-v1 generally:

* `Date` fields compare with other `Date` fields or `None`. There is no date literal;
  use a filter (`{"posting_date__gte": "2025-01-01"}`) for a fixed date.
* `DateTime` fields support `==` and `!=` with another `DateTime` field or `None`
  only. Python `datetime` and `time` values cannot be written in an expression.
* `Time` fields cannot be read at all.
* `Link` fields compare with another `Link` field or `None`, not with an ID number.
  On a model with a row scope for the caller's role, they are refused (see
  Permissions).

Empty values follow finite-v1 exactly:

* `==` and `!=` are always decided. `F("status") == None` is true for an empty status,
  and `F("status") != "issued"` is true for an empty status.
* An ordered comparison with an empty value is undecided, and so is a Boolean field
  that is empty. `~`, `&` and `|` with an undecided part are undecided. An undecided
  condition does not count the record. For example, with an empty total,
  `(F("total") > 5) | (F("status") == "paid")` does not count a paid record.

A number with more decimal places than the field keeps is compared exactly:
`F("total") > D("1.005")` counts `1.01` and not `1.00`.

## Declared measures

```python
from evlet import Count, Currency, Date, Link, Max, Model, Select, Sum
from evlet.calc import F


class Invoice(Model):
    status = Select(("draft", "issued", "paid"), default="draft")
    total = Currency(default="0")
    outstanding = Currency(default="0")

    class Meta:
        measures = {
            "outstanding": Sum("outstanding", where=F("status") == "issued"),
            "count": Count(),
            "payments": Count("payment", link="invoice"),
            "paid": Sum("amount", model="payment", link="invoice"),
            "last_payment": Max("paid_on", model="payment", link="invoice"),
        }


class Payment(Model):
    invoice = Link(Invoice)
    amount = Currency(default="0")
    paid_on = Date()
```

`Meta.measures` is a dictionary of one to eight named measures.

* A measure without `model=` is **model-level**: it covers many records of this model.
* A measure with `model=` and `link=` is **per record**: for one record of this model,
  it covers the rows of the related model whose Link field `link` points to it.
  `Count(Payment, link="invoice")` counts them; `Sum("amount", model=Payment,
  link="invoice")` adds their amounts. `model=` takes a model class or its registered
  name, so a model declared later can be named as text. The related model must be
  registered with a Link to this model. Its `where=` reads the related model's fields.

Declared measures are checked when the `Registry` is built. A field that does not
exist or has the wrong type, an unsupported `where=`, a bad name, or a link that does
not point back is a `ValueError` naming the model and measure.

Read them in Python:

```python
database.measures(Invoice)                       # {"outstanding": Decimal("110.00"), "count": 4}
database.measures(Invoice, filters={"status": "draft"})
database.measures(Invoice, 7)                    # {"payments": 3, "paid": Decimal("40.10"), "last_payment": date(2025, 3, 1)}
context.measures(Invoice)                        # the same, with permissions
```

A record with no related rows gives `0` counts, zero sums and `None` minimums and
maximums. A missing record is `NotFoundError` (`database`) or 404 (`context`).

## HTTP

All three routes are `GET` only and need a signed-in user who can read the model.

### GET /api/{model}/aggregate

```
GET /api/invoice/aggregate?measures=amount:sum:total,count:count,outstanding&group_by=status,posting_date:month&filters={"customer":4}
```

* `measures` (required): comma-separated entries. `name:function:field` asks for a
  function (`count`, `count_distinct`, `sum`, `min`, `max`) over a field; `count` takes
  no field (`n:count`). A bare `name` is a declared model-level measure, under its own
  name. One to eight entries with distinct names.
* `group_by` (optional): comma-separated `field` or `field:day`, `field:month`,
  `field:year`, up to four.
* `filters` (optional): a JSON object in the list filter syntax.
* `q` (optional): the list's search text, matched like the list's search box against the
  model's search fields the caller may read (at most 200 characters).

```json
{"group_by": ["status", "posting_date__month"],
 "measures": ["amount", "count", "outstanding"],
 "items": [{"status": "issued", "posting_date__month": "2025-01", "amount": "150.00", "count": 2, "outstanding": "110.00"}]}
```

Counts are integers. Sums, minimums and maximums use their field's JSON format
(Currency `"150.00"`, dates `"2025-01-10"`, Duration `"PT1H"`). Day keys are dates,
month keys are `"YYYY-MM"` and year keys are integers. Empty values are `null`.

### GET /api/{model}/measures

The declared model-level measures the caller may read, for a list page. It takes the
same optional `filters` and `q` (search) as the list, so totals follow the list's filters and
its search box.

```json
{"measures": {"outstanding": "110.00", "count": 4}}
```

### GET /api/{model}/{id}/measures

The declared per-record measures the caller may read, for one record. It takes no
parameters. The record must be readable by the caller, otherwise 404.

```json
{"measures": {"payments": 3, "paid": "40.10", "last_payment": "2025-03-01"}}
```

### Errors

| Status | When |
|---|---|
| 401 | Not signed in |
| 403 `Measure field is unavailable` | A measure field, `where=` input or group field the caller cannot read, or a declared measure name the caller cannot use, whether or not it exists; any of those that is a Link to a model with a row scope for the caller |
| 403 `Query field is unavailable` | A filter on a field the caller cannot read, as for lists |
| 404 | Unknown model, child table model, or a record the caller cannot read |
| 405 | Another method on these routes |
| 422 | Malformed or repeated parameters, unknown functions, wrong field types, more than eight measures, four group fields or 500 groups, a final sum out of range |

## Permissions

* **Row scope.** Every aggregate and declared measure counts only the records the
  caller may read. A per-record measure also applies the caller's row scope on the
  related model, so an editor who sees only their own payments counts only those.
* **Field permissions.** A measure is available only when the caller can read its
  field, every field its `where=` reads and, for a per-record measure, the Link field
  on the related model. Ad hoc requests for anything else are rejected with 403.
  Declared measures the caller cannot read are left out of `/measures` responses and
  of the schema, rather than rejected.
* **Links to row-scoped models.** A Link field whose target model has a row scope for
  the caller cannot be grouped, counted (`CountDistinct`) or read by `where=`, because
  the results would reveal linked records the caller may not see (lists show such
  links as empty). Declared measures that use one are left out for that role. The
  Link of a per-record measure is exempt: it only matches the one record the caller
  has been allowed to read. Links to models the caller cannot read are not readable
  fields, as elsewhere.
* **Uniform errors.** Field and declared measure names are checked before anything is
  compiled, so an unknown name and a name the caller may not use give the same 403 and
  message, through HTTP and through `Context`. Shape errors (bad syntax, too many
  measures or group fields) are checked first and give 422.
* Filters follow the list rules (`Policy.query_fields`). They are checked even when no
  declared measure is available to the caller.

## Schema

`/api/schema` adds `measures` to a model that declares them, listing the measures the
caller may read:

```json
{"name": "outstanding", "function": "sum", "field": "outstanding", "per_record": false, "model": null, "link": null,
 "where": {"$expr": "eq", "args": [{"$expr": "ref", "scope": "self", "field": "status"},
                                   {"$expr": "literal", "type": "text", "value": "issued"}]}}
```

`where` is the finite-v1 expression IR, or `null`. Per-record entries name the related
model (`model`) and its Link field (`link`).

## Limits and notes

* At most eight measures per request or per `Meta.measures`, four group fields and 500
  groups.
* Text minimums and maximums are not offered, because SQLite and PostgreSQL order text
  differently. Group order is decided in Python for the same reason.
* A per-record request reads the record (one statement for the row scope check and one
  for the record) and then runs one aggregate statement for each related model and link
  pair it declares. There is no batched form for many records yet.
* Measures read saved values. Draft previews and unsaved rows are not included.
* Large integer counts and Integer sums are JSON numbers; values above 2^53 lose
  precision in JavaScript, as for Integer fields elsewhere.
