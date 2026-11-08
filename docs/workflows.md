# Approval workflows

Status: in progress for 0.4.0. This page describes what is built so far: the declaration, the
`review` permission, stored definitions, the engine that routes requests and counts decisions, and
enforcement: the gate on protected operations, stage edit rules, approval invalidation, required
feedback, the journal entries and the HTTP routes that send, decide and withdraw. There is no
configuration editor and there are no workflow screens yet. The full plan is
[docs/internal/workflow.md](internal/workflow.md) and the contract is
[docs/internal/workflow-contract.md](internal/workflow-contract.md).

## Opting a model in

A model is configurable only when it declares `Meta.workflow`:

```python
from evlet import Currency, Fact, Hierarchy, Model, Rule, Select, Table, Text, WorkflowOptions

PEOPLE = Hierarchy('employee', parent='reports_to')

class PurchaseOrder(Model):
    confirmable = True

    class Meta:
        workflow = WorkflowOptions(
            protects=("confirm",),
            executes=("confirm",),
            unmatched="block",
            facts={"lines.count": Fact.count("lines", label="Number of lines"),
                   "supplier_risk": Fact(Select(("low", "high")), "Supplier risk", read="supplier_risk")},
            people=PEOPLE,
            harmless=("remarks",),
        )

    purchase_type = Select(("equipment", "services", "other"))
    total = Currency(default="0")
    remarks = Text(default="")
    lines = Table(Line)

    def supplier_risk(self):
        return self.risk_rating or None

    permissions = {
        "finance_approver": Rule(("read", "review")),
        "purchasing": Rule(("read", "create", "update", "confirm")),
    }
```

`WorkflowOptions(protects, *, executes=(), unmatched, facts=None, people=None, assignable=(),
fields=None, harmless=(), tables=None)`:

| Argument | Meaning |
|---|---|
| `protects` | Operations a workflow may protect: `"confirm"`, `"cancel"`, `"reopen"` on a confirmable model, `"move:<state>"` for a Move target, `"action:<name>"` for an `@action`. An empty tuple allows only operational workflows. |
| `executes` | Operations a workflow action may run, spelled the same way. |
| `unmatched` | Required. `"block"`: a protected operation with no matching workflow fails closed. `"allow"`: it runs as it does today. |
| `facts` | Extra values conditions may read (below). |
| `people` | A `Hierarchy` whose records accounts link to. It gives the person fact `requester.manager` and, when the hierarchy model has a `department` field, the fact `requester.department`. |
| `assignable` | Links to the hierarchy model whose linked account can be assigned. |
| `fields`, `tables` | What a stage's edit rules may name. Default: every writable field and every Table. |
| `harmless` | Fields whose change will not invalidate approval. A field a stored result reads cannot be harmless. |

Registration fails, naming the model and argument, for an operation the model does not have, a
lifecycle name on a model that is not confirmable, a Move target that is not a state or is `void`,
an action that is not an `@action`, a missing `unmatched`, a fact that collides with a field, a
`people` that is not a `Hierarchy`, and `Meta.workflow` on a Table row model.

## Facts

Conditions read typed facts, never Python or SQL. A model's stored root fields are facts when they
are text, select, state, boolean, integer, decimal, percent, currency, date or Link fields, tracked
by value (not masked or excluded). The app adds:

* `Fact(field, label, read="method")`: a read-only lookup. `field` gives the type and choices;
  the model method takes only `self` and runs on the saved record when a stage starts. It must not
  call the network. The fact is named `fact.<name>` in conditions.
* `Fact.count(table)`, `Fact.sum(table, field)`, `Fact.min(table, field)`, `Fact.max(table, field)`
  and `Fact.any(table, where)`: aggregates over the record's own rows, named `<table>.<name>`.
  `where` is a finite condition over the row's fields, such as `F("urgent") == True`.
* `requester` (the account that sent the request) and `requester.role`, always.

Money and decimals compare exactly. A fact with no value, or one that fails to read, makes a
condition undecided. Undecided never counts as false: the request blocks and the message names
the facts, never their values.

## The `review` permission

`review` joins the Rule vocabulary. It lets a role decide workflow tasks it is assigned to:

* It needs `read` and does not need or imply `update`. A reviewer can approve a document they
  cannot edit.
