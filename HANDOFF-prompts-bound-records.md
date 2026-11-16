# Handoff (continued): Prompt and bound records (part B+E), then finish part D

You're continuing an owner-approved handoff on evlet-io/evlet. An earlier session finished parts A and C and ran out of budget:
- PR evlet-io/evlet#251: Part A (release semantics), base main.
- PR evlet-io/evlet#252: Part C (remove Effect/Swap/Negate), stacked on #251, branch `feature/remove-effects`.
- evlet-io/evlet#250 (derive() ordering, `derived=True` on fields replaces `Meta.derives`) is already in main. Use `derived=True` and never `Meta.derives` in anything you write.
- Part D is half done on branch `feature/remove-legacy-action` (commit eb48339, based on main, no PR). Done there: `Action`/`actions=` removed from Application, AppDefinition and the scaffold template; the legacy route removed; the Playground's 4 actions rewritten as `@action` methods; docs and CHANGELOG updated. Still failing are about 13 tests, because the Playground's Customer and Invoice are now documents: updates need If-Match (428), actions answer 200 with `_job` instead of 202 {job, record}, and creates have no Location header. Affected files: test_api_keys, test_jobs, test_events, test_identity, test_create_idempotency, test_ledger, test_business, test_sign_in_rules, test_connector, test_assistants. Adjust the tests to the document behaviour; don't weaken any permission check.

Your work:
1. **Part B+E** (spec below). Branch `feature/prompts-bound-records` from `feature/remove-effects` (A+C). Open a PR with base `feature/remove-effects`. The parent session merges A and C into main soon; then merge main into your branch and retarget the PR to main.
2. **Then part D.** Rebase or merge `feature/remove-legacy-action` onto your B+E branch, fix the failing tests, and open a PR stacked on B+E.

Rules:
- **Never merge PRs.** The parent session (session_014mLhsptF8uKyswXadaKZjU) verifies and merges.
- Send that session a short send_message when each PR is ready, and if you run low on budget, so it can continue the work.
- **Keep it concise.** The owner has stressed that the state manager is already a concise codebase, so don't bloat it. Remove more than you add where you can: Slip, Dialog and their renderer go away entirely. Reuse settle, the finite plan, the record layout code and the frontend record engine; don't build parallel ones.
- Commit early and push often, so the work survives a budget cutoff.
- Tests: `.venv/bin/python -m pytest` (slow, about 35 minutes; run the affected files first), and `npm test` plus `npm run build` in `frontend/` (Node 24). These are known to fail in this container on main for environmental reasons: tests/test_cli_setup_one_go.py when run as root, three test_printing tests (jinja2 missing), and test_schema_fixtures_match_python_metadata (verify it still fails the same way on main before ignoring it).

The owner's spec follows, verbatim from the handoff.

## How to work

- Read `AGENTS.md`, then `docs/states-actions.md`, `docs/effects.md`, `docs/slips.md`,
  `docs/finite-calculations.md`, `docs/architecture.md` (Package layers) before changing code.
- **Remove, don't deprecate.** Evlet has no users yet (owner decision). Removed public names, routes and
  HTTP shapes need no compatibility path or `DeprecationWarning`. Write the CHANGELOG "Upgrading" notes
  anyway, so the change is documented.
- One feature branch and one PR per part, in the order below. Never merge; leave each PR for the owner.
- Stay on 0.4.x patch numbering; do not bump the minor or major version.
- Keep the package layers (`tests/test_layers.py`), the query builder boundary (no SQL outside the
  dialect), backend permission checks on every surface, real SQLite in tests (no mocks).
- Every PR: tests for the new behaviour and the removals, docs updated, Playground updated where it
  demonstrates the feature, `.venv/bin/python -m pytest`, `npm test` and `npm run build` in `frontend/`
  all passing.
- **Keep the current state manager syntax** (owner decision): `State`, `State.Step`, `State.Rule`,
  `State.Level`, `State.Remark`, `@validate`, `@state`, `@state.exit`, `@release`, `@release.exit`,
  `@action`. This revision changes behaviour, not those declarations.

## B. `Prompt`: one transient form, replacing `Slip` and `Dialog`

