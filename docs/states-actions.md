# States, rules and model actions

A `State` field holds where a document is in its life, for example draft, issued or paid. Its
steps are declared in order, and its value changes only through declared rules. Some steps lock
the document against ordinary edits. Hooks run code when a document is saved, enters a state or
leaves one. Model actions are declared methods that may take validated inputs and may be
available only in some states.

The state manager is `evlet/statemanager.py`. Rows a document posts to other models when it
enters a state, and their reversal when it leaves, are described in [effects.md](effects.md).
Each move is recorded in the record's [Journal](journal.md). Confirm, cancel, reopen and void
are modelled as steps and rules ([document-lifecycle.md](document-lifecycle.md)).

## Declaring a State

```python
from evlet import Child, Currency, Date, Decimal, Model, State, Table, Text, ValidationError, action, state, validate
from evlet.calc import F


class Invoice(Model):
    class Line(Child):
        qty = Decimal(default=1)
        rate = Currency(default=0)
        amount = Currency(compute=F("qty") * F("rate"))
        note = Text(default="", versioned=False)

    lines = Table(Line)
    total = Currency(compute=lines.sum("amount"))
    remarks = Text(default="", versioned=False)
    status = State(
        State.Step("draft", "Draft"),
        State.Step("issued", "Issued", color="blue", lock=True, release=True),
        State.Step("paid", "Paid", color="green", lock=True, final=True),
        State.Step("void", "Void", color="rose", lock=True, final=True),
        rules=(
            State.Rule("draft", "issued", "Issue", key="issue", when=F("total") > 0, primary=True),
            State.Rule("issued", "paid", "Mark paid", key="pay", by=("admin",)),
            State.Rule("issued", "void", "Void", key="void", by=("admin",), remark="required"),
        ),
        show="bar",
    )

    @validate
    def has_lines(self):
        if self.status != "draft" and not self.lines:
            raise ValidationError({"lines": "Add a line"})

    @state("issued")
    def on_issue(self):
        self.remarks = "Issued"

    @action("Record payment", inputs={"amount": Currency(required=True), "paid_on": Date(required=True)},
            available_when=F("status") == "issued")
    def record_payment(self, amount, paid_on):
        self.remarks = f"Paid {amount} on {paid_on}"
```

`State(*steps, rules=(), default=..., show="badge")`:

* It is a `Select` over the step keys. Its schema type is `select`, it is read only in forms,
  and finite expressions read it like any other Select (`F("status") == "issued"`).
* `default` is the first step when left out. A new record always starts in it. It cannot be
  final.
* A model has at most one State, and only root documents have one (not `Table` rows).
* It also takes `label`, `help_text`, `tooltip`, `placeholder`, `hidden`, `index`,
  `max_length`, `panel`, `copy` and `column`. It takes no rules of the field kind,
  calculations or validators.
