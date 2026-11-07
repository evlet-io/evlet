# Workflow implementation plan

Status: proposed. Framework design and implementation process. No workflow engine
or API described as proposed in this document is implemented by this change.
Date: 7 November 2026.

## 1. Objective and agreed scope

Provide a general document workflow with approval, custom stages and actions,
multiple levels, conditional and parallel branches, nested reviews, stage-based
edit access, and immutable decision feedback. Configuration must work through a
normal document editor without a flowchart canvas, scripts, or a separate table
of transitions that users must reconcile with a table of states.

The everyday vocabulary is **Workflow**, **Stage**, **Review**, **Action**,
**Condition**, and **Feedback**. Use “stage” consistently in the interface;
“state-wise edit access” means edit access at that workflow stage.

Simple and complex workflows use the same editor and engine. Advanced settings
expand in place; users do not switch between incompatible simple/advanced modes.

This is not approval-only. Support operational stages such as Inspection, On
Hold, Released, and Closed, even for models that are not confirmable.

## 2. Existing contracts to preserve

Implementation must first read these authoritative contracts and their current
code/tests, updating them when a milestone introduces actual public API changes:

| Existing area | Integration contract |
| --- | --- |
| [Document lifecycle](../document-lifecycle.md) | Preserve draft/confirmed/cancelled, confirm/cancel/reopen permissions, immutable confirmation revisions, savepoints, and `check_lifecycle` |
| [States and actions](../states-actions.md) | Preserve existing State/Move consumers, model action inputs, locks, effects, and void/revise behaviour |
| [Journal](../journal.md) | Extend append-only history; use its storage and permission filtering rather than a disconnected feedback log |
| [Controls](../controls.md) | Respect app permission ceilings, account realms, configuration versions, and audited administration |
| [UI framework](../ui-framework.md), [UI](../ui.md), [routing](../routing.md) | Use Evlet's own components, record store, schema, authenticated navigation, CSRF, and ETags |
| [Calculations](../finite-calculations.md) | Reuse the finite expression infrastructure for validated conditions; preserve exact numeric comparisons |
| [Jobs](../jobs-realtime.md) | Commit notification intent with the decision; no mandatory worker for immediate progression |
| [Connector](../connector.md) | All connector writes use existing authenticated HTTP paths and current user permissions |
| [Tenancy](../tenancy-deployment.md), [PostgreSQL](../postgresql.md), [performance](../performance.md), [operations](../production-operations.md) | Keep instance isolation, adapter limits, bounded transactions, and validated deployment assumptions |

Also read `specification.md`, `architecture.md`, and `business-capabilities.md`
before implementing public APIs. The existing browser framework is authoritative
where historical specification sections mention removed frontend technologies.

Current `record_approval` records an application-authorized audit event; it is not
a generic approval engine. Existing events must remain readable but cannot become
approval grants for a newly configured workflow. Do not decide approval from the
latest history entry alone: it cannot represent multiple pending branches.

Workflow stage is separate from lifecycle state and from any app-owned business
status. A workflow action can invoke a registered lifecycle operation or model
action, but cannot assign lifecycle state, bypass validation, or fabricate a
confirmation revision. Existing State/Move applications require explicit adoption;
never enable both independent controllers for the same transition accidentally.

## 3. Configuration document and authoring UX

### 3.1 Workflow header

Show only Name, Document, Applies when, Starting stage, and Draft/Published status.
New rules start as drafts. A basic template starts with Draft, Review, Approved,
and Rejected, with editable names and sensible action defaults.

Expanded settings hold the protected action, completion behaviour, self-approval,
withdrawal, delegation, edit/invalidation rules, deadlines, notifications, and
publication notes. Never require internal model names or numeric lifecycle codes.

Model authors explicitly declare which models/actions are configurable. Configuring
a workflow does not make arbitrary Python methods callable.

### 3.2 Expandable stages

The main editor is an ordered list of compact stage cards. A collapsed card shows
its name, responsible person/group, condition, edit summary, and next destination.
Clicking it reveals settings and an inline actions table:

