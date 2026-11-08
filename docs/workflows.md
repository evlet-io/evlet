# Approval workflows

Status: in progress for 0.4.0. This page describes what is built so far: the declaration, the
`review` permission, stored definitions and the engine that routes requests and counts decisions.
Nothing yet stops a protected operation from running without approval, and there are no workflow
screens or HTTP routes. Do not rely on a workflow to protect anything until the enforcement phase
ships. The full plan is [docs/internal/workflow.md](internal/workflow.md) and the contract is
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
| `evlet_workflow_record_data` | The record's data generation, used to invalidate approval in a later phase |
| `evlet_workflow_executions` | Each time a stage starts, with its frozen routing |
| `evlet_workflow_tasks` | One per review (or per single-reviewer stage) with its quorum |
| `evlet_workflow_assignees` | The people each task resolved to |
| `evlet_workflow_delegations` | Reserved for delegation, built in a later phase |
| `evlet_workflow_decisions` | Every decision (append-only) |

## Not built yet

The protection gate on confirm, moves and actions, stage edit rules on writes, approval
invalidation, idempotency receipts and journal entries come in the enforcement phase. The
configuration editor and the runtime screens come after it, then nested reviews, delegation,
deadlines, hold and override.