* `show` is how the record page draws it: `"badge"`, `"bar"` or `"none"`
  ([below](#how-a-state-and-a-status-show)).

`State.Step(key, label=None, *, color=None, lock=False, release=False, final=False)`:

* `key` is a lowercase name (`draft`, `in_review`). `label` defaults to the key, humanized.
* `color` is one of `blue`, `green`, `amber`, `rose`, `violet`, `teal` and `slate`. A step
  without one is neutral; the UI never guesses a colour from the name.
* `lock=True` makes the document read only to ordinary writes in this step, except fields
  declared `versioned=False` ([below](#ordinary-writes-and-locks)).
* `release=True` marks a step whose content counts as released. An [effect](effects.md)'s `on`
  must be such a step. Release records, versions and the `@release` hook arrive in later 0.4.6
  pull requests.
* `final=True` means no rule leaves the step.

`State.Rule(from_, to, label=None, *, key=None, by=None, when=None, remark=None, ask=(), primary=False, reject=False)`:

* Write the states positionally: `State.Rule("draft", "issued", "Issue")`. `from_` is one
  state or a tuple of states. `label` defaults to the target, humanized.
* `key` is the rule's stable id, used in the route, the Journal and the schema. It defaults to
  `<from>_to_<to>`, with tuple sources joined by `_` (`draft_issued_to_void`). Two rules with
  the same key fail at declaration; give one its own `key=`.
* `by` names who may use the rule: role names, built-in or custom, and `F("field")` user fields
  that hold a user id or username on the saved record (`by=("manager", F("approver"))`). It
  defaults to `("admin", "editor")`. The caller also needs update permission on the record
  within its row scope. `by=()` makes a rule only the application uses
  ([System moves](#system-moves)).
* `when` is a finite-v1 Boolean expression, the same language as `locked_when`, decided on
  the record as saved, before the move. A guard that is pending (an input is empty) or fails
  blocks the move.
* `remark` is `None` (the move takes no remark), `"optional"`, `"required"`, or
  `State.Remark("optional", label="Why?", required_when=F("total") > 1000)`. `label` is what
  the move dialog asks; `required_when` is a finite Boolean on the saved record that makes an
  optional remark required.
* `ask` adds fields the move dialog asks for to those it asks for anyway
  ([Values a move asks for](#values-a-move-asks-for)).
* `primary=True` marks the main button. Without one, screens use the first allowed rule.
* `reject=True` marks a rule back from an approval. Approval levels (`levels=`,
  `State.Level`, `self_approve=`) come in 0.4.6, PR 2.

`State.Rule`, `State.Step` and `State.Remark` live on `State` because `evlet.Rule` is the
permission rule.

Declaration fails when a rule names an unknown state, leaves a final state, or does not change
the state, and when a hook or `ask` names an unknown state or field.

### System moves

Some state changes are made by the application, never by a person: a background job starts
and finishes a build, or an action on one record closes another. Declare them with `by=()`:

```python
class PriceList(Model):
    status = State(
        State.Step("draft"), State.Step("building"), State.Step("ready"), State.Step("failed"),
        rules=(
            State.Rule("draft", "building", "Start", key="building", by=()),
            State.Rule("building", "ready", "Finish", key="ready", by=()),
            State.Rule("building", "failed", "Fail", key="failed", by=()),
            State.Rule(("ready", "failed"), "draft", "Reopen", key="draft"),
        ), show="bar")
```

* It is never offered: it is left out of `_moves`, of the schema's `state.rules` and of the
  list's bulk Move, so no button, badge menu or stage bar step offers it.
* Every HTTP caller is refused it with 403 `Start is made by the application only`: a signed
  in person (an admin too), an API key, an AI assistant through MCP, the local connector and
  Eva. A role that cannot read the State gets the usual 409 `This record has no such move now`.
* Trusted Python makes it with `context.move(Model, id, key)`: a job through
  [`job.business(tx)`](jobs-realtime.md#a-jobs-business-context), an `@action` handler or a
  hook through `self.context.move(...)` on another record, and `business.context(user)` in a
  script. Only the `by` check is left out. Everything else applies: update permission and row
  scope of the context's user, the guard, the hooks, calculations, validation, a closed period,
  the Journal's `moved` entry and the one transaction.

The [Background Playground](jobs-realtime.md#background-playground) declares these rules.

## How a state and a status show

Not every State is a pipeline, and not every status is set by hand. `show` and `Meta.status`
say how the record page and lists draw them.

* `show="badge"` (the default) draws the state as a coloured badge in the record header, with
  the key facts. The moves stay as buttons in the header actions.
* `show="bar"` draws the stage bar under the header, every step in order, for pipelines such
  as draft, submitted, approved.
* `show="none"` draws neither, for an app that places the state itself. The moves stay as
  header buttons.
* Lists and headers read a step's look from the step alone: a final step coloured `rose` reads
  as cancelled (struck-through title), any other final or locked step shows a lock, and the
  default step shows as a draft.

Display never implies that a person can change the state:

* Clicking the badge opens a menu of the moves this person may take now, and only those. A
  step of the bar is a button only when this person may move to that state now. Everything else
  is plain text: states only the system reaches, moves for other roles, and moves whose
  condition is not met. The header buttons still list those moves and say why each is blocked.
* "May take now" is `allowed` in `_moves` (below), so the server decides it: the rule's `by`,
  update permission and the row scope, the rule's condition, `Meta.api = "read"` and a closed
  period (`Meta.period`).
* The badge's accessible name is `Status: Active` (and `Status: Draft. Change status` while it
  offers moves). The bar is a list named after the model (`Invoice status`); the current step
  has `aria-current="step"`.

### Meta.status

`Meta.status` names a Select or a State to show as the record's status badge. Use it for a
status the system sets, such as an invoice's payment status calculated from what was paid:

```python
class Invoice(Model):
    class Meta:
        status = "payment_status"

    total = Currency(required=True)
    amount_paid = Currency(default="0.00")
    payment_status = Select(["unpaid", "partly_paid", "paid"], label="Payment status",
                            colors={"unpaid": "rose", "partly_paid": "amber", "paid": "green"},
                            compute="'paid' if amount_paid >= total else ('partly_paid' if amount_paid > 0 else 'unpaid')")
```

* The field must be a visible Select or State of a root model; anything else fails at
  registration (`Meta.status names a visible Select or State field of the model`). The schema
  carries `status` (the field name) only for roles that can read the field.
* The badge is never a button. A calculated or read-only Select cannot be edited anywhere. A
  Select people may write still shows as a badge; they change it in the form, not from the badge.
* When `Meta.status` names the model's State, the State's badge shows in the header whatever its
  `show`, with its moves. When it names another field, both badges show, the State's first (if
  its `show` is `"badge"`).
* Lists draw the State's column and the `Meta.status` column as the same badges.
* `Select(..., colors={...})` maps some choices to a badge colour from the same list as steps.
  Unknown choices or colours fail when the field is declared. The schema carries `colors` on the
  field.

## Hooks

Four decorators run code on a document. Hooks of one kind run in class order; a subclass
replaces a base's hook by defining a method of the same name. Each is a method that takes only
`self`.

| Hook | Runs | `self` |
| --- | --- | --- |
| `@validate` | every save (create, update, move) and every draft preview, on documents, `Table` rows and forms (Dialog, Slip, Report filters) | read only; assigning raises |
| `@state("a", "b")` | when a move enters one of the states | the draft; `self.context.session` is open |
| `@state.exit("a")` | when a move leaves one of the states | the draft; `self.context.session` is open |
| `@release` | when a release is made | declared now; runs from 0.4.6, PR 3 |

* Inside a hook, `self.is_new` is true on create, and `self.before` is the document as last
  saved, read only (`None` on create). `self.context` is the operation's context.
* `@validate` checks; it does not change values. Raise `ValidationError({"field": "message"})`;
  a plain `ValueError` becomes a `_form` error. A row's errors are keyed under its row. It runs
  last in the settle, after calculations, so it sees the final values. A record built outside
  the document pipeline (a `Session` write) runs its `@validate` checks too.
* `@state` and `@state.exit` are trusted code, like an action: they change the record through
  `self` and may write other rows through `self.context.session`
  ([below](#the-transaction-session-in-hooks-and-actions)). A `ValidationError` or `ValueError`
  refuses the move with 422 and rolls everything back. A new record starting in the default
  state runs no `@state` hook.
* A model that still declares `before_close`, `changed`, `check_lifecycle` or an undecorated
  `validate()`, or sets `confirmable` or `track_changes`, fails when the class is created with a
  message pointing here.

The order of a move:

1. The rule is checked on the saved record (below).
2. When the record leaves an effect's `on` state, its posted rows are reversed.
3. `@state.exit(old)` hooks run.
4. The state changes.
5. `@state(new)` hooks run.
6. The document settles: calculations, then `@validate`.
7. Document numbers due in the new state are assigned.
8. The record is written, effects on the new state post, and the Journal gets its `moved`
   entry with the rule key and the remark.

Everything commits in one transaction. A failure anywhere rolls all of it back, and no
Sequence number is used up.

| Removed | Use |
| --- | --- |
| `Move(..., run="m")` | `@state("to")` on `m` |
| `before_close(self, to)` | `@state.exit(...)` or `@validate` |
| `changed(self, change)` | `@state(...)`, or a job queued from a hook |
| `Model.validate()` | `@validate` |
| `check_lifecycle`, `@validate.confirm/.cancel` | `@validate` reading `self.status` and `self.before` |
| `@action.confirm/.cancel` | `@state("confirmed")`, `@state.exit("confirmed")` |

## Ordinary writes and locks

Ordinary writes are HTTP create, update and delete, `Context.create`, `Context.update`,
`Context.delete`, and dialogs and services that write through `Context`.

* Create may leave the State out or send its default. Any other value is a field error:
  `A new record starts as draft; use its rules to change it`.
* Update may not set the State: `This changes only through its rules`. Sending the saved
  value back is not a change and is accepted.
* Python code cannot assign the State on a document draft either, inside actions or hooks.
* While the saved state is a `lock=True` step, an ordinary write may not change the record or
  any of its Table rows at any depth. Adding, removing or reordering rows is a change. Each
  changed field gets the error `This record is issued, so it is locked`; a row collection that
  gained, lost or reordered rows gets it under the collection name.
* A field declared `versioned=False` stays editable by ordinary writes, on the record and on its
  rows: it says where the document is in its process, not what it says. Its own `locked_when`
  still applies. Calculated fields cannot be `versioned=False`.
* A locked record keeps its saved results. An ordinary write changes only the `versioned=False`
  fields; every calculated value, at every depth, keeps the value it had when the record was
  locked, even if a linked record or today's date changed since. Moves and actions settle
  normally and recalculate, unless the record has postings (below). So that this stays
  consistent, a stored result (at any depth) may not read a `versioned=False` field;
  registration fails with a message naming the field. This covers finite formulas, legacy
  string formulas, reads through virtual results, Query fields and `fetch="customer.name"`
  copies whose Link is `versioned=False`. A virtual result may read such a field.
* Rules and validation on such a write read the saved results too: `required_when`,
  `applies_when`, `locked_when`, `required_if` and validators see the values the record keeps.
  Fetched copies are not refreshed.
* `"_calculation": {"recalculate": true}` on a locked record is refused with 409.
* An ordinary write on a locked record does not recalculate, so it does not need the active
  formula version. A move or action on such a record recalculates, so it answers 409
  `SCHEMA_VERSION_MISMATCH`; send `{"_calculation": {"model_schema_hash": "<active hash>",
  "recalculate": true}}` with the move (or the action) to recalculate with the active formulas.
* A record with live [effect](effects.md) rows keeps its stored calculated values for every
  writer, moves and actions included, even after a linked record or a formula changed.
  `"recalculate": true` on it is refused with 409 `This record has postings. To change its
  totals, move it out of its posted state.` Actions, hooks and move values on it may not change
  anything a stored result reads (a source field, or the rows of a Table that feeds a stored
  result); such a change is refused with 409, for example `Quantity feeds posted totals. To
  change it, move the record out of its posted state.` Fields no stored result reads still
  change.
* The `Recompute` migration operation keeps the saved results of records with live postings,
  and of records that posted and are in a final state, unless an admin migrates with
  `evlet migrate <instance> --recompute-posted` ([finite-calculations.md](finite-calculations.md)).
* Deleting a record in a locked state is refused with 409, and so is deleting a record that
  ever posted: `This record has posted before, so it cannot be deleted. Move it to a final
  state instead.`
* Uploading or removing an attachment is an ordinary write: on a locked record it is refused
  with 409. Downloads still work.
* A role that cannot read the State field gets `This record is locked` (and `This record is
  locked and cannot be deleted`) without the state's name.
* Raw database writes (`Session.create` and `Session.update`, including legacy `Action`
  handlers) may create a record in the default state and may send the saved state back, but
  any other State change raises an error unless it runs inside `tx.maintenance()` for an
  audited repair. Use `context.move()` or a model action instead.

## Moves

`POST /api/{model}/{id}/moves/{key}` makes a move by its rule key. The body is a JSON object:
`{}`, or the remark and the values the move asks for. From Python,
`context.move(Invoice, record_id, "void", remark="Entered twice")` does the same.

```
POST /api/invoice/01hc1sd9vc1ye/moves/void   {"remark": "Entered twice"}
```

* It requires `If-Match` with the record's current ETag: 428 without it, 409 when stale.
* The checks run in this order. Update permission and row scope come first, as for any
  update (403 or 404). Then: an unknown key, 404 `Move not found`. A final current state: 409
  `This record is paid, which is final; it has no moves`. A rule that does not start from the
  current state: 409 `This record cannot move from draft to paid`. A caller `by` does not name:
  403 `Your role cannot use Mark paid`. A [system move](#system-moves): 403 `Start is made by
  the application only`. A guard that is not true: 409, for example `Issue is not available:
  the condition for this move is not met`. Then the remark: missing when required, 422
  `{"remark": "Void needs a remark"}`; sent to a rule without one, 422 `{"remark": "Issue takes
  no remark"}`. A remark is trimmed and has at most 1,000 characters.
* A role that cannot read the State field gets 409 `This record has no such move now` for
  every refused move, so it cannot tell which states and rules exist.
* The response is the permission-filtered detail with a new ETag.
* Moves and actions are declared, checked code. They may change a record in a locked state,
  but each field's `locked_when`, `applies_when`, `required_when`, validation and the
  permissions on what is returned still apply.

### Values a move asks for

A move may carry values for the fields its target state brings in. A move *asks* for a root
field when the field's `applies_when` or `required_when` reads the State, is false in a source
state of the rule, and is true (or depends on other values) in the target state, and for every
field the rule lists in `ask`. Only fields the role may write are asked for.

```python
lost_reason = Text(applies_when=F("stage") == "lost", required_when=F("stage") == "lost", versioned=False)
# POST /api/deal/01hc1sd9vc1ye/moves/lost  {"lost_reason": "Chose another vendor"}
context.move(Deal, deal_id, "lost", lost_reason="Chose another vendor")
```

* The values are applied with the move and checked by the same rules, validation and
  permissions in its transaction. A required value that is missing fails the move with a field
  error (422) and nothing changes.
* Any other key is refused with `This move does not take this value`: as a field error when the
  role can read the field, and as a form error (`_form`) for an unknown key or a field the role
  cannot read, so the answer never confirms a hidden field.
* The rule is checked first, so values never reveal which rules exist: an unknown key is 404
  with or without values.
* `_moves` and the schema's `state.rules` list the asked fields as `asks`. `_moves` also leaves
  out a field whose `locked_when` holds on the record.

## Model actions

```python
@action("Record payment", inputs={"amount": Currency(required=True)}, available_when=F("status") == "issued")
def record_payment(self, amount): ...
```

* `@action`, `@action("Label")` and `@action(label=..., roles=..., inputs=...,
  available_when=...)` are all accepted. Roles default to admin and editor.
* One rule decides how inputs are read. With `inputs`, each input is a Field instance
  (no calculations, rules or fetches). The values are cleaned by their fields and passed as
  keyword arguments; missing required inputs, wrong types, failed bounds and unknown names
  are field errors (422), reported before the method runs. A Link or Reference input must
  name a record the caller can read. The method must take exactly those names, and `name`,
  `model`, `record_id` and `self` are reserved because `context.action()` uses them. Each
  input field is copied, so one Field instance may be shared. Without
  `inputs`, the method's annotated scalar parameters (`str`, `int`, `bool`, `float`, `RecordId`) are its
  inputs, as before.
* `available_when` is a finite-v1 Boolean expression decided on the saved record before
  the inputs are read. When it is not true the server answers 409, for example
  `Record payment is not available: the condition for this action is not met`. 409 is used
  because the record's current state, not the caller's role, is what blocks it; a role the
  action does not list still gets 403.
* An action may queue a background job with `self.context.enqueue(task, payload)` and return
  its receipt. The job is queued only if the action commits and runs right after it; the detail
  carries it as `_job` (`{"id", "state"}`)
  ([jobs-realtime.md](jobs-realtime.md#queue-a-job-from-an-action)).
* `POST /api/{model}/{id}/actions/{name}` takes the inputs as its JSON body and requires
  `If-Match`. `context.action(Model, id, "name", **inputs)` does the same from Python.
* Actions registered with `Action(...)` on `Application` keep working unchanged. They run
  as trusted code with the transaction and are not limited by a State lock, but they cannot
  change the State itself outside `tx.maintenance()`. To change the State and write other
  rows in one transaction, declare a rule and a `@state` hook that uses `self.context.session`.
* An action changes Table rows through the draft: `self.lines[0].qty = 3` edits a row, and
  assigning a list replaces the rows, so `self.lines = [*self.lines, {"qty": "1", "rate": "2"}]`
  adds one. Rows already in the list keep their IDs; a dict is a new row, cleaned and
  calculated like submitted rows; rows left out are deleted.
* An action may return `present(SomeSlip, source=self, values={...})`. The detail carries it as
  `_outcome`, and the record view presents that slip next. See [slips.md](slips.md).
* A hook or action that raises something other than a validation error rolls everything back
  and answers 500 `Internal server error`, without its message.

## The transaction session in hooks and actions

A `@state` or `@state.exit` hook and an `@action` handler change the record through `self`.
When they must also write other rows in the same transaction, such as ledger rows, they use
`self.context.session`. It is the transaction session (`evlet.Session`, the `tx` of
`database.transaction()`) of the move or action itself, with `tx.lock`, `tx.insert_many`,
`tx.reserve_ids`, `tx.aggregate`, `tx.create` and the rest ([ledger.md](ledger.md)).

```python
class Transfer(Model):
    item = Text(required=True)
    origin = Text(default="store")
    destination = Text(default="site")
    quantity = Integer(default=1)
    number = Sequence("TR{n:04}", assign_on="sent")
    status = State(State.Step("draft"), State.Step("sent", lock=True),
                   rules=(State.Rule("draft", "sent", "Send", key="send"),))

    @state("sent")
    def post_movements(self):
        tx = self.context.session
        tx.lock(("place", self.item, self.origin))
        held = tx.aggregate(Movement, measures={"quantity": Sum("quantity")},
                            filters={"item": self.item, "place": self.origin})
        if ((held[0]["quantity"] if held else None) or 0) < self.quantity:
            raise ValidationError({"quantity": "Not enough on hand"})
        tx.insert_many(Movement, [
            {"transfer": self.id, "item": self.item, "place": self.origin, "quantity": -self.quantity},
            {"transfer": self.id, "item": self.item, "place": self.destination, "quantity": self.quantity},
        ])
```

* **One transaction.** The State change, the hooks or action, the Sequence number, the record's
  own changes and every row written through the session commit together. A failure anywhere
  rolls all of it back, and no Sequence number is used up. This holds when trusted code catches
  the failure of `context.move()` or `context.action()` and its own transaction carries on.
* **Trusted code.** Writes through the session bypass row and field permissions and Controls,
  like any Python session ([AGENTS.md](../AGENTS.md): Python sessions and action handlers are
  trusted code). The checks that decide whether the move or action may run at all are
  unchanged: update permission and row scope, the rule's `by` or the action's roles, guards and
  `If-Match`.
* **Only while the method runs.** The session is open only on the context the hook or action
  handler gets as `self.context`, and only until it returns. A context kept past that refuses
  it, and so do the rows' own `context` and `context.read_context()`.
* **Nowhere else.** Every other context refuses it with `AccessError` (403):
  `business.context()`, business services (`@api`), reports, cron functions, `@validate`, and
  everything Eva, assistants, tools, MCP and the connector reach. Those callers may still run
  an action or move they are allowed to: the app's own hook then gets the session, never the
  caller.
* Change the record being moved through `self`, not through the session. The session cannot
  change the State either, outside `tx.maintenance()` for audited repairs.
* `derive()` gets the session read only, for lookups
  ([model-engine.md](model-engine.md#derived-values)).
* A session write to another record whose model declares `derive()`, or to one of its rows,
  settles that record too, so its derived values are stored with it.

## What the caller may do now

Detail responses of a model with a State, or with an action that declares `available_when`,
include keys that are not part of the ETag:

```json
{
  "_locked": false,
  "_moves": [
    {"key": "issue", "to": "issued", "label": "Issue", "allowed": false, "primary": true,
     "reason": "The condition for this move needs values that are not filled in yet"},
    {"key": "void", "to": "void", "label": "Void", "allowed": false, "reason": "Your role cannot use this move",
     "remark": {"mode": "required", "label": null, "required": true}}
  ],
  "_actions": [
    {"name": "record_payment", "label": "Record payment", "allowed": false,
     "reason": "The condition for this action is not met"}
  ]
}
```

* `_moves` lists every rule out of the current state, except system moves, for every role that
  can read the State, with the reason it is blocked: the rule's `by` (`Your role cannot use
  this move`), missing update access (`You cannot change this record`, also on a
  `Meta.api = "read"` model), a record dated in a closed period (`This record is in a closed
  period`), or the guard (not met, waiting for values, could not be checked). Of several rules
  to one state, the first this person may use now is listed.
* `primary` marks a rule declared `primary=True`; `asks` and `remark` appear when they apply,
  `remark.required` decided on this record.
* `_actions` lists the model actions this role may run.
* When a guard reads a value the role cannot read, the reason is always
  `This move is not available now` (or `action`), so it does not tell whether a hidden
  value is empty.
* A role that cannot read the State field gets no `_moves`, no `_locked` and no `state`
  schema. It still gets `_actions`, because actions are not part of the State.

## Schema metadata

`/api/schema` model entries carry, for roles that can read the State:

```json
"state": {"field": "status", "default": "draft", "show": "bar",
          "steps": [{"key": "draft", "label": "Draft", "color": null, "lock": false, "release": false, "final": false}],
          "rules": [{"key": "void", "from": ["issued"], "to": "void", "label": "Void", "by": ["admin"],
                     "guarded": false, "when": null, "primary": false, "reject": false,
                     "remark": {"mode": "required", "label": null}}]}
```

* System moves are left out. `by` lists role names. `when` is the serialized finite-v1
  expression when the role can read every value it reads, otherwise `null`. `asks` appears when
  the rule asks for fields.
* Each model action has `roles`, `inputs` (field schemas, as in `fields`), `available_when`
  (the same readability rule), `guarded`, and `arguments` in the scalar form the current form
  renders.
* Field entries carry `versioned: false` where declared, and the State field has `state: true`.

## Not yet

* Approval levels (0.4.6, PR 2); releases, versions and the `@release` hook (PR 3); restore
  (PR 4); rules changed on an instance (PR 5).
* Draft previews (`/api/_draft`) do not report the State lock; the save does. A preview of a
  locked or posted record shows the calculated values it keeps, like the save.
