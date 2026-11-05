# States, moves and model actions

A `State` field holds where a document is in its life, for example draft, issued or
paid. Its value changes only through declared moves. Some states lock the document
against ordinary edits. Model actions are declared methods that may take validated
inputs and may be available only in some states.

This guide covers Phases 4 and 5 of the model handoff: states, moves, actions, and the
void and revise corrections. Rows a move posts to other models, and their reversal on void,
are described in [effects.md](effects.md). Each move, void and revise is recorded in the
record's [Journal](journal.md).

## Declaring a State

```python
from evlet import Child, Currency, Date, Decimal, Model, Move, State, Table, Text, action
from evlet.calc import F


class Invoice(Model):
    class Line(Child):
        qty = Decimal(default=1)
        rate = Currency(default=0)
        amount = Currency(compute=F("qty") * F("rate"))
        note = Text(default="", editable_when_locked=True)

    lines = Table(Line)
    total = Currency(compute=lines.sum("amount"))
    remarks = Text(default="", editable_when_locked=True)
    status = State(
        ("draft", "issued", "paid", "void"),
        default="draft",
        moves=(
            Move("draft", "issued", "Issue", when=F("total") > 0, run="on_issue"),
            Move("issued", "paid", "Mark paid", roles=("admin",)),
        ),
        locked=("issued", "paid", "void"),
        final=("paid", "void"),
        voidable=("issued",),
    )

    def on_issue(self):
        self.remarks = "Issued"

    @action("Record payment", inputs={"amount": Currency(required=True), "paid_on": Date(required=True)},
            available_when=F("status") == "issued")
    def record_payment(self, amount, paid_on):
        self.remarks = f"Paid {amount} on {paid_on}"
```

`State(choices, default=..., moves=..., locked=..., final=..., voidable=...)`:

* It is a `Select`. Its schema type is `select`, it is read only in forms, and finite
  expressions read it like any other Select (`F("status") == "issued"`).
* `default` is the first state when left out. A new record always starts in it.
* A model has at most one State, and only root documents have one (not `Table` rows).
* Every state named in a move, `locked`, `final` or `voidable` must be one of the
  choices. A final state has no moves out. Voidable states must be locked and not final.
* A State with `voidable` states must also have a state named `void` that is locked and
  final. Void and revise (below) start only from a voidable state and end in `void`. No
  declared Move may then go to `void`: a record is voided only through void, with a reason,
  so later phases can always reverse its effects and record it.
* State also takes `label`, `help_text`, `placeholder`, `hidden`, `index`, `max_length` and
  `colors` (as on a Select, below). It takes no rules, calculations or validators.
* `show` says how the record page draws the State: `"badge"` (the default), `"bar"` or
  `"none"`. See [How a state and a status show](#how-a-state-and-a-status-show).

`Move(from_, to, label, roles=("admin", "editor"), when=None, run=None)`:

* Write the states positionally: `Move("draft", "issued", "Issue")`. The first
  argument is one state or a tuple of states. `label` defaults to the target name.
* There is at most one move between two states, so a move is named by its target.
* `roles` lists the roles that may use the move. Policy rules are not extended with
  move names; the caller also needs update permission on the record within its row scope.
* `when` is a finite-v1 Boolean expression, the same language as `locked_when`. It is
  decided on the record as saved, before the move. A guard that is pending (an input is
  empty) or fails blocks the move, like an undecided lock.
* `run` names a model method that takes only `self`, like an action. It runs after the
  state changes and before the document settles, so calculations, rules and validation see
  its changes.

## How a state and a status show

Not every State is a pipeline, and not every status is set by hand. `show` and `Meta.status`
say how the record page and lists draw them.

```python
class Member(Model):
    # Active or left is not a pipeline: a badge in the header (the default).
    standing = State(("active", "left"), moves=(Move("active", "left", "Mark as left"),
                                                Move("left", "active", "Reinstate", roles=("admin",))))


class Claim(Model):
    # Draft, submitted, approved is a pipeline: the stage bar.
    stage = State(("draft", "submitted", "approved"), show="bar",
                  moves=(Move("draft", "submitted", "Submit"), Move("submitted", "approved", "Approve")))
```

* `show="badge"` (the default) draws the state as a coloured badge in the record header, with
  the key facts. The moves stay as buttons in the header actions.
* `show="bar"` draws the stage bar under the header, every state in order, for pipelines such as
  draft, submitted, approved.
* `show="none"` draws neither, for an app that places the state itself. The moves stay as header
  buttons.
* Any other value fails at registration: `State show is "badge", "bar" or "none"`. The schema's
  `state` entry carries it as `show`.

Display never implies that a person can change the state:

* Clicking the badge opens a menu of the moves this person may take now, and only those. A
  step of the bar is a button only when this person may move to that state now. Everything else
  is plain text with no pointer or hover: states only the system reaches, moves for other roles,
  and moves whose condition is not met. The header buttons still list those moves and say why
  each is blocked.
* "May take now" is `allowed` in `_moves` (below), so the server decides it: the move's roles, update
  permission and the row scope, the move's condition, `Meta.api = "read"` and a closed period
  (`Meta.period`). The record page also needs update permission on the model.
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
* The badge is never a button. A calculated or read-only Select cannot be edited anywhere. A Select
  people may write still shows as a badge; they change it in the form, not from the badge.
* When `Meta.status` names the model's State, the State's badge shows in the header whatever its
  `show`, with its moves. When it names another field, both badges show, the State's first (if
  its `show` is `"badge"`).
