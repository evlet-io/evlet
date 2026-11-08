# Document lifecycle and revisions

Implemented on the 0.2.0 baseline. The approved architecture is in
[lifecycle-specification.md](lifecycle-specification.md).

A confirmable document keeps one database ID and its existing document number:
`draft -> confirmed -> cancelled -> draft`. Every successful confirmation creates
the next immutable revision. Reopening does not create a revision or a new record.
Existing State/Move applications keep their current APIs and behavior. Adoption is
explicit; the framework does not silently migrate their business operations.

## Define a model

```python
from evlet import Model, Text, Currency, Rule, action, validate, ValidationError

class SalesInvoice(Model):
    confirmable = True
    track_changes = True
    number = Text(required=True, unique=True)
    total = Currency(required=True)
    remarks = Text(editable_when_locked=True)
    token = Text(tracking="exclude")
    private_note = Text(tracking="mask")
    lifecycle_labels = {"confirm": "Post", "confirmed": "Posted", "cancel": "Reverse"}
    permissions = {
        "admin": Rule(("read", "create", "update", "delete", "confirm", "cancel", "reopen")),
        "editor": Rule(("read", "create", "update", "confirm")),
        "viewer": Rule(("read",)),
    }

    @validate.confirm
    def valid_total(self):
        if self.total <= 0:
            raise ValidationError({"total": "Enter a positive total"})

    @action.confirm(priority=10)
    def post_ledger(self):
        execution = self.context.lifecycle_execution
        # Create application ledger rows through self.context.create(...).
        # Register each result with self.context.reference_effect(LedgerEntry, id).
        # execution.id is unique; execution.revision identifies this confirmation.

    @validate.cancel
    def check_dependencies(self):
        # Raise ValidationError when a dependent record prevents cancellation.
        pass

    @action.cancel
    def reverse_ledger(self):
        # Create compensation rows, preserving the originals, then reference them.
        # self.context.lifecycle_execution.confirmation_id identifies the posting.
        pass
```

Validators and `check_lifecycle(self, operation)` run with read-only drafts and
contexts. Actions run with a writable context. All handlers take only `self`.
Lower priorities run first; equal priorities sort by method name. Inheritance
uses normal Python overrides, including removing a decorator in an override.
The engine owns state, revision and execution identifiers. Assigning these on a
draft is rejected.

A confirmable model cannot also declare State/Move, Meta.effects or Meta.revisable.
Port the application's business handlers deliberately before enabling confirmation.
Model and child definitions must use the usual explicit permissions where required.

## Work with a document

Use an authenticated `Application` or the `App` shortcut. Handles belong to a live
business context and must not be retained after it exits.

```python
with app.business.context(user, source="Integration", correlation_id="import-123") as context:
    document = context.document(SalesInvoice, number="INV-001", total="100.00")
    document.save()
    document.confirm()                  # Revision 1
    document.cancel(reason="Incorrect details")
    document.reopen()                   # Same ID, still Revision 1
    document.total = "120.00"
    document.save()
    document.confirm()                  # Revision 2
    history = document.history(limit=50, offset=0)
    revisions = document.revisions(limit=50, offset=0)
    first = document.snapshot(revision=1)
    changes = document.diff(1, 2)
```

The equivalent context methods are `create`, `update`, `confirm`, `cancel`,
`reopen`, `delete`, `history`, `revisions`, `snapshot` and `diff`. Child edits use
the existing `children` replacement/change-set contract through
`document.save(children=...)` or `context.update(...)`.

Only fields with `editable_when_locked=True` may change while confirmed or
cancelled. Child tables stay locked. Ordinary actions also obey these protections.
A permitted edit records `post_confirmation_edit` and preserves the original
snapshot. Confirmed calculations keep their saved results. A never-confirmed
draft can be deleted. Once confirmed, a document is retained even after reopening
so its snapshots and downstream references remain accessible.
The naming field (the standard `name`, or a field named `number`) stays fixed after
the first confirmation. Use `document_number_field = "reference"` when another stored
field is the application's permanent document number.

