# Evlet UI

The browser interface is built on Evlet's own UI framework, described in
[ui-framework.md](ui-framework.md). Its code lives in `frontend/src/evlet/`. It has no
runtime dependencies. Sign-in lands on the person's start page or the first list the role
can open, and Docs stay reachable from the sidebar and the search palette. Every model the role can read
gets a list and a record view, built from `/api/schema`, record details and the list
routes, so a model-only application needs no frontend code.

The previous SolidJS interface and the Vue regression harness have been removed.

## What this interface renders

- Sign-in and first-run setup (the first administrator account).
- Docs, with its topics, from the sidebar and the search palette.
- The shell: sidebar with the models; a top bar with a history button for recent records (left of
  search), the search palette and the New menu.
- Generated model screens: lists, boards for models that name their State in `Meta.board`, record views (on their own
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

A Table the record fills itself (`Meta.derives`, [model-engine.md](model-engine.md#derived-values))
is read only on the record page, in focus view and in the row editor, whatever the state: it reads
"Filled in by the record when it is saved." and a save never sends its rows. Its fields show read
only too.

Model `Meta` options shape these screens: `list_fields`, `title_field`, `search_fields`,
`layout` (sections, or tabs and blocks, see Record layouts below), `key_fields`, `cards`, `relations`, `measures`, `open_in`, `list_tabs`,
`list_tab`, `autosave`, `status`, `board`, `summary`, `side_blocks`, `panel`, `focus` and `duplicate`. See "What the model decides" in [ui-framework.md](ui-framework.md).

### Where the cursor starts

`Meta.focus` names the field a new record's form puts the cursor in:

```python
class Invoice(Model):
    class Meta:
        focus = 'customer'
```

It must be a visible field that people fill in: stored, not hidden, not read only, not calculated
(`compute=`, `query=` or `fetch=`), not a `Table` and not a `Sequence`. Anything else fails when the
registry is built with a `ValueError` that names the model and the field, such as
`Invoice.Meta.focus: total is calculated; name a field people fill in`.

`/api/schema` carries it as `"focus": "customer"` on the model entry, only when it is set. Like
`key_fields`, it is left out for a role that cannot read the field. It is display only and never a
permission. Without it, or when this role cannot edit the field, the browser puts the cursor in the
first editable field. Slips take the same option as `focus = "field"` ([slips.md](slips.md)).

### Each fact once on the record page

A record page shows each fact in one place. The header has the title, the name, the State and up
to three key fields. The summary strip under it has calculated values and per-record measures that
the header doesn't show; with nothing left, there is no strip. The Details form of a saved record
leaves out the title, name and State, because the header shows them and edits the title in place;
a new record's form keeps them. The side column's Key fields block shows only while another tab
is open (Activity, Related or a table), as a way back to the fields. Its Related block lists only
relations that have records; the Related tab lists every one. Record info has Created and Last
modified; More > Copy record ID copies the id.

Two options change this per model:

```python
class Project(Model):
    class Meta:
        summary = False                              # no summary strip
        side_blocks = ('related', 'info', 'key_fields')  # these side blocks, in this order
```

`side_blocks` takes `key_fields`, `related`, `files` and `info`, each once. Anything else, or a
`summary` that is not True or False, fails when the registry is built.

### What a side panel shows

A list that opens records in a side panel (`Meta.open_in = 'panel'`, or "Open records in: Side
panel") can show less of each record than the full page. Each app decides which parts:

```python
class Ticket(Model):
    notes = LongText(panel=False)            # this field stays on the full page
    lines = Table(Line, panel=False)         # so does this table's tab

    class Meta:
        open_in = 'panel'
        layout = (Section('Ticket', ('subject', 'owner')),
                  Section('Internal', ('audit_note',), panel=False))   # and this section
```

`panel=False` works on a field, a `Section` and a `Table`. For a model where only a few parts
matter in the panel, `Meta.panel` names what to keep, in order, and hides everything else:

```python
    class Meta:
        panel = ('Ticket', 'related', 'info')   # a section, the Related tab and Record info
```

Names are section titles, field names, `Table` field names, the record tabs (`activity`,
`details`, `revisions`, `related`, `postings`), `summary` (the summary strip) and the side blocks
(`key_fields`, `files`, `info`). Naming a section keeps all its fields; naming a field keeps its
section with only that field; `details` keeps every section. Tabs and blocks show in the order
`Meta.panel` names them. A section or field of the same name wins over a tab or block. When both
are given, `Meta.panel` wins over the flags. An unknown name, a name given twice or a flag that is
not True or False fails when the registry is built.

- **Display only.** Hiding is never a permission: the API, row, field and action checks are
  unchanged, and the full page shows everything.
- **A new record can always be saved.** A required field that is empty on a new record shows in
  the panel even when it is hidden.
- **The full page is one click away.** When the panel hides anything, a line under Details says
  so with an Open full page link, as well as the panel's Expand button.
- Controls, Models, Form layout can change it per instance with the panel button on each section,
  field and tab, and the Side panel card for the summary strip and blocks
  ([controls.md](controls.md)).

### Duplicate

A saved record's Duplicate action opens a new, unsaved record filled in from it. Saving it is an
ordinary create: the server copies nothing itself and checks the new record as any other, so
required, unique, row, field and action rules all apply. The browser copies the values a person
could type and the rows of each `Table`. It leaves out what Evlet sets: the id, the name or
document number, `Sequence` numbers, the State, calculated and read-only values and the record
times. Two options steer it:

```python
class Invoice(Model):
    reference = Text(unique=True, copy=False)   # a duplicate starts with an empty reference
    lines = Table(Line, copy=False)             # and without these rows

    class Meta:
        duplicate = False                       # or: no Duplicate action on this model at all
```

`copy=False` works on any field (a `Child` row field too) and on a `Table`. The schema carries
`copy: false` on that field or `children` entry, and `duplicate: false` on the model, only when
they are off. A value that is not True or False fails with a `ValueError`: for `copy=` when the
field or `Table` is defined, for `Meta.duplicate` when the registry is built. Duplicate shows only
to roles that may create the model (the schema's `permissions.create`). Both options are display
only: they never change storage, migrations or permissions.

### Record layouts

`Meta.layout` can declare the whole record page instead of sections only. It is a list of `Tab`s,
or of plain blocks when there are no tabs. A tab holds blocks in order:

```python
from evlet import Chart, Count, Html, Model, Ref, Related, Section, Tab


class Employee(Model):
    class Meta:
        layout = (
            Tab('Profile', (
                Section('Employee', ('full_name', 'email', 'department', 'reports_to')),
                Html('<p>Skills of <strong>{full_name}</strong> are kept with the record.</p>'),
                'skills',                                  # a Table field, inside the tab
            )),
            Tab('Team', (
                Chart('stat', y='people', model='employee', link='reports_to', label='Direct reports'),
                Chart('bar', x='department', y='people', model='employee', link='reports_to'),
                Related('employee', 'reports_to', label='Direct reports', fields=('full_name', 'email'), order='full_name'),
                Related('employee', 'reports_to', label='Same department', filters={'department': Ref('department')}),
            )),
        )
        measures = {'people': Count()}
```

| Block | What it shows |
|---|---|
| `Section(...)` or `('Title', fields)` | Fields, as before. |
| A `Table` field name | The child table, inside the tab or the form instead of on its own tab. |
| `Related(model, link=None, *, label, filters, fields, limit, order)` | Records of `model` whose Link `link` points to this record, `limit` a page (default 5, at most 20), with a pager and **View all**. |
| `Html(content)` | Text from the app. `{field}` shows that field of the record. |
| `Chart(type, x=None, y=..., *, model, link=None, filters, label)` | Declared measures of `model` over the records that point to this one: `'stat'` numbers, or `'bar'` or `'line'` grouped by `x` (`'status'`, `'due:month'`). |

- **Tabs.** A `Tab(title, blocks, *, name=None, panel=True)` is named by its title in lowercase with
  underscores (`Tab('Long text', ...)` is `long_text`), or by `name=`. `Meta.tabs`,
  `Meta.default_tab`, `Meta.panel` and the address (`?tab=t-<name>`) use that name, and a tab named
  `details` keeps the Details address. Layout tabs replace Details. Activity, Revisions and
  Postings stay, and a Table that no block places keeps its own tab. Without tabs, the blocks show
  on Details in order: a Table can sit between two sections of the form.
- **Fields no section lists** show as Other, last on the first tab, as before.
- **`link`** is the other model's Link field to this one. Leave it out when there is exactly one.
- **Filters** use the list filter syntax on the other model's own fields, as a dict
  (`{'status__ne': 'done'}`) or a list of conditions. `Ref('field')` compares with a field of this
  record (eq, ne, gt, gte, lt, lte); the page reads it from the saved record. Paths and child-row
  filters are refused.
- **Charts** use only measures declared in the other model's `Meta.measures`, without `model=`
  (model-level). Up to four. They read the aggregate route with the same filters, so they count
  only records the person may read, and they draw like a report chart.
- **Html** keeps to the rich text allowlist: paragraphs, h2 and h3 headings, lists, quotes, code,
  strong, em, links and line breaks. Scripts, styles, frames, forms and event attributes fail when
  the class is defined; anything else outside the list is dropped. Record values are always
  inserted as text, never as markup. `{{` and `}}` are literal braces. It is the app's own trusted
  text: never put user-entered HTML in it.
- **The automatic Related tab and side block** turn off for a model whose layout declares a
  `Related` block. Name `related` in `Meta.tabs` to keep the tab, or in `Meta.side_blocks` to keep
  the side block. A model without Related blocks keeps both, as before.
- **Permissions.** Related rows come from `GET /api/{model}` with the block's filters and
  `[link, "eq", <record id>]`, and charts from `GET /api/{model}/aggregate`, so row scopes, field
  permissions and filter checks apply as for any list. `/api/schema` leaves out a Related or Chart
  block the role cannot use (the model, its Link, a filter field or every measure unreadable) and
  cuts Related columns and order to readable fields.
- **Loading.** A block asks for its rows when it first scrolls into view, and a save of the
  record loads shown blocks again. A related record and a chart need a saved record, so a new
  record shows only its sections, tables and Html.
- **Side panel and phones.** In a list's side panel the tabs show as on the page, `panel=False`
  on a `Tab` leaves it out, and the side column's blocks close the first tab. Blocks stack at
  phone width, and the tabs move by keyboard (arrow keys) like the other record tabs.
- **Errors.** An unknown model, field, Link or Table name, a bad filter or operator, a `Ref` to a
  field the model does not show, an unknown measure or group field, a Tab inside a Tab, mixing
  Tabs with plain blocks, two sections with one title, or a Tab named like a record tab fails
  when the registry is built, naming the model, the tab and the block.

`/api/schema` sends the layout as `record_layout`: `{"tabs": [{"name", "label", "blocks"}]}` or
`{"blocks": [...]}`, each block with its `kind` (`section`, `table`, `related`, `html`, `chart`).
Sections are named by title and keep their fields in `layout`, which every other client reads as
before. A model whose layout has only sections has no `record_layout`, and its page is unchanged.
The Playground's Employee page uses every block type.

### Compact record page

A layout that starts with `Main` asks for a compact record page: cards at the top, a primary area
without a heading, and every block after it as "more information". There are no tabs.

```python
from evlet import Count, Currency, Main, Model, Related, Section, Sum


class Shipment(Model):
    class Meta:
        cards = ('total', 'parcels', 'weight')          # up to five derived values
        measures = {'parcels': Count('parcel', link='shipment'),
                    'weight': Sum('weight', model='parcel', link='shipment')}
        layout = (
            Main(('title', 'carrier', 'status'), 'stops'),   # fields, then Table fields
            Section('Charges', ('declared', 'fee', 'total'), description='What it is worth and costs.'),
            Section('Notes', ('notes',), collapsed='empty'),
            Related('parcel', label='Parcels'),
        )
```

- **`Meta.cards`** names zero to five derived values: fields with `compute=`, `fetch=` or `query=`
  (stored or `stored=False`) and per-record measures (`Meta.measures` with `model=` and `link=`). A
  stored field people fill in, a hidden field, a model-level measure, an unknown name, a name given
  twice, more than five, or a name that is both a field and a measure fails when the registry is
  built. Cards are for root models, not Table rows. `/api/schema` sends `cards` as a list of names,
  like `key_fields`, cut to what the role reads: a field it can read, a measure whose fields it can
  read. Without `Meta.cards` nothing changes and the summary strip works as before.
- **`Main(fields, *tables)`** is the primary area: a grid of fields, then Table fields, with no
  heading. Use it once, first. A layout with `Main` has no `Tab`s, takes no `Meta.tabs` or
  `Meta.default_tab`, and places every `Table` field (in `Main` or after it), since there is no tab
  to put one on. Where a name is needed (`Meta.panel`, Controls, Form layout) the area is named by
  the model's label, so a section cannot share that title.
- **`Section(..., description=...)`** adds a line of text under a section's title. Any layout can use it.
- **More information.** Every `Section`, `Table`, `Related`, `Html` and `Chart` after `Main` shows
  as a row that opens and closes, with a one-line summary while it is closed.
- **Header and side column.** The header has the title, the status badge (`Meta.status`) and the
  actions. `Meta.key_fields` keep every other use (search, reference labels, lists, connectors), but
  on a compact page their facts move to the side column's Record info, next to Attachments and
  Activity.

`/api/schema` sends `record_layout` as `{"compact": true, "blocks": [...]}`. The Main fields are a
`section` block named by the model's label and the Main tables are `table` blocks, each with
`"main": true`; the section in `layout` has `"main": true` too, and a section with a description
has `"description"`. A client that ignores these keys shows the blocks in order, as it would any
record layout. The compact design itself (cards, collapsed rows with one-line summaries, the side
column) is drawn by the record page in a later release. The Playground's Project uses it.

### List tabs

A list shows a row of tabs above the toolbar: All, then one tab per value of one field, each with
its count. `Meta.list_tabs` names that field and `Meta.list_tab` names the tab the list opens on.

```python
class Employee(Model):
    status = Select(["active", "left", "on_leave"], default="active")
    department = Link(Department)

    class Meta:
        list_tabs = "status"     # a State, Select or Link field
        list_tab = "active"      # the tab the list opens on; "all" is the default
```

- **Which field.** A State, Select or Link field. Any other field, or a name the model does not
  have, raises `ValueError` when the registry is built. Without `Meta.list_tabs` the model's State
  drives the tabs, as before; a model without a State has none. `list_tabs = None` turns them off.
- **The default tab.** For a State or Select, `list_tab` is `"all"` or one of the field's values,
  checked at definition. For a Link it is the linked record's title (or name), found when the page
  loads; if this person can't read such a record, the list opens on All. `list_tab` without tabs
  raises `ValueError`.
- **Link tabs** show the most used values, at most 8, most used first, each labelled with the
  linked record's title. The filter finds the others. A record a link opened with `?tab=` keeps
  its own tab even when it is not among them.
- **Counts** come from `GET /api/{model}/list_tabs?filters=...&q=...`, which answers
  `{"field", "total", "items": [{"value", "count"}], "more"}`. They follow the list's filters
  and search box, but not the tab itself, and only count records the person may read (row scope).
  Records without a value count in All only. Filters on fields the role can't read are refused.
- **Who sees them.** `/api/schema` gives `list_tabs: {"field", "kind", "default"}` only to roles
  that can read the field, and for a Link only when they can read the linked model without a row
  scope (counting by it would reveal records they can't see). Others get no tabs, and the counts
  route answers 403.
- **The address.** With no `tab` in the address the list opens on the default tab. Choosing
  another, All included, puts it in the address (`?tab=all`), so a link or a reload keeps it.
  Clear filters goes back to the default tab. A tab with no records and no search or filters
  offers Show all instead.
- **Controls.** The list view sets both for an instance ("Tabs by" and "Opens on",
  [controls.md](controls.md)).

The Playground's Projects list opens on Active, with tabs for Planned, Active and Done.

A record's State shows as a coloured badge in the header by default, with its moves as header
buttons; `State(..., show="bar")` draws the stage bar instead, for pipelines, and `show="none"`
draws neither. `Meta.status` adds a status badge for a Select the system sets, such as a
payment status, coloured by `Select(..., colors={...})`. Badges and bar steps offer only the
moves this person may take now; everything else is plain text. Lists draw the same badges. The
badge wraps with the key facts in the side panel and on a phone; the bar scrolls sideways there.
See [states-actions.md](states-actions.md#how-a-state-and-a-status-show).

Related records (`Meta.relations`) show in the side panel's Related block, with a count per
relation, and in the Related tab, with a short table per relation. Nothing is asked for when a
record opens: a relation's records load when the Related block or its tab section first scrolls
into view (at once in a browser without IntersectionObserver). One server page of five records
serves both the count and the tab, and it stays loaded while the record is open, so switching
tabs asks again for nothing; a save of the record reloads the pages already shown. Each tab
section shows five rows; with more, a small pager under it reads "1–5 of 12" with previous and
next, each a server page (`limit` and `offset`). **View all** in the section's heading opens the
related model's list filtered to this record (`?f=[["customer","eq","<id>"]]`), shown as a
filter that can be removed. It is left out when the role cannot filter that list on the link.

## Workspaces

A workspace is a named sidebar: groups of items, three levels deep (workspace, group, item). An app
can declare none, one or several. The browser does not draw them yet; `/api/schema` gives each role
its own view of them under `workspaces`.

```python
# apps/projects/workspaces.py
from evlet import NavGroup, Shortcut, Workspace
from .reports import SalesByCustomer
from .slips import QuickInvoice

PROJECTS = Workspace('Projects', icon='kanban', groups=(
    NavGroup('Projects', icon='kanban', items=(
        'project',                                                   # a model's list
        Shortcut('project', view='board', label='Board'),            # its board (Meta.board)
        Shortcut('task', filters={'done': False}, label='Open tasks'),
    )),
    NavGroup('More', items=(
        Shortcut(report=SalesByCustomer),                            # a report
        Shortcut(slip=QuickInvoice),                                 # a slip without a source
        Shortcut(url='/reports', label='All reports'),               # an app path or http(s) address
    )),
))
```

An app package declares them in `workspaces.py` (or a `workspaces/` package). Discovery collects
every module-level `Workspace` there, in the order they are defined, as it finds models and
reports. An application built by hand passes `Application(workspaces=(...))`, and a legacy app
`AppDefinition(workspaces=(...))`. The Playground declares Sales, Projects and Company
(`examples/playground/workspaces.py`).

Each app has one source of workspaces. A legacy app's `AppDefinition(workspaces=...)` list is the
whole list: a legacy app is never discovered, so a `workspaces.py` its `hooks.py` imports adds
nothing it does not list. A `Workspace` imported from another app (`from notes.workspaces import
DESK`) belongs to that app and is not collected again, as for models and reports. The same
`Workspace` listed twice is registered once. Two different workspaces with one key are refused, and
the message names both and where each comes from (`app tasks (AppDefinition)`,
`app notes (notes/workspaces.py)` or `Application(workspaces=...)`).

- `Workspace(label, *, key=None, icon=None, groups=(...))`. `key` defaults to the label in lowercase
  with underscores (`'Sales desk'` is `sales_desk`); give one when the label has no Latin letters.
- `NavGroup(label, *, icon=None, items=(...))`.
- An item is a model name (`'project'`, the storage name or `app.ClassName`), or a `Shortcut` that
  names exactly one target:
  - `Shortcut(model, view=None, filters=None, label=None)`: a model given by name or class. `view`
    is `'list'` (the default) or `'board'`, for a model whose `Meta.board` names its State.
    `filters` is a filter object (`{'status__in': ['planned', 'active']}`) or a list of conditions,
    the forms a list request takes; it needs a `label`.
  - `Shortcut(report=SalesByCustomer)` or `Shortcut(report='SalesByCustomer')`: a registered report.
  - `Shortcut(slip=QuickInvoice)` or `Shortcut(slip='quick_invoice')`: a registered slip without a
    `source` (a slip with one opens from its record).
  - `Shortcut(url='/reports', label='All reports')`: an app path starting with `/`, or an `http` or
    `https` address. `javascript:`, `data:` and every other scheme are refused. It needs a label.
  - `label` replaces the model's plural name, the report's or the slip's title.
- `icon` is an icon name from the browser's set, such as `receipt` or `users`.

**Checked when the application is built.** Each problem fails with a message that names the
workspace, the group and the item: an unknown model, report or slip; a child table, a ledger model
or a model with `Meta.api = None`; a board for a model without `Meta.board`; a view other than list
or board; filters on unknown fields, with operators the field does not take, with values the field
refuses or on fields of linked records (the same checks a list request makes); a URL that is not an
app path or an http(s) address; an empty or over-long label; two workspaces with one key; and two
items with one key in a workspace.

**Keys.** Keys are stable, so the browser can remember choices and Controls can refer to items:

| What | Key |
| --- | --- |
| Workspace | `key`, or the label as above. An automatic workspace has its app's name (`app` for an application built by hand) |
| Group | The label in lowercase with underscores, unique in its workspace |
| Model item | `model:<name>`; a shortcut with a label, view or filters adds `:<label>` (or `:<view>` without a label): `model:task:open_tasks`, `model:project:board` |
| Report, slip, URL | `report:<name>`, `slip:<name>`, `url:<label>` |

**Each role sees only what it can open.** An item is left out when the role can't read the model,
can't read the State a board follows, can't filter on a field the shortcut's filters name, can't run
the report or can't present the slip. A group left with no items is hidden, and so is a workspace
left with no groups. URL items always show; the page they open checks access itself.

**An app that declares no workspace** gets one automatic workspace, keyed by the app's name and
labelled with its title. It groups the app's models as the sidebar does today: **Documents** (a
model with a State or child rows), **Records** (the rest), then **Reports** (the app's reports,
those without a group first).

```json
"workspaces": [
  {"key": "projects", "label": "Projects", "icon": "kanban", "app": "projects", "source": "declared",
   "groups": [
     {"key": "projects", "label": "Projects", "icon": "kanban", "items": [
       {"key": "model:project", "kind": "model", "label": "Projects", "model": "project", "view": "list", "filters": null},
       {"key": "model:task:open_tasks", "kind": "model", "label": "Open tasks", "model": "task", "view": "list",
        "filters": [["done", "eq", false]]}]},
     {"key": "more", "label": "More", "icon": null, "items": [
       {"key": "report:SalesByCustomer", "kind": "report", "label": "Sales by customer", "report": "SalesByCustomer"},
       {"key": "slip:quick_invoice", "kind": "slip", "label": "Quick invoice", "slip": "quick_invoice"},
       {"key": "url:all_reports", "kind": "url", "label": "All reports", "url": "/reports"}]}]}
]
```

`source` is `declared`, `automatic` or `custom` (added on this site, with `app` null). `filters` is
always in the list form a list address carries (`?f=`), or null. Hiding an item is never a
permission: every route checks access as before.

**Customizing a workspace on one site.** Admins, and roles with "Customize workspaces" on in Controls,
can reorder, hide and rename groups and items, rename a workspace, add shortcuts and add whole
workspaces for the site, and reset any of it. These are site overrides, stored and audited like the
other Controls overrides ([controls.md](controls.md#workspaces)). `/api/schema` gives the effective
workspaces: the declared ones, then the site's overrides, then permission filtering. An override can
never show a role something it can't open: a shortcut to a model a role can't read stays hidden for
that role.

## Not rendered by this interface

These keep their Python APIs, HTTP routes, permission checks and tests. The browser
interface does not show them (owner decision 3 in [handoff-plan.md](internal/handoff-plan.md)):

| Python API | Status |
| --- | --- |
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
Docs off it hides the Docs links.

The start page is the same with Docs on or off. After sign-in, at `/`, at the model base
(`/app`) and at any address that matches no route, the browser opens the start page saved in
Settings, else the first list the role can open. It opens Docs only when that is the saved
start page, or when the app has no list at all. With no list and Docs off it says there is
nothing to open yet.

| Path | Screen |
| --- | --- |
| `/`, `/login` | Sign-in, or the start page when signed in |
| `/docs`, `/docs/<topic>` | Docs |
| `/app/<model>` | List (and board, when `Meta.board` names the State). Search, sort, page, view and filters are in the query |
| `/app/<model>/new` | New record. Query values fill writable fields: `?customer=4` |
| `/app/<model>/<id>` | Record view. `?tab=` keeps the tab |
| `/app/<model>/<id>/view` | Older record link; opens the record view |
| `/settings/<page>`, `/settings/app/<app>` | Settings ([settings.md](settings.md)); `/settings` opens the last page |
| `/settings/accounts` | Redirects to `/controls/users` |
| `/controls`, `/controls/<page>` | Controls ([controls.md](controls.md)), a separate page for roles that open it |
| `/eva`, `/eva/<chat>`, `/eva/tasks` | Eva's chat and scheduled tasks ([eva.md](eva.md)). Served only when config.py turns Eva on; the page asks `/api/_eva` and shows nothing of Eva to people without it |
| `/reports`, `/reports/<name>` | The reports this role may run, and one report's view ([reports.md](reports.md)) |

### Between the app and Controls

People who may open Controls reach it from **Controls** in the account menu, or **Go to Controls**
in the search palette. Both open it in a new tab. In Controls, **Back to <app>** sits at the top of
the sidebar, on phones too. It is a real link, so a plain click goes back in the same tab and Cmd
or Ctrl click opens a new one. It goes to the last app page this browser had open for the person,
kept in localStorage (`evlet.last_app.<user>`, a same-origin path only). With none, it goes to `/`,
which opens the person's start page. The server stores no last app page.

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
`LocalAuth` configuration.

## Records and concurrency

Authenticated record GET/PATCH responses include ETag headers. The record view sends
If-Match on edits, deletes, moves and actions. A stale revision returns 409 and keeps
the draft. Clients without If-Match keep their behavior. Revision comparison happens
inside the same transaction as the mutation and includes permitted child rows.
Currency and decimal values stay exact strings. A Currency field shows its code from
`currency=` on the field: a fixed code, a field of the record, a field of a linked record or an
app setting ([model-engine.md](model-engine.md#where-a-currency-fields-code-comes-from)). How each
code looks (a text or image symbol, before or after the amount) is the site's choice in Controls,
Currencies ([controls.md](controls.md#currencies)).
Selects, dates, date-times, times and durations open Evlet's own pickers ([ui-framework.md](ui-framework.md#pickers)).
A `RichText` field edits in Evlet's own rich text editor ([ui-framework.md](ui-framework.md#rich-text)):
at rest the record shows the formatted text (read only when the record is locked), and list cells
show its plain text. The Playground's projects have one, Brief.
A record's Activity tab writes notes with the same editor; typing `@` mentions a person, with a
warning when they can't open the record. The bell in the top bar opens the notification centre,
with the unread count kept live by the event stream ([journal.md](journal.md#mentions)). It is a
panel along the right edge under the top bar, grouped into Today and Earlier; a click anywhere else,
Esc or the bell closes it. A click on an item marks it read. Go to shows only when the person can
open the item's record, and opens it. While the panel is closed, new items slide in as popups at the
top right for a few seconds (longer while pointed at or focused); a click on one opens the panel.
Today the inbox holds mentions; other kinds show with a bell icon.

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

### Unsaved changes

Leaving a record with unsaved edits never saves it. Only Save writes the record. Leaving means
going to another page or record, closing the side panel, going back to the list, or hiding the
tab. On a saved record the form keeps the edited fields (and the changed child rows) on the
server, apart from the record, and shows "Unsaved changes kept, restore when you return". The
next time the person opens that record a bar offers "Restore" or "Discard". Restore puts the
edits back in the form as unsaved edits; Save is still needed. If someone saved the record in
the meantime, the bar lists each field both of them changed, with both values, and the person
keeps theirs or the saved one per field, or discards all of it.

An unsaved new record stays in the browser only, so no record is created and no number is used
until Save. Opening New for that model again asks "Restore your unfinished new ...?" with
Restore and Start fresh. Signing out, or another person signing in on that browser, clears it.

Server entries are kept for 30 days (200 per person), browser ones for 7 days (20 per person);
an entry over 256 KB is not kept and stays only in the open tab. These are instance settings in
Controls, Jobs ([controls.md](controls.md#unsaved-changes)). Only the person who left the edits
can read them: API keys, Eva, MCP, the Tool Engine, connectors, reports and exports never see
them.

`Meta.autosave = False` on a model keeps the "Discard your changes?" prompt for it instead (the
Playground's Payment does this). Each person can turn it off for themselves in Settings,
Appearance ("Keep unsaved changes when I leave a record"), which also deletes what was kept
([settings.md](settings.md)).

| Route | What it does |
|---|---|
| `GET /api/_unsaved` | The person's limits, entry count and whether the setting is on (also `unsaved` in `GET /api/_settings/me`) |
| `PUT /api/_unsaved/<model>/<id>` | `{"changes", "base_revision"}`: keep the edits; answers `{saved_at, size, dropped}` |
| `GET /api/_unsaved/<model>/<id>` | `{entry, dropped}`: the person's own entry with `stale` when the record moved on |
| `DELETE /api/_unsaved/<model>/<id>` | Discard the person's own entry |

They need a signed-in browser session and its CSRF token, and that the person can read and
update the record now. Fields the person may not write are left out and named in `dropped`;
unknown fields are refused. Keeping edits never runs validators, events, numbering or history.
A save removes the saver's entry, and deleting a record removes everyone's, in the same
transaction. The format of `changes` is in
[internal/unsaved-changes.md](internal/unsaved-changes.md).

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

The list's table has no card around it. It runs flush to the edges of the main area, as a child
table does in focus view, and the first column keeps the page margin so it lines up with the
toolbar. Beside a side panel and on a phone the table does the same.

A list's Filter button (with a count of active filters) opens the filter side panel, docked
beside the list inside the page, like the record side panel. There is no overlay and no
backdrop: the list narrows and stays usable, and the panel fills the height to the bottom of the
screen. On a phone it is a full-width sheet in the same place. The panel has two halves of about
equal height, each scrolling on its own. The top half holds the filter rows, as in Frappe: each
row is a field, a condition and a value on one line, with a remove button; Add filter adds rows
with no limit, and every row must match (AND). The bottom half holds **Saved filters**: name the
current filters and Save keeps them; choosing one applies its rows (the current one is marked);
the trash button deletes it. Saved filters keep the rows in the same form as `?f=`, per person
on the device (browser storage, like a report's saved views), up to 20 per list. Opening the
panel moves focus to the first row's field, or adds a row and opens its field search; Escape
closes the panel and returns focus to the Filter button. The field picker searches the model's fields
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
are no saved filters yet, because Evlet has no place to keep them. The list tabs
([List tabs](#list-tabs)) stay above the toolbar.

## Python pages (removed in 0.4)

Evlet 0.4 removed the Python page API (`evlet.ui` `PageView`, `ListView`, `FormView`, layouts,
`ViewRegistry` extensions and `CustomComponent`), its `GET /api/ui` and `GET /api/ui/resolve`
routes, and native TS/JS panels from an app's `views/` folder. The browser never rendered them.
Shape screens with generated views and `Model.Meta` options instead; the
[upgrade note](../CHANGELOG.md) says what an app changes.

## Work across the UI boundary

Browser code lives in `frontend/`. Model screen metadata and browser routes live in
`evlet/ui/`. `Application` delegates browser routes through one dispatch hook; record
operations, authentication, permissions, storage and jobs remain with their existing
services.

Recorded contracts, each compared with fresh authenticated ASGI responses by
`tests/test_ui_contracts.py`:

- `frontend/src/evlet/tests/api-fixture.json` and `screens-fixture.json`: the responses
  the browser tests replay (generator `scripts/generate_ui_framework_fixtures.py`).
- `tests/fixtures/ui-fixture.json`: the Playground's model schema.
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