* Lists draw the State's column and the `Meta.status` column as the same badges.
* `Select(..., colors={...})` maps some choices to a badge colour: `blue`, `green`, `amber`,
  `rose`, `violet`, `teal` or `slate`. Unknown choices or colours fail when the field is
  declared. The schema carries `colors` on the field. Choices without a colour take the UI's
  usual tint for the name (green for paid or approved, rose for void or lost, and so on).
* A legacy string formula may calculate a Select. The value must be one of its choices.

## Ordinary writes and locks

Ordinary writes are HTTP create, update and delete, `Context.create`, `Context.update`,
`Context.delete`, and dialogs and services that write through `Context`.

* Create may leave the State out or send its default. Any other value is a field error:
  `A new record starts as draft; use its moves to change it`.
* Update may not set the State: `This changes only through its moves`. Sending the saved
  value back is not a change and is accepted.
* Python code cannot assign the State on a document draft either, inside actions or `run`.
* While the saved state is in `locked`, an ordinary write may not change the record or
  any of its Table rows at any depth. Adding, removing or reordering rows is a change.
  Each changed field gets the error `This record is issued, so it is locked`; a row
  collection that gained, lost or reordered rows gets it under the collection name.
* A field declared with `editable_when_locked=True` stays editable by ordinary writes, on
  the record and on its rows. Its own `locked_when` still applies.
* A locked record keeps its saved results. An ordinary write changes only the
  `editable_when_locked` fields; every calculated value, at every depth, keeps the value it
  had when the record was locked, even if a linked record or today's date changed since.
  Moves and actions settle normally and recalculate, unless the record has postings (below).
  So that this stays consistent, a
  stored result (at any depth of the document) may not read an `editable_when_locked`
  field; registration fails with a message naming the field. This covers finite formulas,
  legacy string formulas (`compute="sum(lines.extra)"`), reads through virtual results,
  Query fields and `fetch="customer.name"` copies whose Link is `editable_when_locked`.
  A virtual result may read such a field. A creation default (`default=Ref(...)`) is not a
  result: it applies once, on create.
* Rules and validation on such a write read the saved results too: `required_when`,
  `applies_when`, `locked_when`, `required_if` and validators see the values the record
  keeps, not values it would get if it were recalculated. Fetched copies are not refreshed.
* `"_calculation": {"recalculate": true}` on a locked record is refused with 409.
* An ordinary write on a locked record does not recalculate, so it does not need the
  active formula version and does not change the version recorded for the record. Its
  `editable_when_locked` fields stay editable after a deploy that changes formulas. A move
  or action on such a record recalculates, so it answers 409 `SCHEMA_VERSION_MISMATCH`;
  send `{"_calculation": {"model_schema_hash": "<active hash>", "recalculate": true}}` as
  the body of the move (or with the action) to recalculate with the active formulas.
