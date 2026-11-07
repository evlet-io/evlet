# Document workflow: the contract

Status: proposed for owner approval (phase 1 of [workflow.md](workflow.md)). Nothing here is
built. Date: 07-11-2026.

This document fixes the public shapes the later phases build: the neutral JSON of a workflow
definition, the Python declaration an app writes, the permission extension, how workflow adopts
lifecycle and State/Move, the service operations and HTTP routes with their errors, a storage
sketch and the notification hooks. [workflow.md](workflow.md) stays the source of truth for
vocabulary, semantics and milestones. Where this document is more precise, it is the plan made
concrete against the current code; it does not change the plan's decisions.

The clickable prototype is [docs/design/workflow-mockup.html](../design/workflow-mockup.html).
Its definitions use the JSON shape in section 2, and its journeys are the phase 1 acceptance
journeys. Section 12 lists the open questions, each with a recommended answer.

Examples use a Purchase Order only because it is the plan's worked example. The framework knows
nothing about purchasing. Money is exact decimal text in AED, dates are DD-MM-YYYY on screen and
ISO 8601 in JSON, and times are stored in UTC and shown in the instance time zone (GST in the
examples).

## 0. What exists today

Read from the code on 07-11-2026. The contract builds on these and changes none of them.

| Area | What the code does now | Where |
|---|---|---|
| Accounts | One role per account (`evlet_users.role`). No role sets or groups. `User.links` holds linked records (`evlet_user_links`). | `evlet/auth.py` |
| Operations | `Rule(operations, read_fields, write_fields, scope)`. Allowed names: `read`, `create`, `update`, `delete`, `journal`, `print` and the corrections `void`, `revise`, `unpost`, `repost`, `confirm`, `cancel`, `reopen`. The list exists twice: `permissions.py` (rule check) and `controls.py` (`OPERATIONS`). | `evlet/permissions.py`, `evlet/controls.py` |
| Corrections | Admin gets a correction when its Rule reads and updates the model; another role gets it when its Rule names it. Each also needs `update` on the record. | `Policy.__init__`, `lifecycle.begin` |
| Moves and actions | No permission name of their own. `Move(roles=...)` and `@action(roles=...)` plus `update` on the record in its row scope. | `evlet/states.py`, `evlet/business.py` |
| Lifecycle | `confirmable = True`: draft, confirmed, cancelled. Confirm, cancel and reopen need `update` and the operation. A confirmable model cannot have a State, `Meta.effects` or `Meta.revisable`. | `evlet/lifecycle.py` |
| `record_approval` | `context.record_approval(Model, id, approved=..., reason=...)` appends an `approved` or `rejected` journal entry. Needs `update` and a journal. Nothing in the framework calls it; the confirm snapshot copies the latest one. It grants nothing. | `evlet/lifecycle.py` |
| Journal | `evlet_journal(id, model, record_id, kind, actor, at, data, prev_hash, hash)`, append-only by triggers on SQLite and PostgreSQL, permission-filtered on read. | `evlet/journal.py` |
| Errors | `{"error": "..."}` with the status; 422 is `{"error": "Validation failed", "fields": {...}}`. Missing `If-Match` is 428, a stale one 409. | `evlet/app.py` |
| Idempotency | `evlet_operations(key, actor, request, result)`. Dialogs and slips take a body `key` of 8 to 128 characters, checked before `If-Match`, so a retry returns its receipt even with an old ETag. Missing key is 422; same key, other input is 409. | `evlet/forms.py`, `evlet/slips.py` |
| Conditions | Finite-v1 expressions compile to a JSON plan (`{"$expr": op, ...}`) checked by `check_plan`. List filters use `[field, op, value]` rows with `eq, ne, gt, gte, lt, lte, between, in, nin, isnull, contains`. | `evlet/calc.py`, `evlet/query.py` |
| Jobs and events | `JobQueue.enqueue(tx, ...)` commits with the caller's transaction, with a dedupe key. `EventStore.publish` writes `evlet_events` in the transaction; SSE is a hint to reload. | `evlet/jobs.py`, `evlet/events.py` |
| Inbox | `evlet_inbox(id, user_id, kind, actor, model, record_id, entry_id, at, read_at, email, hidden)`. Only `mention` today. 0.4.0 adds an optional, permission-checked `link`. | `evlet/mentions.py` |
| Configuration | Controls overrides apply at once and raise `evlet_config.version`; there is no draft-then-publish document anywhere yet. Changes are audited in `evlet_audit`. | `evlet/controls.py` |
| Hierarchy | `Hierarchy(model, parent=...)` declares a tree of records; accounts link to their record with `LocalAuth.link_user`. | `evlet/permissions.py` |

## 1. Decisions this contract makes

1. A workflow definition is a JSON document with stable ids (section 2). The browser edits it,
   the backend validates and evaluates it. Labels and positions are never identifiers.
2. Apps opt in per model with `Meta.workflow = WorkflowOptions(...)` (section 3). Nothing is
   configurable until the app says so.
3. A new operation, `review`, is the explicit workflow-decision capability (section 4). It needs
   `read`, not `update`. A second new operation, `override`, is never implied.
4. Each protected transition has exactly one controller (section 5). Workflow gates confirm,
   cancel, reopen, a Move target or a model action only when the app lists it.
5. One backend service handles every caller: HTTP, Python context, jobs, the connector and Eva
   (section 6 and 7).
6. Engine state lives in framework tables, never in app fields (section 8).

## 2. Neutral JSON schema of a workflow definition

### 2.1 Envelope

```json
{
  "schema": "evlet.workflow/1",
  "id": "wfl_po0001",
  "version": 3,
  "name": "Purchase order approval",
  "model": "purchaseorder",
  "protects": {"kind": "lifecycle", "operation": "confirm"},
  "applies_when": null,
  "start": "stg_draft1",
  "settings": { "...": "section 2.10" },
  "notes": "Procurement review added for all orders",
  "stages": [ "...section 2.3..." ]
}
```

| Key | Type | Rule |
|---|---|---|
| `schema` | text | Always `evlet.workflow/1`. A later breaking change gets `/2` and a migration. |
| `id` | id | Stable for the life of the workflow. Set by the server on create. |
| `version` | integer | The version this document is or will become. Published versions are immutable. |
| `name` | text | 1 to 120 characters, unique per model among workflows that are not archived. |
| `model` | text | The storage name of a model whose `Meta.workflow` exists. |
| `protects` | object or null | One of the operations the app lists in `WorkflowOptions.protects` (section 3). `null` is an operational workflow that protects nothing. |
| `applies_when` | condition or null | Chooses this workflow among the published workflows of the same model and `protects`. `null` means every record. |
| `start` | stage id | The stage a request starts in. Must be a `work` stage. |
| `settings` | object | Section 2.10. |
| `notes` | text | Publication notes, up to 2,000 characters. |
| `stages` | list | 1 to 40 stages, in display order. Order sets the default sequence and display; it never rewrites an explicit destination. |

