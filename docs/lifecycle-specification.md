# Evlet Framework — Document Lifecycle, Change Tracking & Revisions

**Status:** Approved architecture specification
**Scope:** Core framework
**Applies to:** All Evlet models, with additional lifecycle capabilities for confirmable documents.

---

## 1. Overview

Evlet will provide a native document lifecycle engine with integrated change tracking and revision management.

The design is inspired by enterprise ERP document controls but avoids Frappe/ERPNext's mandatory Submit, Cancel and Amend mechanism.

### Core principles

- Simple, consistent document lifecycle terminology.
- One permanent document identity across revisions.
- No automatic amendment suffixes.
- Complete field-level and child-table change history.
- Immutable snapshots for confirmed revisions.
- Reconfirmation creates a new revision.
- Lifecycle logic defined through Python decorators.
- Business effects managed by applications, with lifecycle execution controlled by Evlet.
- Approval handled independently of document confirmation.

## 2. Document Lifecycle

### 2.1 Standard states

Evlet will use three universal lifecycle states.

| State | Meaning |
|---|---|
| `draft` | Document is editable and has not been confirmed. |
| `confirmed` | Document has been formally confirmed. Business operations are executed and protected fields become immutable. |
| `cancelled` | Confirmation has been cancelled. Associated business effects must be compensated or reversed as applicable. |

The lifecycle follows:

```text
New → Draft → Confirmed → Cancelled
         ↑                      |
         └────── Reopen ────────┘
```

A reopened document returns to Draft. It retains its existing document identity and revision history.

Reopening does not automatically create another revision. A new revision is created only upon successful reconfirmation.

### 2.2 Standard operations

| Operation | Transition | Purpose |
|---|---|---|
| `save()` | Draft → Draft | Persist edits |
| `confirm()` | Draft → Confirmed | Commit business operation |
| `cancel()` | Confirmed → Cancelled | Cancel confirmation and execute required compensating actions |
| `reopen()` | Cancelled → Draft | Permit further modification and reconfirmation |
| `delete()` | Draft → Deleted | Delete permitted draft documents |

Save is a persistence operation, not a lifecycle state.

Confirmed and cancelled documents cannot be deleted through normal document operations.

### 2.3 Optional confirmation

Not every Evlet model requires confirmation.

```python
class Customer(Model):
    confirmable = False
```

```python
class SalesInvoice(Model):
    confirmable = True
```

For non-confirmable models, normal create, edit, save and delete operations remain available according to permissions.

### 2.4 Configurable action labels

The core API remains consistent, but the user-facing terminology may differ across models.

| Document | Confirm action | Confirmed label | Cancel action |
|---|---|---|---|
| Sales Invoice | Post | Posted | Reverse |
| Purchase Order | Confirm | Confirmed | Cancel |
| Stock Movement | Post | Posted | Reverse |
| Leave Application | Approve | Approved | Revoke |
| Manufacturing Order | Release | Released | Cancel |

These are presentation labels. The underlying lifecycle always uses `draft`, `confirmed` and `cancelled`.

## 3. Approval Management

Approval is separate from the document lifecycle.

The framework must not treat approval and confirmation as interchangeable operations.

A document may be:

- Draft and awaiting approval.
- Draft and approved.
- Confirmed after approval.
- Rejected and still in Draft.

An application can require approval before confirmation.

Approval actions, remarks, approver identity and timestamps must be recorded in the same document history system.

If approval is granted and subsequently an approval-sensitive field changes, the application or approval policy should invalidate the approval and require reapproval.

## 4. Revision Management

### 4.1 Revision concept

Every successful confirmation creates an immutable revision snapshot.

The first confirmation is Revision 1.

After cancellation, reopening, modification and reconfirmation, the document becomes Revision 2.

Revision numbers increase monotonically and are never reused.

### 4.2 Example

Document: `INV-001`

| Operation | State | Revision |
|---|---|---|
| Create | Draft | 0 |
| Save changes | Draft | 0 |
| Confirm | Confirmed | 1 |
| Cancel | Cancelled | 1 |
| Reopen | Draft | 1 |
| Modify fields | Draft | 1 |
| Confirm again | Confirmed | 2 |
| Cancel again | Cancelled | 2 |
| Reopen and confirm | Confirmed | 3 |

The document number stays `INV-001`.

Evlet will not automatically generate `INV-001-1`, `INV-001-2`, etc.

### 4.3 Immutable snapshots

Each confirmed revision captures:

- Complete document data.
- Child-table data.
- Revision number.
- Confirmation timestamp.
- Confirming user.
- Relevant approval information.
- Business effect references.
- Snapshot integrity metadata.

Snapshots must remain immutable.

A historical revision may be viewed or compared, but editing it directly is prohibited.

### 4.4 Reconfirmation