* A record with posted [effects](effects.md) (it is the source of effect rows and is not
  void) keeps its stored calculated values for every writer. Moves and actions on it keep
  the saved values, at every depth, like ordinary locked writes do, even after a linked
  record or a formula changed; they need no recalculation request. `"recalculate": true`
  on such a record is refused with 409 `This record has postings. To change its totals,
  void or revise it.` Corrections go through void or revise, or an admin's unpost (below).
* Actions, move `run` methods and move values on a posted record may not change anything a
  stored result reads: a source field (directly or through virtual results, fetched copies
  and Link paths, where the Link field counts) or the rows of a Table that holds or feeds a
  stored result (adding, removing or reordering them). Such a change is refused with 409
  naming it, for example `Quantity feeds posted totals. To change it, void or revise the
  record.` or `Lines feed posted totals. To change them, void or revise the record.` Fields
  no stored result reads, such as notes and `editable_when_locked` fields, still change.
* The `Recompute` migration operation skips only records with postings, unless an admin
  migrates with `evlet migrate <instance> --recompute-posted` (see
  [finite-calculations.md](finite-calculations.md)). A locked record that never posted, or
  was unposted, is recalculated like any other and gets a `recomputed` Journal entry.
* Deleting a record in a locked state is refused with 409. Corrections use void and revise
  (below).
* Uploading or removing an attachment is an ordinary write: on a locked record it is
  refused with 409. Downloads still work.
* A role that cannot read the State field gets `This record is locked` (and `This record is
  locked and cannot be deleted`) without the state's name.
* Raw database writes (`Session.create` and `Session.update`, including legacy `Action`
  handlers) may create a record in the default state and may send the saved state back,
  but any other State change raises an error unless it runs inside `tx.maintenance()` for
  an audited repair. Legacy handlers should use `context.move()` or a model action instead.

## Moves

`POST /api/{model}/{id}/moves/{to}` with an empty JSON object performs a move.

A move may carry values for the fields its target state brings in. A move *asks* for a root
field when the field's `applies_when` or `required_when` reads the State, is false in a source
state of the move, and is true (or depends on other values) in the target state. Only fields the
role may write are asked for.

```python
lost_reason = Text(applies_when=F("stage") == "lost", required_when=F("stage") == "lost")
# POST /api/deal/01hc1sd9vc1ye/moves/lost  {"lost_reason": "Chose another vendor"}
context.move(Deal, 7, "lost", lost_reason="Chose another vendor")
```

* The values are applied with the move and checked by the same rules, validation and permissions
  in its transaction. A required value that is missing fails the move with a field error (422)
  and nothing changes.
* Any other key is refused with `This move does not take this value`: as a field error when
  the role can read the field, and as a form error (`_form`) for an unknown key or a field the
  role cannot read, so the answer never confirms a hidden field.
* The move itself is checked first (the order below), so values never reveal which moves
  exist: an unknown target is 404 with or without values.
* `_moves` and the schema's `state.moves` list the asked fields as `asks` (field names in
  declaration order). A move that asks for nothing has no `asks` key. The schema lists what the
  declaration asks for; `_moves` also leaves out a field whose `locked_when` holds on the record,
  since a locked field cannot take a value.

* It requires `If-Match` with the record's current ETag, like other document writes:
  428 without it, 409 when it is stale.
* The checks run in this order. Update permission and row scope come first, as for any
  update (403 or 404). Then: a target that is not a state, 404. A final current state:
  409 `This record is paid, which is final; it has no moves`. No declared move from the
  current state: 409 `This record cannot move from draft to paid`. A role the move does
  not list: 403 `Your role cannot use Mark paid`. A guard that is not true: 409, for
  example `Issue is not available: the condition for this move is not met`.
* A role that cannot read the State field gets 409 `This record has no such move now` for
  every refused move: a target that is not a state, a final state, no such move, a move
  for other roles and a guard that is not true. It cannot tell which states and moves
  exist. Roles that can read the State keep the detailed messages above.
* The state change, the `run` method, calculations, rules, validation and the stored
  rows commit in one transaction. Any failure rolls everything back.
* The response is the permission-filtered detail with a new ETag.
* From Python, `context.move(Invoice, record_id, "issued")` performs the same checks.
  The model's `changed()` hook receives the operation `"move"`.