| Button label | Who can use it | Only when | Go to | Feedback | Execute |
| --- | --- | --- | --- | --- | --- |
| Approve | Assigned reviewer | Always | Department Review | Optional | None |
| Request changes | Assigned reviewer | Always | Draft | Required | None |
| Reject | Assigned reviewer | Always | Rejected | Required | None |

“Who can use it” defaults to the stage's assigned reviewer. Destinations use named
stage pickers, including conditional destination rows and an Otherwise row. New
destinations can be created inline. Internally use stable IDs, never labels or
row positions as identifiers. Rename and reorder must preserve references.

Support Add stage, Duplicate, Move up/down, and Remove. Reordering affects default
sequence and presentation; it must not silently rewrite explicit destinations.
Show referenced actions before deleting a stage. Provide Undo before saving.
Never allow a published version to be edited in place.

### 3.3 Conditions

Use familiar three-column rows: Field, Operator, Value. Group using All/Any and
allow nested groups. Offer appropriate controls for dates, links, money, enums,
and users. Keep money as exact decimals in Python and neutral JSON.

Support document fields, owned-row aggregate predicates, requester attributes,
and explicitly registered read-only lookup facts. Python defines the allowed
facts and their types; the browser consumes the neutral schema. No arbitrary
Python, SQL, imports, network calls, or browser-supplied expressions.

Routing defaults to First matching destination with a required Otherwise.
“Start every matching review” is a separate explicit option for parallel reviews.
Missing required facts or evaluation errors block with a useful message. They
never count as false and silently select a lower approval path.

### 3.4 Reviews inside a stage

Most stages need one reviewer. “Add parallel reviews” reveals a small table:

| Review | Assigned to | Only when | Required decisions |
| --- | --- | --- | --- |
| Technical | IT Approver role | Purchase type = Equipment | Any one |
| Operations | Operations Approver role | Purchase type = Services | Any one |
| Finance | Finance Approver role | Always | Any one |
| Procurement | Procurement Approver role | Always | Any one |

Below it: Run together / In sequence; Complete when All / Any / Number approve;
and shared completion, rejection, and changes-requested destinations.
Each review may expand to its own sequence of stages/reviews, providing nested
branches with the same controls. No separate Join document is necessary: join
semantics belong to the containing review group. Do not infer branches merely
because rows happen to share an order number.

### 3.5 Edit access section

Every stage has a visible **Who can edit?** summary. Defaults: Draft is editable
by the requester within existing rights; review stages are read-only.

Options are Nobody, Requester, Assigned reviewers, Selected users/roles, and
Everyone with existing edit access. An expansion allows field-level rules and
child-table operations. Defaults are restrictive, never implicit permission grants.

Show a readable access matrix and a **Check access** tool for a selected user:
“Can approve; cannot edit. Amount is read-only during Finance Review.”

### 3.6 Preview, validation, and publishing

**Try example** accepts a saved record or unsaved sample values and a requester.
Show an ordered textual preview of selected stages/reviews, skipped conditions,
resolved assignees, edit permissions, available buttons, and approval outcome.
Use the backend evaluator; never create tasks, history, jobs, or postings.
The preview requires access to the supplied record and protected lookup facts.

Validate as users edit. Publication blocks missing approvers, broken destinations,
ambiguous selection, unreachable stages, dead ends, impossible quorums, invalid
field permissions, illegal lifecycle operations, and automatic transition loops.
Dynamic eligibility must also be checked at runtime; publication is not proof
that every future request has an eligible manager.

Publishing shows a concise change summary and affected model/action. Existing
requests stay pinned to their version. Administrators can pause new starts without
rewriting or stopping current requests.

## 4. Worked Purchase Order configuration

| Stage | Assignment or behaviour | Next action |
| --- | --- | --- |
| Draft | Requester edits | Send for review → Manager Review |
| Manager Review | Department Manager; document read-only | Approve → Department Review |
| Department Review | Conditional IT/Operations review plus Finance and Procurement, run together | All applicable reviews approve → conditional destination |
| Director Review | Director; only for total above AED 100,000 | Approve → Approved |
| Approved | Required reviews complete | Enable Confirm, or explicitly configured Confirm on completion |
| Rejected | End this approval attempt | Requester may start a new attempt if configured |

