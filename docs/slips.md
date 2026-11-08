# Slips

A slip is a form you open from code. It has the fields of a model: every field type, child
tables, calculations, conditions and link filters. It never saves itself. When the person hands
it in, the values either go to the slip's `hand_in()` method, or become a record of another model
through that model's normal create path.

| Term | Meaning |
| --- | --- |
| Slip | The Python definition (`class X(Slip)`) and the form it opens as |
| Present | Open a slip, with an optional source record and starting values |
| Fill | The person edits; the server checks each change |
| Hand in | The main button: full validation, then the outcome, in one transaction |
| Set aside | Cancel; nothing is sent. It asks first when something was filled in |
| Outcome | What a hand-in produces: a handler result, or a record |
| Becomes | The model a slip turns into |
| Source | The record a slip was presented from, if any |

## Define a slip

```python
from evlet import Child, Currency, Date, Decimal, Link, Outcome, Select, Slip, Table, Text, ValidationError

class Task(Child):
    title = Text(required=True)
    hours = Decimal(minimum="0", default="0")

class StartJob(Slip):
    title = "Start a job"
    hand_in_label = "Start job"
    source = Client                    # a button on each client record
    roles = ("admin", "editor")        # the default
    layout = (("Job", ("name", "client", "starts_on", "billing", "rate")), ("Tasks", ("tasks",)))
    focus = "name"                     # the cursor starts in the job name

    name = Text(required=True, max_length=60)
    client = Link(Client, read_only=True)
    starts_on = Date(required=True)
    billing = Select(["fixed", "time"], default="fixed")
    rate = Currency(visible_if="billing == 'time'", required_if="billing == 'time'")
    tasks = Table(Task, required=True)
    total_hours = Decimal(compute="sum(tasks.hours)", stored=False)

    def load(self, source):            # starting values, before the person's input
        self.client = source.id

    def validate(self):                # rules across fields
        if self.starts_on.year < 2026:
            raise ValidationError({"starts_on": "Pick a date from 2026 on"})

    def hand_in(self):                 # runs once, in one transaction
        job = self.context.create(Job, title=self.name, client=self.client,
                                  children={"steps": [{"title": t.title} for t in self.tasks]})
        return Outcome(f"Job {job['id']} started", open=(Job, job["id"]))

app = Application(database, auth=auth, slips=[StartJob])
```

Class options:

| Option | Default | Meaning |
| --- | --- | --- |
| `title` | the class name | The form's title and the button's label |
| `hand_in_label` | `title` | The main button |
| `source` | none | The model the slip is presented from. Its records get a button |
| `roles` | `("admin", "editor")` | Roles that may present it |
| `permission` | `(source, "update")` | `(Model, operation)` the caller needs, with a source |
| `menu` | `False` | List the slip under New. Only without a source |
| `layout` | none | `(label, field names)` groups. Fields left out follow at the end |
| `becomes` | none | The model it turns into, instead of `hand_in()` |
| `review` | `True` | With `becomes`: open the new record to review rather than create it |
| `focus` | none | The field the form puts the cursor in. Without it, the first editable field |

