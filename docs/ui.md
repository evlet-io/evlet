# Evlet UI

Evlet UI is the native SolidJS frontend. A local session opens Docs after sign-in.
Demo Pages contains the Projects Explorer View and a page built with the Python UI
API. Models contains generated screens backed by the existing authenticated API.

Projects under Demo Pages is browser-local sample data. Projects under Models uses
the database, child tasks, actions, jobs and private attachments. These are separate
examples. The demo keeps the approved light sidebar, blue active state, fixed top
bar, independent list/preview scroll areas and expanded summary cards.

## Build and run

From the repository:

```sh
bash scripts/playground.sh --setup
```

Open http://127.0.0.1:5173. Keep the launcher running. Python source changes reload
the backend; Solid source changes update through Vite. Set `EVLET_RELOAD=0` to turn
off backend reload. The launcher uses loopback and does not replace an occupied port.

Build the bundled UI before building a Python distribution:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m build --no-isolation
```

The frontend build copies assets into `evlet/ui/static/`. The wheel serves them
through `Application`, including direct URLs and browser refresh. Installed Python
applications do not require Node for ordinary development. Node is needed when
changing the browser renderer or building from source.

## A small application

```python
from evlet import App, Model, Text, Boolean

class Note(Model):
    title = Text(required=True)
    done = Boolean(default=False)

app = App(name="notes", models=[Note])
```

Run `uvicorn app:app --host 127.0.0.1 --port 8000 --reload`. Open
http://127.0.0.1:8000, create the first administrator, then sign in. Notes has a list,
a create form, an editor and read-only detail. Search, filters, sort and pagination
run through the model query API. Role, field and record permissions stay on the server.

`App` is a local convenience entry point. Its default database is `.data/notes.sqlite3`;
its default authentication allows initial setup and insecure cookies only on exact
loopback origins on port 8000. A deployed site must supply its database and explicit
`LocalAuth` configuration. Existing `Application(database, auth=..., ...)` consumers
can pass `views=[...]` or `ui=ViewRegistry(...)` without changing model APIs.

## Two ways to define a page

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

## Contracts and rendering

`ComponentDefinition` declares name, category, property types, defaults, events,
slots, bindings, accessibility notes, renderer name and compatibility version.
`ui.components.catalog()` returns inspectable metadata. Docs uses that same catalog
for examples and property tables. Only implemented components are registered.

`/api/schema` retains model schema version 1 and UI metadata version 1.
`/api/ui` provides the separate version 1 component/view schema. Both endpoints
are authenticated when the application uses authentication. Generated UI metadata
is filtered by the same model and field policy as the existing schema endpoint.
Custom pages can specify `roles=("admin",)` and `requires=(Customer,)`.
A page’s UI visibility is not a replacement for server-side authorization.

## Layout vocabulary

The outer layout is separate from the active page:

| Area | Responsibility |
| --- | --- |
| AppShell | Viewport, sidebar, top bar, view host and overlay ownership |
| Sidebar | BrandArea, WorkspaceSwitcher, NavigationGroup, SavedViews, SidebarFooter |
| TopBar | Leading, center and trailing slots |
| ViewHost | Current page and its scroll areas |
| StatusBar | Supporting page status and actions |
| OverlayLayer | Menus, dialogs and notifications |

The list alternative is **Explorer View**:

| Area | Responsibility |
| --- | --- |
| PageHeader | Title, subtitle, badge, primary actions and trailing content |
| SummaryRow | Summary cards; no automatic collapse |
| ViewBar | Category tabs and view switcher |
| ListToolbar | Search, filters, sorting and column controls |
| RecordList | Rows, columns, selection and row actions |
| ListFooter | Pagination and result counts |
| RecordPreview | Icon, identifier, title, badge, metadata, tabs and footer |

One primary action is a button. Multiple primary actions use a dropdown. Category
tabs can use any select field. Each Solid area accepts its own classes, styles,
standard element attributes and content slots; element references are forwarded.
The full preview card scrolls in its own area. The shell does not scroll off-screen.

Python layouts include Row, Column, Grid, Stack, Container, Section, Card, Split,
Panel and FormLayout. A FormLayout uses 12 columns; a Field can set `width=6` or
`width=12`. Layouts adapt to narrow screens. Shell slots are a typed Solid extension
surface; Python applications use the standard shell automatically.

## Routes

Generated routes are `/app/note`, `/app/note/new`, `/app/note/123` and
`/app/note/123/view`. Explicit pages use their registered routes. Custom parameters
use `/orders/{reference}` or `/orders/{id:int}` and are available in view state.
The route resolver rejects ambiguous routes. The namespaces `/api`, `/app`,
`/assets`, `/docs`, `/login`, `/settings` and `/health` are reserved.

Use `Navigate.to`, `to_list`, `to_record`, `to_new` or `back`. Browser history and
direct links work. `GET /api/ui/resolve?path=/app/note/123` resolves only views
available to the authenticated user. Record loading still performs record-level
checks through the existing API.

## State and events

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

View state is local to a mounted view. Record state belongs to the document
controller. Component state is local to each rendered component; event payloads
are available as `When.state("value", scope="component")`. Application state is
shared for the signed-in workspace and cleared on sign-out. Session identity and
tenant fields are read-only in declarative state.

Expressions support equality, inequality, ordering, contains, `&`, `|`, `~` and
conditional values with `.otherwise(yes, no)`. Use `When.field("status")` for
record values. `view.computed` maps scoped paths to expressions; circular
computed dependencies fail compilation. The compiler emits the dependency index;
Solid tracks the specific state reads for updates.

Events use registered operations: Navigate, Refresh, SetValue, ResetValue,
OpenDialog, CloseDialog, ShowNotification, ExecuteAction and SubmitForm. A handler
can contain an ordered list. Model actions go through authenticated server routes.
UI-required or hidden fields never relax the server’s validation rules.

## Generated records

The existing model metadata controls field types, default values, labels,
read-only fields and permissions. Typed native inputs preserve decimal/currency
strings and omit untouched server defaults. Link choices use paginated, searched
server queries. Child collections preserve separate create/update/delete rights.
Attachments remain private, with the existing file size and count limits.

Lists support server queries, category tabs, list/gallery presentations, column
visibility and ordering, selection, bulk delete and saved filters. Preferences
are stored per browser, tenant, user and model; they do not grant access to data.
Filters, order, search, page size and offset are represented in the URL. Bulk
deletes report individual failures and do not claim an all-or-nothing operation.

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

Authenticated record GET/PATCH responses include ETag headers. The new editor
sends If-Match on edits, deletes and record actions. A stale revision returns 409
and preserves the draft. Existing clients without If-Match retain their behavior.
Revision comparison happens inside the same transaction as the mutation and
includes permitted child rows. Live updates do not overwrite dirty forms. Leaving
an unsaved form asks whether to discard it.

## Extensions

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
extensions, browser user preferences. Extensions run in registration order within
each layer. Repeated names in the same layer/target, missing targets and duplicate
component IDs fail explicitly. A later assignment to a property intentionally
overrides the earlier value. Applied extension metadata is inspectable in `/api/ui`.

## Custom frontend components

Register a serializable Python contract, then register the renderer in the
browser bundle using `registerComponent` or `registerLazyComponent`:

```python
from evlet.ui import ComponentDefinition, CustomComponent