When a cancelled document is reopened, it becomes editable again.

Reconfirmation:

1. Validates the current document.
2. Checks lifecycle permissions and dependencies.
3. Executes confirmation handlers.
4. Creates a new immutable snapshot.
5. Increments the revision.
6. Updates lifecycle state to Confirmed.
7. Records the operation in change history.

Each confirmation execution must have a unique identifier.

Downstream records and business effects must reference the relevant confirmation execution or revision, not only the document ID.

### 4.5 Financial document restrictions

Reconfirmation is a framework capability, not unconditional permission to reissue financial documents.

For sales invoices, purchase invoices and other regulated documents, the accounting application must enforce applicable legal and accounting rules.

An already issued tax invoice may require a credit note, reversal or replacement invoice instead of reopening and reconfirming the same legal invoice.

The framework must allow applications to block reopening or require a separate correction workflow.

## 5. Unified Change Tracking Engine

### 5.1 Purpose

Evlet will provide one native change tracking system for every model.

It must track ordinary document changes and lifecycle events through a unified chronological history.

The system must distinguish:

**Change history:** Individual modifications and operations.

**Revision history:** Immutable snapshots created at confirmation milestones.

A document can have hundreds of change events while having only two confirmed revisions.

### 5.2 Field-level tracking

For every tracked update, Evlet records:

- Field identifier.
- Previous value.
- New value.
- Actor.
- Timestamp.
- Operation identifier.
- Change source.

Example:

```text
Change #21
Document: INV-001
Field: customer
Old: Customer A
New: Customer B
Actor: admin
Source: User
```

Tracking should capture persisted changes, not every keystroke in the frontend.

A single save containing multiple field modifications should be recorded as one change operation containing multiple field-level differences.

### 5.3 Child-table tracking

Child-table tracking must support:

| Operation | Expected history |
|---|---|
| Row added | New row snapshot |
| Row deleted | Deleted row snapshot |
| Field updated | Previous and new values |
| Row reordered | Previous and new positions |
| Multiple rows changed | Grouped changes under one operation |

Each child row must have a stable internal identifier so that changing its position does not appear as deleting and recreating the row.

Nested child structures, when supported by the model engine, must also be trackable.

### 5.4 Lifecycle event tracking

The same history engine records:

- Document creation.
- Document updates.
- Approval and rejection.
- Confirmation.
- Cancellation.
- Reopening.
- Reconfirmation.
- Permitted post-confirmation edits.
- System-generated modifications.
- References to associated reversal or correction documents.

Cancellation must capture its reason, actor, timestamp, affected revision and business-effect references.

### 5.5 Change event metadata

Each change operation should contain:

```text
event_id
document_id
model
operation
actor
timestamp
source
revision
changes
reason
correlation_id
```

Possible sources:

- User
- API
- Automation
- Scheduler
- System
- Integration

History must remain append-only.

Administrative corrections to history metadata, if ever required, should themselves be separately audited.

Sensitive values, credentials and protected information must be masked or excluded according to field-level tracking policies.

### 5.6 Revision comparison

Evlet must support comparison between any two confirmed revisions.

Example:

```text
Invoice INV-001

Revision 1 → Revision 2

Customer: ABC Trading → ABC Industries
Quantity: 10 → 8
Rate: 100 → 100
Total: 1,000 → 800
```

Comparison must cover parent fields and child tables.

The frontend should display added, removed and modified values clearly.

## 6. Decorator-Based Lifecycle Logic

Evlet will not adopt Frappe-style `before_confirm`, `on_confirm`, `before_cancel` and `on_cancel` methods.

Instead, lifecycle validators and business actions are registered through decorators.

### 6.1 Standard decorators

```python
@validate.confirm
@validate.cancel

@action.confirm
@action.cancel
```

Validators determine whether an operation is permitted.

Actions perform the application's business-specific effects.

### 6.2 Example model

```python
from evlet import Model, action, validate

class SalesInvoice(Model):
    confirmable = True
    track_changes = True

    @validate.confirm
    def check_invoice(self):
        if self.total <= 0:
            raise ValueError("Invalid invoice total")

    @action.confirm
    def post_ledger(self):
        self.create_ledger_entries()

    @action.cancel
    def reverse_ledger(self):
        self.reverse_ledger_entries()
```

The method names are freely chosen by the developer.

`post_ledger` and `reverse_ledger` are registered as lifecycle handlers. They are not replacements for the framework's public `confirm()` and `cancel()` operations.

### 6.3 Confirmation execution

The framework executes confirmation in this sequence:

1. Acquire the required document concurrency lock.
2. Verify permissions.
3. Verify the document is in Draft.
4. Enforce applicable approval requirements.
5. Perform standard model validation.
6. Execute registered confirmation validators.
7. Execute registered confirmation actions.
8. Persist the confirmed document and its business effects.
9. Create the immutable revision snapshot.
10. Increment the revision number.
11. Append the confirmation event to history.
12. Commit the database transaction.