These names, plus `load`, `validate`, `hand_in` and `document`, cannot be field names on a slip.
`focus` names a visible field people fill in, as `Meta.focus` does on a model
([ui.md](ui.md#where-the-cursor-starts)): not hidden, read only, calculated, a table or a `Sequence`.
A wrong one fails when the application starts, for example `StartJob.focus: client is read only; name a
field people fill in`. The surface carries it as `"focus"` only when it is set.
`source` is the source record inside the slip (`self.source`), not a field. Every slip defines
`hand_in()` or sets `becomes`, not both. Definitions are checked when the application starts.

## Outcomes

`hand_in()` returns an `Outcome`, `present(...)`, a dict or `None`:

```python
Outcome("Visit planned", open=(Visit, 7), data={"hours": "5.5"}, present=present(NextSlip, source=(Client, 3)))
```

* `message` shows as a toast.
* `open` opens that record.
* `data` is returned to browser code that presented the slip.
* `present` opens the next slip.

The result is at most 64 KiB of JSON. The handler runs as trusted code with `self.context`, a
document context bound to the caller. Writes go through `context.create`, `update`, `action`
and the other context operations, with the caller's permissions. A role that cannot create a Job
cannot create one through a slip.

### Becomes a record

```python
class QuickInvoice(Slip):
    title = "Quick invoice"
    menu = True
    becomes = Invoice

    customer = Link(Customer, required=True)
    reference = Text(required=True)
    hours = Decimal(required=True)
    rate = Currency(default="350.00")

    def document(self, source):        # optional
        return {"customer": self.customer, "reference": self.reference, "total": self.hours * self.rate}
```

Without `document()`, fields and child tables with the same names copy over. With
`review = True` (the default) the server validates the slip, maps it and returns the values.
The browser then opens the model's new record screen filled with them, with a note saying where
they came from. Nothing exists until the person selects Create, which is the normal create
route. With `review = False` the record is created on hand-in, in the same transaction. A
declarative model goes through `context.create`; any other model goes through the same checks as
`POST /api/{model}`. Either way the caller needs create permission on the model. Mapped values
the role cannot write are refused with 403, never dropped.

## Present a slip

* **From a record.** A slip with `source = Model` is a button on that model's records.
  `/app/client/01hc1sd9vc1ye?slip=start_job` opens it from a link.
* **From the New menu.** `menu = True`.
* **From an action.** A model action may return `present(...)`. The record view opens the slip
  after the action:

  ```python
  @action("Plan job")
  def plan_job(self):
      return present(StartJob, source=self, values={"name": self.title + " job"})
  ```

* **From another slip.** `hand_in()` returns `present(...)` or `Outcome(present=...)`.
* **From browser code.**

  ```ts
  import { presentSlip } from './app/slips';
  const outcome = await presentSlip('start_job', { source: { model: 'client', id: 4 }, values: { billing: 'time' } });
  // null when set aside
  ```

Starting values given to `present()` or `presentSlip()` are input like anything the person types:
a read-only or calculated field cannot be filled this way. Use `load()` for those.

## Routes

All three take JSON and need a signed-in session.

| Route | Body | Answer |
| --- | --- | --- |
| `POST /api/_slips/{name}/present` | `{source?, values?}` | The surface below |
| `POST /api/_slips/{name}/check` | `{source?, values, revision?}` | The surface, with `revision` echoed |
| `POST /api/_slips/{name}/hand-in` | `{source?, values, key}` | The outcome |

`source` is `{"model": "client", "id": "01hc1sd9vc1ye"}`. `values` carries scalar fields by name and table rows
under `children: {table: [rows]}`. Each row may carry a `_key`, which names it in errors such as
`tasks.row-2.title`. `key` is an idempotency key of 8 to 128 characters: a retry with the same
key and body returns the first outcome without running again; the same key with other input is
409. A hand-in with a source needs `If-Match` with the source revision from the surface. A stale
revision is refused, so a slip filled against an old record does not run. Validation errors are
422 with `fields`, like other routes.

The surface:

```json
{
  "slip": "start_job", "version": 1, "label": "Start a job", "hand_in_label": "Start job",
  "source": {"model": "client", "id": "01hc1sd9vc1ye", "title": "Acme", "revision": "\"...\""},
  "outcome": {"kind": "handler"},
  "layout": [{"label": "Job", "fields": ["name", "client", "starts_on", "billing", "rate"]}],
  "focus": "name",
  "definition": {"fields": [], "children": [{"name": "tasks", "label": "Tasks", "allow_add": true, "definition": {}}]},
  "values": {"client": 4, "billing": "fixed", "children": {"tasks": []}},
  "rules": {"rate": {"visible": false, "required": false}},
  "states": {"name": "required-input"},
  "errors": {}
}
```

`outcome` is `{"kind": "document", "model": "invoice", "review": true}` for a slip that becomes a
record. Calculated fields carry `calculated: true`. `rules` gives the visible, required and read
only state of every field with a condition. `allow_add` is false for a table that `load()`
filled. The model schema lists the slips a role may present: per model under `slips`, and the
menu slips at the top level.

## What the server enforces

* **Roles.** A role outside `roles` gets 403, and the slip is not in its schema.
* **Source.** The caller must be able to read the source record and hold `permission` (update on
  the source model by default). The source must be a record of the declared model.
* **Becomes.** Create permission on the target model, checked when presenting and again on
  create. Without it the slip is not listed.
* **Fields.** Unknown names are 422. Read-only and calculated fields cannot be supplied (403).
  Links must name records the caller can read and match their filters. Tables take at most 100
  rows.
* **Conditions never relax validation.** `required_if` is decided on the server. A hidden field is
  still cleaned.
* **Nothing persists.** A slip has no table. The only write before an outcome is the idempotency
  record in `evlet_operations`.

## In the browser

`frontend/src/evlet/app/slips.ts` renders the surface as a modal with the existing field
controls. Each change is checked with `/check` and an older answer never overwrites a newer
change. Hand-in errors appear on their fields and rows. The outcome is followed: a message, a
record to open, the next slip, or the new record screen filled for review.

The Playground has three slips (`examples/playground/slips.py`): Plan a project on each customer
(becomes a project with its tasks), Estimate a project in New (a handler that calculates and
then presents Plan a project), and Quick invoice in New (becomes an invoice for review).

The older record-bound form class in `evlet.forms` keeps its routes for existing applications.
New code uses Slip.