Department Review sends totals above AED 100,000 to Director Review and others to
Approved. Purchase type must be Equipment or Services in this example; otherwise
block and explain the unsupported type rather than omit the technical review.
Reject and Request changes require feedback. Request changes goes to Draft and
closes the old approval attempt; resubmission creates a linked new attempt.

## 5. Runtime model and branch semantics

Use immutable published definitions and append-only decisions with transactionally
maintained current-state projections. Proposed internal entities:

| Entity | Responsibility |
| --- | --- |
| Workflow definition/version | Stable identity, immutable published configuration, applicability, protected actions |
| Workflow run | Record reference, pinned version, requester, start/end, current status, concurrency version |
| Approval attempt | Saved business-data generation, approval evidence fingerprint, start/end reason |
| Stage execution | One occurrence of a stage, its branch/group ancestry, status, timestamps |
| Review/task | Resolved eligible people, pending/completed status, deadline, decision requirement |
| Decision | Action, actor, delegation, feedback, source, definition/attempt/data versions, journal reference |
| Delegation | Original assignee, delegate, permitted scope, effective period, authorizer |

Do not put engine state in editable app fields. Optional business-status updates
must use explicitly registered, validated field effects. Index active tasks by
assignee/status/due time and executions by record/run/version. Require tenant-local
foreign keys and unique decision/idempotency constraints.

At stage entry, evaluate conditions and resolve assignments from authoritative
saved data under the transaction lock. Persist activated branch IDs, eligible
assignees, routing facts, and required quorum. Freeze that routing for the attempt;
changes to external facts do not silently remove reviews. Explicit refresh ends
the attempt and re-resolves a new one with a reason. Current account/access and
approval authority are always rechecked at decision time.

Parallel completion rules:

1. A group waits only for branches actually activated, never skipped branches.
2. All means all activated branches; Any means one success; Number means N distinct
   branch successes. Within a review, quorum counts distinct eligible people.
3. A person cannot vote twice by changing roles or acting both directly and as a
   delegate. Record the original principal too; delegation does not create votes.
4. Default rejection ends the attempt; default changes-requested returns to Draft.
   Alternative configured paths are explicit. Any/Number groups must state whether
   rejection is a veto or merely a failed branch; default is veto.
5. When the group completes, close unnecessary pending siblings as no longer
   required and cancel their pending notifications. A late decision returns conflict.
6. Zero activated branches and unattainable quorum block by default. An explicit
   “Continue when no reviews apply” setting is available and appears in preview.
7. Missing eligible people block; show an administrative repair action with reason.
   Never lower quorum or skip a branch automatically.

Support human-controlled return paths and repeated operational stages using new
stage execution IDs. Prohibit cycles made entirely of automatic transitions;
bound automatic progression and nesting per transaction. Rework creates a linked
attempt, preventing prior approvals from satisfying a new submission.

Select one workflow per model/protected-action scope for a run. Conditions select
the applicable definition; overlapping matches are a configuration conflict,
not arbitrary priority. Multiple independent policies must be explicit review
groups inside the selected workflow. Unmatched applicability is either an explicit
allow-without-workflow policy or fail-closed protection; the app declares which.

## 6. Stage-wise edit access and authorization

Effective edit rights are the intersection of base model/row access, field/child
permissions, lifecycle/posted-data locks, workflow stage limits, and API scope.
Workflow can narrow rights but never expand the app's permission ceiling.

Field configuration offers All otherwise-writable fields or Only selected fields.
Child tables separately control editing cells, adding, removing, reordering, and
moving rows; nested tables follow the same rules. Attachments have explicit
add/remove restrictions. Unknown/new fields are denied by selected-field rules.
Comments and feedback have their own permissions; they are not document edits.

With parallel stages, intersect restrictions from all active branch stages plus
the containing stage. Any read-only restriction wins. Display this effective
result, not the union of what different reviewers might individually allow.

Validate against the saved source stage before a write; users cannot gain edit
rights by submitting a destination stage with their update. Ordinary saves cannot
change engine-owned stage, assignee, approval, or decision fields. Validate the
full resulting document tree, including replacement payloads and deletions.