**Numbering on confirm.** `Meta.naming = Naming(Series("INV-{YYYY}-{#####}"), assign_on="confirm")`
names a document in its first confirm transaction, so drafts never take a number and confirmed
documents are numbered without gaps. Until then the record page shows "Assigned on confirm". A
reopened and reconfirmed document keeps its name; a revision copy gets a new one
([sequences.md](sequences.md#naming-series)).

## Transactions, permissions and business effects

Operations acquire the database's existing writer lock before reading. SQLite
uses `BEGIN IMMEDIATE`; PostgreSQL uses the database advisory transaction lock.
These are database-wide locks, not distributed per-document locks. Each operation
also has a savepoint. Validation, actions, document rows, snapshots, history and
queued jobs roll back together, even when the caller catches an exception and
commits the surrounding transaction. The surrounding context commits on exit.

Lifecycle actions require both update access and the separate operation permission,
plus the current row scope. Admin has lifecycle operations by default when its
rule allows read and update. Other roles must receive them explicitly. API key,
field, child-row and host API-scope restrictions still apply. Raw Session writes
to tracked roots and their owned rows require the existing explicit maintenance
escape hatch. Trusted maintenance/SQL is not a normal document operation and
must be separately audited by the operator.

Applications own postings and compensations. Use `context.reference_effect(Model,
id)` during an action to associate each record with that execution. Snapshots
include confirmation references; cancellation history includes the original and
compensation references. Readers only receive references their permissions allow.
Do not delete the original postings on cancellation.

External services cannot participate in the database rollback. Use the existing
JobQueue with `self.context.enqueue(task, payload, key=execution.id)` in an action.
The queued intent commits with confirmation. Workers need idempotent external
requests and reconciliation for delivery whose acknowledgement was lost. The
framework does not promise exactly-once delivery to external services.

## Approval and legal restrictions

Approval is independent. An application's permission-checked approval action may
call `context.record_approval(Model, id, approved=True, reason="Checked")`.
Approval and rejection append actor, time and remarks to the same history without
changing lifecycle state. Confirmation snapshots retain the latest approval event.
This audit API is for trusted application policies; it does not grant approver
roles or expose a generic HTTP approval endpoint, and a configured approval
workflow never reads its events. For a configured approval engine that protects
confirm, cancel or reopen, see [Approval workflows](workflows.md): when a workflow
protected a confirm, the snapshot's `approval` member names the workflow run,
attempt and decisions instead of the latest approval event.

Implement `check_lifecycle(self, operation)` to require an approval, reject
cancellation with dependencies, or block reopening of a legal invoice. The policy
can read the history through `self.context.history(...)` and must invalidate an
approval when its sensitive fields change. This is an application decision; the
framework does not infer which fields or roles constitute approval. Financial
applications may require a credit note or a separate replacement document.
A revision number never substitutes for a legal invoice number.

## History and storage

`track_changes=True` enables detailed history for an ordinary model. Confirmation
automatically enables history and revisions, even without that flag. Other models
keep their current journaling configuration. Tracking records persisted saves,
not frontend keystrokes. One save groups parent and nested child differences,
including row additions, deletions, field changes and old/new row positions.
Stable database row IDs distinguish edits and reorderings from replacement.

History extends `evlet_journal`, preserving its append-only database guards and
permission filtering. New tracked events include event ID, model, document ID,
operation, actor, timestamp, source, revision, changes, reason and correlation ID.
Sources are User, API, Automation, Scheduler, System and Integration. Python jobs
and integrations should pass their explicit source to `business.context(...)`.
History pages remain newest first, matching the existing journal.

`evlet_document_lifecycle` holds current state, revision and confirmation ID.
`evlet_document_revisions` holds the immutable document tree, approval information,
effect references, actor, time and execution ID. SHA-256 covers the canonical
snapshot envelope and is verified on read. Triggers reject UPDATE, DELETE and
SQLite replacement; PostgreSQL also rejects TRUNCATE. An administrator who can
drop triggers or change the database is outside these guards' security boundary.
Framework tables are installed idempotently. No columns are added to app models.
Recompute migrations retain records with confirmed revision history.

Fields accept `tracking="value"` (default), `"mask"`, or `"exclude"`. These policies
apply before values reach history and snapshots. Masked and excluded values cannot
be recovered or compared from those artifacts. Choose the policy before saving
sensitive data; changing it does not erase values already stored in older audit
entries. Reads also apply current policies and permissions. Historical child rows
that no longer satisfy a current row scope are withheld, including deleted rows.

## HTTP and generated screens

Writes use `POST /api/{model}/{id}/confirm`, `/cancel`, and `/reopen`. Cancellation
requires `{"reason": "..."}`; the others take `{}`. Each requires the current
`If-Match` ETag. Missing ETags return 428, stale ETags or invalid state transitions
409, forbidden operations 403 and validation failures 422.

Reads use `/history?limit=50&offset=0`, `/revisions?limit=50&offset=0`,
`/snapshot?revision=1` and `/diff?from=1&to=2`. Revision and snapshot routes are
read-only. Revision listings are oldest first. Diffs return changed parent
`fields` and child `rows` with `added`, `removed`, `updated` and `positions`.
Host API scopes must grant lifecycle actions and the `history` or `revisions`
extras. Snapshot/diff use the `revisions` extra.

Schema adds `lifecycle: {confirmable, labels, permissions}`. Details add
`_lifecycle: {state, revision, confirmation_id}` and `_lifecycle_actions`, plus
existing lock/delete availability keys. The generated screen shows allowed actions,
collects cancellation reasons, displays state/revision, and offers read-only
snapshots and comparisons on the Revisions tab. Activity uses the unified journal.

Run the separate Lifecycle Playground with:

```sh
.venv/bin/python -m uvicorn examples.playground.lifecycle.app:create_app --factory --host 127.0.0.1
```

The original Playground database and models are unchanged. The shared browser/API
fixture is generated by `python -m scripts.generate_lifecycle_fixtures`; backend
tests compare fresh real HTTP responses with it. Only volatile identifiers,
timestamps, ETags and test CSRF values are normalized.