Moves and actions are declared, role-checked code. They may change a record in a locked
state, but each field's `locked_when`, `applies_when`, `required_when`, validation and
the permissions on what is returned still apply.

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
* `POST /api/{model}/{id}/actions/{name}` takes the inputs as its JSON body and requires
  `If-Match`. `context.action(Model, id, "name", **inputs)` does the same from Python.
* Actions registered with `Action(...)` on `Application` keep working unchanged. They run
  as trusted code with the transaction and are not limited by a State lock, but they cannot
  change the State itself outside `tx.maintenance()`.
* An action changes Table rows through the draft: `self.lines[0].qty = 3` edits a row, and
  assigning a list replaces the rows, so `self.lines = [*self.lines, {"qty": "1", "rate": "2"}]`
  adds one. Rows already in the list keep their IDs; a dict is a new row, cleaned and
  calculated like submitted rows; rows left out are deleted.
* An action may return `present(SomeSlip, source=self, values={...})`. The detail carries it as
  `_outcome`, and the record view presents that slip next. See [slips.md](slips.md).
* A `run` method or action that raises something other than a validation error rolls
  everything back and answers 500 `Internal server error`, without its message.

## What the caller may do now

Detail responses of a model with a State, or with an action that declares
`available_when`, include three keys that are not part of the ETag:

```json
{
  "_locked": false,
  "_moves": [
    {"to": "issued", "label": "Issue", "allowed": false,
     "reason": "The condition for this move needs values that are not filled in yet"},
    {"to": "paid", "label": "Mark paid", "allowed": false, "reason": "Your role cannot use this move"},
    {"to": "cancelled", "label": "Cancel", "allowed": true, "asks": ["cancel_reason"]}
  ],
  "_actions": [
    {"name": "record_payment", "label": "Record payment", "allowed": false,
     "reason": "The condition for this action is not met"}
  ]
}
```

* `_moves` lists every move out of the current state, for every role, with the reason it
  is blocked: the role, missing update access (`You cannot change this record`, also on a
  `Meta.api = "read"` model), a record dated in a closed period (`This record is in a closed
  period`), or the guard (not met, waiting for values, could not be checked).
* `_actions` lists the model actions this role may run.
* When a guard reads a value the role cannot read, the reason is always
  `This move is not available now` (or `action`), so it does not tell whether a hidden
  value is empty. Whether the operation is allowed is still shown, because the server
  enforces it.
* A role that cannot read the State field gets no `_moves`, no `_locked` and no `state`
  schema. It still gets `_actions`, because actions are not part of the State.
* Guards that read only the record's own values (stored results included) are decided
  without loading its rows. Guards that read owned rows or virtual results use the rows the
  detail response already read when the caller can read every row; otherwise they load the
  saved document once per request.

## Void and revise

A locked record is never edited to correct it. It is voided, or revised, which voids it and
starts a new draft copy.

```python
class Invoice(Model):
    class Meta:
        calculation_language = "finite-v1"
        numeric_profile = "finite40-hu18-v1"
        revisable = True

    status = State(("draft", "issued", "paid", "void"), moves=(Move("draft", "issued", "Issue"),),
                   locked=("issued", "paid", "void"), final=("paid", "void"), voidable=("issued",))
```

### Void

`POST /api/{model}/{id}/void` with `{"reason": "..."}`, or `context.void(Invoice, record_id,
reason)` from Python.

* The model must have voidable states, otherwise 404 `This record cannot be voided`.
* Then the checks run in this order: the `void` permission and the row scope, as for any
  record (403 or 404); `If-Match` over HTTP (428 without it, 409 when stale); the body and
  the reason; the saved state. A missing record answers 404 whatever the body holds.
* The reason is required. It is trimmed and may have up to 1,000 characters. The body takes
  no other keys (`Send only a reason`). Errors are field errors (422).
* Voiding starts only from a state in `voidable`; otherwise 409 `This record is draft, which
  cannot be voided`. A role that cannot read the State gets `This record cannot be voided now`.