Approval decisions require current read access, assigned eligibility, and an
explicit workflow-decision capability declared by the app. They do not require
ordinary document update rights. This is a proposed addition to the permission
contract: extend Policy/Controls explicitly rather than invent an update bypass.
Current single-role account semantics remain supported; multi-role accounts and
role groups are not prerequisites for this engine.

Business operations invoked by workflow still require their existing operation
permissions, update requirements, row scope, field validation, and locks. A reviewer
may approve without permission to confirm. Auto-confirm must use an explicitly
configured authorized executor, recorded separately from the approver, with current
permissions rechecked. Default completion only enables confirmation for an
authorized user; it does not elevate the final reviewer.

Guard protected operations centrally, including direct context calls, custom
actions, imports, jobs, EVA, and HTTP. A custom action cannot call confirm to bypass
the gate. Use an execution-scoped internal grant tied to the exact record, action,
attempt, and data version for the engine's validated completion operation. Never
accept that grant in a client payload. Trusted raw SQL/maintenance remains an
explicit administrative boundary and requires separate audit.

No silent admin exemption from configured workflow controls. Provide a separate
app-authorized override with required feedback and immutable history. Protect
workflow definition administration separately from reviewer eligibility.

## 7. Edits, approval validity, and completion failures

Default: freeze business fields while awaiting approval. Request changes or
withdraw before editing. Stages may explicitly permit selected fields. Every
business-data change invalidates current approval evidence unless the field is
explicitly classified as harmless by the app's policy ceiling.

Owned-row edits, replacement, reorder, attachment changes, computed dependencies,
and configured lookup facts must be included in the validity design. Use a
monotonic business-data generation plus canonical fingerprint; do not store raw
masked/excluded secrets in history to obtain a fingerprint. Relevant change ends
the attempt and closes pending tasks atomically. Explain the cause on screen.
Earlier decisions stay in history, marked as belonging to a superseded attempt.

Final confirmation checks approval and data validity again under the same lock
as posting. Approval is for a specific operation and cannot be reused to cancel
or reconfirm a new revision. Reopen/reconfirm requires new evidence by default.

If completion automatically executes a synchronous business action, its decision,
transition, action, and history are one savepoint: failure rolls all of them back.
The reviewer sees the validation problem and may retry after repair. In the
default “allow confirmation” mode, approval can complete while subsequent manual
confirmation independently fails validation; show Approved, awaiting confirmation.
Never label a document Confirmed when only approval succeeded.

## 8. Required feedback and immutable activity

Each action configures Feedback: Hidden / Optional / Required, plus label, prompt,
and maximum length. Defaults: Approve optional; Reject, Request changes, Override,
Reassign, and explicit eligibility refresh required.

The action dialog uses the actual verb: “Reject purchase order”. Show destination
and effect, then feedback input. For optional feedback-free approvals, allow a
direct action when no other confirmation is needed. Required feedback is enforced
by the backend, trimmed, rejects whitespace-only input, has valid Unicode/no NUL,
and respects a bounded request size. Render text safely without raw HTML.

Persist the decision and journal event in the same transaction as the transition.
The immutable event contains actor ID, authorization basis, original principal and
delegate if applicable, action ID/label, source/destination execution IDs, feedback,
UTC timestamp, workflow version, attempt/data generation, correlation ID, and
execution source/client. Record the actual role used by current single-role
accounts; do not imply a role-selection system that does not exist.

Extend `evlet_journal` and existing lifecycle history readers. Do not use an
editable rejection-reason field as the authoritative evidence. Decision records
and definition versions need append-only database protections, including SQLite
replacement and PostgreSQL truncate protection as applicable. Projections remain
mutable but must reconcile to immutable events; no client CRUD on engine tables.
Database owners who can remove guards are outside the immutability boundary.

Corrections append a linked clarification; original feedback is retained. Document
read access and history filtering apply to every view/export. In the initial
release feedback has the same audience as permitted workflow history, clearly
stated in the dialog; no private/public feedback toggle until separate visibility
and redaction semantics are implemented. Hidden condition values and restricted
documents must not leak through feedback previews, assignments, or notifications.

## 9. Reviewer and requester UX

