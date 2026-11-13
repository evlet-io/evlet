# Document lifecycle

Confirm, cancel, reopen and void are not operations of their own. They are steps and rules of
a document's `State` ([states-actions.md](states-actions.md)). This guide shows the usual
shapes. 0.4.6 removed `confirmable`, `track_changes`, `Meta.revisable` and the void, revise,
unpost and repost operations; the table at the end maps them to the state manager.

## Confirm, reopen and cancel

```python
from evlet import Child, Currency, Integer, Model, Rule, State, Table, Text, ValidationError, state, validate


class Order(Model):
    number = Text(required=True, unique=True)
    customer = Text(required=True)
    remarks = Text(versioned=False)
    status = State(
        State.Step("draft", "Draft"),
        State.Step("confirmed", "Confirmed", color="green", lock=True, release=True),
        State.Step("cancelled", "Cancelled", color="rose", lock=True, final=True),
        rules=(
            State.Rule("draft", "confirmed", "Confirm", primary=True),
            State.Rule("confirmed", "draft", "Reopen", remark="required"),
            State.Rule(("draft", "confirmed"), "cancelled", "Cancel", remark="required"),
        ), show="bar")

    class Line(Child):
        description = Text(required=True)
        quantity = Integer(default=1, minimum=1)
        rate = Currency(default="0")
    lines = Table(Line, required=True)
    permissions = {"admin": Rule(("read", "create", "update", "delete")),
                   "editor": Rule(("read", "create", "update", "journal")),
                   "viewer": Rule(("read",))}

    @validate
    def positive_rates(self):
        if self.status == "confirmed" and any(line.rate <= 0 for line in self.lines):
            raise ValidationError({"lines": "Enter positive rates before confirmation"})

    @state("confirmed")
    def record_confirmation(self):
        self.remarks = "Confirmed by " + (self.context.user.username if self.context.user else "the engine")
```

* **Confirm** is a rule into a `lock=True` step. While confirmed, ordinary writes change only
  `versioned=False` fields, and the record keeps its saved results.
* **Checks before confirming** are `@validate` methods that read the target state
  (`self.status`) and, when needed, the saved one (`self.before.status`).
* **Work on confirming** (posting rows, queueing a job) goes in `@state("confirmed")`, with
  `self.context.session` for other rows in the same transaction. Work on leaving goes in
  `@state.exit("confirmed")`. Rows declared as [effects](effects.md) with `on="confirmed"` are
  posted and reversed by the engine.
* **Reopen** is a rule back, here with a required remark. The record keeps its id and its
  numbers.
* **Cancel** and **void** are final steps reached by a rule, usually with `remark="required"`.
  A final step coloured `rose` reads as cancelled in lists and headers. A record that ever
  posted effect rows is never deleted; move it to a final step instead.
* **Who may** is each rule's `by` (default admin and editor) plus update permission on the
  record. There are no separate `confirm`, `cancel`, `reopen` or `void` permissions.

Each move is a `POST /api/{model}/{id}/moves/{key}` with `If-Match` and `{"remark": "..."}`
when the rule takes one, or `context.move(Order, order_id, "confirmed_to_draft", remark="Price change")`.
The record's [Journal](journal.md) gets a `moved` entry with the rule key and the remark; models
with a State journal by default.

Numbers given on confirm use `Sequence(..., assign_on="confirmed")` or
`Naming(..., assign_on="confirmed")`, the state's name ([sequences.md](sequences.md)).

Run the Lifecycle Playground, which declares the model above, with:

```sh
.venv/bin/python -m uvicorn examples.playground.lifecycle.app:create_app --factory --host 127.0.0.1
```

## What replaced what

| Removed in 0.4.6 | Use |
| --- | --- |
| `confirmable = True` | a State with draft, a locked release step and a final cancelled step |
| `track_changes = True` | `Meta.journal = Journal(tamper_evident=True)`; a State journals by default |
| `Meta.revisable`, `revises`, `revised_by`, `/revise` | a rule back to draft (Reopen) with a remark |
| `/void` | a rule into a final `void` step with `remark="required"` |
| `/unpost`, `/repost` | a rule out of the effect's `on` step (it reverses the rows) and the rule back into it |
| `/confirm`, `/cancel`, `/reopen` | rules, `POST /api/{model}/{id}/moves/{key}` |
| `/revisions`, `/snapshot`, lifecycle `/diff` | `GET /api/{model}/{id}/history`; versions come in 0.4.6, PR 3 |
| `@validate.confirm`, `@validate.cancel`, `check_lifecycle` | `@validate` |
| `@action.confirm`, `@action.cancel`, `context.lifecycle_execution` | `@state(...)`, `@state.exit(...)` |
| `context.document()`, `BoundDocument` | `context.create`, `update`, `move`, `history` |
| `context.record_approval()` | approval levels, 0.4.6, PR 2 |
| `editable_when_locked=True` | `versioned=False` |
| `Naming(assign_on="confirm")` | `assign_on="<state name>"` |

A model that still sets `confirmable` or `track_changes`, or declares `before_close`,
`changed`, `check_lifecycle` or an undecorated `validate()`, fails when the class is created
with a message naming the replacement. Rows that older releases kept in
`evlet_document_lifecycle` and `evlet_document_revisions` are converted by a framework
migration step in 0.4.6, PR 3; until then, an app that used `confirmable` must move to a State
first.
