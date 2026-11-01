# States, moves and model actions

A `State` field holds where a document is in its life, for example draft, issued or
paid. Its value changes only through declared moves. Some states lock the document
against ordinary edits. Model actions are declared methods that may take validated
inputs and may be available only in some states.

This guide covers Phase 4 of the model handoff. Void and revise (Phase 5) and the
Journal (Phase 8) are not implemented yet; see "Not yet" below.

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
            Move(("draft", "issued"), "void", "Void", roles=("admin",)),
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
  `voidable` is stored for Phase 5 and has no effect yet.
* State also takes `label`, `help_text`, `placeholder`, `hidden`, `index` and
  `max_length`. It takes no rules, calculations or validators.

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
  Moves and actions settle normally and recalculate. So that this stays consistent, a
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
* Deleting a record in a locked state is refused with 409. Corrections will come from
  void and revise (Phase 5).
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
  `inputs`, the method's annotated scalar parameters (`str`, `int`, `bool`, `float`) are its
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
    {"to": "void", "label": "Void", "allowed": false, "reason": "Your role cannot use this move"}
  ],
  "_actions": [
    {"name": "record_payment", "label": "Record payment", "allowed": false,
     "reason": "The condition for this action is not met"}
  ]
}
```

* `_moves` lists every move out of the current state, for every role, with the reason it
  is blocked: the role, missing update access, or the guard (not met, waiting for values,
  could not be checked).
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

## Schema metadata

`/api/schema` model entries gain, additively:

* `state`: `field`, `states`, `default`, `locked`, `final`, `voidable` and `moves`, each
  with `from`, `to`, `label`, `roles`, `guarded` and `when`. `when` is the serialized
  finite-v1 expression when the role can read every value it reads, otherwise `null`.
* Each model action gains `roles`, `inputs` (field schemas, as in `fields`),
  `available_when` (the same readability rule), `guarded`, and `arguments` in the scalar
  form the current form renders (text for everything except whole numbers and Booleans).
* Field entries gain `editable_when_locked: true` where declared, and the State field has
  `state: true`.

## Not yet

* Void and revise (Phase 5) will use `voidable`. Until then a locked record cannot be
  corrected except by a move, an action or trusted Python code.
* The Journal (Phase 8) will record each move. The single place that writes it is
  `evlet.states.moved()`, called in the move's transaction.
* Draft previews (`/api/_draft`) do not report the State lock; the save does.
* The connector and the MCP server do not offer moves yet.
* The current SolidJS form shows the State as a read-only select and does not show
  `_moves`; the new UI will.