Document header: one compact stage badge and a sentence such as “Awaiting Finance
and Procurement · Manager approved”. Keep lifecycle state distinguishable.
One primary action; secondary actions in a labelled menu. Do not show internal
branch IDs or route machinery. Reviewers can act while document fields stay locked.

Workflow panel: active reviews first, pending names/groups, deadlines, completed
decisions, and an expandable future-stage preview marked conditional. A parallel
process must not pretend there is one current person. Activity shows feedback next
to its decision, with links to superseded attempts and the approved data version.

Inbox: My reviews, Requested by me, and authorized Team reviews. Show document,
requester, summary/amount, stage, due time, and current availability. No task grants
record access. Return promptly to the next task after a decision. Default to
individual decisions; bulk required-feedback actions wait until per-document
review and feedback are supported safely.

Dirty forms must save or discard before a workflow action; never silently submit
unsaved values. Concurrent decisions show “This request changed. Review the latest
status” and retain typed feedback locally for retry. If commit succeeded but the
response was lost, retrying the same action key retrieves the previous receipt.
If permissions change, invalidate stale field stores so hidden data is not retained
in the page. Reuse the app's event/polling mechanisms for updates.

Keyboard-only configuration/action handling, labelled inputs, visible focus,
inline error associations, focus return after dialogs, screen-reader status, and
non-colour status cues are required. On narrow screens, stage/review rows become
stacked labelled fields; the primary action remains reachable without horizontal
scrolling. Do not use a blurred overlay for the editor's settings side panel.

## 10. Execution, API, and operational process

Proposed service operations: start, decide, withdraw, refresh eligibility,
reassign/delegate, hold/resume, and authorized override. Exact Python exports and
route names must be finalized against current business/HTTP modules during Phase
1; names here are operation contracts, not promises of existing functions.

One backend service implements all paths. Sequence for a mutation:

1. Authenticate; resolve tenant and host/API scope; verify current account access.
2. Acquire the existing database writer lock/savepoint before reading decision state.
3. Resolve a permission-scoped idempotency receipt; reject key reuse with different
   payload/target. A valid retry can return its prior receipt despite an old ETag.
4. Check record and run concurrency versions, current assignment, self-approval,
   operation rights, source stage, condition, feedback, and approval validity.
5. Record the decision; settle quorums and bounded automatic progression; run any
   authorized synchronous business operation through its normal pipeline.
6. Persist projections, immutable decisions/history, idempotency receipt, event,
   and queued notification intent atomically. Return filtered detail and versions.

HTTP mutation contracts follow current CSRF/ETag patterns: missing precondition
428, stale state/conflicting decision 409, denied access 403 or scoped 404, invalid
feedback/inputs 422. Include a workflow concurrency token because a branch decision
can change workflow state without changing document fields. Detail metadata exposes
only effective allowed actions, input requirements, stage labels, lock explanations,
and safe pending summaries. Never let the frontend infer authorization.

Scheduler jobs handle reminders, deadline escalation, and expiry through the same
service with explicit system authority and audit. Deadline expiry never counts as
approval. Delegates need current document access and authority; expired or disabled
assignees need explicit repair. Business-day deadlines require a configured calendar
and timezone; offer elapsed hours until that calendar exists, with honest labels.

Email/integration delivery is at least once through existing infrastructure, with
deduplication keys and no sensitive content by default. Immediate approval works
without a worker. Show delayed reminder delivery in operational diagnostics. SSE
is an invalidation hint; reload the current permission-filtered state after it.

## 11. Implementation milestones and acceptance gates

Work on feature branches using the existing checkout; preserve unrelated changes.
Deliver each milestone with docs, meaningful real-database tests, and an applicable
Playground example. Do not ship a partial feature as full workflow support.