`protects` is `{"kind": "lifecycle", "operation": "confirm" | "cancel" | "reopen"}`,
`{"kind": "move", "to": "<state>"}` or `{"kind": "action", "name": "<model action>"}`.

### 2.2 Ids

Every stage, action, review, condition node and route row has an id: a prefix, an underscore and
6 to 26 lowercase letters or digits (`^(stg|act|rev|cnd|rte)_[a-z0-9]{6,26}$`). The editor makes
them; the server accepts any unused id that matches and refuses duplicates within one definition.
Ids survive rename, reorder and publication, so a running request pinned to version 3 and the
draft of version 4 share them, and the change summary compares by id.

### 2.3 Stage

```json
{
  "id": "stg_deprt1",
  "label": "Department Review",
  "kind": "review",
  "assign": null,
  "require": {"when": {"...": "condition"}, "message": "Purchase type must be Equipment or Services."},
  "deadline": {"hours": 48},
  "edit": {"...": "section 2.9"},
  "reviews": {"...": "section 2.5"},
  "actions": [ "...section 2.6..." ]
}
```

* `kind` is `work` (someone prepares or reworks the document, such as Draft), `review` (one
  reviewer, or a review group in `reviews`), `operational` (a business stage such as Inspection,
  On Hold or Released, worked by an assignee) or `end`.
* An `end` stage has `outcome`: `approved`, `rejected` or `closed` (for operational workflows).
  It has no actions, no assignee and no reviews. Reaching `approved` completes the attempt.
* `assign` is an assignee (2.4) for `work`, `operational` and single-reviewer `review` stages. It
  is `null` on a stage with `reviews`.
* `require` blocks the request with `message` when its condition is not true on entry. Unknown
  values block too; they never count as false.
* `deadline` is `{"hours": n}` (1 to 8,760 elapsed hours) or `null`. Business-day deadlines wait
  for the calendar engine ([calendar.md](calendar.md)); until then the interface labels them
  "elapsed hours".

### 2.4 Assignee

```json
{"kind": "requester"}
{"kind": "role", "role": "finance_approver"}
{"kind": "user", "user": "u_01hc1sd9vc1ye"}
{"kind": "fact", "fact": "requester.manager"}
{"kind": "field", "field": "approver"}
```

* `role` is a role in `evlet_roles`. Everyone with that role at stage entry is eligible.
* `user` is one account, by its public id.
* `fact` is a person fact the app declares (section 3.3), such as the requester's manager.
* `field` is a Link to the account model, or a Link to the app's hierarchy model whose linked
  account is the assignee, declared in `WorkflowOptions.assignable`.
* Assignees resolve from saved data at stage entry, under the writer lock, and are frozen for the
  attempt (plan section 5). At each decision the engine checks again that the person is active,
  can read the record and has `review`.

### 2.5 Review group and review

```json
"reviews": {
  "run": "together",
  "complete": {"kind": "all"},
  "rejection": "veto",
  "when_none": "block",
  "on_complete": {"kind": "first_match", "rows": [
     {"id": "rte_dirc01", "when": {"...": "Total is above 100000.00"}, "stage": "stg_drctr1"}],
     "otherwise": "stg_apprv1"},
  "on_reject": {"kind": "stage", "stage": "stg_rejct1"},
  "on_changes": {"kind": "stage", "stage": "stg_draft1"},
  "items": [
    {"id": "rev_techn1", "label": "Technical", "assign": {"kind": "role", "role": "it_approver"},
     "when": {"...": "Purchase type is Equipment"}, "required": {"kind": "any"}, "stages": null},
    {"id": "rev_finan1", "label": "Finance", "assign": {"kind": "role", "role": "finance_approver"},
     "when": null, "required": {"kind": "any"}, "stages": null}
  ]
}
```

* `run` is `together` (all applicable reviews start at once) or `sequence` (in list order, each
  starting when the one before approves).
* `complete` is `{"kind": "all"}`, `{"kind": "any"}` or `{"kind": "count", "count": n}` with
  1 ≤ n ≤ the number of reviews. It counts reviews that actually started, never skipped ones.
* `rejection` is `veto` (default: one rejection rejects the stage) or `branch` (the review fails
  and the group may still complete). `branch` is allowed only with `any` or `count`.
* `when_none` is `block` (default) or `continue` (go to `on_complete` when no review applies).
* Inside a review, `required` is `{"kind": "any"}` (one eligible person decides) or
  `{"kind": "all"}` or `{"kind": "count", "count": n}` over distinct eligible people.
* `stages` is reserved for nested reviews (a review that runs its own small sequence of stages).
  It is `null` until phase 6 builds it; the schema is fixed now so definitions need no migration.
* Join semantics belong to the group. There is no separate join document.

A stage with `reviews` keeps `actions` (2.6) for the buttons each review offers: their `go_to`
is `null`, because the group's `on_complete`, `on_reject` and `on_changes` decide where the stage
goes.

### 2.6 Action

```json
{
  "id": "act_mapprv",
  "label": "Approve",
  "kind": "approve",
  "who": {"kind": "assigned"},
  "when": null,
  "go_to": {"kind": "stage", "stage": "stg_deprt1"},
  "feedback": {"mode": "optional", "label": "Feedback", "prompt": "", "max_length": 2000},
  "execute": null
}
```

* `kind` is `submit` (from a `work` stage; starts an attempt), `approve`, `reject`,
  `request_changes` or `move` (a plain step between operational stages). The kind drives quorum,
  attempt and history semantics; the label is free text.
* `who` is `{"kind": "assigned"}` (default: the stage's or review's eligible people), the
  requester, or a list of assignees (2.4) for operational stages.
* `when` is a condition that must be true on the saved record for the button to be offered.
* `go_to` is a route (2.7). `null` only on stages with `reviews`.
* `feedback.mode` is `hidden`, `optional` or `required`. Defaults: Approve and Send for review
  optional or hidden; Reject and Request changes required. `max_length` is 1 to 4,000
  (default 2,000).
* `execute` is `null` or an operation the app lists in `WorkflowOptions.executes`:
  `{"kind": "lifecycle", "operation": "confirm", "as": "executor"}`,
  `{"kind": "move", "to": "released", "as": "actor"}` or
  `{"kind": "action", "name": "place_order", "as": "actor"}`. `as` says whose permissions run it:
  the deciding person (`actor`) or the workflow's named executor (`executor`, settings 2.10).

### 2.7 Route

```json
{"kind": "stage", "stage": "stg_apprv1"}
{"kind": "first_match", "rows": [{"id": "rte_dirc01", "when": {}, "stage": "stg_drctr1"}], "otherwise": "stg_apprv1"}
```

`first_match` takes the first row whose condition is true. `otherwise` is required. A row whose
condition cannot be decided (a missing value or an evaluation error) blocks the request with a
message; it is never skipped as false. Starting every matching review is not a route option: it
is a review group (2.5).