Why: today there are four transient-form mechanisms (Slip with its own buttons and routes, nested
Dialog, action `inputs`, the move remark/ask dialog), with string-only rules and a separate renderer.
Owner decision: one concept, like a Frappe dialog. **An action is the button; a Prompt is the form it
opens; the prompt's `submit()` is the callback.** A prompt is never shown any other way.

```python
from evlet import (Child, Currency, Date, Decimal, Main, Model, Outcome, Prompt, Table, Text,
                   ValidationError, action, validate)
from evlet.calc import F, today

class ConfirmQuotation(Prompt):
    order_date = Date(required=True, initial=today())
    customer_po = Text(max_length=60)

    class Line(Child):
        quotation_line = Text(read_only=True, hidden=True)
        item = Text(read_only=True)
        open_qty = Decimal(scale=2, read_only=True)
        confirm_qty = Decimal(scale=2, minimum=0)
        rate = Currency(read_only=True)
        amount = Currency(compute=F('confirm_qty') * F('rate'))      # finite-v1, as on documents

        @validate
        def within_open(self):
            if (self.confirm_qty or 0) > self.open_qty:
                raise ValidationError({'confirm_qty': 'More than is still open'})

    lines = Table(Line)
    total = Currency(compute=lines.sum('amount'))

    class Meta:
        title = 'Confirm quotation'
        primary = 'Create sales order'                               # main button label
        layout = (Main(('order_date', 'customer_po', 'total')), Main('lines'))

    def load(self, quotation):                                       # prefill; also self.source
        self.lines = [{'quotation_line': str(r.id), 'item': r.item, 'open_qty': r.qty - r.confirmed_qty,
                       'confirm_qty': r.qty - r.confirmed_qty, 'rate': r.rate}
                      for r in quotation.lines if r.qty > r.confirmed_qty]

    def submit(self):                                                # the callback, one transaction
        quotation = self.source                                      # a bound record (part E)
        order = quotation.create_order(self.lines, self.order_date, self.customer_po)
        return Outcome(open=order)

class Quotation(Model):
    @action('Confirm', available_when=(F('docstate') == 'submitted') | (F('docstate') == 'partial'))
    def confirm(self):
        return ConfirmQuotation              # or ConfirmQuotation(customer_po='…') with starting values
```

1. **`class X(Prompt)`**: a model without storage. Fields, `Child` Tables, finite-v1 formulas and field
   rules (`applies_when`, `required_when`, `locked_when`, `initial`), `@validate` on the prompt and its
   rows, `Meta.title`, `Meta.primary`, `Meta.layout` with `Main` / `Section` / `TableSection`,
   `Meta.focus`, `load(self, source)`, `submit(self)`. No table, no migrations, not in the registry's
   stored models.
2. **Shown when returned:** an `@action` (or a prompt's `submit()`, or an `@api` function, item 7) returns
   a Prompt class, or an instance carrying starting values. Its source is the action's record
   (`self.source` inside the prompt, a bound record per part E). An action that returns a prompt is
   checked (roles, `available_when`, permission, row scope, revision) and writes nothing.
3. **Prompt ticket** (server-enforced "only when returned"): returning a prompt over HTTP stores a
   single-use ticket: prompt name, source model/id and revision, the presenting action, the user, an
   expiry (default 1 hour). The response carries the ticket and the prompt surface (definition, layout,
   values after `load()`, rules, states). Store tickets in a framework table on the main database file.
4. **Routes:** `POST /api/_prompts/{ticket}/check` (server-only checks: Link choices and filters,
   validators, `load()`-dependent values) and `POST /api/_prompts/{ticket}/submit`. Submit runs in one
   transaction: re-check the presenting action (roles, `available_when`, permission, row scope) on the
   source as it is now; check the source revision; settle and fully validate the prompt; run `submit()`;
   mark the ticket used. A used, expired, other user's or unknown ticket is refused. The ticket is the
   idempotency key: a retry of a completed submit returns the first result.
5. **`Outcome`** is the one result type for actions and prompts: `message`, `open=record or (Model, id)`,
   `new=(Model, values)` (open a prefilled new record for review; replaces `becomes`/`review`), `data`,
   or return another Prompt to chain (a wizard).
6. **Centralized engine:**
   - Backend: prompts use the same draft, `settle`, finite plan (`plan_for`) and validation path as
     documents. Remove the refusal of `F(...)` in transient forms. Report filters gain finite support the
     same way.
   - Layout: `Meta.layout` through the record layout code (`sections.py` / `ui/metadata.py`). The slip
     tuple `layout` goes.
   - Frontend: a prompt opens in a modal built from the record view's engine (record store, finite
     session, layout blocks, child table grid, field components), with live calculations in the browser.
     Server calls only for `check` (links, validators) and `submit`. Remove the separate renderer in
     `frontend/src/evlet/app/slips.ts` and the server check on every change.