* Admin has it by default on models with `Meta.workflow` when its Rule reads the model. Other roles
  have it only when their Rule names it. Being able to review is not being assigned: admin still
  has to be assigned like anyone else.
* A Controls override can take it away and never add it. An API key needs write access and must
  include the model.
* A Rule that names `review` on a model without `Meta.workflow` fails at startup.

## Definitions and versions

A workflow definition is the neutral JSON document of the contract, section 2: stages with stable
ids, actions, routes, conditions, review groups, edit rules and settings. The engine checks a
definition when it is published and lists every problem by id: unknown facts and operators,
values of the wrong type, missing Otherwise destinations, stages nothing leads to, stages with no
way out, reviews nobody can be assigned to, quorums above the number of reviews, operations the
app does not list, cycles of automatic steps, and the size limits (256 KB, 40 stages, 20 actions
per stage, 20 reviews per group, conditions 4 groups deep with 50 rows, 1,000 condition rows).

Published versions are immutable. Each publication adds a row to `evlet_workflow_versions`, which
database triggers protect against UPDATE, DELETE (and TRUNCATE on PostgreSQL), and a request stays
on the version it started with. A second workflow for the same model and operation is accepted
only when both choose records by the same choice fact with no value in common; a record that two
workflows match anyway blocks.

The `stages` slot inside a review is reserved for nested reviews and must be empty for now.

## How a request moves

A run starts with the first Send and belongs to one record, one protected operation and one
published version. When a stage starts, the engine reads the facts it needs from the saved record,
decides its requirement, which actions are offered, where each route goes and which reviews
apply, and resolves the people assigned. It stores all of that with the stage execution, so the
request follows the same branches whatever changes later in the attempt.

* **Routes** go to a stage, or to the first matching destination with a required Otherwise. A
  destination whose condition cannot be decided blocks.
* **Review groups** run together or in sequence and complete when all, any or a number of the
  reviews that apply approve. Skipped reviews are never waited for. A rejection is a veto by
  default, or fails only its own review with `rejection: "branch"`. When the group completes, the
  other open reviews close as no longer required, and a decision on one of them is refused.
* **Zero reviews** that apply block, unless the group says `when_none: "continue"`.
* **Within a review** one person, all eligible people, or a number of distinct people decide. A
  person counts once per stage: after deciding one review they leave the others. If that leaves a
  review that cannot reach its quorum, the request blocks for repair instead of lowering it.
* **Eligibility** is resolved when the stage starts: active accounts that can read the record, hold
  `review`, and are not the requester when self-approval is blocked. It is checked again at each
  decision. Nobody eligible blocks with a repair message; the engine never picks someone else.
* **Request changes** ends the attempt and returns to a work stage; the next Send starts a linked
  new attempt, and earlier approvals never count again. Repeated stages get new executions.
* Automatic steps are bounded to 20 per transaction.

Decisions are append-only rows in `evlet_workflow_decisions`, guarded by the same triggers as the
journal. Runs, attempts, stage executions, tasks and assignees are projections the engine keeps in
step with them in the same transaction. The tables are created at startup when a model declares
`Meta.workflow`; app tables get no columns.

## Tables

| Table | Holds |
|---|---|
| `evlet_workflows` | Each workflow: model, operation, status and the draft being edited |
| `evlet_workflow_versions` | Published definitions (append-only) |
| `evlet_workflow_runs` | One record's pass through one version; one open run per record and operation |
| `evlet_workflow_attempts` | Approval tries inside a run, linked to the one before |
| `evlet_workflow_record_data` | The record's data generation, raised by every write that changes non-harmless data |
| `evlet_workflow_executions` | Each time a stage starts, with its frozen routing |
| `evlet_workflow_tasks` | One per review (or per single-reviewer stage) with its quorum |
| `evlet_workflow_assignees` | The people each task resolved to |
| `evlet_workflow_delegations` | Reserved for delegation, built in a later phase |
| `evlet_workflow_decisions` | Every decision (append-only) |

## The gate on protected operations