### 2.8 Condition

The editor shows Field, Operator and Value rows grouped by All or Any. The JSON is the same tree:

```json
{"id": "cnd_g1", "match": "any", "items": [
   {"id": "cnd_r1", "field": "total", "op": "gt", "value": "100000.00"},
   {"id": "cnd_r2", "field": "fact.supplier_risk", "op": "eq", "value": "high"},
   {"id": "cnd_g2", "match": "all", "items": [
      {"id": "cnd_r3", "field": "lines.count", "op": "gt", "value": 10},
      {"id": "cnd_r4", "field": "requester.department", "op": "in", "value": ["it", "operations"]}]}]}
```

* `field` names a fact from the model's catalogue (2.11): a stored root field (`total`), an
  owned-row aggregate the app declares (`lines.count`, `lines.sum_amount`), a requester attribute
  (`requester.department`) or a registered lookup fact (`fact.supplier_risk`).
* Operators reuse the list filter names: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `between`, `in`,
  `nin`, `isnull`. Each fact type allows a subset: money and numbers all but `in`; enums, links
  and users `eq`, `ne`, `in`, `nin`, `isnull`; dates `eq`, `lt`, `gt`, `between`, `isnull`;
  booleans `eq`.
* Values are typed as in record JSON: money and decimals as exact decimal text (`"100000.00"`),
  dates as ISO text, links and users as public ids, enums as the choice key. `between` takes two
  values, `in` and `nin` take 1 to 100, `isnull` takes `true` or `false`.
* Limits: depth 4, 50 rows per definition condition, 1,000 condition rows per definition.
* The server compiles each condition to a finite-v1 plan and checks it with `check_plan`, so
  comparisons are exact and the browser never supplies an expression. No Python, SQL, imports or
  network calls.
* Evaluation returns true, false or undecided. Undecided blocks and names the missing values.

### 2.9 Edit access

```json
"edit": {
  "who": "requester",
  "fields": {"mode": "only", "names": ["required_by", "remarks"]},
  "tables": {"lines": {"edit": true, "add": false, "remove": false, "reorder": false}},
  "attachments": {"add": true, "remove": false}
}
```

* `who` is `nobody`, `requester`, `assigned`, `selected` (with `people`, a list of assignees)
  or `existing` (everyone who may already update the record).
* `fields.mode` is `all` (every field the person may otherwise write) or `only` (listed fields;
  fields added to the model later are denied until listed).
* `tables` has one entry per Table at any depth (`lines`, `lines.serials`); a missing entry
  denies all four row operations.
* Defaults: a `work` stage allows the requester everything they may already write; every other
  stage is `nobody`.
* Workflow narrows rights and never widens them (section 4.4).

### 2.10 Settings

```json
"settings": {
  "completion": {"mode": "enable", "executor": null},
  "self_approval": "block",
  "withdraw": {"allowed": true},
  "delegation": {"allowed": true},
  "invalidation": {"mode": "any_change"},
  "deadlines": {"unit": "elapsed_hours"},
  "notifications": {"on_assign": true, "on_decision": true, "reminder_hours": 24, "escalate_hours": null},
  "paused": false
}
```

* `completion.mode` `enable` makes the protected operation available to people who may perform
  it. `execute` performs it on completion as `executor` (an account id), which must hold the
  operation when it runs.
* `self_approval` `block` (default) means a requester never decides on their own request.
* `invalidation.mode` is fixed to `any_change` in 0.4.0; the app's `harmless` fields are the
  exceptions (section 3).
* `paused` stops new starts; running requests carry on.

### 2.11 The catalogue the editor reads

`GET /api/_controls/workflows/catalogue` returns, for each model with `Meta.workflow`:

```json
{"model": "purchaseorder", "label": "Purchase order",
 "protects": [{"kind": "lifecycle", "operation": "confirm", "label": "Confirm"}],
 "executes": [{"kind": "lifecycle", "operation": "confirm", "label": "Confirm"}],
 "unmatched": "block",
 "facts": {
   "total": {"label": "Total", "type": "money", "source": "field"},
   "purchase_type": {"label": "Purchase type", "type": "enum", "source": "field",
                     "choices": [["equipment", "Equipment"], ["services", "Services"]]},
   "lines.count": {"label": "Number of lines", "type": "number", "source": "rows"},
   "requester.department": {"label": "Requester's department", "type": "enum", "source": "requester"},
   "fact.supplier_risk": {"label": "Supplier risk rating", "type": "enum", "source": "lookup"}},
 "people": [{"fact": "requester.manager", "label": "Requester's manager"}],
 "fields": [{"name": "required_by", "label": "Required by"}],
 "tables": [{"name": "lines", "label": "Lines"}],
 "harmless": ["remarks"],
 "roles": [{"role": "finance_approver", "label": "Finance approver", "review": true}]}
```

It lists only what the app declares. Choices of a Link are not listed; the editor searches them
through the normal Link search route with the administrator's own permissions.

### 2.12 Size limits

A definition is at most 256 KB of canonical JSON, 40 stages, 20 actions per stage, 20 reviews
per group and nesting depth 3. Automatic progression (a route straight into an `end` or a group
with `when_none: continue`) is bounded to 20 steps per transaction, and a cycle made only of
automatic steps is refused at publication.

### 2.13 Validation at publication

Publication is refused, with every problem listed by id, for: an unknown fact or operator, a
value of the wrong type, a missing `otherwise`, a destination that does not exist, a stage that
cannot be reached from `start`, a non-end stage with no way out, a review with nobody assigned, a
`count` above the number of reviews, `branch` rejection with `all`, field or table names the
model does not have, an `execute` the app does not list, a `protects` the app does not list, a
second published workflow for the same model and `protects` whose `applies_when` can match the
same record (see question 9), and an automatic cycle. Runtime checks still apply: publication
does not prove that every future request has an eligible manager.

## 3. Python declaration API

### 3.1 Opting in

```python
from evlet import Currency, Fact, Hierarchy, Model, Rule, Select, Text, WorkflowOptions

PEOPLE = Hierarchy('people.Employee', parent='reports_to')

class PurchaseOrder(Model):
    confirmable = True

    class Meta:
        workflow = WorkflowOptions(
            protects=("confirm",),
            executes=("confirm",),
            unmatched="block",
            facts={
                "lines.count": Fact.count("lines", label="Number of lines"),
                "supplier_risk": Fact(Select(("low", "medium", "high")), "Supplier risk rating",
                                      read="supplier_risk"),
            },
            people=PEOPLE,
            harmless=("remarks",),
        )

    purchase_type = Select(("equipment", "services", "other"))
    total = Currency(compute=...)
    remarks = Text(default="", editable_when_locked=True)

    def supplier_risk(self):
        return self.supplier.risk_rating if self.supplier else None

    permissions = {
        "finance_approver": Rule(("read", "review")),
        "purchasing": Rule(("read", "create", "update", "confirm")),
    }
```