If any transactional step fails, confirmation must roll back without creating a partial revision or partial business posting.

### 6.4 Cancellation execution

Cancellation follows a corresponding sequence:

1. Verify permissions and document state.
2. Validate cancellation eligibility.
3. Execute registered cancellation validators.
4. Execute cancellation actions.
5. Preserve associated compensation or reversal records.
6. Change document state to Cancelled.
7. Append cancellation history.
8. Commit atomically.

Cancellation must not delete the confirmed snapshot or erase the original business effects from the audit trail.

### 6.5 Reopening

`reopen()` returns an eligible cancelled document to Draft.

The framework validates permissions and application restrictions before reopening.

An optional future extension may expose `@validate.reopen` and `@action.reopen` if application-specific reopening logic is required. These are not part of the initial mandatory decorator contract.

### 6.6 Multiple handlers

The framework should allow multiple validators and action handlers per lifecycle operation.

Handler execution must use a deterministic order.

Where ordering dependencies exist, the framework should support explicit handler priorities or dependency declarations rather than relying on Python method discovery order.

Handlers must not directly modify lifecycle state or revision counters. These remain under framework ownership.

## 7. Business Effect Integrity

The framework owns lifecycle execution, but applications own the meaning of confirmation and cancellation.

For example:

| Model | Confirmation effect | Cancellation effect |
|---|---|---|
| Sales Invoice | Post GL and receivables | Create financial reversals |
| Stock Movement | Create stock ledger movements | Create compensating stock entries |
| Purchase Order | Establish purchasing commitment | Release unfulfilled commitments |
| Leave Application | Update leave allocation | Restore applicable leave balance |

Not all cancellations require ledger reversals.

However, every cancellation must preserve enough history to explain what was undone.

Confirmation actions must be idempotent or use framework-managed execution identifiers to prevent duplicate effects.

For external API calls, Evlet should use durable outbox or queued operations with retries and reconciliation, since external operations cannot necessarily be rolled back with the database transaction.

## 8. Developer API

The proposed public API:

```python
document.save()

document.confirm()

document.cancel(reason="Incorrect details")

document.reopen()

document.confirm()  # Creates next revision
```

History access:

```python
document.history()

document.revisions()

document.diff(1, 2)

document.snapshot(revision=1)
```

These are proposed framework contracts, not confirmation that they already exist in the current Evlet codebase.

### Framework configuration

```python
class SalesInvoice(Model):
    confirmable = True
    track_changes = True
```

Detailed history tracking should be available to every model. Confirmable models should automatically receive confirmation revision tracking.

## 9. Data and Integrity Requirements

The engine must maintain:

- Permanent internal document ID.
- Separate user-facing document number.
- Current lifecycle state.
- Current confirmed revision number.
- Immutable revision snapshots.
- Append-only change events.
- Stable child-row identifiers.
- Unique lifecycle execution identifiers.
- Business-effect references.
- Concurrency protection.
- Transactional validation and persistence.
- Permission and approval enforcement.

For financial documents, revision numbers must not be treated as substitute legal invoice numbers.

A failed operation must leave the document in its previous valid state.

## 10. Scope Boundaries

### Framework responsibilities

- Lifecycle state transitions.
- Decorator registration and execution.
- Revision numbering.
- Snapshot generation.
- Change detection.
- Child-table comparisons.
- Audit history.
- Transaction and concurrency controls.
- Permissions and operation references.

### Application responsibilities

- Business-specific validation.
- Approval policies.
- Financial postings.
- Inventory movements.
- Business reversals.
- Eligibility for cancellation and reopening.
- Legal document correction requirements.
- User-facing lifecycle labels.

## 11. Final Decisions

| Area | Decision |
|---|---|
| Lifecycle terminology | Draft, Confirmed, Cancelled |
| Approval | Independent from lifecycle |
| Confirmation API | `document.confirm()` |
| Cancellation API | `document.cancel()` |
| Reopening API | `document.reopen()` |
| Revision increment | Every successful confirmation |
| Revision identity | Same document ID and number |
| History | Unified append-only change tracker |
| Snapshots | Immutable per confirmed revision |
| Validators | `@validate.confirm`, `@validate.cancel` |
| Business handlers | `@action.confirm`, `@action.cancel` |
| Frappe-style hooks | Not adopted |
| Automatic amendment suffix | Not adopted |
| Financial reversal | Application-controlled, framework-tracked |

---

**Architecture principle:** Evlet controls the document lifecycle, revisions and audit history. Applications define their validation and business effects through decorators. Every confirmation is a traceable execution, and every revision remains recoverable for inspection without rewriting historical records.
