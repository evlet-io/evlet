# Prompts and bound records

**An action is the button; a Prompt is the form it opens; the prompt's `submit()` is the callback.** A prompt
is never shown any other way. Python code reads records through its context as bound records, whose actions
and state rules are methods.

| Term | Meaning |
| --- | --- |
| Prompt | `class X(Prompt)`: a form without storage, shown when an action (or `submit()`, or an `@api` function) returns it |
| Source | The record the presenting action ran on (`self.source`, a bound record); `None` for an `@api` prompt |
| Ticket | The single-use key of a prompt shown over HTTP; check and submit go through it |
| Outcome | What an action or `submit()` returns: a message, a record to open, a new record to review, data, or the next prompt |
| Bound record | What `context.get`, `context.create` and `context.list` return: values as attributes, actions and rules as methods |

## Define a prompt

```python
from evlet import Child, Currency, Date, Decimal, Main, Model, Outcome, Prompt, Table, Text, ValidationError, action, validate
from evlet.calc import F, today

class ConfirmQuotation(Prompt):
    order_date = Date(required=True, initial=today())
    customer_po = Text(max_length=60)

    class Line(Child):
        quotation_line = Text(read_only=True, hidden=True)
        item = Text(read_only=True)
        open_qty = Decimal(scale=2, read_only=True)
        confirm_qty = Decimal(scale=2, minimum=0)
        rate = Currency(read_only=True)
        amount = Currency(compute=F('confirm_qty') * F('rate'))       # finite-v1, as on documents

        @validate
        def within_open(self):
            if (self.confirm_qty or 0) > self.open_qty:
                raise ValidationError({'confirm_qty': 'More than is still open'})

    lines = Table(Line)
    total = Currency(compute=lines.sum('amount'))

    class Meta:
        title = 'Confirm quotation'
        primary = 'Create sales order'                                # the main button
        layout = (Main(('order_date', 'customer_po', 'total')), Main('lines'))
        focus = 'customer_po'

    def load(self, quotation):                                        # starting values; also self.source
        self.lines = [{'quotation_line': str(line.id), 'item': line.item, 'open_qty': line.qty - line.confirmed_qty,
                       'confirm_qty': line.qty - line.confirmed_qty, 'rate': line.rate}
                      for line in quotation.lines if line.qty > line.confirmed_qty]

    def submit(self):                                                 # the callback, one transaction
        order = self.source.create_order(self.lines, self.order_date, self.customer_po)
        return Outcome(open=order)

class Quotation(Model):
    @action('Confirm', available_when=(F('docstate') == 'submitted') | (F('docstate') == 'partial'))
    def confirm(self):
        return ConfirmQuotation             # or ConfirmQuotation(customer_po='...') with starting values
```

