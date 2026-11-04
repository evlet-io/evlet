# API surface and period lock

Status: built on 04-11-2026. The public descriptions are in
[model-engine.md](../model-engine.md#how-much-of-a-model-callers-reach), [effects.md](../effects.md#closed-periods)
and [controls.md](../controls.md#periods).

Two generic model options for apps that keep records only their own code may change, such as a
ledger written by a posting function, and records that must not change once a period is filed.
Append-only enforcement was discussed and is not part of this.

## 1. `Meta.api`

| Value | Callers (HTTP, MCP, Eva, connector) | Trusted Python |
|---|---|---|
| `"full"` (default) | As today, by role | Everything |
| `"read"` | Read, by role. No write of any kind, for any role, admin included | Everything |
| `None` | Nothing: every route answers 404 and no listing names the model | Everything |

**Callers and trusted code.** A request is a caller. `App.__call__` and `Business.dispatch` mark
the request as one (`permissions.CALLER`, a context variable), so in process tool calls from MCP
and Eva, which go through `Business.dispatch`, count as callers too. The app code a request runs is
trusted again: action handlers (declared and legacy), a move's `run`, `changed` reactions, dialog
`load` and `submit`, `@api` service functions and a report's `run()`. Jobs, schedules, migrations and scripts never run
as callers. Inside trusted code, `context.create(LedgerEntry, ...)` works as it always did.

**One rule.** `permissions.surface(registry, model, operation)` decides, before any role:

- effect targets: no create, update or delete, for anyone (the existing rule, unchanged);
- `api="read"`: no write operation while a caller is active;
- `api=None`: no operation at all while a caller is active.

`Policy.allows` asks it first, so Controls overrides (which only change Rules) cannot grant
anything back. Write operations are create, update, delete, journal and every correction.
Attachments, actions, moves and lifecycle routes all check `update` or one of those, and
`model_dispatch` also refuses every method except GET on an `api="read"` model with
403 "<Model> is read only here. The application changes it.", so a route that forgets a check still
cannot write. `/api/_draft/<model>` is refused too.

**`api=None`.** `Business._dispatch` answers 404 "Route not found" for `/api/<model>` and every sub
route, and for the draft route. The UI schema, Discovery, Controls models (permissions, fields,
layout, naming, MCP hidden models), measures, the print catalog, the tool list and the
sidebar all go through either `Policy.allows(..., 'read')` or `Registry.exposed`, so the model is
absent. At registration, a Link from an exposed model to an `api=None` model must be `hidden=True`,
and so must an effect target's `source` when the posting document is `api=None`; otherwise
registration fails naming the model and field. A caller cannot write a Reference that names an
`api=None` model.

**Reports (owner decision, 04-11-2026).** A Python report may read an `api=None` model. Its `run()` is
trusted code, so `context.list` and `context.aggregate` reach the model; the report route stays a caller
and checks the report's own `roles`. The report is listed and runs normally for those roles and shows
only the columns and totals it returns. The catalog never names the hidden model: its `model` and any
link column's `link` are null, and a column `source` on it is not hidden by field rules.

**Frontend.** The schema carries `api` per model. Lists and record pages of an `api="read"` model
show no New, Edit, Delete, actions or moves. The permissions already say false; `api` makes the
reason plain.

## 2. Period lock

`Meta.period = "posting_date"` names a stored Date or DateTime field. `Meta.period_scope =
"company"` optionally names a stored Link whose target scopes the lock. Both are checked at
registration.

**Storage.** `evlet_period_locks(scope, lock_date, changed_by, changed_at)` beside the other
Controls tables (`ensure_schema`). `scope` is `""` for the instance lock or `<model>:<id>` for one
record of a scope model. Every change writes an `evlet_audit` row in area `Periods` with before and
after; that is the history Controls shows. The lock is read straight from the table in the writing
transaction, so a lock saved by one process is enforced by every other one at once, and a trusted
script with no Controls running still sees it.

**Which lock applies.** The later of the instance lock and the lock of the record's scope value.
A scope lock can close more, never reopen what the instance lock closed.

**Enforcement.** In the storage layer, for every writer:

- `Session.create`, `Session.update`, `Session.delete` (plain models, trusted scripts, and the
  document pipeline's own delete);
- `documents.operate` before it writes (create, update, actions, moves, lifecycle), comparing the
  saved and the new values;
- `corrections` void and unpost, which update the State directly;
- `effects.write` for every posted and mirror row.

A record is refused when its saved value or its new value is on or before the lock. Error:
`{period field: "This date is in a closed period (locked up to DD-MM-YYYY)"}`, a 422 over HTTP and a
ValidationError in Python.

**Override.** `with tx.unlocked_periods(reason):` on a Session (or `context.unlocked_periods(reason)`)
lets that block write closed periods. The reason is required, logged on the `evlet.periods` logger and
written to the audit log. Nothing over HTTP reaches it.

**Mirror dating.** On void or unpost, a mirror row of a target with `Meta.period` keeps the original
row's date when that date is open. When it is closed, the mirror takes the first open date: the day
after the lock that applies to the row, or today in the instance timezone when that is later. A
repost writes fresh originals with the document's own dates, so it is refused when one of them is closed.

**Controls.** `GET /api/_controls/periods` lists the period models, the locks and the last 50 history
rows; `POST /api/_controls/periods` `{scope, date}` sets or clears one lock (`date: null`). Admin only,
with the usual recent two-step code. The Periods page (Controls, Build) shows them.

## 3. Decisions for the owner

- Journal entries (notes, messages) count as writes, so an `api="read"` model takes none from callers.
- Mirror rows in a closed period move to the first open date, not today, unless today is later.
- A scope lock and the instance lock combine as the later of the two.

Decided by the owner: Python reports may read `api=None` models (section 1, Reports).