* The engine writes the `void` state and, for a model with effects, the mirror rows that reverse
  them (see [effects.md](effects.md)). A void is not an ordinary write and not
  a declared Move: no State lock, move roles or guards apply, and the document is not
  recalculated. Every other value, at every depth, stays as it was saved, saved results
  included.
* The model's `changed()` hook receives the operation `"void"`.
* The response is the permission-filtered detail with a new ETag.

### Revise

`POST /api/{model}/{id}/revise` with `{"reason": "..."}`, or `context.revise(Invoice,
record_id, reason)`. Only models with `Meta.revisable = True`; others answer 404 `This record
cannot be revised`.

* `Meta.revisable = True` needs a State with voidable states. The engine adds a read-only,
  indexed field `revises = Link(<the model>, index=True, label="Revises")`, so a revision
  always points at a record of the same model. A model may not declare its own `revises` or
  `revised_by`. Ordinary writes cannot set `revises` (403).
* A field that is both `unique` and `required` is not allowed on a revisable model or its
  Table rows, because a revision clears unique values (below).
* The checks are those of void, with the `revise` permission, and the role also needs create
  permission on the model. A reason is required, because the original is voided.
* Revise starts from a voidable state, or from `void` when the record has no revision yet,
  for example because its revision draft was deleted. A void record stays void and gets a new
  copy (no second void, so the void hooks do not run again). A record that has a revision
  answers 409 `This record already has a revision`.
* Before anything changes, the engine checks that the caller's copy could be created, and
  answers 403 with a plain reason otherwise: `You cannot see every row of this record, so you
  cannot revise it`, `Your role cannot add the rows of this record, so you cannot revise it`
  or `This record links to a record you cannot read, so you cannot revise it`.
* In one transaction the engine voids the original exactly as above, then creates a new
  draft. It is an exact copy of the stored source values of the record and of its Table rows
  at every depth, including fields the caller cannot read or write. Rows get new ids and keep
  their order and positions, the State starts at its default, `revises` points at the
  original, and unique fields are cleared. Calculated values are recalculated by the normal
  pipeline, so a stored result over a linked record takes the linked value of today. A field
  with an `initial` suggestion keeps the copied value. Fetched values are fetched again.
  Attachments are not copied.
* The copy is then checked like any new draft: rules, validation, the caller's row scope (the
  copy must be inside it, 404 otherwise) and create permission for each row collection. Any
  failure rolls back the void too.
* The model's `changed()` hook receives `"void"` for the original and `"revise"` for the copy.
* The HTTP response is 201 with the copy's detail, its ETag, a `Location` header and
  `_original`, the id of the voided record.
* The original's detail shows `revised_by`, the id of its revision, found through the index on
  `revises` (there is no column for it). It is `null` until a revision exists, after the
  revision is deleted, or when the caller cannot read the revision, and it is left out for
  roles that cannot read `revises`.

Adding `Meta.revisable = True` to a model that already has a table adds a column, so it
needs a migration like any new field. Pass the model class as the Link target:

```python
Migration("0005_quote_revises", AddField("quote", "revises", Link(Quote, index=True)))
```

Without it, startup reports that the database columns differ from the models.

### Permissions

`Rule.operations` accepts `"void"` and `"revise"`, on models that support them (a Rule naming
one on another model fails at startup).

* Admin always has `void` while its Rule on the model allows `read` and `update`, and
  `revise` while it also allows `create`. This holds for governed and ungoverned models.
* A Rule naming `void` or `revise` adds that role. It never removes admin. So
  `{"admin": Rule(("read", "create", "update", "delete")), "editor": Rule(("read", "update",
  "void"))}` lets both the admin and the editor void.
* A Rule naming `revise` must also name `create`; otherwise startup fails.
* Void and revise are separate operations: revise does not need `void`. Neither changes what
  `read`, `create`, `update` or `delete` allow, and neither is passed on to Table rows.
* The row scope of the role's Rule applies, as for any record.

### Availability in details

Details of a voidable model add `_void`, and of a revisable model `_revise`, outside the
ETag, for roles that can read the State:

```json
{"_void": {"allowed": false, "reason": "This record cannot be voided in its current state"},
 "_revise": {"allowed": true}}
```

