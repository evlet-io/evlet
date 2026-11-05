# Evlet UI

The browser interface is built on Evlet's own UI framework, described in
[ui-framework.md](ui-framework.md). Its code lives in `frontend/src/evlet/`. It has no
runtime dependencies. A session opens Docs after sign-in. Every model the role can read
gets a list and a record view, built from `/api/schema`, record details and the list
routes, so a model-only application needs no frontend code.

The previous SolidJS interface and the Vue regression harness have been removed.

## What this interface renders

- Sign-in and first-run setup (the first administrator account).
- Docs after sign-in, with its topics.
- The shell: sidebar with the models, recent records, search palette and New menu.
- Generated model screens: lists, boards for models with a State, record views (on their own
  page or in a side panel beside the list, `Meta.open_in`), new records, actions, moves, void and revise, unpost and repost, child tables, measures, related records and
  files.
- Reports ([reports.md](reports.md)): filters, typed rows, totals, a chart, Export and Print at
  `/reports/<name>`, listed in the sidebar.
- Slips ([slips.md](slips.md)): forms presented from a record, the New menu, an action or browser
  code, checked on the server and handed in to a handler or turned into a record.
- Printing ([printing.md](printing.md)): a Print button on record views for roles that may print the
  model, when the server has the print extra (in a side panel, Print is in the More menu). It opens a drawer with the server's PDF in the browser's
  own viewer, a choice of design, Download, and File a copy on records that keep attachments.

Tables keep per-person column choices. The Columns menu of a record's child tables and of a report
orders columns by dragging (or the keyboard), shows or hides them, and pins any number on the left:
columns above the menu's divider stay in place while the table scrolls sideways. Prefs saved before
pinning keep the first column pinned. A child table's Focus view keeps the record's header and its
save bar in view ([ui-framework.md](ui-framework.md)).

Model `Meta` options shape these screens: `list_fields`, `title_field`, `search_fields`,
`layout`, `key_fields`, `relations`, `measures`, `open_in` and `status`. See "What the model decides" in
[ui-framework.md](ui-framework.md).