| Phase | Deliverable | Acceptance gate |
| --- | --- | --- |
| 1. Contract and UX | Final neutral schema, permission capability extension, lifecycle/State adoption rules, expandable-stage prototype, API error shapes | Demonstrate manager-only, conditional, parallel, rework, and locked-document reviewer journeys without canvas or code |
| 2. Storage and evaluator | Definition versions, runs/attempts, stage executions, tasks/decisions, finite conditions, immutable guards, constraints | Deterministic routing and quorum behaviour on real SQLite; PostgreSQL validation per adapter contract |
| 3. Enforcement and audit | Central action gates, state-wise field/child edit checks, required feedback, attempt invalidation, transaction/idempotency handling | Direct API/context/custom-action bypass attempts fail; all failures roll back decisions and journal together |
| 4. Configuration editor | Stage cards, inline actions, conditions, reviews, access matrix, Try example, publish/version management | A user can configure the worked example from a blank workflow without scripts or separate transition maintenance |
| 5. Runtime screens | Header actions, workflow panel, inbox, feedback dialogs, history, conflicts/recovery, responsive and accessible handling | Review/return/edit/resubmit/confirm works through real HTTP and generated screens with read-only reviewers |
| 6. Advanced operations | Nested branches, delegation, deadlines/reminders/escalation, pause/resume, registered automation and explicit overrides | Removed access, expired delegation, worker retries, dormant tenants, and action failures remain safe and explainable |
| 7. Hardening and release | Load scenarios, query/index review, upgrades/restore handling, connector coverage, documentation | Complete regression suite passes; operators have repair and monitoring guidance without silent bypasses |

No new runtime dependency by default. Do not introduce a mandatory Redis service,
graph editor library, external workflow engine, worker, or database. Integrate into
existing business/policy/journal modules where practical; create cohesive new
workflow modules only when they have implemented consumers. All UI uses
`frontend/src/evlet/`, with Python-authoritative definitions and neutral schemas.

## 12. Required verification matrix

- Routing: multi-level chains, mutually exclusive and multiple matching branches,
  nested groups, default paths, unknown facts, zero branches, automatic cycles,
  human return loops, repeated stage IDs across execution occurrences.
- Quorums: Any/All/N at reviewer and branch levels, duplicate people across roles,
  delegated duplicates, rejection veto/non-veto, sibling cancellation, late votes.
- Editing: all actor selections; field allowlists; nested child edits/add/delete/
  reorder/replacement; attachments; posted totals; locked lifecycle; masked fields;
  blocked custom actions; stage-changing payload injection; stale concurrent save.
- Permissions: read-only reviewer, hidden fields, creator/self approval,
  cross-row/tenant/realm attempts, API keys, removed roles/access, delegated user
  without access, direct context operations, connector and scheduled execution.
- Audit: required/empty/oversized feedback, Unicode, escaped display, atomic rollback,
  append-only SQL guards, clarification retention, export/history permission filtering.
- Lifecycle: manual versus automatic confirmation, failed posting validation,
  cancellation and reconfirmation approval, same-identity revisions, existing
  State/Move consumers unchanged, old `record_approval` events not treated as grants.
- Concurrency/recovery: simultaneous final decisions, decision versus withdrawal,
  edit versus final confirm, duplicate requests, lost HTTP response, process restart,
  config publication during active run, external lookup/manager change, backup restore.
- UX: keyboard and narrow-screen configuration, condition type controls, safe preview,
  no canvas dependency, dirty form handling, feedback preservation after conflict,
  inaccessible task removal, meaningful blocked-state and access explanations.
- Performance: bounded configuration size/depth and progression; paginated inbox and
  history; indexed task reads without N+1 document loads; batched assignee checks;
  no journal scans to authorize a decision; no external network calls while holding
  the writer lock. Measure with agreed representative workflow/document data.

For implementation changes run `.venv/bin/python -m pytest`, then `npm test` and
`npm run build` in `frontend/`. Verify packaging/install if packaging changes.
Never substitute mocks for SQLite. Add shared schema/HTTP fixtures for the browser
contract and meaningful real-HTTP integration coverage. This document-only change
does not claim these implementation checks were run.

## 13. Completion criteria

The framework can configure and execute the worked multilevel/multibranch example,
protect any explicitly registered business action, enforce stage-wise field and
child edit rights on every supported write path, record required decision feedback
immutably, and explain current access and approval status to users. Existing login,
Docs after sign-in, generated screens, lifecycle, State/Move, Python UI contracts,
and connector permission boundaries remain covered and passing.

The initial shipped system must include the simple setup experience and truthful
failure/recovery UX. A powerful backend with a confusing editor is incomplete.