ui.components.register(ComponentDefinition(
    name="Schedule", category="custom", renderer="Schedule", version=1,
    props={"compact": "boolean"}, defaults={"compact": True},
))
page.add(CustomComponent("Schedule", compact=True))
```

```typescript
registerLazyComponent("Schedule", () => import("./Schedule"), 1);
```

The renderer implements `RendererProps`, including the node contract and runtime
state/event context. Unsupported compatibility versions produce a visible error.
Custom code must use the existing authenticated data client. ProjectsDemo shows
this boundary in `examples/playground/ui.py` and `frontend/src/demos/`.

## Compatibility and current boundaries

No model, database or existing JSON API was replaced. The active bundle uses Solid
and the first-party components only. `frontend/legacy` keeps the original Vue
screens as a test-only parity harness. Vue, PrimeVue and their test tools are dev
dependencies and are absent from the production dependency graph.

The implemented catalog covers the working pages and generated CRUD flow. It does
not claim every widget in the broader design document. Tree, calendar, autocomplete,
visual filter composition beyond the native query controls, remote preference
sync, SSO and a visual editor remain separate work. A custom renderer can provide
specialized screens without changing Python models or permission rules.

Run backend tests, frontend tests/type checks, production build and the isolated
wheel check before shipping. Browser tests use a disposable database; preserve the
user’s working database, accounts, attachments and existing migration history.

Model views keep their generated name and route. Use PageView for a custom route.
Generated forms preserve the model’s named layout groups. The first group has
ID `main_details`; later groups use `section_2`, `section_3`, and so on.