7. **Prompts without a record** (setup wizard, quick expense): an `@api` service function may return a
   Prompt; its source is `None` and the ticket records the endpoint instead of an action (re-checked on
   submit). Reached by a workspace shortcut or menu entry (default for an open question).
8. **Action `inputs={...}` stays** as a shorthand: the server turns it into an anonymous Prompt; same
   engine, renderer and submit path. The move remark / `ask` dialog renders through the same component.
9. **Tools / MCP / Eva:** an action returning a prompt gives the tool the ticket and the prompt schema;
   add a tool to check and submit a prompt by ticket, under the same checks and the existing approval
   rules (default for an open question). The action tool stops skipping dialog actions.
10. **Removed:** `Slip`, `slips.py` routes `/api/_slips/*`, `present()`, slip `source`, `menu`, `roles`,
    `permission`, `becomes`, `review`, `hand_in`, `hand_in_label`, tuple `layout`, slip buttons in the
    schema and the record page, `?slip=` record links; `Dialog`, `forms.py` dialogs and `/api/_dialogs/*`,
    `examples/amendment` `CreateWorkOrders` (rewrite as an action returning a prompt). Tests: 5 files use
    `Slip`, 5 use `Dialog`.

Naming note: `Prompt` is the owner's choice. In an AI-native framework "prompt" may later also mean an
LLM prompt (Eva); keep framework and Eva names apart (e.g. Eva's are "instructions").

---

## E. Bound records: `context.get()` returns a document you can act on

Why: like `frappe.get_doc()`. Owner wants `customer = self.context.get(Customer, id)` then
`customer.convert({...})`, `customer.qualify()`, `customer.save()`, `customer.delete()`.

```python
customer = self.context.get(Customer, customer_id)        # or self.context.get('customer', customer_id)
if customer.docstate == 'lead':
    customer.convert({'credit_days': 30})                 # action; values answer its prompt, no prompt step
customer.qualify(remark='Met at the event')               # a state rule by its key (= move('qualify'))
customer.phone = '+971 50 000 0000'
customer.save()                                           # one update with the changed fields
customer.delete()
```

1. **Returned by** `context.get`, `context.create`, `context.list` (each item): field values as
   attributes, child rows as lists of row objects, bound to the context that read it and to its
   revision. Replaces `Snapshot` as the public read type.
2. **Actions as methods:** `record.<action>(values=None, **values)` runs `context.action(...)` with every
   check. Values fill the action's `inputs`. If the action returns a Prompt, the values fill the prompt
   (`load()` values for the rest), the prompt is validated and its `submit()` runs in the same
   transaction; nothing is shown and no ticket is needed (trusted Python). A missing required prompt
   value raises `ValidationError` keyed by field. Returns the action's or prompt's result. Called with no
   values, an action that returns a Prompt returns the loaded prompt; `prompt.submit()` submits it.
3. **State rules as methods:** `record.<rule key>(remark=None, **asked)` = `record.move(key, ...)`: full
   move checks, hooks, approvals (an approval rule approves the waiting level). System rules (`by=()`)
   are callable from trusted code and still never offered as buttons. Rules added at runtime through
   Controls overrides have no method; `record.move(key)` works for every rule. No generic
   release/submit/cancel helpers: states move only by rule key (owner decision). Docs recommend explicit
   rule keys.
4. **`record.move(key, remark=None, **asked)`**, **`record.save()`** (attributes changed since the read →
   one `context.update`, with the read revision as If-Match; a changed record raises a conflict),
   **`record.delete()`**, **`record.reload()`**. Afterwards the record holds the saved values.
5. **Inside its own write** (an action or hook whose `self` is that record): calling another action on
   `self` runs its checks and body on the same draft instead of being refused as recursive.
6. **One namespace, checked at class creation:** field names, action names, rule keys and the reserved
   names `save`, `delete`, `move`, `reload` must be distinct (today rule keys and action names may
   coincide). The error names both.
7. **Where writes are allowed:** hooks, actions, prompt `submit()`, `@api`, `@cron`, jobs, scripts.
   `@validate` is read only (record methods that write raise there); `derive()` reads through its
   read-only session. Context reads run as the context's user (row scopes, field rules).
8. **Unbound records** (built by hand, or read through a raw `Session`) raise `Get the record through a
   context to run its actions`.
9. `context.action/move/update/delete(Model, id, ...)` stay as the underlying calls. HTTP is unchanged
   by this part.

Tests: each method path; a prompt answered by values (no ticket); prompt values missing; nested action on
self; reserved/duplicate names fail at class creation; unbound record refused; `save()` conflict.

---

## D. Remove legacy `Action(...)`

Code: `business.py` (`Action` class ≈52, `actions=` on `Business` / `Application`, the legacy route
≈843, schema entries ≈503–546), `app.py` (≈366), `__init__.py` export, `docs/business-capabilities.md`
(Registered actions), `docs/states-actions.md` (≈553).

- Rewrite the Playground's four registered actions (`examples/playground/app.py` ≈173) as `@action`
  methods: Post to ledger, Award points, Recalculate progress (`self.context.enqueue`), Rebuild all
  points balances (admin).
- Tests: about 15 uses in 8 files.

---

## Demo (build with PR 3, in the Playground or as tests)

**Quotation**: customer, quote date, valid until; lines (item, qty, rate, amount formula,
`confirmed_qty` `versioned=False` read-only, `open_qty` virtual); total.
Steps: draft, revision, submitted, partial (“Partially confirmed”), confirmed, terminated, cancelled.
Release steps: submitted, partial, confirmed, terminated (locked by implication). Final: terminated,
cancelled.
Rules: Submit (draft, revision → submitted, primary, `when total > 0`), Revise (submitted → revision,
optional remark), Cancel (draft, submitted → cancelled, required remark), Terminate (partial, confirmed →
terminated, required remark), and system rules (`by=()`, never buttons): `part` and `full` (submitted,
partial → partial / confirmed), `reopen_part` and `reopen_all` (partial, confirmed → partial / submitted).
Action **Confirm** (submitted or partial) returns `ConfirmQuotation` (part B). Its `submit()` calls a
model method `create_order(...)` that creates a **Customer Order** with the confirmed lines, moves it to
submitted (`order.submit()`), adds the confirmed quantities to the quotation lines' `confirmed_qty`,
moves the quotation by `full` or `part`, and returns the order, which the UI opens.

**Customer Order**: customer, quotation link, order date, customer PO, lines with `quotation_line`,
total. Steps draft, submitted (release), cancelled (final). `@release.exit` (cancel of a submitted order)
gives the quantities back to the quotation lines and moves the quotation by `reopen_part` or
`reopen_all`.

Buttons people see: draft/revision → Submit, Cancel; submitted → Confirm, Revise, Cancel; partial →
Confirm (what is still open), Terminate; confirmed → Terminate.

Tests: confirm all lines (quotation confirmed); confirm some (partial), confirm the rest (confirmed);
cancel an order (quotation back to partial/submitted); a single prompt submit creates and submits the
order in one transaction and rolls back fully on error; Python path `quotation.confirm({...})`.
Verify: trusted code can write the read-only `confirmed_qty` through `Context.update` / a bound record;
if not, add and document the way.

---

## Defaults taken for open questions (state them in the PR descriptions)

The owner may change these; implement the default unless told otherwise.

1. Prompts without a record come from `@api` functions (B.7).
2. Eva / MCP / tools support for prompts ships in PR 3 (B.9).
3. Versioned content cannot change inside the released set (A.5).
4. The name is `Prompt` (owner's choice).

## Out of scope

- The `derive()` ordering change (separate handoff).
- Changing state manager declaration syntax (`State`, `Step`, `Rule`, decorators).
- Flow/Stage/Move renames, `Meta` restructuring, merging `qb`/data layers, `core`/`engine` merges.
- New UI beyond what the parts above need.