Reasons are `Your role cannot void this record` (no permission, or for revise no create
permission), `You cannot void this record` (outside the row scope), the state reason, which
never names the state, `This record already has a revision`, and for revise the three copy
reasons above. They never include counts or names.

## Unpost and repost

A posted record is a record with live [effect](effects.md) rows: it is the source of effect
rows, and the latest row of some effect is an original, not a reversal. An admin may take
such a record back to the state it posted from, correct it and post it again, without voiding
it or creating a copy. The record keeps its id and its Sequence numbers.

### Unpost

`POST /api/{model}/{id}/unpost` with `{"reason": "..."}`, or `context.unpost(Invoice,
record_id, reason)` from Python.

* The model must declare `Meta.effects`, each with a `reverse`; otherwise 404 `This record
  cannot be unposted`.
* The checks run in the order of void: the `unpost` permission and the row scope (403 or
  404), `If-Match` over HTTP (428 without it, 409 when stale), the body and the reason (422,
  required, trimmed, up to 1,000 characters, no other keys), then the saved record (409):
  * `This record is void, so it cannot be unposted`.
  * `This record has nothing posted to cancel` for a record without live rows (never posted,
    or unposted already).
  * `This record cannot be unposted while Payment 4 depends on it. Void or unpost it first.`
    when a live record depends on it (below). Up to five records the caller can open are
    named, and the rest are counted (`2 records you cannot open`).
  * `The state this record posted from is not known, so it cannot be unposted` (below).
* In one transaction the engine writes the earlier state, writes one reversal row for each
  live effect row (as a void does, see [effects.md](effects.md)), calls the model's
  `changed()` hook with the operation `"unpost"`, and writes the Journal's `unposted` entry,
  the `effects` entry of the reversal rows and a Controls audit row (area `Records`). Nothing
  is deleted. A failure anywhere rolls all of it back.
* The record is not recalculated: every value stays as saved. Its Sequence numbers stay, so no
  number is taken or given back, and a number's scope fields and date period stay fixed.
* Afterwards the record is an ordinary record in that state, with that state's rules. When it
  posted from an unlocked state (draft), it can be edited, recalculated and moved like any
  other, and `Recompute` recalculates it. When it posted from a locked state (for example
  draft, then issued and locked, then paid, with the effect posting on paid), it goes back to
  that locked state: that is the real state it was in before it posted, so it stays locked,
  only its `editable_when_locked` fields change, and it posts again by Repost.
* It is never deleted, even when its state would allow it: its reversed rows still name it as
  their source and its number stays used. Delete answers 409 `This record has posted before,
  so it cannot be deleted. Void it instead.` (or `Repost it and void it instead.` from a state
  that cannot be voided), and details carry `_deletable: false` so screens do not offer it.
* The response is the permission-filtered detail with a new ETag.

**The state it returns to** is the state the posting move started from. The engine takes
the states a record can be in before it posts (reachable from the default without entering a
state an effect posts on) and keeps those a posting move starts from. When there is one, that
is the state. When moves from several states post (testCRM's Mark won starts from proposal or
negotiation), the record's Journal names the one it took last, in its latest `moved` entry
into a posting state. A model without a journal and with several such states cannot unpost.

