# testCRM

testCRM is a small CRM declared only in Python models: `examples/testcrm/models.py`. It has no
frontend code and no custom views. Every screen comes from the generated UI at the site root (`/app/<model>`). It is the
working tester for the model engine and the UI framework (phase `ui/04-testcrm` of
[handoff-plan.md](handoff-plan.md)). The data is fictional. It is not a production CRM.

## Run it

```sh
bash scripts/testcrm.sh
```

The launcher needs the Playground's setup (`bash scripts/playground.sh --setup`). It:

1. creates a disposable project in a new temporary directory,
2. copies `examples/testcrm` into its `apps/`, creates the instance `crm` (timezone
   Asia/Dubai) and installs the app, exactly like `evlet install`,
3. seeds fictional UAE data (`scripts/testcrm.py`) and prints the users and their password,
4. starts the backend on `127.0.0.1:8010` and the UI development server on `127.0.0.1:5180`,
   then prints the address: `http://127.0.0.1:5180/`.

Control-C stops both servers and deletes the project. `EVLET_KEEP_DATA=1` keeps it.
`EVLET_BACKEND_PORT` and `EVLET_FRONTEND_PORT` change the ports, and `EVLET_TESTCRM_PASSWORD`
sets the password instead of a random one. Nothing touches `examples/playground/.data`.

Users: `admin` (admin), `sara`, `omar` and `rahul` (sales people, the editor role) and `vera`
(viewer). The seed has 10 organizations, 12 contacts, 8 products, 17 deals in every stage
(three won, one lost, one with 150 lines), 8 leads and tasks.

To build a project without serving it: `.venv/bin/python -m scripts.testcrm build DIRECTORY`.

## The app package

A flat app needs only `__init__.py`, `pyproject.toml`, `setup.py` and `models.py`. Before this
phase only a `models/` package was discovered; a single `models.py` module now counts too.

## What each model shows

| Model | Engine features |
|---|---|
| Organization | `Select`, `URL`, `Phone`, `Tags`, bounds, `Meta.layout`, `key_fields`, `relations` with order and labels, per-record measures over deals (`Count`, `Sum` with `where`), a model measure, `Attachments` |
| Contact | `Link` with an index, `Email`, a stored calculation through a Link path (`F("organization.emirate")`) |
| Product | `unique` field, `Boolean`, read only for sales |
| Booking | Effect target (`source = Reference()`, `reversal`), read only for every role, a measure |
| Deal | `Sequence` (`DL-{year}-{n:04}`), `State` with guarded moves (`when=`), a role-limited move, locked, final and voidable states, `Meta.revisable`, `run=` on a move, `@action` with `inputs` and `available_when`, `applies_when` and `editable_when_locked`, `initial=` through a Link, a calculated `Percent` through nested `when()`, `round()`, `days()`, `coalesce()`, `today()` in a virtual result, a `Table` of up to 1,000 lines, `sum`, `count` and `where(row.recurring == True)` aggregates, an `Effect` with `Negate`, model and per-record measures, `key_fields`, `relations`, `Attachments`, a row scope and field lists per role |
| Deal line | `initial=` from the product (description, rate, taxable), a discount `Percent`, an auto-rounded amount, VAT through `when()` and `parent.vat_rate` |
| Task | `Time`, `Duration` with `applies_when`, a virtual `Boolean` from `today()`, Links to Deal and Contact |
| Lead | `State` with a guard on the score, `applies_when` with `required_when` (see Gaps), an action that creates other records |

### Permissions

- admin: everything, plus void and revise on deals.
- Sales people (editor): read and write organizations, contacts, leads and tasks; read products
  and bookings; on deals a row scope (`owner` is their username) and field lists without
  `internal_notes`. A deal they create must name them as owner.
- Viewer: reads everything except a deal's internal notes and the fields only sales use.

### Interconnected work

- **Winning a deal** (`Move(..., "won", run="on_won")`) sets `won_on` and the next step, makes the
  organization a customer through `context.update`, creates a kick-off task through
  `context.create`, and posts one booking through the effect, all in the move's transaction.
  Voiding or revising a won deal writes the reversing booking.
- **Schedule follow up** (an action with a date and a note) sets the next step and creates a call
  task for the owner.