A prompt has what a model has, without a table, migrations or a place in the registry: every field type,
`Child` Tables, finite-v1 formulas and field rules (`compute`, `initial`, `applies_when`, `required_when`,
`locked_when`), the string rules, Link filters (`Ref('source.company')` reads the source), and `@validate` on
the prompt and its rows. `Meta` takes `title` (the class name by default), `primary` (`Submit`), `layout`
(`Main`, `Section`, `TableSection`, as on a model; [ui.md](ui.md)), `focus`, and `script` and `on_open` for a
[model script](model-scripts.md#in-a-prompt) (the Playground's Plan project marks a long task with one). A wrong layout or focus fails
when the prompt is first shown. `load(self, source)` fills it before the person sees it; read-only fields are
filled there. A Table that `load()` fills takes no new rows.

## Shown when returned

An `@action`, a prompt's `submit()` or an `@api` function returns the Prompt class, or an instance with
starting values. An action that returns a prompt is checked like any action (roles, `available_when`,
permission, row scope, revision) and **writes nothing**: what it changes on its record is not saved. The
prompt's `submit()` writes.

A prompt without a record (a setup wizard, a quick expense) comes from an `@api` function; its source is
`None`. A workspace shortcut opens it: `Shortcut(api='app.quick_invoice', label='Quick invoice')`
([ui.md](ui.md#workspaces)).

```python
@api(permission=(Invoice, 'create'))
def quick_invoice(context):
    return QuickInvoice
```

## Action inputs and move forms

`@action('Rename', inputs={'new_name': Text(required=True)})` stays as a shorthand for a prompt. Over HTTP, the
action sent without its inputs answers an anonymous prompt with those fields (titled and labelled with the
action's label), and that prompt's `submit()` runs the action with them, with every check. Sent with its inputs
(`{"new_name": "..."}`), the action runs at once, as does `record.rename(new_name=...)` in Python. A move's
remark and the fields it asks for, and an action's typed arguments, open in the same modal
(`openForm` in `prompts.ts`); `ActionDialog` is gone.

## Outcome

`Outcome` is the one result type of actions and prompts:

```python
Outcome('Order created', open=order)                   # a message, and a record to open: a bound record or (Model, id)
Outcome(new=(Invoice, {'customer': 4, 'total': 70}))   # a new invoice filled with these values, to review and create
Outcome(data={'estimate': '1200.00'})                  # data for browser code
return NextStep                                        # another Prompt: a wizard
```

The JSON answer is at most 64 KiB. `submit()` may also return `None` (the screen says it is done) or any other JSON value (a dict, a list, a number or a string), which the screen receives as data.

## Over HTTP

Returning a prompt from an action answers the record as usual, with the prompt under `_outcome`:

```json
{"id": "01kfs…", "...": "...", "_outcome": {"prompt": {
  "ticket": "6N0f…", "name": "_prompt.confirm_quotation", "title": "Confirm quotation", "primary": "Create sales order",
  "source": {"model": "quotation", "id": "01kfs…", "title": "Q-0001"},
  "models": [{"name": "_prompt.confirm_quotation", "fields": [], "layout": [], "record_layout": {"blocks": []},
              "children": [{"name": "lines", "model": "_prompt.confirm_quotation.lines"}], "calculation": {"plan": {}}},
             {"name": "_prompt.confirm_quotation.lines", "fields": []}],
  "values": {"order_date": "2026-11-16", "children": {"lines": [{"_key": "3f9a…", "confirm_qty": "2.00"}]}},
  "errors": {}, "states": {}}}}
```

`models` are the prompt and its rows in the shape of `/api/schema` models, with the finite plan, so the browser
runs the same record engine on them. An `@api` endpoint that returns a prompt answers `{"prompt": {...}}`.

The ticket is single use and the server's own: it records the prompt, the source and its revision, the
presenting action (or endpoint), the user and an expiry (one hour), in `evlet_prompt_tickets` on the main
database file. Two routes take it:

| Route | Body | Answer |
| --- | --- | --- |
| `POST /api/_prompts/{ticket}/check` | `{"values": {...}}` | `{values, errors, states}`: what only the server can decide (Link choices, validators, string computes) |
| `POST /api/_prompts/{ticket}/submit` | `{"values": {...}}` | The outcome; the next prompt as `{"prompt": {...}}` |

`values` carries the fields people fill in by name and rows under `children: {table: [rows]}`, each row with
the `_key` the surface gave it. Read-only and calculated fields cannot be sent (403); unknown ones are 422.

Submit runs in one transaction: it checks the presenting action again on the source as it is now (roles,
`available_when`, permission, row scope) and the source revision (409 when it changed), settles and fully
validates the prompt (422 with `fields`), runs `submit()`, and marks the ticket used. The ticket is the
idempotency key: a retry of a completed submit answers the first result without running again. A used ticket
cannot be checked (409); an expired one is 410; another user's or an unknown one is 404.

## In the browser

`frontend/src/evlet/app/prompts.ts` opens a prompt as a modal built from the record engine: a `RecordStore`
over the prompt's models (its finite session calculates as people type), the layout's sections, the Table grid
and the field controls. It calls the server only to submit, and to check when the prompt uses string computes
the browser cannot run. It then follows the outcome: a toast, a record to open, the new record screen filled for
review, or the next prompt.

## Bound records

```python
customer = self.context.get(Customer, customer_id)        # or self.context.get('customer', customer_id)
if customer.docstate == 'lead':
    customer.convert({'credit_days': 30})                 # an action; values answer its prompt, no prompt step
customer.qualify(remark='Met at the event')               # a state rule by its key: customer.move('qualify')
customer.phone = '+971 50 000 0000'
customer.save()                                           # one update with the changed fields
customer.delete()
```

`context.get`, `context.create` and each row of `context.list(...)['rows']` return a bound record: field values
as attributes (Python values: `Decimal`, `date`, `RecordId`), Table rows as tuples of read-only rows, bound to
the context that read it and to its revision. `record['field']` and `record.get('field')` give the value as the
record's JSON detail carries it, like a report row.

* **Actions are methods.** `record.<action>(values=None, **values)` runs `context.action(...)` with every check.
  The values fill the action's `inputs` (or its typed arguments). When the action returns a prompt instead, the
  values fill the prompt (`load()` fills the rest), the prompt is validated (a missing required value raises
  `ValidationError` keyed by field) and its `submit()` runs in the same transaction; no ticket is needed, since
  this is trusted Python. It returns what the action or `submit()` returned. Called with no values, an action
  that returns a prompt returns the loaded prompt; set its fields and call `prompt.submit()`.
* **State rules are methods**, by key: `record.qualify(remark=None, **asked)` is `record.move('qualify', ...)`,
  with the move's checks, hooks and approvals (an approval rule approves the waiting level). System rules
  (`by=()`) are callable from trusted code and are never buttons. A rule added through Controls has no method;
  `record.move(key)` works for every rule. Declare explicit rule keys (`key='submit'`); there are no generic
  submit or cancel helpers.
* **`record.save()`** writes the attributes changed since the read in one `context.update`; a record that
  changed since the read raises a conflict. **`record.delete()`**, **`record.reload()`**. Afterwards the record
  holds the saved values. Plain model methods are methods too, with `self` the bound record.
* **Inside its own write** (an action or hook whose `self` is that record) another action on `self` runs its
  checks and body on the same draft.
* **One namespace.** A model's field names, action names, rule keys and `save`, `delete`, `move`, `reload` and
  `get` must be distinct; a clash fails when the class is created, naming both.
* **Where writes run:** hooks, actions, prompt `submit()`, `@api`, `@cron`, jobs and scripts. A read-only context
  refuses them. Reads run as the context's user (row scopes, field rules).
* **Trusted code writes read-only fields.** `context.update` and `record.save()` from Python may set a field
  declared `read_only=True` (a quotation line's `confirmed_qty`, say) that the role may otherwise write. HTTP
  callers never can.
* A record built by hand or read through a raw `Session` raises `Get the record through a context to run its
  actions`.

`context.action`, `move`, `update` and `delete(Model, id, ...)` stay as the underlying calls.

## The demo: Quotation and Customer Order

`tests/test_prompts.py` builds the flow end to end. A **Quotation** (lines with `qty`, `rate`, `amount`, a
`confirmed_qty` that is `versioned=False` and read only, and a virtual `open_qty`) moves through draft,
revision, submitted, partial, confirmed, terminated and cancelled. Its system rules `part`, `full`,
`reopen_part` and `reopen_all` (`by=()`) are never buttons. **Confirm** returns `ConfirmQuotation`; its
`submit()` calls the model method `create_order(...)`, which creates a **Customer Order** with the confirmed
lines, moves it to submitted (`order.submit()`), adds the quantities to the quotation's `confirmed_qty` and
moves the quotation by `full` or `part`. Cancelling a submitted order (`@release.exit`) gives the quantities
back and moves the quotation by `reopen_part` or `reopen_all`. In Python: `quotation.confirm({'order_date':
...})`.

The Playground shows **Plan project** (an action on a planned project) and **Quick invoice** (an `@api` prompt
in the Sales workspace that opens a new invoice to review; its lines show the rate's help text
`'per {uom}'` as subtext under each rate), in `examples/playground/prompts.py`.