A record's State shows as a coloured badge in the header by default, with its moves as header
buttons; `State(..., show="bar")` draws the stage bar instead, for pipelines, and `show="none"`
draws neither. `Meta.status` adds a status badge for a Select the system sets, such as a
payment status, coloured by `Select(..., colors={...})`. Badges and bar steps offer only the
moves this person may take now; everything else is plain text. Lists draw the same badges. The
badge wraps with the key facts in the side panel and on a phone; the bar scrolls sideways there.
See [states-actions.md](states-actions.md#how-a-state-and-a-status-show).

## Not rendered by this interface

These keep their Python APIs, HTTP routes, permission checks and tests. The browser
interface does not show them (owner decision 3 in [handoff-plan.md](internal/handoff-plan.md)):

| Python API | Status |
| --- | --- |
| Python pages: `PageView`, `ListView`, `FormView`, layouts, `ViewRegistry` extensions and `CustomComponent` | Compiled and served by `GET /api/ui` and `GET /api/ui/resolve`. A page route opens Docs in the browser. Use generated screens and `Meta` options instead |
| The Playground's Projects demo and Layouts page (`examples/playground/ui.py`) | Registered as Python pages, so not rendered |
| Native TS/JS panels (`evlet/native.py`, `views/` modules in an app) | Built, hashed and served from `/assets/apps/...` for signed-in users as before. No screen mounts them |
| The older record-bound forms (`dialog: true` actions) | Run through their existing routes. Use [slips](slips.md) for forms |
| Account management | `/api/auth/users` routes are unchanged. `/settings/accounts` shows a notice |

## Build and run

From the repository:

```sh
bash scripts/playground.sh --setup
```

Open http://127.0.0.1:5173. Keep the launcher running. Python source changes reload
the backend; browser source changes update through Vite. Set `EVLET_RELOAD=0` to turn
off backend reload. The launcher uses loopback and does not replace an occupied port.
In development, http://127.0.0.1:5173/evlet.html opens the component Library. It is
not part of the production build.

Build the bundled UI before building a Python distribution:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m build --no-isolation
```

`npm run build` type checks, builds `frontend/index.html` into `frontend/dist` and copies
it into `evlet/ui/static/`. The wheel serves those files through `Application`,
including direct URLs and browser refresh. Installed Python applications do not need
Node. Node is needed when changing the browser code or building from source.

The production page loads no external fonts or scripts, so it works under the server's
content security policy (`default-src 'self'`). It uses the system font stack.

## Routes

The Python server returns the app for these paths (`evlet/ui/routes.py`, through the
instance route table in [routing.md](routing.md)). Any other path outside `/api/` and
`/assets/` returns the app with status 404, so the browser shows its own page.
`/docs` is on in development and off once an instance sets a domain, unless its
`routes.py` turns it on. The session's `application.docs` tells the browser; with
Docs off it starts on the first list the role can open and hides the Docs links.

| Path | Screen |
| --- | --- |
| `/`, `/login` | Sign-in, or Docs when signed in |
| `/docs`, `/docs/<topic>` | Docs |
| `/app/<model>` | List (and board). Search, sort, page, view and filters are in the query |
| `/app/<model>/new` | New record. Query values fill writable fields: `?customer=4` |
| `/app/<model>/<id>` | Record view. `?tab=` keeps the tab |
| `/app/<model>/<id>/view` | Older record link; opens the record view |
| `/settings/<page>`, `/settings/app/<app>` | Settings ([settings.md](settings.md)); `/settings` opens the last page |
| `/settings/accounts` | Redirects to `/controls/users` |
| `/eva`, `/eva/<chat>`, `/eva/tasks` | Eva's chat and scheduled tasks ([eva.md](eva.md)). Served only when config.py turns Eva on; the page asks `/api/_eva` and shows nothing of Eva to people without it |
| `/reports`, `/reports/<name>` | The reports this role may run, and one report's view ([reports.md](reports.md)) |
| Registered Python page routes | Served, then open Docs |

The development-only `/ui` and `/m/<model>` paths of the earlier preview are gone.
Signing in keeps the address the user opened. A record with unsaved changes asks
before the page changes or the tab closes.

## A small application

```python
from evlet import App, Model, Text, Boolean

class Note(Model):
    title = Text(required=True)
    done = Boolean(default=False)

app = App(name="notes", models=[Note])
```

Run `uvicorn app:app --host 127.0.0.1 --port 8000 --reload`. Open
http://127.0.0.1:8000, create the first administrator, then sign in. Notes has a list
and a record view. Search, filters, sort and paging run through the model query API.
Role, field and record permissions stay on the server.

`App` is a local convenience entry point. Its default database is `.data/notes.sqlite3`;
its default authentication allows initial setup and insecure cookies only on exact
loopback origins on port 8000. A deployed site must supply its database and explicit
`LocalAuth` configuration. Existing `Application(database, auth=..., ...)` consumers
can pass `views=[...]` or `ui=ViewRegistry(...)` without changing model APIs.

## Records and concurrency

Authenticated record GET/PATCH responses include ETag headers. The record view sends
If-Match on edits, deletes, moves and actions. A stale revision returns 409 and keeps
the draft. Clients without If-Match keep their behavior. Revision comparison happens
inside the same transaction as the mutation and includes permitted child rows.
Currency and decimal values stay exact strings. A Currency field shows its code from
`currency=` on the field: a fixed code, a field of the record, a field of a linked record or an
app setting ([model-engine.md](model-engine.md#where-a-currency-fields-code-comes-from)).
Selects, dates, date-times, times and durations open Evlet's own pickers ([ui-framework.md](ui-framework.md#pickers)).

### Collapsible sections

Sections declared with `evlet.Section`, or set to Can collapse in Controls, open and close
from their heading: a real button with `aria-expanded` and `aria-controls`, so Enter and Space
toggle it, and Tab skips the fields of a closed section. A closed section shows how many of its
fields are filled ("3 of 5 filled"), or "Required missing" when a required field is empty, and
a badge with the number of fields that have errors.

- **Starting state.** The app's `collapsed` value, unless the person chose: open and closed are
  remembered per user and model in their browser preferences, by section title, and the record page
  and the side panel share them. Sections closed in a side panel before this release carry over once.
  Reset layout in the record's More menu forgets those choices.
- **Phones.** Below 760 px, collapsible sections after the first start closed unless the person's
  saved choice says otherwise. This is the default behaviour.
- **Errors and focus.** A field error opens its section. A refused save opens every section with an
  error, then scrolls to and focuses the first one. A `#field` address
  (`/app/project/01hc1sd9vc1ye#starts_on`) opens that field's section and focuses it; code can do the
  same with `revealField(root, name)`.
- **Same everywhere.** New, read-only and locked records fold the same way. In the narrow side panel
  every section can close. Print and PDF show everything: the browser's print style opens closed
  sections, and the server's print designs never use the form layout.
- Height animates only when the device does not ask for reduced motion.

### Field and tab order

The DOM order of a form is its visual order, and nothing uses a positive `tabindex`. A section with
`flow="columns"` lists its fields down the left column, then the right, so Tab goes the same way.
Tab skips values that can't change (read only, locked or calculated); a click still focuses them,
showing why, and screen readers read them. Record tabs follow `Meta.tabs` or the Controls tab order,
and a record opens on its default tab, which the address then leaves out (`?tab=` names the others).

**Enter moves to the next field** (Settings, Appearance, off by default) makes Enter go to the next
field of a form and Shift+Enter to the one before, skipping fields that can't change. It never
applies in long text or rich text, on buttons, or while a picker or list is open, and Ctrl or Cmd
Enter still saves. On a saved record, where each value saves on its own, Enter keeps saving the value.

### Ids, names and titles

Record addresses use the record's text id, `/app/invoice/01hc1sd9vc1ye`, so changing a name never
breaks a link or a bookmark. An older bookmark with a decimal id opens the same record and the
address changes to its text id. A record page shows the record's `title` as its heading and, when
the model has naming, its `name` next to it in a monospaced font, or "Assigned on save" or
"Assigned on confirm" until it has one. Link pickers, list rows, search and the recent records
show titles; the schema's `title_field` is `title` for every record model
([model-engine.md](model-engine.md#name-and-title)).

### Created and last modified

The Record info block of every record page shows when the record was created and last changed, and
by whom: `Created 2 hours ago · Aslam`. Hover or focus a time for the exact time in your zone
(`04-11-2026 12:05 GST`); click it to show every time there exactly, which is remembered for you. The
times move on by themselves while the page is open. Who links to Controls Users for people who may
open Controls. Portal users see the times only. See
[model-engine.md](model-engine.md#record-times) for what is recorded and
[ui-framework.md](ui-framework.md#relative-times) for the `RelativeTime` component.

### Link filters

A Link with `filters` ([business-capabilities.md](business-capabilities.md#link-filters)) is
handled in the form with the values the person has entered, saved or not:

- The picker lists only records that fit, by sending the filters to the list route. While a field
  the filters read is empty, it lists nothing and says "Choose Company first.".
- When a field changes, every Link whose filters read it is checked again: on the same row, and
  `Ref("parent.<field>")` Links on the rows of its tables. A value that still fits is kept. One that
  no longer fits is cleared, with a note under the field (a tooltip in a table cell) such as
  "Cleared because Company changed". Clearing a Link checks the Links that read it in turn, so a
  chain such as company, branch, floor clears together. The browser decides from a record it
  already holds; otherwise it asks the list route (`id__in` and the filters), so the role's row and
  field permissions apply. When it cannot tell, the value stays and the save reports it.

The server still checks every Link on preview and save; the form only avoids keeping a stale value.

### Filters

A list's Filter button (with a count of active filters) opens a panel of filter rows under the
toolbar, as in Frappe. Each row is a field, a condition and a value, with a remove button; Add
filter adds rows, and every row must match (AND). The field picker searches the model's fields
and then each child table's ("Prices → Rate"), by any words of the label, and shows each field's
type; it draws at most 100 matches at a time, so a model with hundreds of fields stays quick.
Arrows and Enter pick a field.

Conditions follow the field type: text has equals, not equals, like, not like, is set, is not
set, in and not in; numbers and currency have =, ≠, >, ≥, <, ≤ and between; dates have on,
before, after, between and a timespan (today, this week, last month and so on, in GST); links
have equals, not equals and in, with the record picker, which lists only records the person can
read; selects have in and not in, as ticked choices; checks have is yes and is no. Not equals,
not in and not like also match empty values. The value inputs are the form's own controls (the
custom select, date pickers, currency input).

Rows on one child table form a group with a choice: "Any Prices row meets all of these" (one row
must meet every condition) or "No Prices row meets all of these". Active filters show as
removable chips above the list, with Clear filters. The rows are kept in the address (`?f=` as
the server's filter list, with a timespan kept by name), so a filtered list can be shared and
survives a reload; links from before the builder (`?f={"stage":"open"}`) still open. A row that
has no value yet is not sent. The server checks every field, condition and value again and
refuses fields the role cannot read. The Playground's Items list (about 200 fields, with
prices and barcodes tables) shows the builder at scale.

There is no OR group between rows, because the server combines filters only with AND; and there
are no saved filters yet, because Evlet has no place to keep them. The saved views of a model's
State stay as tabs.

## Python page API

This part documents the Python page API. It compiles and validates pages and serves
them through `/api/ui`; the browser interface does not render them.

### Two ways to define a page

```python
from evlet.ui import PageView, Column, Card, Text, Button, Navigate

welcome = PageView(
    name="welcome", route="/welcome", title="Welcome",
    layout=Column(gap=16, children=[
        Card(title="Your workspace", children=[
            Text("Your application is ready.", id="greeting"),
            Button("Open notes", on_click=Navigate.to_list(Note)),
        ]),
    ]),
)
```

The same objects can be built incrementally:

```python
welcome = PageView(name="welcome", route="/welcome", title="Welcome")
welcome.layout = Column(gap=16)
card = welcome.add(Card(title="Your workspace"))
card.add(Text("Your application is ready.", id="greeting"))
card.add(Button("Open notes", on_click=Navigate.to_list(Note)))
```

`add`, `insert`, `find`, `remove` and `replace` operate during authoring.
`SchemaCompiler().compile(view)` validates the tree and returns an immutable byte
snapshot. `to_dict()` returns a fresh copy. Schemas contain data and registered
operations, never callables or executable source. `class_name` maps to the browser
class property. `style` accepts safe CSS property values.

Assign explicit IDs to elements referenced by extensions or actions. Other IDs
are derived deterministically from view names and positions. Inserting siblings
can change positional IDs; explicit IDs remain stable. Duplicate IDs, reused or
cyclic nodes, unsupported props/events/slots, unsafe values and missing event
targets are rejected with errors identifying the component.

### Contracts

`ComponentDefinition` declares name, category, property types, defaults, events,
slots, bindings, accessibility notes, renderer name and compatibility version.
`ui.components.catalog()` returns inspectable metadata.

`/api/schema` retains model schema version 1 and UI metadata version 1.
`/api/ui` provides the separate version 1 component/view schema. Both endpoints
are authenticated when the application uses authentication. Generated UI metadata
is filtered by the same model and field policy as the existing schema endpoint.
Custom pages can specify `roles=("admin",)` and `requires=(Customer,)`.
A page's UI visibility is not a replacement for server-side authorization.

`TextInput`, `NumberInput`, `CurrencyInput` and model `Field` definitions accept
`prefix` and `suffix`. These are display labels; they do not alter stored values
or convert currency.

```python
CurrencyInput(label="Amount", prefix="AED", bindings={"value": Bind("amount")})
TextInput(label="Website", prefix="https://", bindings={"value": Bind("domain")})
view.field("budget").prefix = "AED"
```

Python layouts include Row, Column, Grid, Stack, Container, Section, Card, Split,
Panel and FormLayout. A FormLayout uses 12 columns; a Field can set `width=6` or
`width=12`.

### Page routes

Generated page names keep the routes `/app/note`, `/app/note/new`, `/app/note/01hc1sd9vc1ye`
and `/app/note/01hc1sd9vc1ye/view`. The last segment is the record's text id; an older
bookmark with a decimal id (`/app/note/42`) opens the same record and the address changes
to its text id. Explicit pages use their registered routes. Custom parameters
use `/orders/{reference}` or `/orders/{id:int}` and are available in view state.
The route resolver rejects ambiguous routes. Routes start with a fixed segment, and
the first segments in `evlet.routing.RESERVED` (such as `/api`, `/app`, `/assets`,
`/docs`, `/login`, `/reports`, `/settings`, `/health` and anything starting with `_`)
are reserved, ignoring case. See [routing.md](routing.md).

`Navigate.to`, `to_list`, `to_record`, `to_new` and `back` are page operations.
`GET /api/ui/resolve?path=/app/note/01hc1sd9vc1ye` resolves only views available to the
authenticated user. Record loading still performs record-level checks through the
existing API.

### State and events

```python
from evlet.ui import Bind, When, Select, TextInput

page.state = {"kind": "Person", "company": ""}
page.add(Select(label="Customer type", options=["Person", "Company"],
                bindings={"value": Bind("kind")}))
page.add(TextInput(
    label="Company name", bindings={"value": Bind("company")},
    visible=When.state("kind").eq("Company"),
    required=When.state("kind").eq("Company"),
))
```

View state is local to a view, record state to its document and component state to
each component; event payloads are available as
`When.state("value", scope="component")`. Application state is shared for the
signed-in workspace. Session identity and tenant fields are read-only in declarative
state.

Expressions support equality, inequality, ordering, contains, `&`, `|`, `~` and
conditional values with `.otherwise(yes, no)`. Use `When.field("status")` for
record values. `view.computed` maps scoped paths to expressions; circular
computed dependencies fail compilation. The compiler emits the dependency index.

Events use registered operations: Navigate, Refresh, SetValue, ResetValue,
OpenDialog, CloseDialog, ShowNotification, ExecuteAction and SubmitForm. A handler
can contain an ordered list. Model actions go through authenticated server routes.
UI-required or hidden fields never relax the server's validation rules.

### Model views

```python
from evlet.ui import ListView, FormView, FormLayout, Field

customer_list = ListView(
    model=Customer, columns=["name", "email", "active"],
    page_size=25, searchable=True, sortable=True, filterable=True,
)
customer_form = FormView(
    model=Customer,
    layout=FormLayout(children=[Field("name"), Field("email")]),
)
```

Model views keep their generated name and route. Use PageView for a custom route.
Generated forms preserve the model's named layout groups. The first group has
ID `main_details`; later groups use `section_2`, `section_3`, and so on. The browser
interface reads `Model.Meta` for its screens, not these views.

### Extensions

```python
from evlet.ui import ViewRegistry, Card, Text

ui = ViewRegistry([welcome])

@ui.extend("customer.form", name="customer_help")
def customer_help(view):
    view.field("email").width = 12
    view.add(Card(id="help", title="Contact details", children=[
        Text("Use the address this customer checks.")
    ]), after="main_details")
```

Order: framework defaults, application definitions, application extensions, site
extensions. Extensions run in registration order within each layer. Repeated names
in the same layer/target, missing targets and duplicate component IDs fail
explicitly. A later assignment to a property intentionally overrides the earlier
value. Applied extension metadata is inspectable in `/api/ui`.

### Custom components

```python
from evlet.ui import ComponentDefinition, CustomComponent

ui.components.register(ComponentDefinition(
    name="Schedule", category="custom", renderer="Schedule", version=1,
    props={"compact": "boolean"}, defaults={"compact": True},
))
page.add(CustomComponent("Schedule", compact=True))
```

The contract is validated and appears in `/api/ui`. The browser renderer registry
(`registerComponent`, `registerLazyComponent`) belonged to the SolidJS interface and
has been removed; no browser code renders custom components now.

## Work across the UI boundary

Browser code lives in `frontend/`. Python UI definitions, model screen metadata,
compilation and browser routes live in `evlet/ui/`. `Application` delegates UI
routes through one dispatch hook; record operations, authentication, permissions,
storage and jobs remain with their existing services. Imports from
`evlet.metadata` remain supported through a compatibility re-export.

Recorded contracts, each compared with fresh authenticated ASGI responses by
`tests/test_ui_contracts.py`:

- `frontend/src/evlet/tests/api-fixture.json` and `screens-fixture.json`: the responses
  the browser tests replay (generator `scripts/generate_ui_framework_fixtures.py`).
- `tests/fixtures/ui-fixture.json`: the Playground's `/api/ui` response and model schema.
- `tests/fixtures/record-fixture.json`: a paginated record list, a record with child
  rows, and an event page. Only the opaque random cursor value is normalized.

The generators run the actual authenticated routes against a temporary SQLite
database. For an intentional contract change, run from the repository root:

```sh
.venv/bin/python -m scripts.generate_ui_framework_fixtures
.venv/bin/python -m scripts.generate_ui_fixtures
.venv/bin/python -m pytest tests/test_ui_contracts.py tests/test_ui.py
npm --prefix frontend test
```

Review the fixture diff before accepting it. A failed comparison should not be
fixed by silently regenerating a contract the other side cannot consume.

For features that span both sides, agree the response shape and fixture first,
then implement and test both producers and consumers.

Run backend tests, frontend tests and type checks, the production build and the
isolated wheel check before shipping. Browser tests use a disposable database;
preserve the user's working database, accounts, attachments and migration history.