`WorkflowOptions(protects, *, executes=(), unmatched, facts=None, people=None, assignable=(),
fields=None, harmless=(), tables=None)`:

| Argument | Meaning |
|---|---|
| `protects` | Operations a workflow may protect. `"confirm"`, `"cancel"`, `"reopen"` on a confirmable model; `"move:<state>"` for a Move target on a model with a State; `"action:<name>"` for a model action declared with `@action`. An empty tuple allows only operational workflows. |
| `executes` | Operations a workflow action may run (2.6 `execute`), same spelling. A workflow never calls anything else. |
| `unmatched` | Required, no default. `"block"`: a protected operation with no matching published workflow fails closed. `"allow"`: it runs as it does today. |
| `facts` | Extra facts conditions may read. Root fields of the model are facts automatically when they are stored, not masked or excluded from tracking, and of type text, select, state, boolean, integer, decimal, currency, date or Link. |
| `people` | A `Hierarchy` whose records accounts link to. It provides the person facts `requester.manager` and `requester.department` (when the hierarchy model has a `department` field). |
| `assignable` | Fields whose value can name an assignee (2.4 `field`). |
| `fields` | Field names a stage's Who can edit may list. Default: every writable root field. |
| `harmless` | Fields whose change does not invalidate approval evidence. Usually notes and `editable_when_locked` fields. |
| `tables` | Table names the edit rules may mention. Default: every Table at every depth. |

A model gets `Meta.workflow` or nothing. Configuring a workflow never makes an undeclared method
callable.

### 3.2 Facts

`Fact(field, label, *, read)` declares a read-only lookup. `field` is a field instance that gives
the type and choices (no calculations). `read` names a model method that takes only `self`, like a
validator: it runs on a read-only draft with a read-only context, under the writer lock, and its
result is cleaned by `field`. It must not call the network (the plan forbids network calls while
holding the writer lock). `Fact.count(table)`, `Fact.sum(table, field)`, `Fact.min(...)`,
`Fact.max(...)` and `Fact.any(table, condition)` declare owned-row aggregates and compile to the
existing finite aggregates.

Facts the reader cannot see are a permission matter (section 4.5): a condition may read a fact the
requester cannot read, but the preview and the reasons shown to a person never print a value that
person cannot read.

### 3.3 Requester and person facts

The requester is the account that ran the `submit` action of the current run. It is stored on the
run, not read from `evlet_created_by`. Requester facts are `requester` (the account),
`requester.role`, and the facts `people` adds. `requester.manager` resolves through the
hierarchy's parent Link and back to an account through `evlet_user_links`; no link, no manager,
and the stage blocks with a repair action. Plan section 5 rule 7 applies: never skip, never lower
a quorum.

### 3.4 Registration checks

Registration fails, naming the model and argument, when: `protects` or `executes` names an
operation the model does not have; a lifecycle name is used on a model that is not confirmable;
a `move:` target is not a State choice or is `void`; an `action:` name is not a model action;
`unmatched` is missing; a fact name collides with a field or has an unsupported type; `harmless`
names a field a stored result reads; `people` is not a `Hierarchy`; or the model is a `Child`.

### 3.5 Public names

New root exports, pinned in `tests/test_public_api.py` when built: `WorkflowOptions`, `Fact`.
The operation names `review` and `override` join the Rule vocabulary. Context methods are in
section 7.1. Everything else (the engine module, tables, the evaluator) is internal.

## 4. Permission extension

### 4.1 `review`

`review` is a new operation name, accepted by `Rule`, Controls permission overrides and the
`OPERATIONS` list in both places that hold it.

* It requires `read`, like every operation. It does not require or imply `update`.
* It is a capability, not an assignment. A decision needs all of: current read access to the
  record within the row scope, `review` on the model, being an eligible assignee of the open
  task (or its delegate, section 4.3), and the self-approval rule.
* Admin has `review` by default when its Rule reads the model, like the corrections. Other roles
  get it only when their Rule names it. Admin still has to be assigned; there is no admin
  exemption from a configured workflow.
* A Controls override can take `review` away from a role and can never add it beyond the file
  Rule. The Permissions page lists it with the other named operations.
* API keys: a decision is a write. The key must have write access and include the model, as for
  the corrections.
* `Meta.api = "read"` or `None` refuses decisions over HTTP like any other write.
* A model whose Rule names `review` without `Meta.workflow` fails at startup.

### 4.2 `override`

`override` lets a role force a stage or outcome on a run with required feedback (plan section 6).
It is never implied, not even for admin: a role has it only when the app file names it. A Controls
override can remove it. Each use is a decision of kind `override`, written to the journal and to
the Controls audit log.

### 4.3 Delegation

A delegation lets person B decide tasks assigned to person A for a period. B needs their own
`read` and `review`. The decision records both: `actor` B and `principal` A. Delegation never
adds a vote: a principal counts once whether they or a delegate decide. Who may create
delegations is question 7.

### 4.4 Effective edit rights