An operation the app lists in `WorkflowOptions.protects` runs only with approval evidence: the latest
run of the record for that operation reached an `approved` end, its evidence was not used, and the
saved data still matches what was approved (the record's data generation and a SHA-256 fingerprint
of its non-sensitive values, rows and attachment list). The operation's own checks stay exactly as
they are and run first: confirm still needs `update` and `confirm`, a Move its roles and guards, an
action its roles and `available_when`.

The gate sits in the document pipeline, not in a route, so every caller meets it: HTTP,
`context.confirm`, `context.move`, `context.action`, a custom action that calls `context`, jobs and
scheduled work, the connector and Eva. Raw `Session` writes to a workflow model are refused; only
`tx.maintenance()`, the audited administrative boundary, writes around the pipeline (and the
fingerprint still notices such a change before the next protected operation).

* **Evidence is used once.** The operation that succeeds uses it in its own transaction; if the
  operation then fails (a validator, a posting), the use rolls back with it and the evidence stays.
  Approval for confirm never serves cancel, reopen or a later confirm: each needs its own run when
  it is protected. Rejected, ended and withdrawn runs grant nothing, and `record_approval` events are
  never read: they stay audit only.
* **The confirmation snapshot** records `{"workflow": {"run", "workflow", "version", "attempt",
  "decisions"}}` as its `approval` member when a workflow protected the confirm.
* **Refusals** are 409 with a `code`: `WORKFLOW_REQUIRED` ("Confirm needs approval. Send this
  purchase order for review first."), `WORKFLOW_NONE` when `unmatched="block"` and no workflow applies,
  and `WORKFLOW_BLOCKED` when it cannot be decided which workflow applies. With `unmatched="allow"` an
  operation no workflow applies to runs as it did before; an open run for the operation still needs
  its approval.

## Stage edit rules

While a run is open, every write to the record meets the edit rules of its current stage, in each
open run (an approval run and an operational run intersect). Workflow rules narrow what the app's
permissions, row scopes, locks and closed periods allow; they never widen them.

* **Who can edit:** `nobody` (the default for review and operational stages), `requester` (the
  default for work stages), `assigned` (people with an open task at the stage), `selected` (the
  people listed) or `existing` (everyone who may already update the record). There is no admin
  exemption.
* **Fields:** `all` or `only` the listed names. A rule that names no fields lets nobody change one.
  Computed and read-only fields and the State do not count as edits.
* **Rows:** each Table at any depth (`lines`, `lines.serials`) has `edit`, `add`, `remove` and
  `reorder`; a table the rule does not name allows none of them. Replacing a whole list is a removal
  and an addition. Adding a row also adds its own rows.
* **Attachments:** `add` and `remove`; a rule without them allows neither. A rich text image upload
  only needs the person to be among who can edit.
* **Custom actions are edits:** an action that changes fields meets the same rules.
* **Deleting** a record is refused while a request awaits review; at a work stage the person must be
  among who can edit, and the open run ends with the record.

A refused write is 409 `WORKFLOW_LOCKED` with a reason such as "Locked during Manager Review.
Nobody edits at this stage." Field names appear only when the person can read them. Engine state is
not in the record, so a body cannot choose a stage: workflow routes refuse any key they do not take.

## Approval validity

Every committed write that changes anything other than the app's `harmless` fields raises the
record's data generation: root fields, rows at any depth (edit, add, remove, reorder, replace),
stored results, the State and attachments. In the same transaction:

* an attempt awaiting review ends ("The purchase order changed after it was sent for review."), its
  open tasks close, and the run returns to its start stage in a linked new attempt;
* approval evidence not yet used is voided and its run ends, so the protected operation needs a new
  Send.

Earlier decisions stay, marked as belonging to a superseded attempt.

## Decisions, feedback and history

* **Feedback** follows each action's rules (`hidden`, `optional`, `required`, `max_length` up to
  4,000). Reject and Request changes always require it. It is trimmed and normalized to NFC; text
  with only whitespace is none; lone surrogates, NUL and other control characters except tab and line
  breaks are refused; it is stored and returned as plain text, never HTML.
* **Sending** a record for review needs `update` access to it; deciding needs `review`, read access
  and an open task; withdrawing is for the requester, with `update`, while the request awaits review.
  Each decision checks again that the account is active.