- **Convert to deal** on a qualified lead creates the organization, the contact and a deal, moves
  the deal to qualified with `context.move` and links it to the lead.

## Tests

| Where | How | What |
|---|---|---|
| `tests/test_testcrm.py` | The installed app, real SQLite, authenticated ASGI requests | Sign in; schema per role; list sort, search, filter, paging and measures; field edits; line change sets (add from a product, edit, reorder, remove) and totals; 150 lines with paging; the lost reason rule; guarded moves and their reasons; win: task, customer, booking, revise and its reversal; actions; the row scope and hidden fields for sales and viewers; two sessions editing one deal; the journal of a won deal, notes and follow ups |
| `frontend/src/evlet/tests/testcrm-*.test.ts` | The real screens in jsdom over responses recorded from testCRM (`testcrm-fixture.json`) | Sign in and the sidebar; list search, saved views per stage, sorting; the board moved with the keyboard; the command palette; lines edited, added from a product, reordered, saved and removed with instant totals; moves to won with the lock, the posting and the created task; a blocked move; lost with the reason edited in place; 150 lines paged in and one edit saved as a change set; a conflict and Reload; another person's deal; a viewer and an admin; the Activity tab and a note |
| `npm run bench:ui` | jsdom | The 100-deal list, the 1,000-line deal and one line edit ([performance.md](performance.md)) |

Regenerate the recordings with `.venv/bin/python -m scripts.generate_testcrm_fixtures` and
review the diff. Each scenario is a list of phases: the reads the screens make, one write, then
the reads after it. The replay stub (`testcrm-helpers.ts`) answers a read from the latest phase
reached and moves on at each write; a request the recording lacks fails the test with its key.
Dates set from today (`won_on`, the kick-off task) follow the day of recording.

The same flows were driven by hand in a real browser against `scripts/testcrm.sh` (sign in,
sidebar, search, lines, product suggestions, reorder, save, moves, win, lost reason, lead
conversion, board drag and keyboard, palette, 150 lines, scope, viewer, conflict).

The journal is checked on both sides. `test_activity_journal_records_moves_the_win_and_what_people_add`
reads a won deal's history over HTTP: the moves, the win with the kick-off next step and its date,
the Booking posting (`effects`), the earlier edits, the number and the create, then a note, a
follow up and its completion, a viewer's partial view and another sales person's refusal. The
screen test opens the same deal on its Activity tab and adds a note through the journal route.

## Gaps found

- **A field required in a state blocks the move into it.** Lead's `lost_reason` uses
  `applies_when` and `required_when` on `lost`, as in the handoff. Before the move the field does
  not apply, so it is empty, and a move takes no values, so `Mark lost` always answers 422
  `lost_reason: This field is required`. The screen only says "Mark lost did not run". Deal keeps
  `applies_when` without `required_when` so it can be lost. Moves need inputs (like actions) or a
  rule that is checked after the move.
- **Owner is typed.** A row scope can compare a field with the user (`{'owner': user.username}`),
  but nothing fills it with the current user, so a sales person types their own username on a new
  deal. A default from the signed-in user is missing.
- **"Won this month" is not a declared measure.** `where=` cannot compare dates with `today()`;
  the list strip shows won value overall, and the month is an aggregate by `won_on:month`.
- **Errors without names.** `A document field conflicts with its draft interface` does not name the
  field (here a field called `source`, which documents reserve). `Layout must include every visible
  field exactly once` does not name the missing fields, and [ui-framework.md](ui-framework.md) says
  fields left out of a layout go under Other.
- **A Child at module level is a root model.** Discovery registers every `Model` subclass in
  `models.py`, so a `Child` used by `Table(...)` must be nested in its document class.
- **Screens.** Fixed here: the child table footer took a filtered sum (`recurring_total`) for the
  Amount column; the lines lock note kept the state the page opened with; the Related side block
  did not count again after a move or action. Still open: a list measures strip that ignores the
  search box; one title request per linked record on list pages and a repeated lookup on record
  pages; a Link cell in the grid that does not start searching when the user types; "To correct
  it, void or revise" on a lost deal, which cannot be voided; Undo offered after a board move, which
  needs a move back the model may not declare (not checked).
- **Performance.** Opening a 1,000-line deal takes about 160 ms in jsdom, over the 100 ms budget.