**Dependent records.** A record depends on this one when it, or one of its Table rows, links
with a `Link` or a `Reference` to the record, to one of its Table rows at any depth, or to one
of the effect rows it posted (an allocation matching one of its ledger entries, say), or when
it posted effect rows that do. It blocks the unpost while it is live: it has a State, is not
void, and is in a locked state or has live postings itself. A draft payment does not block; a
confirmed one does until it is voided or unposted. The record's own Table rows and effect rows
are not dependents. Only Link fields whose target is the model, one of its Table row models
or one of its effect targets are read, plus every `Reference` field (a Reference may name any
model); see [performance.md](performance.md#unpost-dependents) for the cost.

### Repost

`POST /api/{model}/{id}/repost` with `{}` or `{"reason": "..."}`, or `context.repost(Invoice,
record_id)`.

* The record must have been unposted: it has effect rows, none of them live, and it is not
  void. Otherwise 409 `This record is posted already`, `This record was never posted. Use its
  moves to post it` or `This record is void, so it cannot be reposted`.
* The reason is optional; when given it is checked like an unpost reason and recorded.
* The move that posts from the current state must ask for no value it may require (a
  `required` or `required_when` field it asks for); otherwise 409 `Send asks for values, so
  post the record with Send itself`, since a repost sends no values.
* The engine runs the move that posts from the record's current state, through the normal
  move pipeline: the move's roles and guard, recalculation, validation and the version check
  (a stale record answers 409 `SCHEMA_VERSION_MISMATCH` and is recalculated first), then the
  effects post fresh rows. When no single move posts from the current state, the answer is 409
  and the record's own moves are the way to post it.
* The Journal gets the move's `moved` and `effects` entries, then `reposted` with `to`,
  `move` and the reason when given. A Controls audit row is written. A failed check rolls
  everything back.

### Permissions for unpost and repost

* `Rule.operations` accepts `"unpost"` and `"repost"` on models whose effects all declare a
  `reverse`; naming them on another model fails at startup. A Rule naming `repost` must also
  name `update` (a Controls override likewise), since a repost runs the posting move.
* Admin always has both while its Rule on the model allows `read` and `update`, like void.
  A Rule naming one adds that role and never removes admin. A Controls override can keep or
  take away a role's grant, like `void`.
* Every surface uses the same check: the HTTP routes (sessions, API keys, AI assistants and
  scoped sub-host frontends, which must list `(Model, "unpost")` or `(Model, "repost")` in
  their `ApiScope`), and `Context.unpost()` and `Context.repost()`. An API key needs write
  access and the model in its scope. The connector and the MCP server do not offer them.

### Availability in details

For roles that have the operation and can read the State, details add `_unpost` on a posted
record and `_repost` on an unposted one, outside the ETag:

```json
{"_unpost": {"allowed": true}}
```

They are left out for other roles and when they do not apply. `_repost` is also left out
when the caller could not run the posting move: its role is not among the move's roles, it
cannot update the record (permission or row scope), or the move asks for a required value. Dependent records are checked
when the unpost is sent, not in details, so `_unpost` can be allowed and the unpost still be
refused with the names of the records that depend on it.

## Schema metadata

`/api/schema` model entries gain, additively:

* Entries of voidable models gain `voidable: true` and `revisable`, and their `permissions`
  gain `void` (and `revise` when revisable), for roles that can read the State. Entries of
  models that can unpost gain `unpostable: true` and `unpost` and `repost` in `permissions`.
  Other entries are unchanged. The `revises` field is listed with the other fields (type `link`, read only).
* `state`: `field`, `states`, `default`, `locked`, `final`, `voidable` and `moves`, each
  with `from`, `to`, `label`, `roles`, `guarded`, `when` and, when the move asks for values,
  `asks`. `when` is the serialized
  finite-v1 expression when the role can read every value it reads, otherwise `null`.
* Each model action gains `roles`, `inputs` (field schemas, as in `fields`),
  `available_when` (the same readability rule), `guarded`, and `arguments` in the scalar
  form the current form renders (text for everything except whole numbers and Booleans).
* Field entries gain `editable_when_locked: true` where declared, and the State field has
  `state: true`.

## Not yet

* The Journal ([journal.md](journal.md)) records each move, void (with its reason),
  revise, unpost and repost. `evlet.states.moved()`, `evlet.corrections.voided()`,
  `evlet.corrections.revised()`, `evlet.corrections.unposted()` and
  `evlet.corrections.reposted()` write those entries in the operation's transaction; the
  last two also write the Controls audit row.
* Effects are reversed in `evlet.corrections.reverse_effects()`; see [effects.md](effects.md).
* Document numbers are described in [sequences.md](sequences.md). A void record keeps its
  number; `evlet.corrections.prepare_copy()` clears the numbers on a revision's copy, which
  is numbered again when it reaches `assign_on`.
* Draft previews (`/api/_draft`) do not report the State lock; the save does. A preview
  of a locked or posted record shows the calculated values it keeps, like the save.
* The connector and the MCP server do not offer moves, void, revise, unpost or repost yet.
* The record view shows the State as steps with its moves, and offers void and revise
  from `_moves`, `_void` and `_revise`, and unpost and repost from `_unpost` and `_repost`.