An ordinary write (HTTP create, update and delete, `Context.update`, child edits, attachments,
imports, the connector and Eva) is allowed only when every one of these allows it: the Rule and
row scope, field and child permissions, lifecycle and State locks, closed periods, the host API
scope, and the edit rules of every active stage execution of the open run (the containing stage
and each active review's stage). The stage limits intersect; any read-only rule wins. The check
reads the saved stage under the writer lock, so a body cannot claim a different stage. Ordinary
writes cannot touch engine state at all; it is not in the record.

### 4.5 What a person is told

Detail metadata (section 7.3) gives each person only the actions they may take now, the input
each needs, stage labels, lock explanations and safe pending summaries. Reasons never contain a
value of a field or fact the person cannot read: "This depends on values you cannot see" replaces
it, as `_moves` does today. A person without read access to the record gets 404 from every
workflow route, as from the record.

## 5. Adoption of lifecycle and State/Move

### 5.1 One controller per transition

For each model, each operation in `WorkflowOptions.protects` has one controller: the workflow
engine. Concretely:

* At most one published workflow is selected per (record, protected operation). Overlapping
  `applies_when` is a configuration conflict at publication, never a priority.
* A protected operation runs only with valid approval evidence: the current attempt of the run
  for that record and operation reached an `approved` end, the record's data generation still
  matches the attempt (section 6.3), and the evidence has not been used.
* The operation's own checks stay exactly as they are: confirm still needs `update` and
  `confirm`, a Move still needs its `roles` and `when`, an action its `roles` and
  `available_when`. Workflow adds a gate; it never replaces or relaxes one.
* Evidence is used once. Confirming consumes it. Reopen, cancel and a second confirm need their
  own evidence when they are protected, so approval for confirm is never reused to cancel or to
  confirm a new revision.

### 5.2 The central gate

The gate sits where the operation is decided, in the document write pipeline, not in the HTTP
route. So HTTP, `context.confirm`, `context.move`, `context.action`, legacy `Action` handlers
calling `context`, imports, jobs, the connector and Eva all pass through it. A custom action that
calls `context.confirm` hits the gate like anyone else.

The engine's own completion (`execute`) passes the gate with an execution-scoped grant: an
in-memory object bound to the exact record, operation, attempt and data generation, created and
consumed inside one transaction. It is never serialized, never accepted from a request and gone
when the transaction ends. Trusted raw SQL and `tx.maintenance()` stay outside the gate, as an
audited administrative boundary.

When the gate refuses:

* HTTP answers 409 `{"error": "Confirm needs approval. Send this purchase order for review first.", "code": "WORKFLOW_REQUIRED"}`.
  409, because the record's state, not the caller's role, blocks it (the same reasoning as
  `available_when`).
* `_lifecycle_actions`, `_moves` and `_actions` list the operation with `allowed: false` and that
  reason, so the screen never offers a button that will fail.
* With `unmatched="block"` and no matching workflow: 409
  `{"error": "Confirm is blocked: no approval workflow applies to this purchase order.", "code": "WORKFLOW_NONE"}`.

### 5.3 Lifecycle models

A confirmable model may protect `confirm`, `cancel` and `reopen`. The workflow stage is separate
from the lifecycle state: the header shows both ("Document: Draft" and "Stage: Approved"). The
engine never assigns lifecycle state and never writes a revision. In `enable` mode, approval
completes and the document reads "Approved, awaiting confirmation" until someone who may confirm
does so; a confirmation that then fails validation leaves the stage Approved and the document
Draft. In `execute` mode the decision, the transition, the confirm and its history are one
savepoint: a failed confirm rolls back the decision too, and the reviewer sees the validation
error.

The confirmation snapshot's `approval` member keeps working. When a workflow protected the
confirm, it holds the run id, attempt and decision ids instead of the latest `approved` entry.

### 5.4 State/Move models

A model with a State may protect Move targets (`"move:issued"`) and model actions. Rules:

* The workflow stage never writes the State. A workflow action changes the State only by
  `execute` of a Move the app lists in `executes`, through the normal move pipeline with its
  roles, guards, asks, locks and effects.
* Unlisted Moves are untouched. Existing State/Move consumers keep their routes, `_moves`,
  schema and behaviour exactly.
* An app that already models approval with Moves (draft, submitted, approved) adopts the engine
  deliberately: it lists the approval-ending Move in `protects` and removes or stops offering its
  hand-made approval Moves in the same release. Registration cannot see intent, so the upgrade
  guide gets a checklist. An app that does nothing keeps working unchanged.
* Void and revise stay outside workflow in 0.4.0. Voiding ends any open run of the record with
  reason "Record voided"; a revision copy starts with no run.

### 5.5 Operational workflows

`protects: null` workflows gate nothing. They track stages such as Inspection, On Hold and
Released, with assignments, edit rules, feedback and history, on any model with `Meta.workflow`,
confirmable or not. They may `execute` listed operations. One operational workflow per record at
a time.

### 5.6 Old approval events

`record_approval` keeps its signature and behaviour. Its `approved` and `rejected` journal
entries stay readable in history. The engine never reads them: evidence comes only from
`evlet_workflow_decisions` of the current attempt. A model's `check_lifecycle` keeps running
after the gate; an app adopting workflow should remove a `check_lifecycle` rule that requires a
`record_approval` entry, or both will be required. No deprecation in 0.4.0.

## 6. Runtime semantics this contract relies on

Plan sections 5 and 7 decide these; the contract only names what the API exposes.

### 6.1 Runs and attempts

A run is one pass of a record through one workflow version, for one protected operation. It
starts with the first `submit` and is pinned to the published version of that moment. An attempt
is one approval try inside a run; Request changes and withdrawal end it, and the next `submit`
starts a linked new attempt. Earlier decisions stay, marked as belonging to a superseded attempt,
and never count again.

### 6.2 Concurrency token

Decisions can change the run without changing a single field, so the record ETag alone cannot
detect them. Every run has a counter that rises on each change. The token is
`"<run id>.<counter>"`, opaque to clients. Every workflow mutation sends it in the body as
`token`; record writes do not need it.

### 6.3 Approval validity

Each record of a model with `Meta.workflow` has a monotonic data generation, raised by every
committed write that changes anything other than `harmless` fields: root fields, rows at any
depth (edit, add, remove, reorder, replace), attachments, and stored results. An attempt stores
the generation it started at and a SHA-256 fingerprint of the canonical non-sensitive values
(masked and excluded fields contribute only their generation, never their value). A change of
generation during an attempt ends it with reason "The purchase order changed after it was sent"
and closes its open tasks in the same transaction. Lookup facts are frozen at stage entry; an
explicit refresh ends the attempt.

## 7. Service operations and HTTP

### 7.1 Python

```python
with app.business.context(user, source="Integration") as context:
    wf = context.workflow(PurchaseOrder, record_id)
    wf.status()                                   # same shape as GET .../workflow
    wf.decide("act_mapprv", feedback="Checked against budget", key="import-7781")
    wf.decide(kind="approve", review="rev_finan1", key="import-7782")
    wf.withdraw(feedback="Wrong supplier", key="...")
    wf.refresh(feedback="Manager changed", key="...")
    wf.hold(feedback="Waiting for quote", key="..."); wf.resume(key="...")
    wf.reassign(task_id, to=user_id, feedback="On leave", key="...")
    wf.override(to_stage="stg_apprv1", feedback="Board approved on 06-11-2026", key="...")
```

* `context.workflow(Model, id)` returns a handle bound to the live context, like
  `context.document`. It needs read access.
* `decide(action_id=None, *, kind=None, review=None, feedback=None, key=None)` takes the action
  id, or `kind` when exactly one action of that kind is open to the caller. `review` picks the
  review when the caller is eligible for more than one.
* Starting is the `submit` action of the start stage: `wf.decide(kind="submit")`. There is no
  separate start call.
* Python callers skip the HTTP preconditions (ETag, token) but not the checks: permissions,
  assignment, validity, feedback and the writer lock all apply. `key` is optional in Python and
  required over HTTP.
* Errors are the usual `AccessError` (403/404), `ConflictError` (409) and `ValidationError`
  (422).

### 7.2 Routes

Record routes (any host the model is served on, desk or portal, within the host API scope):

| Route | Body | Does |
|---|---|---|
| `GET /api/{model}/{id}/workflow` | | Run status, current stage, tasks, earlier attempts, future preview, allowed actions, token |
| `POST /api/{model}/{id}/workflow/actions/{action_id}` | `{key, token, review?, feedback?}` | Decide, or submit from a work stage |
| `POST /api/{model}/{id}/workflow/withdraw` | `{key, token, feedback?}` | Requester withdraws; open tasks close |
| `POST /api/{model}/{id}/workflow/refresh` | `{key, token, feedback}` | End the attempt and re-resolve assignees and facts |
| `POST /api/{model}/{id}/workflow/hold` and `/resume` | `{key, token, feedback}` | Pause and resume deadlines and decisions on this run |
| `POST /api/{model}/{id}/workflow/tasks/{task_id}/reassign` | `{key, token, to, feedback}` | Workflow administrators repair an assignment |
| `POST /api/{model}/{id}/workflow/override` | `{key, token, to_stage or outcome, feedback}` | `override` operation |
| `POST /api/{model}/{id}/workflow/decisions/{decision_id}/clarify` | `{key, text}` | The decision's author appends a clarification |
| `GET /api/{model}/{id}/workflow/attempts/{n}` | | One earlier attempt with its decisions |

Inbox and delegation:

| Route | Does |
|---|---|
| `GET /api/_workflow/tasks?view=mine\|requested\|team&limit=50&offset=0` | Paged task list. `team` uses the viewer's hierarchy team scope. Rows the viewer can no longer read are left out. |
| `GET /api/_workflow/delegations`, `POST`, `DELETE /api/_workflow/delegations/{id}` | Delegations (question 7) |

Administration (primary host, Controls access, see question 5):

| Route | Does |
|---|---|
| `GET /api/_controls/workflows/catalogue` | Section 2.11 |
| `GET /api/_controls/workflows`, `POST` | List; create a draft from the basic template |
| `GET /api/_controls/workflows/{id}` | Draft, published version, status |
| `PUT /api/_controls/workflows/{id}/draft` | Save the draft. Body `{definition, revision}`; `revision` guards concurrent editors (409 when stale) |
| `POST /api/_controls/workflows/{id}/validate` | Problems by id, as the editor shows them |
| `POST /api/_controls/workflows/{id}/preview` | Try example: `{record}` or `{values, requester}`. Uses the evaluator; writes nothing |
| `POST /api/_controls/workflows/{id}/check-access` | `{user, stage}`: what that person may do there, in words and as flags |
| `POST /api/_controls/workflows/{id}/publish` | `{revision, notes}`; returns the change summary and the new version |
| `POST /api/_controls/workflows/{id}/pause`, `/resume` | New starts only |
| `GET /api/_controls/workflows/{id}/versions`, `/versions/{n}` | Immutable versions |

Preview needs read access to the supplied record and to every fact it reads; a fact the
administrator cannot read shows as "hidden from you" and the route through it as undecided.

### 7.3 Detail and schema additions

Record details of a model with `Meta.workflow` carry `_workflow`, outside the ETag like `_moves`:

```json
"_workflow": {
  "token": "run_01hd2a9.7",
  "stage": {"id": "stg_deprt1", "label": "Department Review", "status": "active"},
  "summary": "Awaiting Finance and Procurement · Manager approved",
  "attempt": 1,
  "actions": [
    {"id": "act_dapprv", "label": "Approve", "kind": "approve", "review": "rev_finan1",
     "review_label": "Finance", "feedback": "optional", "effect": "Counts towards Department Review"},
    {"id": "act_dreject", "label": "Reject", "kind": "reject", "review": "rev_finan1",
     "review_label": "Finance", "feedback": "required", "effect": "Goes to Rejected"}],
  "edit": {"allowed": false, "reason": "Locked during Department Review. Nobody edits at this stage."},
  "withdraw": false
}
```

`/api/schema` entries gain `workflow: {"protects": [...], "unmatched": "block"}` for models with
`Meta.workflow`, and `review` appears in the role's operations. A role without read access to the
State or to a field a reason would mention gets the safe reason.

### 7.4 Mutation sequence

Every workflow mutation, from any caller:

1. Authenticate, resolve tenant, host and API scope, verify the account is active.
2. Acquire the writer lock (`BEGIN IMMEDIATE` on SQLite, the advisory lock on PostgreSQL) and a
   savepoint, before reading run state.
3. Look up the receipt for `key` (namespaced `"{user}:{model}:{id}:workflow:{key}"` in
   `evlet_operations`). Same key and same request: return the stored result with 200, even if the
   ETag or token is old. Same key, other request or other actor: 409.
4. Check `If-Match` (428 when missing, 409 when stale) and `token` (428 when missing, 409 when
   stale), then read access and row scope (403 or 404), `review` or the operation's permission,
   assignment and delegation, self-approval, the source stage, the action's `when`, feedback,
   and approval validity.
5. Record the decision; settle the review and group; follow routes with bounded automatic
   progression; run any `execute` through its normal pipeline.
6. Write projections, the decision, its journal entry, the receipt, record events and
   notification intents, then commit. Answer with the permission-filtered detail, a new ETag and
   the new token.

### 7.5 Errors

Workflow routes keep the existing body and add a stable `code`:

| Status | `code` | When | Example `error` |
|---|---|---|---|
| 428 | `PRECONDITION_REQUIRED` | No `If-Match`, or no `token` | `A record revision is required` / `A workflow token is required` |
| 409 | `RECORD_CHANGED` | `If-Match` is stale | `This record changed since you opened it. Review the latest version.` |
| 409 | `WORKFLOW_CHANGED` | `token` is stale, or the task was closed or decided meanwhile | `This request changed. Review the latest status.` |
| 409 | `WORKFLOW_BLOCKED` | The run is blocked (missing eligible person, undecided condition, unsupported value) | `Nobody is eligible for Department manager. An administrator must repair the assignment.` |
| 409 | `WORKFLOW_REQUIRED` | A protected operation without valid evidence | `Confirm needs approval. Send this purchase order for review first.` |
| 409 | `WORKFLOW_NONE` | `unmatched="block"` and no workflow applies | `Confirm is blocked: no approval workflow applies to this purchase order.` |
| 409 | `WORKFLOW_PAUSED` | Submit while new starts are paused | `New requests are paused for this workflow.` |
| 409 | `IDEMPOTENCY_MISMATCH` | Key reused with other input | `Idempotency key has different input` |
| 403 | `FORBIDDEN` | No `review`, not assigned, self-approval, no `override` | `You cannot decide on this request` |
| 404 | `NOT_FOUND` | No read access, row scope, unknown action or task | `Record not found` |
| 422 | (fields) | Feedback missing, whitespace only, too long, NUL, unknown keys, missing `key` | `{"error": "Validation failed", "fields": {"feedback": "Enter feedback"}}` |

A role that cannot read the State, or a field a reason would reveal, gets the generic text and
`WORKFLOW_CHANGED` or `FORBIDDEN`, never a reason that names hidden values. An unknown action id
and an action the caller may not see are both 404, so ids do not reveal stages.

Feedback is trimmed; empty after trimming fails a required feedback; it must be valid Unicode
without NUL and at most `max_length` characters; the whole body is limited to 16 KB. It is stored
as plain text and always rendered escaped.

## 8. Storage sketch

Design only. Framework tables are created idempotently at startup on SQLite and PostgreSQL, like
the journal and lifecycle tables. Records are referenced as `(model, record_id)` with the same
types as `evlet_journal`. Accounts are `REFERENCES evlet_users(id)`. Each tenant has its own
database, so every foreign key is tenant-local. No columns are added to app models.

| Table | Columns | Keys and indexes | Mutability |
|---|---|---|---|
| `evlet_workflows` | `id TEXT PK`, `model`, `protects TEXT` (`confirm`, `move:issued`, `action:x` or `''`), `name`, `status` (`draft`, `published`, `paused`, `archived`), `published_version INTEGER NULL`, `draft TEXT` (JSON), `draft_revision INTEGER`, `changed_by`, `changed_at` | index `(model, protects, status)` | Mutable, every change in `evlet_audit` (area `Workflows`) |
| `evlet_workflow_versions` | `workflow_id`, `version`, `definition TEXT`, `hash`, `published_by`, `published_at`, `notes` | PK `(workflow_id, version)` | Append-only |
| `evlet_workflow_runs` | `id INTEGER PK`, `model`, `record_id`, `workflow_id`, `version`, `protects`, `requester`, `status` (`draft`, `active`, `blocked`, `held`, `approved`, `rejected`, `closed`, `ended`), `stage_id`, `attempt_id`, `counter INTEGER`, `started_at`, `ended_at` | FK `(workflow_id, version)`; unique `(model, record_id, protects)` where status is not ended (partial index on both databases); index `(model, record_id)` | Projection: mutable |
| `evlet_workflow_attempts` | `id INTEGER PK`, `run_id`, `n`, `status`, `generation`, `fingerprint`, `previous_id`, `started_at`, `ended_at`, `end_reason`, `evidence_used_at` | unique `(run_id, n)` | Projection: mutable |
| `evlet_workflow_record_data` | `model`, `record_id`, `generation INTEGER` | PK `(model, record_id)` | Mutable, raised in the write transaction |
| `evlet_workflow_executions` | `id INTEGER PK`, `run_id`, `attempt_id`, `stage_id`, `parent_id`, `review_id`, `occurrence`, `status`, `routing TEXT` (frozen facts and activated branch ids), `entered_at`, `left_at` | index `(run_id, attempt_id)`; index `(attempt_id, status)` | Projection: mutable |
| `evlet_workflow_tasks` | `id INTEGER PK`, `execution_id`, `review_id`, `required TEXT`, `status` (`pending`, `approved`, `rejected`, `changes`, `skipped`, `closed`), `due_at`, `closed_reason` | index `(execution_id)`; index `(status, due_at)` | Projection: mutable |
| `evlet_workflow_assignees` | `task_id`, `user_id`, `via` (`role`, `user`, `fact`, `field`, `delegate`), `principal_id`, `active INTEGER` | PK `(task_id, user_id)`; index `(user_id, active, task_id)` for My reviews | Projection: mutable |
| `evlet_workflow_decisions` | `id INTEGER PK`, `run_id`, `attempt_id`, `execution_id`, `task_id`, `action_id`, `kind`, `actor`, `principal`, `delegation_id`, `role`, `feedback`, `clarifies INTEGER NULL`, `at`, `version`, `generation`, `correlation_id`, `source`, `client`, `journal_id` | unique `(task_id, principal)` where `clarifies` is null and kind is a vote; index `(run_id, id)` | Append-only |
| `evlet_workflow_delegations` | `id INTEGER PK`, `from_user`, `to_user`, `scope TEXT`, `starts_at`, `ends_at`, `authorized_by`, `created_at`, `revoked_at`, `revoked_by` | index `(to_user, ends_at)` | Mutable only to revoke, audited |

Receipts reuse `evlet_operations`. Decisions also write a journal entry (kinds `submitted`,
`decided`, `withdrawn`, `overridden`, `clarified`, `stage`), so the record's Activity shows them
with the existing permission filtering; the decisions table is the evidence the engine reads.

Append-only guards follow the journal exactly:

```sql
-- SQLite, for evlet_workflow_decisions and evlet_workflow_versions
CREATE TRIGGER IF NOT EXISTS evlet_workflow_decisions_no_update BEFORE UPDATE ON evlet_workflow_decisions
BEGIN SELECT RAISE(ABORT, 'Workflow decisions are append only'); END;
CREATE TRIGGER IF NOT EXISTS evlet_workflow_decisions_no_delete BEFORE DELETE ON evlet_workflow_decisions
BEGIN SELECT RAISE(ABORT, 'Workflow decisions are append only'); END;
CREATE TRIGGER IF NOT EXISTS evlet_workflow_decisions_no_replace BEFORE INSERT ON evlet_workflow_decisions
WHEN EXISTS (SELECT 1 FROM evlet_workflow_decisions WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'Workflow decisions are append only'); END;
```

```sql
-- PostgreSQL
CREATE OR REPLACE FUNCTION evlet_workflow_append_only() RETURNS trigger LANGUAGE plpgsql
AS $$ BEGIN RAISE EXCEPTION 'Workflow history is append only'; END $$;
CREATE TRIGGER evlet_workflow_decisions_append_only BEFORE UPDATE OR DELETE ON evlet_workflow_decisions
FOR EACH ROW EXECUTE FUNCTION evlet_workflow_append_only();
CREATE TRIGGER evlet_workflow_decisions_no_truncate BEFORE TRUNCATE ON evlet_workflow_decisions
FOR EACH STATEMENT EXECUTE FUNCTION evlet_workflow_append_only();
```

The same triggers protect `evlet_workflow_versions`. Projections are mutable but must always
reconcile to decisions and versions; phase 7 adds a reconcile check for operators. No route,
connector, MCP tool or model writes any of these tables. Someone who can drop triggers is outside
the boundary, as for the journal.

Performance notes for phase 2: My reviews reads `evlet_workflow_assignees` by
`(user_id, active)` and joins tasks and runs, then checks read access in batches per model; no
journal scan authorizes a decision; the record detail's `_workflow` costs at most two indexed
statements.

## 9. Notification intents

The engine does not send anything itself. In the decision's transaction it records notification
intents, which the 0.4.0 notifications work consumes. Until that lands, a minimal consumer writes
`evlet_inbox` rows.

```python
@dataclass(frozen=True)
class NotificationIntent:
    kind: str             # see below
    recipients: tuple     # account ids, resolved at commit
    model: str
    record_id: object
    link: dict            # {"kind": "record", "model": "purchaseorder", "id": "<public id>"}
    label: str            # "PO-2026-00042 needs your review"
    dedupe: str           # "workflow:<task id>:assigned"
    task_id: int | None
    decision_id: int | None
    due_at: str | None
```

| `kind` | Recipients | When |
|---|---|---|
| `workflow.assigned` | Eligible assignees of a new task | A task opens |
| `workflow.decided` | The requester | A decision is recorded |
| `workflow.changes_requested` | The requester | Request changes |
| `workflow.completed` | The requester and, in `enable` mode, people who may run the protected operation (bounded list) | Approved or rejected |
| `workflow.closed` | Assignees of tasks closed as no longer required | Sibling cancellation, withdrawal, invalidation |
| `workflow.blocked` | Workflow administrators | Nobody eligible, undecided condition |
| `workflow.reminder`, `workflow.escalated` | Assignees, then the escalation assignee | Scheduler (section 10) |

* `link` uses the agreed inbox shape: `{"kind": "record", "model", "id"}` plus a display `label`.
  The inbox checks it against the reader's permissions when it is read; if the reader cannot open
  the record, `link` is left out. A task never grants record access.
* Intents carry no field values. The label uses the record's title only when the recipient can
  read it at delivery; otherwise "A purchase order needs your review".
* `dedupe` keeps at-least-once delivery from duplicating. When a task closes, its pending
  reminders become no-ops (the job checks the task status before sending), and unread inbox items
  for it are hidden.
* Each recipient's `notify.<kind>` preference applies, as for mentions.

## 10. Scheduler

Reminders, escalation and expiry run as jobs through the same service with system authority and
an audit row: `workflow.remind` (dedupe `workflow:<task>:remind:<n>`), `workflow.escalate` and
`workflow.expire`. Expiry never counts as approval: it ends the attempt or blocks, as the
definition says. Immediate decisions never need a worker. A delegation that expired or an
assignee who was deactivated is reported for repair; the engine never picks someone else.

## 11. What each later phase builds from this

| Phase | Builds |
|---|---|
| 2 Storage and evaluator | Section 8 tables and guards, the definition validator (2.13), condition compiler to finite-v1, assignment resolution, routing and quorum on real SQLite, PostgreSQL validation |
| 3 Enforcement and audit | `review` and `override` in Policy and Controls, the central gate and grant (5.2), stage edit checks (4.4), generations and invalidation (6.3), receipts, feedback rules, journal kinds |
| 4 Configuration editor | Controls Workflows page and admin routes, as in the prototype's editor |
| 5 Runtime screens | `_workflow`, header and panel, inbox views, decision dialogs, conflict handling |
| 6 Advanced | Nested reviews (`stages` in a review), delegation, deadlines, reminders, escalation, hold, override |
| 7 Hardening | Load scenarios, indexes, upgrade and restore, connector coverage, operator guidance |

## 12. Open questions, with recommended answers

The owner can approve these in one pass. Each recommendation is what the contract above assumes.

1. **Name of the declaration.** `Meta.workflow = WorkflowOptions(...)`, plus `Fact`.
   *Recommended: yes.* "Workflow" alone would clash with the configured document of the same name.
2. **Name of the decision capability.** `review` (alternatives: `decide`, `approve`).
   *Recommended: `review`.* It reads well in a Rule and matches the "My reviews" inbox.
3. **Admin and `review`.** Admin has `review` by default when it reads the model, but must still
   be assigned. *Recommended: yes.* Same pattern as the corrections; assignment is the real gate.
4. **`override` is never implied.** Even admin needs it named in the app file.
   *Recommended: yes.* The plan forbids a silent admin exemption.
5. **Who edits workflow definitions.** On the primary host, in Controls, by roles with a new role
   flag "Manages workflows" (admin always). Audited in `evlet_audit`. *Recommended: yes.* Keeps it
   apart from reviewer eligibility, as the plan asks, without a new Rule operation.
6. **When a run starts.** At the first Send for review, not at record creation. Records created
   earlier start when first sent. *Recommended: yes.* No runs for drafts nobody sends.
7. **Who creates delegations.** Each person for themselves (to someone who has their own access),
   and workflow managers for anyone, with a required reason. *Recommended: yes*, built in phase 6.
8. **`unmatched` has no default.** An app must write `"block"` or `"allow"`.
   *Recommended: yes.* Fail-closed versus open is a business decision the plan gives to the app.
9. **Overlap check at publication.** Two published workflows for the same model and operation may
   not both match. Exact overlap detection on arbitrary conditions is hard, so publication refuses
   a second one unless both have `applies_when` on the same enum field with disjoint values, and a
   runtime double match blocks with `WORKFLOW_BLOCKED`. *Recommended: yes.* Simple, honest, safe.
10. **Error `code` key.** Add `code` to workflow error bodies only; existing routes keep their
    body. *Recommended: yes.* Additive, and the UI needs to tell "changed" from "blocked".
11. **Token in the body, not a header.** `token` sits next to `key` in the JSON body.
    *Recommended: yes.* Matches how `key` already works and avoids a new CORS-relevant header.
12. **Idempotency key required over HTTP, 422 when missing.** Same as dialogs and slips.
    *Recommended: yes.* The plan's 428 is kept for `If-Match` and `token`.
13. **What invalidates approval.** Any change to non-harmless data raises the generation and ends
    the attempt; the app lists harmless fields. Field-by-field "relevant change" rules wait.
    *Recommended: yes for 0.4.0.* Safer and simpler; the plan's default.
14. **Requester's manager.** Comes only from a declared `Hierarchy` and account links; no manager
    blocks with a repair action. *Recommended: yes.* No new people model in the framework.
15. **Team reviews.** The Team tab uses the viewer's hierarchy team scope over requesters.
    *Recommended: yes.* Reuses the existing `Hierarchy.team()` SQL.
16. **Void and revise.** Out of workflow in 0.4.0; voiding ends an open run.
    *Recommended: yes.* Avoids a second controller over corrections.
17. **Nested reviews.** Schema slot (`stages` inside a review) fixed now, built in phase 6.
    *Recommended: yes.* No migration later.
18. **`record_approval`.** Keep, document as "audit only, not a grant", no deprecation in 0.4.0.
    *Recommended: yes.*
19. **Business-day deadlines.** Elapsed hours until the calendar engine ships working schedules;
    then `deadline: {"business_hours": n, "calendar": "<id>"}`. *Recommended: yes.*
20. **Bulk decisions.** Not in 0.4.0. *Recommended: yes*, as the plan says.

## 13. How the prototype maps to this contract

| Journey in the prototype | Contract sections |
|---|---|
| 1 Manager-only approval (expense claim) | 2.3, 2.4 `fact: requester.manager`, 5.3 `enable` mode |
| 2 Conditional route above AED 100,000.00 | 2.5 `on_complete`, 2.7 `first_match`, 2.8 money comparison |
| 3 Parallel Department Review | 2.5 `run: together`, `complete: all`, conditional reviews, 7.5 `WORKFLOW_CHANGED` |
| 4 Request changes, then resubmit | 2.6 `request_changes`, 6.1 attempts, 6.3 invalidation |
| 5 Locked document, read-only reviewer | 4.1 `review` without `update`, 4.4, 4.5 |
| 6 Workflow editor | 2.1 to 2.13, 7.2 administration routes |

The prototype imitates the backend in the browser so the journeys can be clicked. In the real
system the browser never evaluates conditions or decides access; it shows what the server returns.