* **Atomic history:** each decision, Send and withdrawal writes a journal entry (`submitted`,
  `decided`, `withdrawn`, and `stage` when the run moves to another stage or ends) and then the
  append-only decision row that names it, in one transaction. Any failure rolls both back, and the
  `context.workflow` handle uses a savepoint so trusted code that catches the error still leaves
  nothing behind. A model with `Meta.workflow` always journals; `Meta.journal = None` is refused.
* Decision rows record the actor, role, feedback, attempt, data generation, source (User, API,
  Integration...), client (HTTP, Python, or the AI assistant) and correlation id.

## Python

```python
with app.business.context(user, source="Integration") as context:
    wf = context.workflow(PurchaseOrder, record_id)          # needs read access
    wf.status()                                             # same shape as GET .../workflow
    wf.decide(kind="submit", key="import-7781")             # Send for review
    wf.decide("act_mapprv", feedback="Within budget")       # by action id
    wf.decide(kind="approve", review="rev_finan1")          # one of several reviews you may decide
    wf.withdraw(feedback="Wrong supplier")
```

`protects=` picks the operation when a record has several. Python skips the ETag and token but
none of the checks. `key` is optional in Python; with a key, a repeated call returns the first
result and the same key with other input is refused (`IDEMPOTENCY_MISMATCH`).

## HTTP

| Route | Body | Does |
|---|---|---|
| `GET /api/{model}/{id}/workflow[?protects=confirm]` | | Status: stage, tasks, decisions with feedback, the actions you may take now, `withdraw`, `evidence` and the `token` |
| `POST /api/{model}/{id}/workflow/actions/{action_id}` | `{key, token, review?, feedback?, protects?}` | Send for review or decide |
| `POST /api/{model}/{id}/workflow/withdraw` | `{key, token, feedback?, protects?}` | The requester withdraws |

Each POST needs `If-Match` (the record's ETag) and `token`, the run's concurrency token from the
status (`"new"` before the first run). A decision can change the run without changing a field, so
the token catches what the ETag cannot. The order of checks is: read access, the receipt for `key`
(a retry of a request that succeeded returns its stored result even with an old ETag or token),
`If-Match`, `token`, then the decision's own checks. Bodies are at most 16 KB.

Every error from these routes carries a `code`; other routes keep their bodies, and gain a `code`
only for the workflow refusals above:

| Status | `code` | When |
|---|---|---|
| 428 | `PRECONDITION_REQUIRED` | No `If-Match`, or no `token` |
| 409 | `RECORD_CHANGED` | `If-Match` is stale |
| 409 | `WORKFLOW_CHANGED` | `token` is stale, or the task was decided or closed meanwhile |
| 409 | `WORKFLOW_BLOCKED` | The run is blocked (nobody eligible, an undecided condition) |
| 409 | `WORKFLOW_PAUSED`, `WORKFLOW_NONE` | New requests are paused; no workflow applies |
| 409 | `IDEMPOTENCY_MISMATCH` | The key was used with other input |
| 409 | `CONFLICT` | Another database conflict |
| 403 | `FORBIDDEN` | No `review`, not assigned, your own request, not the requester |
| 404 | `NOT_FOUND` | No read access, or an unknown action |
| 413 | `TOO_LARGE` | The body is over 16 KB |
| 422 | `VALIDATION_FAILED` | Missing `key`, unknown keys, feedback rules; `fields` names each problem |

A person who can read but not update the record (a reviewer with `review`) decides through these
routes. `Meta.api = "read"`, a read-only API key and an assistant that may only read are refused like
any other write. The connector and Eva use these same routes with the person's own permissions.

## Try it in the Playground

The lifecycle Playground (`examples/playground/lifecycle/`) declares `Meta.workflow` on Order with
`unmatched="allow"`, so confirming works as before until an administrator publishes
`approval.json`: viewers review every order, and orders whose rates add up to more than 1,000.00 go on
to an admin review. Once it is published, confirm needs approval, an edit after approval sends the
order back for review, and `remarks` (harmless) can change without that.

## Not built yet

The configuration editor and the runtime screens (record header, workflow panel, inbox and
dialogs) come next, then nested reviews, delegation, deadlines, hold, refresh, reassign, override and
automatic execution of operations (`execute`). Until then a definition that uses `execute` or
`completion.mode: "execute"` is refused at publication.
